# MT5 API on Linux (real Windows in Docker)

Runs the FastAPI app in `app/` (a copy of the repo-root `app/`) **unchanged** on a Linux server, inside a real
Windows 11 LTSC VM managed by [dockur/windows](https://github.com/dockur/windows) (KVM).
No Wine: the MT5 terminal and the `MetaTrader5` Python package run on actual Windows.

```
 clients ──► gateway :8080  (Linux container, public)
               │  one request per terminal at a time, queue, API key, health
               ▼  internal compose network
             windows  (dockur/windows, KVM VM)
               ├─ worker 1 :9001 ── MT5 terminal C:\MT5\t1 (portable)
               ├─ worker 2 :9002 ── MT5 terminal C:\MT5\t2 (portable)
               └─ ...               (MT5_INSTANCES)
```

- **Workers** run `app.main:app` as is. `worker.py` only pins each process to its own
  terminal folder, so two users can never end up on the same logged-in terminal.
- **Gateway** passes every request through unchanged (same URLs and JSON as the app)
  and sends at most one request to each worker at a time. Extra requests wait in a queue.
- **Code updates**: `mt5-docker/app/`, `mt5-docker/requirements.txt` and `windows/runtime/` are
  shared into the VM. On every boot, and within ~20 s of a redeploy, the VM resyncs them,
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
2. **Compose Path**: `./mt5-docker/docker-compose.yml`
3. **Environment**: paste from `.env.example` and adjust. At least set:
   - `GATEWAY_API_KEY` = a long random string
   - `WINDOWS_PASSWORD` = something other than `admin`
   - `GATEWAY_BIND=127.0.0.1` (only Traefik should expose the API)
4. **Domains** → add a domain → service **`gateway`**, port **`8080`**, HTTPS on.
   Never add a domain for `windows`.
5. **Deploy.** The first deploy downloads Windows (~5 GB) and installs it unattended,
   then installs Python 3.12 + MT5 and starts the workers. Expect **30–45 min**.
   Later deploys take seconds.
6. Check it: `https://<your-domain>/gateway/health` shows `workers_up` equal to
   `MT5_INSTANCES` when everything is ready.

> Keep the `windows-storage` volume. It holds the installed Windows disk. Deleting it
> means a full reinstall.
>
> Changing `MT5_INSTANCES` recreates the `windows` container (Windows reboots, about 1–2 min).
> New terminal folders are created automatically.

## Run without Dokploy

```bash
cd mt5-docker
cp .env.example .env     # edit it
docker compose up -d --build
docker compose logs -f windows   # installation progress
curl localhost:8070/gateway/health
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
| `GET /gateway/ping` | Gateway liveness (Docker healthcheck) |
| `GET /gateway/health` | Probes each worker. `200` if at least one is up |
| everything else | Forwarded to a free worker (`/`, `/health`, `/api/...`) |

Status codes the gateway adds: `401` bad/missing API key, `503` all terminals busy for
`QUEUE_TIMEOUT` seconds or Windows still booting, `504` an MT5 call exceeded `REQUEST_TIMEOUT`.

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
- To re-run provisioning by hand: run `C:\OEM\install.bat` in the VM.

## Files

| File | Role |
|---|---|
| `app/`, `requirements.txt` | The API that runs inside Windows (copied from the repo root; edit here for deploys) |
| `docker-compose.yml` | The two services, the persistent volume, settings passed into the VM |
| `gateway/` | Linux proxy: queue, one request per terminal, API key, health |
| `windows/oem/install.bat`, `install.ps1` | Run once by dockur after Windows setup: Python, MT5, firewall, no sleep/lock, logon hook |
| `windows/runtime/start.ps1` | Every boot: sync code, pip install if needed, create terminals, run supervisor |
| `windows/runtime/supervisor.py` | Keeps one worker per terminal alive. Exits on redeploy so `start.ps1` resyncs |
| `windows/runtime/worker.py` | Runs the unchanged app, pinned to one terminal |
