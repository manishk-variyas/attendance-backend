"""Per-IP brute-force protection for the auth endpoints.

Pure functions — no global state, no FastAPI dependencies. Each call
opens its own short-lived DB session (mirroring the pattern in
`features/auth/services/session.py`).

Behavior:
  - 5 failed attempts from one IP within 15 min  →  IP blocked for 15 min
  - successful login from a blocked IP             →  block cleared (legit user)
  - 20 failures across all IPs in 1 hour            →  audit "brute_force_suspected" event

Keycloak handles per-email brute-force detection natively (configured in
the Keycloak admin console), so this module only does per-IP.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.login_attempt import (
    BRUTE_FORCE_THRESHOLD,
    BRUTE_FORCE_WINDOW_MINUTES,
    BLOCK_DURATION_MINUTES,
    BLOCK_WINDOW_MINUTES,
    KIND_IP,
    MAX_FAILURES_PER_IP,
    LoginAttempt,
    BlockStatus,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_ip_blocked(ip: str) -> BlockStatus:
    """Return whether this IP is currently blocked. Does NOT record a failure."""
    if not ip:
        return BlockStatus(is_blocked=False, failure_count=0)
    db = SessionLocal()
    try:
        row = (
            db.query(LoginAttempt)
            .filter(LoginAttempt.identifier == ip, LoginAttempt.kind == KIND_IP)
            .first()
        )
        if not row or not row.blocked_until:
            return BlockStatus(is_blocked=False, failure_count=row.failure_count if row else 0)
        now = _utcnow()
        if row.blocked_until > now:
            return BlockStatus(is_blocked=True, blocked_until=row.blocked_until, failure_count=row.failure_count)
        # Block expired; reset the row so the next failure starts a fresh window
        row.failure_count = 0
        row.first_failure_at = None
        row.blocked_until = None
        row.updated_at = now
        db.commit()
        return BlockStatus(is_blocked=False, failure_count=0)
    finally:
        db.close()


def record_ip_failure(ip: str) -> BlockStatus:
    """Record one failed attempt from this IP. May newly-block the IP.

    Returns a BlockStatus that tells the caller whether the IP is now blocked
    AND whether this call caused the block (so the route can return 429 to the
    client and emit the audit event).
    """
    if not ip:
        return BlockStatus(is_blocked=False, failure_count=0)
    now = _utcnow()
    db = SessionLocal()
    try:
        row = (
            db.query(LoginAttempt)
            .filter(LoginAttempt.identifier == ip, LoginAttempt.kind == KIND_IP)
            .first()
        )
        if not row:
            row = LoginAttempt(
                identifier=ip,
                kind=KIND_IP,
                failure_count=1,
                first_failure_at=now,
                last_attempt_at=now,
            )
            db.add(row)
            db.commit()
            return BlockStatus(is_blocked=False, failure_count=1)

        # If the previous failure window has expired, reset the counter.
        if row.first_failure_at is None or (now - row.first_failure_at) > timedelta(minutes=BLOCK_WINDOW_MINUTES):
            row.failure_count = 1
            row.first_failure_at = now
            row.blocked_until = None
        else:
            row.failure_count = (row.failure_count or 0) + 1

        row.last_attempt_at = now
        row.updated_at = now

        just_blocked = False
        if row.failure_count >= MAX_FAILURES_PER_IP and row.blocked_until is None:
            row.blocked_until = now + timedelta(minutes=BLOCK_DURATION_MINUTES)
            just_blocked = True

        db.commit()

        is_blocked_now = (
            row.blocked_until is not None and row.blocked_until > now
        )
        return BlockStatus(
            is_blocked=is_blocked_now,
            blocked_until=row.blocked_until,
            failure_count=row.failure_count,
            just_blocked=just_blocked,
        )
    finally:
        db.close()


def clear_ip(ip: str) -> None:
    """Clear this IP's failure record. Called on successful login so legit
    users aren't penalized for past typos."""
    if not ip:
        return
    db = SessionLocal()
    try:
        row = (
            db.query(LoginAttempt)
            .filter(LoginAttempt.identifier == ip, LoginAttempt.kind == KIND_IP)
            .first()
        )
        if not row:
            return
        db.delete(row)
        db.commit()
    finally:
        db.close()


def system_failure_count(window_minutes: int = BRUTE_FORCE_WINDOW_MINUTES) -> int:
    """Total failed-login attempts across all IPs in the last window minutes.
    Used to detect brute-force attempts across many IPs.
    """
    db = SessionLocal()
    try:
        threshold = _utcnow() - timedelta(minutes=window_minutes)
        result = db.execute(
            text(
                "SELECT COALESCE(SUM(failure_count), 0) "
                "FROM login_attempts "
                "WHERE last_attempt_at >= :threshold"
            ),
            {"threshold": threshold},
        )
        return int(result.scalar() or 0)
    finally:
        db.close()


def cleanup_expired(older_than_hours: int = 24) -> int:
    """Lazy cleanup of rows that haven't been touched in `older_than_hours`.
    Returns the number of rows deleted. Called on a 1-in-100 chance by
    `is_blocked`/`record_failure` to keep the table bounded.
    """
    db = SessionLocal()
    try:
        threshold = _utcnow() - timedelta(hours=older_than_hours)
        result = db.execute(
            text("DELETE FROM login_attempts WHERE last_attempt_at < :threshold"),
            {"threshold": threshold},
        )
        db.commit()
        return int(result.rowcount or 0)
    finally:
        db.close()


def is_brute_force_active() -> bool:
    """True if the system-wide failure count over the last window exceeds the
    threshold. Used to emit a single 'brute_force_suspected' audit event
    when the threshold is crossed.
    """
    return system_failure_count() >= BRUTE_FORCE_THRESHOLD
