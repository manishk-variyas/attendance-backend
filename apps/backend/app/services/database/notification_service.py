from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func, update as sql_update
from app.services.database.base_service import BaseService
from app.models.notification import Notification
from datetime import datetime


class NotificationService(BaseService[Notification]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch_by_user(self, user_id: str, limit: int = 20, skip: int = 0) -> List[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at.desc()).offset(skip).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def get_unread_count(self, user_id: str) -> int:
        stmt = select(func.count(Notification.id)).where(Notification.user_id == user_id, Notification.is_read == False)
        return self.db.execute(stmt).scalar() or 0

    def mark_as_read(self, notification_id, user_id: str) -> bool:
        stmt = select(Notification).where(Notification.id == notification_id, Notification.user_id == user_id)
        notif = self.db.execute(stmt).scalars().first()
        if not notif:
            return False
        notif.is_read = True
        self.db.commit()
        return True

    def mark_all_as_read(self, user_id: str) -> int:
        stmt = select(Notification).where(Notification.user_id == user_id, Notification.is_read == False)
        notifs = list(self.db.execute(stmt).scalars().all())
        count = len(notifs)
        for n in notifs:
            n.is_read = True
        self.db.commit()
        return count
