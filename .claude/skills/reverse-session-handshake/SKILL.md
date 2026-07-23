---
name: reverse-session-handshake
description: Figure out why a re-implemented client can send/receive individual messages but the device won't stream state, answer READs, or accept edits — i.e. reverse the required connect handshake and keepalive/heartbeat that makes a session "active". Use when your client talks to the device but behaves differently from the official app (no push, no stream, writes ignored).
---

# Reverse a device's session handshake + heartbeat

Symptom: your client opens the device and gets identity/simple replies, but the
device won't push state, answer most READs, or apply edits — while the official app
gets a full live stream. The difference is almost always a **session handshake** plus
a **heartbeat** the device requires before it treats a client as present.

## Method
1. **Capture the official app's connect sequence** (interposer, verbose). Decode the
   first ~50 host→device messages in order.
2. **Diff against what you send.** Look for messages you're missing or sending
   differently. Common requirements:
   - a **reset/session** message carrying a session id,
   - the client **announcing its version** (compatibility gate),
   - a **connection/"online" declaration**,
   - a **burst of state READs** on connect (this often "subscribes" the client),
   - a **steady keepalive/heartbeat** — and the *contents matter*: an empty keepalive
     may not count. In this repo the QC only streams while it receives
     `KeepAlive{action:UPDATE, is_online:true}` ~5×/sec; an empty one was ignored.
3. **Run the heartbeat on a background thread** and serialize all sends behind a lock
   (a heartbeat thread + main thread sending concurrently will corrupt chunked frames).
4. **Re-test the read/stream** (e.g. request the full current state the way the app
   does on boot). Once the session is "live", READs and edits work.

## Notes
- **Writes may land even without the heartbeat, but reads/streams need it** — verify
  each independently; don't assume "no response" means "not sent".
- Get ground truth cheaply: have a human watch the device, or read back via the
  official app / a full-state request; don't debug blind.
- The device may distinguish "the active editor". Matching the app's exact connect
  order (not just the individual messages) can be what flips it live.

Reference: `src/qc_mcp/transport.py` (`_handshake`, `_start_heartbeat`,
`get_current_preset`). See PROTOCOL.md §3.
