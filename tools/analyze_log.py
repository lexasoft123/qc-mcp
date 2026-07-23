#!/usr/bin/env python3
"""Parse the interposer HID log, reassemble chunked messages, and dump structure
to reverse the message-level header (command / crc / hash) + protobuf payload."""
import sys, zlib

LOG = sys.argv[1] if len(sys.argv) > 1 else "interceptor/hid_log.txt"


def parse(path):
    frames = []
    for line in open(path):
        line = line.strip()
        if " id=" not in line or " len=" not in line:
            continue
        try:
            ts, dir_, idp, lenp, hexs = line.split(None, 4)
        except ValueError:
            continue
        b = bytes.fromhex(hexs)
        frames.append((float(ts), dir_.strip(), b))
    return frames


def reassemble(frames):
    """Group frames into messages per direction using the flags byte."""
    msgs = []
    buf = {"OUT": b"", "IN": b""}
    for ts, d, b in frames:
        if len(b) < 3:
            continue
        report_id = b[0]
        chunk_len = b[1]
        flags = b[2]
        payload = b[3:3 + chunk_len]
        first = bool(flags & 0x40)
        last = bool(flags & 0x80)
        if first:
            buf[d] = b""
        buf[d] += payload
        if last:
            msgs.append((ts, d, flags, buf[d]))
            buf[d] = b""
    return msgs


def try_gunzip(data):
    i = data.find(b"\x1f\x8b\x08")
    if i < 0:
        return None, i
    try:
        return zlib.decompress(data[i:], 16 + zlib.MAX_WBITS), i
    except Exception:
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            return d.decompress(data[i:]), i
        except Exception:
            return None, i


def main():
    frames = parse(LOG)
    print(f"frames: {len(frames)}")
    msgs = reassemble(frames)
    print(f"reassembled messages: {len(msgs)}\n")
    for k, (ts, d, flags, m) in enumerate(msgs):
        head = m[:28].hex()
        gz, gzi = try_gunzip(m)
        extra = ""
        if gz is not None:
            extra = f"  [gzip@{gzi} -> {len(gz)}B decompressed]"
        print(f"#{k:03} {d} flags=0x{flags:02x} len={len(m):6} head={head}{extra}")
        if k < 24:
            print(f"      full[:64]={m[:64].hex()}")
    # Save reassembled messages for downstream protobuf decoding.
    import json, os
    os.makedirs("interceptor/msgs", exist_ok=True)
    for k, (ts, d, flags, m) in enumerate(msgs):
        open(f"interceptor/msgs/{k:03}_{d}.bin", "wb").write(m)
    print("\nsaved reassembled messages to interceptor/msgs/")


if __name__ == "__main__":
    main()
