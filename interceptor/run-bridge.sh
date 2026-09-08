#!/bin/bash
# Launch the instrumented Cortex Control with the HID bridge enabled, so the MCP
# (in bridge mode) can share the app's live device session simultaneously.
#
# Prereq: interpose.dylib built and CortexControl-instrumented.app re-signed
# (see build.sh — only needed once; the dylib can be recompiled on its own).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/CortexControl-instrumented.app/Contents/MacOS/Cortex Control"
DYLIB="$HERE/interpose.dylib"

# Make sure NO Cortex Control is still holding the device — the stock app, and
# a previous instrumented instance too. This used to kill only the stock app and
# sleep one second, which is neither the whole set nor long enough: a launch on
# top of an instance that has been signalled but has not yet released the USB
# endpoint brings the new app up against a device it cannot own, and it
# segfaults about nine seconds in (EXC_BAD_ACCESS, JUCE message thread).
pkill -f "Applications/Neural DSP/Cortex Control.app" 2>/dev/null || true
pkill -f "CortexControl-instrumented.app" 2>/dev/null || true
for _ in $(seq 1 40); do
  pgrep -f "Contents/MacOS/Cortex Control" >/dev/null 2>&1 || break
  sleep 0.5
done
sleep 1   # and a moment more for the USB endpoint to come free

echo "Launching instrumented Cortex Control (bridge FIFOs: /tmp/qc_inject, /tmp/qc_in)"
echo "Frame log: $HERE/hid_log.txt (QC_VERBOSE=1 — needed by tools/gui correlation)"
QC_INJECT=/tmp/qc_inject QC_OUT=/tmp/qc_in \
  QC_VERBOSE=1 QC_LOG="$HERE/hid_log.txt" \
  DYLD_INSERT_LIBRARIES="$DYLIB" "$APP" "$@"
