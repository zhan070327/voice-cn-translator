param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$AppExe,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ResultPath
)
$ErrorActionPreference = 'Stop'
$restartOnFailure = $false
try {
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw '旧程序未能在 45 秒内退出，更新已取消。'
    }
    $restartOnFailure = $true
    $setup = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS' -Wait -PassThru
    if ($setup.ExitCode -ne 0) {
        throw "安装器返回错误代码 $($setup.ExitCode)；原版本可能仍可启动，请联系发布者。"
    }
    if (-not (Test-Path -LiteralPath $AppExe)) {
        throw '安装后找不到主程序，请联系发布者。'
    }
    Start-Process -FilePath $AppExe
} catch {
    $reason = "更新失败：$($_.Exception.Message)"
    try {
        Set-Content -LiteralPath $ResultPath -Value $reason -Encoding UTF8
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
    }
    [Console]::Error.WriteLine($reason)
    if ($restartOnFailure) {
        try {
            if (Test-Path -LiteralPath $AppExe) {
                Start-Process -FilePath $AppExe
            }
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
        }
    }
    exit 1
}
exit 0
