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
$LlamaServer = Get-ChildItem $RuntimeRoot -Filter "llama-server.exe" -Recurse | Select-Object -First 1
if ($LlamaServer) {
    Write-Host "  既存の llama.cpp を使用: $($LlamaServer.FullName)"
    & $LlamaServer.FullName --version
    if ($LASTEXITCODE -ne 0) { throw "既存の llama-server.exe を実行できません。runtime を確認してください。" }
} else {
    $Releases = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20" -Headers @{"User-Agent"="PLaMo-Paper-Translator"}
    $Release = $Releases | Where-Object {
        @($_.assets | Where-Object { $_.name -match '^llama-.+-bin-win-(?:cuda-12\.4|vulkan)-x64\.zip$' }).Count -gt 0
    } | Select-Object -First 1
    if (-not $Release) { throw "GPU対応Windows版を含む llama.cpp リリースが見つかりません。" }
    Write-Host "  使用する公式リリース: $($Release.tag_name)"
    $HasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($HasNvidia) {
        $MainAsset = $Release.assets | Where-Object { $_.name -match '^llama-.+-bin-win-cuda-12\.4-x64\.zip$' } | Select-Object -First 1
        $CudaAsset = $Release.assets | Where-Object { $_.name -match '^cudart-llama-bin-win-cuda-12\.4-x64\.zip$' } | Select-Object -First 1
        if (-not $MainAsset -or -not $CudaAsset) { throw "llama.cpp リリースに CUDA 12.4 Windows 版がありません。" }
        Write-Host "  NVIDIA GPUを検出しました。CUDA版を使用します。"
    } else {
        $MainAsset = $Release.assets | Where-Object { $_.name -match '^llama-.+-bin-win-vulkan-x64\.zip$' } | Select-Object -First 1
        if (-not $MainAsset) { throw "llama.cpp リリースに Vulkan Windows 版がありません。" }
        Write-Host "  NVIDIA GPUが見つからないためVulkan版を使用します。"
    }

    $MainArchive = Join-Path $RuntimeRoot $MainAsset.name
    Get-RemoteFile -Url $MainAsset.browser_download_url -Destination $MainArchive
    Expand-Archive -LiteralPath $MainArchive -DestinationPath $RuntimeRoot -Force
    Remove-Item -LiteralPath $MainArchive -Force

    if ($HasNvidia) {
        $CudaArchive = Join-Path $RuntimeRoot $CudaAsset.name
        try {
            Get-RemoteFile -Url $CudaAsset.browser_download_url -Destination $CudaArchive
            Expand-Archive -LiteralPath $CudaArchive -DestinationPath $RuntimeRoot -Force
            Remove-Item -LiteralPath $CudaArchive -Force
        } catch {
            $SystemCuda = (Get-Command cudart64_12.dll -ErrorAction SilentlyContinue) -and
                          (Get-Command cublas64_12.dll -ErrorAction SilentlyContinue) -and
                          (Get-Command cublasLt64_12.dll -ErrorAction SilentlyContinue)
            if ($SystemCuda) {
                Write-Warning "CUDAランタイムの取得に失敗しましたが、システムのCUDA 12 DLLを使用できます。"
            } else {
                throw
            }
        }
    }
    if (-not (Get-ChildItem $RuntimeRoot -Filter "llama-server.exe" -Recurse)) { throw "llama-server.exe を展開できませんでした。" }
}

if (-not $RuntimeOnly) {
    Write-Host "[4/4] PLaMo 3 Translate $ModelSize を準備しています..."
    if ($ModelSize -eq "8b") {
        $ModelFile = "plamo-3-translate-8b-Q4_K_M.gguf"
        $MinimumBytes = 4000000000
    } else {
        $ModelFile = "plamo-3-translate-2b-Q4_K_M.gguf"
        $MinimumBytes = 1000000000
    }
    $ModelRepository = "pfnet/plamo-3-translate-$ModelSize-GGUF"
    $BaseUrl = "https://huggingface.co/$ModelRepository/resolve/main"
    $ModelPath = Join-Path $ModelRoot $ModelFile
    Get-RemoteFile -Url "$BaseUrl/${ModelFile}?download=true" -Destination $ModelPath
    $ActualBytes = (Get-Item -LiteralPath $ModelPath).Length
    if ($ActualBytes -lt $MinimumBytes) {
        throw "モデルファイルが小さすぎます（最低: $MinimumBytes / 実際: $ActualBytes）。"
    }

    $Stream = [System.IO.File]::OpenRead($ModelPath)
    try {
        $Header = New-Object byte[] 4
        [void]$Stream.Read($Header, 0, 4)
        $Magic = [System.Text.Encoding]::ASCII.GetString($Header)
    } finally {
        $Stream.Dispose()
    }
    if ($Magic -ne "GGUF") { throw "モデルファイルのGGUFヘッダーを確認できません。" }

    $RemoteEntry = $null
    try {
        $TreeUrl = "https://huggingface.co/api/models/$ModelRepository/tree/main?recursive=false&expand=false"
        $RemoteTree = Invoke-RestMethod -Uri $TreeUrl
        $RemoteEntry = @($RemoteTree) | ForEach-Object { $_ } | Where-Object { $_.path -eq $ModelFile } | Select-Object -First 1
    } catch {
        Write-Warning "Hugging Face APIへ接続できないため、公式サイズとSHA-256の照合を省略します。"
    }
    if ($RemoteEntry) {
        if ($ActualBytes -ne [long]$RemoteEntry.size) {
            throw "モデルのサイズが公式APIと一致しません（期待: $($RemoteEntry.size) / 実際: $ActualBytes）。"
        }
        if ($RemoteEntry.lfs.oid) {
            Write-Host "  SHA-256を検証しています..."
            $LocalHash = (Get-FileHash -LiteralPath $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($LocalHash -ne $RemoteEntry.lfs.oid.ToLowerInvariant()) {
                throw "モデルのSHA-256が公式APIと一致しません。"
            }
        }
    }
    Write-Host "  モデル検証完了: $ModelFile ($ActualBytes bytes)"
    Get-RemoteFile -Url "$BaseUrl/chat_template.jinja?download=true" -Destination (Join-Path $ModelRoot "chat_template.jinja")
} else {
    Write-Host "[4/4] RuntimeOnly のためモデル取得を省略しました。"
}

Write-Host ""
Write-Host "セットアップが完了しました。Start-PLaMoPaperTranslator.cmd をダブルクリックしてください。" -ForegroundColor Green
