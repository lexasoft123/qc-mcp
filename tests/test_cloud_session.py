"""Secure cloud sessions across independent Python clients; no Patchbay/account.

The vault double stores only an encryption key in a temporary directory. Tests
never access a real OS keychain, real credentials, network, or USB device.
"""
import http.cookiejar
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qc_mcp.cloud import CloudClient, CloudError
from qc_mcp.cloud_session import SecureSessionStore, SessionError


class FileKeyring:
    """Cross-process vault double. Its file contains only the generated key."""
    def __init__(self, path):
        self.path = Path(path)

    def get_password(self, service, username):
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text()).get(service + ":" + username)

    def set_password(self, service, username, password):
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data[service + ":" + username] = password
        self.path.write_text(json.dumps(data))

    def delete_password(self, service, username):
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data.pop(service + ":" + username, None)
        self.path.write_text(json.dumps(data))


class UnavailableKeyring:
    def get_password(self, *_):
        raise RuntimeError("private native diagnostic")

    set_password = get_password
    delete_password = get_password


def store_at(directory, **kwargs):
    directory = Path(directory)
    return SecureSessionStore(path=directory / "session.enc",
                              keyring_backend=FileKeyring(directory / "mock-vault.json"),
                              **kwargs)


def refresh_cookie(value):
    return http.cookiejar.Cookie(
        version=0, name="refresh_token", value=value, port=None,
        port_specified=False, domain="cloud-api.neuraldsp.com", domain_specified=False,
        domain_initial_dot=False, path="/", path_specified=True, secure=True,
        expires=None, discard=True, comment=None, comment_url=None, rest={"HttpOnly": None})


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class AccountHTTP:
    """A cookie-rotating account, with state shared between child processes.

    Native urllib's cookie processor normally updates the client's cookie jar.
    This fake opener applies the same side effect without making HTTP requests.
    The client's cross-process session lock serializes access to this state.
    """
    def __init__(self, directory):
        self.state_path = Path(directory) / "account.json"
        self.client = None
        self.requests = []

    def open(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"refreshes": 0}
        path = urlsplit(request.full_url).path
        headers = {k.lower(): v for k, v in request.header_items()}
        if path.endswith("/signin"):
            body = json.loads(request.data)
            if body["password"] == "wrong":
                raise HTTPError(request.full_url, 401, "private password diagnostic", {}, io.BytesIO())
            self.client._cookies.set_cookie(refresh_cookie("refresh-zero-secret"))
            payload = {"access_token": "expired-access-secret"}
        elif path.endswith("/refresh_token"):
            assert "authorization" not in headers
            values = {c.name: c.value for c in self.client._cookies}
            assert values.get("refresh_token") == "refresh-zero-secret", values
            state["refreshes"] += 1
            assert state["refreshes"] == 1, "a competing client reused a rotating refresh cookie"
            self.client._cookies.set_cookie(refresh_cookie("refresh-one-secret"))
            payload = {"access_token": "renewed-access-secret"}
        elif path.endswith("/signout"):
            state["signed_out"] = True
            payload = {}
        elif path.endswith("/profile"):
            if headers.get("authorization") != "Bearer renewed-access-secret":
                raise HTTPError(request.full_url, 401, "expired", {}, io.BytesIO())
            state["profiles"] = state.get("profiles", 0) + 1
            payload = {"username": "test-user", "userId": "test-id"}
        else:
            raise AssertionError("Unexpected HTTP route")
        self.state_path.write_text(json.dumps(state))
        return Response(payload)


class FailingRefreshHTTP(AccountHTTP):
    def __init__(self, directory, failure):
        super().__init__(directory)
        self.failure = failure

    def open(self, request, *, timeout):
        if urlsplit(request.full_url).path.endswith("/refresh_token"):
            self.requests.append(request)
            if isinstance(self.failure, int):
                raise HTTPError(request.full_url, self.failure, "private-service-diagnostic",
                                {}, io.BytesIO(b"private-response"))
            raise self.failure
        return super().open(request, timeout=timeout)


class OfflineHTTP:
    def open(self, request, *, timeout):
        raise URLError("private-network-diagnostic")


def client_at(directory, **kwargs):
    http = AccountHTTP(directory)
    client = CloudClient(opener=http, session_store=store_at(directory), **kwargs)
    http.client = client
    return client, http


def child_command(directory, operation):
    return [sys.executable, str(Path(__file__).resolve()), "--child", str(directory), operation]


def child_main(directory, operation):
    client, _ = client_at(directory)
    if operation == "profile":
        result = client.profile()
    elif operation == "logout":
        result = client.logout()
    elif operation == "hold-lock":
        with store_at(directory).locked():
            print("locked", flush=True)
            sys.stdin.readline()
        return
    else:
        raise AssertionError("Unknown child operation")
    assert "electron" not in sys.modules and "qc_mcp.transport" not in sys.modules
    print(json.dumps(result))


class SecureSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def login(self):
        client, http = client_at(self.directory)
        client.login("test@example.com", "password-never-saved", persist=True)
        return client, http

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true",
                         "native Credential Manager smoke test runs on Windows CI")
    def test_windows_native_credential_store_survives_process_restart(self):
        from unittest.mock import patch
        from uuid import uuid4

        service = "qc-mcp.cortex-cloud.ci." + uuid4().hex
        path = self.directory / "native-session.enc"
        store = SecureSessionStore(path)
        with patch("qc_mcp.cloud_session.SERVICE", service):
            try:
                with store.locked():
                    store.save({"access_token": "fixture-token", "cookies": []})
                result = subprocess.run(
                    [sys.executable, "-c", """
import sys
from qc_mcp import cloud_session
cloud_session.SERVICE = sys.argv[1]
store = cloud_session.SecureSessionStore(sys.argv[2])
with store.locked():
    assert store.load() == {"access_token": "fixture-token", "cookies": []}
""", service, str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                with store.locked():
                    store.clear()
            self.assertFalse(path.exists())
            self.assertIsNone(store._vault().get_password(service, "session-encryption-key"))

    def test_large_session_is_encrypted_with_only_a_small_vault_key(self):
        store = store_at(self.directory)
        session = {"access_token": "access-secret" * 1000,
                   "cookies": [{"value": "refresh-secret" * 1000}],
                   "email": "test@example.com"}
        with store.locked():
            store.prepare()
            store.save(session)
            self.assertEqual(store.load(), session)
        encrypted = (self.directory / "session.enc").read_bytes()
        self.assertNotIn(b"access-secret", encrypted)
        self.assertNotIn(b"refresh-secret", encrypted)
        self.assertNotIn(b"test@example.com", encrypted)
        vault = (self.directory / "mock-vault.json").read_text()
        self.assertLess(len(vault), 1000)
        self.assertNotIn("access-secret", vault)
        if os.name != "nt":
            self.assertEqual((self.directory / "session.enc").stat().st_mode & 0o077, 0)

    def test_unavailable_vault_never_creates_plaintext_fallback_or_sends_password(self):
        store = SecureSessionStore(path=self.directory / "session.enc",
                                  keyring_backend=UnavailableKeyring())
        http = AccountHTTP(self.directory)
        client = CloudClient(opener=http, session_store=store)
        http.client = client
        with self.assertRaises((CloudError, SessionError)) as caught:
            client.login("test@example.com", "private-password", persist=True)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(http.requests, [])
        self.assertFalse((self.directory / "session.enc").exists())

    def test_password_and_refresh_cookie_do_not_appear_in_saved_files_or_status(self):
        client, _ = self.login()
        for path in self.directory.iterdir():
            # The fake service records only nonsecret counters.
            data = path.read_bytes()
            self.assertNotIn(b"password-never-saved", data)
            self.assertNotIn(b"expired-access-secret", data)
            self.assertNotIn(b"refresh-zero-secret", data)
        status = client.status()
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["session_persisted"])
        self.assertNotIn("secret", repr(status))

    def test_existing_client_notices_login_and_logout_from_another_client(self):
        waiting, waiting_http = client_at(self.directory)
        self.assertFalse(waiting.status()["authenticated"])
        owner, _ = self.login()
        self.assertTrue(waiting.status()["authenticated"])
        owner.logout()
        self.assertFalse(waiting.status()["authenticated"])
        with self.assertRaises(CloudError):
            waiting.profile()
        self.assertFalse(waiting_http.requests)

    def test_new_python_process_restores_and_refreshes_without_patchbay(self):
        self.login()
        result = subprocess.run(child_command(self.directory, "profile"),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"username": "test-user", "userId": "test-id"})
        self.assertEqual(json.loads((self.directory / "account.json").read_text())["refreshes"], 1)

    def test_competing_python_clients_refresh_rotating_cookie_once(self):
        self.login()
        processes = [subprocess.Popen(child_command(self.directory, "profile"),
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            output, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, error)
            self.assertEqual(json.loads(output)["username"], "test-user")
        state = json.loads((self.directory / "account.json").read_text())
        self.assertEqual(state["refreshes"], 1)
        self.assertEqual(state["profiles"], 2)

    def test_transient_refresh_failure_preserves_shared_session_for_later_process(self):
        for failure in (500, URLError("private-network-diagnostic")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                signed_in, _ = client_at(directory)
                signed_in.login("test@example.com", "not-saved", persist=True)
                saved = (Path(directory) / "session.enc").read_bytes()
                http = FailingRefreshHTTP(directory, failure)
                client = CloudClient(opener=http, session_store=store_at(directory))
                http.client = client
                with self.assertRaises(CloudError) as caught:
                    client.profile()
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(client.status()["authenticated"])
                self.assertEqual((Path(directory) / "session.enc").read_bytes(), saved)
                # A new process can still renew using the original refresh cookie.
                result = subprocess.run(child_command(directory, "profile"),
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["username"], "test-user")

    def test_rejected_refresh_clears_saved_login_for_all_clients(self):
        for status_code in (400, 401, 403):
            with self.subTest(status_code=status_code), tempfile.TemporaryDirectory() as directory:
                existing, _ = client_at(directory)
                existing.login("test@example.com", "not-saved", persist=True)
                http = FailingRefreshHTTP(directory, status_code)
                client = CloudClient(opener=http, session_store=store_at(directory))
                http.client = client
                with self.assertRaises(CloudError):
                    client.profile()
                self.assertFalse(client.status()["authenticated"])
                self.assertFalse(existing.status()["authenticated"])
                self.assertFalse((Path(directory) / "session.enc").exists())

    def test_explicit_login_ignores_inherited_legacy_credentials_and_session_path(self):
        self.login()
        legacy = self.directory / "legacy-session.json"
        legacy.write_text("invalid legacy data that must never be loaded")
        inherited = {"QC_CLOUD_ACCESS_TOKEN": "inherited-private-token",
                     "QC_CLOUD_SESSION_FILE": str(legacy),
                     "QC_CLOUD_EMAIL": "inherited@example.com",
                     "QC_CLOUD_PASSWORD": "inherited-private-password"}
        with patch.dict(os.environ, inherited):
            client, http = client_at(self.directory, use_environment=False)
            status = client.status()
            self.assertTrue(status["authenticated"])
            self.assertEqual(status["email"], "test@example.com")
            self.assertNotIn("inherited", repr(status))
            with self.assertRaises(CloudError):
                client.login()
            self.assertFalse(http.requests)
        self.assertEqual(legacy.read_text(), "invalid legacy data that must never be loaded")

    def test_logout_in_separate_process_is_seen_by_running_client(self):
        client, http = self.login()
        result = subprocess.run(child_command(self.directory, "logout"),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["authenticated"])
        self.assertFalse(client.status()["authenticated"])
        self.assertFalse((self.directory / "session.enc").exists())
        previous = len(http.requests)
        with self.assertRaises(CloudError):
            client.profile()
        self.assertEqual(len(http.requests), previous)

    def test_ciphertext_corruption_fails_closed_without_http_or_secret_output(self):
        self.login()
        path = self.directory / "session.enc"
        path.write_bytes(b"private-corrupt-token")
        client, http = client_at(self.directory)
        with self.assertRaises((CloudError, SessionError)) as caught:
            client.profile()
        self.assertNotIn("private-corrupt-token", str(caught.exception))
        self.assertFalse(http.requests)

    def test_offline_or_corrupt_session_can_be_signed_out_locally(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as directory:
                observer, _ = client_at(directory)
                observer.login("test@example.com", "not-saved", persist=True)
                session_path = Path(directory) / "session.enc"
                if corrupt:
                    session_path.write_bytes(b"private-corrupt-session")
                client = CloudClient(opener=OfflineHTTP(), session_store=store_at(directory))
                result = client.logout()
                self.assertFalse(result["authenticated"])
                self.assertIn("locally", result["warning"])
                self.assertNotIn("private", repr(result))
                self.assertFalse(session_path.exists())
                self.assertFalse(observer.status()["authenticated"])

    def test_capture_delete_keeps_ownership_reads_and_write_under_one_lock(self):
        from unittest.mock import patch

        client, http = self.login()
        routes = []

        def respond(request, *, timeout):
            # A second client cannot switch accounts between these requests.
            with self.assertRaisesRegex(SessionError, "busy"):
                with store_at(self.directory, lock_timeout=0).locked():
                    self.fail("capture ownership check and write must share the session lock")
            route = urlsplit(request.full_url).path
            routes.append((request.method, route))
            if route == "/api/v1/products/capture":
                return Response({"type": "neural_capture", "authorId": "owner"})
            if route == "/api/v1/user/profile":
                return Response({"userId": "owner"})
            return Response({})

        with patch.object(http, "open", side_effect=respond), \
                patch.object(client._store, "locked", wraps=client._store.locked) as locked:
            client.delete_capture("capture")
            locked.assert_called_once_with()
        self.assertEqual(routes, [
            ("GET", "/api/v1/products/capture"),
            ("GET", "/api/v1/user/profile"),
            ("DELETE", "/api/v1/user/products/capture"),
        ])

    def test_wrong_password_is_not_saved_or_echoed(self):
        client, _ = client_at(self.directory)
        with self.assertRaises(CloudError) as caught:
            client.login("test@example.com", "wrong", persist=True)
        self.assertNotIn("private password", str(caught.exception))
        self.assertFalse((self.directory / "session.enc").exists())
        self.assertFalse(client.status()["authenticated"])

    def test_session_file_symlink_is_rejected_without_following_it(self):
        if os.name == "nt":
            self.skipTest("Windows symlink creation can require additional privileges")
        target = self.directory / "outside"
        target.write_text("must remain untouched")
        (self.directory / "session.enc").symlink_to(target)
        store = store_at(self.directory)
        with self.assertRaises(SessionError):
            with store.locked():
                store.load()
        self.assertEqual(target.read_text(), "must remain untouched")

    def test_other_process_session_lock_has_a_bounded_wait(self):
        process = subprocess.Popen(child_command(self.directory, "hold-lock"),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(SessionError):
                with store_at(self.directory, lock_timeout=0.1).locked():
                    self.fail("another process held the lock")
        finally:
            _, errors = process.communicate("release\n", timeout=10)
            self.assertEqual(process.returncode, 0, errors)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child_main(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
