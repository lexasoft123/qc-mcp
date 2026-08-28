#!/usr/bin/env python3
"""Refresh the bundled device catalog (`src/qc_mcp/ModelRepo.xml`) from a device.

The QC ships its own model catalog over the wire — ModelRepo(51) READ returns a
gzip+tar holding ModelRepo.xml — so a firmware update that adds devices shows up
here. Run after a CorOS update, then diff to see what is new:

    tools/dump_model_repo.py                    # write the snapshot
    tools/dump_model_repo.py --diff             # compare device vs bundled
    tools/dump_model_repo.py -o /tmp/mr.xml     # write somewhere else
"""
import argparse
import io
import os
import sys
import tarfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from qc_mcp import catalog as C          # noqa: E402
from qc_mcp.transport import QuadCortex  # noqa: E402

BUNDLED = os.path.join(ROOT, "src", "qc_mcp", "ModelRepo.xml")


def fetch(timeout_ms=30000):
    """Return the raw ModelRepo.xml bytes read from the connected device."""
    bridge = os.environ.get("QC_BRIDGE", "1") != "0"
    qc = QuadCortex(bridge=bridge).open()
    try:
        msg = qc.read_state("ModelRepo", timeout_ms=timeout_ms)
        payload = getattr(msg, "model_repo_payload", b"")
        if not payload:
            sys.exit("error: device returned an empty ModelRepo payload")
        print(f"device {qc.firmware or '?'} sent {len(payload)} bytes", file=sys.stderr)
    finally:
        qc.close()
    if payload[:3] == b"\x1f\x8b\x08":
        payload = zlib.decompress(payload, 16 + zlib.MAX_WBITS)
    with tarfile.open(fileobj=io.BytesIO(payload)) as tf:
        return tf.extractfile("ModelRepo.xml").read()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=BUNDLED)
    ap.add_argument("--diff", action="store_true",
                    help="report added/removed/renamed models instead of writing")
    args = ap.parse_args()

    xml = fetch()
    live = C._parse(xml)
    with open(BUNDLED, "rb") as fh:
        have = C._parse(fh.read())

    added = sorted(set(live) - set(have))
    gone = sorted(set(have) - set(live))
    renamed = [(i, have[i]["name"], live[i]["name"]) for i in sorted(set(live) & set(have))
               if have[i]["name"] != live[i]["name"]]
    print(f"bundled {len(have)} models, device {len(live)}", file=sys.stderr)
    for i in added:
        print(f"  + {i:6d}  {live[i]['name']}  [{live[i]['category']}]")
    for i in gone:
        print(f"  - {i:6d}  {have[i]['name']}  [{have[i]['category']}]  REMOVED")
    for i, before, after in renamed:
        print(f"  ~ {i:6d}  {before} -> {after}")

    if args.diff:
        return
    with open(args.out, "wb") as fh:
        fh.write(xml)
    print(f"wrote {args.out} ({len(xml)} bytes, {len(live)} models)", file=sys.stderr)


if __name__ == "__main__":
    main()
