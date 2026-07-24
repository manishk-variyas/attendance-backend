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
                       i.created_on, i.updated_on
                FROM redmine.issues i
                JOIN redmine.projects pr ON pr.id = i.project_id AND pr.status = 1
                JOIN redmine.members m ON m.project_id = pr.id {clauses}
                LEFT JOIN redmine.users u ON u.id = i.assigned_to_id
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
                       i.created_on, i.updated_on
                FROM redmine.issues i
                JOIN redmine.projects pr ON pr.id = i.project_id AND pr.status = 1
                LEFT JOIN redmine.users u ON u.id = i.assigned_to_id
                JOIN redmine.issue_statuses st ON st.id = i.status_id
                JOIN redmine.enumerations p ON p.id = i.priority_id
                JOIN redmine.trackers t ON t.id = i.tracker_id
                ORDER BY i.updated_on DESC
            """),
        ).fetchall()
        return [_issue_row_to_dict(r) for r in rows]


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
