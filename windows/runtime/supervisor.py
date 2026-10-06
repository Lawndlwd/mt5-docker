"""Keeps one worker process per MT5 terminal running inside the Windows VM.

Watchdog: every HEALTH_INTERVAL seconds each worker's /health is probed. A worker
whose terminal is down, frozen ("stuck") or that does not answer for
HEALTH_FAILURES probes in a row is killed together with its MT5 terminal and
started again (after a start-up grace period, since opening MT5 takes a while).

Also watches the repo share: when a redeploy changes the app, requirements or
runtime files, it stops the workers and exits so start.ps1 can resync and
start everything again (the Windows container itself is not restarted).
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
SHARE = Path(os.environ.get("MT5_SHARE", r"\\host.lan\Data"))
INSTANCES = int(os.environ.get("MT5_INSTANCES", "2"))
BASE_PORT = int(os.environ.get("WORKER_BASE_PORT", "9001"))
MAX_LOG_BYTES = 20 * 1024 * 1024
WATCH_INTERVAL = 20
HEALTH_INTERVAL = int(os.environ.get("HEALTH_INTERVAL", "30"))
HEALTH_FAILURES = int(os.environ.get("HEALTH_FAILURES", "3"))
HEALTH_TIMEOUT = 10
# Opening MT5 at worker start can take a minute or more: don't judge before this.
START_GRACE = int(os.environ.get("START_GRACE", "180"))


def terminal_path(i: int) -> str:
    return rf"C:\MT5\t{i}\terminal64.exe"


def probe(i: int) -> tuple:
    """(healthy, detail) from the worker's /health."""
    url = f"http://127.0.0.1:{BASE_PORT + i - 1}/health"
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT) as r:
            body = json.loads(r.read() or b"{}")
            return r.status == 200, body.get("state", "?")
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read() or b"{}").get("state", f"http {e.code}")
        except ValueError:
            return False, f"http {e.code}"
    except Exception as e:  # timeout, refused, ...
        return False, type(e).__name__


def kill_worker(i: int, proc) -> None:
    """Stops the worker process tree and the MT5 terminal it drives."""
    if proc is not None and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    path = terminal_path(i).replace("'", "''")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process terminal64 -ErrorAction SilentlyContinue | "
         f"Where-Object {{ $_.Path -eq '{path}' }} | Stop-Process -Force"],
        capture_output=True,
    )


def log(message: str) -> None:
    with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def share_fingerprint():
    """(path, size, mtime) of every file the VM syncs from the share, or None if unreachable."""
    entries = []
    try:
        for top in ("app", "runtime", "settings", "requirements.txt"):
            base = SHARE / top
            files = [base] if base.is_file() else base.rglob("*")
            for f in files:
                if f.is_file() and "__pycache__" not in f.parts:
                    st = f.stat()
                    entries.append((str(f), st.st_size, int(st.st_mtime)))
    except OSError:
        return None
    return sorted(entries)


def spawn(i: int) -> subprocess.Popen:
    log_path = LOGS / f"worker-{i}.log"
    if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
        log_path.replace(log_path.with_suffix(".log.1"))
    env = dict(
        os.environ,
        MT5_TERMINAL_PATH=terminal_path(i),
        WORKER_PORT=str(BASE_PORT + i - 1),
        PYTHONUNBUFFERED="1",
    )
    out = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "worker.py")],
        cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log(f"worker {i} started (pid {proc.pid}, port {BASE_PORT + i - 1})")
    return proc


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    fingerprint = share_fingerprint()
    workers = {i: spawn(i) for i in range(1, INSTANCES + 1)}
    failures = {i: 0 for i in workers}
    restart_at = {i: 0.0 for i in workers}
    started_at = {i: time.monotonic() for i in workers}
    unhealthy = {i: 0 for i in workers}
    last_watch = time.monotonic()
    last_health = time.monotonic()

    while True:
        time.sleep(2)
        now = time.monotonic()

        for i, proc in workers.items():
            if proc is None:
                if now >= restart_at[i]:
                    workers[i] = spawn(i)
                    started_at[i] = now
                    unhealthy[i] = 0
                continue
            code = proc.poll()
            if code is None:
                failures[i] = 0
                continue
            failures[i] += 1
            delay = min(60, 5 * failures[i])
            log(f"worker {i} exited with {code}, restarting in {delay}s")
            workers[i] = None
            restart_at[i] = now + delay

        if now - last_health >= HEALTH_INTERVAL:
            last_health = now
            for i, proc in workers.items():
                if proc is None or proc.poll() is not None or now - started_at[i] < START_GRACE:
                    continue
                ok, detail = probe(i)
                if ok:
                    unhealthy[i] = 0
                    continue
                unhealthy[i] += 1
                log(f"worker {i} unhealthy ({detail}) {unhealthy[i]}/{HEALTH_FAILURES}")
                if unhealthy[i] >= HEALTH_FAILURES:
                    log(f"worker {i} restarted by watchdog ({detail})")
                    kill_worker(i, proc)
                    workers[i] = None
                    restart_at[i] = now + 5
                    unhealthy[i] = 0

        if now - last_watch >= WATCH_INTERVAL:
            last_watch = now
            current = share_fingerprint()
            if current is not None and fingerprint is not None and current != fingerprint:
                log("files on the share changed, stopping workers for resync")
                for proc in workers.values():
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                for proc in workers.values():
                    if proc is not None:
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                return
            if fingerprint is None:
                fingerprint = current


if __name__ == "__main__":
    main()
