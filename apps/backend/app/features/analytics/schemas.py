"""Response schemas for the analytics module (read-only summaries)."""

from typing import Dict, List, Optional
from pydantic import BaseModel


# ── Attendance ─────────────────────────────────────────────────────────────

class DayAttendance(BaseModel):
    date: str
    status: Optional[str] = None
    work_location: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    hours: Optional[float] = None
    is_late: bool = False


class AttendanceSummary(BaseModel):
    from_date: str
    to_date: str
    working_days: int
    recorded_days: int
    present_days: int
    absent_days: int
    leave_days: int
    wfh_days: int
    office_days: int
    late_days: int
    total_hours: float
    avg_hours_per_day: float
    attendance_pct: float
    by_status: Dict[str, int] = {}
    daily: List[DayAttendance] = []


class TeamMemberAttendance(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""
    present_days: int = 0
    absent_days: int = 0
    leave_days: int = 0
    wfh_days: int = 0
    late_days: int = 0
    total_hours: float = 0.0
    attendance_pct: float = 0.0


class TeamDayAttendance(BaseModel):
    date: str
    present: int = 0
    absent: int = 0
    leave: int = 0
    wfh: int = 0


class TeamAttendanceResponse(BaseModel):
    from_date: str
    to_date: str
    working_days: int
    group_by: str = "day"
    member_count: int
    members: List[TeamMemberAttendance] = []
    totals: Dict[str, float] = {}
    daily: List[TeamDayAttendance] = []


# ── Punctuality ──────────────────────────────────────────────────────────

class CheckInPoint(BaseModel):
    date: str
    time: str


class LateTrendPoint(BaseModel):
    date: str
    late: int = 0
    check_in: Optional[str] = None


class PunctualityMe(BaseModel):
    from_date: str
    to_date: str
    late_days: int = 0
    total_checkins: int = 0
    avg_check_in: Optional[str] = None
    earliest_check_in: Optional[CheckInPoint] = None
    latest_check_in: Optional[CheckInPoint] = None
    by_weekday: Dict[str, int] = {}
    trend: List[LateTrendPoint] = []


class PunctualityTeamMember(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""
    late_days: int = 0
    total_checkins: int = 0
    avg_check_in: Optional[str] = None
    habitual: bool = False


class PunctualityTeam(BaseModel):
    from_date: str
    to_date: str
    late_threshold: int = 3
    member_count: int
    habitual_count: int = 0
    ranking: List[PunctualityTeamMember] = []
    by_weekday: Dict[str, int] = {}
    trend: List[LateTrendPoint] = []


# ── Absenteeism ──────────────────────────────────────────────────────────

class AbsenceStreak(BaseModel):
    start: str
    end: str
    length: int


class AbsenteeismMe(BaseModel):
    from_date: str
    to_date: str
    absent_days: int = 0
    longest_streak: int = 0
    streaks: List[AbsenceStreak] = []
    by_weekday: Dict[str, int] = {}
    monday_absences: int = 0
    friday_absences: int = 0


class AbsenteeismTeamMember(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""
    absent_days: int = 0
    longest_streak: int = 0
    monday_absences: int = 0
    friday_absences: int = 0


class AbsenceRedFlag(BaseModel):
    name: str
    email: str = ""
    start: str
    end: str
    length: int


class AbsenteeismTeam(BaseModel):
    from_date: str
    to_date: str
    streak_threshold: int = 3
    member_count: int
    members: List[AbsenteeismTeamMember] = []
    red_flags: List[AbsenceRedFlag] = []
    zero_attendance: List[str] = []
    totals: Dict[str, int] = {}


# ── Per-member drill-down ──────────────────────────────────────────────────

class MemberInfo(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""


class MemberAttendanceDetail(BaseModel):
    """One employee's full attendance picture in a single call."""
    user: MemberInfo
    attendance: AttendanceSummary
    punctuality: PunctualityMe
    absenteeism: AbsenteeismMe


# ── Tickets ────────────────────────────────────────────────────────────────
class TicketBreakdown(BaseModel):
    id: int
    name: str
    count: int


class TicketTrendPoint(BaseModel):
    date: str
    created: int = 0
    closed: int = 0


class TicketSummaryMe(BaseModel):
    from_date: str
    to_date: str
    open_now: int
    overdue_now: int
    created_in_range: int
    closed_in_range: int
    authored_in_range: int
    avg_resolution_days: Optional[float] = None
    by_status: List[TicketBreakdown] = []
    by_priority: List[TicketBreakdown] = []
    by_tracker: List[TicketBreakdown] = []
    trend: List[TicketTrendPoint] = []


class TeamTicketMember(BaseModel):
    redmine_user_id: int
    name: str
    email: str = ""
    open_now: int = 0
    overdue_now: int = 0
    created_in_range: int = 0
    closed_in_range: int = 0


class TeamTicketProject(BaseModel):
    project_id: int
    project_name: str
    total: int = 0
    open: int = 0
    closed: int = 0


class TeamTicketsResponse(BaseModel):
    from_date: str
    to_date: str
    member_count: int
    members: List[TeamTicketMember] = []
    by_project: List[TeamTicketProject] = []
    totals: Dict[str, int] = {}


# ── Leaves (activity, not balances) ────────────────────────────────────────

class LeaveTypeBreakdown(BaseModel):
    type: str
    requests: int = 0
    days: int = 0


class LeaveStatusBreakdown(BaseModel):
    status: str
    count: int = 0


class LeaveTrendPoint(BaseModel):
    date: str
    applied: int = 0
    approved: int = 0


class MonthlyLeavePoint(BaseModel):
    month: str
    applied: int = 0
    approved: int = 0
    approved_days: int = 0


class LeaveSummaryMe(BaseModel):
    from_date: str
    to_date: str
    applied: int = 0
    approved: int = 0
    pending: int = 0
    rejected: int = 0
    cancelled: int = 0
    approved_days: int = 0
    by_type: List[LeaveTypeBreakdown] = []
    by_status: List[LeaveStatusBreakdown] = []
    trend: List[LeaveTrendPoint] = []
    monthly: List[MonthlyLeavePoint] = []


class TeamLeaveMember(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""
    applied: int = 0
    approved: int = 0
    pending: int = 0
    approved_days: int = 0


class TeamLeavesResponse(BaseModel):
    from_date: str
    to_date: str
    member_count: int
    members: List[TeamLeaveMember] = []
    totals: Dict[str, int] = {}
    monthly: List[MonthlyLeavePoint] = []


# ── WFH ────────────────────────────────────────────────────────────────────

class WfhTrendPoint(BaseModel):
    date: str
    applied: int = 0
    approved: int = 0


class MonthlyWfhPoint(BaseModel):
    month: str
    total: int = 0
    approved: int = 0
    approved_days: int = 0


class WfhSummaryMe(BaseModel):
    from_date: str
    to_date: str
    total: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    approved_days: int = 0
    by_status: List[LeaveStatusBreakdown] = []
    trend: List[WfhTrendPoint] = []
    monthly: List[MonthlyWfhPoint] = []


class TeamWfhMember(BaseModel):
    redmine_user_id: Optional[int] = None
    name: str
    email: str = ""
    total: int = 0
    approved: int = 0
    pending: int = 0
    approved_days: int = 0


class TeamWfhResponse(BaseModel):
    from_date: str
    to_date: str
    member_count: int
    members: List[TeamWfhMember] = []
    totals: Dict[str, int] = {}
    monthly: List[MonthlyWfhPoint] = []


# ── Who's out today ────────────────────────────────────────────────────────

class OutTodayLeave(BaseModel):
    name: str
    email: str = ""
    leave_type: Optional[str] = None
    start_date: str
    end_date: str


class OutTodayWfh(BaseModel):
    name: str
    email: str = ""
    start_date: str
    end_date: str


class OutTodayResponse(BaseModel):
    date: str
    on_leave: List[OutTodayLeave] = []
    on_wfh: List[OutTodayWfh] = []
    counts: Dict[str, int] = {}
