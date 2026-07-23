---
name: macho-string-xref
description: Locate and read a specific code path in a Mach-O (arm64) binary by cross-referencing a known string, using capstone — to reverse the logic around it (framing/CRC parsing, error handling, a specific handler) without a full decompiler. Use when static string clues exist and you need to read the surrounding disassembly.
---

# Mach-O string-xref disassembly (arm64, capstone)

When a binary has telltale strings ("header crc mismatch", "command not supported"),
you can jump straight to the code that references them and read the logic, without
Ghidra. This repo's `tools/xref_dis.py` implements it.

## How it works
1. Parse the Mach-O (handle fat → arm64 slice); map `__text` and the cstring sections
   (`__cstring`, `__const`).
2. Find the target string's virtual address.
3. Disassemble `__text` with capstone; **resync past data** (capstone stops at the
   first non-instruction — loop and skip 4 bytes on failure or you'll truncate).
4. Track `adrp` page per register, then match `add reg, reg, #imm` (or `adr`) whose
   computed address equals the string address = an xref. (Strip the `#` from the
   `adrp` immediate — a classic bug that yields zero xrefs.)
5. Print a window around each xref; walk back to a prologue (`pacibsp`/`stp x29,x30`)
   or the previous `ret` for the function start.

```bash
pip install capstone
python3 tools/xref_dis.py "header crc mismatch" 45
```

## Reading tips
- Constants like `0x8b1f` (= gzip magic `1f 8b`) reveal decompression; CRC tables /
  polynomials reveal the checksum.
- Jump tables (`ldrh` + `br`) mark streaming state machines — hard to read fully;
  prefer capturing real frames (`reverse-framed-protocol`) over decoding these by hand.
- Use this to *confirm* field offsets/algorithms, not as the primary method.

Worked example: confirmed the QC frame parser checks gzip magic and a CRC. See
PROTOCOL.md §2.
