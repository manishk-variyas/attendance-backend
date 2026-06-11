from datetime import datetime
import io
from typing import List, Dict, Any
from fastapi import HTTPException
import openpyxl
from pydantic import ValidationError

from app.features.leaves.schemas.leaves import LeaveApplyRequest, LeaveStatus, LeaveType, Holiday as HolidaySchema
from app.features.redmine.service import redmine_service
from app.services.database.leave_service import LeaveService as LeaveDBService, LeaveBalanceService
from app.services.database.holiday_service import HolidayService as HolidayDBService
from app.models.leave import Leave
from app.models.leave_balance import LeaveBalance
from app.models.holiday import Holiday
from sqlalchemy import select, and_, func

class LeaveBusinessService:
    def __init__(self, leave_db: LeaveDBService, holiday_db: HolidayDBService, balance_db: LeaveBalanceService):
        self.leave_db = leave_db
        self.holiday_db = holiday_db
        self.balance_db = balance_db

    async def apply_for_leave(self, user_id: str, email: str, leave_data: LeaveApplyRequest) -> str:
        """Submit a new leave application with overlap protection."""
        start = leave_data.start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = leave_data.end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Check for overlaps
        stmt = select(Leave).where(
            Leave.keycloak_user_id == user_id,
            Leave.approval_status.in_([LeaveStatus.APPROVED.value, LeaveStatus.PENDING.value]),
            Leave.start_date <= end.date(),
            Leave.end_date >= start.date()
        )
        existing_overlap = self.leave_db.db.execute(stmt).scalars().first()
        
        if existing_overlap:
            raise HTTPException(
                status_code=400, 
                detail="You already have an approved or pending leave application that overlaps with these dates."
            )

        # Create leave record
        leave_doc = {
            "keycloak_user_id": user_id,
            "user_email": email,
            "start_date": leave_data.start_date.date(),
            "end_date": leave_data.end_date.date(),
            "leave_type": leave_data.leave_type.value,
            "reason": leave_data.comment or leave_data.reason,
            "comment": leave_data.comment,
            "is_traveling": leave_data.is_traveling,
            "contact_number": leave_data.contact_number,
            "approver_id": leave_data.approver_id,
            "approval_status": LeaveStatus.PENDING.value,
        }
        
        new_leave = self.leave_db.create(Leave, **leave_doc)
        return str(new_leave.id)

    async def get_leave_history(self, user_id: str, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
        """Fetch leave history for a user."""
        stmt = select(Leave).where(Leave.keycloak_user_id == user_id).order_by(Leave.start_date.desc()).offset(skip).limit(limit)
        results = self.leave_db.db.execute(stmt).scalars().all()
        return [r.to_dict() for r in results]

    async def get_user_leave_history(self, email: str, current_user: dict, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
        """Get leave history for a specific user. PMs scoped to shared projects."""
        roles = current_user.get("roles", [])

        tr_user = await redmine_service.get_user_by_email(email)
        if not tr_user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")

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

        stmt = select(Leave).where(Leave.user_email == email).order_by(Leave.start_date.desc()).offset(skip).limit(limit)
        results = self.leave_db.db.execute(stmt).scalars().all()
        return [r.to_dict() for r in results]

    async def get_visible_leave_users(self, current_user: dict) -> list:
        """Return users whose leaves the current user can view."""
        roles = current_user.get("roles", [])
        all_users = await redmine_service.get_all_users()

        if "Admin" in roles:
            return all_users

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
        stmt = select(Holiday).order_by(Holiday.holiday_date.asc()).offset(skip).limit(limit)
        results = self.holiday_db.db.execute(stmt).scalars().all()
        return [r.to_dict() for r in results]

    async def upload_holidays_from_excel(self, file_content: bytes) -> dict:
        """Parse Excel file and bulk upsert holidays."""
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
            sheet = wb.active
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid Excel file: {str(e)}")

        headers = [cell.value for cell in sheet[1]]
        expected_headers = ["country_code", "region", "holiday_date", "holiday_name", "holiday_type", "is_national"]
        
        header_map = {h: i for i, h in enumerate(headers) if h in expected_headers}
        if not all(eh in header_map for eh in expected_headers):
            raise HTTPException(status_code=400, detail=f"Missing expected headers. Required: {expected_headers}")

        parsed_holidays = []
        seen_holidays = set()

        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
                
            try:
                raw_date = row[header_map["holiday_date"]]
                if isinstance(raw_date, datetime):
                    holiday_date_str = raw_date.date()
                else:
                    holiday_date_str = datetime.strptime(str(raw_date).strip(), "%Y-%m-%d").date()

                is_national_val = row[header_map["is_national"]]
                if isinstance(is_national_val, str):
                    is_national = is_national_val.lower() in ["true", "1", "yes"]
                else:
                    is_national = bool(is_national_val)

                country_code = str(row[header_map["country_code"]]).strip()
                holiday_name = str(row[header_map["holiday_name"]]).strip()

                key = (country_code, str(holiday_date_str))
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
                
                HolidaySchema(**holiday_data)
                parsed_holidays.append(holiday_data)
            except ValidationError as e:
                invalid_cols = [str(err["loc"][0]) for err in e.errors() if err.get("loc")]
                raise HTTPException(status_code=400, detail=f"Row {row_idx} has invalid data in column(s): {', '.join(invalid_cols)}")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Error parsing row {row_idx}: {str(e)}")

        if not parsed_holidays:
            raise HTTPException(status_code=400, detail="No valid holiday data found in the Excel file.")

        # Upsert
        inserted = 0
        modified = 0
        for h in parsed_holidays:
            existing = self.holiday_db.fetch_one(Holiday, country_code=h["country_code"], holiday_date=h["holiday_date"])
            if existing:
                self.holiday_db.update(Holiday, existing.id, **h)
                modified += 1
            else:
                self.holiday_db.create(Holiday, **h)
                inserted += 1

        return {
            "message": "Holidays uploaded successfully",
            "inserted": inserted,
            "modified": modified,
            "total_processed": len(parsed_holidays)
        }

    async def get_leave_stats(self, user_id: str) -> Dict[str, Any]:
        """Calculate and fetch dashboard stats with used/total breakdown."""
        balance_doc = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=user_id)
        
        stmt = select(Leave.leave_type, func.count(Leave.id)).where(
            Leave.keycloak_user_id == user_id, 
            Leave.approval_status == LeaveStatus.APPROVED.value
        ).group_by(Leave.leave_type)
        
        used_cursor = self.leave_db.db.execute(stmt).all()
        used_map = {row[0]: float(row[1]) for row in used_cursor}

        stmt_pending = select(func.count(Leave.id)).where(
            Leave.keycloak_user_id == user_id,
            Leave.approval_status == LeaveStatus.PENDING.value
        )
        pending_count = self.leave_db.db.execute(stmt_pending).scalar()

        stats = {
            "total_earned": balance_doc.total_earned if balance_doc else 12.0,
            "used_earned": used_map.get(LeaveType.EL.value, 0.0),
            "total_paid": (balance_doc.unpaid if balance_doc else 0.0) + (balance_doc.total_earned if balance_doc else 12.0),
            "used_paid": used_map.get(LeaveType.PL.value, 0.0),
            "total_unpaid": balance_doc.unpaid if balance_doc else 0.0,
            "used_unpaid": used_map.get(LeaveType.UPL.value, 0.0),
            "pending_applications": pending_count or 0
        }
        return stats

    async def approve_leave(self, leave_id: str, current_user: dict) -> bool:
        """Approve a leave application."""
        leave = self.leave_db.fetch_one(Leave, id=leave_id)
        if not leave:
            return False

        roles = current_user.get("roles", [])
        if "Admin" not in roles:
            pm_email = current_user.get("email")
            pm_user = await redmine_service.get_user_by_email(pm_email)
            if not pm_user:
                return False
                
            pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
            pm_project_ids = {p.id for p in pm_projects}

            tr_email = leave.user_email
            if not tr_email:
                return False
                
            tr_user = await redmine_service.get_user_by_email(tr_email)
            if not tr_user:
                return False
                
            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}

            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(status_code=403, detail="You can only approve leaves for resources in your projects.")

        now = datetime.utcnow()
        actor_email = current_user.get("email")
        
        self.leave_db.update(
            Leave, leave_id, 
            approval_status=LeaveStatus.APPROVED.value, 
            updated_at=now, 
            approved_at=now, 
            approved_by=actor_email,
        )
        return True

    async def reject_leave(self, leave_id: str, current_user: dict) -> bool:
        """Reject a leave application."""
        leave = self.leave_db.fetch_one(Leave, id=leave_id)
        if not leave:
            return False

        roles = current_user.get("roles", [])
        if "Admin" not in roles:
            pm_email = current_user.get("email")
            pm_user = await redmine_service.get_user_by_email(pm_email)
            if not pm_user:
                return False

            pm_projects = await redmine_service.get_projects_for_user(pm_user["id"])
            pm_project_ids = {p.id for p in pm_projects}

            tr_email = leave.user_email
            if not tr_email:
                return False

            tr_user = await redmine_service.get_user_by_email(tr_email)
            if not tr_user:
                return False

            tr_projects = await redmine_service.get_projects_for_user(tr_user["id"])
            tr_project_ids = {p.id for p in tr_projects}

            if not pm_project_ids.intersection(tr_project_ids):
                raise HTTPException(status_code=403, detail="You can only reject leaves for resources in your projects.")

        now = datetime.utcnow()
        actor_email = current_user.get("email")
        
        self.leave_db.update(
            Leave, leave_id, 
            approval_status=LeaveStatus.REJECTED.value, 
            updated_at=now, 
            rejected_at=now, 
            rejected_by=actor_email,
        )
        return True

    async def get_pending_leaves(self, current_user: dict) -> List[Dict[str, Any]]:
        """Fetch all pending leaves."""
        roles = current_user.get("roles", [])

        if "Admin" in roles:
            stmt = select(Leave).where(Leave.approval_status == LeaveStatus.PENDING.value).order_by(Leave.created_at.desc())
            results = self.leave_db.db.execute(stmt).scalars().all()
            return [r.to_dict() for r in results]

        pm_user = await redmine_service.get_user_by_email(current_user.get("email"))
        if not pm_user:
            return []

        stmt = select(Leave).where(
            Leave.approval_status == LeaveStatus.PENDING.value,
            Leave.approver_id == int(pm_user["id"])
        ).order_by(Leave.created_at.desc())
        
        results = self.leave_db.db.execute(stmt).scalars().all()
        return [r.to_dict() for r in results]
