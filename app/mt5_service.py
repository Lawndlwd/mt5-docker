# mt5_service.py
"""One long-lived MT5 terminal per worker process.

The terminal is opened on the first request, with that request's account (an MT5 opened
without an account blocks on its login window), and then stays open. Later requests switch accounts inside that terminal (initialize with credentials), instead of
starting and shutting down a terminal on every call (that took seconds and often
timed out before the login finished, which surfaced as "wrong password").

The live session is reused only when the same login + server + password asks
again. The password is compared by hash, so nobody can read an account that is
already logged in without knowing its password.
"""

import hashlib
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Tuple

import MetaTrader5 as mt5
import pytz
from loguru import logger

server_tz = pytz.timezone('Europe/London')  # Common for many brokers

INIT_TIMEOUT_MS = 60_000

# MT5 last_error codes (MetaTrader5 Python docs).
AUTH_FAILED = -6  # wrong login/password, or the account is unknown on that server
IPC_ERRORS = {-10001, -10002, -10003, -10004, -10005}  # terminal not running / not reachable

# One MT5 call sequence (login + reads) at a time: held for the whole request.
_lock = threading.Lock()
_session = {"key": None, "login": None, "busy_since": None, "last_error": None}

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


def _key(login: int, server: str, password: str) -> str:
    return hashlib.sha256(f"{login}\x00{server}\x00{password}".encode()).hexdigest()


def _fail(login_id: int, server: str, shutdown: bool) -> MT5Error:
    code, msg = _last_error()
    _session["key"] = _session["login"] = None
    _session["last_error"] = f"({code}) {msg}"
    if shutdown:
        mt5.shutdown()  # terminal gone or never answered: the next request starts a fresh one
    logger.warning(f"Login {login_id}@{server} failed: ({code}) {msg}")
    return MT5Error(_kind(code), code, msg)


def _login(login_id: int, password: str, server: str) -> None:
    """Makes `login_id` the active account (caller holds _lock). Raises MT5Error.

    The terminal is opened lazily, WITH the account's credentials: an MT5 opened without an
    account shows its login / "open an account" window and never answers (IPC timeout). Once
    open it stays open, and later requests switch accounts with initialize(credentials).
    """
    key = _key(login_id, server, password)
    started = time.monotonic()
    if mt5.terminal_info() is not None:
        info = mt5.account_info()
        if _session["key"] == key and info is not None and info.login == login_id:
            return  # same account and password: reuse the live session
    # Credentials always go through initialize(): it opens the terminal if needed, otherwise
    # attaches to the running one and authorizes on THIS server. mt5.login() on a terminal that
    # was on another broker kept the old server (journal: "'878865': authorization on
    # MetaQuotes-Demo failed" for a MEXAtlantic-Real account).
    logger.info(f"Connecting {login_id}@{server}")
    _session["key"] = _session["login"] = None
    if not mt5.initialize(login=login_id, password=password, server=server, timeout=INIT_TIMEOUT_MS):
        code, _ = _last_error()
        raise _fail(login_id, server, shutdown=code in IPC_ERRORS)
    info = mt5.account_info()
    if info is None or info.login != login_id or (getattr(info, "server", server) or server) != server:
        # Never serve another account's data: the terminal must really be on the requested one.
        actual = f"{getattr(info, 'login', None)}@{getattr(info, 'server', None)}"
        logger.warning(f"Asked {login_id}@{server}, terminal is on {actual}")
        _session["last_error"] = f"terminal is on {actual}"
        raise MT5Error("auth", -6, f"Terminal is on {actual}, not {login_id}@{server}")
    _session["key"] = key
    _session["login"] = login_id
    _session["last_error"] = None
    logger.info(f"Logged in {login_id}@{server} in {round((time.monotonic() - started) * 1000)} ms")


@contextmanager
def session(login_id: int, password: str, server: str):
    """Login + everything read inside the block runs with this worker's terminal to itself."""
    with _lock:
        _session["busy_since"] = time.monotonic()
        try:
            _login(login_id, password, server)
            yield
        finally:
            _session["busy_since"] = None


def health_status() -> dict:
    """Worker health for the gateway and the supervisor watchdog.

    up    = ready: terminal open, or not opened yet (it opens on the first request, with that
            request's account); `terminal` says which
    busy  = a request is running (normal length)
    stuck = a request has been running longer than STUCK_AFTER_S (watchdog restarts the worker)
    """
    if not _lock.acquire(timeout=0.5):
        since = _session["busy_since"]
        busy_s = round(time.monotonic() - since) if since else 0
        return {"state": "stuck" if busy_s > STUCK_AFTER_S else "busy", "busy_s": busy_s}
    try:
        ti = mt5.terminal_info()
        return {
            "state": "up",
            "terminal": "open" if ti is not None else "closed",
            "connected": bool(getattr(ti, "connected", False)) if ti is not None else False,
            "login": _session["login"],
            **({"error": _session["last_error"]} if _session["last_error"] else {}),
        }
    finally:
        _lock.release()


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
