param(
    [switch]$AllUsers
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$HostName = "jp.gear2.plamo_paper_translator"
$IsAdministrator = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($AllUsers -and -not $IsAdministrator) {
    $PowerShellExecutable = (Get-Process -Id $PID).Path
    $RelaunchArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-AllUsers"
    )
    Write-Host "PC全体の登録を解除するため、管理者権限を要求します。" -ForegroundColor Yellow
    $ElevatedProcess = Start-Process -FilePath $PowerShellExecutable -Verb RunAs -ArgumentList $RelaunchArguments -Wait -PassThru
    exit $ElevatedProcess.ExitCode
}

$RegistryRoots = if ($AllUsers) { @("HKLM:", "HKCU:") } else { @("HKCU:") }
$RegistryPaths = foreach ($RegistryRoot in $RegistryRoots) {
    "$RegistryRoot\Software\Google\Chrome\NativeMessagingHosts\$HostName"
    "$RegistryRoot\Software\Wow6432Node\Google\Chrome\NativeMessagingHosts\$HostName"
}
$ManifestRoot = if ($AllUsers) {
    Join-Path $env:ProgramData "PLaMoPaperTranslator"
} else {
    Join-Path $env:LOCALAPPDATA "PLaMoPaperTranslator"
}
$ManifestPath = Join-Path $ManifestRoot "$HostName.json"
$LauncherConfigPath = Join-Path (Join-Path $env:LOCALAPPDATA "PLaMoPaperTranslator") "launcher-config.json"

foreach ($RegistryPath in $RegistryPaths) {
    if (Test-Path -LiteralPath $RegistryPath) {
        Remove-Item -LiteralPath $RegistryPath -Force
    }
}
if (Test-Path -LiteralPath $ManifestPath) {
    Remove-Item -LiteralPath $ManifestPath -Force
}
if (Test-Path -LiteralPath $LauncherConfigPath) {
    Remove-Item -LiteralPath $LauncherConfigPath -Force
}

Write-Host "ChromeランチャーのNative Messaging登録を解除しました。" -ForegroundColor Green
Write-Host "Scope: $(if ($AllUsers) { 'PC全体 (HKLM) + 現在のユーザー (HKCU)' } else { '現在のユーザー (HKCU)' })"
