from datetime import datetime, timezone, date
import io
from typing import List, Dict, Any
from zoneinfo import ZoneInfo
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
from app.models.shift import Shift
from app.models.attendance import Attendance
from sqlalchemy import select, and_, func

class LeaveBusinessService:
    def __init__(self, leave_db: LeaveDBService, holiday_db: HolidayDBService, balance_db: LeaveBalanceService):
        self.leave_db = leave_db
        self.holiday_db = holiday_db
        self.balance_db = balance_db

    async def apply_for_leave(self, user_id: str, email: str, leave_data: LeaveApplyRequest) -> str:
        """Submit a new leave application with overlap protection."""
        IST = ZoneInfo("Asia/Kolkata")
        from_ist = leave_data.start_date
        if from_ist.tzinfo is None:
            from_ist = from_ist.replace(tzinfo=timezone.utc)
        from_ist = from_ist.astimezone(IST)
        to_ist = leave_data.end_date
        if to_ist.tzinfo is None:
            to_ist = to_ist.replace(tzinfo=timezone.utc)
        to_ist = to_ist.astimezone(IST)

        start = from_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = to_ist.replace(hour=23, minute=59, second=59, microsecond=999999)

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

        # shift_stmt = select(Shift).where(
        #     Shift.keycloak_user_id == user_id,
        #     Shift.date >= start.date(),
        #     Shift.date <= end.date(),
        # )
        # conflicting_shift = self.leave_db.db.execute(shift_stmt).scalars().first()
        # if conflicting_shift:
        #     raise HTTPException(
        #         status_code=400,
        #         detail=f"Leave cannot be applied. A shift ({conflicting_shift.shift_code}) is already assigned on {conflicting_shift.date}.",
        #     )

        # Check sufficient leave balance before submitting
        requested_days = (end.date() - start.date()).days + 1
        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=user_id)

        if leave_data.leave_type == LeaveType.EL:
            total_earned = balance.total_earned if balance else 12.0
            used_earned = balance.used_earned if balance else 0.0
            remaining = total_earned - used_earned
            if remaining < requested_days:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient earned leave balance. Remaining: {remaining}, Requested: {requested_days}"
                )
        elif leave_data.leave_type == LeaveType.PL:
            accrued = balance.accrued_compoff if balance else 0.0
            consumed = balance.consumed_compoff if balance else 0.0
            remaining = accrued - consumed
            if remaining < requested_days:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient comp off balance. Remaining: {remaining}, Requested: {requested_days}"
                )

        # Create leave record
        leave_doc = {
            "keycloak_user_id": user_id,
            "user_email": email,
            "start_date": start.date(),
            "end_date": end.date(),
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

    def emergency_leave(self, user_id: str, email: str, reason: str = None) -> dict:
        today = date.today()

        existing_leave = self.leave_db.db.execute(
            select(Leave).where(
                Leave.keycloak_user_id == user_id,
                Leave.approval_status.in_([LeaveStatus.APPROVED.value, LeaveStatus.PENDING.value]),
                Leave.start_date <= today,
                Leave.end_date >= today,
            )
        ).scalars().first()
        if existing_leave:
            raise HTTPException(status_code=400, detail="Already on leave for today.")

        existing_attendance = self.leave_db.db.execute(
            select(Attendance).where(
                Attendance.keycloak_user_id == user_id,
                Attendance.attendance_date == today,
            )
        ).scalars().first()
        if existing_attendance:
            if existing_attendance.check_out_time:
                raise HTTPException(status_code=400, detail="Already completed work for today.")
            existing_attendance.check_out_time = datetime.now(timezone.utc)
            existing_attendance.remarks = (existing_attendance.remarks or "") + " | Emergency leave"
            self.leave_db.db.commit()

        shift = self.leave_db.db.execute(
            select(Shift).where(
                Shift.keycloak_user_id == user_id,
                Shift.date == today,
            )
        ).scalars().first()
        if shift:
            shift.work_location_status = "LEAVE"
            self.leave_db.db.commit()

        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=user_id)
        if not balance:
            self.balance_db.create(LeaveBalance,
                keycloak_user_id=user_id,
                year=today.year,
                total_earned=12.0,
                used_earned=1,
                accrued_compoff=0,
                consumed_compoff=0,
                unpaid=0,
            )
        else:
            balance.used_earned = (balance.used_earned or 0) + 1
            self.leave_db.db.commit()

        leave_doc = {
            "keycloak_user_id": user_id,
            "user_email": email,
            "start_date": today,
            "end_date": today,
            "leave_type": LeaveType.EL.value,
            "reason": reason or "Emergency leave",
            "approval_status": LeaveStatus.APPROVED.value,
        }
        new_leave = self.leave_db.create(Leave, **leave_doc)
        return {"message": "Emergency leave applied", "leave_id": str(new_leave.id)}

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
            "used_earned": balance_doc.used_earned if balance_doc else 0.0,
            "total_paid": balance_doc.accrued_compoff if balance_doc else 0.0,
            "used_paid": balance_doc.consumed_compoff if balance_doc else 0.0,
            "total_unpaid": 0,
            "used_unpaid": used_map.get(LeaveType.UPL.value, 0.0),
            "pending_applications": pending_count or 0
        }
        return stats

    async def approve_leave(self, leave_id: str, current_user: dict) -> bool:
        """
        Approve a leave application with balance validation.
        
        Checks:
        - EL (Earned Leave): total_earned - used_earned >= requested days
        - PL (Comp Off)    : accrued_compoff - consumed_compoff >= requested days
        - UPL (Unpaid)     : no balance check needed
        
        On approval, updates leave_balances to consume the leave credit.
        """
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

        # ── Calculate requested leave days ─────────────────────────────────
        requested_days = (leave.end_date - leave.start_date).days + 1
        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=leave.keycloak_user_id)

        # ── Balance check by leave type ────────────────────────────────────
        leave_type = leave.leave_type
        detail = None

        if leave_type == LeaveType.EL.value:
            # Earned Leave: check remaining balance
            total_earned = balance.total_earned if balance else 12.0
            used_earned = balance.used_earned if balance else 0.0
            remaining = total_earned - used_earned
            if remaining < requested_days:
                detail = f"Insufficient earned leave balance. Remaining: {remaining}, Requested: {requested_days}"

        elif leave_type == LeaveType.PL.value:
            # Comp Off / Paid Leave: check accrued - consumed
            accrued = balance.accrued_compoff if balance else 0.0
            consumed = balance.consumed_compoff if balance else 0.0
            remaining = accrued - consumed
            if remaining < requested_days:
                detail = f"Insufficient comp off balance. Remaining: {remaining}, Requested: {requested_days}"

        # UPL: no balance check needed, always passes

        if detail:
            raise HTTPException(status_code=400, detail=detail)

        # ── Mark leave as approved ─────────────────────────────────────────
        now = datetime.utcnow()
        actor_email = current_user.get("email")
        
        self.leave_db.update(
            Leave, leave_id, 
            approval_status=LeaveStatus.APPROVED.value, 
            updated_at=now, 
            approved_at=now, 
            approved_by=actor_email,
        )

        # ── Update leave balance (consume credit) ──────────────────────────
        if balance:
            if leave_type == LeaveType.EL.value:
                self.balance_db.update(LeaveBalance, balance.id, used_earned=(balance.used_earned or 0) + requested_days, updated_at=now)
            elif leave_type == LeaveType.PL.value:
                self.balance_db.update(LeaveBalance, balance.id, consumed_compoff=(balance.consumed_compoff or 0) + requested_days, updated_at=now)

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
