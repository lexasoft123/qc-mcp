"""Private sign-in entry points work without Electron, a device, or an account."""
import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import cloud_auth
from qc_mcp.cloud import CloudError


class Client:
    def login(self, email, password, persist=False):
        assert (email, password, persist) == ("person@example.com", "private-password", True)
        return {"authenticated": True, "session_persisted": True, "email": email}

    def status(self):
        return {"authenticated": True, "session_persisted": True, "email": "person@example.com"}

    def logout(self):
        return {"authenticated": False, "session_persisted": False}


def invoke(args, stdin="", factory=Client):
    output, errors = io.StringIO(), io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin)), patch("sys.stdout", output), patch("sys.stderr", errors):
        code = cloud_auth.main(args, client_factory=factory)
    return code, output.getvalue(), errors.getvalue()


def test_json_login_uses_private_stdin_and_reports_only_account_status():
    code, output, errors = invoke(["login", "--json"], json.dumps({
        "email": " person@example.com ", "password": "private-password", "remember": True}))
    assert code == 0 and not errors
    assert json.loads(output)["email"] == "person@example.com"
    assert "private-password" not in output


def test_terminal_login_hides_password_and_never_accepts_a_password_argument():
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="person@example.com"), \
            patch("getpass.getpass", return_value="private-password") as prompt, patch("sys.stdout", io.StringIO()) as output:
        assert cloud_auth.main(["login"], client_factory=Client) == 0
        prompt.assert_called_once()
        assert "private-password" not in output.getvalue()
    assert invoke(["login"])[0] == 1  # cannot fall back to an echoing redirected prompt


def test_bad_private_input_is_rejected_without_login_or_reflecting_secrets():
    def unexpected():
        raise AssertionError("must not construct client for malformed input")
    for value in ("not-json private-password", "[]", json.dumps({"email": "person", "password": "private-password", "remember": False}),
                  json.dumps({"email": "person", "password": 42}), "x" * 65537):
        code, output, _ = invoke(["login", "--json"], value, unexpected)
        assert code == 1 and "error" in json.loads(output)
        assert "private-password" not in output


def test_status_and_logout_have_no_patchbay_dependency():
    code, output, _ = invoke(["status", "--json"])
    assert code == 0 and json.loads(output)["authenticated"]
    code, output, _ = invoke(["logout", "--json"])
    assert code == 0 and not json.loads(output)["authenticated"]


def test_credential_store_error_returns_actionable_json_without_traceback():
    def unavailable():
        raise CloudError("Unlock the operating system credential store and try again.")
    code, output, errors = invoke(["status", "--json"], factory=unavailable)
    assert code == 1 and "Unlock" in json.loads(output)["error"] and not errors
    assert "Traceback" not in output


def test_main_cli_routes_cloud_before_opening_device_or_mcp_stdio():
    from qc_mcp import server
    with patch.object(cloud_auth, "main", return_value=0) as helper, \
            patch.object(server.mcp, "run", side_effect=AssertionError("must not run MCP")):
        assert server.main(["cloud", "status", "--json"]) == 0
        helper.assert_called_once_with(["status", "--json"])


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
