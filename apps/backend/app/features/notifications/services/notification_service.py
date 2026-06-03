from datetime import datetime
from typing import List, Optional
from bson import ObjectId
from app.core.mongodb import get_mongodb


class NotificationService:
    collection = "notifications"

    async def create_notification(
        self,
        user_id: str,
        type: str,
        message: str,
        from_user: str,
        reference_id: Optional[str] = None,
    ) -> str:
        db = get_mongodb()
        doc = {
            "user_id": user_id,
            "type": type,
            "message": message,
            "from_user": from_user,
            "reference_id": reference_id,
            "is_read": False,
            "created_at": datetime.utcnow(),
        }
        result = await db[self.collection].insert_one(doc)
        return str(result.inserted_id)

    async def get_notifications(
        self,
        user_id: str,
        limit: int = 20,
        skip: int = 0,
    ) -> List[dict]:
        db = get_mongodb()
        cursor = (
            db[self.collection]
            .find({"user_id": user_id})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        notifications = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            notifications.append(doc)
        return notifications

    async def get_unread_count(self, user_id: str) -> int:
        db = get_mongodb()
        return await db[self.collection].count_documents(
            {"user_id": user_id, "is_read": False}
        )

    async def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        db = get_mongodb()
        try:
            oid = ObjectId(notification_id)
        except Exception:
            return False
        result = await db[self.collection].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {"is_read": True}},
        )
        return result.modified_count > 0

    async def mark_all_as_read(self, user_id: str) -> int:
        db = get_mongodb()
        result = await db[self.collection].update_many(
            {"user_id": user_id, "is_read": False},
            {"$set": {"is_read": True}},
        )
        return result.modified_count


notification_service = NotificationService()
