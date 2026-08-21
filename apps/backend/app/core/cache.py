"""
In-memory TTL caches for rarely-changing reference rows.

Hot endpoints (check-in / check-out) re-fetch the company settings row and
shift-definition rows on every call, even though they change only when an
admin edits them.  Caching them for a short TTL removes those round-trips
without needing cross-process invalidation.

Notes:
- Loaded ORM rows are expunged from the request session and cached on the
  module so they can be safely read by later requests (no lazy loading).
- A short TTL self-heals if an admin edits the underlying row; call the
  ``clear_*`` functions explicitly from write paths to propagate faster.
"""
import logging
import threading
import time
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SETTINGS_TTL_SECONDS = 30
_SHIFT_DEF_TTL_SECONDS = 30

_settings_lock = threading.Lock()
_settings_cache: dict = {"ts": 0.0, "value": None}

_shift_def_lock = threading.Lock()
_shift_def_cache: dict = {}


def get_company_settings(db: Session) -> Optional[object]:
    """Return the company SystemSetting row (cached briefly).

    The returned object is detached from any session and must only be read
    for scalar column values (never added back to a session).
    """
    now = time.monotonic()
    cached = _settings_cache.get("value")
    if cached is not None and now - _settings_cache.get("ts", 0.0) < _SETTINGS_TTL_SECONDS:
        return cached

    from app.models.system_setting import SystemSetting

    value = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
    with _settings_lock:
        _settings_cache["ts"] = time.monotonic()
        _settings_cache["value"] = value
    if value is not None:
        db.expunge(value)
    return value


def clear_company_settings_cache() -> None:
    """Drop the cached company settings row (call after admin edits)."""
    with _settings_lock:
        _settings_cache["ts"] = 0.0
        _settings_cache["value"] = None


def get_shift_definition(db: Session, shift_code: str) -> Optional[object]:
    """Return a ShiftDefinition by code (cached briefly per code)."""
    if not shift_code:
        return None

    now = time.monotonic()
    cached = _shift_def_cache.get(shift_code)
    if cached is not None and now - (cached.get("ts", 0.0)) < _SHIFT_DEF_TTL_SECONDS:
        return cached.get("value")

    from app.models.shift_definition import ShiftDefinition

    value = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift_code).first()
    with _shift_def_lock:
        _shift_def_cache[shift_code] = {"ts": time.monotonic(), "value": value}
    if value is not None:
        db.expunge(value)
    return value


def clear_shift_definition_cache(shift_code: Optional[str] = None) -> None:
    """Drop cached shift definitions.  Clear one code, or the whole cache."""
    with _shift_def_lock:
        if shift_code is None:
            _shift_def_cache.clear()
        else:
            _shift_def_cache.pop(shift_code, None)