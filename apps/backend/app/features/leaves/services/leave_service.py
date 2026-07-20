from datetime import datetime, timezone, date, timedelta
import io
from typing import List, Dict, Any
from zoneinfo import ZoneInfo
from fastapi import HTTPException
import openpyxl
from pydantic import ValidationError

from app.features.leaves.schemas.leaves import LeaveApplyRequest, LeaveStatus, LeaveType, Holiday as HolidaySchema
from app.features.redmine.sql_service import RedmineSQLService
from app.services.database.leave_service import LeaveService as LeaveDBService, LeaveBalanceService
from app.services.database.holiday_service import HolidayService as HolidayDBService
from app.models.leave import Leave
from app.models.leave_balance import LeaveBalance
from app.models.holiday import Holiday
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.system_setting import SystemSetting
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from sqlalchemy import select, and_, or_, func, insert

class LeaveBusinessService:
    def __init__(self, leave_db: LeaveDBService, holiday_db: HolidayDBService, balance_db: LeaveBalanceService):
        self.leave_db = leave_db
        self.holiday_db = holiday_db
        self.balance_db = balance_db

    async def validate_leave_dates(self, start_date: datetime, end_date: datetime, user_id: str = None, leave_dates: list = None) -> dict:
        IST = ZoneInfo("Asia/Kolkata")

        def _to_ist_date(dt):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).date()

        if leave_dates:
            dates = sorted({_to_ist_date(d) for d in leave_dates})
            if not dates:
                return {"valid": False, "total_days": 0, "working_days": 0, "weekends": 0, "holidays": 0, "warnings": []}
            sd = dates[0]
            ed = dates[-1]
        else:
            sd = _to_ist_date(start_date)
            ed = _to_ist_date(end_date)
            dates = None

        holidays_in_range = self.leave_db.db.execute(
            select(Holiday).where(Holiday.holiday_date.between(sd, ed))
        ).scalars().all()
        holiday_map = {h.holiday_date: h.holiday_name for h in holidays_in_range}

        warnings = []
        weekend_count = 0
        holiday_count = 0
        no_shift_count = 0

        shift_dates = set()
        if user_id:
            shifts = self.leave_db.db.execute(
                select(Shift.date).where(
                    Shift.keycloak_user_id == user_id,
                    Shift.date.between(sd, ed),
                )
            ).scalars().all()
            shift_dates = set(shifts)

        if leave_dates:
            total_days = len(dates)
            for d in dates:
                if d.weekday() == 6:
                    warnings.append({"date": d.isoformat(), "type": "weekend", "label": "Sunday"})
                    weekend_count += 1
                elif d in holiday_map:
                    warnings.append({"date": d.isoformat(), "type": "holiday", "label": holiday_map[d]})
                    holiday_count += 1
                elif shift_dates and d not in shift_dates:
                    warnings.append({"date": d.isoformat(), "type": "no_shift", "label": "No shift assigned"})
                    no_shift_count += 1
        else:
            total_days = (ed - sd).days + 1
            current = sd
            while current <= ed:
                if current.weekday() == 6:
                    warnings.append({"date": current.isoformat(), "type": "weekend", "label": "Sunday"})
                    weekend_count += 1
                elif current in holiday_map:
                    warnings.append({"date": current.isoformat(), "type": "holiday", "label": holiday_map[current]})
                    holiday_count += 1
                elif shift_dates and current not in shift_dates:
                    warnings.append({"date": current.isoformat(), "type": "no_shift", "label": "No shift assigned"})
                    no_shift_count += 1
                current += timedelta(days=1)

        working_days = total_days - weekend_count - holiday_count
        return {
            "valid": working_days > 0,
            "total_days": total_days,
            "working_days": working_days,
            "weekends": weekend_count,
            "holidays": holiday_count,
            "no_shift": no_shift_count,
            "warnings": warnings,
        }

    async def apply_for_leave(self, user_id: str, email: str, leave_data: LeaveApplyRequest) -> dict:
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

        # Determine requested days and overlap check dates
        if leave_data.leave_dates:
            target_dates = [d.astimezone(IST).date() if d.tzinfo else d.replace(tzinfo=timezone.utc).astimezone(IST).date() for d in leave_data.leave_dates]
            requested_days = len(target_dates)
            overlap_start = min(target_dates)
            overlap_end = max(target_dates)
        else:
            target_dates = None
            requested_days = (end.date() - start.date()).days + 1
            overlap_start = start.date()
            overlap_end = end.date()

        if leave_data.resuming_date:
            resume = leave_data.resuming_date
            if isinstance(resume, datetime) and resume.tzinfo is None:
                resume = resume.replace(tzinfo=timezone.utc)
            resume_date = resume.astimezone(IST).date() if isinstance(resume, datetime) else resume
            last_leave = max(target_dates) if target_dates else end.date()
            if resume_date <= last_leave:
                raise HTTPException(status_code=400, detail="Resuming date must be after the last leave day.")

        # Check for completed attendance on requested dates
        completed_att = self.leave_db.db.execute(
            select(Attendance).where(
                Attendance.keycloak_user_id == user_id,
                Attendance.attendance_date >= start.date(),
                Attendance.attendance_date <= end.date(),
                Attendance.check_out_time.is_not(None),
            )
        ).scalars().first()
        if completed_att:
            raise HTTPException(
                status_code=400,
                detail=f"Shift already completed on {completed_att.attendance_date}. Cannot apply for leave."
            )

        # Check for overlaps
        existing_leaves = self.leave_db.db.execute(
            select(Leave).where(
                Leave.keycloak_user_id == user_id,
                Leave.approval_status.in_([LeaveStatus.APPROVED.value, LeaveStatus.PENDING.value]),
                Leave.start_date <= overlap_end,
                Leave.end_date >= overlap_start
            )
        ).scalars().all()

        if existing_leaves:
            if target_dates:
                for d in target_dates:
                    for existing in existing_leaves:
                        if existing.leave_dates:
                            if d.isoformat() in existing.leave_dates:
                                raise HTTPException(status_code=400, detail=f"You already have a leave on {d.isoformat()}")
                        elif existing.start_date <= d <= existing.end_date:
                            raise HTTPException(status_code=400, detail=f"You already have a leave on {d.isoformat()}")
            else:
                raise HTTPException(status_code=400, detail="You already have an approved or pending leave application that overlaps with these dates.")

        # Create leave records — one INSERT for all days
        leave_docs = []
        if leave_data.leave_dates:
            dates = sorted({d.astimezone(IST).date() if d.tzinfo else d.replace(tzinfo=timezone.utc).astimezone(IST).date() for d in leave_data.leave_dates})
        else:
            current = start.date()
            dates = []
            while current <= end.date():
                dates.append(current)
                current += timedelta(days=1)

        for current_date in dates:
            leave_docs.append({
                "keycloak_user_id": user_id,
                "user_email": email,
                "start_date": current_date,
                "end_date": current_date,
                "leave_type": leave_data.leave_type.value,
                "reason": leave_data.comment or leave_data.reason,
                "comment": leave_data.comment,
                "is_traveling": leave_data.is_traveling,
                "contact_number": leave_data.contact_number,
                "resuming_date": leave_data.resuming_date.date() if isinstance(leave_data.resuming_date, datetime) else leave_data.resuming_date,
                "approver_id": leave_data.approver_id,
                "approval_status": LeaveStatus.PENDING.value,
            })
            current_date += timedelta(days=1)

        stmt = insert(Leave).returning(Leave.id)
        result = self.leave_db.db.execute(stmt, leave_docs)
        self.leave_db.db.commit()
        leave_ids = [str(row.id) for row in result]

        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=user_id)
        balance_info = {"requested_days": requested_days, "remaining": None, "will_be_negative": None}
        if balance and leave_data.leave_type in (LeaveType.EL, LeaveType.PL):
            if leave_data.leave_type == LeaveType.EL:
                if balance.total_earned is None:
                    remaining = None
                else:
                    remaining = balance.total_earned - (balance.used_earned or 0.0)
            else:
                remaining = (balance.accrued_compoff or 0.0) - (balance.consumed_compoff or 0.0)
            balance_info["remaining"] = remaining
            balance_info["will_be_negative"] = remaining < requested_days if remaining is not None else None

        return {"leave_id": leave_ids[0], "leave_ids": leave_ids, "created": len(leave_ids), "balance": balance_info}

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
                or_(
                    Attendance.attendance_date == today,
                    and_(Attendance.attendance_date == today - timedelta(days=1), Attendance.check_out_time.is_(None)),
                ),
            )
        ).scalars().first()
        if existing_attendance:
            if existing_attendance.check_out_time:
                raise HTTPException(status_code=400, detail="Already completed work for today.")
            existing_attendance.check_out_time = datetime.now(timezone.utc)
            existing_attendance.remarks = (existing_attendance.remarks or "") + " | Emergency leave"
            self.leave_db.db.commit()

        if not existing_attendance:
            has_time_passed = False
            shift = self.leave_db.db.execute(
                select(Shift).where(
                    Shift.keycloak_user_id == user_id,
                    Shift.date == today,
                )
            ).scalars().first()
            if shift and shift.shift_code:
                sd = self.leave_db.db.execute(
                    select(ShiftDefinition).where(ShiftDefinition.shift_code == shift.shift_code)
                ).scalars().first()
                if sd and sd.end_time:
                    tz = tz_str = sd.timezone or "Asia/Kolkata"
                    try:
                        tz = ZoneInfo(tz_str)
                    except Exception:
                        tz = ZoneInfo("Asia/Kolkata")
                    end_dt = datetime.combine(today, sd.end_time, tzinfo=tz)
                    if sd.end_time <= sd.start_time:
                        end_dt += timedelta(days=1)
                    if datetime.now(tz) > end_dt + timedelta(hours=2):
                        has_time_passed = True
            else:
                company = self.leave_db.db.execute(
                    select(SystemSetting).where(SystemSetting.id == "company")
                ).scalars().first()
                if company and company.default_shift_end_time:
                    tz_str = company.default_timezone or "Asia/Kolkata"
                    try:
                        tz = ZoneInfo(tz_str)
                    except Exception:
                        tz = ZoneInfo("Asia/Kolkata")
                    end_dt = datetime.combine(today, company.default_shift_end_time, tzinfo=tz)
                    if datetime.now(tz) > end_dt + timedelta(hours=2):
                        has_time_passed = True
            if has_time_passed:
                raise HTTPException(status_code=400, detail="Shift hours have ended for today.")

        if not existing_attendance:
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
            "approval_status": LeaveStatus.EMERGENCY.value,
        }
        new_leave = self.leave_db.create(Leave, **leave_doc)
        return {"message": "Emergency leave applied", "leave_id": str(new_leave.id)}

    async def get_leave_history(self, user_id: str) -> List[Dict[str, Any]]:
        stmt = select(Leave).where(Leave.keycloak_user_id == user_id).order_by(Leave.start_date.desc())
        results = self.leave_db.db.execute(stmt).scalars().all()
        return self._enrich_leaves_with_employee(results)

    async def get_team_leaves(self, emails: list, from_date: str = None, to_date: str = None) -> List[Dict[str, Any]]:
        stmt = select(Leave).where(Leave.user_email.in_(emails))
        if from_date:
            stmt = stmt.where(Leave.start_date >= date.fromisoformat(from_date))
        if to_date:
            stmt = stmt.where(Leave.end_date <= date.fromisoformat(to_date))
        stmt = stmt.order_by(Leave.start_date.desc())
        results = self.leave_db.db.execute(stmt).scalars().all()
        return self._enrich_leaves_with_employee(results)

    def _enrich_leaves_with_employee(self, results) -> List[Dict[str, Any]]:
        if not results:
            return []
        emails = {r.user_email for r in results if r.user_email}
        emails.update({r.approved_by for r in results if r.approved_by})
        emails.update({r.rejected_by for r in results if r.rejected_by})
        emp_map = {}
        if emails:
            emps = self.leave_db.db.execute(
                select(EmployeeMaster).where(EmployeeMaster.user_email.in_(emails))
            ).scalars().all()
            for e in emps:
                emp_map[e.user_email] = e
        records = []
        for r in results:
            d = r.to_dict()
            emp = emp_map.get(r.user_email)
            d["userName"] = f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else None
            d["userDesignation"] = emp.designation if emp else None

            if r.approved_by:
                app_emp = emp_map.get(r.approved_by)
                if app_emp:
                    name = f"{app_emp.first_name} {app_emp.last_name}".strip()
                    desig = app_emp.designation or ""
                    d["approved_by"] = f"{name} ({desig})" if desig else name

            if r.rejected_by:
                rej_emp = emp_map.get(r.rejected_by)
                if rej_emp:
                    name = f"{rej_emp.first_name} {rej_emp.last_name}".strip()
                    desig = rej_emp.designation or ""
                    d["rejected_by"] = f"{name} ({desig})" if desig else name

            records.append(d)
        return records

    async def get_user_leave_history(self, email: str, current_user: dict) -> List[Dict[str, Any]]:
        roles = current_user.get("roles", [])
        sql = RedmineSQLService(self.leave_db.db)

        tr_user = sql.get_user_by_email(email)
        if not tr_user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")

        if "Admin" not in roles:
            pm_email = current_user.get("email")
            pm_user = sql.get_user_by_email(pm_email)
            if not pm_user:
                raise HTTPException(status_code=403, detail="Unauthorized")

            if not sql.check_project_access(pm_user["id"], tr_user["id"]):
                raise HTTPException(status_code=403, detail="You can only view leaves for users in your projects.")

        stmt = select(Leave).where(Leave.user_email == email).order_by(Leave.start_date.desc())
        results = self.leave_db.db.execute(stmt).scalars().all()
        return self._enrich_leaves_with_employee(results)

    async def get_visible_leave_users(self, current_user: dict) -> list:
        roles = current_user.get("roles", [])
        sql = RedmineSQLService(self.leave_db.db)

        all_users = sql.get_all_users()

        if "Admin" in roles:
            return all_users

        pm_email = current_user.get("email")
        pm_user = sql.get_user_by_email(pm_email)
        if not pm_user:
            return []

        visible_ids = sql.get_team_member_ids(pm_user["id"])
        return [u for u in all_users if u["id"] in visible_ids]

    async def get_holidays(self, limit: int = 10, skip: int = 0) -> List[Dict[str, Any]]:
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
            "total_earned": balance_doc.total_earned if balance_doc else 0.0,
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
            sql = RedmineSQLService(self.leave_db.db)
            pm_email = current_user.get("email")
            pm_user = sql.get_user_by_email(pm_email)
            if not pm_user:
                return False

            tr_email = leave.user_email
            if not tr_email:
                return False

            tr_user = sql.get_user_by_email(tr_email)
            if not tr_user:
                return False

            if not sql.check_project_access(pm_user["id"], tr_user["id"]):
                raise HTTPException(status_code=403, detail="You can only approve leaves for resources in your projects.")

        # ── Calculate requested leave days ─────────────────────────────────
        requested_days = (leave.end_date - leave.start_date).days + 1
        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=leave.keycloak_user_id)

        # ── Balance check (info only, no block) ────────────────────────────
        leave_type = leave.leave_type

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
            sql = RedmineSQLService(self.leave_db.db)
            pm_email = current_user.get("email")
            pm_user = sql.get_user_by_email(pm_email)
            if not pm_user:
                return False

            tr_email = leave.user_email
            if not tr_email:
                return False

            tr_user = sql.get_user_by_email(tr_email)
            if not tr_user:
                return False

            if not sql.check_project_access(pm_user["id"], tr_user["id"]):
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

    async def batch_approve_leaves(self, leave_ids: list, current_user: dict) -> dict:
        return await self._batch_process_leaves(leave_ids, current_user, action="approve")

    async def batch_reject_leaves(self, leave_ids: list, current_user: dict) -> dict:
        return await self._batch_process_leaves(leave_ids, current_user, action="reject")

    async def _batch_process_leaves(self, leave_ids: list, current_user: dict, action: str) -> dict:
        if len(leave_ids) > 50:
            raise HTTPException(status_code=400, detail="Maximum 50 leave IDs per batch request.")

        import uuid as _uuid
        for lid in leave_ids:
            try:
                _uuid.UUID(lid)
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid leave ID(s). Must be valid UUID.")
        roles = current_user.get("roles", [])
        pm_email = current_user.get("email")
        is_admin = "Admin" in roles

        pm_redmine_id = None
        if not is_admin:
            pm_emp = self.leave_db.db.execute(
                select(EmployeeMaster).where(EmployeeMaster.user_email == pm_email)
            ).scalars().first()
            pm_redmine_id = pm_emp.redmine_user_id if pm_emp else None

        processed = 0
        success = 0
        results = []
        for leave_id in leave_ids:
            processed += 1
            leave = self.leave_db.fetch_one(Leave, id=leave_id)
            if not leave:
                results.append({"leave_id": leave_id, "status": "failed", "reason": "Not found"})
                continue
            if leave.approval_status != LeaveStatus.PENDING.value:
                results.append({"leave_id": leave_id, "status": "failed", "reason": f"Already {leave.approval_status}"})
                continue
            if not is_admin and leave.approver_id != pm_redmine_id:
                results.append({"leave_id": leave_id, "status": "failed", "reason": "Not authorized"})
                continue

            if action == "approve":
                await self._do_approve(leave_id, leave, current_user)
                results.append({"leave_id": leave_id, "status": "approved"})
            else:
                await self._do_reject(leave_id, leave, current_user)
                results.append({"leave_id": leave_id, "status": "rejected"})
            success += 1

        failed = processed - success
        return {
            "processed": processed,
            "approved" if action == "approve" else "rejected": success,
            "failed": failed,
            "results": results,
        }

    async def _do_approve(self, leave_id: str, leave, current_user: dict) -> None:
        requested_days = (leave.end_date - leave.start_date).days + 1
        now = datetime.utcnow()
        actor_email = current_user.get("email")
        self.leave_db.update(
            Leave, leave_id,
            approval_status=LeaveStatus.APPROVED.value,
            updated_at=now, approved_at=now, approved_by=actor_email,
        )
        balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=leave.keycloak_user_id)
        if balance:
            if leave.leave_type == LeaveType.EL.value:
                self.balance_db.update(LeaveBalance, balance.id, used_earned=(balance.used_earned or 0) + requested_days, updated_at=now)
            elif leave.leave_type == LeaveType.PL.value:
                self.balance_db.update(LeaveBalance, balance.id, consumed_compoff=(balance.consumed_compoff or 0) + requested_days, updated_at=now)

    async def _do_reject(self, leave_id: str, leave, current_user: dict) -> None:
        now = datetime.utcnow()
        actor_email = current_user.get("email")
        self.leave_db.update(
            Leave, leave_id,
            approval_status=LeaveStatus.REJECTED.value,
            updated_at=now, rejected_at=now, rejected_by=actor_email,
        )

    async def get_pending_leaves(self, current_user: dict) -> List[Dict[str, Any]]:
        """Fetch all pending leaves with balance info."""
        roles = current_user.get("roles", [])

        if "Admin" in roles:
            stmt = select(Leave).where(Leave.approval_status == LeaveStatus.PENDING.value).order_by(Leave.created_at.desc())
            results = self.leave_db.db.execute(stmt).scalars().all()
        else:
            sql = RedmineSQLService(self.leave_db.db)
            pm_user = sql.get_user_by_email(current_user.get("email"))
            if not pm_user:
                return []
            stmt = select(Leave).where(
                Leave.approval_status == LeaveStatus.PENDING.value,
                Leave.approver_id == int(pm_user["id"])
            ).order_by(Leave.created_at.desc())
            results = self.leave_db.db.execute(stmt).scalars().all()

        pending = []
        for r in results:
            d = r.to_dict()
            balance = self.balance_db.fetch_one(LeaveBalance, keycloak_user_id=r.keycloak_user_id)
            requested_days = len(r.leave_dates) if r.leave_dates else (r.end_date - r.start_date).days + 1
            d["requested_days"] = requested_days
            d["balance"] = {
                "EL": {
                    "total": balance.total_earned if balance else 0,
                    "used": balance.used_earned if balance else 0,
                    "remaining": (balance.total_earned or 0) - (balance.used_earned or 0) if balance else 0,
                },
                "CompOff": {
                    "total": balance.accrued_compoff if balance else 0,
                    "used": balance.consumed_compoff if balance else 0,
                    "remaining": (balance.accrued_compoff or 0) - (balance.consumed_compoff or 0) if balance else 0,
                },
                "Unpaid": {
                    "total": 0,
                    "used": balance.unpaid if balance else 0,
                    "remaining": 0,
                },
            }
            pending.append(d)

        return pending
