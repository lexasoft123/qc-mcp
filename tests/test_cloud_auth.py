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


def test_verified_status_preserves_session_when_cloud_is_unavailable():
    class OfflineClient:
        def status(self, *, verify=False):
            assert verify
            return {"authenticated": True, "session_persisted": True, "error": "Cloud unavailable"}

    code, output, _ = invoke(["status", "--json", "--verify"], factory=OfflineClient)
    assert code == 1 and json.loads(output) == {
        "authenticated": True, "session_persisted": True, "error": "Cloud unavailable"}


def test_product_commands_identify_when_sign_in_is_required():
    for status_code in (401, 403, 500, None):
        class FailingClient:
            def search(self, *args, **kwargs):
                raise CloudError("Cloud request failed", status_code=status_code)

            product = search

        for args in (["search", "--category=captures", "--query=clean"],
                     ["preset-details", "--product-id=preset-id"]):
            code, output, _ = invoke([*args, "--json"], factory=FailingClient)
            assert code == 1
            result = json.loads(output)
            assert result["error"] == "Cloud request failed"
            assert result.get("signInRequired", False) == (status_code == 401)


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


def test_capture_search_returns_requested_page_and_display_metadata():
    class SearchClient:
        def search(self, category, query, page, size):
            assert (category, query, page, size) == ("captures", "-AC30", 1, 10)
            return {"numberOfPages": 3, "data": [{
                "productId": "capture-id", "name": "AC30", "authorUsername": "author",
                "description": "Recorded with an e609", "tags": ["clean"],
                "downloads": 0, "metadata": {"version": 2, "deviceType": "amp_combo", "gainType": 4},
                "unlistedField": "not for the UI",
            }, {"productId": "incomplete"}]}

    code, output, errors = invoke(["search", "--json", "--category=captures", "--query=-AC30", "--page=1"],
                                  factory=SearchClient)
    assert code == 0 and not errors
    assert json.loads(output) == {"hasNext": True, "products": [{
        "productId": "capture-id", "name": "AC30", "authorUsername": "author",
        "description": "Recorded with an e609", "tags": ["clean"], "downloads": 0,
        "version": "2", "deviceType": "amp_combo", "gainType": 4,
    }]}


def test_invalid_capture_query_does_not_call_the_cloud_client():
    def unexpected():
        raise AssertionError("must not construct client for an invalid search")
    for args in (["--query= "], ["--query=clean", "--page=-1"]):
        code, output, _ = invoke(["search", "--json", "--category=captures", *args], factory=unexpected)
        assert code == 1 and json.loads(output)["error"]


def test_preset_search_and_details_show_compatibility_blocks_and_named_scenes():
    class PresetClient:
        def search(self, category, query, page, size):
            assert (category, query, page, size) == ("presets", "AC30", 0, 10)
            return {"numberOfPages": 1, "data": [{"productId": "preset-id", "name": "AC30",
                                                  "creatorType": "quad", "creatorVersion": "3.1.0"}]}

        def product(self, product_id):
            assert product_id == "preset-id"
            return {"type": "preset", "compatibility": ["quad", "native"],
                    "details": {"blocks": [{"name": "UK C30 TopBoost"}],
                                "scenes": [{"name": "Clean"}, {"label": "B"}]}}

    code, output, _ = invoke(["search", "--json", "--category=presets", "--query=AC30"],
                             factory=PresetClient)
    assert code == 0
    assert json.loads(output)["products"][0]["creatorVersion"] == "3.1.0"
    code, output, _ = invoke(["preset-details", "--json", "--product-id=preset-id"],
                             factory=PresetClient)
    assert code == 0 and json.loads(output) == {
        "compatibility": ["quad", "native"], "blocks": ["UK C30 TopBoost"], "scenes": ["Clean"],
    }


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
