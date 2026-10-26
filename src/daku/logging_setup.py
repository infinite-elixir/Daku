"""JSON logging with secret redaction and a rotating file handler."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from daku.config import Settings

LOG_FILE_NAME = "daku.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "color_message",
    }
)


class SecretRedactor:
    """Replace known secret values anywhere in an emitted line."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        # Longest first so a secret that contains another is fully masked.
        self._secrets = sorted((s for s in secrets or [] if s), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or SecretRedactor()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        line = json.dumps(payload, default=str, ensure_ascii=False)
        return self.redactor(line)


def configure_logging(settings: Settings, level: int = logging.INFO) -> Path:
    """Install stdout and rotating-file JSON handlers on the root logger."""

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / LOG_FILE_NAME
    formatter = JsonFormatter(SecretRedactor(settings.secret_values()))

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Uvicorn's own access logger is noisy and duplicates the request log.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return log_path
