from fastapi import APIRouter, HTTPException, Depends, status
from typing import List
from .schemas import ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse
from .service import redmine_service
from app.features.auth.dependencies import get_current_user

router = APIRouter()


# ------------------------------------------------------------------ #
#  GET /api/redmine/users  — Roster: employee dropdown               #
# ------------------------------------------------------------------ #
@router.get("/redmine/users")
async def list_redmine_users(
    current_user: dict = Depends(get_current_user),
):
    """Return a lightweight list of all active Redmine users (id, name, email).
    Used to populate the employee dropdown on the admin roster page."""
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return await redmine_service.get_all_users()


@router.post("/projects", response_model=dict)
async def create_project(
    data: ProjectCreate,
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "admin" not in current_user.get("roles", []):
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
    if current_user["email"] != email and "admin" not in current_user.get("roles", []):
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
    if current_user["email"] != email and "admin" not in current_user.get("roles", []):
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
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_projects_for_user(user_id)

@router.get("/user-projects", response_model=List[UserWithProjects])
async def get_all_user_projects(
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_all_users_with_projects()

@router.get("/user-issues/email/{email}", response_model=List[IssueResponse])
async def get_user_issues_by_email(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    # Admin or self
    if current_user["email"] != email and "admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_issues_for_user(user["id"])

@router.get("/user-issues/id/{user_id}", response_model=List[IssueResponse])
async def get_user_issues_by_id(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    # Admin only
    if "admin" not in current_user.get("roles", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        
    return await redmine_service.get_issues_for_user(user_id)

@router.get("/my-projects", response_model=List[ProjectResponse])
async def get_my_projects(current_user: dict = Depends(get_current_user)):
    """Get projects for the currently logged-in user, auto-creating them in Redmine if missing."""
    email = current_user.get("email")
    username = current_user.get("username")
    
    if not email:
        raise HTTPException(status_code=400, detail="User email not found in session")
        
    user = await redmine_service.get_user_by_email(email)
    if not user:
        # Auto-sync: Create user in Redmine if they exist in Keycloak but not Redmine
        user = await redmine_service.create_user(username, email)
        if not user:
            raise HTTPException(status_code=500, detail="Failed to sync user with Redmine")
            
    return await redmine_service.get_projects_for_user(user["id"])

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
