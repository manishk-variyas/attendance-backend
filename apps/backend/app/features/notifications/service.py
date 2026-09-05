"""High-level notification helper that uses FastAPI BackgroundTasks.

Notifications are scheduled in the response cycle — the main action returns
immediately to the user, and the notification insert happens after. This
keeps the main flow fast (no extra DB round-trip on the hot path) while
ensuring the notification is delivered before the response is fully sent
(so the user polling the notifications endpoint immediately after the
action will see the new entry).

If the process dies between the main action committing and the
background task running, the notification is lost. For most cases this
is acceptable; if a stronger guarantee is needed later, switch to a
durable queue (Celery, RQ, etc.).

Usage:
    from app.features.notifications.service import schedule_notification

    @router.post("/things")
    def create_thing(background_tasks: BackgroundTasks, ...):
        do_main_thing()
        schedule_notification(
            background_tasks,
            user_id=...,
            category="thing_created",
            title="...",
            message="...",
        )
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.database.notification_service import create_notification

logger = logging.getLogger(__name__)


def schedule_notification(
    background_tasks: BackgroundTasks,
    *,
    user_id: uuid.UUID,
    category: str,
    title: str,
    message: str,
    priority: str = "normal",
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    link: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Schedule a notification to be created after the response is sent.

    The call returns immediately. The actual `create_notification` runs in
    a background task with a fresh DB session (so the caller's session is
    not reused after the response cycle has started).
    """
    background_tasks.add_task(
        _create_notification_task,
        user_id=user_id,
        category=category,
        title=title,
        message=message,
        priority=priority,
        actor_id=actor_id,
        actor_name=actor_name,
        link=link,
        extra=extra,
    )


def _create_notification_task(
    *,
    user_id: uuid.UUID,
    category: str,
    title: str,
    message: str,
    priority: str = "normal",
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    link: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Background task: open a fresh session, insert, commit. Errors are
    logged but do not propagate (the response is already sent)."""
    db: Session = SessionLocal()
    try:
        create_notification(
            db,
            user_id=user_id,
            category=category[:32],
            title=title[:255],
            message=message,
            priority=priority[:16] if priority else "normal",
            actor_id=actor_id,
            actor_name=actor_name[:255] if actor_name else None,
            link=link[:512] if link else None,
            extra=extra or {},
        )
        db.commit()
    except Exception as e:
        # The response is already on the wire; just log the failure
        logger.error(
            f"Background notification insert failed: user={user_id} cat={category} err={e}"
        )
    finally:
        db.close()
