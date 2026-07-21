from datetime import datetime
from typing import List, Optional
from app.core.database import SessionLocal
from app.services.database.notification_service import NotificationService as NotificationDBService
from app.models.notification import Notification


class NotificationService:

    async def create_notification(
        self,
        user_id: str,
        type: str,
        message: str,
        from_user: str,
        reference_id: Optional[str] = None,
    ) -> str:
        db = SessionLocal()
        try:
            svc = NotificationDBService(db)
            notif = svc.create(Notification,
                user_id=user_id,
                type=type,
                message=message,
                from_user=from_user,
                reference_id=reference_id,
            )
            return str(notif.id)
        finally:
            db.close()

    async def get_notifications(
        self,
        user_id: str,
        limit: int = 20,
        skip: int = 0,
    ) -> List[dict]:
        db = SessionLocal()
        try:
            svc = NotificationDBService(db)
            notifications = svc.fetch_by_user(user_id, limit, skip)
            return [
                {
                    "id": str(n.id),
                    "user_id": n.user_id,
                    "type": n.type,
                    "message": n.message,
                    "from_user": n.from_user,
                    "reference_id": n.reference_id,
                    "is_read": n.is_read,
                    "created_at": n.created_at,
                }
                for n in notifications
            ]
        finally:
            db.close()

    async def get_unread_count(self, user_id: str) -> int:
        db = SessionLocal()
        try:
            svc = NotificationDBService(db)
            return svc.get_unread_count(user_id)
        finally:
            db.close()

    async def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        db = SessionLocal()
        try:
            svc = NotificationDBService(db)
            return svc.mark_as_read(notification_id, user_id)
        finally:
            db.close()

    async def mark_all_as_read(self, user_id: str) -> int:
        db = SessionLocal()
        try:
            svc = NotificationDBService(db)
            return svc.mark_all_as_read(user_id)
        finally:
            db.close()


notification_service = NotificationService()
