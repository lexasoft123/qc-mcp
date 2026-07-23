#!/usr/bin/env python3
"""Verify the frame model: message = protobuf payload + 8-byte trailer whose
first u16 (LE) is the CortexMessageType command id. Decode prefixes with a
dynamic descriptor pool built from the schemas recovered from the binary."""
import glob, struct, sys, zlib
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

sys.path.insert(0, "tools")
import extract_protos as ep  # reuse recovered FileDescriptorProtos

# CortexMessageType.Enum id -> name
CMD = {
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


def raw_protobuf_fields(data):
    """Minimal top-level protobuf field scan: returns list of (field, wire)."""
    out, i = [], 0
    def rv(i):
        s=r=0
        while i < len(data):
            b=data[i]; r|=(b&0x7f)<<s; i+=1
            if not b&0x80: return r,i
            s+=7
        raise ValueError
    try:
        while i < len(data):
            tag,i = rv(i); f=tag>>3; w=tag&7
            out.append((f,w))
            if w==0: _,i=rv(i)
            elif w==2:
                ln,i=rv(i); i+=ln
            elif w==5: i+=4
            elif w==1: i+=8
            else: return out, False
        return out, True
    except Exception:
        return out, False


def main():
    files = sorted(glob.glob("interceptor/msgs/*.bin"))
    ok = 0
    stats = {}
    for fn in files[:120]:
        m = open(fn,"rb").read()
        if len(m) < 8: continue
        trailer = m[-8:]
        cmd = struct.unpack("<H", trailer[:2])[0]
        rest = trailer[2:]
        pb = m[:-8]
        fields, clean = raw_protobuf_fields(pb)
        name = CMD.get(cmd, f"?{cmd}")
        stats[name] = stats.get(name,0)+1
        if cmd in CMD: ok += 1
        gz = "gzip" if b"\x1f\x8b\x08" in m else ""
        tag = fn.split("/")[-1]
        if int(tag[:3]) < 40:
            print(f"{tag} cmd={cmd:2}({name:16}) pb_ok={clean} "
                  f"trailer2-8={rest.hex()} fields={fields[:6]} {gz}")
    print(f"\nvalid-command trailers: {ok}/{min(120,len(files))}")
    print("command histogram:", dict(sorted(stats.items(), key=lambda x:-x[1])))


if __name__ == "__main__":
    main()
