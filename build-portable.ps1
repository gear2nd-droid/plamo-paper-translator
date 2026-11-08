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
    Copy-Item -Force "Start-PLaMoPaperTranslator.cmd","start.ps1","setup.ps1","requirements.txt","README.md","DISTRIBUTION.md","THIRD_PARTY_NOTICES.md","LICENSE" $PackageRoot
    $LicenseRoot = Join-Path $PackageRoot "licenses"
    New-Item -ItemType Directory -Force -Path $LicenseRoot | Out-Null

    $PythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
    $PythonLicense = Join-Path $PythonBase "LICENSE.txt"
    if (-not (Test-Path -LiteralPath $PythonLicense)) { throw "Pythonのライセンス本文が見つかりません: $PythonLicense" }
    Copy-Item -Force -LiteralPath $PythonLicense -Destination (Join-Path $LicenseRoot "Python-LICENSE.txt")

    function Copy-PackageLicense {
        param([string]$Distribution, [string]$DestinationName)
        $Finder = 'import sys; from importlib.metadata import distribution; d=distribution(sys.argv[1]); files=[f for f in (d.files or []) if "license" in str(f).lower() or "copying" in str(f).lower()]; print(d.locate_file(files[0])) if files else sys.exit(1)'
        $Source = (& $Python -c $Finder $Distribution).Trim()
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Source)) {
            throw "$Distribution のライセンス本文が見つかりません。"
        }
        Copy-Item -Force -LiteralPath $Source -Destination (Join-Path $LicenseRoot $DestinationName)
    }

    Copy-PackageLicense -Distribution "pypdf" -DestinationName "pypdf-LICENSE.txt"
    Copy-PackageLicense -Distribution "Pillow" -DestinationName "Pillow-LICENSE.txt"
    Copy-PackageLicense -Distribution "pyinstaller" -DestinationName "PyInstaller-COPYING.txt"
    $PdfiumFinder = 'import sys; from pathlib import Path; from importlib.metadata import distribution; d=distribution(sys.argv[1]); p=next(Path(d.locate_file(f)) for f in (d.files or []) if "build_licenses" in str(f).lower()); print(next(x for x in (p, *p.parents) if x.name.lower() == "licenses"))'
    $PdfiumLicenseSource = (& $Python -c $PdfiumFinder "pypdfium2").Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PdfiumLicenseSource)) {
        throw "pypdfium2/PDFium のライセンス一式が見つかりません。"
    }
    $PdfiumLicenseDestination = Join-Path $LicenseRoot "pypdfium2"
    $ResolvedLicenseRoot = [IO.Path]::GetFullPath($LicenseRoot) + [IO.Path]::DirectorySeparatorChar
    $ResolvedPdfiumLicenseDestination = [IO.Path]::GetFullPath($PdfiumLicenseDestination)
    if (-not $ResolvedPdfiumLicenseDestination.StartsWith($ResolvedLicenseRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "PDFiumライセンスの出力先が配布用licensesフォルダー外です。"
    }
    if (Test-Path -LiteralPath $ResolvedPdfiumLicenseDestination) {
        Remove-Item -Recurse -Force -LiteralPath $ResolvedPdfiumLicenseDestination
    }
    New-Item -ItemType Directory -Force -Path $PdfiumLicenseDestination | Out-Null
    Copy-Item -Recurse -Force -Path (Join-Path $PdfiumLicenseSource "*") -Destination $PdfiumLicenseDestination
    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "models"),(Join-Path $PackageRoot "runtime"),(Join-Path $PackageRoot "data") | Out-Null
    $Zip = Join-Path $AppRoot "dist\PLaMoPaperTranslator-Windows-portable.zip"
    if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
    Compress-Archive -Path "$PackageRoot\*" -DestinationPath $Zip -CompressionLevel Optimal
    Write-Host "作成完了: $Zip" -ForegroundColor Green
} finally {
    Pop-Location
}
