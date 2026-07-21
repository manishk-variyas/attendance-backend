from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List

from app.features.notifications.schemas.notification import (
    NotificationCreate,
    NotificationResponse,
    UnreadCountResponse,
)
from app.features.notifications.services.notification_service import (
    notification_service,
)
from app.features.auth.dependencies import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=List[NotificationResponse])
async def get_notifications(
    email: str = Query(..., description="User email to fetch notifications for"),
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Fetch notifications for a user.
    - Users can fetch their own notifications.
    - Admins can fetch anyone's notifications.
    """
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own notifications.",
        )
    return await notification_service.get_notifications(email, limit, skip)


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    email: str = Query(..., description="User email"),
    current_user: dict = Depends(get_current_user),
):
    """Get unread notification count for badge."""
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own notifications.",
        )
    count = await notification_service.get_unread_count(email)
    return {"count": count}


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    email: str = Query(..., description="User email"),
    current_user: dict = Depends(get_current_user),
):
    """Mark a single notification as read."""
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own notifications.",
        )
    success = await notification_service.mark_as_read(notification_id, email)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}


@router.put("/read-all")
async def mark_all_read(
    email: str = Query(..., description="User email"),
    current_user: dict = Depends(get_current_user),
):
    """Mark all notifications as read for a user."""
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own notifications.",
        )
    count = await notification_service.mark_all_as_read(email)
    return {"message": f"{count} notifications marked as read"}
