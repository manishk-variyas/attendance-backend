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
    LeaveMemberDetail,
    LeaveSummaryMe,
    MemberAttendanceDetail,
    OutTodayResponse,
    PunctualityMe,
    PunctualityTeam,
    TeamAttendanceResponse,
    TeamLeavesResponse,
    TeamTicketsResponse,
    TeamWfhResponse,
    TicketMemberDetail,
    TicketSummaryMe,
    WfhMemberDetail,
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


def _resolve_member_target(
    svc: AnalyticsService,
    current_user: dict,
    roles: list,
    rm_user: dict,
    redmine_user_id: Optional[int],
    user_email: Optional[str],
    project_id: Optional[int],
) -> tuple[str, dict]:
    """Resolve (keycloak_user_id, user_info) for a per-member analytics target.

    Two server-resolved paths (never trust a client "self" flag):
      - Self: the requested id/email equals the viewer's own Redmine identity.
        Allowed for any member role; keycloak id comes from the session.
      - Lead: Admin/PM/PC requesting a target inside their team scope.
    Raises 400 (no identifier), 403 (non-lead asking for another user),
    404 (out of scope / not linked to an employee login).
    """
    target_email = (user_email or "").strip().lower()
    if redmine_user_id is None and not target_email:
        raise HTTPException(status_code=400, detail="redmine_user_id is required")

    viewer_redmine_id = rm_user["id"]
    viewer_email = (rm_user.get("mail") or "").strip().lower()
    is_self = (
        (redmine_user_id is not None and redmine_user_id == viewer_redmine_id)
        or (redmine_user_id is None and bool(viewer_email) and target_email == viewer_email)
    )
    if is_self:
        kc_id = current_user.get("sub")
        if not kc_id:
            raise HTTPException(status_code=400, detail="User id not found in session")
        return str(kc_id), {
            "redmine_user_id": viewer_redmine_id,
            "name": f"{rm_user.get('firstname', '')} {rm_user.get('lastname', '')}".strip(),
            "email": rm_user.get("mail") or "",
        }

    if not any(r in roles for r in _LEAD_ROLES):
        raise HTTPException(status_code=403, detail="You can only view your own analytics")

    try:
        scope_where, scope_params = svc.team_scope(roles, viewer_redmine_id, project_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if redmine_user_id is not None:
        target = svc.find_team_member(scope_where, scope_params, redmine_user_id=redmine_user_id)
    else:
        target = svc.find_team_member(scope_where, scope_params, user_email=target_email)
    if not target:
        raise HTTPException(status_code=404, detail="Employee not found in your team scope")
    if not target["keycloak_user_id"]:
        raise HTTPException(
            status_code=404,
            detail="No analytics data for this account (not linked to an employee login)",
        )
    return target["keycloak_user_id"], {
        "redmine_user_id": target["redmine_user_id"],
        "name": target["name"],
        "email": target["email"],
    }


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
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
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
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.attendance_team(
        scope_where, scope_params, from_d, to_d,
        sort_by=sort_by, sort_dir=sort_dir, group_by=group_by, hide_empty=hide_empty,
    )


@router.get("/attendance/member", response_model=MemberAttendanceDetail)
@limiter.limit("30/minute")
def member_attendance(
    request: Request,
    redmine_user_id: Optional[int] = Query(None, description="Target employee's Redmine user id (preferred)"),
    user_email: Optional[str] = Query(None, description="Deprecated fallback: target employee's email"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Restrict the target lookup to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One employee's full attendance analytics: summary + punctuality + absenteeism.

    Self (any role) or Admin/PM/PC within team scope. See _resolve_member_target.
    """
    roles = current_user.get("roles", [])
    if not any(r in roles for r in _MEMBER_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    kc_id, user_info = _resolve_member_target(
        svc, current_user, roles, rm_user, redmine_user_id, user_email, project_id
    )
    return {
        "user": user_info,
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
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
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
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.punctuality_team(scope_where, scope_params, from_d, to_d, late_min=late_min)


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
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
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
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.absenteeism_team(scope_where, scope_params, from_d, to_d, streak_min=streak_min)


_TICKET_SCOPES = {"current", "range"}


def _check_ticket_scope(scope: str) -> str:
    scope = (scope or "current").lower()
    if scope not in _TICKET_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be 'current' or 'range'")
    return scope


@router.get("/tickets/me", response_model=TicketSummaryMe)
@limiter.limit("30/minute")
def my_tickets(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    scope: str = Query("current", description="current: all-time header; range: header over tickets created in range"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """My ticket analytics: open/overdue now, created-vs-closed in range, breakdowns, trend."""
    _require_roles(current_user, _MEMBER_ROLES)
    scope = _check_ticket_scope(scope)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    return svc.tickets_me(rm_user["id"], from_d, to_d, scope=scope)


@router.get("/tickets/team", response_model=TeamTicketsResponse)
@limiter.limit("30/minute")
def team_tickets(
    request: Request,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Narrow to one project"),
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
    scope: str = Query("current", description="current: all-time open/overdue; range: over tickets created in range"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Team ticket analytics: per-member workload, per-project split, totals. Admin/PM/PC only."""
    roles = _require_roles(current_user, _LEAD_ROLES)
    scope = _check_ticket_scope(scope)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    try:
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.tickets_team(scope_where, scope_params, from_d, to_d, project_id, scope=scope)


@router.get("/tickets/member", response_model=TicketMemberDetail)
@limiter.limit("30/minute")
def member_tickets(
    request: Request,
    redmine_user_id: Optional[int] = Query(None, description="Target employee's Redmine user id (preferred)"),
    user_email: Optional[str] = Query(None, description="Deprecated fallback: target employee's email"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Restrict the target lookup to one project"),
    scope: str = Query("current", description="current: all-time header; range: header over tickets created in range"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One employee's ticket analytics. Self (any role) or Admin/PM/PC in scope."""
    roles = current_user.get("roles", [])
    if not any(r in roles for r in _MEMBER_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    scope = _check_ticket_scope(scope)
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    _kc_id, user_info = _resolve_member_target(
        svc, current_user, roles, rm_user, redmine_user_id, user_email, project_id
    )
    # tickets_me keys on the Redmine user id.
    return {"user": user_info, "tickets": svc.tickets_me(user_info["redmine_user_id"], from_d, to_d, scope=scope)}


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
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
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
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.leaves_team(scope_where, scope_params, from_d, to_d)


@router.get("/leaves/member", response_model=LeaveMemberDetail)
@limiter.limit("30/minute")
def member_leaves(
    request: Request,
    redmine_user_id: Optional[int] = Query(None, description="Target employee's Redmine user id (preferred)"),
    user_email: Optional[str] = Query(None, description="Deprecated fallback: target employee's email"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Restrict the target lookup to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One employee's leave activity. Self (any role) or Admin/PM/PC in scope."""
    roles = current_user.get("roles", [])
    if not any(r in roles for r in _MEMBER_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    kc_id, user_info = _resolve_member_target(
        svc, current_user, roles, rm_user, redmine_user_id, user_email, project_id
    )
    return {"user": user_info, "leaves": svc.leaves_me(kc_id, from_d, to_d)}


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
    search: Optional[str] = Query(None, description="Filter members by name", max_length=100),
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
        scope_where, scope_params = svc.team_scope(roles, rm_user["id"], project_id, search)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return svc.wfh_team(scope_where, scope_params, from_d, to_d)


@router.get("/wfh/member", response_model=WfhMemberDetail)
@limiter.limit("30/minute")
def member_wfh(
    request: Request,
    redmine_user_id: Optional[int] = Query(None, description="Target employee's Redmine user id (preferred)"),
    user_email: Optional[str] = Query(None, description="Deprecated fallback: target employee's email"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: first day of current month"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD, default: today"),
    project_id: Optional[int] = Query(None, description="Restrict the target lookup to one project"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One employee's WFH activity. Self (any role) or Admin/PM/PC in scope."""
    roles = current_user.get("roles", [])
    if not any(r in roles for r in _MEMBER_ROLES):
        raise HTTPException(status_code=403, detail="Access denied")
    _check_project(db, project_id)
    svc = AnalyticsService(db)
    rm_user = svc.rm.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")
    from_d, to_d = _parse_range(from_date, to_date)
    kc_id, user_info = _resolve_member_target(
        svc, current_user, roles, rm_user, redmine_user_id, user_email, project_id
    )
    return {"user": user_info, "wfh": svc.wfh_me(kc_id, from_d, to_d)}


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
