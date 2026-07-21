"""
Logging configuration for the backend application.

This module sets up structured JSON logging with multiple outputs:
- Console output (stdout) for Docker/Kubernetes logging
- Rotating log files (access.log, audit.log, error.log)

Features:
- All logs are formatted as JSON for easy parsing by log aggregation tools
- Correlation IDs are included in every log entry (tracks requests across the system)
- Different loggers for different purposes (access, audit, error)
- Automatic log rotation (10MB per file, keeps 5 backups)

Loggers:
- app.access: HTTP request logs (method, path, status, duration)
- app.audit: Security events (login, logout, etc.)
- app.error: Error logs
- root: General application logs
"""
import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from typing import Any


# Log directory - can be overridden with LOG_DIR environment variable
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Rotation settings: 10MB per file, keep 5 backup files
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5


class JSONFormatter(logging.Formatter):
    """
    Custom formatter that outputs log records as JSON.
    
    Each log entry is a JSON object with:
    - time: ISO timestamp
    - level: log level (INFO, ERROR, etc.)
    - source: logger name
    - correlation_id: request tracking ID
    - message: the log message
    - metadata: any extra fields (optional)
    - exception: exception info (if present)
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "source": record.name,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        extra = getattr(record, "extra_data", None)
        if extra:
            log_entry["metadata"] = extra

        return json.dumps(log_entry, default=str)


class CorrelationFilter(logging.Filter):
    """
    Logging filter that ensures correlation_id is always present.
    
    If a log record doesn't have a correlation_id attribute,
    this adds one with a default value of "-".
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


def _build_handler(filename: str, level: int = logging.DEBUG) -> logging.handlers.RotatingFileHandler:
    """Create a rotating file handler for the given log file."""
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, filename),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationFilter())
    return handler


def _build_console_handler(level: int = logging.INFO) -> logging.StreamHandler:
    """Create a console handler that outputs to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationFilter())
    return handler


def setup_logging() -> None:
    """
    Configure all loggers for the application.
    
    This sets up:
    - app.access: Logs HTTP requests (method, path, status, duration)
    - app.audit: Logs security events (login, logout, etc.)
    - app.error: Logs errors
    - root: General application logs
    
    Each logger writes to both console (for Docker) and rotating log files.
    """
    # Access logger - logs HTTP requests
    access_logger = logging.getLogger("app.access")
    access_logger.setLevel(logging.INFO)
    access_logger.addHandler(_build_handler("access.log", logging.INFO))
    access_logger.addHandler(_build_console_handler(logging.INFO))
    access_logger.propagate = False

    # Audit logger - logs security events
    audit_logger = logging.getLogger("app.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(_build_handler("audit.log", logging.INFO))
    audit_logger.addHandler(_build_console_handler(logging.INFO))
    audit_logger.propagate = False

    # Error logger - logs errors
    error_logger = logging.getLogger("app.error")
    error_logger.setLevel(logging.ERROR)
    error_logger.addHandler(_build_handler("error.log", logging.ERROR))
    error_logger.addHandler(_build_console_handler(logging.ERROR))
    error_logger.propagate = False

    # Root logger - general application logs
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(_build_console_handler(logging.INFO))
    root.addHandler(_build_handler("error.log", logging.ERROR))
