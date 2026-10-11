# MT5 API on Linux (real Windows in Docker)

Runs the MT5 API in `app/` on a Linux server, inside a real Windows 11 LTSC VM managed by
[dockur/windows](https://github.com/dockur/windows) (KVM). No Wine: the MT5 terminal and the
`MetaTrader5` Python package run on actual Windows.

```
 clients ──► gateway :8080  (Linux container, public)
               │  one request per terminal at a time, queue, API key, health
               ▼  internal compose network
             windows  (dockur/windows, KVM VM)
               ├─ worker 1 :9001 ── MT5 terminal C:\MT5\t1 (portable)
               ├─ worker 2 :9002 ── MT5 terminal C:\MT5\t2 (portable)
               └─ ...               (MT5_INSTANCES)
```

- **Workers** run `app.main:app`, one process per portable terminal (`C:\MT5\tN`), so two
  users never share a logged-in terminal. A terminal opens on its first request (with that
  account) and stays open; later requests switch accounts in it. The same account with the
  same password reuses the live session; the result is checked against the requested
  login and server before any data is returned.
- **Gateway** passes requests through (same URLs and JSON as the workers), one per worker
  at a time, first come first served, preferring a worker already on that account. It
  probes workers every `HEALTH_INTERVAL` s and skips unhealthy ones.
- **Supervisor** (in the VM) keeps one worker per terminal running, restarts frozen or
  dead ones (watchdog on `/health`), and before each terminal start writes the MT5 settings
  the Python API needs (algo trading on, not switched off on account change).
- **Code updates**: on every deploy the `sync` service copies `app/`,
  `requirements.txt` and `windows/runtime/` into the `shared` volume, which the VM
  sees as `\\host.lan\Data`. On every boot, and within ~20 s of a redeploy, the VM resyncs them,
  reinstalls requirements if they changed, and restarts the workers. Windows is
  **not** reinstalled.

## Requirements

- A Linux host with **KVM**: `ls -l /dev/kvm` must exist. That means bare metal, or a
  VPS with nested virtualization. Many cloud VMs (e.g. Hetzner Cloud) don't have it.
- Free resources: about 6 GB RAM, 4 vCPU and 64 GB disk for the VM, plus a little for
  the gateway.
- Windows licence: dockur installs with Microsoft's generic keys. Unactivated Windows
  works (watermark, no personalization). For production use a valid key (`WINDOWS_KEY`).

## Deploy on Dokploy

1. **Create → Compose** (type *Docker Compose*), source = this Git repo/branch.
2. **Compose Path**: `./docker-compose.yml`
3. **Environment**: paste from `.env.example` and adjust. At least set:
   - `GATEWAY_API_KEY` = a long random string
   - `WINDOWS_PASSWORD` = something other than `admin`
4. **Domains** → add a domain → service **`gateway`**, port **`8080`**, HTTPS on.
   Never add a domain for `windows`.
5. **Deploy.** The first deploy downloads Windows (~5 GB) and installs it unattended,
   then installs Python 3.12 + MT5 and starts the workers. Expect **30–45 min**.
   Later deploys take seconds.
6. Check it: `curl -H 'X-API-Key: <GATEWAY_API_KEY>' https://<your-domain>/gateway/health`
   shows `healthy` equal to `MT5_INSTANCES` when everything is ready.

> Keep the `windows-storage` volume. It holds the installed Windows disk. Deleting it
> means a full reinstall.
>
> Changing `MT5_INSTANCES` only restarts the workers (within ~20 s). New terminal folders
> are created automatically.
>
> The `sync` service shows as *exited* after each deploy. That's normal: it copies the
> app into the `shared` volume and stops.

## Run without Dokploy

```bash
cp .env.example .env     # edit it
docker compose up -d --build
docker compose logs -f windows   # installation progress
docker compose exec gateway python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/gateway/health').read().decode())"
```

## Using the API

The paths and bodies are the same as the app. Add the API key header if you set one:

```bash
curl -X POST https://<domain>/api/account/info \
  -H 'X-API-Key: <GATEWAY_API_KEY>' -H 'Content-Type: application/json' \
  -d '{"login": 12345678, "password": "investor-password", "server": "Broker-Server"}'
```

| Gateway endpoint | Purpose |
|---|---|
| `GET /gateway/ping` | Gateway liveness (Docker healthcheck). The only route without the API key |
| `GET /gateway/health` | Probes each worker: state and last MT5 error. `200` if at least one is up |
| `GET /gateway/queue` | Load and estimated wait for a new request |
| `GET /gateway/logs?worker=N&lines=200` | Worker N's log, the supervisor log, its MT5 Journal and `[Experts]` settings |
| `GET /gateway/windows` | Visible window titles on the Windows desktop (MT5 dialogs waiting for a click) |
| `GET /gateway/screen` | PNG screenshot of the Windows desktop |
| `POST /gateway/restart?worker=N` | Restart worker N with a fresh terminal (supervisor rewrites its MT5 settings) |
| `POST /api/account/info`, `/api/account/history`, `/api/positions`, `/api/orders` | Forwarded to a free worker |

Status codes: `401` bad/missing API key or MT5 refused the login (wrong password / account
unknown on that server), `503` no healthy terminal free within `QUEUE_TIMEOUT` s or the terminal
not reachable, `504` an MT5 call exceeded `REQUEST_TIMEOUT`. Error `detail` carries MT5's own
code and message, e.g. `(-6) Authorization failed`.

## Seeing the Windows desktop / debugging

The web viewer listens on `127.0.0.1:8006` on the server. From your machine:

```bash
ssh -L 8006:127.0.0.1:8006 user@server    # then open http://localhost:8006
```

Useful files inside Windows:

| Path | What |
|---|---|
| `C:\OEM\install.log`, `C:\OEM\install-ps.log` | One-time provisioning (Python, MT5) |
| `C:\mt5api\logs\start.log` | Every boot / resync |
| `C:\mt5api\logs\supervisor.log` | Worker starts, crashes, restarts |
| `C:\mt5api\logs\worker-N.log` | The app's own logs for terminal N |

- If the broker's server name isn't found, open `C:\MT5\t1\terminal64.exe` once in the
  viewer, use *File → Open an Account*, and search for the broker so the terminal learns
  its servers. Or copy the broker's `servers.dat` into each `C:\MT5\tN\Config\`.
- `(-10005) IPC timeout`: the terminal doesn't accept Python's commands. The supervisor
  writes `[Experts] Enabled=1, AllowLiveTrading=1, Account=0, Profile=0` into each
  `Config\common.ini` before starting it; if *Algo Trading* is still red in a terminal,
  check `supervisor.log` for the "algo trading settings written" line.
- Don't log into accounts by hand in the worker terminals without *Save password*: MT5
  remembers the last account and asks for its password on the next start.
- To re-run provisioning by hand: run `C:\OEM\install.bat` in the VM.

## Files

| File | Role |
|---|---|
| `app/`, `requirements.txt` | The MT5 worker API that runs inside Windows |
| `docker-compose.yml` | The services, the persistent volumes, settings passed into the VM |
| `sync/` | Copies the app, runtime scripts and settings into the `shared` volume on every deploy |
| `gateway/` | Linux proxy: queue, one request per terminal, API key, health |
| `windows/oem/install.bat`, `install.ps1` | Run once by dockur after Windows setup: Python, MT5, firewall, no sleep/lock, logon hook |
| `windows/runtime/start.ps1` | Every boot: sync code, pip install if needed, create terminals, run supervisor |
| `windows/runtime/supervisor.py` | One worker per terminal: watchdog, MT5 settings, exits on redeploy so `start.ps1` resyncs |
| `windows/runtime/worker.py` | Starts the worker API for one terminal (port and terminal path from the supervisor) |
