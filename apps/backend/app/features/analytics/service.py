"""Analytics service — read-only aggregate queries for dashboards.

Conventions (same as the rest of the backend):
- Direct SQL via the request session (public + redmine schemas, no HTTP).
- One query per aggregate — no N+1 loops.
- Date ranges capped at 366 days.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.features.redmine.sql_service import RedmineSQLService

MAX_RANGE_DAYS = 366
_LEAVE_STATUSES = {"leave", "on_leave", "on leave"}


def _date_range(from_d: date, to_d: date):
    d = from_d
    while d <= to_d:
        yield d
        d += timedelta(days=1)


class AnalyticsService:
    def __init__(self, db: Session):
        self.db = db
        self.rm = RedmineSQLService(db)

    # ── shared helpers ──────────────────────────────────────────────────

    def working_days(self, from_d: date, to_d: date) -> int:
        """Weekdays (Mon–Fri) minus national holidays in range."""
        holidays = self._holiday_set(from_d, to_d)
        return sum(1 for d in _date_range(from_d, to_d) if d.weekday() < 5 and d not in holidays)

    def _holiday_set(self, from_d: date, to_d: date) -> set:
        return {
            r[0]
            for r in self.db.execute(
                text("""
                    SELECT holiday_date FROM holidays
                    WHERE holiday_date BETWEEN :f AND :t AND is_national = true
                """),
                {"f": from_d, "t": to_d},
            ).fetchall()
        }

    def _attendance_rows(self, keycloak_ids: list, from_d: date, to_d: date) -> list:
        if not keycloak_ids:
            return []
        return self.db.execute(
            text("""
                SELECT keycloak_user_id, attendance_date, status,
                       work_location_status, check_in_time, check_out_time,
                       total_hours, is_late
                FROM attendance
                WHERE keycloak_user_id = ANY(:kids)
                  AND attendance_date BETWEEN :f AND :t
                  AND COALESCE(is_deleted, false) = false
                ORDER BY attendance_date
            """),
            {"kids": list(keycloak_ids), "f": from_d, "t": to_d},
        ).fetchall()

    @staticmethod
    def _is_present(status: Optional[str]) -> bool:
        return (status or "").lower() == "present"

    @staticmethod
    def _is_leave(status: Optional[str], work_location: Optional[str]) -> bool:
        return (work_location or "").upper() == "LEAVE" or (status or "").lower() in _LEAVE_STATUSES

    def _summarize_rows(self, rows: list, working_days: int) -> dict:
        present = sum(1 for r in rows if self._is_present(r[2]))
        leave = sum(1 for r in rows if not self._is_present(r[2]) and self._is_leave(r[2], r[3]))
        wfh = sum(1 for r in rows if self._is_present(r[2]) and (r[3] or "").upper() == "WFH")
        late = sum(1 for r in rows if r[7])
        hours = round(sum(float(r[6]) for r in rows if r[6] is not None), 2)
        absent = max(working_days - present - leave, 0)
        return {
            "recorded_days": len(rows),
            "present_days": present,
            "absent_days": absent,
            "leave_days": leave,
            "wfh_days": wfh,
            "office_days": present - wfh,
            "late_days": late,
            "total_hours": hours,
            "avg_hours_per_day": round(hours / present, 2) if present else 0.0,
            "attendance_pct": round(present / working_days * 100, 1) if working_days else 0.0,
            "by_status": dict(Counter((r[2] or "unknown") for r in rows)),
        }

    def resolve_team_members(self, roles: list, rm_user_id: int, project_id: Optional[int]) -> list:
        """Return [{redmine_user_id, keycloak_user_id, name, email}] for the viewer's team.

        - Admin: project members when project_id given, else all active employees.
        - PM/PC: members sharing a project with the viewer (+ self), optionally
          narrowed to one project (viewer must belong to it).
        """
        is_admin = "Admin" in roles
        if is_admin:
            rm_ids: Optional[set] = None
            if project_id is not None:
                rm_ids = self._project_member_ids(project_id)
        else:
            team_ids = self.rm.get_team_member_ids(rm_user_id)
            team_ids.add(rm_user_id)
            if project_id is not None:
                if not self.rm.is_project_member(rm_user_id, project_id):
                    raise PermissionError("You are not a member of this project")
                rm_ids = team_ids & self._project_member_ids(project_id)
            else:
                rm_ids = team_ids

        if rm_ids is not None and not rm_ids:
            return []
        rows = self.db.execute(
            text("""
                SELECT u.id, u.firstname || ' ' || u.lastname AS name,
                       e.address AS email, em.keycloak_user_id
                FROM redmine.users u
                JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                WHERE u.status = 1
                  AND (:all_ids OR u.id = ANY(:ids))
                ORDER BY name
            """),
            {"all_ids": rm_ids is None, "ids": list(rm_ids) if rm_ids is not None else []},
        ).fetchall()
        return [
            {"redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "", "keycloak_user_id": r[3]}
            for r in rows
        ]

    def _project_member_ids(self, project_id: int) -> set:
        rows = self.db.execute(
            text("""
                SELECT DISTINCT m.user_id
                FROM redmine.members m
                JOIN redmine.users u ON u.id = m.user_id AND u.status = 1
                WHERE m.project_id = :pid
            """),
            {"pid": project_id},
        ).fetchall()
        return {r[0] for r in rows}

    # ── attendance ──────────────────────────────────────────────────────

    _MEMBER_SORT_KEYS = {"name", "attendance_pct", "present_days", "absent_days", "late_days", "total_hours"}
    _WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    @staticmethod
    def _bucket(d: date, group_by: str) -> date:
        if group_by == "month":
            return d.replace(day=1)
        if group_by == "week":
            return d - timedelta(days=d.weekday())
        return d

    def _member_day_status(self, rows: list) -> dict:
        """{keycloak_id: {date: 'present'|'leave'}} — first row per member/day wins.

        Anything else on a work date (missing row, other statuses) counts as
        absent; callers decide that from the work-date calendar.
        """
        status: dict = defaultdict(dict)
        for r in rows:
            kid, day = r[0], r[1]
            if day in status[kid]:
                continue
            if self._is_present(r[2]):
                status[kid][day] = "present"
            elif self._is_leave(r[2], r[3]):
                status[kid][day] = "leave"
        return status

    def project_exists(self, project_id: int) -> bool:
        return self.db.execute(
            text("SELECT 1 FROM redmine.projects WHERE id = :pid"),
            {"pid": project_id},
        ).fetchone() is not None

    def attendance_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        rows = self._attendance_rows([keycloak_id], from_d, to_d)
        working = self.working_days(from_d, to_d)
        summary = self._summarize_rows(rows, working)
        summary.update({
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "working_days": working,
            "daily": [
                {
                    "date": r[1].isoformat(),
                    "status": r[2],
                    "work_location": r[3],
                    "check_in": r[4].isoformat() if r[4] else None,
                    "check_out": r[5].isoformat() if r[5] else None,
                    "hours": float(r[6]) if r[6] is not None else None,
                    "is_late": bool(r[7]),
                }
                for r in rows
            ],
        })
        return summary

    def attendance_team(
        self,
        members: list,
        from_d: date,
        to_d: date,
        *,
        sort_by: str = "name",
        sort_dir: str = "asc",
        group_by: str = "day",
        hide_empty: bool = False,
    ) -> dict:
        working = self.working_days(from_d, to_d)
        kids = [m["keycloak_user_id"] for m in members if m["keycloak_user_id"]]
        rows = self._attendance_rows(kids, from_d, to_d)

        by_kid: dict = defaultdict(list)
        for r in rows:
            by_kid[r[0]].append(r)

        member_rows = []
        totals = Counter()
        total_hours = 0.0
        for m in members:
            s = self._summarize_rows(by_kid.get(m["keycloak_user_id"] or "", []), working)
            if hide_empty and s["recorded_days"] == 0:
                continue
            total_hours += s["total_hours"]
            for k in ("present_days", "absent_days", "leave_days", "wfh_days", "late_days"):
                totals[k] += s[k]
            member_rows.append({
                "redmine_user_id": m["redmine_user_id"],
                "name": m["name"],
                "email": m["email"],
                "present_days": s["present_days"],
                "absent_days": s["absent_days"],
                "leave_days": s["leave_days"],
                "wfh_days": s["wfh_days"],
                "late_days": s["late_days"],
                "total_hours": s["total_hours"],
                "attendance_pct": s["attendance_pct"],
            })
        reverse = sort_dir == "desc"
        member_rows.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)

        # Team grid grouped by day/week/month (working days carry absentees).
        day_status = self._member_day_status(rows)
        n = len(member_rows)
        kid_of = {m["redmine_user_id"]: m["keycloak_user_id"] or "" for m in members}
        buckets: dict = defaultdict(lambda: {"present": 0, "leave": 0})
        bucket_workdays: dict = defaultdict(int)
        for d in _date_range(from_d, to_d):
            b = self._bucket(d, group_by)
            if d.weekday() < 5:
                bucket_workdays[b] += 1
            for m in member_rows:
                st = day_status.get(kid_of[m["redmine_user_id"]], {}).get(d)
                if st == "present":
                    buckets[b]["present"] += 1
                elif st == "leave":
                    buckets[b]["leave"] += 1
        # wfh per bucket from row-level location info (first WFH row per member-day wins).
        wfh_by_bucket: dict = Counter()
        seen_wfh: dict = defaultdict(set)
        shown_kids = {kid_of[m["redmine_user_id"]] for m in member_rows}
        for r in rows:
            kid, day, status, loc = r[0], r[1], r[2], r[3]
            if kid not in shown_kids:
                continue
            b = self._bucket(day, group_by)
            if self._is_present(status) and (loc or "").upper() == "WFH" and day not in seen_wfh[(kid, b)]:
                seen_wfh[(kid, b)].add(day)
                wfh_by_bucket[b] += 1
        daily = [
            {
                "date": b.isoformat(),
                "present": buckets[b]["present"],
                "leave": buckets[b]["leave"],
                "wfh": wfh_by_bucket.get(b, 0),
                "absent": max(n * bucket_workdays[b] - buckets[b]["present"] - buckets[b]["leave"], 0),
            }
            for b in sorted(set(list(buckets) + list(bucket_workdays)))
            if b in bucket_workdays or b in buckets
        ]

        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "working_days": working,
            "group_by": group_by,
            "member_count": n,
            "members": member_rows,
            "totals": {**{k: int(v) for k, v in totals.items()}, "total_hours": round(total_hours, 2)},
            "daily": daily,
        }

    # ── punctuality ─────────────────────────────────────────────────────

    @staticmethod
    def _ist_hhmm(dt) -> str:
        return dt.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")

    @staticmethod
    def _ist_secs(dt) -> int:
        t = dt.astimezone(ZoneInfo("Asia/Kolkata"))
        return t.hour * 3600 + t.minute * 60 + t.second

    @staticmethod
    def _secs_to_hhmm(s: float) -> str:
        s = int(round(s)) % 86400
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"

    def punctuality_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        rows = self._attendance_rows([keycloak_id], from_d, to_d)
        checkins = [(r[1], r[4]) for r in rows if r[4] is not None]
        late_rows = [r for r in rows if r[7]]
        secs = [self._ist_secs(c) for _, c in checkins]
        by_weekday = Counter(
            self._WEEKDAY_LABELS[r[1].weekday()] for r in late_rows
        )
        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "late_days": len(late_rows),
            "total_checkins": len(checkins),
            "avg_check_in": self._secs_to_hhmm(sum(secs) / len(secs)) if secs else None,
            "earliest_check_in": (
                {"date": min(checkins)[0].isoformat(), "time": self._ist_hhmm(min(checkins, key=lambda x: x[1])[1])}
                if checkins else None
            ),
            "latest_check_in": (
                {"date": max(checkins)[0].isoformat(), "time": self._ist_hhmm(max(checkins, key=lambda x: x[1])[1])}
                if checkins else None
            ),
            "by_weekday": {d: by_weekday.get(d, 0) for d in self._WEEKDAY_LABELS},
            "trend": [
                {
                    "date": d.isoformat(),
                    "late": 1 if any(r[1] == d and r[7] for r in rows) else 0,
                    "check_in": next(
                        (self._ist_hhmm(r[4]) for r in rows if r[1] == d and r[4] is not None),
                        None,
                    ),
                }
                for d in _date_range(from_d, to_d)
            ],
        }

    def punctuality_team(self, members: list, from_d: date, to_d: date, late_min: int = 3) -> dict:
        kids = [m["keycloak_user_id"] for m in members if m["keycloak_user_id"]]
        rows = self._attendance_rows(kids, from_d, to_d)
        by_kid: dict = defaultdict(list)
        for r in rows:
            by_kid[r[0]].append(r)
        ranking, weekday_total = [], Counter()
        for m in members:
            mrows = by_kid.get(m["keycloak_user_id"] or "", [])
            late = [r for r in mrows if r[7]]
            for r in late:
                weekday_total[self._WEEKDAY_LABELS[r[1].weekday()]] += 1
            secs = [self._ist_secs(r[4]) for r in mrows if r[4] is not None]
            ranking.append({
                "redmine_user_id": m["redmine_user_id"],
                "name": m["name"],
                "email": m["email"],
                "late_days": len(late),
                "total_checkins": len(secs),
                "avg_check_in": self._secs_to_hhmm(sum(secs) / len(secs)) if secs else None,
                "habitual": len(late) >= late_min,
            })
        ranking.sort(key=lambda x: x["late_days"], reverse=True)
        late_by_day = Counter(r[1] for r in rows if r[7])
        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "late_threshold": late_min,
            "member_count": len(members),
            "habitual_count": sum(1 for r in ranking if r["habitual"]),
            "ranking": ranking,
            "by_weekday": {d: weekday_total.get(d, 0) for d in self._WEEKDAY_LABELS},
            "trend": [
                {"date": d.isoformat(), "late": late_by_day.get(d, 0)}
                for d in _date_range(from_d, to_d)
            ],
        }

    # ── absenteeism ─────────────────────────────────────────────────────

    def _streaks(self, day_status: dict, work_dates: list, min_len: int) -> list:
        """Consecutive-'absent' runs over the sorted work-date calendar."""
        runs, cur = [], None
        for d in work_dates:
            if day_status.get(d) == "absent":
                cur = {"start": cur["start"], "end": d} if cur else {"start": d, "end": d}
            else:
                if cur:
                    runs.append(cur)
                cur = None
        if cur:
            runs.append(cur)
        out = []
        for r in runs:
            length = sum(1 for d in work_dates if r["start"] <= d <= r["end"])
            if length >= min_len:
                out.append({"start": r["start"].isoformat(), "end": r["end"].isoformat(), "length": length})
        return out

    def absenteeism_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        rows = self._attendance_rows([keycloak_id], from_d, to_d)
        status = self._member_day_status(rows).get(keycloak_id, {})
        work_dates = sorted(d for d in _date_range(from_d, to_d) if d.weekday() < 5)
        day_map = {d: ("present" if status.get(d) == "present" else "leave" if status.get(d) == "leave" else "absent") for d in work_dates}
        streaks = self._streaks(day_map, work_dates, 2)
        by_weekday = Counter(d.weekday() for d in work_dates if day_map[d] == "absent")
        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "absent_days": sum(1 for d in work_dates if day_map[d] == "absent"),
            "longest_streak": max([s["length"] for s in streaks], default=0),
            "streaks": streaks,
            "by_weekday": {self._WEEKDAY_LABELS[i]: by_weekday.get(i, 0) for i in range(7)},
            "monday_absences": by_weekday.get(0, 0),
            "friday_absences": by_weekday.get(4, 0),
        }

    def absenteeism_team(self, members: list, from_d: date, to_d: date, streak_min: int = 3) -> dict:
        kids = [m["keycloak_user_id"] for m in members if m["keycloak_user_id"]]
        rows = self._attendance_rows(kids, from_d, to_d)
        all_status = self._member_day_status(rows)
        work_dates = sorted(d for d in _date_range(from_d, to_d) if d.weekday() < 5)
        member_rows, red_flags, zero_list = [], [], []
        for m in members:
            kid = m["keycloak_user_id"] or ""
            status = all_status.get(kid, {})
            day_map = {d: ("present" if status.get(d) == "present" else "leave" if status.get(d) == "leave" else "absent") for d in work_dates}
            absent = sum(1 for d in work_dates if day_map[d] == "absent")
            streaks = self._streaks(day_map, work_dates, streak_min)
            longest = max([s["length"] for s in self._streaks(day_map, work_dates, 1)], default=0)
            by_wd = Counter(d.weekday() for d in work_dates if day_map[d] == "absent")
            if not status:
                zero_list.append(m["name"])
            for s in streaks:
                red_flags.append({"name": m["name"], "email": m["email"], **s})
            member_rows.append({
                "redmine_user_id": m["redmine_user_id"], "name": m["name"], "email": m["email"],
                "absent_days": absent, "longest_streak": longest,
                "monday_absences": by_wd.get(0, 0), "friday_absences": by_wd.get(4, 0),
            })
        red_flags.sort(key=lambda x: x["length"], reverse=True)
        member_rows.sort(key=lambda x: x["absent_days"], reverse=True)
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "streak_threshold": streak_min,
            "member_count": len(members),
            "members": member_rows,
            "red_flags": red_flags,
            "zero_attendance": sorted(zero_list),
            "totals": {
                "absent_days": sum(m["absent_days"] for m in member_rows),
                "members_with_streaks": len({f["name"] for f in red_flags}),
            },
        }

    # ── leaves (activity, not balances) ─────────────────────────────────

    @staticmethod
    def _working_overlap_days(start_d: date, end_d: date, from_d: date, to_d: date, holidays: set) -> int:
        """Working-day overlap between [start_d, end_d] and [from_d, to_d]."""
        a, b = max(start_d, from_d), min(end_d, to_d)
        if a > b:
            return 0
        return sum(1 for d in _date_range(a, b) if d.weekday() < 5 and d not in holidays)

    @staticmethod
    def _month_bounds(to_d: date) -> list:
        """First days of the last 6 calendar months ending with to_d's month."""
        months, y, m = [], to_d.year, to_d.month
        for _ in range(6):
            months.append(date(y, m, 1))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        return sorted(months)

    @staticmethod
    def _month_end(first_d: date) -> date:
        return (first_d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    @staticmethod
    def _leave_bucket(status: str) -> str:
        s = (status or "").lower()
        if s in ("approved", "emergency"):
            return "approved"
        if s in ("pending", "cancellation_requested"):
            return "pending"
        if s in ("rejected", "cancellation_rejected"):
            return "rejected"
        if s == "cancelled":
            return "cancelled"
        return "pending"

    def _leave_rows(self, keycloak_ids: list, from_d: date, to_d: date) -> list:
        """Leave requests overlapping the range (the time off falls in period)."""
        if not keycloak_ids:
            return []
        return self.db.execute(
            text("""
                SELECT keycloak_user_id, leave_type, approval_status,
                       start_date, end_date
                FROM leaves
                WHERE keycloak_user_id = ANY(:kids)
                  AND start_date <= :t AND end_date >= :f
            """),
            {"kids": list(keycloak_ids), "f": from_d, "t": to_d},
        ).fetchall()

    def _summarize_leaves(self, rows: list, from_d: date, to_d: date, holidays: set) -> dict:
        buckets = Counter(self._leave_bucket(r[2]) for r in rows)
        approved_days = sum(
            self._working_overlap_days(r[3], r[4], from_d, to_d, holidays)
            for r in rows if self._leave_bucket(r[2]) == "approved"
        )
        by_type: dict = defaultdict(lambda: {"requests": 0, "days": 0})
        for r in rows:
            t = by_type[r[1] or "unknown"]
            t["requests"] += 1
            if self._leave_bucket(r[2]) == "approved":
                t["days"] += self._working_overlap_days(r[3], r[4], from_d, to_d, holidays)
        applied_by_day: dict = Counter(r[3] for r in rows)
        approved_by_day: dict = Counter(
            r[3] for r in rows if self._leave_bucket(r[2]) == "approved"
        )
        return {
            "applied": len(rows),
            "approved": buckets.get("approved", 0),
            "pending": buckets.get("pending", 0),
            "rejected": buckets.get("rejected", 0),
            "cancelled": buckets.get("cancelled", 0),
            "approved_days": approved_days,
            "by_type": [
                {"type": k, "requests": v["requests"], "days": v["days"]}
                for k, v in sorted(by_type.items())
            ],
            "by_status": [
                {"status": k, "count": v}
                for k, v in Counter((r[2] or "unknown") for r in rows).most_common()
            ],
            "trend": [
                {
                    "date": d.isoformat(),
                    "applied": applied_by_day.get(d, 0),
                    "approved": approved_by_day.get(d, 0),
                }
                for d in _date_range(from_d, to_d)
            ],
        }

    def _monthly_leaves(self, rows: list, to_d: date, holidays: set) -> list:
        """Per-month applied/approved/approved-days for the last 6 months."""
        points = []
        for m_start in self._month_bounds(to_d):
            m_end = min(self._month_end(m_start), to_d)
            in_month = [r for r in rows if r[3] <= m_end and r[4] >= m_start]
            approved = [r for r in in_month if self._leave_bucket(r[2]) == "approved"]
            points.append({
                "month": m_start.strftime("%Y-%m"),
                "applied": len(in_month),
                "approved": len(approved),
                "approved_days": sum(
                    self._working_overlap_days(r[3], r[4], m_start, m_end, holidays)
                    for r in approved
                ),
            })
        return points

    def leaves_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        holidays = self._holiday_set(span_start, to_d)
        rows = self._leave_rows([keycloak_id], span_start, to_d)
        in_range = [r for r in rows if r[3] <= to_d and r[4] >= from_d]
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            **self._summarize_leaves(in_range, from_d, to_d, holidays),
            "monthly": self._monthly_leaves(rows, to_d, holidays),
        }

    def leaves_team(self, members: list, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        holidays = self._holiday_set(span_start, to_d)
        kids = [m["keycloak_user_id"] for m in members if m["keycloak_user_id"]]
        rows = self._leave_rows(kids, span_start, to_d)
        by_kid: dict = defaultdict(list)
        for r in rows:
            if r[3] <= to_d and r[4] >= from_d:
                by_kid[r[0]].append(r)
        member_rows, totals = [], Counter()
        for m in members:
            s = self._summarize_leaves(by_kid.get(m["keycloak_user_id"] or "", []), from_d, to_d, holidays)
            for k in ("applied", "approved", "pending", "approved_days"):
                totals[k] += s[k]
            member_rows.append({
                "redmine_user_id": m["redmine_user_id"], "name": m["name"], "email": m["email"],
                "applied": s["applied"], "approved": s["approved"],
                "pending": s["pending"], "approved_days": s["approved_days"],
            })
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "member_count": len(members), "members": member_rows,
            "totals": {k: int(v) for k, v in totals.items()},
            "monthly": self._monthly_leaves(rows, to_d, holidays),
        }

    # ── wfh ─────────────────────────────────────────────────────────────

    def _wfh_rows(self, keycloak_ids: list, from_d: date, to_d: date) -> list:
        if not keycloak_ids:
            return []
        return self.db.execute(
            text("""
                SELECT keycloak_user_id, status, start_date, end_date
                FROM wfh_requests
                WHERE keycloak_user_id = ANY(:kids)
                  AND start_date <= :t AND end_date >= :f
            """),
            {"kids": list(keycloak_ids), "f": from_d, "t": to_d},
        ).fetchall()

    def _summarize_wfh(self, rows: list, from_d: date, to_d: date, holidays: set) -> dict:
        buckets = Counter((r[1] or "").lower() for r in rows)
        approved_days = sum(
            self._working_overlap_days(r[2], r[3], from_d, to_d, holidays)
            for r in rows if (r[1] or "").lower() == "approved"
        )
        applied_by_day: dict = Counter(r[2] for r in rows)
        approved_by_day: dict = Counter(
            r[2] for r in rows if (r[1] or "").lower() == "approved"
        )
        return {
            "total": len(rows),
            "pending": buckets.get("pending", 0),
            "approved": buckets.get("approved", 0),
            "rejected": buckets.get("rejected", 0),
            "approved_days": approved_days,
            "by_status": [
                {"status": k, "count": v} for k, v in buckets.most_common()
            ],
            "trend": [
                {
                    "date": d.isoformat(),
                    "applied": applied_by_day.get(d, 0),
                    "approved": approved_by_day.get(d, 0),
                }
                for d in _date_range(from_d, to_d)
            ],
        }

    def _monthly_wfh(self, rows: list, to_d: date, holidays: set) -> list:
        """Per-month total/approved/approved-days for the last 6 months."""
        points = []
        for m_start in self._month_bounds(to_d):
            m_end = min(self._month_end(m_start), to_d)
            in_month = [r for r in rows if r[2] <= m_end and r[3] >= m_start]
            approved = [r for r in in_month if (r[1] or "").lower() == "approved"]
            points.append({
                "month": m_start.strftime("%Y-%m"),
                "total": len(in_month),
                "approved": len(approved),
                "approved_days": sum(
                    self._working_overlap_days(r[2], r[3], m_start, m_end, holidays)
                    for r in approved
                ),
            })
        return points

    def wfh_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        holidays = self._holiday_set(span_start, to_d)
        rows = self._wfh_rows([keycloak_id], span_start, to_d)
        in_range = [r for r in rows if r[2] <= to_d and r[3] >= from_d]
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            **self._summarize_wfh(in_range, from_d, to_d, holidays),
            "monthly": self._monthly_wfh(rows, to_d, holidays),
        }

    def wfh_team(self, members: list, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        holidays = self._holiday_set(span_start, to_d)
        kids = [m["keycloak_user_id"] for m in members if m["keycloak_user_id"]]
        rows = self._wfh_rows(kids, span_start, to_d)
        by_kid: dict = defaultdict(list)
        for r in rows:
            if r[2] <= to_d and r[3] >= from_d:
                by_kid[r[0]].append(r)
        member_rows, totals = [], Counter()
        for m in members:
            s = self._summarize_wfh(by_kid.get(m["keycloak_user_id"] or "", []), from_d, to_d, holidays)
            for k in ("total", "approved", "pending", "approved_days"):
                totals[k] += s[k]
            member_rows.append({
                "redmine_user_id": m["redmine_user_id"], "name": m["name"], "email": m["email"],
                "total": s["total"], "approved": s["approved"],
                "pending": s["pending"], "approved_days": s["approved_days"],
            })
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "member_count": len(members), "members": member_rows,
            "totals": {k: int(v) for k, v in totals.items()},
            "monthly": self._monthly_wfh(rows, to_d, holidays),
        }

    # ── tickets ─────────────────────────────────────────────────────────

    def tickets_me(self, rm_user_id: int, from_d: date, to_d: date) -> dict:
        base = "FROM redmine.issues i JOIN redmine.issue_statuses st ON st.id = i.status_id"
        open_now, overdue_now = self.db.execute(
            text(f"""
                SELECT COUNT(*) FILTER (WHERE NOT st.is_closed),
                       COUNT(*) FILTER (WHERE NOT st.is_closed AND i.due_date < CURRENT_DATE)
                {base} WHERE i.assigned_to_id = :uid
            """),
            {"uid": rm_user_id},
        ).fetchone()

        cohort = "i.assigned_to_id = :uid AND i.created_on::date BETWEEN :f AND :t"
        created_total, closed_total, avg_days = self.db.execute(
            text(f"""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE st.is_closed),
                       AVG(EXTRACT(EPOCH FROM (i.closed_on - i.created_on)) / 86400.0)
                         FILTER (WHERE st.is_closed AND i.closed_on IS NOT NULL)
                {base} WHERE {cohort}
            """),
            {"uid": rm_user_id, "f": from_d, "t": to_d},
        ).fetchone()

        def _breakdown(col: str, join: str, name_col: str) -> list:
            rows = self.db.execute(
                text(f"""
                    SELECT {col} AS bid, {name_col} AS bname, COUNT(*) AS cnt
                    {base} {join}
                    WHERE {cohort}
                    GROUP BY {col}, {name_col}
                    ORDER BY cnt DESC
                """),
                {"uid": rm_user_id, "f": from_d, "t": to_d},
            ).fetchall()
            return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]

        by_status = _breakdown("st.id", "", "st.name")
        by_priority = _breakdown(
            "i.priority_id",
            "JOIN redmine.enumerations p ON p.id = i.priority_id",
            "p.name",
        )
        by_tracker = _breakdown(
            "i.tracker_id",
            "JOIN redmine.trackers t ON t.id = i.tracker_id",
            "t.name",
        )

        authored = self.db.execute(
            text("""
                SELECT COUNT(*) FROM redmine.issues
                WHERE author_id = :uid AND created_on::date BETWEEN :f AND :t
            """),
            {"uid": rm_user_id, "f": from_d, "t": to_d},
        ).scalar() or 0

        created_by_day = {
            r[0]: r[1] for r in self.db.execute(
                text(f"""
                    SELECT i.created_on::date AS d, COUNT(*)
                    {base} WHERE {cohort}
                    GROUP BY d
                """),
                {"uid": rm_user_id, "f": from_d, "t": to_d},
            ).fetchall()
        }
        closed_by_day = {
            r[0]: r[1] for r in self.db.execute(
                text(f"""
                    SELECT i.closed_on::date AS d, COUNT(*)
                    {base}
                    WHERE i.assigned_to_id = :uid AND i.closed_on::date BETWEEN :f AND :t
                    GROUP BY d
                """),
                {"uid": rm_user_id, "f": from_d, "t": to_d},
            ).fetchall()
        }
        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "open_now": open_now or 0,
            "overdue_now": overdue_now or 0,
            "created_in_range": created_total or 0,
            "closed_in_range": closed_total or 0,
            "authored_in_range": authored,
            "avg_resolution_days": round(float(avg_days), 1) if avg_days is not None else None,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_tracker": by_tracker,
            "trend": [
                {
                    "date": d.isoformat(),
                    "created": created_by_day.get(d, 0),
                    "closed": closed_by_day.get(d, 0),
                }
                for d in _date_range(from_d, to_d)
            ],
        }

    def tickets_team(self, team_ids: set, from_d: date, to_d: date, project_id: Optional[int]) -> dict:
        ids = list(team_ids)
        if not ids:
            return {
                "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
                "member_count": 0, "members": [], "by_project": [],
                "totals": {"open_now": 0, "overdue_now": 0, "created_in_range": 0, "closed_in_range": 0},
            }
        proj_filter = "AND i.project_id = :pid" if project_id is not None else ""
        params = {"ids": ids, "f": from_d, "t": to_d}
        if project_id is not None:
            params["pid"] = project_id

        member_rows = self.db.execute(
            text(f"""
                SELECT i.assigned_to_id,
                       u.firstname || ' ' || u.lastname AS name,
                       e.address AS email,
                       COUNT(*) FILTER (WHERE NOT st.is_closed) AS open_now,
                       COUNT(*) FILTER (WHERE NOT st.is_closed AND i.due_date < CURRENT_DATE) AS overdue,
                       COUNT(*) FILTER (WHERE i.created_on::date BETWEEN :f AND :t) AS created_in_range,
                       COUNT(*) FILTER (WHERE st.is_closed AND i.closed_on::date BETWEEN :f AND :t) AS closed_in_range
                FROM redmine.issues i
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.users u ON u.id = i.assigned_to_id
                JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                WHERE i.assigned_to_id = ANY(:ids) {proj_filter}
                GROUP BY i.assigned_to_id, u.firstname, u.lastname, e.address
                ORDER BY name
            """),
            params,
        ).fetchall()
        members = [
            {
                "redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "",
                "open_now": r[3] or 0, "overdue_now": r[4] or 0,
                "created_in_range": r[5] or 0, "closed_in_range": r[6] or 0,
            }
            for r in member_rows
        ]

        project_rows = self.db.execute(
            text(f"""
                SELECT i.project_id, pr.name,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE NOT st.is_closed) AS open,
                       COUNT(*) FILTER (WHERE st.is_closed) AS closed
                FROM redmine.issues i
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.projects pr ON pr.id = i.project_id
                WHERE i.assigned_to_id = ANY(:ids) {proj_filter}
                GROUP BY i.project_id, pr.name
                ORDER BY total DESC
            """),
            params,
        ).fetchall()
        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "member_count": len(members),
            "members": members,
            "by_project": [
                {"project_id": r[0], "project_name": r[1], "total": r[2], "open": r[3] or 0, "closed": r[4] or 0}
                for r in project_rows
            ],
            "totals": {
                "open_now": sum(m["open_now"] for m in members),
                "overdue_now": sum(m["overdue_now"] for m in members),
                "created_in_range": sum(m["created_in_range"] for m in members),
                "closed_in_range": sum(m["closed_in_range"] for m in members),
            },
        }

    # ── who's out today ───────────────────────────────────────────────

    def out_today(self, members: list) -> dict:
        """Team members on approved leave / WFH right now (today)."""
        today = date.today()
        by_kid = {m["keycloak_user_id"]: m for m in members if m["keycloak_user_id"]}
        on_leave, on_wfh = [], []
        for r in self._leave_rows(list(by_kid), today, today):
            if self._leave_bucket(r[2]) != "approved":
                continue
            m = by_kid[r[0]]
            on_leave.append({
                "name": m["name"], "email": m["email"],
                "leave_type": r[1], "start_date": r[3].isoformat(), "end_date": r[4].isoformat(),
            })
        for r in self._wfh_rows(list(by_kid), today, today):
            if (r[1] or "").lower() != "approved":
                continue
            m = by_kid[r[0]]
            on_wfh.append({
                "name": m["name"], "email": m["email"],
                "start_date": r[2].isoformat(), "end_date": r[3].isoformat(),
            })
        on_leave.sort(key=lambda x: x["name"])
        on_wfh.sort(key=lambda x: x["name"])
        return {
            "date": today.isoformat(),
            "on_leave": on_leave,
            "on_wfh": on_wfh,
            "counts": {"on_leave": len(on_leave), "on_wfh": len(on_wfh)},
        }
