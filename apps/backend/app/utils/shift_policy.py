"""Shift-level timing policy resolver.

`shift_master` is the single source of truth for attendance timing policy
(auto-checkout, grace, reminders, limits, half-day). This module turns a
ShiftDefinition row into a plain dataclass so every call site reads the same
values — no duplicated policy logic and no company-level fallback.

See docs/shift-level-timing-policy.md.
"""
from dataclasses import dataclass
from typing import Optional, Any

from app.constants import (
    DEFAULT_AUTO_CHECKOUT_ENABLED,
    DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES,
    DEFAULT_GRACE_MINUTES,
    DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES,
    DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS,
    DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES,
    DEFAULT_CHECKIN_CUTOFF_MINUTES,
    DEFAULT_HALF_DAY_MINUTES,
)


@dataclass(frozen=True)
class ShiftPolicy:
    auto_checkout_enabled: bool
    auto_checkout_grace_minutes: int
    grace_minutes: int
    checkin_reminder_delay_minutes: int
    checkout_reminder_grace_hours: int
    early_checkin_limit_minutes: int
    checkin_cutoff_minutes: int
    half_day_minutes: int


def resolve_shift_policy(sd: Optional[Any]) -> ShiftPolicy:
    """Return the timing policy for a shift definition.

    Falls back to the module defaults only when the attribute is missing/None
    (e.g. a detached object or a not-yet-migrated row). Otherwise the
    ShiftDefinition row is authoritative.
    """
    def _int(attr: str, default: int) -> int:
        v = getattr(sd, attr, None)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _bool(attr: str, default: bool) -> bool:
        v = getattr(sd, attr, None)
        return bool(v) if v is not None else default

    return ShiftPolicy(
        auto_checkout_enabled=_bool("auto_checkout_enabled", DEFAULT_AUTO_CHECKOUT_ENABLED),
        auto_checkout_grace_minutes=_int("auto_checkout_grace_minutes", DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES),
        grace_minutes=_int("grace_minutes", DEFAULT_GRACE_MINUTES),
        checkin_reminder_delay_minutes=_int("checkin_reminder_delay_minutes", DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES),
        checkout_reminder_grace_hours=_int("checkout_reminder_grace_hours", DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS),
        early_checkin_limit_minutes=_int("early_checkin_limit_minutes", DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES),
        checkin_cutoff_minutes=_int("checkin_cutoff_minutes", DEFAULT_CHECKIN_CUTOFF_MINUTES),
        half_day_minutes=_int("half_day_minutes", DEFAULT_HALF_DAY_MINUTES),
    )
