param(
    [string]$ExecutablePath = "",
    [string]$AppHome = "",
    [switch]$AllUsers
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$HostName = "jp.gear2.plamo_paper_translator"
$ExtensionId = "cpiiadekehcofkocnlplcaolgioefcjd"
$AppRoot = Split-Path -Parent $PSScriptRoot

if (-not $ExecutablePath) {
    $Candidates = @(
        (Join-Path $AppRoot "PLaMoPaperTranslator.exe"),
        (Join-Path $AppRoot "dist\PLaMoPaperTranslator\PLaMoPaperTranslator.exe"),
        (Join-Path $AppRoot "dist\PLaMoPaperTranslator.exe")
    )
    $ExecutablePath = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $ExecutablePath -or -not (Test-Path -LiteralPath $ExecutablePath)) {
    throw "PLaMoPaperTranslator.exe が見つかりません。先に build-portable.ps1 を実行するか、-ExecutablePath を指定してください。"
}

$ExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path
$AppHome = if ($AppHome) { (Resolve-Path -LiteralPath $AppHome).Path } else { (Resolve-Path -LiteralPath $AppRoot).Path }

$IsAdministrator = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($AllUsers -and -not $IsAdministrator) {
    $PowerShellExecutable = (Get-Process -Id $PID).Path
    $RelaunchArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-ExecutablePath", ('"{0}"' -f $ExecutablePath),
        "-AppHome", ('"{0}"' -f $AppHome),
        "-AllUsers"
    )
    Write-Host "PC全体へ登録するため、管理者権限を要求します。" -ForegroundColor Yellow
    $ElevatedProcess = Start-Process -FilePath $PowerShellExecutable -Verb RunAs -ArgumentList $RelaunchArguments -Wait -PassThru
    exit $ElevatedProcess.ExitCode
}

$ManifestRoot = if ($AllUsers) {
    Join-Path $env:ProgramData "PLaMoPaperTranslator"
} else {
    Join-Path $env:LOCALAPPDATA "PLaMoPaperTranslator"
}
$ManifestPath = Join-Path $ManifestRoot "$HostName.json"
$LauncherConfigRoot = Join-Path $env:LOCALAPPDATA "PLaMoPaperTranslator"
$LauncherConfigPath = Join-Path $LauncherConfigRoot "launcher-config.json"
New-Item -ItemType Directory -Force -Path $ManifestRoot | Out-Null
New-Item -ItemType Directory -Force -Path $LauncherConfigRoot | Out-Null

$Manifest = [ordered]@{
    name = $HostName
    description = "PLaMo Paper Translator launcher"
    path = $ExecutablePath
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ManifestPath, ($Manifest | ConvertTo-Json -Depth 3), $Utf8NoBom)
$LauncherConfig = [ordered]@{
    executable = $ExecutablePath
    app_home = $AppHome
}
[System.IO.File]::WriteAllText($LauncherConfigPath, ($LauncherConfig | ConvertTo-Json), $Utf8NoBom)

$RegistryRoots = if ($AllUsers) { @("HKLM:", "HKCU:") } else { @("HKCU:") }
$RegistryPaths = foreach ($RegistryRoot in $RegistryRoots) {
    "$RegistryRoot\Software\Google\Chrome\NativeMessagingHosts\$HostName"
    "$RegistryRoot\Software\Wow6432Node\Google\Chrome\NativeMessagingHosts\$HostName"
}
foreach ($RegistryPath in $RegistryPaths) {
    New-Item -Path $RegistryPath -Force | Out-Null
    Set-Item -Path $RegistryPath -Value $ManifestPath
}

Write-Host "Chromeランチャーを登録しました。" -ForegroundColor Green
Write-Host "Host: $HostName"
Write-Host "Extension ID: $ExtensionId"
Write-Host "Executable: $ExecutablePath"
Write-Host "App home: $AppHome"
Write-Host "Scope: $(if ($AllUsers) { 'PC全体 (HKLM) + 現在のユーザー (HKCU)' } else { '現在のユーザー (HKCU)' })"
if (Get-Process -Name "chrome" -ErrorAction SilentlyContinue) {
    Write-Host "Chromeが起動中です。アドレスバーで chrome://restart を開いてから拡張アイコンを押してください。" -ForegroundColor Yellow
} else {
    Write-Host "Chromeを起動して拡張アイコンを押してください。"
}
