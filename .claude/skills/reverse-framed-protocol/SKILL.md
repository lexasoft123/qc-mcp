---
name: reverse-framed-protocol
description: Reverse the binary framing that wraps known payloads (header/trailer, command id, length, chunking/flags, CRC, compression) by aligning captured host↔device traffic against payloads you can already decode. Use after you have captured real traffic and know the payload schema (e.g. protobuf) but not how messages are delimited, routed, or reassembled on the wire.
---

# Reverse a binary framing layer from labeled captures

Once you can capture traffic and decode payloads (e.g. protobuf), the framing falls
out by aligning bytes against known structure. Work outward: transport chunking →
message boundaries → header/trailer fields → compression.

## Approach
1. **Capture real frames** with direction + timestamps (see `macos-dylib-interpose`).
   Save length-prefixed so you can re-parse.

2. **Find the transport chunking.** For fixed-size reports/packets, inspect the first
   bytes: often `[reportId][len][flags][payload...]`. Flag bits usually encode
   FIRST/MIDDLE/LAST/SINGLE — confirm by watching a large message span many packets
   (e.g. `0x40`=first, `0x00`=middle, `0x80`=last, `0xC0`=single). Reassemble by
   concatenating payloads across FIRST..LAST **per direction** (host and device
   streams interleave — use separate reassemblers).

3. **Find the message envelope.** Reassemble a message whose payload you recognize
   (e.g. a protobuf you can parse), then look at the bytes *around* it:
   - A short constant prefix/suffix that varies by message type = the **command id**
     (match it to the recovered command enum). In this repo it was an 8-byte
     **trailer** `[cmd u16 LE][reserved u32][hash u16]`, not a header.
   - Verify at scale: strip the candidate header/trailer, protobuf-parse the rest,
     and check the command↔message-type correlation holds across hundreds of frames
     (`tools/verify_frames.py` does this — 120/120 validated).

4. **Detect compression / hashing.** Look for gzip magic `1f 8b 08`; large sub-fields
   are often gzipped. Error strings in the binary ("crc mismatch", "payload_hash")
   tell you which fields to expect.

5. **Learn edit/GUI operations by labeled capture.** To reverse an operation you
   can't guess (e.g. "delete a split", "assign to scene"), capture the app performing
   exactly that one action and diff the messages. Small, isolated GUI actions →
   unambiguous messages (e.g. removing a split was simply `split_control_points=(-1,-1)`).

## Tools in this repo
- `tools/hid_capture.py` / interposer log — raw capture
- `tools/analyze_log.py` — reassemble chunked messages from a log
- `tools/verify_frames.py` — validate the trailer/command model against a schema pool

## Gotchas
- **Read vs delta indexing may differ**: in a full-state read, array *position* is the
  index (row/column/param); the id/column/index *fields* are only set in delta edits.
- A whole-object write may **merge** rather than replace — check before assuming.
- Keep captures private: they may contain session ids / auth tokens.

Worked example: QC framing (128-byte reports, flag bits, 8-byte command trailer,
gzipped sub-messages). See PROTOCOL.md §2.
