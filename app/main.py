from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime
import MetaTrader5 as mt5
import pytz
from dotenv import load_dotenv
from loguru import logger

from app.schemas import MT5Credentials, AccountInfoResponse, HistoryResponse, PositionsResponse, OrdersResponse
from app.mt5_service import connect_mt5, get_account_info, get_account_history, get_positions, get_orders
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

# Health check endpoint for Scaleway/Docker
@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration"""
    try:
        # Quick MT5 availability check
        mt5_version = mt5.version()
        return {
            "status": "healthy",
            "mt5_available": mt5_version is not None,
            "timestamp": datetime.now(server_tz).isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

@app.get("/")
async def root():
    return {"message": "MetaTrader 5 Multi-User API is running."}

@app.post("/api/account/info", response_model=AccountInfoResponse)
async def account_info(credentials: MT5Credentials):
    logger.info(f"Received account info request for login: {credentials.login}")
    
    if not connect_mt5(credentials.login, credentials.password, credentials.server):
        logger.error("Failed to connect to MetaTrader 5.")
        raise HTTPException(status_code=401, detail="Failed to connect to MetaTrader 5 with provided credentials.")
    
    info = get_account_info()
    mt5.shutdown()
    
    if info is None:
        logger.error("Could not retrieve account info.")
        raise HTTPException(status_code=500, detail="Could not retrieve account info from MetaTrader 5.")
    
    logger.info("Account info retrieved successfully.")
    return info

@app.post("/api/account/history", response_model=HistoryResponse)
async def account_history(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    logger.info(f"Received history request for login: {credentials.login}")
    
    if not connect_mt5(credentials.login, credentials.password, credentials.server):
        logger.error("Failed to connect to MetaTrader 5.")
        raise HTTPException(status_code=401, detail="Failed to connect to MetaTrader 5 with provided credentials.")
    
    try:
        from_dt = datetime.fromisoformat(from_date) if from_date else None
        to_dt = datetime.fromisoformat(to_date) if to_date else None
        
        history = get_account_history(from_dt, to_dt)
        logger.info(f"History array length: {len(history)}")
        mt5.shutdown()
        
        return {"history": history}
    except Exception as e:
        mt5.shutdown()
        logger.error(f"Error retrieving history: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.post("/api/positions", response_model=PositionsResponse)
async def positions(credentials: MT5Credentials):
    logger.info(f"Received positions request for login: {credentials.login}")
    
    if not connect_mt5(credentials.login, credentials.password, credentials.server):
        logger.error("Failed to connect to MetaTrader 5.")
        raise HTTPException(status_code=401, detail="Failed to connect to MetaTrader 5 with provided credentials.")
    
    try:
        positions_list = get_positions()
        mt5.shutdown()
        
        logger.info(f"Retrieved {len(positions_list)} positions.")
        return {"positions": positions_list}
    except Exception as e:
        mt5.shutdown()
        logger.error(f"Error retrieving positions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.post("/api/orders", response_model=OrdersResponse)
async def orders(credentials: MT5Credentials, from_date: str = None, to_date: str = None):
    logger.info(f"Received orders request for login: {credentials.login}")
    
    if not connect_mt5(credentials.login, credentials.password, credentials.server):
        logger.error("Failed to connect to MetaTrader 5.")
        raise HTTPException(status_code=401, detail="Failed to connect to MetaTrader 5 with provided credentials.")
    
    try:
        from_dt = datetime.fromisoformat(from_date) if from_date else None
        to_dt = datetime.fromisoformat(to_date) if to_date else None
        
        orders_list = get_orders(from_dt, to_dt)
        mt5.shutdown()
        
        logger.info(f"Retrieved {len(orders_list)} orders.")
        return {"orders": orders_list}
    except Exception as e:
        mt5.shutdown()
        logger.error(f"Error retrieving orders: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")
