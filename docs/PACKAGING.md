# Packaging Patchbay

What ships in the installer, what gets built on the user's machine, and why the
line falls where it does.

## What the package contains

| | in the package | built on the machine |
|---|---|---|
| Electron + the app bundles | ✅ | |
| qc-mcp sources (`src/`, `proto/`, `pyproject.toml`) | ✅ | |
| interceptor sources (`interpose.c`, `build.sh`, `run-bridge.sh`) | ✅ | |
| `uv` (one binary, arch-matched) | ✅ | |
| **Python interpreter** | ❌ | uv downloads CPython 3.12 |
| **`.venv` + dependencies** | ❌ | `uv venv` + `uv pip install -e .` |
| `interpose.dylib` + the instrumented Cortex Control | ❌ | `interceptor/build.sh` (macOS) |

Roughly: **~110 MB installer**, of which Electron is the bulk and uv is ~40 MB.
First run adds ~55 MB in `~/.local/share/uv` (the interpreter) plus a 30 MB
`.venv`.

## Why no Python environment in the package

It was the obvious thing to bundle, and it is the wrong thing.

**A venv is bound to its absolute path.** Not by convention — literally. The
console script `uv` and `pip` generate contains the interpreter path inline:

```sh
#!/bin/sh
'''exec' '/…/qc-mcp/.venv/bin/python' "$0" "$@"
' '''
```

Patchbay does stage its payload to a deterministic location, so a prebuilt venv
*could* be made to work — but only if CI built it at exactly that path, per
platform **and** per arch.

**And then macOS would have to sign all of it.** `pydantic-core`, `rpds-py`,
protobuf's upb and the rest are native Mach-O objects; every one becomes part of
the bundle's signature, and any that came from a wheel built elsewhere is a
notarization problem. That is a large, permanent maintenance surface bought for
a step that takes eight seconds.

## Why `uv` instead

The real gap was never dependency installation, it was **Python itself**, and on
both platforms the stock machine fails:

- **macOS** — `/usr/bin/python3` is **3.9.6**. The package needs >= 3.10. A Mac
  with nothing but the Xcode command line tools cannot run qc-mcp, and the old
  setup path could only tell the user to go install one.
- **Windows** — ships no Python at all.

For an app whose whole premise is "it does the setup for you", "first, go
install Python" is the one instruction it must not give. uv closes that: one
static binary that fetches its own interpreter and builds the environment.

Measured cold, from a machine with no usable Python:

```
uv venv --python 3.12 .venv     5.5s   (downloads CPython 3.12.14, 23.8 MB)
uv pip install -e .             2.7s   (30 MB venv)
```

The result is the layout of an ordinary checkout — `<repo>/.venv/bin/qc-mcp` —
which is what everything downstream already assumes: `pathsFor()` in
[paths.ts](../app/src/main/paths.ts), and the command path Patchbay writes into
each MCP client's config.

The install is **editable** on purpose. `stagedRepo()` refreshes the payload
whenever the app version changes, and an editable install already points at
those same sources — so a Patchbay upgrade ships new qc-mcp code without
rebuilding the environment, and the `.venv` inside the staging directory
survives because only the payload's own entries are copied over it.

**No extras.** `[gui]` is pyobjc (~35 MB) for the `tools/gui/` harness, and
`tools/` is not part of the payload. Nothing under `src/qc_mcp` imports it — the
macOS HID transport is ctypes against IOKit.

If uv is absent (a `git clone` + `npm run dev` with no fetch step), setup falls
back to a system interpreter that satisfies >= 3.10. `PATCHBAY_NO_UV=1` builds a
package without it, deliberately.

## What is still built locally, and cannot be shipped

**macOS bridge mode needs a compiler.** `interceptor/build.sh` compiles
`interpose.c` and re-signs a copy of Cortex Control ad-hoc, so bridge mode
requires the Xcode command line tools. Patchbay can trigger Apple's installer
but not complete it — the setup check stays `missing` until the user finishes.
Direct mode needs none of this. On Windows there is no interceptor at all:
shared mode is a second non-exclusive HID handle.

**The instrumented Cortex Control copy never leaves the machine.** It is a
re-signed copy of Neural DSP's app; building it locally from the user's own
install is the only defensible form. See the conventions note in
[CLAUDE.md](../CLAUDE.md).

## Signing

Two paths, and which one you get depends only on whether a **Developer ID
Application** certificate is reachable.

**With one** (release CI, or a Mac that has the certificate in its keychain):
electron-builder signs the bundle for real, under Hardened Runtime with
[`build/entitlements.mac.plist`](../app/build/entitlements.mac.plist), and
notarizes and staples it. The vendored `uv` is inside that signature —
afterPack places it **before** signing, and the signer walks all of
`Contents/`. In CI the certificate comes from a base64 repository
secret. Locally it comes from a keychain you prepare from the `.p12` — *not*
from the copy in your login keychain, which prompts for a password and fails
as `errSecInternalComponent` if you decline. Notarization credentials come
from the SOPS store SingZ maintains (this repo keeps none of its own) and from
repository secrets in CI. Full detail — including why
`CSC_LINK` is not used, and why getting the keychain's key partition list
wrong makes several hundred `codesign` calls fail on a headless runner — is in
[MACOS-SIGNING.md](MACOS-SIGNING.md).

**Without one** (a fork, or any Mac with no certificate): electron-builder logs
`skipped macOS application code signing` and leaves the bundle alone, and
[afterPack.cjs](../app/scripts/afterPack.cjs)'s ad-hoc signature is the final
one. Repacking resources into the prebuilt Electron binary invalidates its
signature, and a *broken* signature plus quarantine gets "app is damaged" with
no right-click escape; a valid ad-hoc one downgrades that to the ordinary
"unidentified developer" prompt. This is what v0.1.0 shipped.

Note the removal: `mac.identity` used to be `null`. That made `sign()` return
immediately — and since notarization runs at the *end* of `sign()`, a
`notarize: true` next to it would have been dead configuration. The key is now
absent so electron-builder can auto-discover an identity, and its absence is
what selects the fallback above.

## Building

```bash
cd app
npm ci
npm run dist:mac      # or dist:win
```

`dist:*` runs `fetch:uv` first. That script pins the uv version **and** the
sha256 of each archive: a release asset can be re-uploaded, and this binary ends
up inside a signed bundle. Bump both together.

## Releasing

[.github/workflows/release.yml](../.github/workflows/release.yml) — `macos-latest`
(dmg, arm64 + x64) and `windows-latest` (nsis, x64):

1. Run the offline Python suite. A red suite never gets packaged.
2. `npm ci`, set the version from the tag, fetch uv (cached), build, then on
   the macOS leg import the Developer ID certificate into a throwaway keychain
   and hand `CSC_NAME`/`CSC_KEYCHAIN` to the Package step (see
   [MACOS-SIGNING.md](MACOS-SIGNING.md) — absent secrets degrade to the ad-hoc
   dmg rather than failing), package.
   macOS packages **one arch per `electron-builder` invocation**. A single
   `--mac` run builds both DMGs in parallel and each mounts its volume under
   the same title, so they collide on `/Volumes/Patchbay` and `hdiutil detach`
   fails every retry. Do not merge them back into one command.
3. Upload artifacts on every run.
4. On a `v*` tag only, create the release and attach the dmg/exe, the
   blockmaps, and `latest.yml`. A hyphenated tag (`v0.2.0-rc1`) is marked
   prerelease. `docs/release-notes/<tag>.md`, if present, supplies the title
   (first line) and body.

`latest.yml` is not optional decoration: it is the feed Patchbay's in-app
updater reads on Windows, and a release published without it leaves every
installed Windows copy believing it is current. It exists because
`electron-builder.yml` carries a `publish:` block — that block makes the nsis
target write the file and packages `app-update.yml` into the app, and nothing
in it ever uploads anything (every invocation still passes `--publish never`).
The macOS leg writes a `latest-mac.yml` of its own — dmg-builder's `DmgTarget`
sets `isWriteUpdateInfo` from the blockmap it builds, so a dmg-only build does
produce one — and the attach step globs `latest.yml` rather than `latest*.yml`
so it stays unpublished. Two arches, two `electron-builder` invocations, one
filename: it only ever describes whichever ran last, and publishing it would
leave correct-looking metadata that offers an Apple Silicon user the Intel dmg
the day macOS is wired up to electron-updater. macOS is handed the release page
instead. See the Updates section of [../app/README.md](../app/README.md).

A hyphenated prerelease tag is invisible to the updater on both platforms —
GitHub's `latest` endpoint skips prereleases, and `electron-updater` is left on
the default stable channel.

To cut a release: commit, `git tag v0.2.0`, `git push --tags`.

The weekly run is a smoke test. Packaging breaks in ways the Checks workflow
cannot see — electron-builder never runs there — so a release must not be the
first time this path is exercised.
