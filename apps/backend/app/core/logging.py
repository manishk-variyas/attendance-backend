"""
Logging configuration for the backend application (loguru-based).

This module sets up structured JSON logging with multiple outputs:
- Console (stdout) for Docker/Kubernetes logging
- Rotating log files (access.log, audit.log, error.log, sweeper.log)

Features:
- All logs are formatted as JSON for easy parsing by log aggregation tools
- Correlation IDs are included in every log entry (tracks requests across the system)
- Different sinks for different purposes (access, audit, error, sweeper)
- Automatic rotation + retention + compression (no cron cleanup needed)
- All existing stdlib `logging.getLogger(...)` calls are routed through loguru
  via an intercept handler, so nothing else in the codebase needs to change.

Loggers/sinks:
- app.access:  HTTP request logs  -> access.log
- app.audit:   security events      -> audit.log
- app.error:   error logs           -> error.log
- app.sweeper: background cron runs -> sweeper.log
- everything else                   -> console (+ error.log on ERROR)
"""
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone

from loguru import logger


# Log directory - can be overridden with LOG_DIR environment variable
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

ROTATION = "10 MB"
RETENTION = "14 days"
COMPRESSION = "zip"


def _record_to_json(record) -> str:
    """Serialize a loguru record into the app's canonical JSON shape."""
    extra = record["extra"]

    t = record["time"]
    if getattr(t, "tzinfo", None) is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc)

    entry = {
        "time": t.isoformat(),
        "level": record["level"].name,
        "source": extra.get("source", record["name"]),
        "correlation_id": extra.get("correlation_id", "-"),
        "message": record["message"],
    }

    if record["exception"]:
        entry["exception"] = "".join(traceback.format_exception(*record["exception"]))

    extra_data = extra.get("extra_data")
    if extra_data:
        entry["metadata"] = extra_data

    return json.dumps(entry, default=str)


def _patch(record):
    record["extra"]["_serialized"] = _record_to_json(record)


logger = logger.patch(_patch)


class InterceptHandler(logging.Handler):
    """Forward stdlib logging records through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).bind(
            correlation_id=getattr(record, "correlation_id", "-"),
            extra_data=getattr(record, "extra_data", None),
            source=record.name,
        ).log(level, record.getMessage())


def setup_logging() -> None:
    """Configure loguru sinks and route all stdlib logging through them."""
    # Route every stdlib logger (app.access, app.audit, app.error, app.sweeper,
    # module loggers, root) through loguru via the intercept handler.
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    logger.remove()

    logger.add(
        sys.stdout,
        format="{extra[_serialized]}",
        level="INFO",
    )
    logger.add(
        os.path.join(LOG_DIR, "access.log"),
        format="{extra[_serialized]}",
        level="INFO",
        filter=lambda r: r["extra"].get("source") == "app.access",
        rotation=ROTATION,
        retention=RETENTION,
        compression=COMPRESSION,
    )
    logger.add(
        os.path.join(LOG_DIR, "audit.log"),
        format="{extra[_serialized]}",
        level="INFO",
        filter=lambda r: r["extra"].get("source") == "app.audit",
        rotation=ROTATION,
        retention="30 days",
        compression=COMPRESSION,
    )
    logger.add(
        os.path.join(LOG_DIR, "error.log"),
        format="{extra[_serialized]}",
        level="ERROR",
        rotation=ROTATION,
        retention="30 days",
        compression=COMPRESSION,
    )
    logger.add(
        os.path.join(LOG_DIR, "sweeper.log"),
        format="{extra[_serialized]}",
        level="INFO",
        filter=lambda r: r["extra"].get("source") == "app.sweeper",
        rotation=ROTATION,
        retention=RETENTION,
        compression=COMPRESSION,
    )
