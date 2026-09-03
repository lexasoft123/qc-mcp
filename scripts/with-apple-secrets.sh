#!/usr/bin/env bash
#
# Run a command with the Apple notarization credentials from the SOPS store in
# the environment. The plaintext never touches a file that outlives the
# command, and never appears in the shell history or a process argument list.
#
# electron-builder runs from app/, and this script does NOT change directory
# for you — cd inside the command:
#
#   scripts/with-apple-secrets.sh bash -c 'cd app && npm run dist:mac'
#
# Exports APPLE_API_KEY (a mode-600 temp .p8 PATH), APPLE_API_KEY_ID,
# APPLE_API_ISSUER and APPLE_TEAM_ID — the four electron-builder's `notarize:
# true` reads. The signing IDENTITY is not handled here. electron-builder will
# auto-discover the Developer ID certificate sitting in the login keychain, but
# that path PROMPTS for the login password and fails as errSecInternalComponent
# if you decline — prepare a dedicated keychain instead. See
# docs/MACOS-SIGNING.md.
#
# APPLE_API_KEY is a filesystem PATH, not the key's contents: app-builder-lib
# passes it untouched to @electron/notarize, which splices it into
# `xcrun notarytool --key <value>`. Handing it a base64 body makes notarytool
# look for a file named by a 250-character blob.
#
# All three of APPLE_API_KEY / APPLE_API_KEY_ID / APPLE_API_ISSUER are set
# together or not at all — electron-builder THROWS on a partial set and only
# skips politely when all three are absent.
#
# Also exports PATCHBAY_MAC_P12_PASSWORD when the store carries
# `mac_p12_password` — the Developer ID .p12's export password, needed only to
# prepare a local signing keychain (docs/MACOS-SIGNING.md). It is deliberately
# NOT called CSC_KEY_PASSWORD: afterPack.cjs treats that name as "real signing
# is configured" and skips its ad-hoc fallback, so exporting it under Apple's
# name on a machine with no importable identity yields a bundle that is neither
# Developer ID-signed nor ad-hoc signed — the one macOS flatly refuses to open.
#
# CI does not use this: it has the same values as repository secrets.
# The store itself belongs to SingZ (override with QC_SOPS_STORE); its age key
# is the one already at ~/.config/sops/age/keys.txt.
set -euo pipefail

if [ $# -eq 0 ]; then
  echo "usage: $(basename "$0") <command> [args...]" >&2
  exit 64
fi

command -v sops >/dev/null || { echo "sops is not installed (brew install sops)" >&2; exit 1; }

# There is deliberately NO store in this repo. For macOS Developer ID nothing
# is per-app — no App ID, no bundle registration, no provisioning profile — so
# one team has exactly one certificate and one App Store Connect key, and a
# second copy of them would be a second thing to rotate and leak. The store
# lives with SingZ, which created it; this repo reads it.
store="${QC_SOPS_STORE:-$HOME/Dev/my/SingZ/.keys/secrets.enc.yaml}"
if [ ! -f "$store" ]; then
  echo "no SOPS store at $store" >&2
  echo "set QC_SOPS_STORE to point at it — see docs/MACOS-SIGNING.md" >&2
  exit 1
fi

# sops does not reliably fall back to the default age key path (measured on 3.x:
# it reports only the SOPS_AGE_* and SSH locations and fails), so name it
# explicitly when the caller has not.
if [ -z "${SOPS_AGE_KEY_FILE:-}" ] && [ -f "$HOME/.config/sops/age/keys.txt" ]; then
  export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
fi

umask 077
tmpdir=$(mktemp -d)
# Not `exec` below, precisely so this still runs.
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT INT TERM HUP

# --config /dev/null: the creation rules are for `sops -e`, and letting sops
# hunt for a .sops.yaml relative to the CWD makes DECRYPTION fail in ways that
# depend on where you happened to be standing.
plain=$(sops --config /dev/null -d --output-type json "$store")

# Parse with node rather than grep/sed: the .p8 is a multi-line PEM, and a
# line-oriented parse of it is how you get a key that is subtly truncated and
# an auth error three steps later that says nothing about parsing.
eval "$(
  printf '%s' "$plain" | node -e '
    let raw = ""
    process.stdin.on("data", (d) => (raw += d))
    process.stdin.on("end", () => {
      const s = JSON.parse(raw)
      const need = ["asc_key_id", "asc_issuer_id", "asc_key_p8"]
      const missing = need.filter((k) => !s[k])
      if (missing.length) {
        console.error(`sops store is missing: ${missing.join(", ")}`)
        process.exit(1)
      }
      const q = (v) => "'"'"'" + String(v).replace(/'"'"'/g, "'"'"'\\'"'"''"'"'") + "'"'"'"
      console.log(`QC_ASC_KEY_ID=${q(s.asc_key_id)}`)
      console.log(`QC_ASC_ISSUER_ID=${q(s.asc_issuer_id)}`)
      console.log(`QC_TEAM_ID=${q(s.team_id || "USJ7H3X44X")}`)
      // Optional: older stores predate it, and every use except preparing a
      // local signing keychain runs fine without it.
      if (s.mac_p12_password) console.log(`QC_MAC_P12_PASSWORD=${q(s.mac_p12_password)}`)
      console.log(`QC_ASC_P8=${q(s.asc_key_p8)}`)
    })
  '
)"

printf '%s' "$QC_ASC_P8" > "$tmpdir/AuthKey_${QC_ASC_KEY_ID}.p8"
unset QC_ASC_P8

export APPLE_API_KEY="$tmpdir/AuthKey_${QC_ASC_KEY_ID}.p8"
export APPLE_API_KEY_ID="$QC_ASC_KEY_ID"
export APPLE_API_ISSUER="$QC_ASC_ISSUER_ID"
export APPLE_TEAM_ID="$QC_TEAM_ID"
unset QC_ASC_KEY_ID QC_ASC_ISSUER_ID QC_TEAM_ID

# The eval above only SETS this as a shell variable; without an explicit export
# the child process — the entire point of this script — never sees it.
if [ -n "${QC_MAC_P12_PASSWORD:-}" ]; then
  export PATCHBAY_MAC_P12_PASSWORD="$QC_MAC_P12_PASSWORD"
  unset QC_MAC_P12_PASSWORD
fi

set +e
"$@"
rc=$?
set -e
exit $rc
