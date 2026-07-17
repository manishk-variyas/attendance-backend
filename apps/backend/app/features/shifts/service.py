import logging
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.holiday import Holiday
from app.models.leave import Leave
from app.models.attendance import Attendance
from app.models.system_setting import SystemSetting
from app.services.database.shift_service import ShiftService as PGShiftService, ShiftDefinitionService as PGShiftDefinitionService
from sqlalchemy import select
from sqlalchemy import update as sql_update, delete as sql_delete

logger = logging.getLogger(__name__)


class ShiftService:
    """Business logic for shift management now backed by PostgreSQL."""

    def _svc(self, db: Session) -> PGShiftService:
        return PGShiftService(db)

    def _def_svc(self, db: Session) -> PGShiftDefinitionService:
        return PGShiftDefinitionService(db)

    def _collect_warnings(self, db: Session, keycloak_id: str, start: date, end: date) -> list:
        warnings = []
        current = start
        while current <= end:
            if current.weekday() >= 5:
                warnings.append({"date": current.isoformat(), "type": "weekend", "label": current.strftime("%A")})

            holiday_rec = db.query(Holiday).filter(Holiday.holiday_date == current).first()
            if holiday_rec:
                warnings.append({"date": current.isoformat(), "type": "holiday", "label": holiday_rec.holiday_name})

            leave_rec = db.query(Leave).filter(
                Leave.keycloak_user_id == keycloak_id,
                Leave.start_date <= current,
                Leave.end_date >= current,
                Leave.approval_status == "approved",
            ).first()
            if leave_rec:
                warnings.append({"date": current.isoformat(), "type": "leave", "label": leave_rec.leave_type})

            current += timedelta(days=1)

        overlaps = db.query(Shift).filter(
            Shift.keycloak_user_id == keycloak_id,
            Shift.date <= end,
            Shift.end_date >= start,
        ).all()
        for s in overlaps:
            warnings.append({"date": s.date.isoformat(), "type": "overlap", "label": f"Overlaps with {s.shift_code} ({s.date} → {s.end_date})"})

        return warnings

    async def validate_shift(self, db: Session, data: dict, current_user: dict = None) -> dict:
        keycloak_id = data.get("keycloakUserId", "")
        if not keycloak_id:
            redmine_uid = data.get("userId")
            if redmine_uid:
                emp = db.execute(select(EmployeeMaster).where(EmployeeMaster.redmine_user_id == redmine_uid)).scalars().first()
                if emp:
                    keycloak_id = emp.keycloak_user_id
        if not keycloak_id and current_user:
            keycloak_id = current_user.get("sub", "")
        if not keycloak_id:
            keycloak_id = data.get("userEmail", "")

        d = date.fromisoformat(data["date"])
        end = date.fromisoformat(data.get("endDate", data["date"]))
        warnings = self._collect_warnings(db, keycloak_id, d, end)
        has_warnings = len(warnings) > 0
        return {"valid": not has_warnings, "warnings": warnings, "message": f"Found {len(warnings)} warning(s)" if has_warnings else "No conflicts found"}

    async def create_shift(self, db: Session, data: dict, current_user: dict = None) -> dict:
        svc = self._svc(db)
        d = date.fromisoformat(data["date"])
        end = date.fromisoformat(data.get("endDate", data["date"]))

        keycloak_id = data.get("keycloakUserId", "")
        if not keycloak_id:
            redmine_uid = data.get("userId")
            if redmine_uid:
                emp = db.execute(select(EmployeeMaster).where(EmployeeMaster.redmine_user_id == redmine_uid)).scalars().first()
                if emp:
                    keycloak_id = emp.keycloak_user_id
        if not keycloak_id and current_user:
            keycloak_id = current_user.get("sub", "")
        if not keycloak_id:
            keycloak_id = data.get("userEmail", "")

        ws = data.get("workStatus", "OFFICE").upper()
        work_location_map = {"PRESENT": "OFFICE", "ON-SITE": "REMOTE_ONSITE", "WFH": "WFH", "REMOTE_ONSITE": "REMOTE_ONSITE", "REMOTE_OFFSITE": "REMOTE_OFFSITE"}
        if ws in work_location_map:
            ws = work_location_map[ws]

        work_address = data.get("workAddress", "N/A")
        if work_address == "N/A":
            emp = db.execute(select(EmployeeMaster).where(EmployeeMaster.keycloak_user_id == keycloak_id)).scalars().first()
            if not emp:
                work_address = "N/A"
            elif ws == "WFH":
                work_address = emp.home_address or "N/A"
            elif emp.location_id:
                office = db.execute(select(OfficeLocation).where(OfficeLocation.id == emp.location_id)).scalars().first()
                if office:
                    work_address = office.address or f"{office.name}, {office.city or ''}".strip(", ")

        start_time = None
        end_time = None
        if data.get("shiftStartTime"):
            try:
                start_time = datetime.strptime(data["shiftStartTime"][:5], "%H:%M").time()
            except ValueError:
                pass
        if data.get("shiftEndTime"):
            try:
                end_time = datetime.strptime(data["shiftEndTime"][:5], "%H:%M").time()
            except ValueError:
                pass

        shift = svc.create(Shift,
            keycloak_user_id=keycloak_id,
            redmine_user_id=data.get("userId"),
            user_email=data.get("userEmail", ""),
            date=d,
            end_date=end,
            shift_code=data.get("shift", ""),
            project_id=data.get("projectId"),
            project_name=data.get("projectName"),
            work_location_status=ws,
            work_address=work_address,
            shift_start_time=start_time,
            shift_end_time=end_time,
            shift_start_utc=data.get("shiftStartUTC"),
            shift_end_utc=data.get("shiftEndUTC"),
            location_id=data.get("locationId"),
            country=data.get("country", ""),
            pincode=data.get("pincode"),
            per_diem_eligible=data.get("perDiemEligible", False),
            conveyance_eligible=data.get("conveyanceEligible", False),
        )

        warnings = self._collect_warnings(db, keycloak_id, d, end)

        return {
            "id": str(shift.id),
            "userId": shift.redmine_user_id,
            "userEmail": shift.user_email,
            "date": shift.date.isoformat() if shift.date else None,
            "endDate": shift.end_date.isoformat() if shift.end_date else None,
            "shift": shift.shift_code,
            "projectId": shift.project_id,
            "projectName": shift.project_name,
            "workStatus": shift.work_location_status,
            "workAddress": shift.work_address,
            "shiftStartTime": shift.shift_start_time.isoformat() if shift.shift_start_time else None,
            "shiftEndTime": shift.shift_end_time.isoformat() if shift.shift_end_time else None,
            "shiftStartUTC": shift.shift_start_utc,
            "shiftEndUTC": shift.shift_end_utc,
            "locationId": str(shift.location_id) if shift.location_id else None,
            "country": shift.country,
            "pincode": shift.pincode,
            "perDiemEligible": shift.per_diem_eligible,
            "conveyanceEligible": shift.conveyance_eligible,
            "warnings": warnings,
            "createdAt": shift.created_at,
            "updatedAt": shift.updated_at,
        }

    async def get_shift_by_id(self, db: Session, shift_id: str) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_one(Shift, id=shift_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_shifts_by_user_id(self, db: Session, user_id: int) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_user_id(user_id)
        return [self._serialize(s) for s in shifts]

    async def get_shifts_by_email(self, db: Session, email: str) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_email(email)
        return [self._serialize(s) for s in shifts]

    async def get_current_shift(self, db: Session, user_id: int) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_current(user_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_active_shift(self, db: Session, user_id: int) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_active(user_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_stats(self, db: Session) -> dict:
        svc = self._svc(db)
        return svc.get_stats()

    async def get_shifts_by_date(self, db: Session, date_str: str, email: str = None) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_date(date_str, email)
        return [self._serialize(s) for s in shifts]

    async def get_shifts_by_date_range(self, db: Session, start_date: str, end_date: str, user_id: int = None) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_date_range(start_date, end_date, user_id)
        return [self._serialize(s) for s in shifts]

    async def get_shift_history_by_email(self, db: Session, email: str) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_email(email)
        if not shifts:
            return []

        emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == email).first()
        company = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
        grace_min = company.grace_minutes if company and company.grace_minutes else 2

        user_ids = list({s.keycloak_user_id for s in shifts if s.keycloak_user_id})
        dates = list({s.date for s in shifts if s.date})
        att_query = db.query(Attendance).filter(
            Attendance.keycloak_user_id.in_(user_ids),
            Attendance.attendance_date.in_(dates),
        ).all()
        att_map = {(a.keycloak_user_id, a.attendance_date): a for a in att_query}

        result = []
        for s in shifts:
            sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == s.shift_code).first()
            leave = db.query(Leave).filter(
                Leave.keycloak_user_id == s.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= s.date,
                Leave.end_date >= s.date,
            ).first()
            att = att_map.get((s.keycloak_user_id, s.date))

            derived_status = s.status
            if att and att.check_out_time:
                derived_status = "Ended"
            elif att and not att.check_out_time:
                derived_status = "In Progress"
            elif leave:
                derived_status = "on_leave"
            elif sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                if datetime.now(tz) > start_dt:
                    derived_status = "Missed"
                else:
                    derived_status = "Yet to start"

            late_by = None
            if att and att.is_late and att.check_in_time and sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                shift_start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                cutoff = shift_start_dt + timedelta(minutes=grace_min)
                if att.check_in_time > cutoff:
                    late_by = round((att.check_in_time - cutoff).total_seconds() / 3600, 1)

            result.append({
                "id": str(s.id),
                "userId": s.redmine_user_id,
                "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else None,
                "userEmail": s.user_email,
                "userDesignation": emp.designation if emp else None,
                "projectId": s.project_id,
                "projectName": s.project_name,
                "shift": s.shift_code,
                "shiftName": sd.shift_name if sd else None,
                "workStatus": s.work_location_status,
                "status": derived_status,
                "workAddress": s.work_address,
                "pincode": s.pincode,
                "leaveType": leave.leave_type if leave else None,
                "perDiemEligible": s.per_diem_eligible,
                "conveyanceEligible": s.conveyance_eligible,
                "date": s.date.isoformat() if s.date else None,
                "dayName": s.date.strftime("%A") if s.date else None,
                "endDate": s.end_date.isoformat() if s.end_date else None,
                "shiftStartTime": s.shift_start_time.isoformat() if s.shift_start_time else None,
                "shiftEndTime": s.shift_end_time.isoformat() if s.shift_end_time else None,
                "checkInTime": att.check_in_time.isoformat() if att and att.check_in_time else None,
                "checkOutTime": att.check_out_time.isoformat() if att and att.check_out_time else None,
                "checkInLocationName": att.check_in_location_name if att else None,
                "checkInLat": float(att.check_in_lat) if att and att.check_in_lat else None,
                "checkInLng": float(att.check_in_lng) if att and att.check_in_lng else None,
                "checkOutLocationName": att.check_out_location_name if att else None,
                "checkOutLat": float(att.check_out_lat) if att and att.check_out_lat else None,
                "checkOutLng": float(att.check_out_lng) if att and att.check_out_lng else None,
                "isLate": att.is_late if att else False,
                "lateByHours": late_by,
                "totalHours": float(att.total_hours) if att and att.total_hours else None,
                "createdAt": s.created_at,
            })
        shifted_dates = {s.date for s in shifts}

        leaves = db.query(Leave).filter(
            Leave.keycloak_user_id.in_(user_ids),
            Leave.approval_status == "approved",
        ).all()
        for lv in leaves:
            if not lv.keycloak_user_id:
                continue
            current = lv.start_date
            while current <= lv.end_date:
                if current not in shifted_dates:
                    result.append({
                        "id": None,
                        "userId": None,
                        "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else email,
                        "userEmail": email,
                        "userDesignation": emp.designation if emp else None,
                        "projectId": None, "projectName": None,
                        "shift": None, "shiftName": None,
                        "workStatus": None, "status": "on_leave",
                        "workAddress": None, "pincode": None,
                        "leaveType": lv.leave_type,
                        "perDiemEligible": False, "conveyanceEligible": False,
                        "date": current.isoformat(),
                        "dayName": current.strftime("%A"),
                        "endDate": current.isoformat(),
                        "shiftStartTime": None, "shiftEndTime": None,
                        "checkInTime": None, "checkOutTime": None,
                        "checkInLat": None, "checkInLng": None,
                        "checkOutLat": None, "checkOutLng": None,
                        "checkInLocationName": None, "checkOutLocationName": None,
                        "isLate": False, "lateByHours": None, "totalHours": None,
                        "createdAt": None,
                    })
                current += timedelta(days=1)

        result.sort(key=lambda x: x["date"], reverse=True)
        return result

    async def get_team_shifts(self, db: Session, emails: list, from_date: str = None, to_date: str = None) -> List[dict]:
        query = db.query(Shift).filter(Shift.user_email.in_(emails))
        if from_date:
            query = query.filter(Shift.date >= date.fromisoformat(from_date))
        if to_date:
            query = query.filter(Shift.date <= date.fromisoformat(to_date))
        shifts = query.order_by(Shift.date.desc()).all()
        if not shifts:
            return []

        company = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
        grace_min = company.grace_minutes if company and company.grace_minutes else 2

        user_ids = list({s.keycloak_user_id for s in shifts if s.keycloak_user_id})
        dates = list({s.date for s in shifts if s.date})
        att_query = db.query(Attendance).filter(
            Attendance.keycloak_user_id.in_(user_ids),
            Attendance.attendance_date.in_(dates),
        ).all()
        att_map = {(a.keycloak_user_id, a.attendance_date): a for a in att_query}

        result = []
        for s in shifts:
            emp = db.query(EmployeeMaster).filter(EmployeeMaster.user_email == s.user_email).first()
            sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == s.shift_code).first()
            leave = db.query(Leave).filter(
                Leave.keycloak_user_id == s.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= s.date,
                Leave.end_date >= s.date,
            ).first()
            att = att_map.get((s.keycloak_user_id, s.date))

            derived_status = s.status
            if att and att.check_out_time:
                derived_status = "Ended"
            elif att and not att.check_out_time:
                derived_status = "In Progress"
            elif leave:
                derived_status = "on_leave"
            elif sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                if datetime.now(tz) > start_dt:
                    derived_status = "Missed"
                else:
                    derived_status = "Yet to start"

            late_by = None
            if att and att.is_late and att.check_in_time and sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                shift_start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                cutoff = shift_start_dt + timedelta(minutes=grace_min)
                if att.check_in_time > cutoff:
                    late_by = round((att.check_in_time - cutoff).total_seconds() / 3600, 1)

            result.append({
                "id": str(s.id),
                "userId": s.redmine_user_id,
                "userEmail": s.user_email,
                "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else s.user_email,
                "userDesignation": emp.designation if emp else None,
                "shiftName": sd.shift_name if sd else None,
                "projectId": s.project_id,
                "projectName": s.project_name,
                "shift": s.shift_code,
                "workStatus": s.work_location_status,
                "status": derived_status,
                "workAddress": s.work_address,
                "pincode": s.pincode,
                "leaveType": leave.leave_type if leave else None,
                "perDiemEligible": s.per_diem_eligible,
                "conveyanceEligible": s.conveyance_eligible,
                "date": s.date.isoformat() if s.date else None,
                "dayName": s.date.strftime("%A") if s.date else None,
                "endDate": s.end_date.isoformat() if s.end_date else None,
                "shiftStartTime": s.shift_start_time.isoformat() if s.shift_start_time else None,
                "shiftEndTime": s.shift_end_time.isoformat() if s.shift_end_time else None,
                "checkInTime": att.check_in_time.isoformat() if att and att.check_in_time else None,
                "checkOutTime": att.check_out_time.isoformat() if att and att.check_out_time else None,
                "checkInLocationName": att.check_in_location_name if att else None,
                "checkInLat": float(att.check_in_lat) if att and att.check_in_lat else None,
                "checkInLng": float(att.check_in_lng) if att and att.check_in_lng else None,
                "checkOutLocationName": att.check_out_location_name if att else None,
                "checkOutLat": float(att.check_out_lat) if att and att.check_out_lat else None,
                "checkOutLng": float(att.check_out_lng) if att and att.check_out_lng else None,
                "isLate": att.is_late if att else False,
                "lateByHours": late_by,
                "totalHours": float(att.total_hours) if att and att.total_hours else None,
                "createdAt": s.created_at,
            })
        shifted_dates = {(s.user_email, s.date) for s in shifts}

        if user_ids:
            leaves = db.query(Leave).filter(
                Leave.keycloak_user_id.in_(user_ids),
                Leave.approval_status == "approved",
            ).all()
            for lv in leaves:
                if not lv.keycloak_user_id:
                    continue
                lemp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == lv.keycloak_user_id).first()
                current = lv.start_date
                while current <= lv.end_date:
                    lv_email = lemp.user_email if lemp else ""
                    if (lv_email, current) not in shifted_dates:
                        result.append({
                            "id": None, "userId": None,
                            "userEmail": lv_email,
                            "userName": f"{lemp.first_name} {lemp.middle_name or ''} {lemp.last_name}".strip().replace("  ", " ") if lemp else lv_email,
                            "userDesignation": lemp.designation if lemp else None,
                            "shiftName": None, "projectId": None, "projectName": None,
                            "shift": None, "workStatus": None, "status": "on_leave",
                            "workAddress": None, "pincode": None,
                            "leaveType": lv.leave_type,
                            "perDiemEligible": False, "conveyanceEligible": False,
                            "date": current.isoformat(),
                            "dayName": current.strftime("%A"),
                            "endDate": current.isoformat(),
                            "shiftStartTime": None, "shiftEndTime": None,
                            "checkInTime": None, "checkOutTime": None,
                            "checkInLat": None, "checkInLng": None,
                            "checkOutLat": None, "checkOutLng": None,
                            "checkInLocationName": None, "checkOutLocationName": None,
                            "isLate": False, "lateByHours": None, "totalHours": None,
                            "createdAt": None,
                        })
                    current += timedelta(days=1)

        result.sort(key=lambda x: x["date"], reverse=True)
        return result

    async def update_shift(self, db: Session, shift_id: str, data: dict) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_one(Shift, id=shift_id)
        if not shift:
            return None
        mapping = {
            "shift": "shift_code",
            "workStatus": "work_location_status",
            "workAddress": "work_address",
            "shiftStartTime": "shift_start_time",
            "shiftEndTime": "shift_end_time",
            "shiftStartUTC": "shift_start_utc",
            "shiftEndUTC": "shift_end_utc",
            "projectId": "project_id",
            "projectName": "project_name",
            "endDate": "end_date",
            "locationId": "location_id",
            "pincode": "pincode",
            "perDiemEligible": "per_diem_eligible",
            "conveyanceEligible": "conveyance_eligible",
        }
        update_data = {}
        for mongo_key, pg_col in mapping.items():
            if mongo_key in data and pg_col:
                val = data[mongo_key]
                if pg_col == "end_date" and isinstance(val, str):
                    val = date.fromisoformat(val)
                update_data[pg_col] = val
        if update_data:
            svc.update(Shift, shift.id, **update_data)

        # Cascade to attendance records in this shift's date range
        att_updates = {}
        if "shift_code" in update_data:
            att_updates["shift_code"] = update_data["shift_code"]
        if "work_location_status" in update_data:
            att_updates["work_location_status"] = update_data["work_location_status"]
        if att_updates:
            db.query(Attendance).filter(
                Attendance.keycloak_user_id == shift.keycloak_user_id,
                Attendance.attendance_date >= shift.date,
                Attendance.attendance_date <= (shift.end_date or shift.date),
                Attendance.shift_code == shift.shift_code,
            ).update(att_updates, synchronize_session=False)
            db.commit()

        return await self.get_shift_by_id(db, shift_id)

    async def delete_shift(self, db: Session, shift_id: str) -> bool:
        shift = db.query(Shift).filter(Shift.id == shift_id).first()
        if not shift:
            return False
        db.query(Attendance).filter(
            Attendance.keycloak_user_id == shift.keycloak_user_id,
            Attendance.attendance_date >= shift.date,
            Attendance.attendance_date <= (shift.end_date or shift.date),
            Attendance.shift_code == shift.shift_code,
        ).update({Attendance.shift_code: None}, synchronize_session=False)
        db.delete(shift)
        db.commit()
        return True

    async def delete_shift_by_date(self, db: Session, keycloak_user_id: str, target_date: date) -> bool:
        shifts = db.query(Shift).filter(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date <= target_date,
            Shift.end_date >= target_date,
        ).all()
        if not shifts:
            return False

        for shift in shifts:
            db.query(Attendance).filter(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == target_date,
                Attendance.shift_code == shift.shift_code,
            ).update({Attendance.shift_code: None}, synchronize_session=False)

            if shift.date == shift.end_date:
                db.delete(shift)
                continue

            if target_date == shift.date:
                shift.date = target_date + timedelta(days=1)
                continue

            if target_date == shift.end_date:
                shift.end_date = target_date - timedelta(days=1)
                continue

            left = Shift(
                keycloak_user_id=shift.keycloak_user_id,
                redmine_user_id=shift.redmine_user_id,
                user_email=shift.user_email,
                date=shift.date,
                end_date=target_date - timedelta(days=1),
                shift_code=shift.shift_code,
                project_id=shift.project_id,
                project_name=shift.project_name,
                work_location_status=shift.work_location_status,
                work_address=shift.work_address,
                shift_start_time=shift.shift_start_time,
                shift_end_time=shift.shift_end_time,
                location_id=shift.location_id,
                country=shift.country,
                per_diem_eligible=shift.per_diem_eligible,
                conveyance_eligible=shift.conveyance_eligible,
            )
            right = Shift(
                keycloak_user_id=shift.keycloak_user_id,
                redmine_user_id=shift.redmine_user_id,
                user_email=shift.user_email,
                date=target_date + timedelta(days=1),
                end_date=shift.end_date,
                shift_code=shift.shift_code,
                project_id=shift.project_id,
                project_name=shift.project_name,
                work_location_status=shift.work_location_status,
                work_address=shift.work_address,
                shift_start_time=shift.shift_start_time,
                shift_end_time=shift.shift_end_time,
                location_id=shift.location_id,
                country=shift.country,
                per_diem_eligible=shift.per_diem_eligible,
                conveyance_eligible=shift.conveyance_eligible,
            )
            db.add(left)
            db.add(right)
            db.delete(shift)

        db.commit()
        return True

    async def create_shift_definition(self, db: Session, data: dict) -> dict:
        svc = self._def_svc(db)
        existing = svc.fetch_by_code(data["shiftCode"])
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Shift code '{data['shiftCode']}' already exists.",
            )
        start = datetime.strptime(data.get("startTime", "00:00")[:5], "%H:%M").time()
        end = datetime.strptime(data.get("endTime", "00:00")[:5], "%H:%M").time()
        sd = svc.create(ShiftDefinition,
            shift_code=data["shiftCode"],
            shift_name=data.get("shiftName", ""),
            start_time=start,
            end_time=end,
            timezone=data.get("timezone", "Asia/Kolkata"),
            country=data.get("country", ""),
        )
        return self._serialize_def(sd)

    async def get_all_shift_definitions(self, db: Session) -> List[dict]:
        svc = self._def_svc(db)
        defs = svc.fetch_all()
        return [self._serialize_def(d) for d in defs]

    async def get_shift_definition_by_code(self, db: Session, shift_code: str) -> Optional[dict]:
        svc = self._def_svc(db)
        sd = svc.fetch_by_code(shift_code)
        if not sd:
            return None
        return self._serialize_def(sd)

    async def get_shift_definition(self, db: Session, shift_id: str) -> Optional[dict]:
        svc = self._def_svc(db)
        sd = svc.fetch_one(ShiftDefinition, shift_id=shift_id)
        if not sd:
            return None
        return self._serialize_def(sd)

    async def update_shift_definition(self, db: Session, shift_id: str, data: dict) -> Optional[dict]:
        svc = self._def_svc(db)
        existing = svc.fetch_one(ShiftDefinition, shift_id=shift_id)
        if not existing:
            return None
        if data.get("shiftCode") and data["shiftCode"] != existing.shift_code:
            dup = svc.fetch_by_code(data["shiftCode"])
            if dup:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Shift code '{data['shiftCode']}' already exists.",
                )
        update_data = {}
        if "shiftCode" in data:
            update_data["shift_code"] = data["shiftCode"]
        if "shiftName" in data:
            update_data["shift_name"] = data["shiftName"]
        if "startTime" in data:
            update_data["start_time"] = datetime.strptime(data["startTime"][:5], "%H:%M").time()
        if "endTime" in data:
            update_data["end_time"] = datetime.strptime(data["endTime"][:5], "%H:%M").time()
        if "timezone" in data:
            update_data["timezone"] = data["timezone"]
        if "country" in data:
            update_data["country"] = data["country"]
        if update_data:
            svc.db.execute(
                sql_update(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id).values(**update_data)
            )
            svc.db.commit()
        return await self.get_shift_definition(db, shift_id)

    async def delete_shift_definition(self, db: Session, shift_id: str) -> bool:
        svc = self._def_svc(db)
        result = svc.db.execute(sql_delete(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id))
        svc.db.commit()
        return result.rowcount > 0

    async def search_shifts(self, db: Session, query: str, limit: int = 5) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.search_by_project(query, limit)
        result = []
        for s in shifts:
            result.append({
                "id": f"shift_{s.id}",
                "date": s.date.isoformat() if s.date else "",
                "startTime": s.shift_start_time.isoformat() if s.shift_start_time else "",
                "endTime": s.shift_end_time.isoformat() if s.shift_end_time else "",
                "status": s.work_location_status or "YET_TO_START",
                "projectName": "",
                "employeeName": s.user_email,
            })
        return result

    def _serialize(self, shift: Shift) -> dict:
        return {
            "id": str(shift.id),
            "userId": shift.redmine_user_id,
            "userEmail": shift.user_email,
            "date": shift.date.isoformat() if shift.date else None,
            "endDate": shift.end_date.isoformat() if shift.end_date else None,
            "shift": shift.shift_code,
            "projectId": shift.project_id,
            "projectName": shift.project_name,
            "workStatus": shift.work_location_status,
            "workAddress": shift.work_address,
            "shiftStartTime": shift.shift_start_time.isoformat() if shift.shift_start_time else None,
            "shiftEndTime": shift.shift_end_time.isoformat() if shift.shift_end_time else None,
            "shiftStartUTC": shift.shift_start_utc,
            "shiftEndUTC": shift.shift_end_utc,
            "country": shift.country,
            "pincode": shift.pincode,
            "perDiemEligible": shift.per_diem_eligible,
            "conveyanceEligible": shift.conveyance_eligible,
            "status": shift.status,
            "createdAt": shift.created_at,
            "updatedAt": shift.updated_at,
        }

    def _serialize_def(self, sd: ShiftDefinition) -> dict:
        return {
            "_id": str(sd.shift_id),
            "shiftCode": sd.shift_code,
            "shiftName": sd.shift_name,
            "startTime": sd.start_time.isoformat() if sd.start_time else None,
            "endTime": sd.end_time.isoformat() if sd.end_time else None,
            "timezone": sd.timezone,
            "country": sd.country,
            "createdAt": sd.created_at,
            "updatedAt": sd.updated_at,
        }


shift_service = ShiftService()
