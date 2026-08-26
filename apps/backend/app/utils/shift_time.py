"""Shared shift/time helpers used across attendance, shifts, and the background worker.

Kept here to avoid duplicating these small functions across feature modules.
"""
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.features.redmine.constants import REDMINE_TO_IANA_TZ


def safe_zone(tz_str: str) -> ZoneInfo:
    """Resolve a timezone, mapping Redmine/Rails labels to IANA names first."""
    iana = REDMINE_TO_IANA_TZ.get(tz_str, tz_str)
    try:
        return ZoneInfo(iana)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def safe_zone_plain(tz_str: str) -> ZoneInfo:
    """Resolve an IANA timezone directly (no Redmine label mapping)."""
    try:
        return ZoneInfo(tz_str or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def shift_end_datetime(shift_date: date, start_time, end_time, tz: ZoneInfo) -> datetime:
    """Compute the shift's end datetime, adding a day for overnight shifts."""
    if start_time and end_time and end_time <= start_time:
        shift_date = shift_date + timedelta(days=1)
    return datetime.combine(shift_date, end_time, tzinfo=tz)


def resolve_timestamp(client_timestamp: Optional[datetime]) -> tuple[datetime, bool]:
    """Normalize a client timestamp. Returns (timestamp, is_synced).

    `is_synced` is True when the client-provided timestamp was trusted.
    """
    if not client_timestamp:
        return datetime.now(timezone.utc), False
    now = datetime.now(timezone.utc)
    if client_timestamp.tzinfo is None:
        client_timestamp = client_timestamp.replace(tzinfo=timezone.utc)
    if client_timestamp > now + timedelta(hours=1):
        return now, False
    if now - client_timestamp > timedelta(hours=24):
        return now, False
    return client_timestamp, True


def resolve_timestamp_dt(client_timestamp: Optional[datetime]) -> datetime:
    """Normalize a client timestamp, returning only the datetime."""
    ts, _ = resolve_timestamp(client_timestamp)
    return ts
