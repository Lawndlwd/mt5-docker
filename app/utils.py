import os

from loguru import logger


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="<green>{time}</green> <level>{message}</level>",
    )
