import json
import os
import logging
import httpx
import openpyxl
from io import BytesIO
from fastapi import APIRouter, HTTPException, Depends, status, Query, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError
from typing import List, Optional
from datetime import date, timedelta, datetime
from .schemas import IssueCreate, IssueUpdate, ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse, IssueListResponse, TimeZoneInfo, ProjectMember, SearchResponse, SearchResults, ProjectSearchItem, PersonSearchItem, ShiftSearchItem
from .service import redmine_service
from .sql_service import RedmineSQLService
from .constants import REDMINE_TIMEZONES
from app.features.auth.dependencies import get_current_user
from app.core.limiter import limiter
from app.utils.audit import audit_logger
from app.middleware.logging import _get_client_ip
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.shifts.service import shift_service
from app.models.employee_master import EmployeeMaster

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".pdf",
    ".txt", ".csv", ".log",
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm",
}

RECORDING_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm"}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_FILE_COUNT = 2


def _validation_errors(e: PydanticValidationError) -> list[dict]:
    return [
        {"field": err["loc"][-1] if err["loc"] else "__root__", "message": err["msg"]}
        for err in e.errors()
    ]


def _validate_uploaded_file(f: UploadFile):
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

router = APIRouter()


# ------------------------------------------------------------------ #
#  GET /api/redmine/timezones  — Redmine-compatible timezone list    #
# ------------------------------------------------------------------ #
@router.get("/redmine/timezones", response_model=List[TimeZoneInfo])
async def list_timezones(
    current_user: dict = Depends(get_current_user),
):
    """Return the full list of Redmine-compatible timezones.
    Mirrors the timezone dropdown from Redmine's user preferences form.
    Used by the onboarding screen to pick a user's timezone."""
    return REDMINE_TIMEZONES


# ------------------------------------------------------------------ #
#  GET /api/redmine/roles  — Redmine role list                       #
# ------------------------------------------------------------------ #
@router.get("/redmine/roles")
async def list_redmine_roles(
    current_user: dict = Depends(get_current_user),
):
    """Return all Redmine roles. Admin, PM, or PC only."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    return await redmine_service.get_roles()


# ------------------------------------------------------------------ #
#  GET /api/redmine/users  — Roster: employee dropdown               #
# ------------------------------------------------------------------ #
@router.get("/redmine/users")
async def list_redmine_users(
    current_user: dict = Depends(get_current_user),
):
    """Return a lightweight list of all active Redmine users (id, name, email).
    Used to populate the employee dropdown on the admin roster page."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    return await redmine_service.get_all_users()


@router.get("/projects", response_model=List[dict])
async def list_all_projects(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine projects (direct SQL). Admin, PM, or PC only."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    sql = RedmineSQLService(db)
    return sql.get_all_projects()

# ── BACKUP: old HTTP-based handler ──────────────────────────────────
# @router.get("/projects", response_model=List[dict])
# async def list_all_projects_http(
#     current_user: dict = Depends(get_current_user),
# ):
#     roles = current_user.get("roles", [])
#     if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Admin, Project Manager, or Project Coordinator access required",
#         )
#     return await redmine_service.get_all_projects()


@router.post("/projects", response_model=dict)
async def create_project(
    data: ProjectCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Rate limit: max 5 projects per admin per day
    sql = RedmineSQLService(db)
    email = current_user.get("email")
    user = sql.get_user_by_email(email)
    if user:
        daily = sql.count_daily_projects(user["id"])
        if daily >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You can create up to 5 projects per day. Please try again tomorrow.",
            )

    try:
        project = await redmine_service.create_or_update_project(data)
        return {"status": "success", "project": project}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/projects/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email_alias(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    # Admin or self
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/email/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    # Admin or self
    if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/id/{user_id}", response_model=List[ProjectResponse])
async def get_user_projects_by_id(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    # Admin only (since we can't easily check self by ID here without a lookup)
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
        
    return await redmine_service.get_projects_for_user(user_id)

@router.get("/user-projects", response_model=List[UserWithProjects])
async def get_all_user_projects(
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_all_users_with_projects()

# @router.get("/user-issues/email/{email}", response_model=List[IssueResponse])
# async def get_user_issues_by_email(
#     email: str,
#     current_user: dict = Depends(get_current_user)
# ):
#     # Admin or self
#     if current_user["email"] != email and "Admin" not in current_user.get("roles", []):
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
#
#     user = await redmine_service.get_user_by_email(email)
#     if not user:
#         raise HTTPException(status_code=404, detail="User not found")
#     return await redmine_service.get_issues_for_user(user["id"])


@router.get("/projects/all/issues", response_model=List[IssueResponse])
async def get_all_project_issues(
    team: Optional[bool] = Query(False, description="Set to true to view your team's issues. Admin/PM/PC only."),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    email = current_user.get("email")
    current_rm_user = sql.get_user_by_email(email)
    if not current_rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = not is_admin and not is_pm_or_pc

    # Own issues (default) — all roles see only assigned issues
    if not team:
        if is_tr:
            return sql.get_all_issues_for_user(current_rm_user["id"], assigned_only=True, include_authored=True)
        return sql.get_all_issues_for_user(current_rm_user["id"], assigned_only=True)

    # Team issues
    if is_admin:
        return sql.get_all_issues_admin()

    if not is_pm_or_pc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin, PM, or PC can view team issues.",
        )

    team_ids = sql.get_team_member_ids(current_rm_user["id"])
    team_ids.add(current_rm_user["id"])
    seen = set()
    issues = []
    for tid in team_ids:
        for issue in sql.get_all_issues_for_user(tid, assigned_only=False):
            if issue["id"] not in seen:
                seen.add(issue["id"])
                issues.append(issue)
    return issues


def _parse_issue_date(value: Optional[str], add_day: bool = False):
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    return d + timedelta(days=1) if add_day else d


def _paginated_response(records: list, total: int, page: int, page_size: int) -> dict:
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    return {"records": records, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


def _build_issues_excel(records: list) -> BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Issues"
    ws.append(["ID", "Subject", "Description", "Status", "Priority", "Tracker", "Project",
               "Assigned To", "Author", "Created On", "Updated On", "Start Date", "Due Date",
               "Estimated Hours", "Done Ratio"])
    for r in records:
        ws.append([
            r.get("id"),
            r.get("subject"),
            r.get("description"),
            r.get("status"),
            r.get("priority"),
            r.get("tracker"),
            r.get("project_name"),
            r.get("assigned_to_name"),
            r.get("author_name"),
            r.get("created_on"),
            r.get("updated_on"),
            r.get("start_date"),
            r.get("due_date"),
            r.get("estimated_hours"),
            r.get("done_ratio"),
        ])
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _export_issues(request: Request, current_user: dict, records: list, label: str):
    output = _build_issues_excel(records)
    audit_logger.info(
        f"Issues exported by {current_user.get('username')}",
        extra={
            "correlation_id": request.state.correlation_id,
            "extra_data": {
                "action": f"export_{label}_issues",
                "username": current_user.get("username"),
                "status": "success",
                "client_ip": _get_client_ip(request),
                "exported_rows": len(records),
            },
        },
    )
    filename = f"{label}_issues_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/my-issues/paginated", response_model=IssueListResponse)
@limiter.limit("30/minute")
async def get_my_issues_paginated(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("updated_on", description="updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name"),
    sort_dir: str = Query("desc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    status_id: Optional[int] = Query(None, description="Filter by status id"),
    priority_id: Optional[int] = Query(None, description="Filter by priority id"),
    tracker_id: Optional[int] = Query(None, description="Filter by tracker id"),
    project_id: Optional[int] = Query(None, description="Filter by project id"),
    search: Optional[str] = Query(None, description="Search subject or author name"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(r in roles for r in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=403, detail="Access denied")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_tr = "Technical Resource" in roles and "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles

    records, total = sql.get_issues_paginated(
        scope_user_id=rm_user["id"],
        include_authored=is_tr,
        status_id=status_id,
        priority_id=priority_id,
        tracker_id=tracker_id,
        project_id=project_id,
        search=(search or "").strip()[:100] or None,
        from_date=_parse_issue_date(from_date),
        to_date=_parse_issue_date(to_date, add_day=True),
        sort_by=sort_by,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
        export=export,
    )
    if export:
        return _export_issues(request, current_user, records, "my")
    return _paginated_response(records, total, page, page_size)


@router.get("/team-issues", response_model=IssueListResponse)
@limiter.limit("30/minute")
async def get_team_issues_paginated(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("updated_on", description="updated_on, created_on, subject, priority, status, due_date, done_ratio, project_name"),
    sort_dir: str = Query("desc", description="asc or desc"),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    status_id: Optional[int] = Query(None, description="Filter by status id"),
    priority_id: Optional[int] = Query(None, description="Filter by priority id"),
    tracker_id: Optional[int] = Query(None, description="Filter by tracker id"),
    project_id: Optional[int] = Query(None, description="Filter by project id"),
    search: Optional[str] = Query(None, description="Search subject or author name"),
    export: bool = Query(False, description="Export as Excel spreadsheet"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not any(r in roles for r in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    sql = RedmineSQLService(db)
    rm_user = sql.get_user_by_email(current_user.get("email"))
    if not rm_user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_admin = "Admin" in roles
    team_ids = None
    if not is_admin:
        team_ids = sql.get_team_member_ids(rm_user["id"])
        team_ids.add(rm_user["id"])

    records, total = sql.get_issues_paginated(
        admin_all=is_admin,
        team_ids=team_ids,
        status_id=status_id,
        priority_id=priority_id,
        tracker_id=tracker_id,
        project_id=project_id,
        search=(search or "").strip()[:100] or None,
        from_date=_parse_issue_date(from_date),
        to_date=_parse_issue_date(to_date, add_day=True),
        sort_by=sort_by,
        sort_dir=sort_dir if sort_dir in ("asc", "desc") else "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
        export=export,
    )
    if export:
        return _export_issues(request, current_user, records, "team")
    return _paginated_response(records, total, page, page_size)


@router.get("/projects/{project_id}/issues", response_model=List[IssueResponse])
async def get_project_issues(
    project_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get issues for a project.
    - Admin/PM/PC: sees all issues.
    - TR: sees only issues assigned to them.
    """
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    is_tr = "Technical Resource" in roles and "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles

    if is_tr:
        user = await redmine_service.get_user_by_email(current_user.get("email"))
        if not user:
            raise HTTPException(status_code=404, detail="User not found in Redmine")
        return await redmine_service.get_issues_for_project(project_id, assigned_to_id=user["id"])

    return await redmine_service.get_issues_for_project(project_id)

@router.get("/user-issues/id/{user_id}", response_model=List[IssueResponse])
async def get_user_issues_by_id(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_issues_for_user(user_id)

@router.get("/my-projects", response_model=List[ProjectResponse])
async def get_my_projects(
    user_id: Optional[int] = Query(None, description="Admin/PM/PC: view another user's projects"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get projects for a user (direct SQL — no Redmine HTTP API)."""
    email = current_user.get("email")
    roles = current_user.get("roles", [])

    sql = RedmineSQLService(db)

    if user_id is None:
        if not email:
            raise HTTPException(status_code=400, detail="User email not found in session")
        user = sql.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found in Redmine. Contact admin to sync your account.")
        return sql.get_projects_for_user_rich(user["id"])

    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )

    return sql.get_projects_for_user_rich(user_id)

# ── BACKUP: old HTTP-based handler (Redmine API) ────────────────────
# @router.get("/my-projects", response_model=List[ProjectResponse])
# async def get_my_projects_http(
#     user_id: Optional[int] = Query(None, description="Admin/PM/PC: view another user's projects"),
#     current_user: dict = Depends(get_current_user),
# ):
#     email = current_user.get("email")
#     username = current_user.get("username")
#     roles = current_user.get("roles", [])
#
#     if user_id is None:
#         if not email:
#             raise HTTPException(status_code=400, detail="User email not found in session")
#         user = await redmine_service.get_user_by_email(email)
#         if not user:
#             user = await redmine_service.create_user(username, email)
#             if not user:
#                 raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
#         return await redmine_service.get_projects_for_user(user["id"])
#
#     if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Admin, Project Manager, or Project Coordinator access required",
#         )
#
#     return await redmine_service.get_projects_for_user(user_id)

@router.get("/my-issues", response_model=List[IssueResponse])
async def get_my_issues(current_user: dict = Depends(get_current_user)):
    """Get issues for the currently logged-in user, auto-creating them in Redmine if missing."""
    email = current_user.get("email")
    username = current_user.get("username")
    
    if not email:
        raise HTTPException(status_code=400, detail="User email not found in session")
        
    user = await redmine_service.get_user_by_email(email)
    if not user:
        user = await redmine_service.create_user(username, email)
        if not user:
            raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
            
    return await redmine_service.get_issues_for_user(user["id"])


@router.get("/project-members/{project_id}", response_model=List[ProjectMember])
async def get_project_members(
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all members of a project (direct SQL). Scoped by user role."""
    roles = current_user.get("roles", [])
    sql = RedmineSQLService(db)

    if "Admin" in roles:
        members = sql.get_project_members(project_id)
    else:
        email = current_user.get("email")
        user = sql.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=403, detail="Redmine account not found")
        user_projects = sql.get_projects_for_user(user["id"])
        if not any(p.id == project_id for p in user_projects):
            raise HTTPException(status_code=403, detail="You are not a member of this project")
        members = sql.get_project_members(project_id)

    for m in members:
        m["email"] = m.get("email", "")
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m.get("user_id")).first()
        if emp:
            m["email"] = emp.user_email
            emp_name = f"{emp.first_name or ''} {emp.middle_name or ''} {emp.last_name or ''}".strip().replace("  ", " ")
            if emp_name:
                m["name"] = emp_name

    return members

# ── BACKUP: old HTTP-based handler ──────────────────────────────────
# @router.get("/project-members/{project_id}", response_model=List[ProjectMember])
# async def get_project_members_http(
#     project_id: int,
#     current_user: dict = Depends(get_current_user),
#     db: Session = Depends(get_db),
# ):
#     roles = current_user.get("roles", [])
#     if "Admin" in roles:
#         members = await redmine_service.get_project_members(project_id)
#     else:
#         email = current_user.get("email")
#         user = await redmine_service.get_user_by_email(email)
#         if not user:
#             raise HTTPException(status_code=403, detail="Redmine account not found")
#         user_projects = await redmine_service.get_projects_for_user(user["id"])
#         if not any(p.id == project_id for p in user_projects):
#             raise HTTPException(status_code=403, detail="You are not a member of this project")
#         members = await redmine_service.get_project_members(project_id)
#
#     for m in members:
#         m["email"] = m.get("email", "")
#         emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m.get("user_id")).first()
#         if emp:
#             m["email"] = emp.user_email
#
#     return members


@router.get("/search", response_model=SearchResponse)
async def global_search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(5, ge=1, le=50, description="Max results per section"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Global search across projects, people, and shifts."""
    projects = await redmine_service.search_projects(q, limit)
    project_ids = [int(p["id"]) for p in projects]
    people = await redmine_service.search_people(project_ids, limit) if project_ids else []
    shifts = await shift_service.search_shifts(db, q, limit)

    return SearchResponse(
        query=q,
        results=SearchResults(
            projects=[ProjectSearchItem(**p) for p in projects],
            people=[PersonSearchItem(**p) for p in people],
            shifts=[ShiftSearchItem(**s) for s in shifts],
        ),
    )


@router.get("/redmine/trackers")
async def list_trackers(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue trackers (Bug, Feature, Support, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_trackers()


@router.get("/redmine/priorities")
async def list_priorities(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue priorities (Low, Normal, High, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_priorities()


@router.get("/redmine/statuses")
async def list_statuses(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue statuses (New, In Progress, Closed, etc.)"""
    sql = RedmineSQLService(db)
    return sql.get_statuses()


@router.get("/redmine/users")
async def list_users(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all active Redmine users for assignee/watcher dropdowns."""
    sql = RedmineSQLService(db)
    return sql.get_all_users()


@router.get("/redmine/custom_fields")
async def list_custom_fields(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all Redmine issue custom field definitions."""
    sql = RedmineSQLService(db)
    return sql.get_custom_fields()


@router.get("/redmine/issues/search")
async def search_redmine_issues(
    q: str = Query(..., min_length=1, description="Search query"),
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search issues by subject (ILIKE). Optionally filter by project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.search_issues(q, project_id, limit)


@router.get("/redmine/versions")
async def list_versions(
    project_id: int = Query(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all versions (sprint/milestones) for a project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.get_versions(project_id)


@router.get("/redmine/categories")
async def list_categories(
    project_id: int = Query(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all issue categories for a project."""
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    sql = RedmineSQLService(db)
    return sql.get_categories(project_id)


@router.post("/redmine/issues", status_code=201)
async def create_redmine_issue(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    data: str = Form(...),
    files: list[UploadFile] = File(None),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Parse JSON data
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    try:
        issue_data = IssueCreate(**parsed)
    except PydanticValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors(e))

    sql = RedmineSQLService(db)
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redmine user not found. Contact admin to sync your account.",
        )

    redmine_user_id = user["id"]

    # Rate limit: max 50 issues per user per day
    daily_count = sql.count_daily_issues(redmine_user_id)
    if daily_count >= 50:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can create up to 50 tickets per day. Please try again tomorrow.",
        )

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    # Project membership check (skip for Admin)
    if not is_admin:
        if not sql.is_project_member(redmine_user_id, issue_data.project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this project",
            )

    # Duplicate check: same subject + same project
    existing = sql.get_duplicate_issue(issue_data.subject, issue_data.project_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An issue with this subject already exists in this project",
        )

    # Build issue payload
    issue_attrs = issue_data.model_dump(exclude_none=True)
    issue_attrs["author_id"] = redmine_user_id

    # Validate assignee is a project member
    if "assigned_to_id" in issue_attrs and issue_attrs["assigned_to_id"] != redmine_user_id:
        if not sql.is_project_member(issue_attrs["assigned_to_id"], issue_data.project_id):
            del issue_attrs["assigned_to_id"]

    # Upload files to Redmine and get tokens
    uploads = []
    if files:
        if len(files) > MAX_FILE_COUNT:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_FILE_COUNT} files allowed per request",
            )
        for f in files:
            _validate_uploaded_file(f)
            file_bytes = await f.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {f.filename} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
            upload_result = await redmine_service.upload_file(file_bytes, f.filename, f.content_type)
            uploads.append({
                "token": upload_result["token"],
                "filename": f.filename or "attachment",
                "content_type": f.content_type or "application/octet-stream",
                "description": "",
            })

    if uploads:
        issue_attrs["uploads"] = uploads

    try:
        issue = await redmine_service.create_issue(issue_attrs, switch_user=user["login"])
        return {"status": "success", "message": "Issue created successfully", "id": issue["id"]}
    except httpx.HTTPStatusError as e:
        logger.error("Redmine error (create issue): status=%s body=%s", e.response.status_code, e.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Redmine error: {e.response.text}",
        )
    except Exception as e:
        logger.error("Failed to create issue: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create issue: {e}",
        )


@router.get("/redmine/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{redmine_service.url}/attachments/download/{attachment_id}",
            headers={"X-Redmine-API-Key": redmine_service.headers["X-Redmine-API-Key"]},
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Attachment not found")
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(
            resp.iter_bytes(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="attachment-{attachment_id}"',
                "Content-Length": resp.headers.get("content-length", ""),
            },
        )


@router.delete("/redmine/attachments/{attachment_id}")
async def delete_attachment(
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    sql = RedmineSQLService(db)
    info = sql.get_attachment_issue_info(attachment_id)
    if not info:
        raise HTTPException(status_code=404, detail="Attachment not found")

    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="Redmine user not found")
    redmine_user_id = user["id"]

    if is_admin:
        pass
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, info["project_id"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this issue's project",
            )
    elif is_tr:
        if info["assigned_to_id"] != redmine_user_id and info["author_id"] != redmine_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete attachments from tickets you created or are assigned to",
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    deleted = await redmine_service.delete_attachment(attachment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return {"status": "success", "message": "Attachment deleted successfully", "id": attachment_id}


@router.post("/redmine/issues/{issue_id}/attachments")
async def upload_issue_attachment(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    sql = RedmineSQLService(db)
    email = current_user.get("email")
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="Redmine user not found")

    redmine_user_id = user["id"]
    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    issue_pid = sql.get_issue_project_id(issue_id)
    if issue_pid is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    if is_admin:
        pass
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, issue_pid):
            raise HTTPException(status_code=403, detail="You are not a member of this issue's project")
    elif is_tr:
        assigned_to = sql.get_issue_assigned_to(issue_id)
        author_id = sql.get_issue_author_id(issue_id)
        if assigned_to != redmine_user_id and author_id != redmine_user_id:
            raise HTTPException(status_code=403, detail="You can only add recordings to tickets you created or are assigned to")
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    # Rate limit: max 20 uploads per user per day
    daily = sql.count_daily_updates(redmine_user_id)
    if daily >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can upload up to 20 recordings per day. Please try again tomorrow.",
        )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in RECORDING_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only audio recordings are allowed. Got '{ext}'",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")

    existing = sql.count_issue_attachments(issue_id)
    if existing + 1 > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Attachment limit reached (10 per ticket). Create a new ticket and attach there.",
        )

    upload_result = await redmine_service.upload_file(file_bytes, file.filename, file.content_type)
    uploads = [{
        "token": upload_result["token"],
        "filename": file.filename or "recording",
        "content_type": file.content_type or "application/octet-stream",
        "description": "",
    }]

    await redmine_service.update_issue(issue_id, {"uploads": uploads}, switch_user=user["login"])
    return {"status": "success", "message": "Recording uploaded", "id": issue_id}


@router.get("/redmine/issues/{issue_id}")
async def get_redmine_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    sql = RedmineSQLService(db)
    issue = sql.get_issue_by_id_enriched(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {"status": "success", "issue": issue}


@router.delete("/redmine/issues/{issue_id}")
async def delete_redmine_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    deleted = await redmine_service.delete_issue(issue_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Issue not found")

    return {"status": "success", "message": "Issue deleted successfully", "id": issue_id}


@router.put("/redmine/issues/{issue_id}")
async def update_redmine_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    data: str = Form(...),
    files: list[UploadFile] = File(None),
):
    roles = current_user.get("roles", [])
    email = current_user.get("email")

    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Parse JSON data
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    try:
        issue_data = IssueUpdate(**parsed)
    except PydanticValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors(e))

    sql = RedmineSQLService(db)
    user = sql.get_user_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redmine user not found. Contact admin to sync your account.",
        )

    redmine_user_id = user["id"]

    # Rate limit: max 20 updates per user per day
    daily = sql.count_daily_updates(redmine_user_id)
    if daily >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You can update up to 20 tickets per day. Please try again tomorrow.",
        )

    is_admin = "Admin" in roles
    is_pm_or_pc = "Project Manager" in roles or "Project Coordinator" in roles
    is_tr = "Technical Resource" in roles and not is_admin and not is_pm_or_pc

    # Role-based access control
    issue_pid = sql.get_issue_project_id(issue_id)
    if issue_pid is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    if is_admin:
        pass  # full access
    elif is_pm_or_pc:
        if not sql.is_project_member(redmine_user_id, issue_pid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this issue's project",
            )
    elif is_tr:
        assigned_to = sql.get_issue_assigned_to(issue_id)
        author_id = sql.get_issue_author_id(issue_id)
        if assigned_to != redmine_user_id and author_id != redmine_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only edit tickets you created or are assigned to",
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Build issue payload
    issue_attrs = issue_data.model_dump(exclude_none=True)
    notes = issue_attrs.pop("notes", None)
    private_notes = issue_attrs.pop("private_notes", None)

    if notes:
        issue_attrs["notes"] = notes
    if private_notes is not None:
        issue_attrs["private_notes"] = private_notes

    # Validate assignee is a project member
    if "assigned_to_id" in issue_attrs and issue_attrs["assigned_to_id"] != redmine_user_id:
        if not sql.is_project_member(issue_attrs["assigned_to_id"], issue_pid):
            del issue_attrs["assigned_to_id"]

    # Upload files to Redmine and get tokens
    uploads = []
    if files:
        existing = sql.count_issue_attachments(issue_id)
        if existing + len(files) > 10:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment limit reached (10 per ticket). Create a new ticket and attach there.",
            )
        for f in files:
            _validate_uploaded_file(f)
            file_bytes = await f.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {f.filename} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
            upload_result = await redmine_service.upload_file(file_bytes, f.filename, f.content_type)
            uploads.append({
                "token": upload_result["token"],
                "filename": f.filename or "attachment",
                "content_type": f.content_type or "application/octet-stream",
                "description": "",
            })

    if uploads:
        issue_attrs["uploads"] = uploads

    try:
        issue = await redmine_service.update_issue(issue_id, issue_attrs, switch_user=user["login"])
        return {"status": "success", "message": "Issue updated successfully", "id": issue_id}
    except httpx.HTTPStatusError as e:
        logger.error("Redmine error (update issue): status=%s body=%s", e.response.status_code, e.response.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Redmine error: {e.response.text}",
        )
    except Exception as e:
        logger.error("Failed to update issue: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update issue: {e}",
        )
