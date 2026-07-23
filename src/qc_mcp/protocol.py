"""Quad Cortex control protocol: protobuf messages over chunked 128-byte HID
reports. Reverse-engineered from Cortex Control; see PROTOCOL.md.

Layer stack (host <-> QC):
  report  = [reportId][chunkLen][flags][payload...]      (128 data bytes)
  message = <protobuf bytes> + [command u16 LE][u32 reserved][u16 hash]
"""
from __future__ import annotations
import os
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
}
NAME_TO_CMD = {v: k for k, v in COMMANDS.items()}

# MessageAction.Enum
ACTION = {"CREATE": 0, "UPDATE": 1, "DELETE": 2, "READ": 3, "MOVE": 4,
          "COPY": 5, "UPLOAD": 6, "DOWNLOAD": 7, "SWAP": 8}

REPORT_HOST_TO_QC = 0x02
REPORT_QC_TO_HOST = 0x01
FLAG_FIRST, FLAG_LAST, FLAG_SINGLE, FLAG_MIDDLE = 0x40, 0x80, 0xC0, 0x00
REPORT_SIZE = 128           # data bytes per report (excl. report id)
CHUNK_MAX = REPORT_SIZE - 2  # minus [chunkLen][flags] = 126

_pool = None


_DESCRIPTOR_SET = os.path.join(os.path.dirname(__file__), "qc_descriptors.pb")


def _recovered_fdps():
    """Prefer the bundled descriptor set; fall back to scanning the app binary."""
    from google.protobuf import descriptor_pb2
    if os.path.exists(_DESCRIPTOR_SET):
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(open(_DESCRIPTOR_SET, "rb").read())
        return {f.name: f for f in fds.file}
    # fallback: scan the Cortex Control binary directly
    tools = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
    sys.path.insert(0, os.path.abspath(tools))
    import extract_protos
    return extract_protos.recover()


def pool():
    global _pool
    if _pool is None:
        _pool = descriptor_pool.DescriptorPool()
        fdps = _recovered_fdps()
        from google.protobuf import descriptor_pb2
        default = descriptor_pool.Default()
        for dep in ("google/protobuf/wrappers.proto",
                    "google/protobuf/any.proto",
                    "google/protobuf/descriptor.proto"):
            try:
                fdp = descriptor_pb2.FileDescriptorProto()
                default.FindFileByName(dep).CopyToProto(fdp)
                _pool.Add(fdp)
            except Exception:
                pass
        for name in ("Preset.proto", "ProductionAutomation.proto"):
            if name in fdps:
                _pool.Add(fdps[name])
    return _pool


def message_class(command):
    """Return the generated message class for a command id or name."""
    name = command if isinstance(command, str) else COMMANDS[command]
    desc = pool().FindMessageTypeByName(f"{PACKAGE}.{name}Message")
    return _msg_class(desc)


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
