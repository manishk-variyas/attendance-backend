from datetime import datetime
from typing import List, Dict, Any
from fastapi import HTTPException
from app.core.mongodb import get_mongodb
from app.features.leaves.schemas.leaves import LeaveApplyRequest, LeaveStatus, LeaveStats, LeaveType
from bson import ObjectId

class LeaveService:
    def __init__(self):
        self.leaves_collection = "leaves"
        self.holidays_collection = "holidays"
        self.balances_collection = "leave_balances"

    @property
    def db(self):
        """Dynamically get the MongoDB database instance."""
        db = get_mongodb()
        if db is None:
            raise RuntimeError("MongoDB not initialized. Check if connect_to_mongo was called.")
        return db

    async def apply_for_leave(self, user_id: str, leave_data: LeaveApplyRequest) -> str:
        """Submit a new leave application with overlap protection."""
        
        # Check for overlapping leaves (Approved or Pending)
        overlap_query = {
            "user_id": user_id,
            "status": {"$in": [LeaveStatus.APPROVED, LeaveStatus.PENDING]},
            "start_date": {"$lte": leave_data.end_date},
            "end_date": {"$gte": leave_data.start_date}
        }
        
        existing_overlap = await self.db[self.leaves_collection].find_one(overlap_query)
        if existing_overlap:
            raise HTTPException(
                status_code=400, 
                detail="You already have an approved or pending leave application that overlaps with these dates."
            )

        leave_doc = {
            "user_id": user_id,
            "start_date": leave_data.start_date,
            "end_date": leave_data.end_date,
            "leave_type": leave_data.leave_type,
            "reason": leave_data.reason,
            "status": LeaveStatus.PENDING,
            "created_at": datetime.utcnow()
        }
        result = await self.db[self.leaves_collection].insert_one(leave_doc)
        return str(result.inserted_id)

    async def get_leave_history(self, user_id: str, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
        """Fetch leave history for a user."""
        cursor = self.db[self.leaves_collection].find({"user_id": user_id})\
            .sort("start_date", -1)\
            .skip(skip)\
            .limit(limit)
        
        history = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            history.append(doc)
        return history

    async def get_holidays(self) -> List[Dict[str, Any]]:
        """Fetch list of holidays."""
        cursor = self.db[self.holidays_collection].find().sort("date", 1)
        holidays = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            holidays.append(doc)
        return holidays

    async def get_leave_stats(self, user_id: str) -> Dict[str, Any]:
        """
        Calculate and fetch dashboard stats with used/total breakdown.
        """
        # 1. Fetch current balances (Total allocated)
        balance_doc = await self.db[self.balances_collection].find_one({"user_id": user_id})
        
        # 2. Count used leaves (Only Approved ones)
        pipeline = [
            {"$match": {"user_id": user_id, "status": LeaveStatus.APPROVED}},
            {"$group": {
                "_id": "$leave_type",
                "count": {"$sum": 1}
            }}
        ]
        used_cursor = self.db[self.leaves_collection].aggregate(pipeline)
        used_map = {doc["_id"]: float(doc["count"]) async for doc in used_cursor}

        # 3. Count pending applications
        pending_count = await self.db[self.leaves_collection].count_documents({
            "user_id": user_id,
            "status": LeaveStatus.PENDING
        })

        # Return stats in the format expected by frontend
        stats = {
            "total_earned": balance_doc.get("total_earned", 12.0) if balance_doc else 12.0,
            "used_earned": used_map.get(LeaveType.EL, 0.0),
            "total_paid": balance_doc.get("total_paid", 5.0) if balance_doc else 5.0,
            "used_paid": used_map.get(LeaveType.PL, 0.0),
            "total_unpaid": balance_doc.get("total_unpaid", 0.0) if balance_doc else 0.0,
            "used_unpaid": used_map.get(LeaveType.UPL, 0.0),
            "pending_applications": pending_count
        }
        return stats

leave_service = LeaveService()
