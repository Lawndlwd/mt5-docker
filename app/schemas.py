from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime

class MT5Credentials(BaseModel):
    login: int = Field(..., example=12345678)
    password: str = Field(..., example="your_password")
    server: str = Field(..., example="Broker-Server")

class AccountInfoResponse(BaseModel):
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    currency: str
    name: Optional[str]
    login: int

class HistoryQuery(BaseModel):
    from_date: Optional[datetime]
    to_date: Optional[datetime]

class Deal(BaseModel):
    ticket: int
    time: int
    type: int
    entry: int
    volume: float
    price: float
    symbol: str
    profit: float
    comment: str
    # Add more fields as needed

class Position(BaseModel):
    ticket: int
    symbol: str
    volume: float
    price_open: float
    price_current: float
    profit: float
    # Add more fields as needed

class Order(BaseModel):
    ticket: int
    symbol: str
    volume_current: float
    price_open: float
    type_time: int
    type_filling: int
    # Add more fields as needed

class HistoryResponse(BaseModel):
    history: List[Any]

class PositionsResponse(BaseModel):
    positions: List[Any]

class OrdersResponse(BaseModel):
    orders: List[Any]
