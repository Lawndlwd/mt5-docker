"""One installed MT5 terminal, driven like the old Wine version.

Every request runs initialize(credentials) -> reads -> shutdown(), one at a time (lock).
"""

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Tuple

import MetaTrader5 as mt5
import pytz
from loguru import logger

server_tz = pytz.timezone('Europe/London')  # Common for many brokers

TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
INIT_TIMEOUT_MS = 60_000

# MT5 last_error codes (MetaTrader5 Python docs).
AUTH_FAILED = -6  # wrong login/password, or the account is unknown on that server
IPC_ERRORS = {-10001, -10002, -10003, -10004, -10005}  # terminal not running / not reachable

# One MT5 call sequence (login + reads) at a time: held for the whole request.
_lock = threading.Lock()
_session = {"busy_since": None, "last_error": None}

# Busy longer than this = the terminal is considered frozen (supervisor restarts the worker).
STUCK_AFTER_S = 180


class MT5Error(Exception):
    """Login/terminal failure carrying MT5's own code and message."""

    def __init__(self, kind: str, code: int, message: str):
        super().__init__(f"({code}) {message}")
        self.kind = kind  # "auth" | "terminal" | "unknown"
        self.code = code
        self.message = message


def _last_error() -> Tuple[int, str]:
    err = mt5.last_error() or (0, "unknown")
    return int(err[0]), str(err[1])


def _kind(code: int) -> str:
    if code == AUTH_FAILED:
        return "auth"
    if code in IPC_ERRORS:
        return "terminal"
    return "unknown"


@contextmanager
def session(login_id: int, password: str, server: str):
    """initialize(credentials) -> the block's reads -> shutdown(). Raises MT5Error."""
    with _lock:
        _session["busy_since"] = started = time.monotonic()
        try:
            logger.info(f"Connecting {login_id}@{server}")
            if not mt5.initialize(
                TERMINAL_PATH, login=login_id, password=password, server=server, timeout=INIT_TIMEOUT_MS,
            ):
                code, msg = _last_error()
                _session["last_error"] = f"({code}) {msg}"
                logger.warning(f"Login {login_id}@{server} failed: ({code}) {msg}")
                raise MT5Error(_kind(code), code, msg)
            info = mt5.account_info()
            if info is None or info.login != login_id:
                # Never serve another account's data.
                actual = getattr(info, "login", None)
                _session["last_error"] = f"terminal is on {actual}"
                raise MT5Error("auth", AUTH_FAILED, f"Terminal is on {actual}, not {login_id}@{server}")
            _session["last_error"] = None
            logger.info(f"Logged in {login_id}@{server} in {round((time.monotonic() - started) * 1000)} ms")
            yield
        finally:
            mt5.shutdown()  # as in the old version: drop the connection after every request
            _session["busy_since"] = None


def health_status() -> dict:
    """up = idle, busy = a request is running, stuck = running longer than STUCK_AFTER_S."""
    if not _lock.acquire(timeout=0.5):
        since = _session["busy_since"]
        busy_s = round(time.monotonic() - since) if since else 0
        return {"state": "stuck" if busy_s > STUCK_AFTER_S else "busy", "busy_s": busy_s}
    _lock.release()
    return {"state": "up", **({"error": _session["last_error"]} if _session["last_error"] else {})}


def get_account_info():
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "currency": info.currency,
        "name": getattr(info, 'name', None),
        "login": info.login,
        # False for an investor (read-only) login: lets the app spot master passwords.
        "trade_allowed": bool(getattr(info, 'trade_allowed', False)),
    }


def _range(from_date: Optional[datetime], to_date: Optional[datetime]) -> Tuple[datetime, datetime]:
    """Requested window; default the last 2 years (+1 day for broker time zones)."""
    to_dt = to_date or (datetime.now(server_tz) + timedelta(hours=24))
    from_dt = from_date or (to_dt - timedelta(days=730))
    return from_dt, to_dt


def get_account_history(from_date: datetime = None, to_date: datetime = None):
    from_dt, to_dt = _range(from_date, to_date)
    logger.info(f"from_date: {from_dt}, to_date: {to_dt}")
    deals = mt5.history_deals_get(from_dt, to_dt)
    if deals is None:
        return []
    return [deal._asdict() for deal in deals]


def get_positions():
    positions = mt5.positions_get()
    if positions is None:
        return []
    return [pos._asdict() for pos in positions]


def get_orders(from_date: datetime = None, to_date: datetime = None):
    from_dt, to_dt = _range(from_date, to_date)
    orders = mt5.history_orders_get(from_dt, to_dt)
    if orders is None:
        return []
    return [order._asdict() for order in orders]
