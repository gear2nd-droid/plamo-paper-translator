$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Executable = Join-Path $AppRoot "PLaMoPaperTranslator.exe"
$Python = Join-Path $AppRoot ".venv\Scripts\python.exe"
$App = Join-Path $AppRoot "app.py"

if (Test-Path $Executable) {
    & $Executable
    exit $LASTEXITCODE
}
if (-not (Test-Path $Python)) {
    Write-Host "初回セットアップが必要です。setup.ps1 を実行します。" -ForegroundColor Yellow
    & (Join-Path $AppRoot "setup.ps1")
}
if (-not (Test-Path $Python)) { throw "Python環境を準備できませんでした。" }
& $Python $App
