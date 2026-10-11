from typing import Any, List, Optional

from pydantic import BaseModel, Field


class MT5Credentials(BaseModel):
    login: int = Field(..., examples=[12345678])
    password: str = Field(..., examples=["investor-password"])
    server: str = Field(..., examples=["Broker-Server"])


class AccountInfoResponse(BaseModel):
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    currency: str
    name: Optional[str]
    login: int
    trade_allowed: Optional[bool] = None


class HistoryResponse(BaseModel):
    history: List[Any]


class PositionsResponse(BaseModel):
    positions: List[Any]


class OrdersResponse(BaseModel):
    orders: List[Any]
