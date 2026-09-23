"""Analytics endpoints — read-only dashboard summaries.

- /analytics/attendance/me + /team
- /analytics/tickets/me + /team

Same security and rate-limit conventions as the redmine list endpoints.
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.features.analytics.schemas import (
    AbsenteeismMe,
    AbsenteeismTeam,
    AttendanceSummary,
    LeaveSummaryMe,
    MemberAttendanceDetail,
    OutTodayResponse,
    PunctualityMe,
    PunctualityTeam,
    TeamAttendanceResponse,
    TeamLeavesResponse,
    TeamTicketsResponse,
    TeamWfhResponse,
    TicketSummaryMe,
    WfhSummaryMe,
)
from app.features.analytics.service import MAX_RANGE_DAYS, AnalyticsService
from app.features.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

_MEMBER_ROLES = ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]
_LEAD_ROLES = ["Admin", "Project Manager", "Project Coordinator"]
_SORT_KEYS = {"name", "attendance_pct", "present_days", "absent_days", "late_days", "total_hours"}
_GROUP_KEYS = {"day", "week", "month"}


def _check_project(db: Session, project_id: Optional[int]) -> None:
    if project_id is not None and not AnalyticsService(db).project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _parse_range(
    from_date: Optional[str],
    to_date: Optional[str],
) -> tuple[date, date]:
    today = date.today()
    try:
        from_d = date.fromisoformat(from_date) if from_date else today.replace(day=1)
        to_d = date.fromisoformat(to_date) if to_date else today
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    if from_d > to_d:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date.")
    if (to_d - from_d).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range too large. Maximum {MAX_RANGE_DAYS} days.",
        )
    return from_d, to_d


def _require_roles(current_user: dict, allowed: list) -> list:
    roles = current_user.get("roles", [])
    if not any(r in roles for r in allowed):
        raise HTTPException(status_code=403, detail="Access denied")
    return roles


@router.get("/attendance/me", response_model=AttendanceSummary)
@limiter.limit("30/minute")
def my_attendance(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My attendance summary for a date range (present/absent/leave/WFH, hours, daily rows)."""
    _require_roles(current_user, _MEMBER_ROLES)
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    from_d, to_d = _parse_range(from_date, to_date)
    return AnalyticsService(db).attendance_me(str(sub), from_d, to_d)


@router.get("/attendance/team", response_model=TeamAttendanceResponse)
@limiter.limit("30/minute")
def team_attendance(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    sort_by: str = Query("name", description="name, attendance_pct, present_days, absent_days, late_days, total_hours"),
    sort_dir: str = Query("asc", description="asc or desc"),
    group_by: str = Query("day", description="day, week, or month buckets for the daily grid"),
    hide_empty: bool = Query(False, description="Hide members with no attendance rows"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team attendance: per-member summary + totals + daily grid. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    if sort_by not in _SORT_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by. Use one of: {', '.join(sorted(_SORT_KEYS))}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="sort_dir must be asc or desc")
    if group_by not in _GROUP_KEYS:
        raise HTTPException(status_code=400, detail="group_by must be day, week, or month")
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.attendance_team(
        members, from_d, to_d,
        sort_by=sort_by, sort_dir=sort_dir, group_by=group_by, hide_empty=hide_empty,
    )


@router.get("/attendance/member", response_model=MemberAttendanceDetail)
@limiter.limit("30/minute")
def member_attendance(
    request: Request,
    user_email: str = Query(..., description="Target employee's email"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Restrict the target lookup to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One employee's full attendance analytics: summary + punctuality + absenteeism.

    Admin/PM/PC only. The target must be inside the viewer's scope (same project
    membership rules as /team); others get 404 so cross-team probing leaks nothing.
    """
    roles = _require_roles(current_user, _LEAD_ROLES)
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)

    target_email = (user_email or "").strip().lower()
    if not target_email:
        raise HTTPException(status_code=400, detail="user_email is required")
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    target = next((m for m in members if (m["email"] or "").lower() == target_email), None)
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found in your team scope")
    if not target["keycloak_user_id"]:
        # Test/vendor/service Redmine accounts have no employee login and
        # therefore no attendance rows — nothing to analyse.
        raise HTTPException(
            status_code=404,
            detail="No attendance data for this account (not linked to an employee login)",
        )

    kc_id = target["keycloak_user_id"]
    return {
        "user": {
            "redmine_user_id": target["redmine_user_id"],
            "name": target["name"],
            "email": target["email"],
        },
        "attendance": svc.attendance_me(kc_id, from_d, to_d),
        "punctuality": svc.punctuality_me(kc_id, from_d, to_d),
        "absenteeism": svc.absenteeism_me(kc_id, from_d, to_d),
    }


@router.get("/attendance/punctuality/me", response_model=PunctualityMe)
@limiter.limit("30/minute")
def my_punctuality(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My punctuality: late days, avg check-in (IST), weekday pattern, daily trend."""
    _require_roles(current_user, _MEMBER_ROLES)
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    from_d, to_d = _parse_range(from_date, to_date)
    return AnalyticsService(db).punctuality_me(str(sub), from_d, to_d)


@router.get("/attendance/punctuality/team", response_model=PunctualityTeam)
@limiter.limit("30/minute")
def team_punctuality(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    late_min: int = Query(3, ge=1, le=31, description="Late days marking a habitual latecomer"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team punctuality ranking: habitual latecomers, avg check-ins, weekday pattern. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.punctuality_team(members, from_d, to_d, late_min=late_min)


@router.get("/attendance/absenteeism/me", response_model=AbsenteeismMe)
@limiter.limit("30/minute")
def my_absenteeism(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My absenteeism: absence streaks, weekday pattern, Mon/Fri counts."""
    _require_roles(current_user, _MEMBER_ROLES)
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    from_d, to_d = _parse_range(from_date, to_date)
    return AnalyticsService(db).absenteeism_me(str(sub), from_d, to_d)


@router.get("/attendance/absenteeism/team", response_model=AbsenteeismTeam)
@limiter.limit("30/minute")
def team_absenteeism(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    streak_min: int = Query(3, ge=2, le=31, description="Consecutive absent days marking a red flag"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team absenteeism: streaks, red flags, Mon/Fri pattern, zero-attendance list. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.absenteeism_team(members, from_d, to_d, streak_min=streak_min)


@router.get("/tickets/me", response_model=TicketSummaryMe)
@limiter.limit("30/minute")
def my_tickets(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My ticket analytics: open/overdue now, created-vs-closed in range, breakdowns, trend."""
    _require_roles(current_user, _MEMBER_ROLES)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    return svc.tickets_me(rm_user["id"], from_d, to_d)


@router.get("/tickets/team", response_model=TeamTicketsResponse)
@limiter.limit("30/minute")
def team_tickets(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team ticket analytics: per-member workload, per-project split, totals. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    team_ids = {m["redmine_user_id"] for m in members}
    return svc.tickets_team(team_ids, from_d, to_d, project_id)


@router.get("/leaves/me", response_model=LeaveSummaryMe)
@limiter.limit("30/minute")
def my_leaves(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My leave activity for a date range (counts, approved days, by type, trend). No balances."""
    _require_roles(current_user, _MEMBER_ROLES)
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    from_d, to_d = _parse_range(from_date, to_date)
    return AnalyticsService(db).leaves_me(str(sub), from_d, to_d)


@router.get("/leaves/team", response_model=TeamLeavesResponse)
@limiter.limit("30/minute")
def team_leaves(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team leave activity: per-member applied/approved/pending/days + totals. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.leaves_team(members, from_d, to_d)


@router.get("/wfh/me", response_model=WfhSummaryMe)
@limiter.limit("30/minute")
def my_wfh(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My WFH activity for a date range (counts, approved days, trend)."""
    _require_roles(current_user, _MEMBER_ROLES)
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="User id not found in session")
    from_d, to_d = _parse_range(from_date, to_date)
    return AnalyticsService(db).wfh_me(str(sub), from_d, to_d)


@router.get("/wfh/team", response_model=TeamWfhResponse)
@limiter.limit("30/minute")
def team_wfh(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team WFH activity: per-member totals + team totals. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.wfh_team(members, from_d, to_d)


@router.get("/team/out-today", response_model=OutTodayResponse)
@limiter.limit("30/minute")
def team_out_today(
    request: Request,
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Who's out right now: team members on approved leave / WFH today. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    try:
        members = svc.resolve_team_members(roles, rm_user["id"], project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.out_today(members)
