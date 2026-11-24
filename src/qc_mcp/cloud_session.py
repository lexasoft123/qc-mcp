"""OS-vault protected Cortex Cloud sessions, shared by CLI, MCP and Patchbay.

Only a short encryption key is kept in the native credential store. The renewable
session is encrypted on disk, avoiding Windows credential blob size limits.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

SERVICE = "qc-mcp.cortex-cloud"
KEY_ACCOUNT = "session-encryption-key"


class SessionError(RuntimeError):
    """A credential-storage failure safe to display without secrets."""


def default_session_path():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "qc-mcp" / "cloud-session.enc"


def _native_backend():
    # Do not use keyring's discovery: user-installed plaintext/fail backends must
    # never silently receive authentication material.
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        else:
            raise SessionError(
                "Remembering Cortex Cloud sign-in requires macOS Keychain or Windows Credential Manager. "
                "For a process-only MCP login, set QC_CLOUD_EMAIL and QC_CLOUD_PASSWORD in the server "
                "environment and call cloud_login(persist=False), or set QC_CLOUD_ACCESS_TOKEN."
            )
        if backend.priority <= 0:
            raise RuntimeError
        return backend
    except SessionError:
        raise
    except Exception:
        raise SessionError("The OS credential store is unavailable; unlock it and try again, or sign in without remembering") from None


class SecureSessionStore:
    def __init__(self, path=None, *, keyring_backend=None, lock_timeout=30):
        self.path = Path(path) if path is not None else default_session_path()
        self._backend = keyring_backend
        self._lock_timeout = lock_timeout

    @property
    def storage_name(self):
        return "keychain" if sys.platform == "darwin" else "windows-credential-manager" if sys.platform == "win32" else "os-credential-store"

    def _vault(self):
        if self._backend is None:
            self._backend = _native_backend()
        return self._backend

    def _cipher(self, create=False):
        try:
            from cryptography.fernet import Fernet
            vault = self._vault()
            key = vault.get_password(SERVICE, KEY_ACCOUNT)
            if key is None and create:
                key = Fernet.generate_key().decode("ascii")
                vault.set_password(SERVICE, KEY_ACCOUNT, key)
            if key is None:
                raise SessionError("The saved Cortex Cloud session key is missing; sign in again")
            return Fernet(key.encode("ascii"))
        except SessionError:
            raise
        except Exception:
            raise SessionError("Could not access the OS credential store; unlock it and try again, or sign in without remembering") from None

    def prepare(self):
        """Validate secure storage before submitting a persistent login."""
        self._cipher(create=True)

    def _private_file(self, path):
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or (os.name != "nt" and st.st_mode & 0o077):
            raise SessionError("The saved Cortex Cloud session must be a private regular file")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise SessionError("The saved Cortex Cloud session belongs to a different user")

    @contextmanager
    def locked(self):
        """Serialize read/refresh/write and logout across independent processes."""
        handle = None
        acquired = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_path = self.path.with_name(self.path.name + ".lock")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(lock_path, flags, 0o600)
            handle = os.fdopen(fd, "r+b")
            self._private_file(lock_path)
            if os.name == "nt":
                # msvcrt locks an existing byte; leave the inode in place forever.
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                import msvcrt
                def acquire():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                def release():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                def acquire():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                def release():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    acquire()
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise SessionError("Cortex Cloud sign-in is busy in another process; try again shortly") from None
                    time.sleep(0.05)
            yield
        except SessionError:
            raise
        except OSError:
            raise SessionError("Could not access the private Cortex Cloud session directory") from None
        finally:
            if handle is not None:
                if acquired:
                    release()
                handle.close()

    def load(self):
        try:
            self._private_file(self.path)
        except FileNotFoundError:
            return None
        except OSError:
            raise SessionError("Could not read the saved Cortex Cloud session") from None
        try:
            raw = self._cipher().decrypt(self.path.read_bytes())
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                raise ValueError
            return data
        except SessionError:
            raise
        except Exception:
            raise SessionError("The saved Cortex Cloud session is damaged or cannot be decrypted; sign in again") from None

    def save(self, data):
        temporary = None
        try:
            ciphertext = self._cipher(create=True).encrypt(json.dumps(data).encode("utf8"))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".cloud-session-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except SessionError:
            raise
        except Exception:
            raise SessionError("Could not securely save the Cortex Cloud session") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def clear(self):
        # Remove the ciphertext first: readers observing logout cannot revive an
        # in-memory refresh token. Keep lock inode stable for other processes.
        try:
            try:
                self.path.unlink()
            except FileNotFoundError:
                return
            vault = self._vault()
            if vault.get_password(SERVICE, KEY_ACCOUNT) is not None:
                vault.delete_password(SERVICE, KEY_ACCOUNT)
        except SessionError:
            raise
        except Exception:
            raise SessionError("Could not remove the saved Cortex Cloud sign-in from the OS credential store") from None
