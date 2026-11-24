"""Cortex Cloud HTTPS client, independent of the Quad Cortex USB session.

Routes are observed from the official web client; see docs/CORTEX-CLOUD.md.
Renewable sessions can be remembered using OS-vault protected storage. Passwords
are never saved. Legacy session_file loading remains available explicitly.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import http.cookiejar
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from .cloud_session import SecureSessionStore, SessionError

_DEFAULT_STORE = object()

API_BASE = "https://cloud-api.neuraldsp.com"
try:
    USER_AGENT = "qc-mcp/" + version("qc-mcp")
except PackageNotFoundError:
    USER_AGENT = "qc-mcp"
_SECRET_KEYS = {"password", "token", "access_token", "refresh_token", "id_token",
                "authorization", "cookie", "set-cookie"}


class CloudError(RuntimeError):
    """An actionable cloud failure that never includes credentials or raw bodies."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def redact(value):
    """Remove credential fields recursively before returning service data."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()
                if str(k).lower().replace("-", "_") not in
                {s.replace("-", "_") for s in _SECRET_KEYS}}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _segment(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise CloudError("A nonempty product ID or username is required")
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]+", value) or value in {".", ".."}:
        raise CloudError("Invalid product ID or username")
    return urllib.parse.quote(value, safe="")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward account authorization to another host (or insecure URL).
        raise CloudError("Cortex Cloud unexpectedly redirected the API request")


class CloudClient:
    def __init__(self, *, opener=None, session_file=None, access_token=None, timeout=20,
                 session_store=_DEFAULT_STORE, use_environment=True):
        self._cookies = http.cookiejar.CookieJar()
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies), _NoRedirect())
        self._use_environment = use_environment
        self._token = access_token or (os.environ.get("QC_CLOUD_ACCESS_TOKEN", "") if use_environment else "")
        session_file = session_file or (os.environ.get("QC_CLOUD_SESSION_FILE") if use_environment else None)
        self._session_file = Path(session_file) if session_file else None
        # Injected HTTP transports default to memory-only to keep offline clients
        # and tests from ever touching a user's native credential store.
        self._store = (SecureSessionStore() if opener is None and not self._session_file else None) if session_store is _DEFAULT_STORE else session_store
        self._managed = self._store is not None and not self._token
        self._session_depth = 0
        self._email = ""
        self._persist = False
        self._timeout = timeout
        self._lock = threading.RLock()
        if self._session_file:
            try:
                st = self._session_file.lstat()
            except FileNotFoundError:
                return
            except OSError:
                raise CloudError("Could not inspect the cloud session file") from None
            import stat
            if not stat.S_ISREG(st.st_mode) or (os.name != "nt" and st.st_mode & 0o077):
                raise CloudError("Cloud session file must be a regular private file (0600)")
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                raise CloudError("Cloud session file must belong to the current user")
            try:
                data = json.loads(self._session_file.read_text())
                if not isinstance(data, dict) or not isinstance(data.get("access_token", ""), str):
                    raise ValueError("invalid session")
                self._token = self._token or data.get("access_token", "")
                self._email = data.get("email", "")
                for cookie in data.get("cookies", []):
                    self._cookies.set_cookie(http.cookiejar.Cookie(**cookie))
                self._persist = True
            except (ValueError, TypeError, OSError):
                raise CloudError("Could not read the cloud session file") from None

    def _session_data(self):
        fields = ("version", "name", "value", "port", "port_specified", "domain",
                  "domain_specified", "domain_initial_dot", "path", "path_specified",
                  "secure", "expires", "discard", "comment", "comment_url", "rfc2109")
        cookies = [{**{f: getattr(c, f) for f in fields}, "rest": c._rest}
                   for c in self._cookies]
        return {"access_token": self._token, "cookies": cookies, "email": self._email}

    def _restore(self):
        data = self._store.load()
        self._token = ""
        self._email = ""
        self._cookies.clear()
        self._persist = data is not None
        if data is None:
            return
        try:
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                raise ValueError
            self._token = data["access_token"]
            self._email = data.get("email", "")
            if not isinstance(self._email, str):
                raise ValueError
            for cookie in data.get("cookies", []):
                self._cookies.set_cookie(http.cookiejar.Cookie(**cookie))
        except (ValueError, TypeError):
            self._token = ""
            self._email = ""
            self._cookies.clear()
            raise CloudError("The saved Cortex Cloud session is invalid; sign in again") from None

    @contextmanager
    def _session(self, reload=True):
        with self._lock:
            if self._session_depth or not self._managed:
                yield
                return
            try:
                with self._store.locked():
                    self._session_depth += 1
                    try:
                        if reload:
                            self._restore()
                        yield
                    finally:
                        self._session_depth -= 1
            except SessionError as exc:
                raise CloudError(str(exc)) from None

    def _save(self):
        try:
            if self._managed and self._persist:
                self._store.save(self._session_data())
            else:
                self._save_session()
        except SessionError as exc:
            raise CloudError(str(exc)) from None
        except OSError:
            raise CloudError("Could not save the private cloud session file") from None

    def _save_session(self):
        if not self._session_file or not self._persist:
            return
        fields = ("version", "name", "value", "port", "port_specified", "domain",
                  "domain_specified", "domain_initial_dot", "path", "path_specified",
                  "secure", "expires", "discard", "comment", "comment_url", "rfc2109")
        cookies = [{**{f: getattr(c, f) for f in fields}, "rest": c._rest}
                   for c in self._cookies]
        # Exclusive temporary creation plus atomic rename never exposes partial tokens.
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix=".qc-cloud-", dir=self._session_file.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"access_token": self._token, "cookies": cookies}, stream)
            os.replace(tmp, self._session_file)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _clear_session_file(self):
        try:
            self._session_file.unlink(missing_ok=True)
        except OSError:
            raise CloudError("Could not remove the private cloud session file") from None

    def status(self):
        with self._session():
            return {"authenticated": bool(self._token), "session_persisted": self._persist,
                    "email": self._email,
                    "storage": self._store.storage_name if self._managed else
                               "legacy-file" if self._session_file and self._persist else "memory"}

    def _expired(self):
        self._token = ""
        self._email = ""
        self._cookies.clear()
        if self._managed:
            self._store.clear()
        elif self._session_file and self._persist:
            self._clear_session_file()
        self._persist = False

    def _request(self, method, path, *, params=None, data=None, auth=True, retry=True):
        with self._session():
            if auth and not self._token:
                raise CloudError("Log in to Cortex Cloud first (cloud_login)")
            if not path.startswith("/") or path.startswith("//"):
                raise CloudError("Invalid Cortex Cloud API path")
            url = API_BASE + path
            if params:
                encoded = {k: str(v).lower() if isinstance(v, bool) else
                           ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v
                           for k, v in params.items() if v is not None}
                url += "?" + urllib.parse.urlencode(encoded)
            headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "User-Agent": USER_AGENT,
                       "X-Cloud-Api-Client-Type": "web-cloud",
                       "X-Cloud-Api-Client-Version": "1.0.0"}
            if auth:
                headers["Authorization"] = "Bearer " + self._token
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                send = getattr(self._opener, "open", self._opener)
                with send(req, timeout=self._timeout) as response:
                    payload = response.read()
                result = json.loads(payload) if payload else {}
            except urllib.error.HTTPError as exc:
                # Error bodies may contain private account diagnostics. We do
                # not consume them, and must release their connection before
                # making a refresh request.
                exc.close()
                if exc.code == 401 and auth and retry:
                    try:
                        refreshed = self._request("POST", "/api/v1/user/refresh_token", auth=False)
                    except CloudError as exc:
                        # An offline service does not revoke a renewable session.
                        # Preserve it so another process can retry after recovery.
                        if exc.status_code not in {400, 401, 403}:
                            raise
                        self._expired()
                        raise CloudError("Cloud session expired; sign in with Patchbay or run qc-mcp cloud login", status_code=401) from None
                    if (not isinstance(refreshed, dict)
                            or not isinstance(refreshed.get("access_token"), str)
                            or not refreshed["access_token"]):
                        self._expired()
                        raise CloudError("Cloud session expired; sign in with Patchbay or run qc-mcp cloud login") from None
                    self._token = refreshed["access_token"]
                    self._save()
                    return self._request(method, path, params=params, data=data, retry=False)
                if exc.code == 401:
                    if auth:
                        self._expired()
                    raise CloudError("Cortex Cloud authentication failed; sign in with Patchbay or run qc-mcp cloud login", status_code=401) from None
                if exc.code == 403:
                    raise CloudError("Cortex Cloud denied access to this item", status_code=403) from None
                if exc.code == 404:
                    raise CloudError("Cortex Cloud item was not found", status_code=404) from None
                if exc.code == 429:
                    raise CloudError("Cortex Cloud rate limit reached; try again later", status_code=429) from None
                raise CloudError(f"Cortex Cloud request failed (HTTP {exc.code})", status_code=exc.code) from None
            except (urllib.error.URLError, OSError, TimeoutError):
                raise CloudError("Could not reach Cortex Cloud; check your network and retry") from None
            except (ValueError, UnicodeError):
                raise CloudError("Cortex Cloud returned an invalid JSON response") from None
            return result

    def login(self, email="", password="", persist=False):
        email = email or (os.environ.get("QC_CLOUD_EMAIL", "") if self._use_environment else "")
        password = password or (os.environ.get("QC_CLOUD_PASSWORD", "") if self._use_environment else "")
        if not email or not password:
            raise CloudError("Sign in using Patchbay or run qc-mcp cloud login; email and password are required")
        if not isinstance(persist, bool):
            raise CloudError("persist must be a boolean")
        if persist and not self._store and not self._session_file:
            raise CloudError("A secure session store is required for remembered sign-in")
        with self._lock:
            self._managed = bool(persist and self._store is not None)
            with self._session(reload=False):
                if self._managed:
                    self._store.prepare()
                self._token = ""
                self._cookies.clear()
                result = self._request("POST", "/api/v1/user/signin",
                                       data={"username": email, "password": password}, auth=False)
                if not isinstance(result, dict) or not isinstance(result.get("access_token"), str) or not result["access_token"]:
                    raise CloudError("Cortex Cloud did not return a valid session")
                self._token = result["access_token"]
                self._email = email
                self._persist = persist
                if not persist and self._session_file:
                    self._clear_session_file()
                self._save()
                return self.status()

    def logout(self):
        # A damaged/expired session must still be removable locally, including
        # while the network is down. Do not let restore failure skip cleanup.
        with self._session(reload=False):
            warning = None
            try:
                if self._managed:
                    self._restore()
                if self._token:
                    self._request("POST", "/api/v1/user/signout", data={}, retry=False)
            except (CloudError, SessionError):
                warning = "Signed out locally; Cortex Cloud could not confirm remote sign-out."
            finally:
                self._token = ""
                self._email = ""
                self._cookies.clear()
                self._persist = False
                if self._managed:
                    self._store.clear()
                if self._session_file:
                    self._clear_session_file()
            result = self.status()
            if warning:
                result["warning"] = warning
            return result

    def search(self, category="presets", query="", page=0, size=20,
               sort="relevance", sort_dir="descending", username="", scope="", **filters):
        kinds = {"presets": "preset", "captures": "neuralCapture", "users": "user"}
        if category not in kinds:
            raise CloudError("category must be presets, captures, or users")
        if isinstance(page, bool) or not isinstance(page, int) or page < 0:
            raise CloudError("page must be a nonnegative integer")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 100:
            raise CloudError("size must be between 1 and 100")
        if sort not in {"relevance", "name", "recent", "popular", "author", "device", "gain", "instrument"}:
            raise CloudError("Unsupported Cortex Cloud sort")
        if sort_dir not in {"ascending", "descending"}:
            raise CloudError("sort_dir must be ascending or descending")
        scopes = {"owned": "ownedByMe", "shared": "sharedWithMe", "downloaded": "inDownloadedHistory"}
        if scope and scope not in scopes:
            raise CloudError("scope must be owned, shared, or downloaded")
        allowed = {"captureVersion", "deviceType", "instrumentType", "createdAfter",
                   "minGain", "maxGain", "featured"}
        if set(filters) - allowed:
            raise CloudError("Unsupported Cortex Cloud search filter")
        if username and (scope or category == "users"):
            raise CloudError("username filtering is only available for public product search")
        params = dict(query=query, page=page, size=size, sort=sort, sortDir=sort_dir, **filters)
        if username:
            _segment(username)
            params["ownedBy"] = username
        if scope:
            params[scopes[scope]] = True
        return redact(self._request("GET", "/search/v1/for/" + kinds[category], params=params))

    def profile(self, username=""):
        path = "/api/v1/users/" + _segment(username) if username else "/api/v1/user/profile"
        return redact(self._request("GET", path))

    def product(self, product_id):
        return redact(self._request("GET", "/api/v1/products/" + _segment(product_id)))

    def queue_download(self, product_id):
        return redact(self._request("POST", "/api/v1/user/downloaded_products/" + _segment(product_id)))

    def follow(self, username, enabled=True):
        if not isinstance(enabled, bool):
            raise CloudError("enabled must be a boolean")
        return redact(self._request("PUT" if enabled else "DELETE",
                                    "/api/v1/user/followings/" + _segment(username)))

    def _owned_capture(self, product_id):
        product = self.product(product_id)
        if not isinstance(product, dict) or product.get("type") != "neural_capture":
            raise CloudError("This item is not a Neural Capture")
        profile = self.profile()
        if not isinstance(profile, dict):
            raise CloudError("Cortex Cloud returned an invalid user profile")
        author_id, user_id = product.get("authorId"), profile.get("userId")
        author_name, username = product.get("authorUsername"), profile.get("username")
        owned = author_id == user_id if author_id and user_id else bool(author_name and author_name == username)
        if not owned:
            raise CloudError("Only your own authored captures can be managed")
        return product

    def update_capture(self, product_id, *, name=None, description=None, tags=None,
                       instrument_type=None, device_type=None):
        if all(v is None for v in (name, description, tags, instrument_type, device_type)):
            raise CloudError("Provide at least one capture field to update")
        if name is not None and (not isinstance(name, str) or not 4 <= len(name.strip()) <= 32):
            raise CloudError("Capture name must contain 4 to 32 characters")
        if description is not None and (not isinstance(description, str) or len(description) > 1000):
            raise CloudError("Capture description must not exceed 1000 characters")
        if tags is not None and (not isinstance(tags, list) or len(tags) > 8 or
                                 any(not isinstance(t, str) or not 1 <= len(t) <= 30 for t in tags) or
                                 len(set(tags)) != len(tags)):
            raise CloudError("Provide up to 8 distinct tags of 1 to 30 characters")
        if instrument_type is not None and instrument_type not in {"instrument_not_set", "other", "guitar", "bass", "synth", "vocal"}:
            raise CloudError("Unsupported instrument type")
        if device_type is not None and device_type not in {"amp_head", "amp_and_cab", "amp_combo", "cab", "pedal", "fuzz", "compressor", "default", "overdrive"}:
            raise CloudError("Unsupported capture device type")
        with self._session():
            current = self._owned_capture(product_id)
            data = {k: copy.deepcopy(current[k]) for k in ("name", "description", "tags", "metadata") if k in current}
            for key, value in (("name", name.strip() if name is not None else None), ("description", description), ("tags", tags)):
                if value is not None:
                    data[key] = value
            for key, value in (("instrumentType", instrument_type), ("deviceType", device_type)):
                if value is not None:
                    if not isinstance(data.get("metadata"), dict):
                        data["metadata"] = {}
                    data.setdefault("metadata", {})[key] = value
            return redact(self._request("PUT", "/api/v1/user/products/" + _segment(product_id) + "/metadata", data=data))

    def publish_capture(self, product_id, published=True):
        if not isinstance(published, bool):
            raise CloudError("published must be a boolean")
        with self._session():
            self._owned_capture(product_id)
            return redact(self._request("POST" if published else "DELETE",
                                        "/api/v1/user/products/" + _segment(product_id) + "/publish"))

    def delete_capture(self, product_id):
        with self._session():
            self._owned_capture(product_id)
            return redact(self._request("DELETE", "/api/v1/user/products/" + _segment(product_id)))
