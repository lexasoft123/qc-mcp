#!/bin/bash
# Build an instrumented copy of Cortex Control with the HID interposer, so the
# MCP can capture traffic and/or share the app's device session (bridge mode).
#
# What it does:
#   1. compile interpose.dylib (arm64)
#   2. copy Cortex Control.app -> CortexControl-instrumented.app
#   3. write merged entitlements (original + injection allowances)
#   4. re-sign the dylib and the app ad-hoc WITHOUT hardened runtime, so
#      DYLD_INSERT_LIBRARIES + an unsigned dylib are allowed
#   5. verify the result
#
# Re-run any time. To only rebuild the dylib (the app copy is unchanged), use
# `build.sh --dylib-only` — much faster and avoids re-signing the app.
#
# Env overrides: SRC_APP, OUT_APP.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_APP="${SRC_APP:-/Applications/Neural DSP/Cortex Control.app}"
OUT_APP="${OUT_APP:-$HERE/CortexControl-instrumented.app}"
DYLIB="$HERE/interpose.dylib"
ENT="$HERE/entitlements.plist"
DYLIB_ONLY=0
[ "${1:-}" = "--dylib-only" ] && DYLIB_ONLY=1

fail() { echo "error: $*" >&2; exit 1; }

command -v clang    >/dev/null || fail "clang not found (install Xcode command line tools)"
command -v codesign >/dev/null || fail "codesign not found"
[ -d "$SRC_APP" ]   || fail "Cortex Control not found at: $SRC_APP"

ARCH="$(uname -m)"; [ "$ARCH" = "arm64" ] || ARCH="x86_64"

echo "[1/5] compiling interpose.dylib ($ARCH)"
clang -arch "$ARCH" -dynamiclib -O2 \
  -framework IOKit -framework CoreFoundation \
  -o "$DYLIB" "$HERE/interpose.c"
codesign -f -s - "$DYLIB"

if [ "$DYLIB_ONLY" = 1 ]; then
  [ -d "$OUT_APP" ] || fail "--dylib-only but $OUT_APP doesn't exist; run a full build first"
  echo "dylib rebuilt; existing instrumented app reused."
  echo "done."
  exit 0
fi

echo "[2/5] copying app -> $OUT_APP"
rm -rf "$OUT_APP"
cp -R "$SRC_APP" "$OUT_APP"

echo "[3/5] writing entitlements"
cat > "$ENT" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <!-- from the original app -->
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.device.audio-input</key><true/>
  <!-- needed so DYLD_INSERT_LIBRARIES loads our (ad-hoc) dylib -->
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
  <key>com.apple.security.get-task-allow</key><true/>
</dict>
</plist>
PLIST

echo "[4/5] re-signing app (ad-hoc, no hardened runtime)"
# sign the main executable then the bundle; NO '--options runtime' => hardened
# runtime disabled => DYLD injection permitted.
codesign -f -s - --entitlements "$ENT" "$OUT_APP/Contents/MacOS/Cortex Control"
codesign -f -s - --entitlements "$ENT" "$OUT_APP"

echo "[5/5] verifying"
FLAGS="$(codesign -dvvv "$OUT_APP" 2>&1 | grep -i '^CodeDirectory' | grep -oi 'flags=[^ ]*' || true)"
echo "  app codesign $FLAGS"
echo "$FLAGS" | grep -qi runtime && fail "hardened runtime still set — injection would be blocked"
codesign -d --entitlements :- "$OUT_APP" 2>/dev/null | tr -d '\0' \
  | grep -q disable-library-validation || fail "library-validation entitlement missing"
codesign -v "$DYLIB" >/dev/null 2>&1 || fail "dylib signature invalid"
echo "  ok: hardened runtime off, injection entitlements present, dylib signed"

cat <<EOF

Done. Instrumented app: $OUT_APP

Run with the HID bridge (Cortex Control + MCP simultaneously):
    $HERE/run-bridge.sh &
    # then use the quad-cortex MCP (it auto-detects the bridge)

Or capture traffic to a log (verbose):
    QC_VERBOSE=1 QC_LOG=$HERE/hid_log.txt \\
      DYLD_INSERT_LIBRARIES=$DYLIB \\
      "$OUT_APP/Contents/MacOS/Cortex Control"
EOF
