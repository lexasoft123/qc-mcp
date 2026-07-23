# Reverse-engineering skills

Reusable Claude Code skills capturing the methodology used to reverse-engineer the
Quad Cortex USB protocol (see ../../PROTOCOL.md). They're written to generalize to
other hardware/app protocols, with the QC work as the worked example. Each lives in
`.claude/skills/<name>/SKILL.md` and is auto-discovered by Claude Code in this repo.

Rough order you'd apply them in a new protocol effort:

1. **reverse-hardware-transport** — find *where* the bytes flow (transport, interface,
   endpoints, how the app opens the device). Do this first.
2. **extract-embedded-protobuf** — if payloads are protobuf, recover the schema
   statically from the app binary and decode/encode dynamically.
3. **macos-dylib-interpose** — inject into the app to *capture* real traffic (and later
   *bridge* so your tool shares the app's session).
3b. **drive-gui-correlate-protocol** — automate the app's GUI (screenshot + click) and
   diff the captured traffic per action, to reverse GUI operations you can't guess and
   for scenario testing. Pairs with the interposer capture from step 3.
4. **reverse-framed-protocol** — align captures against known payloads to reverse the
   chunking, message envelope (header/trailer, command id), and compression.
5. **reverse-session-handshake** — if the device won't stream/answer/accept edits,
   reverse the required connect handshake + heartbeat.
6. **calibrate-param-mapping** — reverse normalized↔display value tapers (linear vs
   log) by capturing known values and curve-fitting.
7. **macho-string-xref** — read specific code paths (framing/CRC) via capstone when you
   need to *confirm* an algorithm; prefer capture over hand-decoding state machines.

Supporting tools referenced by the skills live in `../../tools/` and
`../../interceptor/`.

Ethics: these are for interop/debugging on software you're licensed to run and
hardware you own. Don't distribute re-signed app copies; keep capture logs (which can
contain session ids / auth tokens) out of version control.
