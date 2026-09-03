"""The long-lived qc-mcp daemon: one process owns the device, many clients use it.

Why this exists
---------------
`qc-mcp` on its own is a stdio MCP server, and every client spawns its own copy.
That is fine for one client and impossible for two: on macOS IOKit hands the HID
device to a single owner, and even where a second handle is allowed the two
copies would each run their own handshake and heartbeat.

So the daemon holds exactly one `QuadCortex` — with its handshake, its heartbeat
and its session — and speaks a tiny transport-level protocol to any number of
attached clients. The split is deliberately as low as it can go: the daemon moves
*HID reports*, and every layer above (framing, gzip, protobuf, the catalog, the
preset model) still runs inside each client, unchanged and untouched by this file.

Wire protocol — newline-delimited JSON, both directions.

    -> {"id": 1, "op": "hello"}
    <- {"id": 1, "ok": true, "client": 0, "firmware": "4.1.0", "mode": "direct"}
    -> {"id": 2, "op": "write", "report_id": 0, "data": "<hex>", "include_id": false}
    <- {"id": 2, "ok": true}
    -> {"id": 3, "op": "read"}
    <- {"id": 3, "ok": true, "reports": [[0, "<hex>"], ...]}
    -> {"id": 4, "op": "status"}
    <- {"id": 4, "ok": true, "clients": 2, "reports": 91043, ...}

There are no unsolicited frames: every message from the daemon answers a request
id. Device->host reports are fanned out into a per-client queue, exactly as the
interposer's FIFO does, and a client collects its own with `read` — so a client
that stops polling cannot stall the others. Each client rejects the traffic that
is not its own by matching `request_id`, and the ranges handed out at hello are
disjoint so two of them can never collide.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

from .backend import BridgeError

_ENC = "utf-8"
#: Each attached client gets its own slice of the request_id space.
REQ_STRIDE = 0x10000
#: Keep at most this many broadcast reports per client if one stops reading.
QUEUE_CAP = 20000


# ─────────────────────────────────────────────────────── endpoint helpers ────

def _is_posix() -> bool:
    return hasattr(socket, "AF_UNIX")


def _listener(path: str):
    """A listening socket at `path`.

    POSIX gets a unix socket. Windows has no AF_UNIX in every Python build and no
    named pipes without pywin32, so it gets a loopback socket instead and the
    chosen port is written to `<path>.port` — the endpoint file is still the one
    thing both sides agree on.
    """
    if _is_posix():
        try:
            os.unlink(path)
        except OSError:
            pass
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".port", "w", encoding=_ENC) as fh:
            fh.write(str(srv.getsockname()[1]))
        # a placeholder so "does the endpoint exist" is one check on both platforms
        with open(path, "w", encoding=_ENC) as fh:
            fh.write("loopback\n")
    srv.listen(8)
    return srv


def _connect(path: str, timeout: float = 5.0):
    if _is_posix():
        sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sk.settimeout(timeout)
        sk.connect(path)
        return sk
    with open(path + ".port", encoding=_ENC) as fh:
        port = int(fh.read().strip())
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.settimeout(timeout)
    sk.connect(("127.0.0.1", port))
    return sk


def endpoint_alive(path: str) -> bool:
    """Is a daemon actually listening? A stale socket file is not enough."""
    try:
        sk = _connect(path, timeout=1.0)
    except Exception:
        return False
    sk.close()
    return True


class _Lines:
    """Newline-delimited JSON over a stream socket."""

    def __init__(self, sock):
        self.sock = sock
        self._buf = b""

    def send(self, obj) -> None:
        self.sock.sendall((json.dumps(obj) + "\n").encode(_ENC))

    #: sentinel distinguishing "nothing arrived in time" from "peer went away"
    TIMEOUT = object()

    def recv(self, timeout=None):
        """Next message, `TIMEOUT` if none arrived in time, or None on close."""
        self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return self.TIMEOUT
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        if not line.strip():
            return {}
        return json.loads(line.decode(_ENC))


# ────────────────────────────────────────────────────────────── the server ────

class Daemon:
    """Owns the device; serves attached clients."""

    def __init__(self, qc, socket_path: str, mode: str = "auto"):
        self.qc = qc
        self.path = socket_path
        self.mode = mode
        self._srv = None
        self._clients = {}            # id -> (_Lines, list queue, threading.Lock)
        self._next_client = 0
        # Two locks on purpose: a slow HID write must not stop the pump from
        # draining device->host reports, or the underlying buffer backs up and
        # multi-chunk replies are lost.
        self._lock = threading.Lock()       # the client registry
        self._write_lock = threading.Lock() # one writer at a time on the device
        self._stop = threading.Event()
        self._reports = 0
        self._started = time.time()

    # -- broadcast ---------------------------------------------------------

    def _pump(self) -> None:
        """One reader on the real device; every report goes to every client.

        This mirrors what the interposer already does on the FIFO: the device
        answers whoever asked by echoing their request_id, so fan-out plus
        per-client filtering is both correct and much simpler than routing.
        """
        while not self._stop.is_set():
            try:
                reports = self.qc.io.read_reports(timeout=0.05)
            except Exception:
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                continue
            if not reports:
                continue
            self._reports += len(reports)
            with self._lock:
                targets = list(self._clients.values())
            for _, queue, qlock in targets:
                with qlock:
                    queue.extend(reports)
                    if len(queue) > QUEUE_CAP:
                        del queue[: len(queue) - QUEUE_CAP]

    # -- one client --------------------------------------------------------

    def _serve_client(self, sock) -> None:
        link = _Lines(sock)
        with self._lock:
            cid = self._next_client
            self._next_client += 1
            queue: list = []
            self._clients[cid] = (link, queue, threading.Lock())
        try:
            while not self._stop.is_set():
                msg = link.recv(timeout=0.5)
                if msg is None:
                    break                       # peer closed
                if msg is _Lines.TIMEOUT:
                    if self._drained(sock):
                        break
                    continue
                self._handle(link, cid, queue, msg)
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                self._clients.pop(cid, None)
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _drained(sock) -> bool:
        """A recv timeout is normal; a closed peer is not. Tell them apart."""
        try:
            sock.settimeout(0)
            peek = sock.recv(1, socket.MSG_PEEK)
            return peek == b""
        except (BlockingIOError, socket.timeout):
            return False
        except OSError:
            return True

    def _handle(self, link, cid, queue, msg) -> None:
        op = msg.get("op")
        rid = msg.get("id")
        try:
            if op == "hello":
                link.send({
                    "id": rid, "ok": True,
                    "client": cid,
                    "req_base": REQ_STRIDE * cid,
                    "firmware": self.qc.firmware,
                    "device_type": self.qc.device_type,
                    "mode": self.mode,
                })
            elif op == "write":
                data = bytes.fromhex(msg["data"])
                # one writer at a time: the device is a single endpoint and the
                # server may be answering several clients at once
                with self._write_lock:
                    self.qc.io.set_report(msg.get("report_id", 0), data,
                                          include_id=msg.get("include_id", False))
                link.send({"id": rid, "ok": True})
            elif op == "read":
                with self._clients[cid][2]:
                    batch, queue[:] = list(queue), []
                link.send({"id": rid, "ok": True,
                           "reports": [[r[0], bytes(r[1]).hex()] for r in batch]})
            elif op == "status":
                link.send({"id": rid, "ok": True, **self.status()})
            elif op == "bye":
                link.send({"id": rid, "ok": True})
                raise OSError("client said goodbye")
            else:
                link.send({"id": rid, "ok": False, "error": f"unknown op {op!r}"})
        except OSError:
            raise
        except Exception as exc:                       # never kill the daemon
            try:
                link.send({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            except OSError:
                raise

    def status(self) -> dict:
        return {
            "clients": len(self._clients),
            "reports": self._reports,
            "uptime": round(time.time() - self._started, 1),
            "mode": self.mode,
            "firmware": self.qc.firmware,
            "socket": self.path,
            "pid": os.getpid(),
        }

    # -- lifecycle ---------------------------------------------------------

    def serve_forever(self) -> None:
        self._srv = _listener(self.path)
        pump = threading.Thread(target=self._pump, daemon=True)
        pump.start()
        print(f"qc-mcp daemon listening on {self.path} (mode={self.mode}, "
              f"firmware={self.qc.firmware})", flush=True)
        self._srv.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    sock, _ = self._srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    # close() shuts the listener down under us; that is the
                    # normal way this loop ends, not a failure
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(target=self._serve_client, args=(sock,), daemon=True).start()
        finally:
            self.close()

    def close(self) -> None:
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except OSError:
            pass
        for path in (self.path, self.path + ".port"):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            self.qc.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────── the client side ────

class SocketTransport:
    """A `QuadCortex` transport that rides the daemon's session.

    Same three methods the FIFO bridge and the raw HID backend expose, so
    everything above it is unaware there is a socket in the way.
    """

    BENIGN_WRITE_CODES = frozenset({0})

    def __init__(self, socket_path: str):
        self.path = socket_path
        self.link = None
        self.client_id = None
        self.req_base = 0
        self.firmware = None
        self._id = 0
        self._lock = threading.Lock()

    def _rpc(self, obj, timeout=10.0):
        with self._lock:
            self._id += 1
            obj["id"] = self._id
            self.link.send(obj)
            while True:
                reply = self.link.recv(timeout=timeout)
                if reply is _Lines.TIMEOUT:
                    raise BridgeError(
                        f"the qc-mcp daemon did not answer {obj['op']!r} within "
                        f"{timeout:g}s (it is running but busy)")
                if reply is None:
                    raise BridgeError("the qc-mcp daemon closed the connection")
                if reply.get("id") == obj["id"]:
                    if not reply.get("ok"):
                        raise BridgeError(reply.get("error", "daemon refused the request"))
                    return reply

    def open(self):
        # Idempotent: QuadCortex.open() calls io.open() too, and a second
        # connect would leave the first socket registered as a client whose
        # queue nobody ever drains.
        if self.link is not None:
            return self
        try:
            sock = _connect(self.path)
        except Exception as exc:
            raise BridgeError(
                f"no qc-mcp daemon at {self.path}; start it with "
                f"`qc-mcp --daemon --socket {self.path}`") from exc
        sock.settimeout(None)
        self.link = _Lines(sock)
        hello = self._rpc({"op": "hello"})
        self.client_id = hello.get("client")
        self.req_base = hello.get("req_base", 0)
        self.firmware = hello.get("firmware")
        return self

    def set_report(self, report_id, data, include_id=False):
        self._rpc({"op": "write", "report_id": report_id,
                   "data": bytes(data).hex(), "include_id": bool(include_id)})

    def read_reports(self, timeout=0.1):
        reply = self._rpc({"op": "read"}, timeout=max(1.0, timeout + 1.0))
        out = [(int(rid), bytes.fromhex(hexdata)) for rid, hexdata in reply["reports"]]
        if not out and timeout:
            # nothing buffered: wait a beat rather than spinning the socket
            time.sleep(min(timeout, 0.05))
        return out

    def status(self) -> dict:
        return self._rpc({"op": "status"})

    def close(self):
        try:
            if self.link:
                self._rpc({"op": "bye"}, timeout=1.0)
        except Exception:
            pass
        try:
            if self.link:
                self.link.sock.close()
        except OSError:
            pass
        self.link = None


def attach(socket_path: str):
    """A `QuadCortex` that shares the daemon's session. Raises if none is running."""
    from .transport import QuadCortex
    io = SocketTransport(socket_path).open()
    qc = QuadCortex(io=io)
    # disjoint request_id ranges, so two attached clients cannot mistake each
    # other's replies for their own
    qc._req_id += io.req_base
    return qc.open(handshake=False)


def serve(socket_path: str, mode: str = "auto") -> int:
    """Open the device the way `mode` asks, then serve clients until killed."""
    from .transport import QuadCortex
    from .backend import bridge_supported

    from .server import _bridge_running, _cortex_running

    bridge = share = False
    if mode in ("auto", "bridge"):
        # _bridge_running(), not just "do the FIFOs exist" — they are plain
        # filesystem objects that outlive the app, so existence alone lies.
        if bridge_supported() and _bridge_running():
            bridge = sys.platform != "win32"
            share = sys.platform == "win32"   # a second, non-exclusive handle
        elif mode == "bridge":
            raise BridgeError(
                "bridge mode needs Cortex Control running: the instrumented "
                "build on macOS (interceptor/run-bridge.sh), the stock app on "
                "Windows")

    if not bridge and not share and _cortex_running() and sys.platform != "win32":
        raise BridgeError(
            "Cortex Control is holding the device, so it cannot be seized. Quit "
            "it, or launch the instrumented build and start the daemon in bridge "
            "mode.")

    qc = QuadCortex(bridge=bridge, share=share).open(handshake=True)
    daemon = Daemon(qc, socket_path, mode="bridge" if bridge else "shared" if share else "direct")

    import signal

    def _bye(*_):
        daemon.close()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):
            pass

    daemon.serve_forever()
    return 0
