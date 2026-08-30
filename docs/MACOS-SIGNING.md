# Signing and notarizing Patchbay

**v0.1.0 shipped ad-hoc signed** — [afterPack.cjs](../app/scripts/afterPack.cjs)
repairs the signature our repacked resources invalidate, which turns the
unrecoverable "app is damaged" quarantine dialog into the ordinary
"unidentified developer" right-click-to-open flow. Every download still needed
that workaround, and the release notes had to tell people to perform it.

A **Developer ID Application** certificate plus Apple notarization removes it:
Gatekeeper checks the stapled notarization ticket instead of complaining.

The ad-hoc path is not replaced, it is demoted to a fallback. `afterPack.cjs`
still signs every bundle built without a certificate, which is what keeps a
fork — and any Mac that has never held a Developer ID certificate — building
exactly as before.

## The shape of it

Two tools, and it matters which owns what:

| | owns |
|---|---|
| **SOPS** | the local secret store: the App Store Connect API key id, issuer id and `.p8` body |
| **electron-builder** | building, signing, notarizing, stapling |

The **Developer ID Application certificate** is in neither. Locally it sits in
your login keychain and electron-builder auto-discovers it; in CI it arrives as
a base64 repository secret that the workflow imports into a throwaway keychain.

This is the shape SingZ uses for its own Electron desktop build
(`docs/MACOS-SIGNING.md` there). Worth stating because the obvious guess is
wrong: SingZ *does* use fastlane and `match`, but only for **iOS and Android**.
Its macOS path has no fastlane in it at all, and nothing macOS-related goes
into the `singz-ios-certs` repo — that repo holds an Apple Distribution
certificate and iOS provisioning profiles, and an Apple Distribution
certificate cannot sign a notarized macOS app.

## The secret store, which lives in SingZ

**This repo has no store of its own, on purpose.** For macOS Developer ID
nothing is per-app — no App ID, no bundle registration, no provisioning
profile — so one team has exactly one Developer ID certificate and one App
Store Connect key. Apple also caps Developer ID certificates per team and
hands out a `.p8` exactly once. A second copy would be a second thing to
rotate, leak and lose, for no gain, so Patchbay reads the store SingZ already
maintains:

```
~/Dev/my/SingZ/.keys/secrets.enc.yaml     # override with QC_SOPS_STORE
```

It is [SOPS](https://github.com/getsops/sops)-encrypted to the age key at
`~/.config/sops/age/keys.txt`, and only *values* are encrypted — the key names
stay readable, so the layout can be confirmed without decrypting anything.
The keys this repo uses:

```yaml
asc_key_id:        # App Store Connect API key id
asc_issuer_id:     # App Store Connect issuer id (a UUID)
asc_key_p8:        # the .p8 body, multi-line PEM
mac_p12_password:  # the Developer ID .p12's export password (local builds only)
team_id:           # absent there; defaults to USJ7H3X44X
```

[`scripts/with-apple-secrets.sh <command>`](../scripts/with-apple-secrets.sh)
decrypts it, exports `APPLE_API_KEY` (a mode-600 temp `.p8` **path**),
`APPLE_API_KEY_ID`, `APPLE_API_ISSUER` and `APPLE_TEAM_ID`, runs the command,
and deletes the temp file on the way out — so no secret reaches a shell
history, a process argument list, or a file that outlives the command.

```bash
scripts/with-apple-secrets.sh bash -c 'cd app && npm run dist:mac'
```

It also exports `PATCHBAY_MAC_P12_PASSWORD` when the store carries
`mac_p12_password`. That name is deliberate: `afterPack.cjs` treats
`CSC_KEY_PASSWORD` as "real signing is configured" and skips its ad-hoc
fallback, so exporting the `.p12` password under Apple's own name on a machine
with no importable identity would produce a bundle that is neither Developer
ID-signed nor ad-hoc signed — the one macOS refuses to open outright.

The helper does not deal with the signing identity; see
[Building locally on a Mac that has the certificate](#building-locally-on-a-mac-that-has-the-certificate).

Two things are deliberate:

- **Nothing encrypted is committed here, and `.keys/` stays ignored anyway** —
  in `.gitignore` *and* `.git/info/exclude` (the latter covers every worktree
  immediately, which the tracked file cannot until it is merged). This repo is
  public, and the belt-and-braces costs nothing even with no store present.
- **`SOPS_AGE_KEY_FILE` is set explicitly** by the helper. Measured: sops does
  not fall back to the default age key path on its own — it reports only the
  `SOPS_AGE_*`/SSH locations and fails to decrypt.

## The certificate

A **Developer ID Application** certificate — a third Apple certificate type,
distinct from the two an iOS pipeline uses:

| Certificate | Signs |
| --- | --- |
| Apple Development | iOS dev builds |
| Apple Distribution | iOS App Store / TestFlight builds |
| **Developer ID Application** | **macOS apps distributed outside the Mac App Store** |

Make one in Xcode → Settings → Accounts → your team → Manage Certificates →
**+** → **Developer ID Application**, or in the portal from a CSR. No
provisioning profile: Developer ID apps are not sandboxed or profile-scoped the
way iOS builds are. There is already one in this Mac's login keychain, which is necessary but
not sufficient — a local build will *find* it and then fail to *use* it. See
[Building locally on a Mac that has the certificate](#building-locally-on-a-mac-that-has-the-certificate).

Apple caps these at five per account and will not re-issue the private key for
an existing one, so export rather than regenerate when wiring up a second
machine or CI.

## Building locally on a Mac that has the certificate

"The certificate is in the login keychain" and "a local build can sign with
it" are different claims, and on this Mac only the first is true. With no
`CSC_*` set, electron-builder does not skip signing — it runs
`findIdentity`, which searches the **user keychain search list**, finds the
Developer ID identity, and signs with it. Then `codesign` needs the private
key, the key's ACL does not list `codesign` as an always-allowed caller, and
macOS puts up a keychain password dialog. Cancel it and every retry fails the
same way; electron-builder retries four times and dies on whichever file it
reached first:

```
locale.pak: errSecInternalComponent
⨯ Command failed: codesign --sign <hash> --force --timestamp --options runtime …
```

That error names a file, so it reads like a bad bundle. It is not — it is the
keychain refusing a non-interactive caller, and it will hit any file.

Three ways out, in increasing order of how much they leave behind:

1. **Answer the dialog once with "Always Allow."** This is the only step that
   needs the login password, and it is a one-time grant that amends the key's
   ACL. `security set-key-partition-list -S apple-tool:,apple:,codesign: -s
   -k <login password> ~/Library/Keychains/login.keychain-db` does the same
   thing from a terminal, for every key in the keychain at once.
2. **Do locally what CI does.** Needs the `.p12` and its export password;
   needs no login password, and prompts for nothing. `mac_p12_password` is in
   the SOPS store, so `scripts/with-apple-secrets.sh` can supply it as
   `PATCHBAY_MAC_P12_PASSWORD`:

   ```bash
   kc=~/Library/Keychains/patchbay-signing.keychain-db
   kcpw=$(openssl rand -base64 24)        # OURS, not the .p12's
   security create-keychain -p "$kcpw" "$kc"
   security set-keychain-settings "$kc"   # else it re-locks after 300s idle
   security unlock-keychain -p "$kcpw" "$kc"
   scripts/with-apple-secrets.sh bash -c \
     'security import <path>/singz-mac-sign.p12 -k "'"$kc"'" \
        -T /usr/bin/codesign -P "$PATCHBAY_MAC_P12_PASSWORD"'
   security set-key-partition-list -S apple-tool:,apple: -s -k "$kcpw" "$kc"

   export CSC_KEYCHAIN="$kc"
   export CSC_NAME="Alexey Tarasov (USJ7H3X44X)"   # BARE — no type prefix
   # CSC_LINK unset. Always.
   scripts/with-apple-secrets.sh bash -c 'cd app && npm run dist:mac'
   ```

   **Put your keychain first in the search list, and restore it after.**
   `codesign --keychain` constrains the *certificate* search; the
   **private-key** lookup still walks the user search list. So if any other
   keychain holding the same certificate sits earlier in that list and is
   locked, codesign resolves the identity into a keychain it cannot open and
   fails — and because the same certificate legitimately lives in several
   keychains at once on a developer's Mac, passing `--keychain` is not the
   protection it appears to be:

   ```bash
   ORIG=$(security list-keychains -d user | tr -d '"')
   trap 'security list-keychains -d user -s $ORIG' EXIT
   security list-keychains -d user -s "$kc" $ORIG
   ```

   Measured here: with a locked `singz-signing.keychain-db` first in the list,
   electron-builder issued `codesign --sign <hash> --keychain <our keychain>`
   and got `errSecInternalComponent` — reported against
   `Electron Framework.framework/.../locale.pak`, which reads like a damaged
   bundle and is nothing of the sort. With the same keychain moved to the
   front, the identical signature succeeds, with
   `Authority=Developer ID Application` and a secure timestamp. Add
   `codesign:` to the partition list too (`-S apple-tool:,apple:,codesign:`);
   `-T /usr/bin/codesign` at import time is not sufficient on its own.

   CI never sees this: a fresh runner's login keychain holds no Developer ID
   certificate, so there is only ever one candidate. It is purely a
   developer-machine failure, which is why it is easy to misdiagnose as a
   packaging bug.

   Keep `$kcpw` for the session; you need it again to unlock after a reboot.
   If reading the `.p12` fails in a way that looks like a wrong password,
   suspect OpenSSL before the credential: OpenSSL 3 rejects the legacy PKCS#12
   encryption (RC2-40-CBC) that Keychain Access writes, and needs `-legacy` —
   `openssl pkcs12 -in cert.p12 -legacy -nokeys -passin pass:"$PATCHBAY_MAC_P12_PASSWORD"`.
   Without the flag the error sends you hunting for a rotated credential that
   is perfectly fine. (`security import` itself is unaffected.)

   This is the shape [SingZ documents][singz] for its own local builds — with
   one correction: that write-up has `with-apple-secrets.sh` exporting the
   `.p12` password, but no checked-in copy of SingZ's helper reads
   `mac_p12_password` at all, so there the variable expands empty. Ours does
   read it.
3. **Do not sign locally at all.** Signing is CI's job; a local build only has
   to prove the packaging still works.

Option 3 has a trap worth stating outright, because it is the opposite of what
you would guess: on a Mac **with** a Developer ID certificate, the ordinary
"unsigned" build is not the default and cannot be reached by simply not
setting anything. Auto-discovery finds the identity and you are back at the
dialog. Turn discovery off explicitly:

```bash
CSC_IDENTITY_AUTO_DISCOVERY=false npm run dist:mac
```

That takes the same path a fork takes: electron-builder logs `skipped macOS
application code signing`, `notarize: true` is never reached (it is called
from the end of `sign()`), and `afterPack.cjs`'s ad-hoc signature stays final.
It is the only local build in this repo that is verified to run start to
finish — see [Verification status](#verification-status).

[singz]: https://github.com/lexasoft123/SingZ/blob/main/docs/MACOS-SIGNING.md

## Why CI imports the certificate itself instead of setting `CSC_LINK`

Signing a Patchbay bundle is several hundred `codesign` invocations, each
needing access to the Developer ID private key. Get the keychain's **key
partition list** wrong and every one fails with `errSecInternalComponent` — on
a developer Mac as a modal prompt per file, on a headless runner as a hard
failure with no one to click "Allow".

`CSC_LINK` is the documented way to hand electron-builder a `.p12`, and it is
**broken in the pinned electron-builder 25.1.8**. Do not "simplify"
`release.yml` to it. `app-builder-lib`'s `createKeychain` builds the temporary
keychain with `randomBytes(32).toString("base64")` as its password, and then
`importCerts` runs, per certificate:

```
security import <cert> -k <keychain> -T /usr/bin/codesign -P <p12 password>
security set-key-partition-list -S apple-tool:,apple: -s -k <p12 password> <keychain>
```

The `import -P` is right. The `set-key-partition-list -k` is not: that flag
takes the **keychain** password and is being given the `.p12` one. The two
agree only if you happened to use the same string for both. The build then dies
as a bare `security process failed 1` — after the app builds and before
anything is packaged, with nothing pointing at a password mix-up — and
electron-builder echoes the failing command, **including the `.p12` password**,
into the job log.

This was found on SingZ against 26.15.3; it is **confirmed present in the
25.1.8 this repo pins**, read from the installed
`app/node_modules/app-builder-lib/out/codeSign/macCodeSign.js` (`importCerts`,
around line 169) rather than carried over on the assumption that two majors
behave alike.

`CSC_KEYCHAIN` is only consulted when `CSC_LINK` is unset — `macPackager.js`
calls `createKeychain` solely when `getCscLink()` is non-null, and otherwise
falls through to `{ keychainFile: process.env.CSC_KEYCHAIN || null }`. So the
workflow creates the keychain, imports into it, sets the partition list with
the keychain's *own* password, and exports `CSC_KEYCHAIN` + `CSC_NAME` —
never `CSC_LINK`.

`CSC_NAME` must be the identity **bare**, without the
`Developer ID Application: ` prefix: `findIdentity()` runs the value through
`checkPrefix()`, which throws `Please remove prefix …` on the prefixed form —
exactly the form `security find-identity` prints. The step strips it, and
rejects a `.p12` that turns out to hold an `Apple Development` or `Apple
Distribution` certificate rather than putting a non-Developer-ID name into
`CSC_NAME`.

> **Note for SingZ:** its `docs/MACOS-SIGNING.md` still contains a stale
> paragraph saying the step "maps the repo secrets onto … `CSC_LINK`,
> `CSC_KEY_PASSWORD` …". Its `build.yml` does not — it uses the
> `create-keychain` path described above. The prose contradicts the section
> directly above it in the same file, and should be corrected there.

## The CI secrets

```bash
# Keychain Access → "Developer ID Application: …" → right-click → Export →
# .p12 → set a password → save as developer-id.p12
gh secret set APPLE_DEVELOPER_ID_CERTIFICATE_BASE64 < <(base64 -i developer-id.p12)
gh secret set APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD --body "<the .p12 export password>"

gh secret set APP_STORE_CONNECT_API_KEY_ID    --body '<KeyID>'
gh secret set APP_STORE_CONNECT_API_ISSUER_ID --body '<IssuerID>'
gh secret set APP_STORE_CONNECT_API_KEY_BASE64 < <(base64 -i AuthKey_<KeyID>.p8)
```

The last three are the **same three** SingZ already sets — one App Store
Connect key serves both repositories' notarization.

With none of them set the macOS leg still succeeds and produces an ad-hoc
signed dmg, with a `::warning::` saying so — that tolerance is deliberate and
is exercised by every fork.

Once they *are* set, note what the schedule does: the weekly run signs and
notarizes too, because nothing about signing is gated on a tag ref (only the
release attachment is). That is the point — it keeps the credential path
exercised, so an expired certificate or a revoked API key surfaces on a Tuesday
rather than on the release you are trying to cut. It also puts the macOS leg in
Apple's notary queue on every run, which is what the raised `timeout-minutes`
accounts for.

## What electron-builder does with it

[`app/electron-builder.yml`](../app/electron-builder.yml)'s `mac:` block. Three
things about its shape are load-bearing:

**`identity` is gone, not set to `null`.** With `identity: null`, `sign()` logs
"skipped macOS code signing" and returns *before doing anything* — and
`notarizeIfProvided()` is called from the **end of `sign()`**, so `notarize:
true` alongside `identity: null` would never have run either. That combination
fails silently and looks configured.

**`hardenedRuntime` / `entitlements` / `entitlementsInherit` / `notarize` are
flat `mac:` siblings, NOT nested under a `sign:` key.** In v25 `mac.sign` is
the slot for a custom sign *function* (`CustomMacSign | string | null`); the
nested-object shape some documentation shows is a v27+ schema. Getting this
wrong does not fail loudly: with no identity present `sign()` returns before
ever touching a misconfigured `sign:` value, so a broken nested config looks
fine on every machine without Apple secrets, and throws
`customSign is not a function` the moment a real identity is found — i.e.
exactly when it is meant to start working.

**`notarize: true` is safe to leave on unconditionally.** With no credentials
in the environment `getNotarizeOptions()` returns undefined and the build logs
`skipped macOS notarization` as a *warning*, not an error. But a **partial**
set throws: `APPLE_API_KEY`, `APPLE_API_KEY_ID` and `APPLE_API_ISSUER` must be
all-present or all-absent — which is why both the workflow and
`with-apple-secrets.sh` write them together.

One detail worth not rediscovering: **`APPLE_API_KEY` is a filesystem PATH to
the `.p8`, not the key's contents.** app-builder-lib passes it untouched to
`@electron/notarize`, which splices it into `xcrun notarytool --key <value>`.
Some electron-builder documentation shows a base64 body; for the pinned
version, the code is what counts.

`CSC_KEYCHAIN`/`CSC_NAME` are produced by a step gated on
`runner.os == 'macOS'` and are not placed on the matrix job or the shared
Package step. Keep that boundary: electron-builder's CSC variables also drive
the **Windows** Authenticode path, so leaking Apple signing configuration
across the matrix can surface as a Windows signing failure with nothing
"Apple" in its message.

## The entitlements

[`app/build/entitlements.mac.plist`](../app/build/entitlements.mac.plist) and
`entitlements.mac.inherit.plist` grant `com.apple.security.cs.allow-jit` and
`com.apple.security.cs.allow-unsigned-executable-memory` — what V8/Node need to
keep running under Hardened Runtime, and nothing else.

Patchbay spends its life launching other programs, so the reflex is to add
entitlements for that. None of it needs any:

- **Spawning `uv`, `clang`, `codesign`, `open` and `run-bridge.sh`.** Hardened
  Runtime constrains what is loaded *into* a process, not what it `exec`s. Each
  child gets its own signature and its own rules — which is also why the
  CPython that uv downloads can import unsigned wheels' `.so` files: that
  happens in the python process, not in ours.
- **`DYLD_INSERT_LIBRARIES` for bridge mode.** `run-bridge.sh` sets it for the
  instrumented Cortex Control copy, and it is *that copy's* signature — ad-hoc,
  Hardened Runtime off, `allow-dyld-environment-variables` — that decides
  whether dyld honours it. Patchbay does not need
  `allow-dyld-environment-variables`; that entitlement is about `DYLD_*` in our
  own launch environment, which nothing sets.
- **Network access.** uv downloads CPython over the network. Hardened Runtime
  does not gate the network — App Sandbox does.

Deliberately absent, and wrong to add by analogy with an App Sandbox guide:

- **No App Sandbox entitlements.** Those belong to the `mas` target; this is
  `dmg`, direct distribution. Copying them here would sandbox an app never
  built for it.
- **No `disable-library-validation`.** Patchbay declares no runtime
  `dependencies` and sets `npmRebuild: false` — nothing native is loaded into
  the main process. If a native addon ever appears, sign it with the same team
  rather than weakening this.

## The vendored `uv` is inside the signature

This is the part of the bundle most likely to fail notarization, and the part
that has no equivalent in a plain Electron app. `afterPack.cjs` copies the
arch-matching `uv` into `Contents/Resources/uv/` — a third-party prebuilt
Mach-O binary that Apple's notary service will insist is signed with the same
Developer ID and carries a secure timestamp.

It is covered, for two reasons that both have to hold:

- `afterPack` runs **before** signing (`platformPackager.js` calls
  `info.afterPack()` and then `signApp()`), so the binary is in place when the
  signer walks the bundle. This ordering is also why the ad-hoc branch of
  `afterPack.cjs` skips itself when a real identity is configured: its
  signature would only be overwritten moments later.
- `@electron/osx-sign` walks all of `Contents/` and signs everything
  `isbinaryfile` flags, `Contents/Resources` included — not just Frameworks and
  Helpers.

`uv` receives `entitlements.mac.inherit.plist`, like the Electron helpers: for
non-root, non-LoginItems paths `getOptionsForFile()` hands out the inherit
entitlements.

A notary rejection names the failing path ("not signed with a valid Developer
ID certificate" / "missing a secure timestamp"), and electron-builder puts that
output in the job log. `Contents/Resources/uv/uv` is the first place to look.

Bumping the pinned uv version in `scripts/fetch-uv.mjs` means a new binary
inside a signed bundle: the script pins the sha256 of each archive for that
reason, and both must be bumped together.

## Verifying a signed build the way a user experiences it

A green build is not the test, and neither is the notarization log. What
matters is Gatekeeper's verdict on a downloaded, quarantined artifact:

```bash
D=Patchbay-<version>-mac-arm64.dmg
# Flag it the way Safari would; a file built locally has no quarantine bit,
# so without this you are testing a path no downloader takes.
xattr -w com.apple.quarantine "0081;$(printf %x $(date +%s));Safari;$(uuidgen)" "$D"
MP=$(hdiutil attach "$D" -nobrowse -readonly | grep -o '/Volumes/.*' | head -1)
codesign --verify --deep --strict --verbose=2 "$MP"/*.app
xcrun stapler validate "$MP"/*.app   # ticket is stapled => works offline
spctl -a -t exec -vvv "$MP"/*.app    # want: accepted / source=Notarized Developer ID
hdiutil detach "$MP"
```

In the CI log, the corresponding evidence is `1 identity imported`, then
`signing … identityName=Developer ID Application: …`, then
`notarization successful` — once per architecture.

### The dmg itself is not signed, and that is correct

`spctl -a -t open --context context:primary-signature` on the **dmg** reports
`rejected / no usable signature`, and `syspolicy_check distribution` calls it
*Fatal*. Neither is a defect and neither is worth fixing. electron-builder
notarizes and staples the `.app`, then builds the dmg **around** the stapled
app, so the container carries no ticket of its own. What decides whether the
app opens is the assessment of the `.app`, which passes. `dmg.sign` is `false`
by default in this version and should stay that way.

The SingZ work measured this against shipping software on the same Mac: of six
dmgs checked — KeePassXC, AnyDesk, REAPER, ktalk and another electron-builder
app among them — five fail the identical check. Only ChatGPT's notarizes the
container. A day was lost there treating it as a bug; it is documented here so
it is not lost twice.

## What signature checks do not prove

A valid signature and a stapled ticket say the bundle is *acceptable*. They do
not say Patchbay still **works** — and Patchbay's whole job is the class of
thing Hardened Runtime is designed to interfere with. Release acceptance
therefore also requires launching a real Developer ID-signed, notarized build
and getting through Setup:

1. **Setup's probes go green** — in particular the ones that run the bundled
   `uv`, have it fetch CPython, and build the `.venv`. That exercises the
   signed `uv` under Hardened Runtime through the real app process, which no
   signature check does.
2. **The daemon starts and a client attaches** — Connect reaches the Quad
   Cortex and a preset reads back.
3. **Bridge mode still injects** — the instrumented Cortex Control copy is
   built and launched *from the signed app*, and shares its session. This is
   the step whose entitlement reasoning above is a prediction until it is run.

Direct mode alone is not sufficient evidence: it never touches the interposer.

## Verification status

**Signed, notarized and stapled — verified locally, end to end.** Not yet run
on CI, and the runtime gate below is still outstanding.

A local `--mac dmg:arm64` build (2026-08-30) produced a Developer ID-signed,
notarized, stapled app. Checked the way a downloader experiences it rather
than by trusting the build log — the dmg was flagged with quarantine the way
Safari does, mounted, and put to Gatekeeper:

- `codesign -dvv`: `Authority=Developer ID Application: Alexey Tarasov
  (USJ7H3X44X)` -> `Developer ID Certification Authority` -> `Apple Root CA`,
  `flags=0x10000(runtime)`, `TeamIdentifier=USJ7H3X44X`, secure timestamp.
- `codesign --verify --deep --strict`: passes.
- `xcrun stapler validate`: `The validate action worked!` — the ticket is
  attached, so it holds offline.
- `spctl -a -t exec -vvv`: `accepted`, `source=Notarized Developer ID` — both
  on the app and on the app inside the quarantined, mounted dmg.
- The **vendored `uv`** carries the same Developer ID authority and
  `flags=0x10000(runtime)`. This was previously an argument from how the
  signer walks the bundle; it is now an observation. Apple's notary service
  accepted it without comment.
- The dmg **container** reports `rejected / no usable signature`, which is
  correct and expected — see
  [The dmg itself is not signed, and that is correct](#the-dmg-itself-is-not-signed-and-that-is-correct).

The supporting pieces, each verified separately:

- **The credential pipeline**, against the real service:
  `scripts/with-apple-secrets.sh` reading SingZ's store, then
  `xcrun notarytool history` -> `Successfully received submission history`.
  That exercises the store path, the age key, the JSON parse of the multi-line
  `.p8`, the mode-600 temp file and the `APPLE_API_KEY`-is-a-path contract.
- **The `.p12` import**: `mac_p12_password` out of the same store gives
  `1 identity imported` — the line CI emits — and a usable
  `Developer ID Application` identity with no prompt.
- **`afterPack.cjs` steps aside** when a real identity is configured:
  `real signing identity configured — skipping ad-hoc sign`, so no ~200 MB
  deep-sign is thrown away.
- **The unsigned path still works**, which is what forks and certificate-less
  machines get: with `CSC_IDENTITY_AUTO_DISCOVERY=false`,
  `ad-hoc signed Patchbay.app` -> `skipped macOS application code signing` ->
  dmg, `--deep --strict` clean, `flags=0x2(adhoc)`, and `notarize: true` never
  reached. That `--strict` pass also rules out the dangling-symlink failure
  SingZ hit packaging from a git worktree — this build *was* made from one.
- **The release workflow's signing step**, against fixtures and without
  secrets: `set_if_present` survives `bash -e` on an empty value (and the
  `[ -n ... ] && ...` form it replaced is confirmed to abort, so that comment
  is a live regression guard), and the identity `sed` picks the Developer ID
  line out of a mixed listing, strips to the bare `CSC_NAME` that
  `checkPrefix()` demands, and returns empty — tripping the explicit error —
  for an Apple Distribution `.p12` or an empty keychain. 8/8.
- The v25.1.8 behaviours this configuration depends on — `identity: null`
  short-circuiting `sign()`, `notarizeIfProvided()` living at the end of
  `sign()`, `mac.sign` being a function slot, `notarize` warning rather than
  failing without credentials and throwing on a partial set, `CSC_KEYCHAIN`
  only being read when `CSC_LINK` is unset, `checkPrefix()` requiring a bare
  `CSC_NAME`, the `set-key-partition-list` password bug, and `afterPack`
  running before `signApp` — were each read out of the installed
  `app/node_modules/app-builder-lib`, not taken from SingZ's v26 notes or the
  online documentation.

Still outstanding:

- **A CI run.** The five repository secrets are not set yet, and no tagged
  release has exercised the workflow. The step's logic is fixture-tested but
  has not run against a real runner.
- **The x64 half.** Only `dmg:arm64` was built and notarized locally;
  `npm run dist:mac` also produces x64, which notarizes separately.
- **The runtime gate above**, on a signed build, against real hardware. A
  valid signature says the bundle is acceptable, not that Patchbay still
  works — and Hardened Runtime is designed to interfere with exactly what
  Patchbay does.

Until a tagged release has done all of it, the release notes should keep
telling macOS users to right-click -> Open.
