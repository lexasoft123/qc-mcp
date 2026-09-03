# Build the Windows interposer (qcinject.dll) and its launcher (qclaunch.exe).
# Needs Visual Studio Build Tools with the C++ workload; finds vcvars64 itself.
#
#   .\build.ps1
#
# The macOS twin is ../interceptor/build.sh. Unlike it, nothing here re-signs or
# copies the app: the DLL is injected into the stock installation at launch.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vcvars = $null
if (Test-Path $vswhere) {
    $root = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($root) { $vcvars = Join-Path $root "VC\Auxiliary\Build\vcvars64.bat" }
}
if (-not ($vcvars -and (Test-Path $vcvars))) {
    $vcvars = Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\*\VC\Auxiliary\Build\vcvars64.bat" -EA SilentlyContinue |
              Select-Object -First 1 -Expand FullName
}
if (-not $vcvars) { throw "vcvars64.bat not found - install VS Build Tools with the C++ workload" }
Write-Host "==> using $vcvars"

# cl must run inside a vcvars environment, so drive it through one cmd session.
$build = @"
call "$vcvars" >nul
cl /nologo /W3 /O2 /LD qcinject.c /Fe:qcinject.dll /link /OUT:qcinject.dll || exit /b 1
cl /nologo /W3 /O2 qclaunch.c /Fe:qclaunch.exe || exit /b 1
"@
$bat = Join-Path $env:TEMP "qc_build.bat"
Set-Content -Path $bat -Value $build -Encoding ASCII
& cmd /c $bat
if ($LASTEXITCODE -ne 0) { throw "build failed ($LASTEXITCODE)" }
Remove-Item *.obj -EA SilentlyContinue

foreach ($f in "qcinject.dll", "qclaunch.exe") {
    $i = Get-Item $f
    Write-Host ("==> {0}  {1:N0} bytes" -f $i.Name, $i.Length)
}
Write-Host @"

Built. Launch the instrumented app with:
  .\run-bridge.ps1
Then connect the MCP in bridge mode. Logs: set QC_LOG / QC_VERBOSE=1 first.
"@
