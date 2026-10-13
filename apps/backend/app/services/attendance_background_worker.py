import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.core.database import SessionLocal
import app.models  # noqa: F401 — register every model so FK targets resolve standalone
from app.models.attendance import Attendance
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.leave import Leave
from app.services.email_service import email_service
from app.utils.shift_policy import resolve_shift_policy
from app.utils.shift_time import safe_zone_plain, shift_end_datetime, compute_total_hours

logger = logging.getLogger("app.sweeper")


def run_attendance_background_sweep():
    """Runs periodic background sweep for auto-checkout and missed check-in email alerts."""
    db: Session = SessionLocal()
    auto_checked_out = 0
    reminders_sent = 0
    try:
        now_utc = datetime.now(timezone.utc)
        today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        logger.info("[Background Sweeper] Sweep started")

        # ── 1. Auto-Checkout Sweeper ───────────────────────────────────────────────
        # Timing policy is per shift (shift_master), not company-wide.
        open_attendances = db.query(Attendance).filter(
            and_(
                Attendance.check_out_time.is_(None),
                Attendance.attendance_date >= today_ist - timedelta(days=3),
            )
        ).all()

        for att in open_attendances:
            try:
                if not att.shift_code:
                    continue

                sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == att.shift_code).first()
                if not sd or not sd.end_time:
                    continue

                policy = resolve_shift_policy(sd)
                if not policy.auto_checkout_enabled:
                    continue

                tz = safe_zone_plain(sd.timezone or "Asia/Kolkata")
                effective_date = att.attendance_date
                shift_end = shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz)

                # Auto-checkout at the shift's own end + its configured grace.
                cutoff_dt = shift_end + timedelta(minutes=policy.auto_checkout_grace_minutes)

                if now_utc > cutoff_dt:
                    att.check_out_time = cutoff_dt
                    att.total_hours = compute_total_hours(att.check_in_time, cutoff_dt)
                    att.remarks = ", ".join(filter(None, [att.remarks, f"Auto-checkout: shift ended (+{policy.auto_checkout_grace_minutes}m grace)"]))
                    
                    shift_rec = db.query(Shift).filter(
                        Shift.keycloak_user_id == att.keycloak_user_id,
                        Shift.date <= effective_date,
                        Shift.end_date >= effective_date,
                    ).first()
                    if shift_rec:
                        shift_rec.status = "Ended"
                    
                    db.commit()
                    auto_checked_out += 1
                    logger.info(f"[Background Sweeper] Auto-checked out attendance {att.id} for user {att.keycloak_user_id}")
            except Exception as e:
                db.rollback()
                logger.error(f"[Background Sweeper] Error auto-checking out attendance {att.id}: {e}")

        # ── 2. Missed Check-In Email Sweeper ────────────────────────────────────────
        recent_shifts = db.query(Shift).filter(
            and_(
                Shift.checkin_reminder_sent.is_(False),
                Shift.date >= today_ist - timedelta(days=1),
                Shift.date <= today_ist + timedelta(days=1),
            )
        ).all()

        for shift in recent_shifts:
            try:
                sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift.shift_code).first()
                if not sd or not sd.start_time:
                    continue

                tz = safe_zone_plain(sd.timezone or "Asia/Kolkata")
                start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz)

                # Check if current time > shift_start + the shift's delay
                policy = resolve_shift_policy(sd)
                if now_utc > start_dt + timedelta(minutes=policy.checkin_reminder_delay_minutes):
                    # Check if user has an attendance record for this shift date
                    att_exists = db.query(Attendance).filter(
                        and_(
                            Attendance.keycloak_user_id == shift.keycloak_user_id,
                            Attendance.attendance_date.in_([
                                shift.date - timedelta(days=1),
                                shift.date,
                                shift.date + timedelta(days=1),
                            ]),
                        )
                    ).first()

                    # Check if user is on leave
                    on_leave = db.query(Leave).filter(
                        Leave.keycloak_user_id == shift.keycloak_user_id,
                        Leave.approval_status.in_(["approved", "emergency"]),
                        Leave.start_date <= shift.date,
                        Leave.end_date >= shift.date,
                    ).first()

                    if not att_exists and not on_leave and shift.work_location_status != "LEAVE":
                        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == shift.keycloak_user_id).first()
                        user_email = emp.user_email if emp else shift.user_email
                        emp_name = f"{emp.first_name} {emp.last_name}".strip() if (emp and emp.first_name) else (user_email or "Employee")

                        if user_email:
                            start_str = sd.start_time.strftime("%H:%M")
                            email_service.send_missed_checkin_reminder(
                                to=user_email,
                                employee_name=emp_name,
                                shift_date=shift.date.isoformat(),
                                shift_start_time=start_str,
                            )
                            reminders_sent += 1
                            logger.info(f"[Background Sweeper] Sent missed check-in email to {user_email} for shift {shift.date}")

                    shift.checkin_reminder_sent = True
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"[Background Sweeper] Error processing shift reminder {shift.id}: {e}")

        logger.info(f"[Background Sweeper] Sweep completed - auto_checked_out={auto_checked_out}, reminders_sent={reminders_sent}")

    except Exception as outer_err:
        logger.error(f"[Background Sweeper] Sweeper loop error: {outer_err}")
    finally:
        db.close()
