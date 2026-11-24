"""Hardware-independent Cortex Cloud tools registered on the existing MCP server."""
from __future__ import annotations

import os
import threading

from .cloud import CloudClient, CloudError

_client = None
_lock = threading.RLock()


def _cloud():
    global _client
    if _client is None:
        _client = CloudClient(
            session_file=os.environ.get("QC_CLOUD_SESSION_FILE") or None,
            access_token=os.environ.get("QC_CLOUD_ACCESS_TOKEN") or None,
        )
    return _client


def _call(method, *args, **kwargs):
    # A cookie jar and token refresh belong to one account/session. Serialize
    # requests so concurrent MCP calls cannot race a refresh or account switch.
    with _lock:
        try:
            return getattr(_cloud(), method)(*args, **kwargs)
        except (CloudError, ValueError) as exc:
            return {"error": str(exc)}


def register_cloud_tools(mcp):
    @mcp.tool()
    def cloud_login(persist: bool = True) -> dict:
        """WRITE: Sign in to Cortex Cloud independently of the Quad Cortex USB
        session. On macOS and Windows, sign in privately through Patchbay or
        `qc-mcp cloud login` to create a saved session that works with Patchbay
        closed. For a process-only login, set QC_CLOUD_EMAIL and QC_CLOUD_PASSWORD
        in the server environment and call cloud_login(persist=False). You can
        also set QC_CLOUD_ACCESS_TOKEN in the server environment."""
        email = os.environ.get("QC_CLOUD_EMAIL", "")
        password = os.environ.get("QC_CLOUD_PASSWORD", "")
        if not email and not password:
            result = _call("status")
            if result.get("authenticated") or result.get("error"):
                return result
            return {"error": "On macOS or Windows, sign in through Patchbay or run `qc-mcp cloud login`. "
                             "For a process-only login, set QC_CLOUD_EMAIL and QC_CLOUD_PASSWORD in the "
                             "MCP server environment and call cloud_login(persist=False), or set QC_CLOUD_ACCESS_TOKEN."}
        return _call("login", email, password, persist=persist)

    @mcp.tool()
    def cloud_logout() -> dict:
        """WRITE: Sign out of Cortex Cloud and clear this server's cloud session.
        Does not log the physical Quad Cortex or Cortex Control out."""
        return _call("logout")

    @mcp.tool()
    def cloud_status() -> dict:
        """Read this server's Cortex Cloud authentication status without exposing
        access tokens, refresh tokens, passwords, or session cookies."""
        return _call("status")

    @mcp.tool()
    def cloud_search_presets(query: str = "", page: int = 0, size: int = 20,
                             sort: str = "relevance", sort_dir: str = "descending",
                             username: str = "") -> dict:
        """Read-only search for Cortex Cloud presets, optionally by author's
        username. Pages start at zero. Use returned product IDs with
        cloud_get_product and cloud_queue_download; these are not local slots."""
        return _call("search", category="presets", query=query, page=page, size=size,
                     sort=sort, sort_dir=sort_dir, username=username)

    @mcp.tool()
    def cloud_search_captures(query: str = "", page: int = 0, size: int = 20,
                              sort: str = "relevance", sort_dir: str = "descending",
                              username: str = "") -> dict:
        """Read-only search for Neural Captures on Cortex Cloud, optionally by
        author username. Pages start at zero. Cloud IDs need downloading and
        device sync before add_capture can use the local directory key."""
        return _call("search", category="captures", query=query, page=page, size=size,
                     sort=sort, sort_dir=sort_dir, username=username)

    @mcp.tool()
    def cloud_search_users(query: str = "", page: int = 0, size: int = 20) -> dict:
        """Read-only Cortex Cloud user/profile search. Pages start at zero;
        inspect a result with cloud_get_profile(username), then search that
        user's presets or captures by username."""
        return _call("search", category="users", query=query, page=page, size=size)

    @mcp.tool()
    def cloud_get_profile(username: str = "") -> dict:
        """Read a Cortex Cloud user's profile by username. An empty username
        returns the signed-in user's own profile and requires cloud_login."""
        return _call("profile", username=username)

    @mcp.tool()
    def cloud_get_product(product_id: str) -> dict:
        """Read Cortex Cloud preset or capture details by product ID, including
        its author and metadata, before queueing or managing the product."""
        return _call("product", product_id)

    @mcp.tool()
    def cloud_my_captures(query: str = "", page: int = 0, size: int = 20) -> dict:
        """Read the signed-in user's own Cortex Cloud captures, including private
        captures available to that account. Pages start at zero; requires login.
        Use product IDs for metadata, publishing, or deletion tools."""
        return _call("search", category="captures", query=query, page=page,
                     size=size, scope="owned")

    @mcp.tool()
    def cloud_queue_download(product_id: str) -> dict:
        """WRITE: Add a Cortex Cloud preset/capture to the account download queue.
        Requires login. This does not prove device download or load. After
        Cortex Control/device sync, refresh search_directory, then switch_preset
        with downloads_cloud_id or add_capture with the local key and name."""
        return _call("queue_download", product_id)

    @mcp.tool()
    def cloud_update_capture(product_id: str, name: str | None = None,
                             description: str | None = None,
                             tags: list[str] | None = None,
                             instrument_type: str | None = None,
                             device_type: str | None = None) -> dict:
        """WRITE: Edit metadata of a capture owned by the signed-in user.
        Omitted fields are preserved. Inspect cloud_get_product first; this
        changes cloud metadata, not a capture block in the current preset."""
        return _call("update_capture", product_id, name=name, description=description,
                     tags=tags, instrument_type=instrument_type, device_type=device_type)

    @mcp.tool()
    def cloud_publish_capture(product_id: str, published: bool = True) -> dict:
        """WRITE: Publish an owned Cortex Cloud capture publicly, or unpublish
        it with published=False. Requires login and verifies capture ownership."""
        return _call("publish_capture", product_id, published=published)

    @mcp.tool()
    def cloud_delete_capture(product_id: str, confirm: bool = False) -> dict:
        """WRITE: Permanently delete an owned Cortex Cloud capture. Inspect the
        product first and pass confirm=True to delete. Requires login and
        verifies ownership; this does not delete an on-device capture copy."""
        if not confirm:
            return {"error": "Deletion requires confirm=True after reviewing the capture."}
        return _call("delete_capture", product_id)

    @mcp.tool()
    def cloud_follow_user(username: str, follow: bool = True) -> dict:
        """WRITE: Follow a Cortex Cloud user, or unfollow with follow=False,
        using their username from cloud_search_users. Requires cloud login."""
        return _call("follow", username, enabled=follow)
