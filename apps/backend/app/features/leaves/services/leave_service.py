from datetime import datetime
import io
from typing import List, Dict, Any
from fastapi import HTTPException
from pymongo import UpdateOne
import openpyxl
from pydantic import ValidationError
from app.core.mongodb import get_mongodb
from app.features.leaves.schemas.leaves import LeaveApplyRequest, LeaveStatus, LeaveStats, LeaveType, Holiday
from app.features.redmine.service import redmine_service
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

    async def apply_for_leave(self, user_id: str, email: str, leave_data: LeaveApplyRequest) -> str:
        """Submit a new leave application with overlap protection."""
        
        # Normalise dates to start-of-day (00:00:00) so time components dont
        # cause false negatives when comparing same calendar dates.
        start = leave_data.start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = leave_data.end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

        # OLD approach — used raw user-supplied datetimes which could
        # contain non-midnight time components, allowing duplicate
        # applications for the same day.
        #
        # overlap_query = {
        #     "user_id": user_id,
        #     "status": {"$in": [LeaveStatus.APPROVED, LeaveStatus.PENDING]},
        #     "start_date": {"$lte": leave_data.end_date},
        #     "end_date": {"$gte": leave_data.start_date}
        # }

        overlap_query = {
            "user_id": user_id,
            "status": {"$in": [LeaveStatus.APPROVED, LeaveStatus.PENDING]},
            "start_date": {"$lte": end},
            "end_date": {"$gte": start}
        }
        
        existing_overlap = await self.db[self.leaves_collection].find_one(overlap_query)
        if existing_overlap:
            raise HTTPException(
                status_code=400, 
                detail="You already have an approved or pending leave application that overlaps with these dates."
            )

        leave_doc = {
            "user_id": user_id,
            "user_email": email,
            "start_date": leave_data.start_date,
            "end_date": leave_data.end_date,
            "leave_type": leave_data.leave_type,
            "reason": leave_data.comment or leave_data.reason,
            "comment": leave_data.comment,
            "is_traveling": leave_data.is_traveling,
            "contact_number": leave_data.contact_number,
            "resuming_date": leave_data.resuming_date,
            "leave_dates": leave_data.leave_dates,
            "approver_id": leave_data.approver_id,
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

    async def get_user_leave_history(self, email: str, current_user: dict, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
        """Get leave history for a specific user. PMs scoped to shared projects."""
        roles = current_user.get("roles", [])

        # Resolve target user
        tr_user = await redmine_service.get_user_by_email(email)
        if not tr_user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")

        # PM scope check
        if "Admin" not in roles:
            pm_email = current_user.get("email")
            pm_user = await redmine_service.get_user_by_email(pm_email)
            if not pm_user:
                raise HTTPException(status_code=403, detail="Unauthorized")

            pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
            pm_project_ids = {p.id for p in pm_projects}
            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}

            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(status_code=403, detail="You can only view leaves for users in your projects.")

        # Fetch leave history from MongoDB using email
        cursor = self.db[self.leaves_collection].find({"user_email": email})\
            .sort("start_date", -1).skip(skip).limit(limit)

        history = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            history.append(doc)
        return history

    async def get_visible_leave_users(self, current_user: dict) -> list:
        """Return users whose leaves the current user can view."""
        roles = current_user.get("roles", [])
        all_users = await redmine_service.get_all_users()

        if "Admin" in roles:
            return all_users

        # PM scoping
        pm_email = current_user.get("email")
        pm_user = await redmine_service.get_user_by_email(pm_email)
        if not pm_user:
            return []

        pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
        pm_project_ids = {p.id for p in pm_projects}

        visible = []
        for u in all_users:
            tr_user = await redmine_service.get_user_by_email(u["email"])
            if not tr_user:
                continue
            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}
            if pm_project_ids.intersection(tr_project_ids):
                visible.append(u)

        return visible

    async def get_holidays(self, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
        """Fetch paginated list of holidays."""
        cursor = self.db[self.holidays_collection].find().sort("holiday_date", 1).skip(skip).limit(limit)
        holidays = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            holidays.append(doc)
        return holidays

    async def upload_holidays_from_excel(self, file_content: bytes) -> dict:
        """Parse Excel file and bulk upsert holidays."""
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
            sheet = wb.active
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid Excel file: {str(e)}")

        headers = [cell.value for cell in sheet[1]]
        expected_headers = ["country_code", "region", "holiday_date", "holiday_name", "holiday_type", "is_national"]
        
        # Verify headers (case-insensitive check for simplicity, or just map by index if strict)
        # We will map by index assuming the order or strict header names
        # Let's map dynamically to be safe
        header_map = {h: i for i, h in enumerate(headers) if h in expected_headers}
        if not all(eh in header_map for eh in expected_headers):
            raise HTTPException(status_code=400, detail=f"Missing expected headers. Required: {expected_headers}")

        operations = []
        parsed_holidays = []
        seen_holidays = set()

        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):  # Skip empty rows
                continue
                
            try:
                # Extract values based on header map
                raw_date = row[header_map["holiday_date"]]
                if isinstance(raw_date, datetime):
                    holiday_date_str = raw_date.strftime("%Y-%m-%d")
                else:
                    holiday_date_str = str(raw_date).strip()

                is_national_val = row[header_map["is_national"]]
                if isinstance(is_national_val, str):
                    is_national = is_national_val.lower() in ["true", "1", "yes"]
                else:
                    is_national = bool(is_national_val)

                country_code = str(row[header_map["country_code"]]).strip()
                holiday_name = str(row[header_map["holiday_name"]]).strip()

                key = (country_code, holiday_date_str)
                if key in seen_holidays:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Duplicate holiday on row {row_idx}: '{holiday_name}' on {holiday_date_str} for {country_code} already exists in the file."
                    )
                seen_holidays.add(key)

                holiday_data = {
                    "country_code": country_code,
                    "region": str(row[header_map["region"]]).strip() if row[header_map["region"]] else None,
                    "holiday_date": holiday_date_str,
                    "holiday_name": holiday_name,
                    "holiday_type": str(row[header_map["holiday_type"]]).strip().upper(),
                    "is_national": is_national
                }
                
                # Validate with Pydantic
                holiday_obj = Holiday(**holiday_data)
                parsed_holidays.append(holiday_obj.model_dump())
            except ValidationError as e:
                invalid_cols = [str(err["loc"][0]) for err in e.errors() if err.get("loc")]
                err_msg = f"Row {row_idx} has invalid data in column(s): {', '.join(invalid_cols)}"
                raise HTTPException(status_code=400, detail=err_msg)
            except Exception as e:
                # If a row fails validation, we can either skip or fail the whole upload. Let's fail fast.
                raise HTTPException(status_code=400, detail=f"Error parsing row {row_idx}: {str(e)}")

        if not parsed_holidays:
            raise HTTPException(status_code=400, detail="No valid holiday data found in the Excel file.")

        # Create bulk operations (Upsert based on country_code and holiday_date)
        for h in parsed_holidays:
            operations.append(
                UpdateOne(
                    {
                        "country_code": h["country_code"],
                        "holiday_date": h["holiday_date"]
                    },
                    {"$set": h},
                    upsert=True
                )
            )

        if operations:
            result = await self.db[self.holidays_collection].bulk_write(operations)
            return {
                "message": "Holidays uploaded successfully",
                "inserted": result.upserted_count,
                "modified": result.modified_count,
                "total_processed": len(parsed_holidays)
            }
        
        return {"message": "No operations performed"}

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

    async def approve_leave(self, leave_id: str, current_user: dict) -> bool:
        """Approve a leave application. PMs can only approve leaves of resources in their projects."""
        try:
            oid = ObjectId(leave_id)
        except Exception:
            return False

        leave = await self.db[self.leaves_collection].find_one({"_id": oid})
        if not leave:
            return False

        # If not admin, verify PM permissions
        roles = current_user.get("roles", [])
        if "Admin" not in roles:
            # PM check
            pm_email = current_user.get("email")
            pm_user = await redmine_service.get_user_by_email(pm_email)
            if not pm_user:
                return False
                
            pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
            pm_project_ids = {p.id for p in pm_projects}

            # Get target user (TR) from leave document
            tr_email = leave.get("user_email")
            if not tr_email:
                # Fallback: if user_email not stored, we cannot verify project scope for PM
                return False
                
            tr_user = await redmine_service.get_user_by_email(tr_email)
            if not tr_user:
                return False
                
            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}

            # Check if PM and TR share at least one project
            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(status_code=403, detail="You can only approve leaves for resources in your projects.")

        # Update leave status to approved
        now = datetime.utcnow()
        actor_email = current_user.get("email")
        actor_roles = current_user.get("roles", [])
        actor_role = "Admin" if "Admin" in actor_roles else "Project Manager"
        result = await self.db[self.leaves_collection].update_one(
            {"_id": oid},
            {"$set": {"status": LeaveStatus.APPROVED, "updated_at": now, "approved_at": now, "approved_by": actor_email, "approved_by_role": actor_role}}
        )
        return result.modified_count > 0

    async def reject_leave(self, leave_id: str, current_user: dict) -> bool:
        """Reject a leave application. PMs can only reject leaves of resources in their projects."""
        try:
            oid = ObjectId(leave_id)
        except Exception:
            return False

        leave = await self.db[self.leaves_collection].find_one({"_id": oid})
        if not leave:
            return False

        # If not admin, verify PM permissions
        roles = current_user.get("roles", [])
        if "Admin" not in roles:
            pm_email = current_user.get("email")
            pm_user = await redmine_service.get_user_by_email(pm_email)
            if not pm_user:
                return False

            pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
            pm_project_ids = {p.id for p in pm_projects}

            tr_email = leave.get("user_email")
            if not tr_email:
                return False

            tr_user = await redmine_service.get_user_by_email(tr_email)
            if not tr_user:
                return False

            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}

            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(status_code=403, detail="You can only reject leaves for resources in your projects.")

        # Update leave status to rejected
        now = datetime.utcnow()
        actor_email = current_user.get("email")
        actor_roles = current_user.get("roles", [])
        actor_role = "Admin" if "Admin" in actor_roles else "Project Manager"
        result = await self.db[self.leaves_collection].update_one(
            {"_id": oid},
            {"$set": {"status": LeaveStatus.REJECTED, "updated_at": now, "rejected_at": now, "rejected_by": actor_email, "rejected_by_role": actor_role}}
        )
        return result.modified_count > 0

    async def get_pending_leaves(self, current_user: dict) -> List[Dict[str, Any]]:
        """Fetch all pending leaves. PMs see only leaves assigned to them via approver_id."""
        roles = current_user.get("roles", [])

        if "Admin" in roles:
            cursor = self.db[self.leaves_collection].find({"status": LeaveStatus.PENDING}).sort("created_at", -1)
            all_pending = []
            async for doc in cursor:
                doc["id"] = str(doc.pop("_id"))
                all_pending.append(doc)
            return all_pending

        # PM scoping by approver_id
        pm_user = await redmine_service.get_user_by_email(current_user.get("email"))
        if not pm_user:
            return []

        cursor = self.db[self.leaves_collection].find({
            "status": LeaveStatus.PENDING,
            "approver_id": pm_user["id"]
        }).sort("created_at", -1)

        filtered = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            filtered.append(doc)

        return filtered

leave_service = LeaveService()
