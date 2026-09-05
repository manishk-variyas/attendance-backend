"""Notifications API for the in-app notification feed.

Polling-based (not WebSocket — simpler, no extra infrastructure, fine for
employee-scale usage). The frontend is expected to poll every 30-60s
to update the bell badge and the notification list.

Endpoints:
  GET    /notifications?limit=50&offset=0&unread_only=false
        -> list + unread_count + total
  GET    /notifications/unread-count
        -> just the unread count (cheap, used for the badge poll)
  POST   /notifications/{id}/read
        -> mark one read
  POST   /notifications/read-all
        -> mark all read (returns count marked)
  POST   /notifications
        -> admin/manual broadcast to a single user
  DELETE /notifications/{id}
        -> hard-delete one (only if owned by caller)

All endpoints require an authenticated user. The user_id used for queries
is taken from the caller's session (not from the request body), so
users can only see/mark their own notifications.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user
from app.features.notifications.schemas import (
    NotificationCreateRequest,
    NotificationItem,
    NotificationListResponse,
)
from app.services.database.notification_service import (
    create_notification,
    delete_notification,
    list_for_user,
    mark_all_read,
    mark_read,
)
from sqlalchemy import select, func
from app.models.notification import Notification


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _current_user_id(current_user: dict, db: Session) -> uuid.UUID:
    """Resolve the caller's employee_master.id from the session.

    Tries the cached value first (added at login time, no DB hit).
    Falls back to a `keycloak_user_id` lookup if the cache is missing
    (e.g. for sessions created before the cache was added).
    """
    cached = current_user.get("employee_id")
    if cached:
        try:
            return uuid.UUID(cached)
        except (TypeError, ValueError):
            pass
    sub = current_user.get("sub") or current_user.get("user_id") or current_user.get("id")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    try:
        kc_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user id in session")
    from app.models.employee_master import EmployeeMaster
    emp = (
        db.query(EmployeeMaster)
        .filter(EmployeeMaster.keycloak_user_id == str(kc_id))
        .first()
    )
    if not emp:
        raise HTTPException(
            status_code=403,
            detail="No employee record linked to this login",
        )
    return emp.id


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    items = list_for_user(
        db, user_id=user_id, limit=limit, offset=offset, unread_only=unread_only
    )
    unread = count_for_user(db, user_id=user_id, unread_only=True)
    total = count_for_user(db, user_id=user_id, unread_only=False)
    return NotificationListResponse(
        items=[NotificationItem.model_validate(n) for n in items],
        unread_count=unread,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count")
def unread_count(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    return {"unread_count": count_for_user(db, user_id=user_id, unread_only=True)}


@router.post("/{notification_id}/read", status_code=status.HTTP_200_OK)
def mark_one_read(
    notification_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    if not mark_read(db, notification_id=notification_id, user_id=user_id):
        # Not found, already read, or not owned by the caller — all return 404
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/read-all", status_code=status.HTTP_200_OK)
def mark_everything_read(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    count = mark_all_read(db, user_id=user_id)
    return {"ok": True, "marked": count}


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_one(
    notification_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    if not delete_notification(db, notification_id=notification_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return None


@router.post("", response_model=NotificationItem, status_code=status.HTTP_201_CREATED)
def create_one(
    payload: NotificationCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only manual broadcast to a single user. Most notifications are
    created automatically by the system (e.g. on leave approval)."""
    roles = current_user.get("roles") or []
    if "Admin" not in roles:
        raise HTTPException(
            status_code=403,
            detail="Only admins can create arbitrary notifications",
        )
    # The session's `sub` is a Keycloak-level identifier, NOT the same as
    # employee_master.id. Look up the actor's employee_master.id by email so
    # the FK to employee_master.actor_id holds.
    from app.models.employee_master import EmployeeMaster
    actor_email = current_user.get("email")
    actor_emp_id = None
    if actor_email:
        emp = (
            db.query(EmployeeMaster)
            .filter(EmployeeMaster.user_email == actor_email)
            .first()
        )
        if emp:
            actor_emp_id = emp.id
    actor_name = current_user.get("name") or current_user.get("preferred_username") or actor_email
    n = create_notification(
        db,
        user_id=payload.user_id,
        category=payload.category,
        title=payload.title,
        message=payload.message,
        priority=payload.priority,
        actor_id=actor_emp_id,
        actor_name=actor_name,
        link=payload.link,
        metadata=payload.extra,
    )
    db.commit()
    db.refresh(n)
    return NotificationItem.model_validate(n)


def count_for_user(db: Session, *, user_id: uuid.UUID, unread_only: bool) -> int:
    """Count of notifications for a user. Uses the partial index
    `idx_notifications_user_unread` when unread_only=True, the regular
    index otherwise."""
    stmt = select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return int(db.execute(stmt).scalar() or 0)
