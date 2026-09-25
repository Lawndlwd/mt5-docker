from fastapi import HTTPException
from app.schemas import MT5Credentials

def get_mt5_credentials(credentials: MT5Credentials):
    # Add further validation/sanitization if needed
    if not credentials.login or not credentials.password or not credentials.server:
        raise HTTPException(status_code=400, detail="Missing MT5 credentials.")
    return credentials
