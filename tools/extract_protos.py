#!/usr/bin/env python3
"""Recover embedded protobuf FileDescriptorProtos from the Cortex Control binary.

protobuf's C++ codegen embeds each .proto's serialized FileDescriptorProto as a
byte blob. Each blob begins with field #1 (name), i.e. tag 0x0A, a length byte,
then the ".proto" filename. We locate those markers, walk the protobuf wire
format field-by-field to find the blob's end, then parse it with descriptor_pb2
and render human-readable .proto definitions.
"""
import sys
from google.protobuf import descriptor_pb2

BIN = "/Applications/Neural DSP/Cortex Control.app/Contents/MacOS/Cortex Control"
TARGET_SUFFIX = b".proto"

# FileDescriptorProto top-level field numbers we expect (for end-detection).
VALID_TOP_FIELDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}


def read_varint(buf, i):
    shift = 0
    result = 0
    start = i
    while i < len(buf):
        b = buf[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            break
    raise ValueError(f"bad varint at {start}")


def scan_message_end(buf, start):
    """Walk protobuf fields from `start`; return end offset where the wire
    format stops looking like a valid FileDescriptorProto."""
    i = start
    while i < len(buf):
        tag_start = i
        try:
            tag, i = read_varint(buf, i)
        except ValueError:
            return tag_start
        field_no = tag >> 3
        wire = tag & 0x7
        if field_no == 0 or field_no not in VALID_TOP_FIELDS:
            return tag_start
        if wire == 0:  # varint
            try:
                _, i = read_varint(buf, i)
            except ValueError:
                return tag_start
        elif wire == 2:  # length-delimited
            try:
                ln, i = read_varint(buf, i)
            except ValueError:
                return tag_start
            if i + ln > len(buf) or ln > 5_000_000:
                return tag_start
            i += ln
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            return tag_start
    return i


TYPE_NAMES = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 10: "group",
    11: "message", 12: "bytes", 13: "uint32", 14: "enum", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}
LABELS = {1: "optional", 2: "required", 3: "repeated"}


def render(fdp):
    out = []
    out.append(f'// file: {fdp.name}  syntax={fdp.syntax or "proto2"} '
               f'package={fdp.package or "-"}')
    for dep in fdp.dependency:
        out.append(f'// import "{dep}"')

    def render_enum(e, indent=""):
        out.append(f'{indent}enum {e.name} {{')
        for v in e.value:
            out.append(f'{indent}  {v.name} = {v.number};')
        out.append(f'{indent}}}')

    def render_msg(m, indent=""):
        out.append(f'{indent}message {m.name} {{')
        for f in m.field:
            t = f.type_name.lstrip(".") if f.type in (11, 14) else \
                TYPE_NAMES.get(f.type, f"type{f.type}")
            lbl = LABELS.get(f.label, "")
            lbl = (lbl + " ") if (lbl and lbl != "optional") else ""
            out.append(f'{indent}  {lbl}{t} {f.name} = {f.number};')
        for e in m.enum_type:
            render_enum(e, indent + "  ")
        for nm in m.nested_type:
            render_msg(nm, indent + "  ")
        out.append(f'{indent}}}')

    for e in fdp.enum_type:
        render_enum(e)
    for m in fdp.message_type:
        render_msg(m)
    return "\n".join(out)


def recover(binary_path=BIN):
    """Return {proto_name: FileDescriptorProto} recovered from the binary."""
    with open(binary_path, "rb") as f:
        data = f.read()
    seen = {}
    idx = 0
    while True:
        j = data.find(TARGET_SUFFIX, idx)
        if j < 0:
            break
        idx = j + 1
        for back in range(1, 60):
            ns = j - back
            lp = ns - 1
            tp = ns - 2
            if tp < 0:
                break
            if data[tp] != 0x0A:
                continue
            name_len = data[lp]
            if name_len != (j - ns + len(TARGET_SUFFIX)):
                continue
            name = data[ns:ns + name_len]
            if not all(32 <= c < 127 for c in name):
                continue
            start = tp
            end = scan_message_end(data, start)
            blob = data[start:end]
            fdp = descriptor_pb2.FileDescriptorProto()
            try:
                fdp.ParseFromString(blob)
            except Exception:
                continue
            if fdp.name.encode() != name or fdp.name in seen:
                break
            if not (fdp.message_type or fdp.enum_type):
                break
            seen[fdp.name] = fdp
            break
    return seen


def main():
    with open(BIN, "rb") as f:
        data = f.read()
    print(f"binary size: {len(data)} bytes", file=sys.stderr)

    seen = {}
    idx = 0
    while True:
        j = data.find(TARGET_SUFFIX, idx)
        if j < 0:
            break
        idx = j + 1
        # Walk back to a plausible FileDescriptorProto start: 0x0A <len> <name>
        # name length = (j + len('.proto')) - name_start
        # Try candidate name-start positions.
        for back in range(1, 60):
            ns = j - (back)  # potential first char of the name string
            lp = ns - 1      # length byte
            tp = ns - 2       # tag byte 0x0A
            if tp < 0:
                break
            if data[tp] != 0x0A:
                continue
            name_len = data[lp]
            if name_len != (j - ns + len(TARGET_SUFFIX)):
                continue
            name = data[ns:ns + name_len]
            if not all(32 <= c < 127 for c in name):
                continue
            start = tp
            end = scan_message_end(data, start)
            blob = data[start:end]
            fdp = descriptor_pb2.FileDescriptorProto()
            try:
                fdp.ParseFromString(blob)
            except Exception:
                continue
            if fdp.name.encode() != name:
                continue
            if fdp.name in seen:
                break
            # sanity: must define at least one message or enum
            if not (fdp.message_type or fdp.enum_type):
                break
            seen[fdp.name] = (len(blob), fdp)
            break

    print(f"\nRecovered {len(seen)} descriptor(s):", file=sys.stderr)
    for name, (ln, fdp) in sorted(seen.items()):
        print(f"  {name}: {ln} bytes, "
              f"{len(fdp.message_type)} messages, {len(fdp.enum_type)} enums",
              file=sys.stderr)

    for name, (ln, fdp) in sorted(seen.items()):
        if name.startswith("google/"):
            continue
        print("\n" + "=" * 70)
        print(render(fdp))


if __name__ == "__main__":
    main()
