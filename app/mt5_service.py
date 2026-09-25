# mt5_service.py
import MetaTrader5 as mt5
from datetime import datetime
import pytz
import time
from datetime import datetime, timedelta
from loguru import logger

# Placeholder for MT5 connection and account info retrieval
server_tz = pytz.timezone('Europe/London')  # Common for many brokers

def connect_mt5(login_id: int, password: str, server: str):
    if not mt5.initialize(server=server, login=login_id, password=password):
        return False
    return True

def get_account_info():
    info = mt5.account_info()
    if info is None:
        mt5.shutdown()
        return None
    result = {
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "currency": info.currency,
        "name": getattr(info, 'name', None),
        "login": info.login,
    }
    mt5.shutdown()
    return result

def get_account_history(from_date: datetime = None, to_date: datetime = None):
    mt5.terminal_info()
    time.sleep(1)
    # Default: last 30 days
    to_date = datetime.now(server_tz) + timedelta(hours=24)
    from_date = to_date - timedelta(days=730)
    logger.info(f"from_date: {from_date}, to_date: {to_date}")

    deals = mt5.history_deals_get(from_date, to_date)
    mt5.shutdown()
    if deals is None:
        return []
    return [deal._asdict() for deal in deals]

def get_positions():
    positions = mt5.positions_get()
    mt5.shutdown()
    if positions is None:
        return []
    return [pos._asdict() for pos in positions]

def get_orders(from_date: datetime = None, to_date: datetime = None):
    mt5.terminal_info()
    time.sleep(1)
    # Default: last 30 days
    to_date = datetime.now(server_tz) + timedelta(hours=24)
    from_date = to_date - timedelta(days=730)
    deals = mt5.history_orders_get(from_date, to_date)
    mt5.shutdown()
    if deals is None:
        return []
    return [deal._asdict() for deal in deals]
