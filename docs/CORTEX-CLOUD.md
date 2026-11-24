# Cortex Cloud

qc-mcp exposes Cortex Cloud search, profiles, download queuing, and management of
captures owned by the signed-in account. These tools use HTTPS and work without a
USB connection. Device control still uses the existing `connect` workflow.

## Sign in

In Patchbay, click **Cloud**, enter your email and password, and click
**Sign in**. The session stays signed in securely on this computer. The password
is discarded after authentication. The account screen shows the signed-in email
and provides **Sign out**.

On macOS and Windows, you can also sign in from a terminal with Patchbay closed:

```bash
qc-mcp cloud login
qc-mcp cloud status
qc-mcp cloud logout
```

The login command prompts for your email and a hidden password; never put a
password in command arguments or chat. Run the command from the qc-mcp virtual
environment if `qc-mcp` is not on PATH (`.venv/bin/qc-mcp` on macOS,
`.venv\Scripts\qc-mcp.exe` on Windows).

Patchbay, terminal login, and MCP share one session under the current OS user.
**Patchbay does not need to be open:** a standalone MCP process restores the
session and refreshes it independently, without a USB connection or the HID
daemon. Already-running MCP processes notice a later login or logout on their
next cloud request. Sessions shared by multiple clients coordinate refreshes so
rotating cookies are not overwritten. Signing out removes the shared saved
session; a revoked or expired refresh session requires signing in again.

The renewable session is encrypted with authenticated encryption. Its encryption
key is stored in macOS Keychain or Windows Credential Manager. The encrypted
file is under `~/Library/Application Support/qc-mcp/cloud-session.enc` on macOS,
or `%LOCALAPPDATA%\qc-mcp\cloud-session.enc` on Windows. No password is saved.
There is no plaintext fallback when the OS credential store is unavailable;
unlock it and retry. The OS may ask permission for the qc-mcp Python interpreter
to access the credential store.

`cloud_login()` without credentials restores the saved session or explains how
to sign in privately. `cloud_status()` reports account status without credentials.
`cloud_logout()` signs out and removes the shared saved sign-in.

### Linux

The terminal's remembered sign-in uses the macOS or Windows credential store.
On Linux, supply `QC_CLOUD_EMAIL` and `QC_CLOUD_PASSWORD` to the MCP server
through your client's private environment configuration, then call
`cloud_login(persist=False)`. Sign in again after restarting the server. If you
already have an access token, supply `QC_CLOUD_ACCESS_TOKEN` to the MCP server
and call `cloud_status()`.

### Automation

For automation, existing `QC_CLOUD_EMAIL`, `QC_CLOUD_PASSWORD` and
`QC_CLOUD_ACCESS_TOKEN` support remains. On macOS and Windows, environment-based
MCP login saves securely by default. `persist=False` keeps a login in that server
process only.
The old explicitly configured `QC_CLOUD_SESSION_FILE` format remains compatible
and stores unencrypted session data with restricted file permissions. Prefer the
new sign-in flow, which ignores that legacy setting and uses OS-protected storage.
When switching an existing MCP configuration to shared sign-in, remove its old
`QC_CLOUD_*` credential overrides so it uses the same account as Patchbay and the
terminal command.

## Discover and use content

In Patchbay, open **Cloud** after signing in. Choose **Neural Captures** or **Presets**, then search by name, gear, or keyword.
Expand a result to view its metadata. Preset details include compatibility, blocks,
and named scenes when Cortex Cloud provides them. Search works without
opening Cortex Control or connecting a Quad Cortex.

Patchbay displays 10 results per page. MCP search pages start at **0** and
default to 20 results.

```python
cloud_search_presets(query="clean", page=0, size=20)
cloud_search_captures(query="AC30", page=0, size=20)
cloud_search_users(query="artist", page=0, size=20)
cloud_get_profile(username="artist")
cloud_search_captures(query="", username="artist")
cloud_get_product(product_id="<id from search>")
```

Preset and capture searches accept `sort="relevance"` and
`sort_dir="descending"` by default. Use the returned product IDs for subsequent
operations. `cloud_get_profile()` with no username reads your own profile.
`cloud_follow_user(username="artist", follow=True)` follows a user;
`follow=False` unfollows them. This changes the cloud account.

To use a preset or capture on the Quad Cortex:

1. Call `cloud_queue_download(product_id="<id>")`. This writes to the account's
   download queue.
2. Let the Quad Cortex or Cortex Control synchronize with the same cloud account.
3. Refresh `search_directory(query="<name>", category="presets", refresh=True)`
   or use `category="captures"`.
4. For presets, pass the directory result's `downloads_cloud_id` to
   `switch_preset(downloads_cloud_id="<id>")`. For captures, use
   `add_capture(row=0, column=0, capture_key="<directory key>",
   capture_name="<directory name>")`.

Queue success proves the cloud accepted the request. It does not prove that the
hardware downloaded the content or loaded it into the current preset. Recall
changes the active preset; placing a capture changes the current grid. Use
`get_current_preset()` to inspect the recalled preset or confirm the Neural
Capture block's placement. To confirm the selected capture itself, inspect the
block in Cortex Control or on the device.

## Manage your captures

`cloud_my_captures(query="", page=0, size=20)` searches only captures owned by
your account, including private content available to that session. Inspect a
capture with `cloud_get_product(product_id=...)` before editing it.

```python
cloud_update_capture(product_id="<owned capture id>",
                     name="My amp clean", description="Bridge pickup",
                     tags=["clean"], instrument_type="guitar",
                     device_type="amp_head")
cloud_publish_capture(product_id="<owned capture id>", published=True)
cloud_publish_capture(product_id="<owned capture id>", published=False)
cloud_delete_capture(product_id="<owned capture id>", confirm=True)
```

Updates change only supplied metadata fields. Management requires the capture's
author account. Instrument types are `guitar`, `bass`, `synth`, `vocal`, `other`,
and `instrument_not_set`. Device types are `amp_head`, `amp_and_cab`,
`amp_combo`, `cab`, `pedal`, `default`, `fuzz`, `compressor`, and `overdrive`.
Publishing makes a capture public; unpublishing removes its public visibility.
Deletion requires `confirm=True` and removes the cloud capture. These tools do
not upload a new capture payload or remove a downloaded copy from the hardware.

## API evidence and verification

The integration follows requests and schemas shipped by the official
[Cortex Cloud website](https://cloud.neuraldsp.com/), specifically its
[production JavaScript bundle](https://cloud.neuraldsp.com/assets/index-MV_rnE6C.js)
observed on 2026-11-24, backed by `https://cloud-api.neuraldsp.com`. These
are observed website contracts, not a separately documented public API. The
observed routes include:

| Operation | Request |
| --- | --- |
| Sign in / refresh | `POST /api/v1/user/signin`, `POST /api/v1/user/refresh_token` |
| Search | `/search/v1/for/preset`, `/search/v1/for/neuralCapture`, `/search/v1/for/user` |
| Own profile / public user | `GET /api/v1/user/profile`, `GET /api/v1/users/{username}` |
| Product details | `GET /api/v1/products/{id}` |
| Queue download | `POST /api/v1/user/downloaded_products/{id}` |
| Update owned metadata | `PUT /api/v1/user/products/{id}/metadata` |
| Publish / unpublish | `POST` / `DELETE /api/v1/user/products/{id}/publish` |
| Delete owned product | `DELETE /api/v1/user/products/{id}` |

The installed Cortex Control application independently contains the sign-in,
refresh, profile, product metadata and publish route fragments. Offline tests
check request construction, capture ownership and metadata, independent-process
session restoration and refresh, concurrent refresh, and logout propagation.
Live verification covered sign-in, search, reading product details with
Patchbay closed, and downloading and placing a capture. Capture metadata edits,
publishing, and deletion were tested with offline HTTP fixtures. This is an
unofficial integration with the website's API, whose contracts may change
independently of CorOS.
