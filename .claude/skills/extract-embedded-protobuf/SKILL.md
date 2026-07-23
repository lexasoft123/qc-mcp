---
name: extract-embedded-protobuf
description: Recover Protocol Buffers message schemas (names, field numbers, types, enums) statically from a compiled binary that uses protobuf, then decode/encode messages dynamically without .proto sources. Use when an app's wire format is protobuf and you need the schema — e.g. reversing a device/app protocol whose payloads are protobuf, or reading captured protobuf traffic.
---

# Extract embedded protobuf schemas from a binary

protobuf's generated C++ embeds each `.proto`'s serialized `FileDescriptorProto` in
the binary. You can recover the full schema (message names, field numbers/types,
enums) and then build message classes at runtime — no `.proto` files or `protoc`.

## How to find them
Each embedded descriptor begins with field #1 (name): tag `0x0A`, a length byte,
then the `.proto` filename. Scan for `".proto"`, walk back to the `0x0A <len>`
marker, then let `descriptor_pb2.FileDescriptorProto` parse from there (walking the
wire format to find the blob end). This repo's `tools/extract_protos.py` does exactly
this and prints readable `.proto` output:
```bash
strings -n8 "$BIN" | grep -E "\.proto"          # see which schemas are embedded
python3 tools/extract_protos.py                  # recover + render them
```
Also grep the mangled C++ symbols for the message set and command enum:
```bash
strings -n5 "$BIN" | grep -E "MessageReceiver|MessageSender" | sort -u
strings -n8 "$BIN" | grep -oE "<.*type=\"[A-Za-z]+\" value=\"[0-9]+\"" # sometimes an XML cmd table
```

## Decode/encode without .proto sources
Build a `DescriptorPool` from the recovered `FileDescriptorProto`s and make message
classes dynamically:
```python
from google.protobuf import descriptor_pool, message_factory, descriptor_pb2
pool = descriptor_pool.DescriptorPool()
# add google well-known deps from the Default pool first, then your files in dep order
for fdp in recovered_in_dependency_order:      # e.g. Preset.proto before ProductionAutomation.proto
    pool.Add(fdp)
def msg_class(fullname):
    desc = pool.FindMessageTypeByName(fullname)
    return (message_factory.GetMessageClass(desc)
            if hasattr(message_factory,"GetMessageClass")
            else message_factory.MessageFactory().GetPrototype(desc))
```
Cache the recovered descriptors as a serialized `FileDescriptorSet` (`.pb`) so you
don't rescan the binary at runtime (see `src/qc_mcp/protocol.py`).

## Notes
- The top-level command/enum map (e.g. `CortexMessageType.Enum`) tells you which
  message a wire command id decodes to.
- To read raw/unknown protobuf quickly: `protoc --decode_raw < blob`, or a small
  tag/wire-type walker (see `verify_frames.py:raw_protobuf_fields`).
- Sub-messages may be **gzip-compressed** inside a `bytes` field (magic `1f 8b 08`).

Worked example: recovered `Preset.proto` (BinaryPreset/Chain/Model) and
`ProductionAutomation.proto` (71 message types) from Cortex Control. See PROTOCOL.md §4-5.
