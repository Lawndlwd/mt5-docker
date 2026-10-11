# Runs at every logon (from \\host.lan\Data\runtime, via C:\mt5api\boot.cmd).
# Syncs the app from the repo share, updates dependencies when requirements.txt
# changes, and runs the supervisor.
# Returns when the supervisor detects new files on the share; boot.cmd then runs it again.

$ErrorActionPreference = "Stop"
$Share = "\\host.lan\Data"
$Root  = "C:\mt5api"
$Mt5Install = "C:\Program Files\MetaTrader 5"

New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
Start-Transcript -Path "$Root\logs\start.log" -Append | Out-Null

# --- Settings (written to the share by the `sync` service on each deploy) ---
$settings = @{ MT5_INSTANCES = "1"; WORKER_BASE_PORT = "9001"; LOG_LEVEL = "info" }
$settingsFile = "$Share\settings\settings.env"
if (Test-Path $settingsFile) {
    foreach ($line in Get-Content $settingsFile) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$') { $settings[$Matches[1]] = $Matches[2] }
    }
}
$instances = 1  # one installed MT5 terminal, like the old Wine setup
Write-Host "Settings: $($settings | ConvertTo-Json -Compress)"

# --- Stop a previous run (safe to re-run by hand) --------------------------
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$Root*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# Leftover terminals, including the old portable copies in C:\MT5\tN.
Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# --- Sync code from the share --------------------------------------------
robocopy "$Share\app" "$Root\app" /MIR /R:3 /W:2 /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy of app failed with $LASTEXITCODE" }
Copy-Item "$Share\requirements.txt", "$Share\runtime\worker.py", "$Share\runtime\supervisor.py" -Destination $Root -Force

# --- Python environment ----------------------------------------------------
$venvPython = "$Root\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & "C:\Python312\python.exe" -m venv "$Root\venv"
    if ($LASTEXITCODE) { throw "venv creation failed" }
}
$hash = (Get-FileHash "$Root\requirements.txt").Hash
$hashFile = "$Root\venv\requirements.sha256"
if ((Get-Content $hashFile -ErrorAction SilentlyContinue) -ne $hash) {
    Write-Host "Installing Python requirements..."
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    & $venvPython -m pip install --disable-pip-version-check -r "$Root\requirements.txt"
    if ($LASTEXITCODE) { throw "pip install failed" }
    Set-Content -Path $hashFile -Value $hash
}

# --- Run the supervisor (blocks until the share changes) --------------------
$env:MT5_INSTANCES    = "$instances"
$env:WORKER_BASE_PORT = $settings.WORKER_BASE_PORT
$env:LOG_LEVEL        = $settings.LOG_LEVEL
$env:MT5_SHARE        = $Share
Write-Host "Starting supervisor with $instances worker(s)."
Stop-Transcript | Out-Null
& $venvPython "$Root\supervisor.py"
