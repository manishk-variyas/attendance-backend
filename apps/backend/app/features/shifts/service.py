import logging
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.utils.shift_time import safe_zone, shift_end_datetime
from app.utils.holiday_scope import employee_state, find_holiday
from app.utils.shift_policy import resolve_shift_policy
from app.constants import (
    DEFAULT_AUTO_CHECKOUT_ENABLED,
    DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES,
    DEFAULT_GRACE_MINUTES,
    DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES,
    DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS,
    DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES,
    DEFAULT_CHECKIN_CUTOFF_MINUTES,
    DEFAULT_HALF_DAY_MINUTES,
)

from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.holiday import Holiday
from app.models.leave import Leave
from app.models.leave_type import LeaveTypeConfig
from app.models.attendance import Attendance
from app.models.attendance_punch import AttendancePunch
from app.core.cache import clear_shift_definition_cache
from app.services.database.shift_service import ShiftService as PGShiftService, ShiftDefinitionService as PGShiftDefinitionService
from sqlalchemy import select, func
from sqlalchemy import update as sql_update, delete as sql_delete

logger = logging.getLogger(__name__)


def _roster_shift_status(shift: Shift, punch, leave, sd, tz: ZoneInfo) -> str:
    """Derive per-shift status from the shift_attendance punch (source of truth for multi-shift)."""
    if leave is not None or shift.work_location_status == "LEAVE":
        return "on_leave"
    if punch and punch.check_out_time:
        return "completed"
    if punch:
        return "in_progress"
    if sd and sd.end_time:
        end_dt = shift_end_datetime(shift.date, sd.start_time, sd.end_time, tz)
        if datetime.now(tz) > end_dt:
            return "missed"
    return "yet_to_start"


def _build_shift_record(s: Shift, punch, sd, leave) -> dict:
    """Build one rich per-shift record from the shift + its punch (shift_attendance
    or a legacy `attendance` row fallback). `punch` may be None (not checked in)."""
    tz = safe_zone(sd.timezone if sd else None)
    start_time = s.shift_start_time or (sd.start_time if sd else None)
    end_time = s.shift_end_time or (sd.end_time if sd else None)
    grace_min = resolve_shift_policy(sd).grace_minutes

    late_by = None
    if punch and punch.is_late and punch.check_in_time and sd and sd.start_time:
        start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
        cutoff = start_dt + timedelta(minutes=grace_min)
        if punch.check_in_time > cutoff:
            late_by = round((punch.check_in_time - cutoff).total_seconds() / 3600, 1)

    return {
        "id": str(s.id),
        "shift": s.shift_code,
        "shiftName": sd.shift_name if sd else None,
        "date": s.date.isoformat() if s.date else None,
        "dayName": s.date.strftime("%A") if s.date else None,
        "shiftStartTime": start_time.isoformat() if start_time else None,
        "shiftEndTime": end_time.isoformat() if end_time else None,
        "timezone": sd.timezone if sd else None,
        "projectId": s.project_id,
        "projectName": s.project_name,
        "workStatus": s.work_location_status,
        "workAddress": s.work_address,
        "locationId": str(s.location_id) if s.location_id else None,
        "status": _roster_shift_status(s, punch, leave, sd, tz),
        "checkInTime": punch.check_in_time.isoformat() if punch and punch.check_in_time else None,
        "checkOutTime": punch.check_out_time.isoformat() if punch and punch.check_out_time else None,
        "checkInLat": float(punch.check_in_lat) if punch and punch.check_in_lat else None,
        "checkInLng": float(punch.check_in_lng) if punch and punch.check_in_lng else None,
        "checkInLocationName": punch.check_in_location_name if punch else None,
        "checkOutLat": float(punch.check_out_lat) if punch and punch.check_out_lat else None,
        "checkOutLng": float(punch.check_out_lng) if punch and punch.check_out_lng else None,
        "checkOutLocationName": punch.check_out_location_name if punch else None,
        "isLate": punch.is_late if punch else False,
        "lateByHours": late_by,
        "totalHours": float(punch.total_hours) if punch and punch.total_hours else None,
    }


def _lookup_attendance(att_map: dict, keycloak_user_id: str, shift_date: date, shift_code: str = None):
    a = att_map.get((keycloak_user_id, shift_date))
    if a and (shift_code is None or a.shift_code == shift_code):
        return a
    return None


def _leave_type_name_map(db: Session) -> dict:
    types = db.query(LeaveTypeConfig).all()
    return {t.code: t.name for t in types}


def _leave_type_display_name(code: str, name_map: dict) -> str:
    if code in name_map:
        return name_map[code]
    if code == "PL" and "CO" in name_map:
        return name_map["CO"]
    return code


class ShiftService:
    """Business logic for shift management now backed by PostgreSQL."""

    def _svc(self, db: Session) -> PGShiftService:
        return PGShiftService(db)

    def _def_svc(self, db: Session) -> PGShiftDefinitionService:
        return PGShiftDefinitionService(db)

    def _collect_warnings(self, db: Session, keycloak_id: str, start: date, end: date) -> list:
        warnings = []
        state = employee_state(db, keycloak_id)
        current = start
        while current <= end:
            if current.weekday() >= 5:
                warnings.append({"date": current.isoformat(), "type": "weekend", "label": current.strftime("%A")})

            holiday_rec = find_holiday(db, current, state)
            if holiday_rec:
                warnings.append({"date": current.isoformat(), "type": "holiday", "label": holiday_rec["name"]})

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
                work_address = emp.current_address or emp.permanent_address or "N/A"
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
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        user_ids = list({s.keycloak_user_id for s in shifts if s.keycloak_user_id})
        shift_codes = list({s.shift_code for s in shifts if s.shift_code})
        min_date = min(s.date for s in shifts)
        max_date = max(s.date for s in shifts)

        # Widen attendance query by one day on each side to catch night shifts
        # whose check-in UTC date differs from the shift's calendar date
        # (e.g. SMNI July 22 IST → check-in stored as UTC July 21).
        att_query = db.query(Attendance).filter(
            Attendance.keycloak_user_id.in_(user_ids),
            Attendance.attendance_date.between(min_date - timedelta(days=1), max_date + timedelta(days=1)),
        ).all()
        att_map = {(a.keycloak_user_id, a.attendance_date): a for a in att_query}

        sd_map = {}
        if shift_codes:
            sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
            sd_map = {s.shift_code: s for s in sds}

        leaves = db.query(Leave).filter(
            Leave.keycloak_user_id.in_(user_ids),
            Leave.approval_status == "approved",
            Leave.start_date <= max_date,
            Leave.end_date >= min_date,
        ).all()

        name_map = _leave_type_name_map(db)

        result = []
        for s in shifts:
            sd = sd_map.get(s.shift_code)
            leave = next((l for l in leaves if l.keycloak_user_id == s.keycloak_user_id and l.start_date <= s.date and l.end_date >= s.date), None)
            att = _lookup_attendance(att_map, s.keycloak_user_id, s.date, s.shift_code)

            derived_status = s.status
            if att and att.check_out_time:
                derived_status = "Ended"
            elif att and not att.check_out_time and s.date <= today:
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
                    end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz) if sd.end_time else None
                    derived_status = "Not checked in" if end_dt and datetime.now(tz) < end_dt else "Missed"
                else:
                    derived_status = "Yet to start"

            late_by = None
            if att and att.is_late and att.check_in_time and sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                shift_start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                cutoff = shift_start_dt + timedelta(minutes=resolve_shift_policy(sd).grace_minutes)
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
                "locationId": str(s.location_id) if s.location_id else None,
                "pincode": s.pincode,
                "leaveType": leave.leave_type if leave else None,
                "leaveTypeId": str(leave.leave_type_id) if leave and leave.leave_type_id else None,
                "leaveTypeName": _leave_type_display_name(leave.leave_type, name_map) if leave else None,
                "leaveId": str(leave.id) if leave else None,
                "leaveStatus": "on_leave" if leave else None,
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
                        "leaveId": str(lv.id),
                        "userId": None,
                        "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else email,
                        "userEmail": email,
                        "userDesignation": emp.designation if emp else None,
                        "projectId": None, "projectName": None,
                        "shift": None, "shiftName": None,
                        "workStatus": None, "status": "on_leave",
                        "workAddress": None, "pincode": None,
                        "leaveType": lv.leave_type,
                        "leaveTypeId": str(lv.leave_type_id) if lv.leave_type_id else None,
                        "leaveTypeName": _leave_type_display_name(lv.leave_type, name_map),
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

        result.sort(key=lambda x: x["date"], reverse=False)
        return result

    async def get_roster(self, db: Session, start_date: str, end_date: str, user_id: int = None) -> dict:
        """Single-user roster grouped by date, with one entry per shift (multi-shift aware).

        Check-in/out, late and hours are read from `shift_attendance` (per-shift
        punches), NOT the single-row-per-day `attendance` table, so multiple
        shifts on the same day are represented correctly.
        """
        svc = self._svc(db)
        shifts = svc.fetch_by_date_range(start_date, end_date, user_id)

        sd_date = date.fromisoformat(start_date)
        ed_date = date.fromisoformat(end_date)

        shift_ids = [s.id for s in shifts]
        shift_codes = {s.shift_code for s in shifts if s.shift_code}
        user_ids = {s.keycloak_user_id for s in shifts if s.keycloak_user_id}

        punch_map: dict = {}
        if shift_ids:
            punches = db.query(AttendancePunch).filter(
                AttendancePunch.shift_id.in_(shift_ids)
            ).all()
            punch_map = {p.shift_id: p for p in punches}

        sd_map: dict = {}
        if shift_codes:
            sds = db.query(ShiftDefinition).filter(
                ShiftDefinition.shift_code.in_(shift_codes)
            ).all()
            sd_map = {d.shift_code: d for d in sds}

        emp = None
        if user_ids:
            emp = db.query(EmployeeMaster).filter(
                EmployeeMaster.keycloak_user_id == next(iter(user_ids))
            ).first()

        leaves = []
        if user_ids:
            leaves = db.query(Leave).filter(
                Leave.keycloak_user_id.in_(user_ids),
                Leave.approval_status == "approved",
                Leave.start_date <= ed_date,
                Leave.end_date >= sd_date,
            ).all()

        name_map = _leave_type_name_map(db)

        by_date: dict = {}
        for s in shifts:
            d_str = s.date.isoformat()
            punch = punch_map.get(s.id)
            sd = sd_map.get(s.shift_code)
            leave = next(
                (
                    lv for lv in leaves
                    if lv.keycloak_user_id == s.keycloak_user_id
                    and lv.start_date <= s.date
                    and lv.end_date >= s.date
                ),
                None,
            )
            record = _build_shift_record(s, punch, sd, leave)
            entry = by_date.setdefault(d_str, {"date": d_str, "shifts": [], "leave": None})
            entry["shifts"].append(record)

        for lv in leaves:
            current = lv.start_date
            while current <= lv.end_date:
                d_str = current.isoformat()
                entry = by_date.setdefault(d_str, {"date": d_str, "shifts": [], "leave": None})
                if not entry["leave"]:
                    entry["leave"] = {
                        "id": str(lv.id),
                        "leaveType": lv.leave_type,
                        "leaveTypeName": _leave_type_display_name(lv.leave_type, name_map),
                        "startDate": lv.start_date.isoformat(),
                        "endDate": lv.end_date.isoformat(),
                    }
                current += timedelta(days=1)

        timeline = [by_date[k] for k in sorted(by_date)]
        return {"timeline": timeline, "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else None, "userEmail": emp.user_email if emp else None}

    async def get_shifts_v1(
        self, db: Session, emails: List[str],
        from_date: str = None, to_date: str = None,
        work_status: str = None, project_id: int = None,
        status: str = None, is_late: bool = None,
        search: str = None, include_name_search: bool = False,
        sort_by: str = "date", sort_dir: str = "asc",
        page: int = 1, page_size: int = 50, export: bool = False,
    ) -> dict:
        """Relationship-based, per-shift shift/attendance history (multi-shift aware).

        Reads each shift's punch via `Shift.attendance_punch` (shift_attendance FK
        relationship), falling back to the legacy `attendance` table for old
        single-shift data. No virtual record synthesis.
        """
        kemps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.in_(emails)).all()
        kemp_map = {e.keycloak_user_id: e for e in kemps if e.keycloak_user_id}
        kemp_by_email = {e.user_email: e for e in kemps if e.user_email}
        user_ids = list({e.keycloak_user_id for e in kemps if e.keycloak_user_id})

        query = (
            db.query(Shift)
            .options(selectinload(Shift.attendance_punch))
            .filter(Shift.user_email.in_(emails))
        )
        if from_date:
            query = query.filter(Shift.date >= date.fromisoformat(from_date))
        if to_date:
            query = query.filter(Shift.date <= date.fromisoformat(to_date))
        if work_status:
            query = query.filter(Shift.work_location_status == work_status)
        if project_id:
            query = query.filter(Shift.project_id == project_id)
        shifts = query.order_by(Shift.date.desc()).all()

        shift_user_ids = {s.keycloak_user_id for s in shifts if s.keycloak_user_id}
        all_user_ids = list(set(user_ids) | shift_user_ids)

        f_date = date.fromisoformat(from_date) if from_date else None
        t_date = date.fromisoformat(to_date) if to_date else None

        ACTIVE_LEAVE_STATUSES = ["approved", "emergency", "cancellation_requested", "cancellation_rejected"]

        leaves = []
        if all_user_ids:
            leave_query = db.query(Leave).filter(
                Leave.keycloak_user_id.in_(all_user_ids),
                func.lower(Leave.approval_status).in_(ACTIVE_LEAVE_STATUSES),
            )
            if f_date:
                leave_query = leave_query.filter(Leave.end_date >= f_date)
            if t_date:
                leave_query = leave_query.filter(Leave.start_date <= t_date)
            leaves = leave_query.all()

        if not shifts and not leaves:
            return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        shift_codes = list({s.shift_code for s in shifts if s.shift_code})

        # Legacy fallback: old /check-in wrote `attendance`, not shift_attendance
        legacy_map: dict = {}
        if shifts and all_user_ids:
            min_date = min(s.date for s in shifts)
            max_date = max(s.date for s in shifts)
            legacy_rows = db.query(Attendance).filter(
                Attendance.keycloak_user_id.in_(all_user_ids),
                Attendance.attendance_date.between(min_date - timedelta(days=1), max_date + timedelta(days=1)),
            ).all()
            legacy_map = {(a.keycloak_user_id, a.attendance_date, a.shift_code): a for a in legacy_rows}

        sd_map: dict = {}
        if shift_codes:
            sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
            sd_map = {d.shift_code: d for d in sds}

        name_map = _leave_type_name_map(db)

        result = []
        for s in shifts:
            punch = s.attendance_punch
            if punch is None:
                punch = legacy_map.get((s.keycloak_user_id, s.date, s.shift_code))
            sd = sd_map.get(s.shift_code)
            emp = kemp_map.get(s.keycloak_user_id) or kemp_by_email.get(s.user_email)
            leave = next(
                (
                    lv for lv in leaves
                    if lv.keycloak_user_id == s.keycloak_user_id
                    and lv.start_date <= s.date
                    and lv.end_date >= s.date
                ),
                None,
            )

            rec = _build_shift_record(s, punch, sd, leave)
            rec["userEmail"] = s.user_email
            rec["userName"] = (
                f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ")
                if emp else s.user_email
            )
            rec["userDesignation"] = emp.designation if emp else None
            rec["leaveType"] = leave.leave_type if leave else None
            rec["leaveTypeId"] = str(leave.leave_type_id) if leave and leave.leave_type_id else None
            rec["leaveTypeName"] = _leave_type_display_name(leave.leave_type, name_map) if leave else None
            rec["leaveId"] = str(leave.id) if leave else None
            rec["leaveStatus"] = "on_leave" if leave else None
            rec["perDiemEligible"] = s.per_diem_eligible
            rec["conveyanceEligible"] = s.conveyance_eligible
            rec["endDate"] = s.end_date.isoformat() if s.end_date else None
            rec["pincode"] = s.pincode
            result.append(rec)

        # Leave-only records (approved leave days with no scheduled shift)
        shifted_dates = {(s.user_email, s.date) for s in shifts}
        if not work_status and not project_id:
            for lv in leaves:
                if not lv.keycloak_user_id:
                    continue
                lemp = kemp_map.get(lv.keycloak_user_id)
                lv_email = lemp.user_email if lemp else lv.user_email
                ld = max(lv.start_date, f_date) if f_date else lv.start_date
                rd = min(lv.end_date, t_date) if t_date else lv.end_date
                current = ld
                while current <= rd:
                    if (lv_email, current) not in shifted_dates:
                        result.append({
                            "id": None,
                            "leaveId": str(lv.id),
                            "userEmail": lv_email,
                            "userName": f"{lemp.first_name} {lemp.middle_name or ''} {lemp.last_name}".strip().replace("  ", " ") if lemp else (lv_email or ""),
                            "userDesignation": lemp.designation if lemp else None,
                            "projectId": None, "projectName": None,
                            "shift": None, "shiftName": None,
                            "workStatus": None, "status": "on_leave",
                            "workAddress": None, "pincode": None,
                            "leaveType": lv.leave_type,
                            "leaveTypeId": str(lv.leave_type_id) if lv.leave_type_id else None,
                            "leaveTypeName": _leave_type_display_name(lv.leave_type, name_map),
                            "leaveStatus": "on_leave",
                            "perDiemEligible": False, "conveyanceEligible": False,
                            "date": current.isoformat(),
                            "dayName": current.strftime("%A"),
                            "endDate": current.isoformat(),
                            "shiftStartTime": None, "shiftEndTime": None,
                            "timezone": None,
                            "checkInTime": None, "checkOutTime": None,
                            "checkInLat": None, "checkInLng": None,
                            "checkOutLat": None, "checkOutLng": None,
                            "checkInLocationName": None, "checkOutLocationName": None,
                            "isLate": False, "lateByHours": None, "totalHours": None,
                        })
                    current += timedelta(days=1)

        # is_late filter
        if is_late is not None:
            result = [r for r in result if r.get("isLate") == is_late]

        # Search filter
        if search:
            term = search.strip().lower()[:100]
            fields = ["userName", "userEmail", "shift", "shiftName", "projectName", "workAddress", "workStatus", "leaveType"]
            if not include_name_search:
                fields = [f for f in fields if f != "userName"]
            result = [r for r in result if any(term in str(r.get(f) or "").lower() for f in fields)]

        # Status filter (multi-select)
        if status:
            statuses = {x.strip().lower() for x in status.split(",") if x.strip()}
            if statuses:
                result = [r for r in result if str(r.get("status") or "").lower() in statuses]

        # Sort
        allowed_sort = {"date", "shiftStartTime", "shiftCode", "workStatus", "userName"}
        sort_by = sort_by if sort_by in allowed_sort else "date"
        sort_key = "shift" if sort_by == "shiftCode" else sort_by
        result.sort(key=lambda r: (r.get(sort_key) or ""), reverse=(sort_dir == "desc"))

        # Paginate
        total = len(result)
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
        if export:
            return {"records": result, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}
        offset = (page - 1) * page_size
        return {
            "records": result[offset:offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
        offset = (page - 1) * page_size
        return {
            "records": result[offset:offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def get_team_shifts(self, db: Session, emails: list, from_date: str = None, to_date: str = None) -> List[dict]:
        query = db.query(Shift).filter(Shift.user_email.in_(emails))
        if from_date:
            query = query.filter(Shift.date >= date.fromisoformat(from_date))
        if to_date:
            query = query.filter(Shift.date <= date.fromisoformat(to_date))
        shifts = query.order_by(Shift.date.desc()).all()
        if not shifts:
            return []

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        user_ids = list({s.keycloak_user_id for s in shifts if s.keycloak_user_id})
        shift_user_emails = list({s.user_email for s in shifts if s.user_email})
        shift_codes = list({s.shift_code for s in shifts if s.shift_code})
        min_date = min(s.date for s in shifts)
        max_date = max(s.date for s in shifts)

        att_query = db.query(Attendance).filter(
            Attendance.keycloak_user_id.in_(user_ids),
            Attendance.attendance_date.between(min_date - timedelta(days=1), max_date + timedelta(days=1)),
        ).all()
        att_map = {(a.keycloak_user_id, a.attendance_date): a for a in att_query}

        sd_map = {}
        if shift_codes:
            sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
            sd_map = {s.shift_code: s for s in sds}

        leaves = db.query(Leave).filter(
            Leave.keycloak_user_id.in_(user_ids),
            Leave.approval_status == "approved",
            Leave.start_date <= max_date,
            Leave.end_date >= min_date,
        ).all()

        name_map = _leave_type_name_map(db)

        emp_map = {}
        if shift_user_emails:
            emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.in_(shift_user_emails)).all()
            emp_map = {e.user_email: e for e in emps}

        result = []
        for s in shifts:
            emp = emp_map.get(s.user_email)
            sd = sd_map.get(s.shift_code)
            leave = next((l for l in leaves if l.keycloak_user_id == s.keycloak_user_id and l.start_date <= s.date and l.end_date >= s.date), None)
            att = _lookup_attendance(att_map, s.keycloak_user_id, s.date, s.shift_code)

            derived_status = s.status
            if att and att.check_out_time:
                derived_status = "Ended"
            elif att and not att.check_out_time and s.date <= today:
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
                    end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz) if sd.end_time else None
                    derived_status = "Not checked in" if end_dt and datetime.now(tz) < end_dt else "Missed"
                else:
                    derived_status = "Yet to start"

            late_by = None
            if att and att.is_late and att.check_in_time and sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                shift_start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                cutoff = shift_start_dt + timedelta(minutes=resolve_shift_policy(sd).grace_minutes)
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
                "locationId": str(s.location_id) if s.location_id else None,
                "pincode": s.pincode,
                "leaveType": leave.leave_type if leave else None,
                "leaveTypeId": str(leave.leave_type_id) if leave and leave.leave_type_id else None,
                "leaveTypeName": _leave_type_display_name(leave.leave_type, name_map) if leave else None,
                "leaveId": str(leave.id) if leave else None,
                "leaveStatus": "on_leave" if leave else None,
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
                            "leaveTypeId": str(lv.leave_type_id) if lv.leave_type_id else None,
                            "leaveTypeName": _leave_type_display_name(lv.leave_type, name_map),
                            "leaveId": str(lv.id),
                            "leaveStatus": "on_leave",
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

        result.sort(key=lambda x: x["date"], reverse=False)
        return result

    async def get_shifts_paginated(
        self, db: Session, emails: List[str],
        from_date: str = None, to_date: str = None,
        work_status: str = None, project_id: int = None,
        status: str = None,
        search: str = None, include_name_search: bool = False,
        sort_by: str = "date", sort_dir: str = "asc",
        page: int = 1, page_size: int = 50,
        export: bool = False,
    ) -> dict:
        query = db.query(Shift).filter(Shift.user_email.in_(emails))
        if from_date:
            query = query.filter(Shift.date >= date.fromisoformat(from_date))
        if to_date:
            query = query.filter(Shift.date <= date.fromisoformat(to_date))
        if work_status:
            query = query.filter(Shift.work_location_status == work_status)
        if project_id:
            query = query.filter(Shift.project_id == project_id)
        shifts = query.all()

        if not shifts:
            return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        user_ids = list({s.keycloak_user_id for s in shifts if s.keycloak_user_id})
        shift_codes = list({s.shift_code for s in shifts if s.shift_code})
        min_date = min(s.date for s in shifts)
        max_date = max(s.date for s in shifts)

        att_query = db.query(Attendance).filter(
            Attendance.keycloak_user_id.in_(user_ids),
            Attendance.attendance_date.between(min_date - timedelta(days=1), max_date + timedelta(days=1)),
        ).all()
        att_map = {(a.keycloak_user_id, a.attendance_date): a for a in att_query}

        sd_map = {}
        if shift_codes:
            sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
            sd_map = {s.shift_code: s for s in sds}

        leaves = db.query(Leave).filter(
            Leave.keycloak_user_id.in_(user_ids),
            Leave.approval_status == "approved",
            Leave.start_date <= max_date,
            Leave.end_date >= min_date,
        ).all()

        name_map = _leave_type_name_map(db)

        kemp_map = {}
        if user_ids:
            kemps = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id.in_(user_ids)).all()
            kemp_map = {e.keycloak_user_id: e for e in kemps}

        result = []
        for s in shifts:
            emp = kemp_map.get(s.keycloak_user_id)
            sd = sd_map.get(s.shift_code)
            leave = next((l for l in leaves if l.keycloak_user_id == s.keycloak_user_id and l.start_date <= s.date and l.end_date >= s.date), None)
            att = _lookup_attendance(att_map, s.keycloak_user_id, s.date, s.shift_code)

            derived_status = s.status
            if att and att.check_out_time:
                derived_status = "Ended"
            elif att and not att.check_out_time and s.date <= today:
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
                    end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz) if sd.end_time else None
                    derived_status = "Not checked in" if end_dt and datetime.now(tz) < end_dt else "Missed"
                else:
                    derived_status = "Yet to start"

            late_by = None
            if att and att.is_late and att.check_in_time and sd and sd.start_time:
                try:
                    tz = ZoneInfo(sd.timezone or "Asia/Kolkata")
                except Exception:
                    tz = ZoneInfo("Asia/Kolkata")
                shift_start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
                cutoff = shift_start_dt + timedelta(minutes=resolve_shift_policy(sd).grace_minutes)
                if att.check_in_time > cutoff:
                    late_by = round((att.check_in_time - cutoff).total_seconds() / 3600, 1)

            result.append({
                "id": str(s.id),
                "userId": s.redmine_user_id,
                "userEmail": s.user_email,
                "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else s.user_email,
                "userDesignation": emp.designation if emp else None,
                "projectId": s.project_id,
                "projectName": s.project_name,
                "shift": s.shift_code,
                "shiftName": sd.shift_name if sd else None,
                "workStatus": s.work_location_status,
                "status": derived_status,
                "workAddress": s.work_address,
                "locationId": str(s.location_id) if s.location_id else None,
                "pincode": s.pincode,
                "leaveType": leave.leave_type if leave else None,
                "leaveTypeId": str(leave.leave_type_id) if leave and leave.leave_type_id else None,
                "leaveTypeName": _leave_type_display_name(leave.leave_type, name_map) if leave else None,
                "leaveId": str(leave.id) if leave else None,
                "leaveStatus": "on_leave" if leave else None,
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
        for lv in leaves:
            if not lv.keycloak_user_id:
                continue
            # Skip leave injection when filtering by work status — leave rows have no work type
            if work_status:
                break
            lemp = kemp_map.get(lv.keycloak_user_id)
            lv_email = lemp.user_email if lemp else None
            current = lv.start_date
            while current <= lv.end_date:
                if (lv_email, current) not in shifted_dates:
                    result.append({
                        "id": None,
                        "leaveId": str(lv.id),
                        "userId": None,
                        "userEmail": lv_email,
                        "userName": f"{lemp.first_name} {lemp.middle_name or ''} {lemp.last_name}".strip().replace("  ", " ") if lemp else lv_email,
                        "userDesignation": lemp.designation if lemp else None,
                        "projectId": None, "projectName": None,
                        "shift": None, "shiftName": None,
                        "workStatus": None, "status": "on_leave",
                        "workAddress": None, "pincode": None,
                        "leaveType": lv.leave_type,
                        "leaveTypeId": str(lv.leave_type_id) if lv.leave_type_id else None,
                        "leaveTypeName": _leave_type_display_name(lv.leave_type, name_map),
                        "leaveStatus": "on_leave",
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

        # Search filter
        if search:
            term = search.strip().lower()[:100]
            fields = ["userName", "userEmail", "shift", "shiftName", "projectName", "workAddress", "workStatus", "leaveType"]
            if not include_name_search:
                fields = [f for f in fields if f != "userName"]
            result = [r for r in result if any(term in str(r.get(f) or "").lower() for f in fields)]

        # Status filter (case-insensitive, multi-select)
        if status:
            statuses = {s.strip().lower() for s in status.split(",") if s.strip()}
            if statuses:
                result = [r for r in result if str(r.get("status") or "").lower() in statuses]

        # Sort
        allowed_sort = {"date", "shiftStartTime", "shiftCode", "workStatus", "userName"}
        sort_by = sort_by if sort_by in allowed_sort else "date"
        # "shiftCode" maps to "shift" key in the record
        sort_key = "shift" if sort_by == "shiftCode" else sort_by
        result.sort(key=lambda r: (r.get(sort_key) or ""), reverse=(sort_dir == "desc"))

        # Paginate
        total = len(result)
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
        if export:
            return {"records": result, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}
        offset = (page - 1) * page_size
        records = result[offset:offset + page_size]

        return {"records": records, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}

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
        ).delete(synchronize_session=False)
        db.query(AttendancePunch).filter(
            AttendancePunch.shift_id == shift_id
        ).delete(synchronize_session=False)
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
            ).delete(synchronize_session=False)

            if shift.date == shift.end_date:
                db.query(AttendancePunch).filter(
                    AttendancePunch.shift_id == shift.id
                ).delete(synchronize_session=False)
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
            auto_checkout_enabled=data.get("autoCheckoutEnabled", DEFAULT_AUTO_CHECKOUT_ENABLED),
            auto_checkout_grace_minutes=data.get("autoCheckoutGraceMinutes", DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES),
            grace_minutes=data.get("graceMinutes", DEFAULT_GRACE_MINUTES),
            checkin_reminder_delay_minutes=data.get("checkinReminderDelayMinutes", DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES),
            checkout_reminder_grace_hours=data.get("checkoutReminderGraceHours", DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS),
            early_checkin_limit_minutes=data.get("earlyCheckinLimitMinutes", DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES),
            checkin_cutoff_minutes=data.get("checkinCutoffMinutes", DEFAULT_CHECKIN_CUTOFF_MINUTES),
            half_day_minutes=data.get("halfDayMinutes", DEFAULT_HALF_DAY_MINUTES),
        )
        clear_shift_definition_cache(data["shiftCode"])
        return self._serialize_def(sd)

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
        # Shift-level timing policy (optional fields; None = leave unchanged).
        for camel, col in {
            "autoCheckoutEnabled": "auto_checkout_enabled",
            "autoCheckoutGraceMinutes": "auto_checkout_grace_minutes",
            "graceMinutes": "grace_minutes",
            "checkinReminderDelayMinutes": "checkin_reminder_delay_minutes",
            "checkoutReminderGraceHours": "checkout_reminder_grace_hours",
            "earlyCheckinLimitMinutes": "early_checkin_limit_minutes",
            "checkinCutoffMinutes": "checkin_cutoff_minutes",
            "halfDayMinutes": "half_day_minutes",
        }.items():
            if data.get(camel) is not None:
                update_data[col] = data[camel]
        if update_data:
            svc.db.execute(
                sql_update(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id).values(**update_data)
            )
            svc.db.commit()
            clear_shift_definition_cache()
        return await self.get_shift_definition(db, shift_id)

    async def delete_shift_definition(self, db: Session, shift_id: str) -> bool:
        svc = self._def_svc(db)
        result = svc.db.execute(sql_delete(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id))
        svc.db.commit()
        clear_shift_definition_cache()
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
            "locationId": str(shift.location_id) if shift.location_id else None,
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
            "autoCheckoutEnabled": sd.auto_checkout_enabled,
            "autoCheckoutGraceMinutes": sd.auto_checkout_grace_minutes,
            "graceMinutes": sd.grace_minutes,
            "checkinReminderDelayMinutes": sd.checkin_reminder_delay_minutes,
            "checkoutReminderGraceHours": sd.checkout_reminder_grace_hours,
            "earlyCheckinLimitMinutes": sd.early_checkin_limit_minutes,
            "checkinCutoffMinutes": sd.checkin_cutoff_minutes,
            "halfDayMinutes": sd.half_day_minutes,
            "createdAt": sd.created_at,
            "updatedAt": sd.updated_at,
        }


shift_service = ShiftService()
