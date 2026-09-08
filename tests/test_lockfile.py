"""The one record that says who holds the device.

Offline: every case is a file and a pid, and the only pids used are this
process (certainly alive) and one that certainly is not.
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import lockfile as L   # noqa: E402

DEAD = 999_999_999      # no such process, and out of pid_max on both platforms
ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("FAIL: %s" % name)


def sock(d):
    return os.path.join(d, "daemon.sock")


def main():
    with tempfile.TemporaryDirectory() as d:
        s = sock(d)

        # --- nothing there ---
        check("empty dir has no owner", L.read(s) is None)
        check("releasing nothing is not an error", L.release(s) is False)

        # --- claiming ---
        rec = L.acquire("daemon", "bridge", s, launched_by="patchbay")
        check("acquire returns the record", rec["owner"] == "daemon" and rec["mode"] == "bridge")
        check("record carries our pid", rec["pid"] == os.getpid())
        check("extras are kept", rec["launched_by"] == "patchbay")
        check("it is on disk", os.path.exists(L.path(s)))
        check("and readable", (L.read(s) or {}).get("mode") == "bridge")

        # --- re-claiming by the same process is not a conflict ---
        again = L.acquire("daemon", "direct", s)
        check("our own record can be rewritten", again["mode"] == "direct")

        # --- a live foreign owner blocks ---
        L._write(L.path(s), {"pid": os.getpid(), "owner": "mcp", "mode": "direct"})
        # (same pid, so simulate a foreign one by pretending: use a live pid we
        #  do not own the record for — the check is pid != getpid())
        L._write(L.path(s), {"pid": 1, "owner": "mcp", "mode": "direct"})   # launchd: alive, not us
        held = None
        try:
            L.acquire("daemon", "bridge", s)
        except L.Held as e:
            held = e
        check("a live foreign owner raises Held", held is not None)
        check("and says who and how", "mcp" in str(held) and "direct" in str(held))
        check("the record is untouched by the refusal", (L.read(s) or {}).get("owner") == "mcp")

        # --- takeover is explicit ---
        took = L.acquire("daemon", "bridge", s, takeover=True)
        check("takeover writes over a live owner", took["owner"] == "daemon")
        check("and the file now names us", (L.read(s) or {}).get("pid") == os.getpid())

        # --- a dead owner is not an owner ---
        L._write(L.path(s), {"pid": DEAD, "owner": "daemon", "mode": "bridge"})
        check("a stale record reads as no owner", L.read(s) is None)
        check("but is still visible raw", (L.read_raw(s) or {}).get("pid") == DEAD)
        fresh = L.acquire("daemon", "direct", s)
        check("and can be claimed with no takeover", fresh["pid"] == os.getpid())

        # --- release only removes our own ---
        L._write(L.path(s), {"pid": 1, "owner": "mcp", "mode": "direct"})
        check("release refuses somebody else's record", L.release(s) is False)
        check("and leaves it in place", os.path.exists(L.path(s)))
        L._write(L.path(s), {"pid": os.getpid(), "owner": "daemon", "mode": "bridge"})
        check("release removes ours", L.release(s) is True)
        check("and it is gone", not os.path.exists(L.path(s)))

        # --- update fills in what was not known at acquire time ---
        L.acquire("daemon", "bridge", s)
        L.update(s, firmware="4.1.0")
        check("update merges", (L.read(s) or {}).get("firmware") == "4.1.0")
        check("and keeps the rest", (L.read(s) or {}).get("owner") == "daemon")
        L._write(L.path(s), {"pid": 1, "owner": "mcp", "mode": "direct"})
        check("update refuses somebody else's record", L.update(s, firmware="9") is None)

        # --- rubbish on disk is not an owner ---
        with open(L.path(s), "w") as fh:
            fh.write("{ not json")
        check("unparseable file reads as no owner", L.read(s) is None)
        with open(L.path(s), "w") as fh:
            json.dump([1, 2, 3], fh)
        check("a non-object reads as no owner", L.read(s) is None)

        # --- the context manager ---
        with L.Lock("daemon", "bridge", s) as lock:
            check("Lock claims on enter", (L.read(s) or {}).get("owner") == "daemon")
            lock.update(firmware="4.0.0")
            check("Lock.update works", (L.read(s) or {}).get("firmware") == "4.0.0")
        check("Lock releases on exit", L.read(s) is None)

        try:
            with L.Lock("daemon", "direct", s):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        check("Lock releases even when the body throws", L.read(s) is None)

        # --- validation ---
        for bad in (("nobody", "bridge"), ("daemon", "sideways")):
            try:
                L.acquire(bad[0], bad[1], s)
                check("rejects %r" % (bad,), False)
            except ValueError:
                check("rejects %r" % (bad,), True)

        # --- the write is atomic: no .tmp left behind ---
        L.acquire("daemon", "bridge", s)
        check("no temp files left", not [f for f in os.listdir(d) if f.endswith(".tmp")])
        L.release(s)

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
