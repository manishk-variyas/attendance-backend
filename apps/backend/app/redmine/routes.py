from fastapi import APIRouter, HTTPException, Depends
from typing import List
from .schemas import ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse
from .service import redmine_service
from app.auth.dependencies import get_current_user # Assuming this exists for Bearer token

router = APIRouter()

@router.post("/projects", response_model=dict)
async def create_project(data: ProjectCreate):
    try:
        project = await redmine_service.create_or_update_project(data)
        return {"status": "success", "project": project}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/projects/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email_alias(email: str):
    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/email/{email}", response_model=List[ProjectResponse])
async def get_user_projects_by_email(email: str):
    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_projects_for_user(user["id"])

@router.get("/user-projects/id/{user_id}", response_model=List[ProjectResponse])
async def get_user_projects_by_id(user_id: int):
    return await redmine_service.get_projects_for_user(user_id)

@router.get("/user-projects", response_model=List[UserWithProjects])
async def get_all_user_projects():
    return await redmine_service.get_all_users_with_projects()

@router.get("/user-issues/email/{email}", response_model=List[IssueResponse])
async def get_user_issues_by_email(email: str):
    user = await redmine_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await redmine_service.get_issues_for_user(user["id"])

@router.get("/user-issues/id/{user_id}", response_model=List[IssueResponse])
async def get_user_issues_by_id(user_id: int):
    return await redmine_service.get_issues_for_user(user_id)
