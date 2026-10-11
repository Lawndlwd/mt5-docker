"""Runs the MT5 worker API (app/main.py) for one terminal.

The supervisor sets MT5_TERMINAL_PATH (this worker's portable terminal), WORKER_PORT and
LOG_LEVEL; app/mt5_service.py opens that terminal itself.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # C:\mt5api holds the app package

import uvicorn  # noqa: E402

PORT = int(os.environ["WORKER_PORT"])
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info")
os.environ["LOG_LEVEL"] = LOG_LEVEL.upper()  # loguru expects upper-case levels

if __name__ == "__main__":
    # No per-request access lines: the gateway (15 s) and watchdog (30 s) health probes would
    # bury the useful ones. The app logs each MT5 request, login and error itself.
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower(), access_log=False,
    )
