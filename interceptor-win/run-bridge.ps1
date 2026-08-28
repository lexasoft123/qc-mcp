# Start Cortex Control with the interposer loaded, so the MCP can share its session.
# The macOS twin is ../interceptor/run-bridge.sh.
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:QC_LOG) { $env:QC_LOG = Join-Path $Here "hid_log.txt" }
if (-not $env:QC_VERBOSE) { $env:QC_VERBOSE = "1" }   # the GUI tools want the frame log
& (Join-Path $Here "qclaunch.exe") @args
