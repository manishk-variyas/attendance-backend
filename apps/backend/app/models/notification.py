"""Notification model for in-app user notifications.

Each row is a notification delivered to a single user (the recipient). The
schema is intentionally simple:

  - user_id     : recipient (FK to employee_master, the user table)
  - category    : short tag like "leave_approved", "ticket_assigned", "wfh_requested"
  - title       : short heading shown in the UI
  - message     : 1-3 line body
  - priority    : "low" | "normal" | "high" (default "normal")
  - actor_id    : who triggered the notification (FK to employee_master, optional)
  - actor_name  : denormalized snapshot of the actor's name (for fast display)
  - link        : optional URL the user can click to take action
  - metadata    : JSONB blob for per-category extras (ticket_id, comment_id, etc.)
  - read_at     : when the user marked it read (null = unread)
  - created_at  : when the notification was created

Use the partial index `idx_notifications_user_unread` (WHERE read_at IS NULL)
for the unread badge query; use `idx_notifications_user_created` for the
"show me my last 50 notifications" history query.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.models import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notifications_user_created", "user_id", "created_at"),
        Index(
            "idx_notifications_user_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("employee_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String(32), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(String(16), nullable=False, server_default=text("'normal'"))
    actor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("employee_master.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_name = Column(String(255), nullable=True)
    link = Column(String(512), nullable=True)
    extra = Column("extra", JSONB, nullable=False, server_default=text("'{}'::jsonb"), key="extra")
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "priority": self.priority,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_name": self.actor_name,
            "link": self.link,
            "metadata": self.extra or {},
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
