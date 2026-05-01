import logging
import os
from logging.config import dictConfig


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {
                "handlers": ["default"],
                "level": level,
            },
        }
    )


def get_logger(name: str = "vent") -> logging.Logger:
    return logging.getLogger(name)
