"""Offline Cortex Cloud HTTP contract checks; no account, network or device."""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp.cloud import CloudClient, CloudError  # noqa: E402


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class HTTP:
    """Record real urllib Requests and serve explicit ordered fake replies."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        assert self.responses, f"unexpected request: {request.method} {request.full_url}"
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Response(reply)

    def last(self):
        request = self.requests[-1]
        url = urlsplit(request.full_url)
        body = json.loads(request.data) if request.data else None
        return request.method, url.path, parse_qs(url.query), body


PROFILE = {"userId": "owner", "username": "ownername"}
CAPTURE = {"id": "capture-id", "type": "neural_capture", "authorId": "owner",
           "authorUsername": "ownername", "name": "Original", "description": "Keep",
           "tags": ["clean"], "metadata": {"deviceType": "amp_head",
           "instrumentType": "guitar", "version": 2, "unknownField": "preserve"}}


def expect_error(fn, text=None):
    try:
        fn()
    except CloudError as error:
        if text:
            assert text.lower() in str(error).lower(), str(error)
        return str(error)
    raise AssertionError("expected CloudError")


def test_login_posts_credentials_and_never_returns_tokens():
    http = HTTP({"access_token": "login-secret", "refresh_token": "refresh-secret"})
    client = CloudClient(opener=http)
    result = client.login("person@example.com", "password-secret")
    method, path, _, body = http.last()
    assert (method, path) == ("POST", "/api/v1/user/signin")
    assert body == {"username": "person@example.com", "password": "password-secret"}
    assert "login-secret" not in repr(result)
    assert "refresh-secret" not in repr(result)
    assert "password-secret" not in repr(result)
    assert "login-secret" not in repr(client.status())


def test_auth_headers_are_sent_only_to_fixed_cloud_api():
    http = HTTP(PROFILE)
    client = CloudClient(opener=http, access_token="test-access-secret")
    client.profile()
    request = http.requests[-1]
    assert urlsplit(request.full_url).netloc == "cloud-api.neuraldsp.com"
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers["authorization"] == "Bearer test-access-secret"
    assert headers["x-cloud-api-client-type"] == "web-cloud"
    assert headers["user-agent"].split("/")[0] == "qc-mcp"


def test_user_profile_encodes_username_as_a_single_path_segment():
    http = HTTP(PROFILE)
    client = CloudClient(opener=http, access_token="token")
    client.profile("name@example.com")
    assert http.last()[1] == "/api/v1/users/name%40example.com"


def test_path_controls_and_empty_ids_are_rejected_before_network():
    http = HTTP()
    client = CloudClient(opener=http, access_token="token")
    for value in ("", "..", "../profile", "x?admin=true", "x#secret", "x\nheader", "x y"):
        expect_error(lambda value=value: client.product(value))
    assert not http.requests


def test_profile_nested_credentials_are_redacted():
    http = HTTP({**PROFILE, "nested": {"access_token": "hidden-access",
                "refresh_token": "hidden-refresh", "password": "hidden-password"}})
    client = CloudClient(opener=http, access_token="token")
    result = client.profile()
    assert result["username"] == "ownername"
    for secret in ("hidden-access", "hidden-refresh", "hidden-password"):
        assert secret not in repr(result), repr(result)


def test_invalid_search_arguments_do_not_make_requests():
    http = HTTP()
    client = CloudClient(opener=http, access_token="token")
    for kwargs in ({"category": "unknown"}, {"page": -1}, {"size": 0},
                   {"size": 10001}, {"sort_dir": "sideways"}):
        expect_error(lambda kwargs=kwargs: client.search(**kwargs))
    assert not http.requests


def test_http_errors_do_not_expose_remote_response_or_tokens():
    error = HTTPError("https://cloud-api.neuraldsp.com/api/v1/user/profile", 500,
                      "private-token", {}, io.BytesIO(b'{"message":"private-password"}'))
    http = HTTP(error)
    client = CloudClient(opener=http, access_token="private-access")
    message = expect_error(client.profile)
    for secret in ("private-token", "private-password", "private-access"):
        assert secret not in message, message
    assert "500" in message


def test_network_failures_are_cloud_errors_without_transport_secrets():
    http = HTTP(URLError("password=private-password"))
    client = CloudClient(opener=http, access_token="token")
    message = expect_error(client.profile)
    assert "private-password" not in message


def test_search_categories_pagination_and_author_filter_match_api():
    http = HTTP({"items": []}, {"items": []}, {"items": []})
    client = CloudClient(opener=http, access_token="token")
    for category, route in (("presets", "preset"), ("captures", "neuralCapture"),
                            ("users", "user")):
        client.search(category=category, query="Fender & Vox", page=2, size=10)
        method, path, params, _ = http.last()
        assert (method, path) == ("GET", "/search/v1/for/" + route)
        assert params["query"] == ["Fender & Vox"]
        assert params["page"] == ["2"] and params["size"] == ["10"]
        assert params["sortDir"] == ["descending"]

    http.responses.append({"items": []})
    client.search(category="captures", username="artist", captureVersion=2)
    params = http.last()[2]
    assert params["ownedBy"] == ["artist"]
    assert params["captureVersion"] == ["2"]


def test_own_capture_scope_uses_owned_by_me_and_authentication():
    http = HTTP({"items": []})
    client = CloudClient(opener=http, access_token="token")
    client.search(category="captures", scope="owned")
    assert http.last()[2]["ownedByMe"] == ["true"]
    expect_error(lambda: client.search(category="captures", username="artist", scope="owned"))
    assert len(http.requests) == 1


def test_download_queues_product_instead_of_fetching_binary_data():
    http = HTTP({"queued": True})
    client = CloudClient(opener=http, access_token="token")
    assert client.queue_download("capture-id") == {"queued": True}
    assert http.last() == ("POST", "/api/v1/user/downloaded_products/capture-id", {}, None)


def test_expired_access_token_refreshes_without_bearer_then_retries_once():
    expired = HTTPError("https://cloud-api.neuraldsp.com/api/v1/user/profile", 401,
                        "expired", {}, io.BytesIO(b"{}"))
    http = HTTP(expired, {"access_token": "new-private-token"}, PROFILE)
    client = CloudClient(opener=http, access_token="old-private-token")
    assert client.profile() == PROFILE
    assert len(http.requests) == 3
    refresh = http.requests[1]
    assert refresh.method == "POST"
    assert urlsplit(refresh.full_url).path == "/api/v1/user/refresh_token"
    assert refresh.data is None
    assert "authorization" not in {k.lower() for k, _ in refresh.header_items()}
    headers = {k.lower(): v for k, v in http.requests[2].header_items()}
    assert headers["authorization"] == "Bearer new-private-token"


def test_malformed_refresh_does_not_retry_with_invalid_credentials():
    expired = HTTPError("https://cloud-api.neuraldsp.com/api/v1/user/profile", 401,
                        "expired", {}, io.BytesIO(b"{}"))
    http = HTTP(expired, {"access_token": 123})
    client = CloudClient(opener=http, access_token="old-token")
    expect_error(client.profile, "expired")
    assert len(http.requests) == 2
    assert client.status()["authenticated"] is False


def test_follow_and_unfollow_use_observed_methods_and_validate_input():
    http = HTTP({}, {})
    client = CloudClient(opener=http, access_token="token")
    client.follow("artist")
    assert http.last()[:2] == ("PUT", "/api/v1/user/followings/artist")
    client.follow("artist", enabled=False)
    assert http.last()[:2] == ("DELETE", "/api/v1/user/followings/artist")
    expect_error(lambda: client.follow("artist", enabled="false"))
    assert len(http.requests) == 2


def test_metadata_update_preserves_unedited_values_and_nested_fields():
    http = HTTP(CAPTURE, PROFILE, {"updated": True})
    client = CloudClient(opener=http, access_token="token")
    assert client.update_capture("capture-id", name=" New name ", instrument_type="bass") == {"updated": True}
    method, path, _, body = http.last()
    assert (method, path) == ("PUT", "/api/v1/user/products/capture-id/metadata")
    assert body["name"] == "New name"
    assert body["description"] == "Keep" and body["tags"] == ["clean"]
    assert body["metadata"] == {**CAPTURE["metadata"], "instrumentType": "bass"}
    assert CAPTURE["metadata"]["instrumentType"] == "guitar"


def test_capture_writes_reject_foreign_or_missing_ownership():
    for product in ({**CAPTURE, "authorId": "someone-else"},
                    {k: v for k, v in CAPTURE.items() if k not in {"authorId", "authorUsername"}}):
        for operation in ("update", "publish", "delete"):
            http = HTTP(product, PROFILE)
            client = CloudClient(opener=http, access_token="token")
            if operation == "update":
                call = lambda: client.update_capture("capture-id", description="Changed")
            elif operation == "publish":
                call = lambda: client.publish_capture("capture-id", True)
            else:
                call = lambda: client.delete_capture("capture-id")
            expect_error(call, "own")
            assert len(http.requests) == 2
            assert all(r.method == "GET" for r in http.requests)


def test_capture_writes_reject_presets_before_mutation():
    http = HTTP({**CAPTURE, "type": "preset"})
    client = CloudClient(opener=http, access_token="token")
    expect_error(lambda: client.delete_capture("capture-id"), "capture")
    assert len(http.requests) == 1 and http.requests[0].method == "GET"


def test_capture_metadata_validation_precedes_network():
    http = HTTP()
    client = CloudClient(opener=http, access_token="token")
    for kwargs in ({}, {"name": ""}, {"name": "x" * 33},
                   {"tags": ["same", "same"]}, {"tags": [1]},
                   {"description": "x" * 1001}, {"device_type": "unknown"},
                   {"instrument_type": "unknown"}):
        expect_error(lambda kwargs=kwargs: client.update_capture("capture-id", **kwargs))
    assert not http.requests


def test_publish_unpublish_and_delete_use_owned_product_routes():
    for operation, method, suffix in (("publish", "POST", "/publish"),
                                      ("unpublish", "DELETE", "/publish"),
                                      ("delete", "DELETE", "")):
        http = HTTP(CAPTURE, PROFILE, {})
        client = CloudClient(opener=http, access_token="token")
        if operation == "delete":
            client.delete_capture("capture-id")
        else:
            client.publish_capture("capture-id", operation == "publish")
        assert http.last() == (method, "/api/v1/user/products/capture-id" + suffix, {}, None)


def test_explicit_session_persistence_is_private_and_logout_clears_it():
    with tempfile.TemporaryDirectory() as directory:
        session = Path(directory) / "cloud-session.json"
        http = HTTP({"access_token": "saved-private-token"}, {})
        client = CloudClient(opener=http, session_file=session)
        client.login("person@example.com", "password", persist=True)
        assert session.exists()
        if os.name != "nt":
            assert session.stat().st_mode & 0o077 == 0
        restored = CloudClient(opener=HTTP(PROFILE), session_file=session)
        assert restored.profile() == PROFILE
        assert "saved-private-token" not in repr(restored.status())
        client.logout()
        assert not session.exists()
        assert not client.status()["authenticated"]


def test_persistence_requires_explicit_login_option_and_configured_path():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
        session = Path(directory) / "cloud-session.json"
        http = HTTP({"access_token": "memory-only-token"})
        client = CloudClient(opener=http, session_file=session)
        client.login("person@example.com", "password")
        assert not session.exists()
        assert not client.status()["session_persisted"]
        no_path_http = HTTP()
        no_path = CloudClient(opener=no_path_http)
        expect_error(lambda: no_path.login("person@example.com", "password", persist=True), "session")
        assert not no_path_http.requests


def test_missing_login_and_authentication_fail_before_network():
    with patch.dict(os.environ, {}, clear=True):
        http = HTTP()
        client = CloudClient(opener=http)
        expect_error(client.login)
        expect_error(client.profile, "log in")
        assert not http.requests


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
