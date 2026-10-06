from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import contextmanager
from datetime import datetime
import pytz
from dotenv import load_dotenv
from loguru import logger

from app.schemas import MT5Credentials, AccountInfoResponse, HistoryResponse, PositionsResponse, OrdersResponse
from app.mt5_service import (
    MT5Error, get_account_info, get_account_history, get_orders, get_positions,
    health_status, session,
)
from app.dependencies import setup_logging

# Load environment variables
load_dotenv()

# Setup logging
setup_logging()

app = FastAPI(
    title="MetaTrader 5 Multi-User API",
    description="REST API for MetaTrader 5 account operations",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add no-cache headers for API endpoints
@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

server_tz = pytz.timezone('Europe/London')

# Health for the gateway probe and the supervisor watchdog. Sync (threadpool) so it
# still answers while an MT5 request is running.
@app.get("/health")
def health_check():
    st = health_status()
    ok = st["state"] in ("up", "busy")
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "healthy" if ok else "unhealthy", **st,
                 "timestamp": datetime.now(server_tz).isoformat()},
    )

@app.get("/")
async def root():
    return {"message": "MetaTrader 5 Multi-User API is running."}

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


@app.post("/api/account/info", response_model=AccountInfoResponse)
def account_info(credentials: MT5Credentials):
    logger.info(f"Received account info request for login: {credentials.login}")
    with mt5_session(credentials):
        info = get_account_info()
    if info is None:
        logger.error("Could not retrieve account info.")
        raise HTTPException(status_code=500, detail="Could not retrieve account info from MetaTrader 5.")
    return info


@app.post("/api/account/history", response_model=HistoryResponse)
def account_history(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    logger.info(f"Received history request for login: {credentials.login}")
    try:
        with mt5_session(credentials):
            history = get_account_history(parse_dt(from_date), parse_dt(to_date))
        logger.info(f"History array length: {len(history)}")
        return {"history": history}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving history: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/positions", response_model=PositionsResponse)
def positions(credentials: MT5Credentials):
    logger.info(f"Received positions request for login: {credentials.login}")
    try:
        with mt5_session(credentials):
            positions_list = get_positions()
        logger.info(f"Retrieved {len(positions_list)} positions.")
        return {"positions": positions_list}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving positions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/orders", response_model=OrdersResponse)
def orders(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    logger.info(f"Received orders request for login: {credentials.login}")
    try:
        with mt5_session(credentials):
            orders_list = get_orders(parse_dt(from_date), parse_dt(to_date))
        logger.info(f"Retrieved {len(orders_list)} orders.")
        return {"orders": orders_list}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving orders: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")
