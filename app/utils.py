from loguru import logger
import os

def setup_logging():
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="<green>{time}</green> <level>{message}</level>"
    )

def redact_sensitive(data: dict) -> dict:
    redacted = data.copy()
    if 'password' in redacted:
        redacted['password'] = '***REDACTED***'
    return redacted
