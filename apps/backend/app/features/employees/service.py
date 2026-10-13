from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.features.redmine.sql_service import RedmineSQLService
from app.models.employee_master import EmployeeMaster


def employee_to_dict(e: EmployeeMaster) -> dict:
    return {
        "id": str(e.id),
        "keycloak_user_id": e.keycloak_user_id,
        "redmine_user_id": e.redmine_user_id,
        "first_name": e.first_name,
        "middle_name": e.middle_name,
        "last_name": e.last_name,
        "user_email": e.user_email,
        "designation": e.designation,
        "employee_id": e.employee_id,
        "work_location_status": e.work_location_status,
        "work_address": e.work_address,
        "current_address": e.current_address,
        "permanent_address": e.permanent_address,
        "contact_number": e.contact_number,
        "alt_contact_number": e.alt_contact_number,
        "country": e.country,
        "state_code": e.state_code,
        "title": e.title,
        "status": e.status,
        "is_active": e.is_active,
        "onboarded_at": e.onboarded_at,
        "joining_date": e.joining_date.isoformat() if e.joining_date else None,
        "location_id": str(e.location_id) if e.location_id else None,
        "reports_to": e.reports_to,
        "reports_to_name": e.reports_to_name,
        "profilePictureUrl": f"/api/employees/{e.id}/profile-picture" if e.profile_picture_url else None,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }


_SORT_COLUMNS = {
    "first_name",
    "last_name",
    "user_email",
    "designation",
    "employee_id",
    "status",
    "created_at",
    "updated_at",
}


def _attach_state_names(db: Session, records: list) -> list:
    """Add `state_name` (display name) to each record from its `state_code`."""
    from app.features.states.service import state_name_map
    name_map = state_name_map(db, [r.get("state_code") for r in records])
    for r in records:
        r["state_name"] = name_map.get(r.get("state_code"))
    return records


def _enrich_employees(sql: RedmineSQLService, employees: list) -> list:
    """Return employee dicts enriched with projects/roles/managers via bulk SQL."""
    rm_ids = [e.redmine_user_id for e in employees if e.redmine_user_id]
    user_roles: dict = {}
    project_ids: set = set()
    if rm_ids:
        for m in sql.get_memberships_for_users(rm_ids):
            user_roles.setdefault(m["user_id"], []).append({"project": m["project"], "role": m["role"]})
            project_ids.add(m["project_id"])

    managers_map: dict = {}
    if project_ids:
        for m in sql.get_project_managers(list(project_ids)):
            managers_map.setdefault(m["project"], []).append(m["manager"])

    records = []
    for e in employees:
        rec = employee_to_dict(e)
        roles_list = user_roles.get(e.redmine_user_id, [])
        rec["projects"] = [r["project"] for r in roles_list]
        rec["keycloak_role"] = None
        rec["redmine_roles"] = [
            {**r, "managers": managers_map.get(r["project"], [])}
            for r in roles_list
        ]
        records.append(rec)
    return records


def get_employees_paginated(
    db: Session,
    current_user: dict,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "first_name",
    sort_dir: str = "asc",
    search: Optional[str] = None,
    status: Optional[str] = None,
    onboarded: Optional[bool] = None,
    designation: Optional[str] = None,
    country: Optional[str] = None,
    work_location_status: Optional[str] = None,
    is_active: Optional[bool] = None,
    reports_to: Optional[int] = None,
    project_id: Optional[str] = None,
    export: bool = False,
) -> dict:
    roles = current_user.get("roles", [])
    sql = RedmineSQLService(db)

    query = db.query(EmployeeMaster)

    if "Admin" not in roles:
        if "Project Manager" not in roles and "Project Coordinator" not in roles:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        rm_user = sql.get_user_by_email(current_user.get("email"))
        if not rm_user:
            raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
        member_ids = sql.get_team_member_ids(rm_user["id"])
        member_ids.add(rm_user["id"])
        query = query.filter(EmployeeMaster.redmine_user_id.in_(member_ids))

    if status:
        query = query.filter(EmployeeMaster.status == status)
    if onboarded is True:
        query = query.filter(EmployeeMaster.onboarded_at.isnot(None))
    elif onboarded is False:
        query = query.filter(EmployeeMaster.onboarded_at.is_(None))
    if designation:
        query = query.filter(EmployeeMaster.designation == designation)
    if country:
        query = query.filter(EmployeeMaster.country == country)
    if work_location_status:
        query = query.filter(EmployeeMaster.work_location_status == work_location_status)
    if is_active is not None:
        query = query.filter(EmployeeMaster.is_active == is_active)
    if reports_to is not None:
        query = query.filter(EmployeeMaster.reports_to == reports_to)

    if project_id:
        try:
            pids = [int(x.strip()) for x in project_id.split(",") if x.strip()][:100]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid project_id. Use comma-separated Redmine project ids.")
        if pids:
            member_ids = sql.get_project_member_ids(pids)
            if member_ids:
                query = query.filter(EmployeeMaster.redmine_user_id.in_(member_ids))
            else:
                query = query.filter(EmployeeMaster.redmine_user_id.is_(None))  # no members → empty result

    employees = query.all()

    if search:
        term = search.strip().lower()[:100]
        if term:
            employees = [
                e for e in employees
                if term in " ".join(filter(None, [
                    e.first_name, e.middle_name, e.last_name, e.user_email,
                    e.employee_id, e.designation, e.reports_to_name,
                ])).lower()
            ]

    sort_attr = sort_by if sort_by in _SORT_COLUMNS else "first_name"
    employees.sort(key=lambda e: (getattr(e, sort_attr) or ""), reverse=(sort_dir == "desc"))

    total = len(employees)

    if export:
        records = _attach_state_names(db, _enrich_employees(sql, employees))
        return {
            "records": records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size) if total else 0,
        }

    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    offset = (page - 1) * page_size
    page_employees = employees[offset:offset + page_size]

    records = _attach_state_names(db, [employee_to_dict(e) for e in page_employees])

    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_employee_projects(db: Session, current_user: dict, employee_id: str) -> dict:
    roles = current_user.get("roles", [])
    sql = RedmineSQLService(db)

    employee = db.query(EmployeeMaster).filter(EmployeeMaster.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    if "Admin" not in roles:
        if "Project Manager" not in roles and "Project Coordinator" not in roles:
            raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")
        rm_user = sql.get_user_by_email(current_user.get("email"))
        if not rm_user:
            raise HTTPException(status_code=404, detail="Current user not found in Redmine.")
        member_ids = sql.get_team_member_ids(rm_user["id"])
        member_ids.add(rm_user["id"])
        if employee.redmine_user_id not in member_ids:
            raise HTTPException(status_code=403, detail="You can only view your team members.")

    roles_list: list = []
    project_ids: set = set()
    if employee.redmine_user_id:
        for m in sql.get_memberships_for_users([employee.redmine_user_id]):
            roles_list.append({"project": m["project"], "role": m["role"]})
            project_ids.add(m["project_id"])

    managers_map: dict = {}
    if project_ids:
        for m in sql.get_project_managers(list(project_ids)):
            managers_map.setdefault(m["project"], []).append(m["manager"])

    return {
        "employee_id": str(employee.id),
        "first_name": employee.first_name,
        "last_name": employee.last_name,
        "user_email": employee.user_email,
        "reports_to": employee.reports_to,
        "reports_to_name": employee.reports_to_name,
        "projects": [r["project"] for r in roles_list],
        "redmine_roles": [
            {**r, "managers": managers_map.get(r["project"], [])}
            for r in roles_list
        ],
    }
