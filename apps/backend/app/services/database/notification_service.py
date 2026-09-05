"""Service layer for in-app notifications.

Pure SQLAlchemy helpers — no FastAPI dependencies, no global state.
Callers open a Session (via Depends(get_db)) and pass it in.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification


def create_notification(
    db: Session,
    *,
    user_id: uuid.UUID,
    category: str,
    title: str,
    message: str,
    priority: str = "normal",
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    link: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Notification:
    """Create a single notification row. Caller commits."""
    n = Notification(
        user_id=user_id,
        category=category[:32],
        title=title[:255],
        message=message,
        priority=priority[:16] if priority else "normal",
        actor_id=actor_id,
        actor_name=actor_name[:255] if actor_name else None,
        link=link[:512] if link else None,
        extra=metadata or {},
    )
    db.add(n)
    db.flush()
    return n


def list_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
) -> list[Notification]:
    """Return notifications for a user, newest first."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_unread(db: Session, *, user_id: uuid.UUID) -> int:
    """Count of unread notifications for the badge query. Uses the partial
    index `idx_notifications_user_unread` for fast lookup."""
    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.read_at.is_(None),
    )
    return len(list(db.execute(stmt).scalars().all()))


def mark_read(
    db: Session,
    *,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Mark a single notification read. Returns True if changed, False if not
    found / already read / not owned by the user."""
    now = datetime.now(timezone.utc)
    # First do an explicit lookup so we know if the row exists for this user
    # (and we can skip the UPDATE if it doesn't match)
    n = (
        db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if not n:
        return False
    n.read_at = now
    db.commit()
    return True


def mark_all_read(db: Session, *, user_id: uuid.UUID) -> int:
    """Mark every unread notification for the user as read.
    Returns the number of rows updated."""
    now = datetime.now(timezone.utc)
    # Use bulk update_mappings for efficiency on a potentially large set
    rows = (
        db.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.read_at = now
    db.commit()
    return len(rows)


def delete_notification(
    db: Session,
    *,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Hard-delete a single notification. Only if it belongs to the user.
    Returns True if deleted."""
    n = (
        db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        .scalars()
        .first()
    )
    if not n:
        return False
    db.delete(n)
    db.commit()
    return True
