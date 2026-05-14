import logging
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId

logger = logging.getLogger(__name__)


def _serialize_shift(doc: dict) -> dict:
    """Convert MongoDB document to a JSON-serialisable dict."""
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    return doc


class ShiftService:
    """Business logic for shift management stored in MongoDB."""

    # ------------------------------------------------------------------ #
    #  Create                                                              #
    # ------------------------------------------------------------------ #
    async def create_shift(self, db, data: dict) -> dict:
        now = datetime.now(timezone.utc)
        data["createdAt"] = now
        data["updatedAt"] = now

        result = await db.shifts.insert_one(data)
        created = await db.shifts.find_one({"_id": result.inserted_id})
        return _serialize_shift(created)

    # ------------------------------------------------------------------ #
    #  Read helpers                                                        #
    # ------------------------------------------------------------------ #
    async def get_shift_by_id(self, db, shift_id: str) -> Optional[dict]:
        try:
            doc = await db.shifts.find_one({"_id": ObjectId(shift_id)})
            return _serialize_shift(doc)
        except Exception as e:
            logger.error(f"get_shift_by_id error: {e}")
            return None

    async def get_shifts_by_user_id(self, db, user_id: int) -> List[dict]:
        cursor = db.shifts.find({"userId": user_id}).sort("createdAt", -1)
        return [_serialize_shift(doc) async for doc in cursor]

    async def get_shifts_by_email(self, db, email: str) -> List[dict]:
        cursor = db.shifts.find({"userEmail": email}).sort("createdAt", -1)
        return [_serialize_shift(doc) async for doc in cursor]

    async def get_current_shift(self, db, user_id: int) -> Optional[dict]:
        """Most recent shift for user (regardless of status)."""
        doc = await db.shifts.find_one(
            {"userId": user_id},
            sort=[("createdAt", -1)]
        )
        return _serialize_shift(doc)

    async def get_active_shift(self, db, user_id: int) -> Optional[dict]:
        """Shift where workStatus is ACTIVE or shiftEndUTC is in the future."""
        now = datetime.now(timezone.utc)
        doc = await db.shifts.find_one(
            {
                "userId": user_id,
                "$or": [
                    {"workStatus": "ACTIVE"},
                    {"shiftEndUTC": {"$gt": now}},
                ],
            },
            sort=[("createdAt", -1)],
        )
        return _serialize_shift(doc)

    async def get_stats(self, db) -> dict:
        total = await db.shifts.count_documents({})
        active = await db.shifts.count_documents({"workStatus": "ACTIVE"})
        leave = await db.shifts.count_documents({"workStatus": "LEAVE"})
        # Count distinct users
        users = await db.shifts.distinct("userId")
        return {
            "total_shifts": total,
            "active_shifts": active,
            "completed_shifts": total - active - leave,
            "leave_shifts": leave,
            "users_with_shifts": len(users),
        }

    

    async def get_shift_history_by_email(self, db, email: str, limit: int = 10, skip: int = 0) -> List[dict]:
        """Fetch shift history for a user by email."""
        cursor = db.shifts.find({"userEmail": email}).sort("createdAt", -1).skip(skip).limit(limit)
        
        history = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            history.append(doc)
        return history

    # ------------------------------------------------------------------ #
    #  Update                                                              #
    # ------------------------------------------------------------------ #
    async def update_shift(self, db, shift_id: str, data: dict) -> Optional[dict]:
        try:
            data["updatedAt"] = datetime.now(timezone.utc)
            result = await db.shifts.update_one(
                {"_id": ObjectId(shift_id)},
                {"$set": data},
            )
            if result.matched_count == 0:
                return None
            return await self.get_shift_by_id(db, shift_id)
        except Exception as e:
            logger.error(f"update_shift error: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Delete                                                              #
    # ------------------------------------------------------------------ #
    async def delete_shift(self, db, shift_id: str) -> bool:
        try:
            result = await db.shifts.delete_one({"_id": ObjectId(shift_id)})
            return result.deleted_count == 1
        except Exception as e:
            logger.error(f"delete_shift error: {e}")
            return False


shift_service = ShiftService()
