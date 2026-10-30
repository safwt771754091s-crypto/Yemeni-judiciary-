"""Structured logging setup."""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.core.config import settings

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.is_production else logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%H:%M:%S"
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "asyncpg", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
