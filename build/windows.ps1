# Build the Windows SaveSync application and installer.
# Usage: powershell -ExecutionPolicy Bypass -File build\windows.ps1 [-SkipInstaller] [-Version 1.2.0]

param(
    [switch]$SkipInstaller,
    [string]$Version = "1.2.0"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --disable-pip-version-check --upgrade cryptography pywebview pyinstaller

$inno = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $inno)) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $inno = $cmd.Source }
}
if (-not $SkipInstaller -and -not (Test-Path $inno)) {
    throw "Inno Setup 6 is required for the installer. Install it from https://jrsoftware.org/isdl.php, or pass -SkipInstaller."
}

python -m PyInstaller --noconfirm --clean --onedir --windowed --name SaveSync `
    --icon assets/icon.ico `
    --add-data "savesync/ui;savesync/ui" `
    SaveSync.pyw

if (-not $SkipInstaller) {
    & $inno "/DSaveSyncVersion=$Version" installer\SaveSync.iss | Write-Host
    if ($LASTEXITCODE) { throw "Inno Setup compilation failed." }
    Write-Host "Installer written to dist\SaveSync-Setup.exe"
} else {
    Write-Host "Application written to dist\SaveSync\SaveSync.exe"
}
