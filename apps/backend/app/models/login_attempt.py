"""Login-attempt tracking for per-IP brute-force protection.

One row per (identifier, kind) — currently only kind='ip' is used.
The per-email block is handled by Keycloak's built-in Brute Force
Detection (configured in the Keycloak admin console), not here.

Constants are hardcoded by design (no admin config):
  - MAX_FAILURES_PER_IP     = 5
  - BLOCK_WINDOW_MINUTES    = 15  (rolling window for failure count)
  - BLOCK_DURATION_MINUTES  = 15  (how long the IP stays blocked)
  - BRUTE_FORCE_THRESHOLD   = 20  (system-wide failures per hour -> alert)
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.models import Base

logger = logging.getLogger(__name__)


MAX_FAILURES_PER_IP = 5
BLOCK_WINDOW_MINUTES = 15
BLOCK_DURATION_MINUTES = 15
BRUTE_FORCE_THRESHOLD = 20
BRUTE_FORCE_WINDOW_MINUTES = 60

KIND_IP = "ip"
KIND_EMAIL = "email"

MAX_FAILURES_PER_EMAIL = 10
EMAIL_BLOCK_WINDOW_MINUTES = 15
EMAIL_BLOCK_DURATION_MINUTES = 30


class LoginAttempt(Base):
    """Tracks failed login attempts for brute-force protection.

    One row per (identifier, kind). Currently only kind='ip' is used.
    The per-email block is handled by Keycloak directly.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint("kind IN ('ip', 'email')", name="chk_login_attempts_kind"),
        Index("ix_login_attempts_kind_blocked", "kind", "blocked_until"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    identifier = Column(String(255), nullable=False)
    kind = Column(String(16), nullable=False)
    failure_count = Column(Integer, nullable=False, server_default=text("0"))
    first_failure_at = Column(DateTime(timezone=True), nullable=True)
    blocked_until = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


@dataclass
class BlockStatus:
    is_blocked: bool
    blocked_until: Optional[datetime] = None
    failure_count: int = 0
    just_blocked: bool = False  # True if this call caused the block
