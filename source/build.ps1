# Build the installer locally:  powershell -ExecutionPolicy Bypass -File build.ps1
#   Add -SkipPyInstaller to reuse the existing dist\ folder and only rebuild the installer.
# Needs: Python 3.12 (64-bit) and Inno Setup 6 or 7 (https://jrsoftware.org/isdl.php)
param([switch]$SkipPyInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Find a real Python. The bare "python" command is often Windows' Microsoft Store shortcut (which does
# nothing), so try the "py" launcher first, then "python".
function Find-Python {
    foreach ($candidate in @(@("py", "-3.12"), @("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        try {
            $out = & $exe @extra --version 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -and $out -match "Python 3\.\d+") { return , $candidate }
        } catch { }
    }
    throw "Python 3 was not found. Install Python 3.12 from https://www.python.org/downloads/ and tick 'Add python.exe to PATH' (and keep the 'py launcher' option), then open a NEW PowerShell window and run this again."
}

# Find Inno Setup's compiler (version 7 or 6, 64-bit or 32-bit, all-users or per-user install).
function Find-Iscc {
    if ($env:ISCC_PATH -and (Test-Path $env:ISCC_PATH)) { return $env:ISCC_PATH }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs") | Where-Object { $_ }
    foreach ($ver in @("7", "6")) {
        foreach ($root in $roots) {
            $path = Join-Path $root "Inno Setup $ver\ISCC.exe"
            if (Test-Path $path) { return $path }
        }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$py = Find-Python
$pyExe = $py[0]
$pyArgs = @($py | Select-Object -Skip 1)
Write-Host ("Using Python: " + ((& $pyExe @pyArgs --version 2>&1) | Out-String).Trim())

$version = ((& $pyExe @pyArgs -c "from tutoring_organiser.version import __version__; print(__version__)") | Out-String).Trim()
if (-not $version) { throw "Couldn't read the version from tutoring_organiser\version.py" }
Write-Host "Building version $version"

if (-not $SkipPyInstaller) {
    & $pyExe @pyArgs -m venv .venv-build
    $venvPython = ".\.venv-build\Scripts\python.exe"
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements-build.txt
    & $venvPython -m PyInstaller --noconfirm --clean tutoring_organiser.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (see the messages above)." }
} elseif (-not (Test-Path "dist\TutoringOrganiser\TutoringOrganiser.exe")) {
    throw "-SkipPyInstaller was used but dist\TutoringOrganiser\TutoringOrganiser.exe doesn't exist. Run without the flag first."
}

$iscc = Find-Iscc
if (-not $iscc) {
    throw "Inno Setup (version 6 or 7) not found. Install it from https://jrsoftware.org/isdl.php, or set `$env:ISCC_PATH to the full path of ISCC.exe."
}
Write-Host "Using Inno Setup: $iscc"
& $iscc "/DMyAppVersion=$version" installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (see the messages above)." }

$exe = "installer_output\TutoringOrganiser-Setup-$version.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  TutoringOrganiser-Setup-$version.exe" | Set-Content -Encoding ascii "$exe.sha256"
Write-Host ""
Write-Host "Built $exe"