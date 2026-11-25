param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$AppExe,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [string]$AppId = ''
)
$ErrorActionPreference = 'Stop'
$restartOnFailure = $false
function Write-UpdateStep([string]$message) {
    Add-Content -LiteralPath $ResultPath -Value "$(Get-Date -Format o) $message" -Encoding UTF8
}
try {
    Write-UpdateStep "更新器已启动：$Installer"
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw '旧程序未能在 45 秒内退出，更新已取消。'
    }
    $restartOnFailure = $true
    Write-UpdateStep "启动安装器：$Installer"
    $setup = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS' -Wait -PassThru
    Write-UpdateStep "安装器返回码：$($setup.ExitCode)"
    if ($setup.ExitCode -ne 0) {
        throw "安装器返回错误代码 $($setup.ExitCode)；原版本可能仍可启动，请联系发布者。"
    }
    $installedExe = $null
    if ($AppId) {
        foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
            $entry = Join-Path $root "${AppId}_is1"
            $record = Get-ItemProperty -LiteralPath $entry -ErrorAction SilentlyContinue
            if ($record -and $record.InstallLocation) {
                $candidate = Join-Path $record.InstallLocation (Split-Path $AppExe -Leaf)
                if (Test-Path -LiteralPath $candidate) { $installedExe = $candidate; break }
            }
        }
    }
    if (-not $installedExe -and (Test-Path -LiteralPath $AppExe)) {
        # Inno normally reuses a custom installation directory. A portable copy
        # has no install record, so an unrelated old EXE must not be restarted.
        if ($AppId) { throw '安装后未找到安装目录记录；已保留旧程序，无法确认新程序路径。' }
        $installedExe = $AppExe
    }
    if (-not $installedExe) { throw '安装后找不到主程序，请联系发布者。' }
    Write-UpdateStep "准备重开：$installedExe"
    $newProcess = Start-Process -FilePath $installedExe -PassThru
    Write-UpdateStep "更新成功；已重开新程序，进程 ID：$($newProcess.Id)"
} catch {
    $reason = "更新失败：$($_.Exception.Message)"
    try {
        Write-UpdateStep $reason
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
    }
    [Console]::Error.WriteLine($reason)
    if ($restartOnFailure) {
        try {
            if (Test-Path -LiteralPath $AppExe) {
                Write-UpdateStep "尝试重开原程序：$AppExe"
                Start-Process -FilePath $AppExe
            }
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
        }
    }
    exit 1
}
exit 0
