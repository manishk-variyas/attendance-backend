import httpx
from typing import List, Optional
from app.core.config import settings
from .schemas import ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse
import logging

logger = logging.getLogger(__name__)

class RedmineService:
    def __init__(self):
        self.url = settings.REDMINE_URL
        self.headers = {
            "X-Redmine-API-Key": settings.REDMINE_API_KEY,
            "Content-Type": "application/json"
        }

    async def get_custom_fields(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.url}/custom_fields.json", headers=self.headers)
            response.raise_for_status()
            return response.json().get("custom_fields", [])

    async def get_user_by_email(self, email: str):
        async with httpx.AsyncClient() as client:
            # Redmine doesn't have a direct "find by email" endpoint that works reliably with filters in some versions
            # but we can try filtering users
            response = await client.get(f"{self.url}/users.json?name={email}", headers=self.headers)
            response.raise_for_status()
            users = response.json().get("users", [])
            for user in users:
                if user.get("mail") == email:
                    return user
            return None

    async def get_projects_for_user(self, user_id: int) -> List[ProjectResponse]:
        async with httpx.AsyncClient() as client:
            # We need to get projects where user is a member
            # Redmine API: GET /projects.json?user_id=123
            response = await client.get(f"{self.url}/projects.json?user_id={user_id}&include=custom_fields", headers=self.headers)
            response.raise_for_status()
            projects_data = response.json().get("projects", [])
            
            projects = []
            for p in projects_data:
                custom_values = {cf["name"]: cf.get("value") for cf in p.get("custom_fields", [])}
                projects.append(ProjectResponse(
                    id=p["id"],
                    name=p["name"],
                    identifier=p["identifier"],
                    city=custom_values.get("City", ""),
                    customerName=p["name"], # mapping name to customerName for now
                    customerOfficeLocation=custom_values.get("Customer Office Location", ""),
                    projectType=custom_values.get("Project Type", ""),
                    status="active" # placeholder
                ))
            return projects

    async def create_or_update_project(self, data: ProjectCreate):
        # 1. Find user
        user = await self.get_user_by_email(data.email)
        if not user:
            raise Exception(f"User with email {data.email} not found in Redmine")

        # 2. Find custom fields to get IDs
        cfs = await self.get_custom_fields()
        cf_map = {cf["name"]: cf["id"] for cf in cfs}

        # 3. Create project payload
        payload = {
            "project": {
                "name": data.customerName,
                "identifier": data.customerName.lower().replace(" ", "-"),
                "custom_fields": [
                    {"id": cf_map["City"], "value": data.city},
                    {"id": cf_map["Customer Office Location"], "value": data.customerOfficeLocation},
                    {"id": cf_map["Project Type"], "value": data.projectType}
                ]
            }
        }

        async with httpx.AsyncClient() as client:
            # Check if project exists
            check_resp = await client.get(f"{self.url}/projects/{payload['project']['identifier']}.json", headers=self.headers)
            if check_resp.status_code == 200:
                # Update
                p_id = check_resp.json()["project"]["id"]
                resp = await client.put(f"{self.url}/projects/{p_id}.json", json=payload, headers=self.headers)
            else:
                # Create
                resp = await client.post(f"{self.url}/projects.json", json=payload, headers=self.headers)
            
            resp.raise_for_status()
            
            # 4. Ensure user is a member
            p_data = resp.json().get("project") if resp.status_code == 201 else check_resp.json().get("project")
            p_id = p_data["id"]
            
            membership_payload = {
                "membership": {
                    "user_id": user["id"],
                    "role_ids": [3] # Default to 'Manager' or similar ID
                }
            }
            await client.post(f"{self.url}/projects/{p_id}/memberships.json", json=membership_payload, headers=self.headers)
            
            return p_data

    async def get_all_users_with_projects(self) -> List[UserWithProjects]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.url}/users.json", headers=self.headers)
            response.raise_for_status()
            users_data = response.json().get("users", [])
            
            result = []
            for u in users_data:
                projects = await self.get_projects_for_user(u["id"])
                result.append(UserWithProjects(
                    id=u["id"],
                    firstname=u["firstname"],
                    lastname=u["lastname"],
                    mail=u.get("mail", ""),
                    projects=projects
                ))
            return result

    async def get_issues_for_user(self, user_id: int) -> List[IssueResponse]:
        async with httpx.AsyncClient() as client:
            # Fetch issues where user is assigned or author
            # Redmine API: GET /issues.json?assigned_to_id=123 (or author_id)
            response = await client.get(f"{self.url}/issues.json?assigned_to_id={user_id}", headers=self.headers)
            response.raise_for_status()
            issues_data = response.json().get("issues", [])
            
            issues = []
            for i in issues_data:
                issues.append(IssueResponse(
                    id=i["id"],
                    subject=i["subject"],
                    description=i.get("description"),
                    status=i["status"]["name"],
                    priority=i["priority"]["name"],
                    tracker=i["tracker"]["name"],
                    project_id=i["project"]["id"],
                    project_name=i["project"]["name"],
                    created_on=i["created_on"],
                    updated_on=i["updated_on"]
                ))
            return issues

redmine_service = RedmineService()
