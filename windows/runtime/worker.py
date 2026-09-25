"""Runs the unmodified FastAPI app (app/main.py) bound to one portable MT5 terminal.

The app calls mt5.initialize(login=..., password=..., server=...) without a
terminal path, which would make every worker share the same terminal. Here we
wrap mt5.initialize so this process always uses its own terminal folder.
"""

import functools
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5  # noqa: E402

TERMINAL_PATH = os.environ["MT5_TERMINAL_PATH"]
PORT = int(os.environ["WORKER_PORT"])
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info")

_initialize = mt5.initialize


@functools.wraps(_initialize)
def initialize(*args, **kwargs):
    if not args and "path" not in kwargs:
        args = (TERMINAL_PATH,)
    kwargs.setdefault("portable", True)
    return _initialize(*args, **kwargs)


mt5.initialize = initialize

# The current app/main.py imports setup_logging from app.dependencies, but it is
# defined in app.utils. Bridge it here so the app can start without editing it.
import app.dependencies  # noqa: E402
import app.utils  # noqa: E402

if not hasattr(app.dependencies, "setup_logging"):
    app.dependencies.setup_logging = app.utils.setup_logging

os.environ["LOG_LEVEL"] = LOG_LEVEL.upper()  # loguru expects upper-case levels

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower())
