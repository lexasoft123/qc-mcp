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

# make sure the real app isn't already holding the device
pkill -f "Applications/Neural DSP/Cortex Control.app" 2>/dev/null || true
sleep 1

echo "Launching instrumented Cortex Control (bridge FIFOs: /tmp/qc_inject, /tmp/qc_in)"
echo "Frame log: $HERE/hid_log.txt (QC_VERBOSE=1 — needed by tools/gui correlation)"
QC_INJECT=/tmp/qc_inject QC_OUT=/tmp/qc_in \
  QC_VERBOSE=1 QC_LOG="$HERE/hid_log.txt" \
  DYLD_INSERT_LIBRARIES="$DYLIB" "$APP" "$@"
