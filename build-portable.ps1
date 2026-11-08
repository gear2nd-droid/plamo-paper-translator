$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $AppRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "先に setup.ps1 を実行してください。" }

& $Python -m pip install --disable-pip-version-check "pyinstaller>=6.16,<7"
Push-Location $AppRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --name PLaMoPaperTranslator --hidden-import pypdf --add-data "static;static" app.py
    $PackageRoot = Join-Path $AppRoot "dist\PLaMoPaperTranslator"
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
    Copy-Item -Force "dist\PLaMoPaperTranslator.exe" $PackageRoot
    Copy-Item -Force "Start-PLaMoPaperTranslator.cmd","start.ps1","setup.ps1","requirements.txt","README.md","DISTRIBUTION.md","THIRD_PARTY_NOTICES.md" $PackageRoot
    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "models"),(Join-Path $PackageRoot "runtime"),(Join-Path $PackageRoot "data") | Out-Null
    $Zip = Join-Path $AppRoot "dist\PLaMoPaperTranslator-Windows-portable.zip"
    if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
    Compress-Archive -Path "$PackageRoot\*" -DestinationPath $Zip -CompressionLevel Optimal
    Write-Host "作成完了: $Zip" -ForegroundColor Green
} finally {
    Pop-Location
}
