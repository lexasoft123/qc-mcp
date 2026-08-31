"""Quad Cortex control protocol: protobuf messages over chunked 128-byte HID
reports. Reverse-engineered from Cortex Control; see PROTOCOL.md.

Layer stack (host <-> QC):
  report  = [reportId][chunkLen][flags][payload...]      (128 data bytes)
  message = <protobuf bytes> + [command u16 LE][u32 reserved][u16 hash]
"""
from __future__ import annotations
import functools
import os
import re
import struct
import sys
import zlib

from google.protobuf import descriptor_pool
try:
    from google.protobuf import message_factory
    def _msg_class(desc):
        if hasattr(message_factory, "GetMessageClass"):
            return message_factory.GetMessageClass(desc)
        return message_factory.MessageFactory().GetPrototype(desc)
except ImportError:  # pragma: no cover
    raise

PACKAGE = "cortex_protobuf_v2"

# CortexMessageType.Enum  (id <-> name)
COMMANDS = {
 0:"Undefined",1:"Grid",2:"SetlistPosition",3:"IOSettings",4:"File",5:"IOMeter",
 6:"Tuner",7:"Diagnostics",8:"MIDISettings",9:"GeneralSettings",10:"Version",
 11:"ProductionAutomationMode",12:"GridMove",13:"Scene",14:"Mode",15:"RecallPreset",
 16:"EnableCaptureOut",17:"MasterVolume",18:"CloudLogin",19:"DefaultParameters",
 20:"RecentsFavorites",21:"UndoRedo",22:"SceneCopy",23:"SceneLabel",24:"ShowGigView",
 25:"Screenshot",26:"CPULoad",27:"ShowTuner",28:"Looper",29:"ProductForward",
 30:"BackupsForward",31:"LogsForward",32:"KeepAlive",33:"GlobalTempo",34:"PresetDirty",
 35:"ModuleStats",36:"NeuralCapture",37:"GridModelMeter",38:"GlobalEQ",39:"RecentSearches",
 40:"LocalBackup",41:"CloudBackup",42:"CompilerInhibitedModules",43:"SystemTimeSync",
 44:"Logs",45:"ProcessDownloadsQueue",46:"CloudProduct",47:"Confirmation",48:"SceneColor",
 49:"Connection",50:"NewModels",51:"ModelRepo",52:"ResetCommsBuffers",53:"SuspendConnection",
 54:"PinnedModels",55:"GigViewButton",56:"GenericError",57:"BulkOperation",58:"License",
 59:"PresetSpeedTest",60:"Updater",61:"UpdaterForward",62:"GainCalibration",63:"NeuralCapture2",
 64:"Serialization",65:"TestFarm",66:"ProductionTest",67:"LoadAutomatedTestPreset",
 68:"SetTestPresetInputOutputPorts",69:"SetTestPresetSplitMixPoints",70:"GenerateTestPreset",
 71:"ModelPreset",72:"RemoteControl",
}
NAME_TO_CMD = {v: k for k, v in COMMANDS.items()}

# --- protocol versions -----------------------------------------------------
# The QC's wire schema changes between CorOS releases, so we ship one descriptor
# set per *generation* (major.minor) and pick from the connected firmware. Add a
# generation with `tools/build_descriptors.py build <gen>`; see PROTOCOL.md 12.
PROTOCOL_VERSIONS = ("4.0", "4.1")   # oldest -> newest
LATEST_VERSION = PROTOCOL_VERSIONS[-1]

# Commands that only exist from a given generation on.
COMMAND_SINCE = {71: "4.1", 72: "4.1"}

# Named capabilities -> the generation that introduced them. Tools gate on these
# so a 4.0 device gets a clear "needs CorOS x.y" error instead of a silent no-op.
FEATURES = {
    "model_presets":      "4.1",  # ModelPreset(71): per-device saved settings
    "dual_footswitch":    "4.1",  # StompModeAssignment.type PRIMARY/SECONDARY
    "favorites_by_type":  "4.1",  # RecentsFavorites split preset/IR/capture
    "midi_clock_readout": "4.1",  # GeneralSettings.external_midi_clock_tempo
}

_active_version = LATEST_VERSION


def parse_version(text):
    """'4.1.0' / 'v4.1' / (4, 1) -> comparable tuple. Unparseable -> ()."""
    if isinstance(text, (tuple, list)):
        return tuple(int(x) for x in text)
    return tuple(int(n) for n in re.findall(r"\d+", str(text or ""))[:3])


def generation(firmware):
    """Map a firmware version onto the newest schema generation we ship.

    Older than the oldest falls back to the oldest set (best effort); newer than
    we know about uses the newest, since the schema is additive far more often
    than not.
    """
    v = parse_version(firmware)
    if not v:
        return LATEST_VERSION
    best = PROTOCOL_VERSIONS[0]
    for gen in PROTOCOL_VERSIONS:
        if parse_version(gen) <= v:
            best = gen
    return best


def set_version(firmware):
    """Select the schema generation for the connected device. Returns it."""
    global _active_version
    _active_version = generation(firmware)
    return _active_version


def active_version():
    return _active_version


def supports(feature, version=None):
    """True if `feature` (a FEATURES key, or a bare version like '4.1') exists
    on the active — or given — generation.

    A name that is neither raises: `parse_version` returns () for it, and every
    version compares >= (), so a typo'd or renamed gate would silently pass and
    only fail later where the message is actually built.
    """
    need = FEATURES.get(feature, feature)
    if not parse_version(need):
        raise KeyError(f"unknown feature {feature!r}; "
                       f"expected one of {sorted(FEATURES)} or a version string")
    return parse_version(version or _active_version) >= parse_version(need)


def require(feature, what=None):
    """None if supported, else a human-readable error string for a tool to
    return instead of sending a message the device would ignore."""
    if supports(feature):
        return None
    return (f"{what or feature} needs CorOS {FEATURES.get(feature, feature)} or "
            f"newer; this device is on protocol generation {_active_version}.")


def commands(version=None):
    """The command id -> name map valid for a generation."""
    ver = version or _active_version
    return {cmd: name for cmd, name in COMMANDS.items()
            if parse_version(COMMAND_SINCE.get(cmd, "0")) <= parse_version(ver)}

# MessageAction.Enum
ACTION = {"CREATE": 0, "UPDATE": 1, "DELETE": 2, "READ": 3, "MOVE": 4,
          "COPY": 5, "UPLOAD": 6, "DOWNLOAD": 7, "SWAP": 8}

REPORT_HOST_TO_QC = 0x02
REPORT_QC_TO_HOST = 0x01
FLAG_FIRST, FLAG_LAST, FLAG_SINGLE, FLAG_MIDDLE = 0x40, 0x80, 0xC0, 0x00
REPORT_SIZE = 128           # data bytes per report (excl. report id)
CHUNK_MAX = REPORT_SIZE - 2  # minus [chunkLen][flags] = 126

_pools = {}
_DESCRIPTOR_DIR = os.path.join(os.path.dirname(__file__), "descriptors")
# pre-versioning layout kept working: a single unversioned set in the package
_LEGACY_SET = os.path.join(os.path.dirname(__file__), "qc_descriptors.pb")


def descriptor_path(version):
    return os.path.join(_DESCRIPTOR_DIR, f"qc_descriptors-{version}.pb")


def _recovered_fdps(version):
    """Prefer the bundled descriptor set for `version`; fall back to the legacy
    unversioned file, then to scanning the installed app binary."""
    from google.protobuf import descriptor_pb2
    for path in (descriptor_path(version), _LEGACY_SET):
        if os.path.exists(path):
            fds = descriptor_pb2.FileDescriptorSet()
            with open(path, "rb") as fh:
                fds.ParseFromString(fh.read())
            return {f.name: f for f in fds.file}
    tools = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
    sys.path.insert(0, os.path.abspath(tools))
    import extract_protos
    return extract_protos.recover()


def pool(version=None):
    """Descriptor pool for a schema generation (default: the active one).

    Cached per generation, so reconnecting to a device on different firmware
    picks up the right schema without reparsing.
    """
    ver = version or _active_version
    if ver not in _pools:
        p = descriptor_pool.DescriptorPool()
        fdps = _recovered_fdps(ver)
        from google.protobuf import descriptor_pb2
        default = descriptor_pool.Default()
        for dep in ("google/protobuf/wrappers.proto",
                    "google/protobuf/any.proto",
                    "google/protobuf/descriptor.proto"):
            try:
                fdp = descriptor_pb2.FileDescriptorProto()
                default.FindFileByName(dep).CopyToProto(fdp)
                p.Add(fdp)
            except Exception:
                pass
        for name in ("Preset.proto", "ProductionAutomation.proto"):
            if name in fdps:
                p.Add(fdps[name])
        _pools[ver] = p
    return _pools[ver]


@functools.lru_cache(maxsize=512)
def _message_class(name, ver):
    desc = pool(ver).FindMessageTypeByName(f"{PACKAGE}.{name}Message")
    return _msg_class(desc)


def message_class(command, version=None):
    """Return the generated message class for a command id or name.

    Memoized on the RESOLVED generation, not on `version=None` — otherwise a
    later `set_version` would keep handing back the previous schema's class.
    Worth caching: a client that decodes streamed telemetry calls this for every
    frame, and the pool lookup plus class build is the bulk of that work.
    """
    name = command if isinstance(command, str) else COMMANDS[command]
    return _message_class(name, version or _active_version)


# --- message (protobuf + trailer) -----------------------------------------
def encode_message(command, proto_bytes=b"", hash_=0):
    """protobuf bytes + 8-byte trailer -> full message bytes."""
    cmd = command if isinstance(command, int) else NAME_TO_CMD[command]
    trailer = struct.pack("<HIH", cmd, 0, hash_)
    return proto_bytes + trailer


def decode_message(data):
    """full message bytes -> (command_id, protobuf_bytes, hash).
    Large messages gzip the entire protobuf payload; the QC auto-detects by
    magic, so we transparently decompress here."""
    if len(data) < 8:
        return None, data, 0
    proto, trailer = data[:-8], data[-8:]
    cmd, _reserved, hash_ = struct.unpack("<HIH", trailer)
    if proto[:3] == b"\x1f\x8b\x08":
        un = gunzip(proto)
        if un is not None:
            proto = un
    return cmd, proto, hash_


# --- HID report chunking ---------------------------------------------------
def message_to_reports(message, report_id=REPORT_HOST_TO_QC):
    """Split a full message into 129-byte HID write buffers (report id + 128)."""
    reports = []
    if len(message) <= CHUNK_MAX:
        chunks = [(message, FLAG_SINGLE)]
    else:
        chunks = []
        off = 0
        while off < len(message):
            part = message[off:off + CHUNK_MAX]
            off += CHUNK_MAX
            if off >= len(message):
                flag = FLAG_LAST
            elif not chunks:
                flag = FLAG_FIRST
            else:
                flag = FLAG_MIDDLE
            chunks.append((part, flag))
    for part, flag in chunks:
        buf = bytearray(1 + REPORT_SIZE)
        buf[0] = report_id
        buf[1] = len(part)
        buf[2] = flag
        buf[3:3 + len(part)] = part
        reports.append(bytes(buf))
    return reports


class Reassembler:
    """Feed raw input-report buffers; yields complete messages."""
    def __init__(self):
        self._buf = bytearray()

    def feed(self, report, has_report_id=False):
        # `report` is either [chunkLen][flags][payload] (has_report_id=False,
        # e.g. the IOKit input callback) or [reportId][chunkLen][flags][payload].
        b = bytes(report)
        if has_report_id:
            b = b[1:]
        if len(b) < 2:
            return []
        chunk_len, flags = b[0], b[1]
        payload = b[2:2 + chunk_len]
        out = []
        if flags & FLAG_FIRST:
            self._buf = bytearray()
        self._buf += payload
        if flags & FLAG_LAST:
            out.append(bytes(self._buf))
            self._buf = bytearray()
        return out


# --- gzip helper (sub-message compression) --------------------------------
def gunzip(data):
    i = data.find(b"\x1f\x8b\x08")
    if i < 0:
        return None
    try:
        return zlib.decompress(data[i:], 16 + zlib.MAX_WBITS)
    except Exception:
        try:
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data[i:])
        except Exception:
            return None


def gzip_bytes(data):
    co = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    return co.compress(data) + co.flush()
