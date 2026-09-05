"""Pydantic schemas for the notifications API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    category: str
    title: str
    message: str
    priority: str
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    link: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict, alias="extra")
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    total: int
    limit: int
    offset: int


class NotificationCreateRequest(BaseModel):
    """Optional — only admins can create arbitrary notifications.
    The system creates most notifications internally (e.g. on leave approval,
    ticket assignment, etc.). This endpoint is for manual broadcasts."""

    user_id: UUID
    category: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    priority: str = Field(default="normal", max_length=16)
    link: Optional[str] = Field(default=None, max_length=512)
    extra: dict[str, Any] = Field(default_factory=dict)

