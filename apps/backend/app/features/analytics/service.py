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

    def _shift_dates(self, keycloak_ids: list, from_d: date, to_d: date) -> dict:
        """{keycloak_id: set(dates)} of scheduled shift days in range.

        A shift row means the user was assigned work that day — including
        weekends and public holidays, so those dates count as *working* days
        for that user (important tasks are scheduled then).
        """
        ids = [k for k in keycloak_ids if k]
        if not ids:
            return {}
        rows = self.db.execute(
            text("""
                SELECT keycloak_user_id, date
                FROM shifts
                WHERE keycloak_user_id = ANY(:kids)
                  AND date BETWEEN :f AND :t
            """),
            {"kids": ids, "f": from_d, "t": to_d},
        ).fetchall()
        out: dict = defaultdict(set)
        for kid, d in rows:
            out[kid].add(d)
        return out

    def _working_dates(self, keycloak_id: Optional[str], from_d: date, to_d: date) -> list:
        """Ordered working dates for one user = the dates they have a shift.

        Working days come from the assigned schedule (the roster), not a
        weekday assumption: a date is a working day only if a shift exists.
        Unknown/no-schedule users have no working days.
        """
        if not keycloak_id:
            return []
        return sorted(self._shift_dates([keycloak_id], from_d, to_d).get(keycloak_id, set()))

    def _working_days_for(self, keycloak_id: Optional[str], from_d: date, to_d: date) -> int:
        """Working-day count for one user (their assigned shift days)."""
        return len(self._working_dates(keycloak_id, from_d, to_d))

    def _work_date_sets_for_kids(self, keycloak_ids: list, from_d: date, to_d: date) -> dict:
        """{keycloak_id: set(shift dates)} for many members (one query, no N+1)."""
        kids = {k for k in keycloak_ids if k}
        if not kids:
            return {}
        return self._shift_dates(list(kids), from_d, to_d)

    def _holiday_set(self, from_d: date, to_d: date, country: Optional[str] = None) -> set:
        """Holiday dates in range, matching the shifts roster calendar.

        Roster rule (``shifts/routes.py``): an employee's country holidays plus
        all national holidays. We mirror that here so attendance/leave day math
        agrees with the roster. ``country`` is the employee's ISO code; when
        omitted, every holiday in range counts (all company data is single
        country, and this equals the roster for any employee in it).
        """
        if country:
            sql = """
                SELECT holiday_date FROM holidays
                WHERE holiday_date BETWEEN :f AND :t
                  AND (country_code = :country OR is_national = true)
            """
            params = {"f": from_d, "t": to_d, "country": country}
        else:
            sql = "SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN :f AND :t"
            params = {"f": from_d, "t": to_d}
        return {r[0] for r in self.db.execute(text(sql), params).fetchall()}

    def holiday_days(self, from_d: date, to_d: date) -> int:
        """Holidays in range (all days, including weekend holidays).

        A distinct calendar figure; working days come from shifts, so a shift
        scheduled on a holiday is both a working day and a holiday.
        """
        return len(self._holiday_set(from_d, to_d))

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

    def team_scope(self, roles: list, rm_user_id: int, project_id: Optional[int],
                     search: Optional[str] = None) -> tuple[str, dict]:
        """Build the (WHERE fragment, params) filtering redmine.users (alias ``u``)
        to the viewer's team scope, with optional name search.

        - Admin: project members when project_id given, else all active employees.
        - PM/PC: members sharing a project with the viewer (+ self), optionally
          narrowed to one project (viewer must belong to it).
        May raise PermissionError. The fragment also references ``e``
        (default email_addresses row) and ``em`` (employee_master) joins, so
        callers must include those joins with the same aliases.
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

        params: dict = {}
        # Only active employees (employee_master) appear in team analytics;
        # inactive/test/service accounts (and rows with no employee_master)
        # are hidden so they don't inflate working/absent counts.
        clauses = ["u.status = 1", "COALESCE(em.is_active, false) = true"]
        if rm_ids is not None:
            if not rm_ids:
                return "FALSE", {}
            clauses.append("u.id = ANY(:ids)")
            params["ids"] = list(rm_ids)
        term = (search or "").strip()[:100]
        if term:
            clauses.append("(u.firstname || ' ' || u.lastname) ILIKE :search")
            params["search"] = f"%{term}%"
        return " AND ".join(clauses), params

    _MEMBER_JOINS = """
        FROM redmine.users u
        JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
        LEFT JOIN employee_master em ON em.redmine_user_id = u.id
    """

    _MEMBER_COLS = """
        u.id AS redmine_user_id,
        u.firstname || ' ' || u.lastname AS name,
        e.address AS email,
        em.keycloak_user_id AS keycloak_user_id
    """

    @staticmethod
    def _member_from_row(r) -> dict:
        return {
            "redmine_user_id": r[0],
            "name": (r[1] or "").strip(),
            "email": r[2] or "",
            "keycloak_user_id": r[3],
        }

    def find_team_member(self, scope_where: str, scope_params: dict,
                         redmine_user_id: Optional[int] = None,
                         user_email: Optional[str] = None) -> Optional[dict]:
        """Single scoped member lookup (LIMIT 1) for drill-down targets."""
        filt, params = dict(scope_params), ""
        if redmine_user_id is not None:
            filt["rid"] = redmine_user_id
            params = "AND u.id = :rid"
        elif (user_email or "").strip():
            filt["email"] = (user_email or "").strip().lower()
            params = "AND LOWER(e.address) = :email"
        else:
            return None
        row = self.db.execute(
            text(f"""
                SELECT {self._MEMBER_COLS} {self._MEMBER_JOINS}
                WHERE {scope_where} {params}
                LIMIT 1
            """),
            filt,
        ).fetchone()
        return self._member_from_row(row) if row else None

    def resolve_team_members(self, roles: list, rm_user_id: int, project_id: Optional[int],
                             search: Optional[str] = None) -> list:
        """Return [{redmine_user_id, keycloak_user_id, name, email}] for the viewer's team.

        - Admin: project members when project_id given, else all active employees.
        - PM/PC: members sharing a project with the viewer (+ self), optionally
          narrowed to one project (viewer must belong to it).
        Optional ``search`` filters by full name at the database level.

        Full-list variant kept for snapshot endpoints (e.g. out-today) that
        genuinely need every member; paged team endpoints must use
        :meth:`page_team_members` instead.
        """
        scope_where, scope_params = self.team_scope(roles, rm_user_id, project_id, search)
        rows = self.db.execute(
            text(f"""
                SELECT {self._MEMBER_COLS} {self._MEMBER_JOINS}
                WHERE {scope_where}
                ORDER BY name ASC, redmine_user_id ASC
            """),
            scope_params,
        ).fetchall()
        return [self._member_from_row(r) for r in rows]

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
        working = self._working_days_for(keycloak_id, from_d, to_d)
        summary = self._summarize_rows(rows, working)
        summary.update({
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "working_days": working,
            "holiday_days": self.holiday_days(from_d, to_d),
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

    # Attendance row predicates shared by the member/totals/daily queries below.
    # They mirror _is_present/_is_leave exactly (NULL-safe: FILTER keeps only TRUE).
    _ATT_PRESENT = "LOWER(a.status::text) = 'present'"
    _ATT_LEAVE = ("NOT (LOWER(a.status::text) = 'present') AND "
                  "(UPPER(a.work_location_status::text) = 'LEAVE' OR "
                  "LOWER(a.status::text) IN ('leave', 'on_leave', 'on leave'))")
    _ATT_WFH = ("LOWER(a.status::text) = 'present' AND "
                "UPPER(a.work_location_status::text) = 'WFH'")

    def _attendance_scope_join(self, hide_empty: bool) -> str:
        """JOIN fragment from the member-identity subquery to attendance rows.

        hide_empty switches to an INNER JOIN so members without rows in range
        disappear from member pages, counts and totals alike.
        """
        join = "JOIN" if hide_empty else "LEFT JOIN"
        return f"""{join} attendance a ON a.keycloak_user_id = m.keycloak_user_id
            AND a.attendance_date BETWEEN :f AND :t
            AND COALESCE(a.is_deleted, false) = false"""

    def attendance_team(
        self,
        scope_where: str,
        scope_params: dict,
        from_d: date,
        to_d: date,
        *,
        sort_by: str = "name",
        sort_dir: str = "asc",
        group_by: str = "day",
        hide_empty: bool = False,
    ) -> dict:
        if sort_by not in self._MEMBER_SORT_KEYS:
            raise ValueError(f"Invalid sort_by. Use one of: {', '.join(sorted(self._MEMBER_SORT_KEYS))}")
        direction = (sort_dir or "asc").lower()
        if direction not in ("asc", "desc"):
            raise ValueError("sort_dir must be 'asc' or 'desc'")
        if group_by == "month":
            trunc: str = "month"
        elif group_by == "week":
            trunc = "week"
        elif group_by == "day":
            trunc = "day"
        else:
            raise ValueError("group_by must be day, week, or month")

        identity = f"""SELECT u.id AS redmine_user_id,
               u.firstname || ' ' || u.lastname AS name,
               e.address AS email,
               em.keycloak_user_id AS keycloak_user_id
            FROM redmine.users u
            JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
            LEFT JOIN employee_master em ON em.redmine_user_id = u.id
            WHERE {scope_where}"""
        # Working days per member = the dates they have a shift (the roster).
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(a.id) FILTER (WHERE {self._ATT_PRESENT}) AS present_days,
               COUNT(a.id) FILTER (WHERE {self._ATT_LEAVE}) AS leave_days,
               COUNT(a.id) FILTER (WHERE a.is_late) AS late_days,
               COALESCE(SUM(a.total_hours), 0) AS total_hours,
               COUNT(a.id) FILTER (WHERE {self._ATT_WFH}) AS wfh_days,
               COALESCE((
                   SELECT COUNT(DISTINCT s.date) FROM shifts s
                   WHERE s.keycloak_user_id = m.keycloak_user_id
                     AND s.date BETWEEN :f AND :t
               ), 0) AS working_days
            FROM ({identity}) m
            {self._attendance_scope_join(hide_empty)}
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        # NOTE: name ordering uses COLLATE "C" (binary) to match the old
        # Python-level sort exactly; the DB default collation sorts
        # case-insensitively first ("ac@..." before "Akansha").
        sort_map = {
            "name": 'name COLLATE "C"',
            "attendance_pct": "COALESCE(present_days * 100.0 / NULLIF(working_days, 0), 0)",
            "present_days": "present_days",
            "absent_days": "(working_days - present_days - leave_days)",
            "late_days": "late_days",
            "total_hours": "total_hours",
        }
        params = {**scope_params, "f": from_d, "t": to_d}
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t "
                 f"ORDER BY {sort_map[sort_by]} {direction.upper()}, name ASC, rm_id ASC"),
            params,
        ).fetchall()
        member_rows = []
        for r in result:
            present, leave, late = (r[4] or 0), (r[5] or 0), (r[6] or 0)
            hours = round(float(r[7] or 0), 2)
            wfh = r[8] or 0
            mworking = r[9] or 0
            absent = max(mworking - present - leave, 0)
            member_rows.append({
                "redmine_user_id": r[0],
                "name": (r[1] or "").strip(),
                "email": r[2] or "",
                "has_login": bool(r[3]),
                "present_days": present,
                "absent_days": absent,
                "leave_days": leave,
                "wfh_days": wfh,
                "late_days": late,
                "total_hours": hours,
                "attendance_pct": round(present / mworking * 100, 1) if mworking else 0.0,
            })

        n = len(member_rows)
        agg = self.db.execute(
            text(f"""SELECT
                    COUNT(a.id) FILTER (WHERE {self._ATT_PRESENT}) AS present,
                    COUNT(a.id) FILTER (WHERE {self._ATT_LEAVE}) AS leave,
                    COUNT(a.id) FILTER (WHERE a.is_late) AS late,
                    COALESCE(SUM(a.total_hours), 0) AS hours,
                    COUNT(a.id) FILTER (WHERE {self._ATT_WFH}) AS wfh
                FROM ({identity}) m
                {self._attendance_scope_join(hide_empty)}"""),
            params,
        ).fetchone()
        present_total, leave_total = (agg[0] or 0), (agg[1] or 0)
        totals = {
            "present_days": present_total,
            "absent_days": sum(m["absent_days"] for m in member_rows),
            "leave_days": leave_total,
            "wfh_days": (agg[4] or 0),
            "late_days": (agg[2] or 0),
            "total_hours": round(float(agg[3] or 0), 2),
        }

        day_rows = self.db.execute(
            text(f"""SELECT date_trunc('{trunc}', a.attendance_date)::date AS b,
                    COUNT(DISTINCT (a.keycloak_user_id, a.attendance_date)) FILTER (WHERE {self._ATT_PRESENT}) AS present,
                    COUNT(DISTINCT (a.keycloak_user_id, a.attendance_date)) FILTER (WHERE {self._ATT_LEAVE}) AS leave,
                    COUNT(DISTINCT (a.keycloak_user_id, a.attendance_date)) FILTER (WHERE {self._ATT_WFH}) AS wfh
                FROM attendance a
                JOIN ({identity}) m ON m.keycloak_user_id = a.keycloak_user_id
                WHERE a.attendance_date BETWEEN :f AND :t
                  AND COALESCE(a.is_deleted, false) = false
                GROUP BY b ORDER BY b"""),
            params,
        ).fetchall()
        per_bucket = {r[0]: {"present": r[1] or 0, "leave": r[2] or 0, "wfh": r[3] or 0} for r in day_rows}
        # Scheduled member-days per bucket = distinct (member, date) shift rows,
        # i.e. how many people were rostered each bucket. Absent = scheduled
        # minus present minus leave.
        shift_rows = self.db.execute(
            text(f"""SELECT date_trunc('{trunc}', s.date)::date AS b,
                    COUNT(DISTINCT (s.keycloak_user_id, s.date)) AS c
                FROM shifts s
                JOIN ({identity}) m ON m.keycloak_user_id = s.keycloak_user_id
                WHERE s.date BETWEEN :f AND :t
                GROUP BY b"""),
            params,
        ).fetchall()
        bucket_workdays = {r[0]: r[1] or 0 for r in shift_rows}
        daily = [
            {
                "date": b.isoformat(),
                "present": per_bucket.get(b, {}).get("present", 0),
                "leave": per_bucket.get(b, {}).get("leave", 0),
                "wfh": per_bucket.get(b, {}).get("wfh", 0),
                "absent": max(
                    bucket_workdays.get(b, 0)
                    - per_bucket.get(b, {}).get("present", 0)
                    - per_bucket.get(b, {}).get("leave", 0), 0),
            }
            for b in sorted(set(list(per_bucket) + list(bucket_workdays)))
            if b in bucket_workdays or b in per_bucket
        ]

        # Team working days = distinct dates the team is rostered (at least one
        # scope member has a shift).
        team_working_days = self.db.execute(
            text(f"""SELECT COUNT(DISTINCT s.date)
                FROM shifts s
                JOIN ({identity}) m ON m.keycloak_user_id = s.keycloak_user_id
                WHERE s.date BETWEEN :f AND :t"""),
            params,
        ).scalar() or 0

        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "working_days": team_working_days,
            "holiday_days": self.holiday_days(from_d, to_d),
            "group_by": group_by,
            "member_count": n,
            "members": member_rows,
            "totals": totals,
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

    def punctuality_team(self, scope_where: str, scope_params: dict,
                           from_d: date, to_d: date, late_min: int = 3) -> dict:
        identity = f"""SELECT u.id AS redmine_user_id,
               u.firstname || ' ' || u.lastname AS name,
               e.address AS email,
               em.keycloak_user_id AS keycloak_user_id
            FROM redmine.users u
            JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
            LEFT JOIN employee_master em ON em.redmine_user_id = u.id
            WHERE {scope_where}"""
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(a.id) FILTER (WHERE a.is_late) AS late_days,
               COUNT(a.check_in_time) AS total_checkins
            FROM ({identity}) m
            LEFT JOIN attendance a ON a.keycloak_user_id = m.keycloak_user_id
                AND a.attendance_date BETWEEN :f AND :t
                AND COALESCE(a.is_deleted, false) = false
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        params = {**scope_params, "f": from_d, "t": to_d}
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t "
                 f"ORDER BY late_days DESC, name ASC, rm_id ASC"),
            params,
        ).fetchall()
        # Avg/earliest/latest need actual timestamps: fetch them for these members only.
        kid_of = {}
        kid_rows = self.db.execute(
            text(f"""SELECT m.redmine_user_id, m.keycloak_user_id
                FROM ({identity}) m WHERE m.redmine_user_id = ANY(:rmids)"""),
            {**params, "rmids": [r[0] for r in result]},
        ).fetchall() if result else []
        kid_of = {r[0]: r[1] for r in kid_rows}
        checkin_rows = []
        member_kids = [k for k in kid_of.values() if k]
        if member_kids:
            checkin_rows = self.db.execute(
                text("""SELECT keycloak_user_id, attendance_date, check_in_time
                    FROM attendance
                    WHERE keycloak_user_id = ANY(:kids)
                      AND attendance_date BETWEEN :f AND :t
                      AND COALESCE(is_deleted, false) = false
                      AND check_in_time IS NOT NULL"""),
                {"kids": member_kids, "f": from_d, "t": to_d},
            ).fetchall()
        by_kid_times: dict = defaultdict(list)
        for kid, day, ci in checkin_rows:
            by_kid_times[kid].append((day, ci))

        ranking = []
        for r in result:
            kid = kid_of.get(r[0])
            times = by_kid_times.get(kid, []) if kid else []
            secs = [self._ist_secs(c) for _, c in times]
            ranking.append({
                "redmine_user_id": r[0],
                "name": (r[1] or "").strip(),
                "email": r[2] or "",
                "has_login": bool(r[3]),
                "late_days": r[4] or 0,
                "total_checkins": r[5] or 0,
                "avg_check_in": self._secs_to_hhmm(sum(secs) / len(secs)) if secs else None,
                "habitual": (r[4] or 0) >= late_min,
            })
            if times:
                ci_sorted = sorted(times, key=lambda x: x[1])
                ranking[-1]["_earliest"] = ci_sorted[0]
                ranking[-1]["_latest"] = ci_sorted[-1]
        # Attach earliest/latest in response shape (kept out of the loop above for clarity).
        for entry in ranking:
            e, l = entry.pop("_earliest", None), entry.pop("_latest", None)
            entry["earliest_check_in"] = (
                {"date": e[0].isoformat(), "time": self._ist_hhmm(e[1])} if e else None
            )
            entry["latest_check_in"] = (
                {"date": l[0].isoformat(), "time": self._ist_hhmm(l[1])} if l else None
            )

        dow_rows = self.db.execute(
            text(f"""SELECT EXTRACT(ISODOW FROM a.attendance_date)::int AS dow, COUNT(*)
                FROM attendance a
                JOIN ({identity}) m ON m.keycloak_user_id = a.keycloak_user_id
                WHERE a.attendance_date BETWEEN :f AND :t
                  AND COALESCE(a.is_deleted, false) = false
                  AND a.is_late
                GROUP BY dow"""),
            params,
        ).fetchall()
        by_weekday = {d: 0 for d in self._WEEKDAY_LABELS}
        for dow, cnt in dow_rows:
            by_weekday[self._WEEKDAY_LABELS[dow - 1]] = cnt

        trend_rows = self.db.execute(
            text(f"""SELECT a.attendance_date AS d,
                    COUNT(*) FILTER (WHERE a.is_late) AS late
                FROM attendance a
                JOIN ({identity}) m ON m.keycloak_user_id = a.keycloak_user_id
                WHERE a.attendance_date BETWEEN :f AND :t
                  AND COALESCE(a.is_deleted, false) = false
                GROUP BY d ORDER BY d"""),
            params,
        ).fetchall()
        trend_by_day = {r[0]: r[1] for r in trend_rows}

        habitual_count = self.db.execute(
            text(f"""SELECT COUNT(*) FROM (
                    SELECT m.redmine_user_id
                    FROM ({identity}) m
                    LEFT JOIN attendance a ON a.keycloak_user_id = m.keycloak_user_id
                        AND a.attendance_date BETWEEN :f AND :t
                        AND COALESCE(a.is_deleted, false) = false
                    GROUP BY m.redmine_user_id
                    HAVING COUNT(a.id) FILTER (WHERE a.is_late) >= :late_min
                ) t"""),
            {**params, "late_min": late_min},
        ).scalar() or 0

        return {
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "late_threshold": late_min,
            "member_count": len(result),
            "habitual_count": habitual_count,
            "ranking": ranking,
            "by_weekday": by_weekday,
            "trend": [
                {"date": d.isoformat(), "late": trend_by_day.get(d, 0)}
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
        work_dates = self._working_dates(keycloak_id, from_d, to_d)
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

    def absenteeism_team(self, scope_where: str, scope_params: dict,
                           from_d: date, to_d: date, streak_min: int = 3) -> dict:
        identity = f"""SELECT u.id AS redmine_user_id,
               u.firstname || ' ' || u.lastname AS name,
               e.address AS email,
               em.keycloak_user_id AS keycloak_user_id
            FROM redmine.users u
            JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
            LEFT JOIN employee_master em ON em.redmine_user_id = u.id
            WHERE {scope_where}"""
        # Working days per member = their assigned shift dates (the roster).
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(a.id) FILTER (WHERE {self._ATT_PRESENT}) AS present_days,
               COUNT(a.id) FILTER (WHERE {self._ATT_LEAVE}) AS leave_days,
               COALESCE((
                   SELECT COUNT(DISTINCT s.date) FROM shifts s
                   WHERE s.keycloak_user_id = m.keycloak_user_id
                     AND s.date BETWEEN :f AND :t
               ), 0) AS working_days
            FROM ({identity}) m
            LEFT JOIN attendance a ON a.keycloak_user_id = m.keycloak_user_id
                AND a.attendance_date BETWEEN :f AND :t
                AND COALESCE(a.is_deleted, false) = false
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        # member_sums reused by the absent_total aggregate below.
        member_sums = f"""SELECT
                COUNT(a.id) FILTER (WHERE {self._ATT_PRESENT}) AS present_days,
                COUNT(a.id) FILTER (WHERE {self._ATT_LEAVE}) AS leave_days,
                m.keycloak_user_id AS keycloak_user_id
            FROM ({identity}) m
            LEFT JOIN attendance a ON a.keycloak_user_id = m.keycloak_user_id
                AND a.attendance_date BETWEEN :f AND :t
                AND COALESCE(a.is_deleted, false) = false
            GROUP BY m.redmine_user_id, m.keycloak_user_id"""
        params = {**scope_params, "f": from_d, "t": to_d}
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t "
                 f"ORDER BY (working_days - present_days - leave_days) DESC, name ASC, rm_id ASC"),
            params,
        ).fetchall()
        kids = []
        kid_of = {}
        if result:
            kid_rows = self.db.execute(
                text(f"""SELECT m.redmine_user_id, m.keycloak_user_id
                    FROM ({identity}) m WHERE m.redmine_user_id = ANY(:rmids)"""),
                {**params, "rmids": [r[0] for r in result]},
            ).fetchall()
            kid_of = {r[0]: r[1] for r in kid_rows}
            kids = [k for k in kid_of.values() if k]
        page_rows = []
        if kids:
            page_rows = self.db.execute(
                text("""SELECT keycloak_user_id, attendance_date, status, work_location_status
                    FROM attendance
                    WHERE keycloak_user_id = ANY(:kids)
                      AND attendance_date BETWEEN :f AND :t
                      AND COALESCE(is_deleted, false) = false"""),
                {"kids": kids, "f": from_d, "t": to_d},
            ).fetchall()
        page_status = self._member_day_status(page_rows)
        # Shift dates for every member in scope (page + red-flags/zero lists).
        all_member_kids = [k for k in kid_of.values() if k]
        if not all_member_kids:
            all_member_kids = [r[0] for r in self.db.execute(
                text(f"""SELECT m.keycloak_user_id FROM ({identity}) m
                    WHERE m.keycloak_user_id IS NOT NULL"""),
                params,
            ).fetchall()]
        shift_map = self._shift_dates(all_member_kids, from_d, to_d)

        def _work_dates_for(kid: Optional[str]) -> list:
            return sorted(shift_map.get(kid, set())) if kid else []

        def _day_map_for(kid: str, wd: list) -> dict:
            status = page_status.get(kid, {})
            return {d: ("present" if status.get(d) == "present"
                        else "leave" if status.get(d) == "leave" else "absent")
                    for d in wd}

        member_rows = []
        for r in result:
            kid = kid_of.get(r[0], "")
            wd = _work_dates_for(kid)
            day_map = _day_map_for(kid, wd)
            absent = sum(1 for d in wd if day_map[d] == "absent")
            streaks = self._streaks(day_map, wd, 1)
            longest = max([s["length"] for s in streaks], default=0)
            by_wd = Counter(d.weekday() for d in wd if day_map[d] == "absent")
            member_rows.append({
                "redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "",
                "has_login": bool(r[3]),
                "absent_days": absent,
                "longest_streak": longest,
                "monday_absences": by_wd.get(0, 0), "friday_absences": by_wd.get(4, 0),
            })

        # Team-wide pattern stats need absence shape per member across the scope.
        # One narrow (kid, date, status, loc) query — compact tuples, no enrichment —
        # then the exact same Python streak logic as before.
        scope_rows = self.db.execute(
            text("""SELECT a.keycloak_user_id, a.attendance_date, a.status, a.work_location_status
                FROM attendance a
                WHERE a.keycloak_user_id IN (
                    SELECT em.keycloak_user_id
                    FROM redmine.users u
                    JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                    LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                    WHERE {scope_where} AND em.keycloak_user_id IS NOT NULL
                )
                AND a.attendance_date BETWEEN :f AND :t
                AND COALESCE(a.is_deleted, false) = false""".replace("{scope_where}", scope_where)),
            params,
        ).fetchall()
        all_status = self._member_day_status(scope_rows)
        # Full identity list (narrow: rm_id, name, email, kid) so zero-attendance
        # and red flags iterate exactly the members the old loop saw — including
        # no-login members, who count as fully absent AND get a full-range
        # streak in red_flags, just like before.
        ident_rows = self.db.execute(            text(f"""SELECT m.redmine_user_id, m.name, m.email, m.keycloak_user_id
                FROM ({identity}) m ORDER BY m.name ASC, m.redmine_user_id ASC"""),
            params,
        ).fetchall()
        red_flags, zero_list = [], []
        for rm_id, name, email, kid in ident_rows:
            status = all_status.get(kid or "", {})
            if not status:
                zero_list.append((name or "").strip())
            wd = _work_dates_for(kid)
            day_map = {d: ("present" if status.get(d) == "present"
                           else "leave" if status.get(d) == "leave" else "absent")
                       for d in wd}
            for s in self._streaks(day_map, wd, streak_min):
                red_flags.append({"name": (name or "").strip(), "email": email or "", **s})
        # Stable sort keeps the name-ASC input order within equal lengths,
        # matching the old loop-over-name-ordered-members behavior.
        red_flags.sort(key=lambda x: x["length"], reverse=True)
        # totals.absent_days sums the per-member absence: each member's working
        # set is their assigned shift dates.
        absent_total = self.db.execute(
            text(f"""SELECT COALESCE(SUM(GREATEST(
                        COALESCE((
                            SELECT COUNT(DISTINCT s.date) FROM shifts s
                            WHERE s.keycloak_user_id = t.keycloak_user_id
                              AND s.date BETWEEN :f AND :t
                        ), 0)
                        - present_days - leave_days, 0)), 0)
                FROM ({member_sums}) t"""),
            params,
        ).fetchone()
        absent_total = absent_total[0] or 0 if absent_total else 0
        n = len(result)
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "streak_threshold": streak_min,
            "member_count": n,
            "members": member_rows,
            "red_flags": red_flags,
            "zero_attendance": sorted(zero_list),
            "totals": {
                "absent_days": absent_total,
                "members_with_streaks": len({f["name"] for f in red_flags}),
            },
        }

    # ── leaves (activity, not balances) ─────────────────────────────────

    @staticmethod
    def _working_overlap_days(start_d: date, end_d: date, from_d: date, to_d: date,
                              work_dates: Optional[set] = None) -> int:
        """Count of a member's working days inside [start_d, end_d] ∩ [from_d, to_d].

        A working day is a day the member has a shift (the roster), so leave
        taken on any scheduled day (incl. a weekend/holiday shift) is counted.
        """
        a, b = max(start_d, from_d), min(end_d, to_d)
        if a > b:
            return 0
        wd = work_dates or set()
        return sum(1 for d in _date_range(a, b) if d in wd)

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
    def _month_starts(span_start: date, to_d: date) -> list:
        """First days of every calendar month overlapping [span_start, to_d].

        For ranges within the trailing 6 months this equals _month_bounds, so
        short-range responses are unchanged; longer ranges (up to the 366-day
        cap) get full monthly coverage instead of a truncated 6-point series.
        """
        out, y, m = [], span_start.year, span_start.month
        while (y, m) <= (to_d.year, to_d.month):
            out.append(date(y, m, 1))
            m += 1
            if m == 13:
                m, y = 1, y + 1
        return out

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

    def _summarize_leaves(self, rows: list, from_d: date, to_d: date,
                          work_dates_by_kid: Optional[dict] = None) -> dict:
        work_dates_by_kid = work_dates_by_kid or {}
        buckets = Counter(self._leave_bucket(r[2]) for r in rows)
        approved_days = sum(
            self._working_overlap_days(r[3], r[4], from_d, to_d, work_dates_by_kid.get(r[0]))
            for r in rows if self._leave_bucket(r[2]) == "approved"
        )
        by_type: dict = defaultdict(lambda: {"requests": 0, "days": 0})
        for r in rows:
            t = by_type[r[1] or "unknown"]
            t["requests"] += 1
            if self._leave_bucket(r[2]) == "approved":
                t["days"] += self._working_overlap_days(r[3], r[4], from_d, to_d, work_dates_by_kid.get(r[0]))
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

    def _monthly_leaves(self, rows: list, span_start: date, to_d: date,
                        work_dates_by_kid: Optional[dict] = None) -> list:
        """Per-month applied/approved/approved-days over the fetched window."""
        work_dates_by_kid = work_dates_by_kid or {}
        points = []
        for m_start in self._month_starts(span_start, to_d):
            m_end = min(self._month_end(m_start), to_d)
            in_month = [r for r in rows if r[3] <= m_end and r[4] >= m_start]
            approved = [r for r in in_month if self._leave_bucket(r[2]) == "approved"]
            points.append({
                "month": m_start.strftime("%Y-%m"),
                "applied": len(in_month),
                "approved": len(approved),
                "approved_days": sum(
                    self._working_overlap_days(r[3], r[4], m_start, m_end, work_dates_by_kid.get(r[0]))
                    for r in approved
                ),
            })
        return points

    def leaves_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        rows = self._leave_rows([keycloak_id], span_start, to_d)
        in_range = [r for r in rows if r[3] <= to_d and r[4] >= from_d]
        work_dates = {keycloak_id: set(self._working_dates(keycloak_id, span_start, to_d))}
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            **self._summarize_leaves(in_range, from_d, to_d, work_dates),
            "monthly": self._monthly_leaves(rows, span_start, to_d, work_dates),
        }

    # Leave buckets mirror _leave_bucket exactly (NULL statuses fall through
    # to pending on both sides: Python's else-branch vs applied-minus-rest).
    _LEAVE_BUCKET_SQL = """CASE
        WHEN LOWER(l.approval_status::text) IN ('approved', 'emergency') THEN 'approved'
        WHEN LOWER(l.approval_status::text) IN ('pending', 'cancellation_requested') THEN 'pending'
        WHEN LOWER(l.approval_status::text) IN ('rejected', 'cancellation_rejected') THEN 'rejected'
        WHEN LOWER(l.approval_status::text) = 'cancelled' THEN 'cancelled'
        ELSE 'pending' END"""

    def leaves_team(self, scope_where: str, scope_params: dict,
                    from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        identity = f"""SELECT u.id AS redmine_user_id,
               u.firstname || ' ' || u.lastname AS name,
               e.address AS email,
               em.keycloak_user_id AS keycloak_user_id
            FROM redmine.users u
            JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
            LEFT JOIN employee_master em ON em.redmine_user_id = u.id
            WHERE {scope_where}"""
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(l.keycloak_user_id) AS applied,
               COUNT(l.keycloak_user_id) FILTER (WHERE {self._LEAVE_BUCKET_SQL} = 'approved') AS approved,
               COUNT(l.keycloak_user_id) FILTER (WHERE {self._LEAVE_BUCKET_SQL} = 'pending') AS pending
            FROM ({identity}) m
            LEFT JOIN leaves l ON l.keycloak_user_id = m.keycloak_user_id
                AND l.start_date <= :t AND l.end_date >= :f
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        params = {**scope_params, "f": from_d, "t": to_d}
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t ORDER BY name ASC, rm_id ASC"),
            params,
        ).fetchall()
        # approved_days needs working-day overlap math: narrow rows for these
        # members, then the same Python helper as before.
        kid_of_member: dict = {}
        if result:
            kid_of_member = {
                r[0]: r[1] for r in self.db.execute(
                    text(f"""SELECT m.redmine_user_id, m.keycloak_user_id
                        FROM ({identity}) m WHERE m.redmine_user_id = ANY(:rmids)"""),
                    {**params, "rmids": [r[0] for r in result]},
                ).fetchall()
            }
        member_kids = [k for k in kid_of_member.values() if k]
        all_rows = []
        if member_kids:
            all_rows = self.db.execute(
                text("""SELECT keycloak_user_id, leave_type, approval_status, start_date, end_date
                    FROM leaves
                    WHERE keycloak_user_id = ANY(:kids)
                      AND start_date <= :t AND end_date >= :f"""),
                {"kids": member_kids, "f": from_d, "t": to_d},
            ).fetchall()
        by_member: dict = defaultdict(list)
        for r in all_rows:
            by_member[r[0]].append(r)
        # Working dates per member (their shift days) so leave on a scheduled
        # day counts toward approved_days.
        work_dates_by_kid = self._work_date_sets_for_kids(member_kids, span_start, to_d)
        member_rows = []
        for r in result:
            kid = kid_of_member.get(r[0]) or ""
            kid_rows = by_member.get(kid, [])
            member_rows.append({
                "redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "",
                "has_login": bool(r[3]),
                "applied": r[4] or 0, "approved": r[5] or 0,
                "pending": r[6] or 0,
                "approved_days": sum(
                    self._working_overlap_days(x[3], x[4], from_d, to_d, work_dates_by_kid.get(kid))
                    for x in kid_rows
                    if self._leave_bucket(x[2]) == "approved"
                ),
            })
        # Team totals: counts from SQL over the full scope; approved_days and
        # monthly from one narrow scope-rows query (calendar arithmetic in Python).
        totals_row = self.db.execute(
            text(f"""SELECT COUNT(l.keycloak_user_id) AS applied,
                    COUNT(l.keycloak_user_id) FILTER (WHERE {self._LEAVE_BUCKET_SQL} = 'approved') AS approved,
                    COUNT(l.keycloak_user_id) FILTER (WHERE {self._LEAVE_BUCKET_SQL} = 'rejected') AS rejected,
                    COUNT(l.keycloak_user_id) FILTER (WHERE {self._LEAVE_BUCKET_SQL} = 'cancelled') AS cancelled
                FROM ({identity}) m
                LEFT JOIN leaves l ON l.keycloak_user_id = m.keycloak_user_id
                    AND l.start_date <= :t AND l.end_date >= :f"""),
            params,
        ).fetchone()
        applied_total = totals_row[0] or 0
        approved_total = totals_row[1] or 0
        scope_rows = self.db.execute(
            text("""SELECT keycloak_user_id, leave_type, approval_status, start_date, end_date
                FROM leaves
                WHERE keycloak_user_id IN (
                    SELECT em.keycloak_user_id
                    FROM redmine.users u
                    JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                    LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                    WHERE __SCOPE__ AND em.keycloak_user_id IS NOT NULL
                )
                AND start_date <= :t2 AND end_date >= :f2""".replace("__SCOPE__", scope_where)),
            {**params, "f2": span_start, "t2": to_d, "f": from_d, "t": to_d},
        ).fetchall()
        in_range = [r for r in scope_rows if r[3] <= to_d and r[4] >= from_d]
        work_dates_by_kid.update(self._work_date_sets_for_kids(
            [r[0] for r in scope_rows], span_start, to_d))
        approved_days_total = sum(
            self._working_overlap_days(r[3], r[4], from_d, to_d, work_dates_by_kid.get(r[0]))
            for r in in_range if self._leave_bucket(r[2]) == "approved"
        )
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "member_count": len(result),
            "members": member_rows,
            "totals": {
                "applied": applied_total,
                "approved": approved_total,
                "pending": applied_total - approved_total - (totals_row[2] or 0) - (totals_row[3] or 0),
                "approved_days": approved_days_total,
            },
            "monthly": self._monthly_leaves(scope_rows, span_start, to_d, work_dates_by_kid),
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

    def _summarize_wfh(self, rows: list, from_d: date, to_d: date,
                       work_dates_by_kid: Optional[dict] = None) -> dict:
        work_dates_by_kid = work_dates_by_kid or {}
        buckets = Counter((r[1] or "").lower() for r in rows)
        approved_days = sum(
            self._working_overlap_days(r[2], r[3], from_d, to_d, work_dates_by_kid.get(r[0]))
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

    def _monthly_wfh(self, rows: list, span_start: date, to_d: date,
                     work_dates_by_kid: Optional[dict] = None) -> list:
        """Per-month total/approved/approved-days over the fetched window."""
        work_dates_by_kid = work_dates_by_kid or {}
        points = []
        for m_start in self._month_starts(span_start, to_d):
            m_end = min(self._month_end(m_start), to_d)
            in_month = [r for r in rows if r[2] <= m_end and r[3] >= m_start]
            approved = [r for r in in_month if (r[1] or "").lower() == "approved"]
            points.append({
                "month": m_start.strftime("%Y-%m"),
                "total": len(in_month),
                "approved": len(approved),
                "approved_days": sum(
                    self._working_overlap_days(r[2], r[3], m_start, m_end, work_dates_by_kid.get(r[0]))
                    for r in approved
                ),
            })
        return points

    def wfh_me(self, keycloak_id: str, from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        rows = self._wfh_rows([keycloak_id], span_start, to_d)
        in_range = [r for r in rows if r[2] <= to_d and r[3] >= from_d]
        work_dates = {keycloak_id: set(self._working_dates(keycloak_id, span_start, to_d))}
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            **self._summarize_wfh(in_range, from_d, to_d, work_dates),
            "monthly": self._monthly_wfh(rows, span_start, to_d, work_dates),
        }

    def wfh_team(self, scope_where: str, scope_params: dict,
                 from_d: date, to_d: date) -> dict:
        span_start = min(from_d, self._month_bounds(to_d)[0])
        identity = f"""SELECT u.id AS redmine_user_id,
               u.firstname || ' ' || u.lastname AS name,
               e.address AS email,
               em.keycloak_user_id AS keycloak_user_id
            FROM redmine.users u
            JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
            LEFT JOIN employee_master em ON em.redmine_user_id = u.id
            WHERE {scope_where}"""
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(w.keycloak_user_id) AS total,
               COUNT(w.keycloak_user_id) FILTER (WHERE LOWER(w.status::text) = 'approved') AS approved,
               COUNT(w.keycloak_user_id) FILTER (WHERE LOWER(w.status::text) = 'pending') AS pending
            FROM ({identity}) m
            LEFT JOIN wfh_requests w ON w.keycloak_user_id = m.keycloak_user_id
                AND w.start_date <= :t AND w.end_date >= :f
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        params = {**scope_params, "f": from_d, "t": to_d}
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t ORDER BY name ASC, rm_id ASC"),
            params,
        ).fetchall()
        kid_of_member: dict = {}
        if result:
            kid_of_member = {
                r[0]: r[1] for r in self.db.execute(
                    text(f"""SELECT m.redmine_user_id, m.keycloak_user_id
                        FROM ({identity}) m WHERE m.redmine_user_id = ANY(:rmids)"""),
                    {**params, "rmids": [r[0] for r in result]},
                ).fetchall()
            }
        member_kids = [k for k in kid_of_member.values() if k]
        all_rows = []
        if member_kids:
            all_rows = self.db.execute(
                text("""SELECT keycloak_user_id, status, start_date, end_date
                    FROM wfh_requests
                    WHERE keycloak_user_id = ANY(:kids)
                      AND start_date <= :t AND end_date >= :f"""),
                {"kids": member_kids, "f": from_d, "t": to_d},
            ).fetchall()
        by_member: dict = defaultdict(list)
        for r in all_rows:
            by_member[r[0]].append(r)
        work_dates_by_kid = self._work_date_sets_for_kids(member_kids, span_start, to_d)
        member_rows = []
        for r in result:
            kid = kid_of_member.get(r[0]) or ""
            kid_rows = by_member.get(kid, [])
            member_rows.append({
                "redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "",
                "has_login": bool(r[3]),
                "total": r[4] or 0, "approved": r[5] or 0,
                "pending": r[6] or 0,
                "approved_days": sum(
                    self._working_overlap_days(x[2], x[3], from_d, to_d, work_dates_by_kid.get(kid))
                    for x in kid_rows
                    if (x[1] or "").lower() == "approved"
                ),
            })
        totals_row = self.db.execute(
            text(f"""SELECT COUNT(w.keycloak_user_id) AS total,
                    COUNT(w.keycloak_user_id) FILTER (WHERE LOWER(w.status::text) = 'approved') AS approved,
                    COUNT(w.keycloak_user_id) FILTER (WHERE LOWER(w.status::text) = 'pending') AS pending,
                    COUNT(w.keycloak_user_id) FILTER (WHERE LOWER(w.status::text) = 'rejected') AS rejected
                FROM ({identity}) m
                LEFT JOIN wfh_requests w ON w.keycloak_user_id = m.keycloak_user_id
                    AND w.start_date <= :t AND w.end_date >= :f"""),
            params,
        ).fetchone()
        scope_rows = self.db.execute(
            text("""SELECT keycloak_user_id, status, start_date, end_date
                FROM wfh_requests
                WHERE keycloak_user_id IN (
                    SELECT em.keycloak_user_id
                    FROM redmine.users u
                    JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                    LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                    WHERE __SCOPE__ AND em.keycloak_user_id IS NOT NULL
                )
                AND start_date <= :t2 AND end_date >= :f2""".replace("__SCOPE__", scope_where)),
            {**params, "f2": span_start, "t2": to_d, "f": from_d, "t": to_d},
        ).fetchall()
        in_range = [r for r in scope_rows if r[2] <= to_d and r[3] >= from_d]
        work_dates_by_kid.update(self._work_date_sets_for_kids(
            [r[0] for r in scope_rows], span_start, to_d))
        approved_days_total = sum(
            self._working_overlap_days(r[2], r[3], from_d, to_d, work_dates_by_kid.get(r[0]))
            for r in in_range if (r[1] or "").lower() == "approved"
        )
        return {
            "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
            "member_count": len(result),
            "members": member_rows,
            "totals": {
                "total": totals_row[0] or 0,
                "approved": totals_row[1] or 0,
                "pending": totals_row[2] or 0,
                "approved_days": approved_days_total,
            },
            "monthly": self._monthly_wfh(scope_rows, span_start, to_d, work_dates_by_kid),
        }

    # ── tickets ─────────────────────────────────────────────────────────

    # "Today" pinned to IST so overdue day-boundaries never depend on the DB
    # session timezone (pgbouncer uses transaction pooling; container TZ is
    # IST today but must not be load-bearing). issues.created_on/closed_on are
    # naive wall timestamps, so only CURRENT_DATE needs the pin.
    _IST_TODAY_SQL = "(now() AT TIME ZONE 'Asia/Kolkata')::date"

    # Status bucket for self analytics: closed comes from Redmine's
    # authoritative is_closed; "Resolved" is the awaiting-close bucket;
    # everything else non-closed is open. Kept here so the SQL and the
    # docs stay in sync.
    _TICKET_OPEN_STATUS = "Resolved"

    # Ticket scope: "current" describes all my tickets as of now; "range"
    # restricts the header universe to tickets created in [from_d, to_d]
    # (current statuses — there is no status-history table). Range-only fields
    # (created/closed/breakdowns/trend) are identical in both scopes.
    _TICKET_SCOPES = {"current", "range"}

    def tickets_me(self, rm_user_id: int, from_d: date, to_d: date,
                   scope: str = "current") -> dict:
        if scope not in self._TICKET_SCOPES:
            raise ValueError("scope must be 'current' or 'range'")
        base = "FROM redmine.issues i JOIN redmine.issue_statuses st ON st.id = i.status_id"
        # scope=range narrows the header universe to the created-in-range
        # cohort; scope=current keeps the all-time universe. Empty fragment
        # keeps the current-scope SQL byte-identical to before.
        cohort_filter = "AND i.created_on::date BETWEEN :f AND :t" if scope == "range" else ""

        # Header counts: assigned to me plus the distinct set I'm involved in
        # (assigned ∪ authored), and their Open/Pending/Closed/Overdue split.
        assigned_total, created_total_now, involved_total = self.db.execute(
            text(f"""
                SELECT COUNT(*) FILTER (WHERE i.assigned_to_id = :uid),
                       COUNT(*) FILTER (WHERE i.author_id = :uid),
                       COUNT(*)
                FROM redmine.issues i
                WHERE (i.assigned_to_id = :uid OR i.author_id = :uid) {cohort_filter}
            """),
            {"uid": rm_user_id, "f": from_d, "t": to_d},
        ).fetchone()

        open_count, pending_count, closed_count, overdue_now = self.db.execute(
            text(f"""
                SELECT COUNT(*) FILTER (WHERE NOT st.is_closed AND st.name <> :pending),
                       COUNT(*) FILTER (WHERE NOT st.is_closed AND st.name = :pending),
                       COUNT(*) FILTER (WHERE st.is_closed),
                       COUNT(*) FILTER (WHERE NOT st.is_closed AND i.due_date < {self._IST_TODAY_SQL})
                {base}
                WHERE i.assigned_to_id = :uid {cohort_filter}
            """),
            {"uid": rm_user_id, "f": from_d, "t": to_d, "pending": self._TICKET_OPEN_STATUS},
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
            # Header counts — current state of my tickets (assigned ∪ authored).
            "total": involved_total or 0,
            "assigned_to_me": assigned_total or 0,
            "created_by_me": created_total_now or 0,
            "open": open_count or 0,
            "pending": pending_count or 0,
            "closed": closed_count or 0,
            "overdue": overdue_now or 0,
            # Range-scoped activity (created/closed within [from_date, to_date]).
            # open_now keeps its original meaning (every non-closed ticket);
            # the open/pending split above is the bucketed view of the same set.
            "open_now": (open_count or 0) + (pending_count or 0),
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

    def tickets_team(self, scope_where: str, scope_params: dict,
                       from_d: date, to_d: date, project_id: Optional[int] = None,
                       scope: str = "current") -> dict:
        if scope not in self._TICKET_SCOPES:
            raise ValueError("scope must be 'current' or 'range'")
        proj_filter = "AND i.project_id = :pid" if project_id is not None else ""
        # scope=range restricts open/overdue (and the member/project sets) to
        # the created-in-range cohort; scope=current keeps all-time state.
        # Empty fragment keeps the current-scope SQL byte-identical to before.
        cohort_filter = "AND i.created_on::date BETWEEN :f AND :t" if scope == "range" else ""
        params = {**scope_params, "f": from_d, "t": to_d}
        if project_id is not None:
            params["pid"] = project_id

        # Member list: assignees with issues in scope (preserves the existing
        # contract that only members holding issues appear here).
        member_inner = f"""SELECT m.redmine_user_id AS rm_id, m.name AS name, m.email AS email,
               (m.keycloak_user_id IS NOT NULL) AS has_login,
               COUNT(i.id) FILTER (WHERE NOT st.is_closed) AS open_now,
                    COUNT(i.id) FILTER (WHERE NOT st.is_closed AND i.due_date < {self._IST_TODAY_SQL}) AS overdue,
               COUNT(i.id) FILTER (WHERE i.created_on::date BETWEEN :f AND :t) AS created_in_range,
               COUNT(i.id) FILTER (WHERE st.is_closed AND i.closed_on::date BETWEEN :f AND :t) AS closed_in_range
            FROM (SELECT u.id AS redmine_user_id,
                         u.firstname || ' ' || u.lastname AS name,
                         e.address AS email,
                         em.keycloak_user_id AS keycloak_user_id
                  FROM redmine.users u
                  JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                  LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                  WHERE {scope_where}) m
            JOIN redmine.issues i ON i.assigned_to_id = m.redmine_user_id {proj_filter} {cohort_filter}
            JOIN redmine.issue_statuses st ON st.id = i.status_id
            GROUP BY m.redmine_user_id, m.name, m.email, m.keycloak_user_id"""
        result = self.db.execute(
            text(f"SELECT * FROM ({member_inner}) t ORDER BY name ASC, rm_id ASC"),
            params,
        ).fetchall()
        members = [
            {
                "redmine_user_id": r[0], "name": (r[1] or "").strip(), "email": r[2] or "",
                "has_login": bool(r[3]),
                "open_now": r[4] or 0, "overdue_now": r[5] or 0,
                "created_in_range": r[6] or 0, "closed_in_range": r[7] or 0,
            }
            for r in result
        ]

        totals = self.db.execute(
            text(f"""SELECT COUNT(i.id) FILTER (WHERE NOT st.is_closed) AS open_now,
                   COUNT(i.id) FILTER (WHERE NOT st.is_closed AND i.due_date < {self._IST_TODAY_SQL}) AS overdue,
                    COUNT(i.id) FILTER (WHERE i.created_on::date BETWEEN :f AND :t) AS created_in_range,
                    COUNT(i.id) FILTER (WHERE st.is_closed AND i.closed_on::date BETWEEN :f AND :t) AS closed_in_range
                FROM redmine.issues i
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                WHERE i.assigned_to_id IN (
                    SELECT u.id
                    FROM redmine.users u
                    JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                    LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                    WHERE {scope_where}
                ) {proj_filter} {cohort_filter}"""),
            params,
        ).fetchone()

        project_rows = self.db.execute(
            text(f"""SELECT i.project_id, pr.name,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE NOT st.is_closed) AS open,
                       COUNT(*) FILTER (WHERE st.is_closed) AS closed
                FROM redmine.issues i
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.projects pr ON pr.id = i.project_id
                WHERE i.assigned_to_id IN (
                    SELECT u.id
                    FROM redmine.users u
                    JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                    LEFT JOIN employee_master em ON em.redmine_user_id = u.id
                    WHERE {scope_where}
                ) {proj_filter} {cohort_filter}
                GROUP BY i.project_id, pr.name
                ORDER BY total DESC, i.project_id ASC"""),
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
                "open_now": totals[0] or 0,
                "overdue_now": totals[1] or 0,
                "created_in_range": totals[2] or 0,
                "closed_in_range": totals[3] or 0,
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
