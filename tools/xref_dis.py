#!/usr/bin/env python3
"""Minimal Mach-O (arm64) string-xref disassembler.
Finds a C string, locates ADRP/ADD references to it in __text, and disassembles
the enclosing function so we can read protocol framing logic (header/crc).

Usage: xref_dis.py "header crc mismatch" [context_instrs]
"""
import struct, sys
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

BIN = "/Applications/Neural DSP/Cortex Control.app/Contents/MacOS/Cortex Control"
TARGET = sys.argv[1].encode() if len(sys.argv) > 1 else b"header crc mismatch"
CTX = int(sys.argv[2]) if len(sys.argv) > 2 else 60

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFEED
LC_SEGMENT_64 = 0x19


def load_arm64_slice(data):
    magic = struct.unpack(">I", data[:4])[0]
    if magic in (FAT_MAGIC, FAT_CIGAM):
        n = struct.unpack(">I", data[4:8])[0]
        off = 8
        for _ in range(n):
            cputype, cpusub, offset, size, align = struct.unpack(">IIIII", data[off:off+20])
            off += 20
            if cputype == 0x0100000C:  # arm64
                return data[offset:offset+size], offset
        raise SystemExit("no arm64 slice")
    return data, 0


def parse(data):
    magic = struct.unpack("<I", data[:4])[0]
    assert magic == MH_MAGIC_64, hex(magic)
    ncmds = struct.unpack("<I", data[16:20])[0]
    off = 32
    sects = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack("<II", data[off:off+8])
        if cmd == LC_SEGMENT_64:
            segname = data[off+8:off+24].split(b"\0")[0].decode()
            nsects = struct.unpack("<I", data[off+64:off+68])[0]
            so = off + 72
            for _ in range(nsects):
                sn = data[so:so+16].split(b"\0")[0].decode()
                sgn = data[so+16:so+32].split(b"\0")[0].decode()
                addr, size = struct.unpack("<QQ", data[so+32:so+48])
                offset = struct.unpack("<I", data[so+48:so+52])[0]
                sects.append((sgn, sn, addr, size, offset))
                so += 80
        off += cmdsize
    return sects


def main():
    raw = open(BIN, "rb").read()
    data, _ = load_arm64_slice(raw)
    sects = parse(data)
    text = next((s for s in sects if s[1] == "__text"), None)
    # locate string across cstring-ish sections
    str_addr = None
    for sgn, sn, addr, size, offset in sects:
        if sn in ("__cstring", "__const", "__oslogstring", "__cfstring"):
            blob = data[offset:offset+size]
            k = blob.find(TARGET)
            if k >= 0:
                str_addr = addr + k
                print(f"string {TARGET!r} @ 0x{str_addr:012x} in {sgn},{sn}")
                break
    if str_addr is None:
        raise SystemExit("string not found")

    taddr, tsize, toff = text[2], text[3], text[4]
    code = data[toff:toff+tsize]
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = False

    # First pass: decode all (resync past data), track adrp page, find refs.
    insns = []
    pos = 0
    while pos < len(code):
        chunk = list(md.disasm(code[pos:], taddr + pos))
        if not chunk:
            pos += 4
            continue
        insns.extend(chunk)
        last = chunk[-1]
        newpos = (last.address - taddr) + last.size
        if newpos <= pos:
            pos += 4
        else:
            pos = newpos
    print(f"disassembled {len(insns)} insns in __text")
    adrp_page = {}
    xref_idx = []
    for i, ins in enumerate(insns):
        m = ins.mnemonic
        op = ins.op_str
        if m == "adrp":
            try:
                reg, imm = op.split(", ")
                adrp_page[reg] = int(imm.replace("#", ""), 0)
            except Exception:
                pass
        elif m == "add" and "#" in op:
            parts = [p.strip() for p in op.split(",")]
            if len(parts) == 3 and parts[1] in adrp_page:
                try:
                    val = adrp_page[parts[1]] + int(parts[2].replace("#", ""), 0)
                    if val == str_addr:
                        xref_idx.append(i)
                except Exception:
                    pass
        elif m in ("adr",):
            parts = [p.strip() for p in op.split(",")]
            if len(parts) == 2:
                try:
                    if int(parts[1], 0) == str_addr:
                        xref_idx.append(i)
                except Exception:
                    pass

    print(f"found {len(xref_idx)} xref(s)")
    for xi in xref_idx:
        # find function start: walk back to prologue-ish boundary
        start = xi
        for j in range(xi, max(0, xi-400), -1):
            if insns[j].mnemonic in ("ret",) and j < xi-1:
                start = j+1
                break
            if insns[j].mnemonic == "pacibsp":
                start = j
                break
        print("\n" + "="*72)
        lo = max(start, xi-CTX)
        hi = min(len(insns), xi+CTX)
        for k in range(lo, hi):
            ins = insns[k]
            mark = "  <<< XREF" if k == xi else ""
            print(f"0x{ins.address:012x}  {ins.mnemonic:8} {ins.op_str}{mark}")


if __name__ == "__main__":
    main()
