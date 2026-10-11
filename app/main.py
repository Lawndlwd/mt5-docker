"""MT5 worker API: one process per MT5 terminal, called only by the gateway.

Every /api/* call logs the worker's terminal into the caller's account (see mt5_service.session)
and reads the requested data. Errors carry MT5's own code and message.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from app.mt5_service import (
    MT5Error, get_account_history, get_account_info, get_orders, get_positions,
    health_status, session,
)
from app.schemas import (
    AccountInfoResponse, HistoryResponse, MT5Credentials, OrdersResponse, PositionsResponse,
)
from app.utils import setup_logging

setup_logging()

app = FastAPI(title="MT5 worker", docs_url=None, redoc_url=None, openapi_url=None)


# Gateway probe + supervisor watchdog. A sync handler (threadpool), so it still answers while
# an MT5 request is running.
@app.get("/health")
def health_check():
    st = health_status()
    ok = st["state"] in ("up", "busy")
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "healthy" if ok else "unhealthy", **st,
                 "timestamp": datetime.now(timezone.utc).isoformat()},
    )


@contextmanager
def mt5_session(credentials: MT5Credentials):
    """Runs the block logged in as the caller, or answers with MT5's reason.

    401 = MT5 refused the login (wrong login/password or account unknown on that server)
    503 = the terminal is not running / not reachable (retry later)
    502 = any other MT5 failure
    """
    try:
        with session(credentials.login, credentials.password, credentials.server):
            yield
    except MT5Error as e:
        status = 401 if e.kind == "auth" else 503 if e.kind == "terminal" else 502
        raise HTTPException(
            status_code=status,
            detail=f"MT5 login failed for {credentials.server}: ({e.code}) {e.message}",
        )


def parse_dt(value: str = None):
    return datetime.fromisoformat(value) if value else None


def read(credentials: MT5Credentials, what: str, fetch):
    """Logs in, runs `fetch`, and turns unexpected failures into a 500."""
    logger.info(f"{what} request for login {credentials.login}")
    try:
        with mt5_session(credentials):
            return fetch()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading {what}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/account/info", response_model=AccountInfoResponse)
def account_info(credentials: MT5Credentials):
    info = read(credentials, "account info", get_account_info)
    if info is None:
        raise HTTPException(status_code=500, detail="Could not retrieve account info from MetaTrader 5.")
    return info


@app.post("/api/account/history", response_model=HistoryResponse)
def account_history(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    history = read(credentials, "history", lambda: get_account_history(parse_dt(from_date), parse_dt(to_date)))
    return {"history": history}


@app.post("/api/positions", response_model=PositionsResponse)
def positions(credentials: MT5Credentials):
    return {"positions": read(credentials, "positions", get_positions)}


@app.post("/api/orders", response_model=OrdersResponse)
def orders(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    orders_list = read(credentials, "orders", lambda: get_orders(parse_dt(from_date), parse_dt(to_date)))
    return {"orders": orders_list}
