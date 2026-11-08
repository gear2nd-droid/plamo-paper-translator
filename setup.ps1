param(
    [ValidateSet("8b", "2b")]
    [string]$ModelSize = "8b",
    [switch]$RuntimeOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path $AppRoot "runtime\llama"
$ModelRoot = Join-Path $AppRoot "models"
$VenvPython = Join-Path $AppRoot ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "PLaMo Paper Translator - 初回セットアップ" -ForegroundColor Green
Write-Host "================================================"
Write-Host "PLaMo 3 Translate のモデルは PLaMo Community License で配布されています。"
Write-Host "個人利用・非商用利用・学術利用は無償です。組織内利用は商用利用に含まれ、"
Write-Host "直近年度の売上高が10億円以下の場合も事前登録が必要です。10億円超は別契約です。"
Write-Host ""
Write-Host "日本語ライセンス: https://plamo.preferredai.jp/info/plamo-community-license-ja"
Write-Host "モデル配布元:     https://huggingface.co/pfnet/plamo-3-translate-$ModelSize-GGUF"
Write-Host ""
$Consent = Read-Host "条件を確認し、同意して公式配布元からモデルを取得しますか？ [yes/no]"
if ($Consent -notin @("yes", "YES", "Yes", "y", "Y")) {
    Write-Host "セットアップを中止しました。モデルはダウンロードしていません。" -ForegroundColor Yellow
    exit 2
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $ModelRoot | Out-Null

if (-not (Test-Path $VenvPython)) {
    Write-Host "[1/4] Python環境を作成しています..."
    python -m venv (Join-Path $AppRoot ".venv")
}
Write-Host "[2/4] PDF処理ライブラリを準備しています..."
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $AppRoot "requirements.txt")

function Get-RemoteFile {
    param([string]$Url, [string]$Destination)
    if (Test-Path $Destination) {
        Write-Host "  既存ファイルを使用: $(Split-Path -Leaf $Destination)"
        return
    }
    $Partial = "$Destination.partial"
    Write-Host "  ダウンロード: $(Split-Path -Leaf $Destination)"
    & curl.exe -L --fail --retry 4 --retry-delay 3 -C - -o $Partial $Url
    if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $Url" }
    Move-Item -Force -LiteralPath $Partial -Destination $Destination
}

Write-Host "[3/4] llama.cpp を準備しています..."
$Release = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" -Headers @{"User-Agent"="PLaMo-Paper-Translator"}
$HasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($HasNvidia) {
    $MainAsset = $Release.assets | Where-Object { $_.name -match '^llama-.+-bin-win-cuda-12\.4-x64\.zip$' } | Select-Object -First 1
    $CudaAsset = $Release.assets | Where-Object { $_.name -match '^cudart-llama-bin-win-cuda-12\.4-x64\.zip$' } | Select-Object -First 1
    if (-not $MainAsset -or -not $CudaAsset) { throw "最新の llama.cpp リリースに CUDA 12.4 Windows 版がありません。" }
    $Assets = @($MainAsset, $CudaAsset)
    Write-Host "  NVIDIA GPUを検出しました。CUDA版を使用します。"
} else {
    $MainAsset = $Release.assets | Where-Object { $_.name -match '^llama-.+-bin-win-vulkan-x64\.zip$' } | Select-Object -First 1
    if (-not $MainAsset) { throw "最新の llama.cpp リリースに Vulkan Windows 版がありません。" }
    $Assets = @($MainAsset)
    Write-Host "  NVIDIA GPUが見つからないためVulkan版を使用します。"
}
foreach ($Asset in $Assets) {
    $Archive = Join-Path $RuntimeRoot $Asset.name
    Get-RemoteFile -Url $Asset.browser_download_url -Destination $Archive
    Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeRoot -Force
    Remove-Item -LiteralPath $Archive -Force
}
if (-not (Get-ChildItem $RuntimeRoot -Filter "llama-server.exe" -Recurse)) { throw "llama-server.exe を展開できませんでした。" }

if (-not $RuntimeOnly) {
    Write-Host "[4/4] PLaMo 3 Translate $ModelSize を準備しています..."
    if ($ModelSize -eq "8b") {
        $ModelFile = "plamo-3-translate-8b-Q4_K_M.gguf"
        $ExpectedBytes = 5280435168
    } else {
        $ModelFile = "plamo-3-translate-2b-Q4_K_M.gguf"
        $ExpectedBytes = 1807192480
    }
    $BaseUrl = "https://huggingface.co/pfnet/plamo-3-translate-$ModelSize-GGUF/resolve/main"
    $ModelPath = Join-Path $ModelRoot $ModelFile
    Get-RemoteFile -Url "$BaseUrl/$ModelFile?download=true" -Destination $ModelPath
    $ActualBytes = (Get-Item -LiteralPath $ModelPath).Length
    if ($ActualBytes -ne $ExpectedBytes) {
        throw "モデルのファイルサイズが公式値と一致しません（期待: $ExpectedBytes / 実際: $ActualBytes）。"
    }
    Get-RemoteFile -Url "$BaseUrl/chat_template.jinja?download=true" -Destination (Join-Path $ModelRoot "chat_template.jinja")
} else {
    Write-Host "[4/4] RuntimeOnly のためモデル取得を省略しました。"
}

Write-Host ""
Write-Host "セットアップが完了しました。Start-PLaMoPaperTranslator.cmd をダブルクリックしてください。" -ForegroundColor Green
