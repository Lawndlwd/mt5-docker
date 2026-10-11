"""Read-only diagnostics for the gateway's /gateway/logs (API key) and a self-restart.

Everything is read from files on this VM: the worker and supervisor logs (paths from the
supervisor), the terminal's own Journal (C:\\MT5\\tN\\logs\\YYYYMMDD.log, what the Journal tab
shows) and the [Experts] section of its Config\\common.ini. Passwords are never logged.
"""

import os
import threading
import time
from pathlib import Path
from typing import List, Optional

TERMINAL_DIR = Path(os.environ["MT5_TERMINAL_PATH"]).parent
TAIL_BYTES = 512 * 1024


def _decode(raw: bytes) -> str:
    # MT5 writes its journal and ini files as UTF-16 LE (with a BOM); our logs are UTF-8.
    if raw.startswith(b"\xff\xfe") or raw[1:200:2].count(b"\x00") > 50:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace")


def tail(path: Optional[Path], lines: int) -> List[str]:
    if not path or not path.exists():
        return [f"(missing: {path})"]
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - TAIL_BYTES)
            if start % 2:  # stay on a UTF-16 code unit boundary
                start += 1
            f.seek(start)
            text = _decode(f.read())
    except OSError as e:
        return [f"(unreadable: {path}: {e})"]
    return text.splitlines()[-lines:]


def latest_journal() -> Optional[Path]:
    files = sorted((TERMINAL_DIR / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def experts_settings() -> List[str]:
    ini = TERMINAL_DIR / "Config" / "common.ini"
    lines = tail(ini, 2000)
    out, inside = [], False
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            inside = s.lower() == "[experts]"
        if inside:
            out.append(s)
    return out or ["(no [Experts] section)"]


def collect(lines: int) -> dict:
    journal = latest_journal()
    env = os.environ
    return {
        "terminal": str(TERMINAL_DIR),
        "experts": experts_settings(),
        "journal_file": str(journal) if journal else None,
        "journal": tail(journal, lines),
        "worker_log": tail(Path(env["WORKER_LOG"]) if env.get("WORKER_LOG") else None, lines),
        "supervisor_log": tail(Path(env["SUPERVISOR_LOG"]) if env.get("SUPERVISOR_LOG") else None, lines),
    }


def restart_soon(delay: float = 1.0) -> None:
    """Exit after the response is sent; the supervisor starts a fresh worker + terminal."""
    def go():
        time.sleep(delay)
        os._exit(3)
    threading.Thread(target=go, daemon=True).start()
