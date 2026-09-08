"""Who holds the Quad Cortex, and how.

Everything used to infer this, and the inferences disagreed. `pgrep` for the
app, the existence of two FIFOs for the bridge, a socket connect for the daemon,
a saved preference for the mode — four guesses, none of which says who owns the
device or what they actually opened. So Patchbay adopted daemons it could not
identify, `stop()` fell back to `pkill -f`, and the interface described a
session nobody had.

This is the one answer. Whoever opens the device writes it; everyone else reads
it. It is advisory rather than an OS lock, deliberately: the point is to say
what is true, and to make taking over a decision somebody makes on purpose.

    {"pid": 4711, "owner": "daemon", "mode": "bridge", "socket": "…/daemon.sock",
     "started_at": 1757320000.0, "firmware": "4.1.0",
     "launched_by": "patchbay", "app_pid": 349}

`pid` is the liveness test — a record whose process is gone is stale and may be
taken without ceremony. Nothing here is secret, and nothing here is trusted
beyond what a stat can confirm.
"""
from __future__ import annotations

import json
import os
import sys
import time

__all__ = ["path", "read", "acquire", "release", "alive", "Lock", "Held"]

OWNERS = ("daemon", "mcp", "bench")
MODES = ("bridge", "shared", "direct")


class Held(Exception):
    """Somebody else has the device, and they are still running."""

    def __init__(self, lock):
        self.lock = lock
        who = lock.get("owner", "something")
        mode = lock.get("mode", "?")
        pid = lock.get("pid", "?")
        by = lock.get("launched_by")
        super().__init__(
            "the Quad Cortex is already held by %s (pid %s) in %s mode%s"
            % (who, pid, mode, ", started by %s" % by if by else ""))


def path(socket_path=None):
    """Next to the daemon socket, so one directory holds the whole session."""
    if socket_path:
        return os.path.join(os.path.dirname(socket_path), "session.json")
    from .server import default_socket
    return os.path.join(os.path.dirname(default_socket()), "session.json")


def alive(pid):
    """Is that process still there? Signal 0 asks without disturbing it."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # someone else's process, but it exists
    except (OSError, ValueError, TypeError):
        return False
    return True


def read(socket_path=None):
    """The current record, or None. A record whose process is gone is None too:
    a stale file is not an owner, and every caller means the live question."""
    p = path(socket_path)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not alive(data.get("pid")):
        return None
    return data


def read_raw(socket_path=None):
    """The record as written, dead or alive — for diagnostics that want to say
    'a stale lock from pid N is being cleaned up' rather than nothing at all."""
    try:
        with open(path(socket_path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def acquire(owner, mode, socket_path=None, takeover=False, **extra):
    """Claim the device, or raise Held.

    `takeover=True` writes over a LIVE owner. It does not stop them — stopping
    somebody is the caller's business, and doing it here would hide it. It is
    for the caller that has already ended the other session and wants the record
    to say so.
    """
    if owner not in OWNERS:
        raise ValueError("owner must be one of %s" % (OWNERS,))
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    p = path(socket_path)
    current = read(socket_path)
    if current and not takeover and current.get("pid") != os.getpid():
        raise Held(current)
    record = {
        "pid": os.getpid(),
        "owner": owner,
        "mode": mode,
        "socket": socket_path or "",
        "started_at": time.time(),
        "platform": sys.platform,
    }
    record.update({k: v for k, v in extra.items() if v is not None})
    _write(p, record)
    return record


def update(socket_path=None, **fields):
    """Fill in what was not known at acquire time — the firmware, mostly, which
    the device only reports once the session is open."""
    current = read_raw(socket_path)
    if not current or current.get("pid") != os.getpid():
        return None
    current.update({k: v for k, v in fields.items() if v is not None})
    _write(path(socket_path), current)
    return current


def release(socket_path=None):
    """Give it up — but only our own record. Deleting somebody else's is how the
    old code lost track of adopted daemons."""
    current = read_raw(socket_path)
    if current and current.get("pid") not in (None, os.getpid()):
        return False
    try:
        os.unlink(path(socket_path))
    except (FileNotFoundError, OSError):
        return False
    return True


def _write(p, record):
    """Atomic: write beside it and rename, so a reader never sees half a file."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


class Lock:
    """`with Lock('daemon', 'bridge', socket): …` — released however you leave."""

    def __init__(self, owner, mode, socket_path=None, takeover=False, **extra):
        self.owner, self.mode = owner, mode
        self.socket_path, self.takeover, self.extra = socket_path, takeover, extra
        self.record = None

    def __enter__(self):
        self.record = acquire(self.owner, self.mode, self.socket_path,
                              takeover=self.takeover, **self.extra)
        return self

    def update(self, **fields):
        self.record = update(self.socket_path, **fields) or self.record
        return self.record

    def __exit__(self, *_exc):
        release(self.socket_path)
        return False
