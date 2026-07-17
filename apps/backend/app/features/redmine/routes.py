from fastapi import APIRouter, HTTPException, Depends, status, Query
from typing import List, Optional
from .schemas import ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse, TimeZoneInfo, ProjectMember, SearchResponse, SearchResults, ProjectSearchItem, PersonSearchItem, ShiftSearchItem
from .service import redmine_service
from .constants import REDMINE_TIMEZONES
from app.features.auth.dependencies import get_current_user
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.features.shifts.service import shift_service
from app.models.employee_master import EmployeeMaster

router = APIRouter()


# ------------------------------------------------------------------ #
#  GET /api/redmine/timezones  — Redmine-compatible timezone list    #
# ------------------------------------------------------------------ #
@router.get("/redmine/timezones", response_model=List[TimeZoneInfo])
async def list_timezones():
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
):
    """Return all Redmine projects. Admin, PM, or PC only."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )
    return await redmine_service.get_all_projects()


@router.post("/projects", response_model=dict)
async def create_project(
    data: ProjectCreate,
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "Admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
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
    current_user: dict = Depends(get_current_user),
):
    """Get issues across all projects scoped by user role.
    Admin → all projects. PM/PC → managed projects. TR → own projects."""
    roles = current_user.get("roles", [])
    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator", "Technical Resource"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    email = current_user.get("email")
    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in Redmine")

    is_tr = "Technical Resource" in roles and "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles

    if "Admin" in roles:
        projects = await redmine_service.get_all_projects()
    else:
        projects = await redmine_service.get_projects_for_user(user["id"])

    all_issues = []
    for p in projects:
        pid = p.id if hasattr(p, 'id') else p.get("id")
        if is_tr:
            issues = await redmine_service.get_issues_for_project(pid, assigned_to_id=user["id"])
        else:
            issues = await redmine_service.get_issues_for_project(pid)
        all_issues.extend(issues)

    return all_issues


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
):
    """Get projects for a user.
    - No user_id: currently logged-in user (auto-creates in Redmine if missing)
    - user_id provided: Admin/PM/PC can view any user's projects
    """
    email = current_user.get("email")
    username = current_user.get("username")
    roles = current_user.get("roles", [])

    if user_id is None:
        if not email:
            raise HTTPException(status_code=400, detail="User email not found in session")
        user = await redmine_service.get_user_by_email(email)
        if not user:
            user = await redmine_service.create_user(username, email)
            if not user:
                raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
        return await redmine_service.get_projects_for_user(user["id"])

    if not any(role in roles for role in ["Admin", "Project Manager", "Project Coordinator"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin, Project Manager, or Project Coordinator access required",
        )

    return await redmine_service.get_projects_for_user(user_id)

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
    """Get all members of a project with their roles. Scoped by user role."""
    roles = current_user.get("roles", [])
    if "Admin" in roles:
        members = await redmine_service.get_project_members(project_id)
    else:
        email = current_user.get("email")
        user = await redmine_service.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=403, detail="Redmine account not found")
        user_projects = await redmine_service.get_projects_for_user(user["id"])
        if not any(p.id == project_id for p in user_projects):
            raise HTTPException(status_code=403, detail="You are not a member of this project")
        members = await redmine_service.get_project_members(project_id)

    for m in members:
        m["email"] = m.get("email", "")
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id == m.get("user_id")).first()
        if emp:
            m["email"] = emp.user_email

    return members


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
