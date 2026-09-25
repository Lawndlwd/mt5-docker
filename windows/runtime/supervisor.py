"""Keeps one worker process per MT5 terminal running inside the Windows VM.

Also watches the repo share: when a redeploy changes the app, requirements or
runtime files, it stops the workers and exits so start.ps1 can resync and
start everything again (the Windows container itself is not restarted).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
SHARE = Path(os.environ.get("MT5_SHARE", r"\\host.lan\Data"))
INSTANCES = int(os.environ.get("MT5_INSTANCES", "2"))
BASE_PORT = int(os.environ.get("WORKER_BASE_PORT", "9001"))
MAX_LOG_BYTES = 20 * 1024 * 1024
WATCH_INTERVAL = 20


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
        MT5_TERMINAL_PATH=rf"C:\MT5\t{i}\terminal64.exe",
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
    last_watch = time.monotonic()

    while True:
        time.sleep(2)
        now = time.monotonic()

        for i, proc in workers.items():
            if proc is None:
                if now >= restart_at[i]:
                    workers[i] = spawn(i)
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
