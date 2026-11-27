param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$AppExe,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [string]$AppId = '',
    [string]$ExpectedVersion = ''
)
$ErrorActionPreference = 'Stop'
$restartOnFailure = $false
function Write-UpdateStep([string]$message) {
    Add-Content -LiteralPath $ResultPath -Value "$(Get-Date -Format o) $message" -Encoding UTF8
}
function Test-InstalledVersion([string]$candidate) {
    if (-not $candidate) { return $false }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf -ErrorAction SilentlyContinue)) { return $false }
    $actualVersion = (Get-Item -LiteralPath $candidate -ErrorAction SilentlyContinue).VersionInfo.ProductVersion
    if ($actualVersion -and $actualVersion.Trim() -eq $ExpectedVersion) { return $true }
    Write-UpdateStep "主程序版本不匹配：$candidate（当前：$actualVersion，目标：$ExpectedVersion）"
    return $false
}
try {
    Write-UpdateStep "更新器已启动：$Installer"
    $parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($parent) {
        $deadline = (Get-Date).AddSeconds(45)
        while (-not $parent.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 300
        }
        if (-not $parent.HasExited) {
            try {
                Stop-Process -Id $ParentPid -Force
                if ($parent.WaitForExit(5000)) {
                    Write-UpdateStep '旧程序未在 45 秒内退出，已强制结束'
                } else {
                    Write-UpdateStep '已请求强制结束旧程序，继续启动安装器。'
                }
            } catch {
                if (-not $parent.HasExited) {
                    Write-UpdateStep "无法强制结束旧程序，继续启动安装器：$($_.Exception.Message)"
                }
            }
        }
    }
    $restartOnFailure = $true
    Write-UpdateStep "启动安装器：$Installer"
    $setup = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS' -Wait -PassThru
    Write-UpdateStep "安装器返回码：$($setup.ExitCode)"
    if ($setup.ExitCode -ne 0) {
        throw "安装器返回错误代码 $($setup.ExitCode)；原版本可能仍可启动，请联系发布者。"
    }
    $restartOnFailure = $false
    if (-not $ExpectedVersion) { throw '缺少用于验证新程序的目标版本。' }
    $installedExe = $null
    if ($AppId) {
        foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
            $entry = Join-Path $root "${AppId}_is1"
            $record = Get-ItemProperty -LiteralPath $entry -ErrorAction SilentlyContinue
            if ($record -and $record.InstallLocation) {
                $candidate = Join-Path $record.InstallLocation (Split-Path $AppExe -Leaf) -ErrorAction SilentlyContinue
                if (Test-InstalledVersion $candidate) { $installedExe = $candidate; break }
            }
        }
    }
    if (-not $installedExe -and (Test-InstalledVersion $AppExe)) {
        $installedExe = $AppExe
    }
    if (-not $installedExe) { throw "安装后找不到目标版本 $ExpectedVersion 的主程序。" }
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
