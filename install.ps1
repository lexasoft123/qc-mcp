# One-shot setup for Windows: venv + qc-mcp package + Claude Code registration.
# (install.sh is the macOS/Linux twin.)
#
#   .\install.ps1            # user scope (default): 'quad-cortex' available in
#                            # EVERY Claude Code session, any folder
#   .\install.ps1 -Local     # this-folder-only registration instead
#
# Idempotent - safe to re-run (also after moving the repo: paths re-register).
#
# If PowerShell refuses to run this ("running scripts is disabled"), either
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# or allow local scripts once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
[CmdletBinding()]
param([switch]$Local)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$Scope = if ($Local) { "local" } else { "user" }
$Python = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$VenvPy = Join-Path $Here ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPy)) {
    Write-Host "==> Creating venv"
    # 'py -3' is the Windows launcher; fall back to whatever 'python' is on PATH.
    if (Get-Command $Python -ErrorAction SilentlyContinue) {
        & $Python -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
}
if (-not (Test-Path $VenvPy)) { throw "venv creation failed: $VenvPy missing" }

Write-Host "==> Installing qc-mcp (editable)"
# No [gui] extra here: the GUI verification harness drives Cortex Control through
# macOS screencapture + the accessibility API, so it is macOS-only.
& $VenvPy -m pip install -q --upgrade pip
& $VenvPy -m pip install -q -e .

$Bin = Join-Path $Here ".venv\Scripts\qc-mcp.exe"
if (-not (Test-Path $Bin)) { throw "install failed: $Bin missing" }

$registered = $false
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Write-Host "==> Registering with Claude Code (scope: $Scope)"
    # Drop stale user/local registrations, then add fresh. Do NOT touch project
    # scope - that's the repo's committed .mcp.json (removing rewrites the file).
    foreach ($s in @("user", "local")) {
        claude mcp remove quad-cortex -s $s 2>$null | Out-Null
    }
    claude mcp add --scope $Scope quad-cortex -- $Bin
    claude mcp list 2>$null | Select-String -Pattern "quad-cortex"
    $registered = $true
} else {
    Write-Host "claude CLI not found - register manually once it's installed:"
    Write-Host "  claude mcp add --scope user quad-cortex -- `"$Bin`""
}

$extra = if ($Scope -eq "user") { " - available in every folder" } else { "" }
$reg = if ($registered) { "'quad-cortex' is registered at $Scope scope$extra." }
       else { "Package installed; run the registration line above once the claude CLI is present." }
Write-Host @"

Done. $reg
Note: the repo's skills + CLAUDE.md knowledge still load only for sessions
opened INSIDE this repo; from other folders you get device control alone.

Windows can seize the device (direct mode, Cortex Control closed) or run
alongside the app on a shared handle - connect(mode='bridge') does the latter and
needs no setup. Heavy write work is safest in direct mode; see docs/WINDOWS.md.
The GUI verification harness is macOS-only.
Check the device is reachable with:
  .venv\Scripts\python.exe tools\win_hid_check.py
"@
