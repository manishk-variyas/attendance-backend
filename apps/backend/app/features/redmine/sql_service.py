"""Direct SQL access to Redmine tables — no HTTP, no cache, real-time."""

from sqlalchemy.orm import Session
from sqlalchemy import text


def _issue_row_to_dict(r) -> dict:
    return dict(
        id=r[0], subject=r[1], description=r[2], status=r[3],
        priority=r[4], tracker=r[5], project_id=r[6], project_name=r[7],
        assigned_to_name=r[8],
        created_on=r[9].isoformat() if r[9] else None,
        updated_on=r[10].isoformat() if r[10] else None,
        start_date=r[11].isoformat() if r[11] else None,
        due_date=r[12].isoformat() if r[12] else None,
        estimated_hours=float(r[13]) if r[13] is not None else None,
        done_ratio=r[14],
        is_private=r[15],
        status_id=r[16],
        priority_id=r[17],
        tracker_id=r[18],
        assigned_to_id=r[19],
        author_id=r[20],
        author_name=r[21] or "",
        attachments=r[22] if r[22] else [],
        custom_fields=r[23] if r[23] else [],
    )


class RedmineSQLService:
    def __init__(self, db: Session):
        self.db = db

    def get_user_by_email(self, email: str):
        row = self.db.execute(
            text("""
                SELECT u.id, u.login, u.firstname, u.lastname, u.status, e.address as email
                FROM redmine.users u
                JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                WHERE e.address = :email
            """),
            {"email": email},
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "login": row[1],
            "firstname": row[2],
            "lastname": row[3],
            "status": row[4],
            "mail": row[5],
        }

    def get_projects_for_user(self, user_id: int) -> list:
        rows = self.db.execute(
            text("""
                SELECT p.id, p.name, p.identifier, p.status
                FROM redmine.members m
                JOIN redmine.projects p ON p.id = m.project_id
                WHERE m.user_id = :user_id AND p.status = 1
            """),
            {"user_id": user_id},
        ).fetchall()
        return [
            type("Project", (), {"id": r[0], "name": r[1], "identifier": r[2], "status": r[3]})
            for r in rows
        ]

    def get_projects_for_user_rich(self, user_id: int) -> list:
        """Get projects with custom fields (city, office, type) for direct
        ProjectResponse construction. Returns list of dicts."""
        rows = self.db.execute(
            text("""
                SELECT p.id, p.name, p.identifier, p.status,
                       cf.name as cf_name, cv.value as cf_value
                FROM redmine.members m
                JOIN redmine.projects p ON p.id = m.project_id AND p.status = 1
                LEFT JOIN redmine.custom_values cv ON cv.customized_id = p.id
                    AND cv.customized_type = 'Project'
                LEFT JOIN redmine.custom_fields cf ON cf.id = cv.custom_field_id
                WHERE m.user_id = :user_id
            """),
            {"user_id": user_id},
        ).fetchall()

        _STATUS_MAP = {1: "active", 5: "closed"}

        projects = {}
        for r in rows:
            pid = r[0]
            if pid not in projects:
                projects[pid] = {
                    "id": r[0],
                    "name": r[1],
                    "identifier": r[2],
                    "status": _STATUS_MAP.get(r[3], "archived"),
                    "customerName": r[1],
                    "city": "",
                    "customerOfficeLocation": "",
                    "projectType": "",
                }
            cf_name = r[4]
            cf_value = r[5] or ""
            if cf_name == "City":
                projects[pid]["city"] = cf_value
            elif cf_name == "Customer Office Location":
                projects[pid]["customerOfficeLocation"] = cf_value
            elif cf_name == "Project Type":
                projects[pid]["projectType"] = cf_value

        return list(projects.values())

    def get_project_members(self, project_id: int) -> list:
        rows = self.db.execute(
            text("""
                SELECT u.id, u.firstname, u.lastname, e.address, r.name as role_name
                FROM redmine.members m
                JOIN redmine.users u ON u.id = m.user_id AND u.status = 1
                JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                JOIN redmine.member_roles mr ON mr.member_id = m.id
                JOIN redmine.roles r ON r.id = mr.role_id
                WHERE m.project_id = :project_id
            """),
            {"project_id": project_id},
        ).fetchall()
        result = {}
        for r in rows:
            uid = r[0]
            if uid not in result:
                result[uid] = {
                    "user_id": uid,
                    "name": f"{r[1]} {r[2]}".strip(),
                    "email": r[3],
                    "roles": [],
                }
            result[uid]["roles"].append(r[4])
        return list(result.values())

    def get_team_member_ids(self, pm_user_id: int) -> set:
        """Get all user IDs who share at least one project with the PM."""
        rows = self.db.execute(
            text("""
                SELECT DISTINCT tr_m.user_id
                FROM redmine.members pm_m
                JOIN redmine.members tr_m ON tr_m.project_id = pm_m.project_id
                JOIN redmine.users u ON u.id = tr_m.user_id AND u.status = 1
                WHERE pm_m.user_id = :pm_id AND tr_m.user_id != :pm_id2
            """),
            {"pm_id": pm_user_id, "pm_id2": pm_user_id},
        ).fetchall()
        return {r[0] for r in rows}

    def check_project_access(self, pm_user_id: int, target_user_id: int) -> bool:
        """Check if PM and target user share any project."""
        row = self.db.execute(
            text("""
                SELECT 1
                FROM redmine.members pm_m
                JOIN redmine.members tr_m ON tr_m.project_id = pm_m.project_id
                WHERE pm_m.user_id = :pm_id AND tr_m.user_id = :tr_id
                LIMIT 1
            """),
            {"pm_id": pm_user_id, "tr_id": target_user_id},
        ).fetchone()
        return row is not None

    def get_all_issues_for_user(self, user_id: int, assigned_only: bool = False) -> list:
        clauses = "AND m.user_id = :user_id"
        params = {"user_id": user_id}
        if assigned_only:
            clauses += " AND i.assigned_to_id = :user_id"
        rows = self.db.execute(
            text(f"""
                SELECT i.id, i.subject, i.description, st.name as status,
                       p.name as priority, t.name as tracker,
                       i.project_id, pr.name as project_name,
                       u.firstname || ' ' || u.lastname as assigned_to,
                       i.created_on, i.updated_on,
                       i.start_date, i.due_date, i.estimated_hours,
                       i.done_ratio, i.is_private,
                       st.id as status_id, p.id as priority_id, t.id as tracker_id,
                       i.assigned_to_id,
                       i.author_id, auth.firstname || ' ' || auth.lastname as author_name,
                       COALESCE(
                           (SELECT json_agg(json_build_object(
                               'id', a.id, 'filename', a.filename,
                               'content_type', a.content_type, 'filesize', a.filesize
                           )) FROM redmine.attachments a
                           WHERE a.container_id = i.id AND a.container_type = 'Issue'),
                           '[]'::json
                       ) as attachments,
                       COALESCE(
                           (SELECT json_agg(json_build_object(
                               'id', cf.id, 'name', cf.name, 'value', cv.value
                           ) ORDER BY cf.id)
                           FROM redmine.custom_values cv
                           JOIN redmine.custom_fields cf ON cf.id = cv.custom_field_id
                           WHERE cv.customized_id = i.id AND cv.customized_type = 'Issue'),
                           '[]'::json
                       ) as custom_fields
                FROM redmine.issues i
                JOIN redmine.projects pr ON pr.id = i.project_id AND pr.status = 1
                JOIN redmine.members m ON m.project_id = pr.id {clauses}
                LEFT JOIN redmine.users u ON u.id = i.assigned_to_id
                LEFT JOIN redmine.users auth ON auth.id = i.author_id
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.enumerations p ON p.id = i.priority_id
                JOIN redmine.trackers t ON t.id = i.tracker_id
                ORDER BY i.updated_on DESC
            """),
            params,
        ).fetchall()
        return [_issue_row_to_dict(r) for r in rows]

    def get_all_issues_admin(self) -> list:
        rows = self.db.execute(
            text("""
                SELECT i.id, i.subject, i.description, st.name as status,
                       p.name as priority, t.name as tracker,
                       i.project_id, pr.name as project_name,
                       u.firstname || ' ' || u.lastname as assigned_to,
                       i.created_on, i.updated_on,
                       i.start_date, i.due_date, i.estimated_hours,
                       i.done_ratio, i.is_private,
                       st.id as status_id, p.id as priority_id, t.id as tracker_id,
                       i.assigned_to_id,
                       i.author_id, auth.firstname || ' ' || auth.lastname as author_name,
                       COALESCE(
                           (SELECT json_agg(json_build_object(
                               'id', a.id, 'filename', a.filename,
                               'content_type', a.content_type, 'filesize', a.filesize
                           )) FROM redmine.attachments a
                           WHERE a.container_id = i.id AND a.container_type = 'Issue'),
                           '[]'::json
                       ) as attachments,
                       COALESCE(
                           (SELECT json_agg(json_build_object(
                               'id', cf.id, 'name', cf.name, 'value', cv.value
                           ) ORDER BY cf.id)
                           FROM redmine.custom_values cv
                           JOIN redmine.custom_fields cf ON cf.id = cv.custom_field_id
                           WHERE cv.customized_id = i.id AND cv.customized_type = 'Issue'),
                           '[]'::json
                       ) as custom_fields
                FROM redmine.issues i
                JOIN redmine.projects pr ON pr.id = i.project_id AND pr.status = 1
                LEFT JOIN redmine.users u ON u.id = i.assigned_to_id
                LEFT JOIN redmine.users auth ON auth.id = i.author_id
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.enumerations p ON p.id = i.priority_id
                JOIN redmine.trackers t ON t.id = i.tracker_id
                ORDER BY i.updated_on DESC
            """),
        ).fetchall()
        return [_issue_row_to_dict(r) for r in rows]


    def get_duplicate_issue(self, subject: str, project_id: int) -> int | None:
        row = self.db.execute(
            text("""
                SELECT id FROM redmine.issues
                WHERE subject = :subject AND project_id = :project_id
                LIMIT 1
            """),
            {"subject": subject, "project_id": project_id},
        ).fetchone()
        return row[0] if row else None


    def get_all_users(self) -> list:
        rows = self.db.execute(
            text("""
                SELECT u.id, u.login, u.firstname, u.lastname, e.address
                FROM redmine.users u
                JOIN redmine.email_addresses e ON e.user_id = u.id AND e.is_default = true
                WHERE u.status = 1 AND u.type = 'User'
                ORDER BY u.firstname
            """),
        ).fetchall()
        return [
            {
                "id": r[0],
                "login": r[1],
                "name": f"{r[2]} {r[3]}".strip(),
                "email": r[4],
            }
            for r in rows if r[0] != 1
        ]

    def get_all_projects(self) -> list:
        rows = self.db.execute(
            text("""
                SELECT id, name, identifier, status
                FROM redmine.projects
                WHERE status = 1
                ORDER BY name
            """),
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "identifier": r[2], "status": r[3]}
            for r in rows
        ]

    def get_trackers(self) -> list[dict]:
        rows = self.db.execute(
            text("SELECT id, name FROM redmine.trackers ORDER BY id"),
        ).fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]

    def get_priorities(self) -> list[dict]:
        rows = self.db.execute(
            text("SELECT id, name FROM redmine.enumerations WHERE type = 'IssuePriority' ORDER BY id"),
        ).fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]

    def get_statuses(self) -> list[dict]:
        rows = self.db.execute(
            text("SELECT id, name, is_closed FROM redmine.issue_statuses ORDER BY id"),
        ).fetchall()
        return [{"id": r[0], "name": r[1], "is_closed": r[2]} for r in rows]

    def get_custom_fields(self) -> list[dict]:
        rows = self.db.execute(
            text("SELECT id, name, field_format FROM redmine.custom_fields WHERE type = 'IssueCustomField' ORDER BY id"),
        ).fetchall()
        return [{"id": r[0], "name": r[1], "field_format": r[2]} for r in rows]

    def count_daily_issues(self, author_id: int) -> int:
        row = self.db.execute(
            text("""
                SELECT count(*) FROM redmine.issues
                WHERE author_id = :author_id
                  AND created_on::date = CURRENT_DATE
            """),
            {"author_id": author_id},
        ).fetchone()
        return row[0] if row else 0

    def count_daily_updates(self, user_id: int) -> int:
        row = self.db.execute(
            text("""
                SELECT count(*) FROM redmine.journals
                WHERE user_id = :user_id
                  AND journalized_type = 'Issue'
                  AND created_on::date = CURRENT_DATE
            """),
            {"user_id": user_id},
        ).fetchone()
        return row[0] if row else 0

    def count_daily_projects(self, user_id: int) -> int:
        row = self.db.execute(
            text("""
                SELECT count(*) FROM redmine.projects
                WHERE author_id = :user_id
                  AND created_on::date = CURRENT_DATE
            """),
            {"user_id": user_id},
        ).fetchone()
        return row[0] if row else 0

    def is_project_member(self, user_id: int, project_id: int) -> bool:
        row = self.db.execute(
            text("""
                SELECT 1 FROM redmine.members
                WHERE user_id = :user_id AND project_id = :project_id
                LIMIT 1
            """),
            {"user_id": user_id, "project_id": project_id},
        ).fetchone()
        return row is not None

    def search_issues(self, q: str, project_id: int | None = None, limit: int = 20) -> list[dict]:
        q_param = f"%{q}%"
        where = "i.subject ILIKE :q"
        params = {"q": q_param}
        if project_id is not None:
            where += " AND i.project_id = :project_id"
            params["project_id"] = project_id
        rows = self.db.execute(
            text(f"""
                SELECT i.id, i.subject, t.id as tracker_id, t.name as tracker_name,
                       s.id as status_id, s.name as status_name
                FROM redmine.issues i
                JOIN redmine.trackers t ON t.id = i.tracker_id
                JOIN redmine.issue_statuses s ON s.id = i.status_id
                WHERE {where}
                ORDER BY i.updated_on DESC
                LIMIT :limit
            """),
            {**params, "limit": limit},
        ).fetchall()
        return [
            {
                "id": r[0],
                "subject": r[1],
                "tracker_id": r[2],
                "tracker_name": r[3],
                "status_id": r[4],
                "status_name": r[5],
            }
            for r in rows
        ]

    def get_versions(self, project_id: int) -> list[dict]:
        rows = self.db.execute(
            text("""
                SELECT id, name, status, due_date, description
                FROM redmine.versions
                WHERE project_id = :project_id
                ORDER BY name
            """),
            {"project_id": project_id},
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "status": r[2],
                "due_date": r[3].isoformat() if r[3] else None,
                "description": r[4],
            }
            for r in rows
        ]

    def get_categories(self, project_id: int) -> list[dict]:
        rows = self.db.execute(
            text("""
                SELECT id, name
                FROM redmine.issue_categories
                WHERE project_id = :project_id
                ORDER BY name
            """),
            {"project_id": project_id},
        ).fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]
