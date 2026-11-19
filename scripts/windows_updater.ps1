param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$AppExe,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ResultPath
)
$ErrorActionPreference = 'Stop'
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 300
}
if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
    '旧程序未能在 45 秒内退出，更新已取消。' | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    exit 1
}
try {
    $setup = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS' -Wait -PassThru
    if ($setup.ExitCode -ne 0) {
        throw "安装器返回错误代码 $($setup.ExitCode)；原版本可能仍可启动，请联系发布者。"
    }
    if (-not (Test-Path -LiteralPath $AppExe)) {
        throw '安装后找不到主程序，请联系发布者。'
    }
    Start-Process -FilePath $AppExe
} catch {
    "更新失败：$($_.Exception.Message)" | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    if (Test-Path -LiteralPath $AppExe) {
        try { Start-Process -FilePath $AppExe } catch {}
    }
    exit 1
}
