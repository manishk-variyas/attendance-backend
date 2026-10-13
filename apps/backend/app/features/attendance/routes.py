import logging
from datetime import date, datetime, time, timezone, timedelta
from typing import Optional, List
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_active
from app.features.attendance.schemas import CheckInRequest, CheckOutRequest, AttendanceResponse, CompleteAttendanceRequest, TodayAttendanceResponse, TodayShiftsResponse, ShiftCheckInRequest, ShiftCheckOutRequest
from app.features.attendance.admin_routes import _to_response_with_email_batched, _build_virtual_record
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.location import UserLocation
from app.models.holiday import Holiday
from app.utils.holiday_scope import employee_state, find_holiday
from app.models.leave import Leave
from app.models.attendance_punch import AttendancePunch
from app.features.attendance.services.shift_attendance_service import shift_attendance_service
from app.features.attendance.services.geo import determine_work_location, location_name, find_office_at
from app.features.shifts.service import shift_service
from app.features.leaves.services.balance_utils import credit_comp_off
from app.features.redmine.sql_service import RedmineSQLService
from app.core.cache import get_company_settings, get_shift_definition
from app.services.email_service import email_service
from app.utils.audit import audit_logger
from app.utils.device import device_label, compute_device_fingerprint, normalize_ua
from app.utils.shift_time import safe_zone, shift_end_datetime, resolve_timestamp, compute_total_hours
from app.utils.excel import build_attendance_excel
from app.middleware.logging import _get_client_ip
from app.utils.shift_policy import resolve_shift_policy, ShiftPolicy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])


def _credit_comp_off_if_holiday(db: Session, attendance: Attendance) -> None:
    if attendance.comp_off_credited:
        return
    if not attendance.check_out_time:
        return

    holiday = find_holiday(db, attendance.attendance_date, employee_state(db, attendance.keycloak_user_id))
    if not holiday:
        return

    keycloak_id = attendance.keycloak_user_id
    credit_comp_off(db, keycloak_id, attendance.attendance_date)
    attendance.comp_off_credited = True
    attendance.remarks = ", ".join(filter(None, [attendance.remarks, f"Compoff Auto Credited - {holiday['name']}"]))
    db.commit()
    logger.info(f"Comp-off credited: {keycloak_id} for {holiday['name']} on {attendance.attendance_date}")


def _find_matching_shift(db: Session, keycloak_user_id: str, shift_code: str,
                         check_in_time: datetime) -> Optional[Shift]:
    """Return the shift whose active window contains check_in_time.
    Uses shift_master.timezone for timezone-aware window computation.
    Only searches shifts within ±1 day of the check-in date."""
    sd = get_shift_definition(db, shift_code)
    if not sd:
        return None
    tz = safe_zone(sd.timezone or "Asia/Kolkata")
    att_date = check_in_time.astimezone(tz).date()
    candidates = db.query(Shift).filter(
        Shift.keycloak_user_id == keycloak_user_id,
        Shift.shift_code == shift_code,
        Shift.date >= att_date - timedelta(days=1),
        Shift.date <= att_date + timedelta(days=1),
    ).all()
    for c in candidates:
        win_start = datetime.combine(c.date, sd.start_time, tzinfo=tz)
        win_end = shift_end_datetime(c.date, sd.start_time, sd.end_time, tz)
        if win_start <= check_in_time <= win_end:
            return c
    if candidates:
        candidates.sort(key=lambda x: abs((x.date - att_date).days))
        return candidates[0]
    return None


def _to_response(db: Session, a: Attendance) -> dict:
    data = a.to_dict()
    if a.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == a.location_id).first()
        data["officeName"] = office.name if office else None
    else:
        data["officeName"] = None
    if a.shift_code:
        shift_def = get_shift_definition(db, a.shift_code)
        data["shiftName"] = shift_def.shift_name if shift_def else None
    else:
        data["shiftName"] = None
    data["perDiemEligible"] = a.per_diem_eligible
    data["conveyanceEligible"] = a.conveyance_eligible
    return data


@router.post("/check-in", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
def check_in(
    request: Request,
    payload: CheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    target_date = today
    if payload.date:
        try:
            target_date = date.fromisoformat(payload.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        if target_date > today:
            raise HTTPException(status_code=400, detail="Cannot check in for a future date.")
        if target_date < today - timedelta(days=1):
            raise HTTPException(status_code=400, detail="Can only check in for today or yesterday.")

    if target_date == today:
        open_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == today - timedelta(days=1),
                Attendance.check_out_time.is_(None),
            )
        ).first()
        if open_att:
            raise HTTPException(status_code=400, detail="Close yesterday's attendance before checking in today.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()
    if existing:
        return JSONResponse(content=_to_response(db, existing), status_code=200)

    on_leave = db.query(Leave).filter(
        and_(
            Leave.keycloak_user_id == keycloak_user_id,
            Leave.approval_status.in_(["approved", "emergency"]),
            Leave.start_date <= target_date,
            Leave.end_date >= target_date,
        )
    ).first()
    if on_leave:
        raise HTTPException(status_code=400, detail="You are on leave today. Cannot check in.")

    check_in_time, is_synced = resolve_timestamp(payload.clientTimestamp)
    server_received_at = datetime.now(timezone.utc)
    now = check_in_time

    if target_date != today:
        is_synced = True

    shift_code = None
    is_late = False
    shift_start_time = None
    shift_tz = "Asia/Kolkata"

    # Widen date range so overnight / timezone-crossing shifts are visible
    # when the server runs on UTC (same fix as /today endpoint).
    q_start = target_date - timedelta(days=1)
    q_end = target_date + timedelta(days=1)

    all_shifts_today = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            or_(
                Shift.date.in_([q_start, target_date, q_end]),
                and_(Shift.end_date.is_not(None), Shift.end_date >= q_start),
            ),
        )
    ).all()

    all_shifts_today.sort(key=lambda s: 0 if s.date == target_date else 1)

    if all_shifts_today:
        shift_codes = {s.shift_code for s in all_shifts_today if s.shift_code}
        shift_defs = {
            sd.shift_code: sd
            for sd in db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
        }

        now_tz = now.astimezone(timezone.utc)
        active_shift = None
        next_upcoming = None
        earliest = None
        earliest_dt = None

        for s in all_shifts_today:
            sd = shift_defs.get(s.shift_code)
            if not sd:
                continue
            tz = safe_zone(sd.timezone or "Asia/Kolkata")
            # Use the shift's own calendar date — same timezone-aware fix as _pick_shift
            start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
            end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz)

            if earliest_dt is None or start_dt < earliest_dt:
                earliest_dt = start_dt
                earliest = (s, sd, tz, start_dt, end_dt)

            if start_dt <= now_tz <= end_dt:
                active_shift = (s, sd, tz, start_dt, end_dt)
                break

            if next_upcoming is None and start_dt > now_tz:
                next_upcoming = (s, sd, tz, start_dt, end_dt)

        picked = active_shift or next_upcoming
        if not picked:
            raise HTTPException(
                status_code=400,
                detail="All shifts for today have ended. No active or upcoming shift.",
            )
        s, sd, tz, start_dt, end_dt = picked
        today = datetime.now(tz).date()
        shift_code = s.shift_code
        shift_start_time = sd.start_time
        shift_tz = sd.timezone or "Asia/Kolkata"

        if s.date > target_date:
            raise HTTPException(status_code=400,
                detail=f"Your shift is scheduled for {s.date.isoformat()}. Cannot check in before the shift date.")

    if shift_code and shift_start_time and now.tzinfo and sd:
        policy = resolve_shift_policy(sd)
        shift_start_dt = datetime.combine(target_date, shift_start_time, tzinfo=safe_zone(shift_tz))
        if now < shift_start_dt - timedelta(minutes=policy.early_checkin_limit_minutes):
            raise HTTPException(
                status_code=400,
                detail=f"Too early to check in. Your shift starts at {shift_start_time.strftime('%H:%M')} on {target_date.isoformat()}.",
            )
        if now > shift_start_dt + timedelta(minutes=policy.grace_minutes):
            is_late = True

    emp = getattr(request.state, "employee", None)
    location_id = emp.location_id if emp else None
    if shift_code and s:
        shift_loc = s.location_id
        if shift_loc:
            location_id = shift_loc

    is_wfh_shift = shift_code and s and s.work_location_status == "WFH"

    if is_wfh_shift:
        work_location_status = "WFH"
        check_in_location_name = "Home"
    else:
        work_location_status = determine_work_location(db, payload.latitude, payload.longitude, location_id)
        if work_location_status == "OFFICE" and location_id:
            office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
            check_in_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
        else:
            check_in_location_name = location_name(db, payload.latitude, payload.longitude, location_id)

    remarks = None
    if target_date != today:
        remarks = "Next-day check-in"

    attendance = Attendance(
        keycloak_user_id=keycloak_user_id,
        attendance_date=target_date,
        check_in_time=check_in_time,
        check_in_lat=payload.latitude,
        check_in_lng=payload.longitude,
        check_in_location_name=check_in_location_name,
        work_location_status=work_location_status,
        shift_code=shift_code,
        is_late=is_late,
        location_id=location_id,
        shift_id=s.id if (shift_code and s) else None,
        actual_location_id=(
            None if is_wfh_shift
            else find_office_at(db, payload.latitude, payload.longitude)
        ),
        check_in_device=device_label(request),
        is_synced=is_synced,
        server_received_at=server_received_at,
        remarks=remarks,
    )
    db.add(attendance)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == target_date,
            )
        ).first()
        if existing:
            return JSONResponse(content=_to_response(db, existing), status_code=200)
        raise
    db.refresh(attendance)

    if shift_code and s:
        s.status = "In Progress"
        db.commit()

    user_email = current_user.get("email")
    if user_email and payload.latitude is not None and payload.longitude is not None:
        existing_loc = db.query(UserLocation).filter(UserLocation.email == user_email).first()
        if existing_loc:
            existing_loc.latitude = payload.latitude
            existing_loc.longitude = payload.longitude
            existing_loc.location_name = check_in_location_name
            existing_loc.updated_at = now
        else:
            db.add(UserLocation(email=user_email, latitude=payload.latitude, longitude=payload.longitude, location_name=check_in_location_name, updated_at=now))
        db.commit()

    logger.info(f"Check-in: {keycloak_user_id} on {target_date}")
    check_in_time_str = attendance.check_in_time.strftime("%H:%M:%S") if attendance.check_in_time else (now.strftime("%H:%M:%S") if now else "-")
    lat_str = f"{payload.latitude:.4f}" if payload.latitude is not None else "-"
    lng_str = f"{payload.longitude:.4f}" if payload.longitude is not None else "-"
    ua = (request.headers.get("user-agent") or "").strip()
    dev_label = device_label(request)
    dev_fp = compute_device_fingerprint(request)
    _, _, is_mobile = normalize_ua(ua)
    audit_logger.info(
        f"Check-in: {current_user.get('email') or keycloak_user_id} on {target_date} at {check_in_time_str} (lat={lat_str}, lng={lng_str})",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "attendance_checkin",
                "username": current_user.get("email") or keycloak_user_id,
                "status": "success",
                "client_ip": _get_client_ip(request),
                "attendance_id": str(attendance.id),
                "attendance_date": target_date.isoformat(),
                "check_in_time": now.isoformat(),
                "work_location_status": attendance.work_location_status,
                "location_id": str(attendance.location_id) if attendance.location_id else None,
                "latitude": float(payload.latitude) if payload.latitude is not None else None,
                "longitude": float(payload.longitude) if payload.longitude is not None else None,
                "is_late": attendance.is_late,
                "device": dev_label,
                "device_fingerprint": dev_fp,
                "is_mobile": is_mobile,
                "user_agent": ua[:300] or None,
            },
        },
    )
    return _to_response(db, attendance)


@router.post("/check-out", response_model=AttendanceResponse)
def check_out(
    request: Request,
    payload: CheckOutRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    target_date = today
    if payload.date:
        try:
            target_date = date.fromisoformat(payload.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        if target_date > today:
            raise HTTPException(status_code=400, detail="Cannot check out for a future date.")
        if target_date < today - timedelta(days=1):
            raise HTTPException(status_code=400, detail="Can only check out for today or yesterday.")

    attendance = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()
    if not attendance:
        attendance = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == target_date - timedelta(days=1),
                Attendance.check_out_time.is_(None),
            )
        ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail=f"No active check-in found for {target_date}")
    if attendance.check_out_time:
        return JSONResponse(content=_to_response(db, attendance), status_code=200)

    check_out_time, is_synced = resolve_timestamp(payload.clientTimestamp)
    attendance.check_out_time = check_out_time
    attendance.total_hours = compute_total_hours(attendance.check_in_time, check_out_time)
    if is_synced or target_date != today:
        attendance.is_synced = True
        if not attendance.server_received_at:
            attendance.server_received_at = datetime.now(timezone.utc)
    if target_date != today:
        attendance.remarks = ", ".join(filter(None, [attendance.remarks, "Next-day checkout"]))
    attendance.check_out_lat = payload.latitude
    attendance.check_out_lng = payload.longitude
    attendance.check_out_device = device_label(request)

    check_out_ws = determine_work_location(db, payload.latitude, payload.longitude, attendance.location_id)
    if check_out_ws == "OFFICE" and attendance.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == attendance.location_id).first()
        attendance.check_out_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
    else:
        attendance.check_out_location_name = location_name(db, payload.latitude, payload.longitude, attendance.location_id)

    if payload.remarks:
        attendance.remarks = payload.remarks

    # Determine effective shift date (timezone-aware) for early-leave check.
    # Look up the matching shift first so we use shift.date (e.g. July 22 IST)
    # instead of att.attendance_date (UTC July 21) for the end-time comparison.
    eff_date = attendance.attendance_date
    if attendance.shift_code and attendance.keycloak_user_id:
        shift_rec = _find_matching_shift(
            db, attendance.keycloak_user_id, attendance.shift_code, attendance.check_in_time
        )
        if shift_rec:
            eff_date = shift_rec.date
            shift_rec.status = "Ended"

    shift_start_time = None
    shift_end_time = None
    end_tz = "Asia/Kolkata"
    if attendance.shift_code:
        shift_def = get_shift_definition(db, attendance.shift_code)
        if shift_def:
            shift_start_time = shift_def.start_time
            shift_end_time = shift_def.end_time
            end_tz = shift_def.timezone or "Asia/Kolkata"
    if not shift_end_time:
        company = get_company_settings(db)
        if company and company.default_shift_end_time:
            shift_end_time = company.default_shift_end_time
            end_tz = company.default_timezone or "Asia/Kolkata"
    if shift_end_time and attendance.check_out_time.tzinfo:
        shift_end_dt = shift_end_datetime(eff_date, shift_start_time, shift_end_time, safe_zone(end_tz))
        if attendance.check_out_time < shift_end_dt:
            attendance.remarks = ", ".join(filter(None, [attendance.remarks, "Early leave"]))

    user_email = current_user.get("email")
    if user_email and payload.latitude is not None and payload.longitude is not None:
        existing_loc = db.query(UserLocation).filter(UserLocation.email == user_email).first()
        if existing_loc:
            existing_loc.latitude = payload.latitude
            existing_loc.longitude = payload.longitude
            existing_loc.location_name = attendance.check_out_location_name
            existing_loc.updated_at = attendance.check_out_time
        else:
            db.add(UserLocation(email=user_email, latitude=payload.latitude, longitude=payload.longitude, location_name=attendance.check_out_location_name, updated_at=attendance.check_out_time))
        db.commit()

    db.commit()
    db.refresh(attendance)

    _credit_comp_off_if_holiday(db, attendance)

    logger.info(f"Check-out: {keycloak_user_id} on {today}")
    ci_str = attendance.check_in_time.strftime("%H:%M:%S") if attendance.check_in_time else "-"
    co_str = attendance.check_out_time.strftime("%H:%M:%S") if attendance.check_out_time else "-"
    hours_str = f"{attendance.total_hours:.2f}" if attendance.total_hours is not None else "-"
    ua = (request.headers.get("user-agent") or "").strip()
    dev_label = device_label(request)
    dev_fp = compute_device_fingerprint(request)
    _, _, is_mobile = normalize_ua(ua)
    audit_logger.info(
        f"Check-out: {current_user.get('email') or keycloak_user_id} on {today} (in {ci_str} → out {co_str}, total {hours_str}h)",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "attendance_checkout",
                "username": current_user.get("email") or keycloak_user_id,
                "status": "success",
                "client_ip": _get_client_ip(request),
                "attendance_id": str(attendance.id),
                "attendance_date": today.isoformat(),
                "check_in_time": attendance.check_in_time.isoformat() if attendance.check_in_time else None,
                "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
                "work_location_status": attendance.work_location_status,
                "total_hours": float(attendance.total_hours) if attendance.total_hours else None,
                "device": dev_label,
                "device_fingerprint": dev_fp,
                "is_mobile": is_mobile,
                "user_agent": ua[:300] or None,
            },
        },
    )
    return _to_response(db, attendance)


# @router.post("/complete", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
# async def complete_attendance(
#     payload: CompleteAttendanceRequest,
#     db: Session = Depends(get_db),
#     current_user: dict = Depends(get_current_user),
#     _: None = Depends(require_active),
# ):
    keycloak_user_id = current_user["sub"]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    try:
        target_date = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if target_date > today or target_date < today - timedelta(days=1):
        raise HTTPException(status_code=400, detail="Only today or yesterday allowed.")

    if payload.checkInTime >= payload.checkOutTime:
        raise HTTPException(status_code=400, detail="Check-in must be before check-out.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()

    emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.keycloak_user_id == keycloak_user_id
    ).first()
    location_id = emp.location_id if emp else None

    shift_code = None
    shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            or_(
                Shift.date == target_date,
                and_(Shift.end_date.is_not(None), Shift.end_date >= target_date),
            ),
        )
    ).first()
    if shift:
        shift_code = shift.shift_code

    is_wfh = shift and shift.work_location_status == "WFH"
    if is_wfh:
        ws = "WFH"
        loc_name = "Home"
    else:
        ws = determine_work_location(db, payload.latitude, payload.longitude, location_id)
        if ws == "OFFICE" and location_id:
            office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
            loc_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
        else:
            loc_name = location_name(db, payload.latitude, payload.longitude, location_id)

    if existing:
        existing.check_out_time = payload.checkOutTime
        existing.check_out_lat = payload.latitude
        existing.check_out_lng = payload.longitude
        existing.check_out_location_name = loc_name
        existing.is_synced = True
        if not existing.server_received_at:
            existing.server_received_at = datetime.now(timezone.utc)
        existing.remarks = ", ".join(filter(None, [existing.remarks, "Next-day completion"]))
        if payload.remarks:
            existing.remarks += f" ({payload.remarks})"
        db.commit()
        db.refresh(existing)
        if shift:
            shift.status = "Ended"
            db.commit()
        _credit_comp_off_if_holiday(db, existing)
        return _to_response(db, existing)

    attendance = Attendance(
        keycloak_user_id=keycloak_user_id,
        attendance_date=target_date,
        check_in_time=payload.checkInTime,
        check_in_lat=payload.latitude,
        check_in_lng=payload.longitude,
        check_in_location_name=loc_name,
        check_out_time=payload.checkOutTime,
        total_hours=compute_total_hours(payload.checkInTime, payload.checkOutTime),
        check_out_lat=payload.latitude,
        check_out_lng=payload.longitude,
        check_out_location_name=loc_name,
        work_location_status=ws,
        shift_code=shift_code,
        location_id=location_id,
        shift_id=shift.id if shift else None,
        actual_location_id=(
            None if is_wfh
            else find_office_at(db, payload.latitude, payload.longitude)
        ),
        check_in_device=device_label(request),
        check_out_device=device_label(request),
        is_synced=True,
        server_received_at=datetime.now(timezone.utc),
        remarks=f"Next-day completion" + (f" ({payload.remarks})" if payload.remarks else ""),
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    if shift:
        shift.status = "Ended"
        db.commit()

    _credit_comp_off_if_holiday(db, attendance)

    logger.info(f"Complete attendance: {keycloak_user_id} on {target_date}")
    return _to_response(db, attendance)


@router.get("/pending")
def get_pending_attendance(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    yesterday = today - timedelta(days=1)

    unattended = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == yesterday,
            Attendance.check_out_time.is_(None),
        )
    ).order_by(Attendance.attendance_date.asc()).first()
    if unattended:
        result = _to_response(db, unattended)
        result["pendingType"] = "forgot_end"
        result["shiftStartTime"] = None
        result["shiftEndTime"] = None
        if unattended.shift_code:
            shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == unattended.shift_code).first()
            if shift_def:
                tz = safe_zone(shift_def.timezone or "Asia/Kolkata")
                if shift_def.start_time:
                    result["shiftStartTime"] = datetime.combine(unattended.attendance_date, shift_def.start_time, tzinfo=tz).isoformat()
                if shift_def.end_time:
                    result["shiftEndTime"] = shift_end_datetime(unattended.attendance_date, shift_def.start_time, shift_def.end_time, tz).isoformat()
        return result

    past_shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date == yesterday,
        )
    ).first()
    if past_shift:
        past_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == yesterday,
            )
        ).first()
        if not past_att:
            return {
                "pendingType": "forgot_start",
                "attendanceDate": yesterday.isoformat(),
                "shiftCode": past_shift.shift_code,
                "shiftName": None,
                "shiftStartTime": None,
                "shiftEndTime": None,
            }

    raise HTTPException(status_code=404, detail="No pending attendance action")


@router.get("/today", response_model=TodayAttendanceResponse)
def get_today_attendance(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    now = datetime.now(timezone.utc)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    yesterday = today - timedelta(days=1)

    # 1. Open (unchecked-out) session takes priority
    open_att = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.check_out_time.is_(None),
        )
    ).order_by(Attendance.attendance_date.desc()).first()

    if open_att:
        s = "overnight" if open_att.attendance_date < today else "in_progress"
        srec = _find_matching_shift(db, open_att.keycloak_user_id, open_att.shift_code, open_att.check_in_time)
        if _shift_has_ended(db, open_att, shift_date=srec.date if srec else None):
            effective_date = srec.date if srec else open_att.attendance_date
            sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == open_att.shift_code).first()
            if sd and sd.end_time:
                policy = resolve_shift_policy(sd)
                tz = safe_zone(sd.timezone or "Asia/Kolkata")
                shift_end = shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz)
                # Auto-checkout at the shift's own end + its configured grace.
                cutoff_dt = shift_end + timedelta(minutes=policy.auto_checkout_grace_minutes)
                if policy.auto_checkout_enabled and datetime.now(timezone.utc) > cutoff_dt:
                    open_att.check_out_time = cutoff_dt
                    open_att.remarks = ", ".join(filter(None, [open_att.remarks, f"Auto-checkout: shift ended (+{policy.auto_checkout_grace_minutes}m grace)"]))
                    shift_rec = db.query(Shift).filter(
                        Shift.keycloak_user_id == open_att.keycloak_user_id,
                        Shift.date <= effective_date,
                        Shift.end_date >= effective_date,
                    ).first()
                    if shift_rec:
                        shift_rec.status = "Ended"
                    db.commit()
                    s = "completed"
        return _build_status_response(db, open_att, today, s)

    # 1b. Check if user is on leave today (approved or emergency)
    on_leave_today = db.query(Leave).filter(
        Leave.keycloak_user_id == keycloak_user_id,
        Leave.approval_status.in_(["approved", "emergency"]),
        Leave.start_date <= today,
        Leave.end_date >= today,
    ).first()

    # 2. Find shifts covering today (±1 calendar day to handle any timezone).
    #    A night shift at 00:00 IST on "July 22" has UTC start ~July 21 18:30.
    #    Querying only date==today(UTC) would miss it.  Widening by one day on
    #    each side lets _pick_shift (which uses the shift's own timezone from
    #    shift_master.timezone) correctly decide which shift is active right now.
    tomorrow = today + timedelta(days=1)
    shifts_today = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            or_(
                Shift.date.in_([yesterday, today, tomorrow]),
                and_(Shift.end_date.is_not(None), Shift.end_date >= yesterday),
            ),
        )
    ).order_by(Shift.date.asc()).all()

    # 3. Pick active or next-upcoming shift (same logic as check-in)
    picked = _pick_shift(db, shifts_today, today, now)

    if picked:
        shift, sd, tz, start_dt, end_dt = picked
        today = datetime.now(tz).date()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)
        if shift.date > tomorrow:
            return {"status": "no_shift", "shift": None, "attendance": None, "remainingHours": None}
        # Attendance may be stored with a UTC date that differs from shift.date
        # when the shift crosses the UTC midnight boundary (e.g. 00-07 IST on
        # July 22 has check-in at UTC July 21).  Check ±1 day around shift.date.
        date_before = shift.date - timedelta(days=1)
        date_after  = shift.date + timedelta(days=1)
        shift_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date.in_([date_before, shift.date, date_after]),
            )
        ).order_by(Attendance.attendance_date.desc()).first()

        if shift_att:
            if shift_att.attendance_date < today and shift_att.check_out_time:
                shift_att = None
            else:
                s = "completed" if shift_att.check_out_time else "in_progress"
                if s == "in_progress" and end_dt and now > end_dt:
                    s = "shift_ended"
                return _build_status_response(db, shift_att, today, s)

        
        if on_leave_today or shift.work_location_status == "LEAVE":
            return _no_attendance_response("on_leave", shift, sd, tz, today)

        if end_dt and now > end_dt:
            return _no_attendance_response("missed", shift, sd, tz, today)

        return _no_attendance_response("yet_to_start", shift, sd, tz, today)

    # 4. Any attendance record today (checked in without a shift)
    att_today = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == today,
        )
    ).first()

    if att_today:
        s = "completed" if att_today.check_out_time else "in_progress"
        return _build_status_response(db, att_today, today, s)

    # 5. Nothing at all
    if on_leave_today:
        return {"status": "on_leave", "shift": None, "attendance": None, "remainingHours": None}
    return {"status": "no_shift", "shift": None, "attendance": None, "remainingHours": None}


@router.get("/today/shifts", response_model=TodayShiftsResponse)
def get_today_shifts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    now = datetime.now(timezone.utc)
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    today_start = datetime.combine(today, time.min, tzinfo=ist)

    # Shifts scheduled yesterday or today. Yesterday is included so an overnight
    # shift that ends this morning (or is still in progress) still shows.
    candidates = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date.in_([today - timedelta(days=1), today]),
        )
    ).order_by(Shift.date.asc(), Shift.shift_start_time.asc()).all()

    on_leave_today = db.query(Leave).filter(
        Leave.keycloak_user_id == keycloak_user_id,
        Leave.approval_status.in_(["approved", "emergency"]),
        Leave.start_date <= today,
        Leave.end_date >= today,
    ).first()

    # Shift definitions by code
    shift_codes = {s.shift_code for s in candidates if s.shift_code}
    sd_map = {}
    if shift_codes:
        sds = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code.in_(shift_codes)).all()
        sd_map = {sd.shift_code: sd for sd in sds}

    # Per-shift punches by shift_id
    shift_ids = [s.id for s in candidates]
    punch_map = {}
    if shift_ids:
        punches = db.query(AttendancePunch).filter(AttendancePunch.shift_id.in_(shift_ids)).all()
        punch_map = {p.shift_id: p for p in punches}

    entries = []
    for s in candidates:
        sd = sd_map.get(s.shift_code)
        tz = safe_zone(sd.timezone or "Asia/Kolkata") if sd else ist
        start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz) if (sd and sd.start_time) else None
        end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz) if (sd and sd.end_time) else None
        punch = punch_map.get(s.id)

        # Drop shifts that fully ended before today (e.g. yesterday's day shift),
        # unless a punch is still open (overnight in progress).
        if end_dt is not None and end_dt < today_start and not (punch and not punch.check_out_time):
            continue

        # Per-shift status
        if on_leave_today or s.work_location_status == "LEAVE":
            status = "on_leave"
        elif punch and punch.check_out_time:
            status = "completed"
        elif punch and not punch.check_out_time:
            status = "overnight" if punch.attendance_date < today else "in_progress"
            if end_dt and now > end_dt:
                status = "shift_ended"
        elif end_dt and now > end_dt:
            status = "missed"
        else:
            status = "yet_to_start"

        remaining = None
        if punch:
            if punch.check_out_time:
                remaining = 0.0
            elif end_dt:
                remaining = round(max((end_dt - now).total_seconds() / 3600, 0.0), 2)
        elif start_dt and start_dt > now:
            remaining = round((start_dt - now).total_seconds() / 3600, 2)

        entries.append({
            "shiftId": str(s.id),
            "shift_code": s.shift_code,
            "shift_name": sd.shift_name if sd else None,
            "date": s.date.isoformat(),
            "start_time": start_dt.isoformat() if start_dt else (s.shift_start_time.isoformat() if s.shift_start_time else None),
            "end_time": end_dt.isoformat() if end_dt else None,
            "work_location_status": s.work_location_status,
            "project_id": s.project_id,
            "project_name": s.project_name,
            "status": status,
            "attendance": _shift_attendance_to_dict(punch) if punch else None,
            "remainingHours": remaining,
        })

    # Ad-hoc punch today (checked in without a shift)
    adhoc = db.query(AttendancePunch).filter(
        AttendancePunch.keycloak_user_id == keycloak_user_id,
        AttendancePunch.attendance_date == today,
        AttendancePunch.shift_id.is_(None),
        AttendancePunch.is_deleted.is_(False),
    ).first()
    if adhoc:
        entries.append({
            "shiftId": None,
            "shift_code": None,
            "shift_name": None,
            "date": today.isoformat(),
            "start_time": None,
            "end_time": None,
            "work_location_status": adhoc.work_location_status,
            "project_id": None,
            "project_name": None,
            "status": "completed" if adhoc.check_out_time else "in_progress",
            "attendance": _shift_attendance_to_dict(adhoc),
            "remainingHours": 0.0 if adhoc.check_out_time else None,
        })

    # Overall status
    if not entries:
        overall = "on_leave" if on_leave_today else "no_shift"
    elif any(e["status"] in ("in_progress", "overnight", "shift_ended") for e in entries):
        overall = "in_progress"
    elif any(e["status"] == "yet_to_start" for e in entries):
        overall = "yet_to_start"
    elif any(e["status"] == "completed" for e in entries):
        overall = "completed"
    elif any(e["status"] == "on_leave" for e in entries):
        overall = "on_leave"
    else:
        overall = "no_shift"

    return {"status": overall, "date": today.isoformat(), "shifts": entries}


def _shift_attendance_to_dict(p: AttendancePunch) -> dict:
    return {
        "checkInTime": p.check_in_time.isoformat() if p.check_in_time else None,
        "checkInLat": float(p.check_in_lat) if p.check_in_lat else None,
        "checkInLng": float(p.check_in_lng) if p.check_in_lng else None,
        "checkInLocationName": p.check_in_location_name,
        "checkOutTime": p.check_out_time.isoformat() if p.check_out_time else None,
        "checkOutLat": float(p.check_out_lat) if p.check_out_lat else None,
        "checkOutLng": float(p.check_out_lng) if p.check_out_lng else None,
        "checkOutLocationName": p.check_out_location_name,
        "workLocationStatus": p.work_location_status,
        "isLate": p.is_late,
        "totalHours": float(p.total_hours) if p.total_hours else None,
        "shiftCode": p.shift_code,
        "status": p.status,
        "checkInDevice": p.check_in_device,
        "checkOutDevice": p.check_out_device,
        "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("/shifts/check-in", status_code=status.HTTP_201_CREATED)
def shift_check_in(
    request: Request,
    payload: ShiftCheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    ua = (request.headers.get("user-agent") or "").strip()
    dev_label = device_label(request)
    dev_fp = compute_device_fingerprint(request)
    _, _, is_mobile = normalize_ua(ua)
    punch = shift_attendance_service.check_in(
        db,
        current_user["sub"],
        payload.shiftId,
        payload.latitude,
        payload.longitude,
        payload.clientTimestamp,
        check_in_device=dev_label,
    )
    lat_s = f"{float(punch.check_in_lat):.4f}" if punch.check_in_lat is not None else "-"
    lng_s = f"{float(punch.check_in_lng):.4f}" if punch.check_in_lng is not None else "-"
    audit_logger.info(
        f"Shift check-in: {current_user.get('email') or current_user['sub']} shift={payload.shiftId} "
        f"at {punch.check_in_time.strftime('%H:%M:%S') if punch.check_in_time else '-'} (lat={lat_s}, lng={lng_s})",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "shift_checkin",
                "username": current_user.get("email") or current_user["sub"],
                "status": "success",
                "client_ip": _get_client_ip(request),
                "shift_id": payload.shiftId,
                "shift_attendance_id": str(punch.id),
                "check_in_time": punch.check_in_time.isoformat() if punch.check_in_time else None,
                "latitude": float(punch.check_in_lat) if punch.check_in_lat is not None else None,
                "longitude": float(punch.check_in_lng) if punch.check_in_lng is not None else None,
                "work_location_status": punch.work_location_status,
                "is_late": punch.is_late,
                "device": dev_label,
                "device_fingerprint": dev_fp,
                "is_mobile": is_mobile,
                "user_agent": ua[:300] or None,
            },
        },
    )
    return _shift_attendance_to_dict(punch)


@router.post("/shifts/check-out")
def shift_check_out(
    request: Request,
    payload: ShiftCheckOutRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    ua = (request.headers.get("user-agent") or "").strip()
    dev_label = device_label(request)
    dev_fp = compute_device_fingerprint(request)
    _, _, is_mobile = normalize_ua(ua)
    punch = shift_attendance_service.check_out(
        db,
        current_user["sub"],
        payload.shiftId,
        payload.latitude,
        payload.longitude,
        payload.clientTimestamp,
        payload.remarks,
        check_out_device=dev_label,
    )
    lat_s = f"{float(punch.check_out_lat):.4f}" if punch.check_out_lat is not None else "-"
    lng_s = f"{float(punch.check_out_lng):.4f}" if punch.check_out_lng is not None else "-"
    total_s = f"{float(punch.total_hours):.2f}" if punch.total_hours is not None else "-"
    audit_logger.info(
        f"Shift check-out: {current_user.get('email') or current_user['sub']} shift={payload.shiftId} "
        f"(in {punch.check_in_time.strftime('%H:%M:%S') if punch.check_in_time else '-'} "
        f"-> out {punch.check_out_time.strftime('%H:%M:%S') if punch.check_out_time else '-'}, "
        f"total {total_s}h, lat={lat_s}, lng={lng_s})",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": "shift_checkout",
                "username": current_user.get("email") or current_user["sub"],
                "status": "success",
                "client_ip": _get_client_ip(request),
                "shift_id": payload.shiftId,
                "shift_attendance_id": str(punch.id),
                "check_in_time": punch.check_in_time.isoformat() if punch.check_in_time else None,
                "check_out_time": punch.check_out_time.isoformat() if punch.check_out_time else None,
                "work_location_status": punch.work_location_status,
                "total_hours": float(punch.total_hours) if punch.total_hours else None,
                "device": dev_label,
                "device_fingerprint": dev_fp,
                "is_mobile": is_mobile,
                "user_agent": ua[:300] or None,
            },
        },
    )
    return _shift_attendance_to_dict(punch)


def _pick_shift(db: Session, shifts: list, target_date: date, now: datetime) -> Optional[tuple]:
    shift_defs = {
        s.shift_code: db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == s.shift_code).first()
        for s in shifts
    }
    active = None
    next_upcoming = None
    earliest = None
    earliest_dt = None

    for s in shifts:
        sd = shift_defs.get(s.shift_code)
        if not sd:
            continue
        tz = safe_zone(sd.timezone or "Asia/Kolkata")
        # Use the shift's own calendar date (e.g. July 22 IST) so overnight
        # shifts that cross the UTC date boundary produce correct start/end
        # datetimes rather than being pinned to a UTC "today" they don't belong to.
        start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
        end_dt = shift_end_datetime(s.date, sd.start_time, sd.end_time, tz)

        if earliest_dt is None or start_dt < earliest_dt:
            earliest_dt = start_dt
            earliest = (s, sd, tz, start_dt, end_dt)

        if start_dt <= now <= end_dt:
            active = (s, sd, tz, start_dt, end_dt)
            break

        if next_upcoming is None and start_dt > now:
            next_upcoming = (s, sd, tz, start_dt, end_dt)

    return active or next_upcoming or earliest


def _shift_has_ended(db: Session, att: Attendance, shift_date: date = None) -> bool:
    """Check if the shift associated with an open attendance has ended."""
    if not att.shift_code or not att.check_in_time:
        return False
    sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == att.shift_code).first()
    if not sd or not sd.end_time:
        return False
    effective_date = shift_date or att.attendance_date
    end_dt = shift_end_datetime(effective_date, sd.start_time, sd.end_time, safe_zone(sd.timezone or "Asia/Kolkata"))
    return datetime.now(timezone.utc) > end_dt


def _build_status_response(db: Session, att: Attendance, today: date, status: str) -> dict:
    shift_info = None
    shift_start_dt = None
    shift_end_dt = None
    sd = None
    if att.shift_code:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == att.shift_code).first()
        if sd:
            # Use the shared helper to find which shift's window contains the
            # check-in time — handles night shifts crossing UTC midnight correctly.
            srec = _find_matching_shift(
                db, att.keycloak_user_id, att.shift_code, att.check_in_time
            )
            effective_date = srec.date if srec else att.attendance_date

            tz = safe_zone(sd.timezone or "Asia/Kolkata")
            if sd.start_time:
                shift_start_dt = datetime.combine(effective_date, sd.start_time, tzinfo=tz)
            if sd.end_time:
                shift_end_dt = shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz)
            shift_info = {
                "shift_code": att.shift_code,
                "shift_name": sd.shift_name,
                "date": effective_date.isoformat(),
                "work_location_status": att.work_location_status,
                "start_time": shift_start_dt.isoformat() if shift_start_dt else None,
                "end_time": shift_end_dt.isoformat() if shift_end_dt else None,
            }
            if srec:
                shift_info["project_id"] = srec.project_id
                shift_info["project_name"] = srec.project_name

    rem_tz = safe_zone(sd.timezone or "Asia/Kolkata") if sd else ZoneInfo("Asia/Kolkata")
    rem_end = None
    if sd and sd.end_time:
        rem_end = shift_end_datetime(att.attendance_date, sd.start_time, sd.end_time, rem_tz)
    remaining = 0.0
    if att.check_out_time:
        remaining = 0.0
    elif rem_end:
        remaining = round(max((rem_end - datetime.now(timezone.utc)).total_seconds() / 3600, 0), 2)
    else:
        remaining = None

    return {
        "status": status,
        "shift": shift_info,
        "attendance": _to_response(db, att),
        "remainingHours": remaining,
    }


def _no_attendance_response(status: str, shift: Shift, sd: ShiftDefinition, tz: ZoneInfo, target_date: date) -> dict:
    shift_info = None
    if sd:
        # Use the shift's own calendar date so overnight / timezone-crossing
        # shifts show their correct date instead of the UTC "today" that was
        # used to widen the query window.
        effective_date = shift.date
        shift_info = {
            "shift_code": shift.shift_code,
            "shift_name": sd.shift_name,
            "date": effective_date.isoformat(),
            "work_location_status": shift.work_location_status,
            "start_time": datetime.combine(effective_date, sd.start_time, tzinfo=tz).isoformat() if sd.start_time else None,
            "end_time": shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz).isoformat() if sd.end_time else None,
            "project_id": shift.project_id,
            "project_name": shift.project_name,
        }
    return {
        "status": status,
        "shift": shift_info,
        "attendance": None,
        "remainingHours": 0.0 if status == "missed" else None,
    }


@router.get("/project/{project_id}")
def get_project_attendance(
    project_id: int,
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    if "Admin" not in roles:
        email = current_user.get("email")
        rm_user = sql.get_user_by_email(email)
        if not rm_user:
            raise HTTPException(status_code=403, detail="Not found in Redmine.")
        my_projects = sql.get_projects_for_user(rm_user["id"])
        if not any(p.id == project_id for p in my_projects):
            raise HTTPException(status_code=403, detail="You are not a member of this project.")

    members = sql.get_project_members(project_id)
    if not members:
        return {"total": 0, "records": []}

    member_rm_ids = [m["user_id"] for m in members if m.get("user_id")]
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    visible_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
    if not visible_ids:
        return {"total": 0, "records": []}

    query = db.query(Attendance).filter(
        Attendance.keycloak_user_id.in_(visible_ids)
    )
    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format.")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format.")

    records = query.order_by(Attendance.attendance_date.desc()).all()

    result = []
    for a in records:
        shift = db.query(Shift).filter(
            and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
        ).first()
        if not shift or shift.project_id != project_id:
            continue

        rec = _to_response(db, a)
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
        if emp:
            rec["userEmail"] = emp.user_email
            rec["userName"] = f"{emp.first_name} {emp.last_name}".strip()
            rec["userDesignation"] = emp.designation

        rec["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"
        rec["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"
        rec["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

        rec["shiftProject"] = shift.project_name if shift else None

        leave = db.query(Leave).filter(
            and_(
                Leave.keycloak_user_id == a.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= a.attendance_date,
                Leave.end_date >= a.attendance_date,
            )
        ).first()
        rec["onLeave"] = leave is not None
        rec["leaveType"] = leave.leave_type if leave else None
        rec["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
        rec["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
        rec["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
        rec["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

        if a.check_out_time:
            if a.modified_by:
                ended_by_emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
                rec["endedBy"] = f"{ended_by_emp.first_name} {ended_by_emp.last_name}".strip() if ended_by_emp else a.modified_by
                rec["endedByRole"] = ended_by_emp.designation if ended_by_emp else None
            elif emp:
                rec["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
                rec["endedByRole"] = emp.designation
            else:
                rec["endedBy"] = None
                rec["endedByRole"] = None
        else:
            rec["endedBy"] = None
            rec["endedByRole"] = None

        result.append(rec)

    return {
        "total": len(result),
        "records": result,
    }


@router.get("/my-projects")
def get_my_projects_attendance(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    email = current_user.get("email")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    rm_user = sql.get_user_by_email(email)
    if not rm_user:
        raise HTTPException(status_code=403, detail="Not found in Redmine.")

    member_rm_ids = sql.get_team_member_ids(rm_user["id"])
    member_rm_ids.add(rm_user["id"])
    if not member_rm_ids:
        return {"total": 0, "records": []}

    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    visible_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
    if not visible_ids:
        return {"total": 0, "records": []}

    query = db.query(Attendance).filter(Attendance.keycloak_user_id.in_(visible_ids))
    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format.")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format.")

    records = query.order_by(Attendance.attendance_date.desc()).all()

    result = []
    for a in records:
        rec = _to_response(db, a)
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
        if emp:
            rec["userEmail"] = emp.user_email
            rec["userName"] = f"{emp.first_name} {emp.last_name}".strip()
            rec["userDesignation"] = emp.designation

        rec["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"
        rec["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"
        rec["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

        shift = db.query(Shift).filter(
            and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
        ).first()
        rec["shiftProject"] = shift.project_name if shift else None

        leave = db.query(Leave).filter(
            and_(
                Leave.keycloak_user_id == a.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= a.attendance_date,
                Leave.end_date >= a.attendance_date,
            )
        ).first()
        rec["onLeave"] = leave is not None
        rec["leaveType"] = leave.leave_type if leave else None
        rec["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
        rec["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
        rec["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
        rec["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

        if a.check_out_time:
            if a.modified_by:
                ended_by_emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
                rec["endedBy"] = f"{ended_by_emp.first_name} {ended_by_emp.last_name}".strip() if ended_by_emp else a.modified_by
                rec["endedByRole"] = ended_by_emp.designation if ended_by_emp else None
            elif emp:
                rec["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
                rec["endedByRole"] = emp.designation
            else:
                rec["endedBy"] = None
                rec["endedByRole"] = None
        else:
            rec["endedBy"] = None
            rec["endedByRole"] = None

        result.append(rec)

    return {
        "total": len(result),
        "records": result,
    }


def _compute_attendance_flags(attendance: Attendance, shift, sd, policy: ShiftPolicy) -> list:
    flags = []
    ci = attendance.check_in_time
    if not ci:
        return flags

    grace = policy.grace_minutes
    th = attendance.total_hours
    if th is not None and float(th) < policy.half_day_minutes / 60.0:
        flags.append("half_day")

    tz_str = "Asia/Kolkata"
    shift_start = sd.start_time if sd else None
    shift_end = sd.end_time if sd else None
    if sd:
        tz_str = sd.timezone or "Asia/Kolkata"
    if shift and shift.shift_start_time:
        shift_start = shift.shift_start_time
    if shift and shift.shift_end_time:
        shift_end = shift.shift_end_time

    tz = safe_zone(tz_str)
    d = attendance.attendance_date
    if ci.tzinfo is None:
        ci = ci.replace(tzinfo=tz)

    if shift_start:
        start_dt = datetime.combine(d, shift_start, tzinfo=tz)
        if ci < start_dt - timedelta(minutes=grace):
            flags.append("early_check_in")
        elif ci > start_dt + timedelta(minutes=grace):
            flags.append("late_check_in")

    co = attendance.check_out_time
    if shift_end and co:
        if co.tzinfo is None:
            co = co.replace(tzinfo=tz)
        end_dt = datetime.combine(d, shift_end, tzinfo=tz)
        if shift_start and shift_end <= shift_start:
            end_dt += timedelta(days=1)
        if co < end_dt - timedelta(minutes=grace):
            flags.append("early_check_out")
        elif co > end_dt + timedelta(minutes=grace):
            flags.append("late_check_out")

    return flags


ACTIVE_LEAVE_STATUSES = ["approved", "emergency", "cancellation_requested", "cancellation_rejected"]


def _get_attendance_paginated(
    db: Session, keycloak_user_ids: List[str],
    from_date: Optional[str] = None, to_date: Optional[str] = None,
    work_status: Optional[str] = None, project_id: Optional[int] = None,
    per_diem: Optional[bool] = None, conveyance: Optional[bool] = None,
    entry_type: Optional[str] = None, status_filter: Optional[str] = None,
    is_late: Optional[bool] = None,
    search: Optional[str] = None, include_name_search: bool = False,
    attendance_status: Optional[str] = None,
    sort_by: str = "attendanceDate", sort_dir: str = "asc",
    page: int = 1, page_size: int = 50, export: bool = False,
    include_future: bool = False,
) -> dict:
    f = date.fromisoformat(from_date) if from_date else None
    t = date.fromisoformat(to_date) if to_date else None

    # Attendance history = what happened. Future dates are plans (roster shifts,
    # upcoming leave), not attendance, so they are hidden by default. Pass
    # include_future=True (or an explicit future to_date) to opt in. Any future
    # row that is returned is labelled as a plan ("scheduled"/"on_leave"),
    # never "present".
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    include_future = include_future or (t is not None and t > today_ist)

    query = db.query(Attendance).filter(Attendance.keycloak_user_id.in_(keycloak_user_ids))
    if f:
        query = query.filter(Attendance.attendance_date >= f)
    if t:
        query = query.filter(Attendance.attendance_date <= t)
    if not include_future:
        query = query.filter(Attendance.attendance_date <= today_ist)
    if work_status:
        query = query.filter(Attendance.work_location_status == work_status)
    if per_diem is not None:
        query = query.filter(Attendance.per_diem_eligible == per_diem)
    if conveyance is not None:
        query = query.filter(Attendance.conveyance_eligible == conveyance)
    if entry_type:
        query = query.filter(Attendance.is_manual_entry == (entry_type == "manual"))
    if status_filter:
        query = query.filter(Attendance.status == status_filter)
    if is_late is not None:
        query = query.filter(Attendance.is_late == is_late)
    if project_id:
        query = query.join(Shift, and_(
            Shift.keycloak_user_id == Attendance.keycloak_user_id,
            Shift.date == Attendance.attendance_date,
        )).filter(Shift.project_id == project_id)

    records = query.all()

    user_ids = list({r.keycloak_user_id for r in records if r.keycloak_user_id} | set(keycloak_user_ids))
    loc_ids = list({str(r.location_id) for r in records if r.location_id})
    shift_codes = list({r.shift_code for r in records if r.shift_code})
    modified_by_ids = list({r.modified_by for r in records if r.modified_by})

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

    # Fetch shifts for the target users
    shift_query = db.query(Shift).filter(Shift.keycloak_user_id.in_(keycloak_user_ids))
    if f:
        shift_query = shift_query.filter(Shift.date >= f)
    if t:
        shift_query = shift_query.filter(Shift.date <= t)
    if project_id:
        shift_query = shift_query.filter(Shift.project_id == project_id)
    if work_status:
        shift_query = shift_query.filter(Shift.work_location_status == work_status)
    shift_list = shift_query.order_by(Shift.date.desc()).all()
    shift_map = {(s.keycloak_user_id, s.date): s for s in shift_list}

    # Fetch leaves for the target users
    leave_query = db.query(Leave).filter(
        Leave.keycloak_user_id.in_(keycloak_user_ids),
        func.lower(Leave.approval_status).in_(ACTIVE_LEAVE_STATUSES),
    )
    if f:
        leave_query = leave_query.filter(Leave.end_date >= f)
    if t:
        leave_query = leave_query.filter(Leave.start_date <= t)
    leave_list = leave_query.all()

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

    result = []
    for r in records:
        rec = _to_response_with_email_batched(r, emp_map, loc_map, sd_map, shift_map, leave_map, modified_by_map)
        shift = shift_map.get((r.keycloak_user_id, r.attendance_date))
        rec["attendanceFlags"] = _compute_attendance_flags(r, shift, sd_map.get(r.shift_code), resolve_shift_policy(sd_map.get(r.shift_code)))
        result.append(rec)
    covered = {(r.keycloak_user_id, r.attendance_date) for r in records}

    # Synthesize shift records for days not punched and not on leave
    for s in shift_list:
        if (s.keycloak_user_id, s.date) in leave_map:
            continue
        if (s.keycloak_user_id, s.date) in covered:
            continue
        if not include_future and s.date > today_ist:
            continue
        emp = emp_map.get(s.keycloak_user_id)
        vr = _build_virtual_record(db, s, emp)
        vr["attendanceFlags"] = []
        result.append(vr)
        covered.add((s.keycloak_user_id, s.date))

    # Synthesize leave records (when not filtered by work location, project, or manual entry)
    if not work_status and not project_id and not entry_type and (status_filter is None or status_filter in ("on_leave", "present")):
        for l in leave_list:
            if not l.keycloak_user_id:
                continue
            ld = max(l.start_date, f) if f else l.start_date
            rd = min(l.end_date, t) if t else l.end_date
            current = ld
            while current <= rd:
                if not include_future and current > today_ist:
                    break
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
                        "attendanceFlags": [],
                    }
                    result.append(record)
                    covered.add((l.keycloak_user_id, current))
                current += timedelta(days=1)

    if search:
        term = (search or "").strip().lower()[:100]
        if term:
            fields = ["shiftCode", "shiftName", "officeName", "shiftProject", "workLocationStatus", "leaveType"]
            if include_name_search:
                fields = ["userName", "userEmail"] + fields
            result = [r for r in result if any(term in str(r.get(f) or "").lower() for f in fields)]

    if attendance_status:
        wanted = {x.strip().lower().replace(" ", "_") for x in attendance_status.split(",") if x.strip()}
        if wanted:
            def _matches(r):
                tokens = {t.lower().replace(" ", "_") for t in (r.get("attendanceFlags") or [])}
                ds = (r.get("derivedStatus") or "").lower().replace(" ", "_")
                st = (r.get("status") or "").lower().replace(" ", "_")
                if ds:
                    tokens.add(ds)
                if st:
                    tokens.add(st)
                return bool(wanted & tokens)
            result = [r for r in result if _matches(r)]

    allowed_sort = {"attendanceDate", "checkInTime", "checkOutTime", "workStatus", "totalHours", "userName"}
    sort_by = sort_by if sort_by in allowed_sort else "attendanceDate"
    sort_key = "workLocationStatus" if sort_by == "workStatus" else sort_by
    if sort_key == "totalHours":
        result.sort(key=lambda r: r.get("totalHours") if r.get("totalHours") is not None else 0, reverse=(sort_dir == "desc"))
    else:
        result.sort(key=lambda r: r.get(sort_key) or "", reverse=(sort_dir == "desc"))

    total = len(result)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    if export:
        return {"records": result, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}
    offset = (page - 1) * page_size
    records_out = result[offset:offset + page_size]
    return {"records": records_out, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


@router.get("/my-attendance")
def get_my_attendance(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("attendanceDate", description="attendanceDate, checkInTime, checkOutTime, workStatus, totalHours, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    per_diem: Optional[bool] = Query(None, description="Filter per-diem eligible (true/false)"),
    conveyance: Optional[bool] = Query(None, description="Filter conveyance eligible (true/false)"),
    entry_type: Optional[str] = Query(None, description="regular or manual"),
    status: Optional[str] = Query(None, description="Filter by status"),
    is_late: Optional[bool] = Query(None, description="Filter late (true/false)"),
    search: Optional[str] = Query(None, description="Search shift code, project, office"),
    attendance_status: Optional[str] = Query(None, description="Comma-separated: half_day, early_check_in, late_check_in, early_check_out, late_check_out"),
    include_future: bool = Query(False, description="Include future planned shifts/leave (returned as plans, never 'present')"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    result = _get_attendance_paginated(
        db, keycloak_user_ids=[keycloak_user_id],
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        per_diem=per_diem, conveyance=conveyance,
        entry_type=entry_type, status_filter=status, is_late=is_late,
        search=search, include_name_search=False,
        attendance_status=attendance_status,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size, export=export,
        include_future=include_future,
    )
    if export:
        output = build_attendance_excel(result["records"])
        audit_logger.info(
            f"Attendance history exported by {current_user.get('username')}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_my_attendance",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(result["records"]),
                },
            },
        )
        filename = f"my_attendance_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return result


@router.get("/team-attendance")
def get_team_attendance(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("attendanceDate", description="attendanceDate, checkInTime, checkOutTime, workStatus, totalHours, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    per_diem: Optional[bool] = Query(None, description="Filter per-diem eligible (true/false)"),
    conveyance: Optional[bool] = Query(None, description="Filter conveyance eligible (true/false)"),
    entry_type: Optional[str] = Query(None, description="regular or manual"),
    status: Optional[str] = Query(None, description="Filter by status"),
    is_late: Optional[bool] = Query(None, description="Filter late (true/false)"),
    search: Optional[str] = Query(None, description="Search by name, email, shift code, project"),
    attendance_status: Optional[str] = Query(None, description="Comma-separated: half_day, early_check_in, late_check_in, early_check_out, late_check_out"),
    include_future: bool = Query(False, description="Include future planned shifts/leave (returned as plans, never 'present')"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(email)
    if not rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    member_rm_ids = sql.get_team_member_ids(rm_user["id"])
    member_rm_ids.add(rm_user["id"])
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    member_keycloak_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
    if not member_keycloak_ids:
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    result = _get_attendance_paginated(
        db, keycloak_user_ids=member_keycloak_ids,
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        per_diem=per_diem, conveyance=conveyance,
        entry_type=entry_type, status_filter=status, is_late=is_late,
        search=search, include_name_search=True,
        attendance_status=attendance_status,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size, export=export,
        include_future=include_future,
    )
    if export:
        output = build_attendance_excel(result["records"])
        audit_logger.info(
            f"Team attendance history exported by {current_user.get('username')}",
            extra={
                "correlation_id": request.state.correlation_id,
                "extra_data": {
                    "action": "export_team_attendance",
                    "username": current_user.get("username"),
                    "status": "success",
                    "client_ip": _get_client_ip(request),
                    "exported_rows": len(result["records"]),
                },
            },
        )
        filename = f"team_attendance_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return result


@router.get("/my-attendance-v1")
async def get_my_attendance_v1(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("date", description="date, shiftStartTime, shiftCode, workStatus, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    status: Optional[str] = Query(None, description="Comma-separated: completed, in_progress, on_leave, missed, yet_to_start"),
    is_late: Optional[bool] = Query(None, description="Filter late (true/false)"),
    search: Optional[str] = Query(None, description="Search shift code, project, work address"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User email not found.")
    return await shift_service.get_shifts_v1(
        db, emails=[email],
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        status=status, is_late=is_late,
        search=search, include_name_search=False,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )


@router.get("/team-attendance-v1")
async def get_team_attendance_v1(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("date", description="date, shiftStartTime, shiftCode, workStatus, userName"),
    sort_dir: str = Query("asc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    work_status: Optional[str] = Query(None, description="OFFICE, WFH, REMOTE_ONSITE, REMOTE_OFFSITE"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    status: Optional[str] = Query(None, description="Comma-separated: completed, in_progress, on_leave, missed, yet_to_start"),
    is_late: Optional[bool] = Query(None, description="Filter late (true/false)"),
    search: Optional[str] = Query(None, description="Search by name, email, shift code, project"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(email)
    if not rm_user:
        raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
    member_rm_ids = sql.get_team_member_ids(rm_user["id"])
    member_rm_ids.add(rm_user["id"])
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    member_emails = [e.user_email for e in emps if e.user_email]
    if not member_emails:
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    return await shift_service.get_shifts_v1(
        db, emails=member_emails,
        from_date=from_date, to_date=to_date,
        work_status=work_status, project_id=project_id,
        status=status, is_late=is_late,
        search=search, include_name_search=True,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )
