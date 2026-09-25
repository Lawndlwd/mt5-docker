# One-time provisioning of the Windows VM: Python, MetaTrader 5, firewall,
# power settings and the logon hook that starts the MT5 workers.
# Everything that can change between deploys lives in ../runtime and is
# re-synced from the \\host.lan\Data share on every boot instead.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Start-Transcript -Path "C:\OEM\install-ps.log" -Append

$PythonVersion = "3.12.10"
$PythonDir     = "C:\Python312"
$Mt5Url        = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
$Mt5Terminal   = "C:\Program Files\MetaTrader 5\terminal64.exe"
$Root          = "C:\mt5api"
$Downloads     = "C:\OEM\downloads"

New-Item -ItemType Directory -Force -Path $Root, "$Root\logs", "C:\MT5", $Downloads | Out-Null

function Get-File($Url, $Dest) {
    for ($i = 1; $i -le 5; $i++) {
        try { Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing; return }
        catch { Write-Host "Download failed ($i/5): $_"; Start-Sleep -Seconds (10 * $i) }
    }
    throw "Could not download $Url"
}

# --- Keep the interactive session alive (MT5 needs a logged-in desktop) ------
Write-Host "Configuring power and lock settings..."
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization" /v NoLockScreen /t REG_DWORD /d 1 /f | Out-Null
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive /t REG_SZ /d 0 /f | Out-Null
# Never auto-reboot for updates while the user is logged on.
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f | Out-Null

# --- Python ---------------------------------------------------------------
if (-not (Test-Path "$PythonDir\python.exe")) {
    Write-Host "Installing Python $PythonVersion..."
    $installer = "$Downloads\python-$PythonVersion-amd64.exe"
    Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" $installer
    $p = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList `
        "/quiet", "InstallAllUsers=1", "TargetDir=$PythonDir", "PrependPath=1", `
        "Include_test=0", "Include_doc=0", "Include_launcher=0", "Shortcuts=0"
    if ($p.ExitCode -ne 0) { throw "Python installer exited with $($p.ExitCode)" }
}

# --- MetaTrader 5 -----------------------------------------------------------
if (-not (Test-Path $Mt5Terminal)) {
    Write-Host "Installing MetaTrader 5..."
    $setup = "$Downloads\mt5setup.exe"
    Get-File $Mt5Url $setup
    # The installer downloads the terminal and launches it when done, so do not
    # -Wait on it (that would wait for the terminal too). Poll instead.
    Start-Process -FilePath $setup -ArgumentList "/auto"
    $deadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $deadline) {
        $setupRunning = Get-Process -Name "mt5setup" -ErrorAction SilentlyContinue
        if ((Test-Path $Mt5Terminal) -and -not $setupRunning) { break }
        Start-Sleep -Seconds 5
    }
    if (-not (Test-Path $Mt5Terminal)) { throw "MetaTrader 5 did not install within 20 minutes" }
    Start-Sleep -Seconds 15
    Get-Process -Name "terminal64", "mt5setup" -ErrorAction SilentlyContinue | Stop-Process -Force
}

# --- Permissions: the workers run non-elevated from the Startup folder -------
icacls $Root /grant "*S-1-5-32-545:(OI)(CI)M" /T /Q | Out-Null
icacls "C:\MT5" /grant "*S-1-5-32-545:(OI)(CI)M" /T /Q | Out-Null

# --- Firewall: allow the gateway to reach the workers -----------------------
if (-not (Get-NetFirewallRule -DisplayName "MT5 API workers" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "MT5 API workers" -Direction Inbound -Protocol TCP `
        -LocalPort 9001-9016 -Action Allow -Profile Any | Out-Null
}

# --- Logon hook -------------------------------------------------------------
# boot.cmd waits for the shared folder, then runs the (always up to date)
# start.ps1 straight from the share, in a loop.
@'
@echo off
REM Loops forever: start.ps1 returns whenever the supervisor sees new code on
REM the share (a redeploy), and is then run again to resync and restart workers.
:wait
if exist "\\host.lan\Data\runtime\start.ps1" goto run
timeout /t 5 /nobreak >nul
goto wait
:run
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "\\host.lan\Data\runtime\start.ps1"
timeout /t 15 /nobreak >nul
goto wait
'@ | Set-Content -Path "$Root\boot.cmd" -Encoding ASCII

$startup = "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\mt5api.cmd"
"@start `"`" /min `"$Root\boot.cmd`"" | Set-Content -Path $startup -Encoding ASCII

# First start right now, without waiting for a reboot.
Write-Host "Provisioning done, starting workers..."
Start-Process -FilePath "$Root\boot.cmd" -WindowStyle Minimized
Stop-Transcript
