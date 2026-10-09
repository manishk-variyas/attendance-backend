import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

ACTIVE_LEAVE_STATUSES = ["approved", "emergency", "cancellation_requested", "cancellation_rejected"]
from typing import List, Optional

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_admin, require_admin_or_pm
from app.features.attendance.schemas import AdminAttendanceCreate, AdminAttendanceUpdate, AttendanceResponse
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift_definition import ShiftDefinition
from app.models.shift import Shift
from app.models.leave import Leave
from app.services.database.base_service import BaseService
from zoneinfo import ZoneInfo

from app.utils.shift_time import safe_zone
from app.utils.audit import audit_logger
from app.middleware.logging import _get_client_ip
from app.features.leaves.services.balance_utils import reverse_comp_off

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/attendance", tags=["admin-attendance"])


def _enrich_with_maps(data: dict, a: Attendance, loc_map: dict, sd_map: dict) -> dict:
    if a.location_id:
        office = loc_map.get(str(a.location_id))
        data["officeName"] = office.name if office else None
    else:
        data["officeName"] = None
    if a.shift_code:
        sd = sd_map.get(a.shift_code)
        if sd:
            data["shiftName"] = sd.shift_name
            tz = sd.timezone or "Asia/Kolkata"
            if sd.start_time:
                data["shiftStartTime"] = datetime.combine(a.attendance_date, sd.start_time, tzinfo=safe_zone(tz)).isoformat()
            if sd.end_time:
                data["shiftEndTime"] = datetime.combine(a.attendance_date, sd.end_time, tzinfo=safe_zone(tz)).isoformat()
    else:
        data["shiftName"] = None
    data["perDiemEligible"] = a.per_diem_eligible
    data["conveyanceEligible"] = a.conveyance_eligible
    return data


def _to_response_with_email_batched(
    a: Attendance, emp_map: dict, loc_map: dict, sd_map: dict,
    shift_map: dict, leave_map: dict, modified_by_map: dict
) -> dict:
    data = a.to_dict()
    data = _enrich_with_maps(data, a, loc_map, sd_map)

    emp = emp_map.get(a.keycloak_user_id)
    if emp:
        data["userEmail"] = emp.user_email
        data["userName"] = f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ")
        data["userDesignation"] = emp.designation

    data["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"
    data["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"
    data["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

    shift = shift_map.get((a.keycloak_user_id, a.attendance_date))
    data["shiftProject"] = shift.project_name if shift else None

    leave = leave_map.get((a.keycloak_user_id, a.attendance_date))
    data["onLeave"] = leave is not None
    data["leaveType"] = leave.leave_type if leave else None
    data["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
    data["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
    data["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
    data["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

    if a.check_out_time:
        if a.modified_by:
            eb = modified_by_map.get(a.modified_by)
            data["endedBy"] = f"{eb.first_name} {eb.last_name}".strip() if eb else a.modified_by
            data["endedByRole"] = eb.designation if eb else None
        elif emp:
            data["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
            data["endedByRole"] = emp.designation
        else:
            data["endedBy"] = None
            data["endedByRole"] = None
    else:
        data["endedBy"] = None
        data["endedByRole"] = None
    return data


def _to_response(db: Session, a: Attendance) -> dict:
    from uuid import UUID
    emp_map = {}
    loc_map = {}
    sd_map = {}
    shift_map = {}
    leave_map = {}
    modified_by_map = {}
    if a.keycloak_user_id:
        e = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
        if e:
            emp_map[a.keycloak_user_id] = e
    if a.location_id:
        o = db.query(OfficeLocation).filter(OfficeLocation.id == UUID(str(a.location_id))).first()
        if o:
            loc_map[str(a.location_id)] = o
    if a.shift_code:
        s = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == a.shift_code).first()
        if s:
            sd_map[a.shift_code] = s
    shift = db.query(Shift).filter(
        and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
    ).first()
    if shift:
        shift_map[(a.keycloak_user_id, a.attendance_date)] = shift
    leave = db.query(Leave).filter(
        and_(
            Leave.keycloak_user_id == a.keycloak_user_id,
            func.lower(Leave.approval_status).in_(ACTIVE_LEAVE_STATUSES),
            Leave.start_date <= a.attendance_date,
            Leave.end_date >= a.attendance_date,
        )
    ).first()
    if leave:
        leave_map[(a.keycloak_user_id, a.attendance_date)] = leave
    if a.modified_by:
        mb = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
        if mb:
            modified_by_map[a.modified_by] = mb
    return _to_response_with_email_batched(a, emp_map, loc_map, sd_map, shift_map, leave_map, modified_by_map)


def _build_virtual_record(db: Session, shift, emp) -> dict:
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    has_started = False
    if shift.shift_code:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift.shift_code).first()
        if sd and sd.start_time:
            tz = safe_zone(sd.timezone or "Asia/Kolkata")
            shift_start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz)
            has_started = now_ist >= shift_start_dt

    derived = "not_started" if not has_started else "no_show"

    return {
        "id": None,
        "keycloakUserId": shift.keycloak_user_id,
        "attendanceDate": shift.date.isoformat() if shift.date else None,
        "checkInTime": None, "checkInLat": None, "checkInLng": None, "checkInLocationName": None,
        "checkOutTime": None, "checkOutLat": None, "checkOutLng": None, "checkOutLocationName": None,
        "locationId": None, "officeName": None,
        "workLocationStatus": shift.work_location_status,
        "shiftCode": shift.shift_code, "shiftName": None,
        "totalHours": None, "isLate": False, "status": "present",
        "isManualEntry": False, "isSynced": False, "remarks": None,
        "userEmail": emp.user_email if emp else shift.user_email,
        "userName": f"{emp.first_name} {emp.last_name}".strip() if emp else shift.user_email,
        "userDesignation": emp.designation if emp else None,
        "derivedStatus": derived,
        "isAtAssignedLocation": None,
        "dayName": shift.date.strftime("%A") if shift.date else None,
        "shiftProject": shift.project_name,
        "onLeave": False,
        "leaveType": None,
        "leaveStartDate": None,
        "leaveEndDate": None,
        "leaveStartDay": None,
        "leaveEndDay": None,
        "endedBy": None,
        "endedByRole": None,
        "perDiemEligible": shift.per_diem_eligible,
        "conveyanceEligible": shift.conveyance_eligible,
    }


@router.post("", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
def admin_create_attendance(
    payload: AdminAttendanceCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    emp_svc = BaseService[EmployeeMaster](db)
    emp = emp_svc.fetch_one(EmployeeMaster, user_email=payload.user_email)
    if not emp or not emp.keycloak_user_id:
        raise HTTPException(status_code=404, detail="Employee not found or not onboarded")

    try:
        att_date = date.fromisoformat(payload.attendance_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attendance_date format. Use YYYY-MM-DD.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == emp.keycloak_user_id,
            Attendance.attendance_date == att_date,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Attendance record already exists for this date")

    IST = ZoneInfo("Asia/Kolkata")
    check_in = None
    check_out = None
    if payload.check_in_time:
        try:
            check_in = datetime.fromisoformat(payload.check_in_time)
            if check_in.tzinfo is None:
                check_in = check_in.replace(tzinfo=IST)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid check_in_time format")
    if payload.check_out_time:
        try:
            check_out = datetime.fromisoformat(payload.check_out_time)
            if check_out.tzinfo is None:
                check_out = check_out.replace(tzinfo=IST)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid check_out_time format")

    shift_rec = None
    if payload.shift_code and emp.keycloak_user_id:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == payload.shift_code).first()
        if not sd:
            raise HTTPException(status_code=400, detail=f"Invalid shift code: {payload.shift_code}")
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == emp.keycloak_user_id,
            Shift.date == att_date,
        ).first()
        if not shift_rec:
            raise HTTPException(status_code=400, detail=f"No shift assigned for this employee on {att_date}.")
        if shift_rec.shift_code != payload.shift_code:
            raise HTTPException(status_code=400, detail=f"Shift code '{payload.shift_code}' does not match assigned shift '{shift_rec.shift_code}'.")

    attendance = Attendance(
        keycloak_user_id=emp.keycloak_user_id,
        attendance_date=att_date,
        check_in_time=check_in,
        check_out_time=check_out,
        work_location_status=payload.work_location_status,
        shift_code=payload.shift_code,
        status=payload.status,
        is_late=payload.is_late,
        per_diem_eligible=payload.per_diem_eligible if payload.per_diem_eligible is not None else False,
        conveyance_eligible=payload.conveyance_eligible if payload.conveyance_eligible is not None else False,
        is_manual_entry=True,
        modified_by=current_user["sub"],
        remarks=payload.remarks,
        location_id=payload.location_id,
    )
    if payload.shift_code and shift_rec:
        shift_rec.status = "Ended" if payload.check_out_time else "In Progress"

    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    logger.info(f"Admin {current_user['email']} created attendance for {payload.user_email} on {att_date}")
    return _to_response(db, attendance)


@router.put("/{attendance_id}", response_model=AttendanceResponse)
def admin_update_attendance(
    attendance_id: uuid.UUID,
    payload: AdminAttendanceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    roles = current_user.get("roles", [])
    is_pm = "Admin" not in roles
    IST = ZoneInfo("Asia/Kolkata")

    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    non_remark_fields = any(v is not None for v in (
        payload.check_in_time, payload.check_out_time, payload.work_location_status,
        payload.shift_code, payload.status, payload.is_late, payload.per_diem_eligible,
        payload.conveyance_eligible, payload.location_id, payload.check_in_location_name,
        payload.check_out_location_name, payload.check_in_lat, payload.check_in_lng,
        payload.check_out_lat, payload.check_out_lng,
    ))

    if is_pm:
        if payload.remarks is None:
            raise HTTPException(status_code=400, detail="PM can only update remarks.")
        if non_remark_fields:
            raise HTTPException(status_code=403, detail="PM can only update remarks. Admin required for other fields.")
    else:
        if payload.check_in_time is not None:
            try:
                t = datetime.fromisoformat(payload.check_in_time) if payload.check_in_time else None
                if t and t.tzinfo is None:
                    t = t.replace(tzinfo=IST)
                attendance.check_in_time = t
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid check_in_time format")
        if payload.check_out_time is not None:
            try:
                t = datetime.fromisoformat(payload.check_out_time) if payload.check_out_time else None
                if t and t.tzinfo is None:
                    t = t.replace(tzinfo=IST)
                attendance.check_out_time = t
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid check_out_time format")
        if payload.work_location_status is not None:
            attendance.work_location_status = payload.work_location_status
        if payload.shift_code is not None:
            attendance.shift_code = payload.shift_code
        if payload.status is not None:
            attendance.status = payload.status
        if payload.is_late is not None:
            attendance.is_late = payload.is_late
        if payload.per_diem_eligible is not None:
            attendance.per_diem_eligible = payload.per_diem_eligible
        if payload.conveyance_eligible is not None:
            attendance.conveyance_eligible = payload.conveyance_eligible
        if payload.location_id is not None:
            from uuid import UUID
            try:
                attendance.location_id = UUID(str(payload.location_id)) if payload.location_id else None
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid location_id format")
        if payload.check_in_location_name is not None:
            attendance.check_in_location_name = payload.check_in_location_name
        if payload.check_out_location_name is not None:
            attendance.check_out_location_name = payload.check_out_location_name
        if payload.check_in_lat is not None:
            attendance.check_in_lat = payload.check_in_lat
        if payload.check_in_lng is not None:
            attendance.check_in_lng = payload.check_in_lng
        if payload.check_out_lat is not None:
            attendance.check_out_lat = payload.check_out_lat
        if payload.check_out_lng is not None:
            attendance.check_out_lng = payload.check_out_lng

    if payload.remarks is not None:
        attendance.remarks = payload.remarks

    # Validate check-out is not before check-in
    if attendance.check_in_time and attendance.check_out_time and attendance.check_out_time < attendance.check_in_time:
        raise HTTPException(status_code=400, detail="check_out_time cannot be before check_in_time")

    attendance.is_manual_entry = True
    attendance.modified_by = current_user["sub"]

    if attendance.shift_code and attendance.keycloak_user_id:
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == attendance.keycloak_user_id,
            Shift.date == attendance.attendance_date,
            Shift.shift_code == attendance.shift_code,
        ).first()
        if shift_rec:
            if attendance.check_out_time:
                shift_rec.status = "Ended"
            elif attendance.check_in_time:
                shift_rec.status = "In Progress"

    db.commit()
    db.refresh(attendance)

    changed_fields = ", ".join([k for k, v in payload.model_dump().items() if v is not None]) or "none"

    target_email = None
    emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.keycloak_user_id == attendance.keycloak_user_id
    ).first()
    if emp:
        target_email = emp.user_email

    att_date = attendance.attendance_date.isoformat() if attendance.attendance_date else "-"
    editor_email = current_user.get("email") or current_user.get("sub", "-")

    audit_logger.info(
        f"Attendance for {target_email or attendance.keycloak_user_id} on {att_date} edited by {editor_email} — changed: {changed_fields}",
        extra={
            "correlation_id": "-",
            "extra_data": {
                "action": "attendance_update",
                "username": editor_email,
                "status": "success",
                "client_ip": "-",
                "attendance_id": str(attendance_id),
                "attendance_date": att_date,
                "employee_email": target_email,
                "edited_by": editor_email,
                "changed_fields": changed_fields,
            },
        },
    )

    logger.info(f"Admin {current_user['email']} updated attendance {attendance_id}")
    return _to_response(db, attendance)


@router.get("/{attendance_id}", response_model=AttendanceResponse)
def admin_get_attendance(
    attendance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return _to_response(db, attendance)


@router.get("")
def admin_list_attendance(
    user_email: Optional[str] = Query(None, description="Filter by employee email"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    is_synced: Optional[bool] = Query(None, description="Filter by synced records"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin_or_pm),
):
    query = db.query(Attendance)

    if user_email:
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.user_email.ilike(f"{user_email}%")).limit(50).all()
        if not emps:
            return {"total": 0, "records": []}
        keycloak_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
        if not keycloak_ids:
            return {"total": 0, "records": []}
        query = query.filter(Attendance.keycloak_user_id.in_(keycloak_ids))

    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format")
    if status:
        query = query.filter(Attendance.status == status)
    if is_synced is not None:
        query = query.filter(Attendance.is_synced == is_synced)

    records = query.order_by(Attendance.attendance_date.desc()).all()

    f = date.fromisoformat(from_date) if from_date else None
    t = date.fromisoformat(to_date) if to_date else None

    # Fetch leaves in range
    leave_query = db.query(Leave).filter(
        func.lower(Leave.approval_status).in_(ACTIVE_LEAVE_STATUSES),
    )
    if f:
        leave_query = leave_query.filter(Leave.end_date >= f)
    if t:
        leave_query = leave_query.filter(Leave.start_date <= t)
    leave_list = leave_query.all()

    if not records and not leave_list and not (f and t):
        return {"total": 0, "records": []}

    leave_user_ids = {l.keycloak_user_id for l in leave_list if l.keycloak_user_id}
    user_ids = list({r.keycloak_user_id for r in records if r.keycloak_user_id} | leave_user_ids)
    loc_ids = list({str(r.location_id) for r in records if r.location_id})
    shift_codes = list({r.shift_code for r in records if r.shift_code})
    modified_by_ids = list({r.modified_by for r in records if r.modified_by})
    dates_set = list({r.attendance_date for r in records})

    emp_map = {}
    if user_ids:
        emps = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id.in_(user_ids)).all()
        emp_map = {e.keycloak_user_id: e for e in emps}

    loc_map = {}
    if loc_ids:
        from uuid import UUID
        locs = db.query(OfficeLocation).filter(OfficeLocation.id.in_([UUID(x) for x in loc_ids])).all()
        loc_map = {str(l.id): l for l in locs}

    sd_map = {}
    if shift_codes:
        sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
        sd_map = {s.shift_code: s for s in sds}

    shift_map = {}
    if user_ids and dates_set:
        shifts = db.query(Shift).filter(
            Shift.keycloak_user_id.in_(user_ids),
            Shift.date.in_(dates_set),
        ).all()
        shift_map = {(s.keycloak_user_id, s.date): s for s in shifts}

    leave_map = {}
    for l in leave_list:
        if l.keycloak_user_id:
            ld = max(l.start_date, f) if f else l.start_date
            rd = min(l.end_date, t) if t else l.end_date
            current = ld
            while current <= rd:
                leave_map[(l.keycloak_user_id, current)] = l
                current += timedelta(days=1)

    modified_by_map = {}
    if modified_by_ids:
        mb_emps = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id.in_(modified_by_ids)).all()
        modified_by_map = {e.keycloak_user_id: e for e in mb_emps}

    result = [_to_response_with_email_batched(r, emp_map, loc_map, sd_map, shift_map, leave_map, modified_by_map) for r in records]
    covered = {(r.keycloak_user_id, r.attendance_date) for r in records}

    if f and t:
        leave_covered = set(leave_map.keys())

        shift_query = db.query(Shift).filter(
            and_(
                Shift.date >= f,
                Shift.date <= t,
            )
        )
        for s in shift_query.order_by(Shift.date.desc()).all():
            if (s.keycloak_user_id, s.date) in leave_covered:
                continue
            if (s.keycloak_user_id, s.date) in covered:
                continue
            emp = emp_map.get(s.keycloak_user_id) or (
                db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == s.keycloak_user_id).first()
            )
            record = _build_virtual_record(db, s, emp)
            result.append(record)
            covered.add((s.keycloak_user_id, s.date))

    for l in leave_list:
        if not l.keycloak_user_id:
            continue
        ld = max(l.start_date, f) if f else l.start_date
        rd = min(l.end_date, t) if t else l.end_date
        current = ld
        while current <= rd:
            if (l.keycloak_user_id, current) not in covered:
                emp = emp_map.get(l.keycloak_user_id)
                record = {
                    "id": None, "keycloakUserId": l.keycloak_user_id,
                    "attendanceDate": current.isoformat(),
                    "checkInTime": None, "checkInLat": None, "checkInLng": None, "checkInLocationName": None,
                    "checkOutTime": None, "checkOutLat": None, "checkOutLng": None, "checkOutLocationName": None,
                    "locationId": None, "officeName": None,
                    "workLocationStatus": None, "shiftCode": None, "shiftName": None,
                    "totalHours": None, "isLate": False, "status": "on_leave",
                    "isManualEntry": False, "isSynced": False, "remarks": None,
                    "userEmail": emp.user_email if emp else l.user_email or "",
                    "userName": f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else (l.user_email or ""),
                    "userDesignation": emp.designation if emp else None,
                    "derivedStatus": "on_leave",
                    "isAtAssignedLocation": None,
                    "dayName": current.strftime("%A"),
                    "shiftProject": None,
                    "onLeave": True,
                    "leaveType": l.leave_type,
                    "leaveStartDate": l.start_date.isoformat() if l.start_date else None,
                    "leaveEndDate": l.end_date.isoformat() if l.end_date else None,
                    "leaveStartDay": l.start_date.strftime("%A") if l.start_date else None,
                    "leaveEndDay": l.end_date.strftime("%A") if l.end_date else None,
                    "endedBy": None, "endedByRole": None,
                }
                result.append(record)
                covered.add((l.keycloak_user_id, current))
            current += timedelta(days=1)

    return {"total": len(result), "records": result}


@router.delete("/{attendance_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_attendance(
    attendance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # Capture fields BEFORE delete (the ORM object expires after db.delete)
    att_date = attendance.attendance_date
    att_user_kc = attendance.keycloak_user_id
    was_comp_off_credited = bool(attendance.comp_off_credited)

    # Resolve the affected employee's email (NOT the raw keycloak UUID) for the audit log
    target_email = None
    target_name = None
    if att_user_kc:
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == att_user_kc).first()
        if emp:
            target_email = emp.user_email
            target_name = f"{emp.first_name or ''} {emp.last_name or ''}".strip() or emp.user_email

    if attendance.check_in_time is not None and attendance.keycloak_user_id and attendance.attendance_date:
        shift_rec = db.query(Shift).filter(
            Shift.keycloak_user_id == attendance.keycloak_user_id,
            Shift.date == attendance.attendance_date,
        ).first()
        if shift_rec:
            shift_rec.status = "Yet to start"

    # If this attendance had a comp-off credited for a holiday, reverse it before
    # deleting the record. This prevents orphan comp-off credits.
    compoff_reversed = False
    if was_comp_off_credited and att_user_kc and att_date:
        compoff_reversed = reverse_comp_off(db, att_user_kc, att_date)

    db.delete(attendance)
    db.commit()

    logger.info(f"Admin {current_user['email']} deleted attendance {attendance_id}")
    audit_logger.info(
        f"Attendance deleted for {target_name or target_email or att_user_kc} on {att_date} by {current_user.get('email')}",
        extra={
            "correlation_id": "-",
            "extra_data": {
                "action": "attendance_delete",
                "username": current_user.get("email"),
                "status": "success",
                "client_ip": "-",
                "attendance_id": str(attendance_id),
                "attendance_date": att_date.isoformat() if att_date else None,
                "target_user_email": target_email,
                "compoff_reversed": compoff_reversed,
            },
        },
    )
    if was_comp_off_credited:
        audit_logger.info(
            f"Comp-off reversed for {target_name or target_email or att_user_kc} (holiday {att_date}) — attendance for {att_date} deleted by {current_user.get('email')}",
            extra={
                "correlation_id": "-",
                "extra_data": {
                    "action": "compoff_reversed",
                    "username": current_user.get("email"),
                    "status": "success" if compoff_reversed else "warning",
                    "client_ip": "-",
                    "attendance_id": str(attendance_id),
                    "attendance_date": att_date.isoformat() if att_date else None,
                    "target_user_email": target_email,
                    "reason": "attendance_deleted",
                },
            },
        )
