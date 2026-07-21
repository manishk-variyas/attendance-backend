import httpx
from typing import List, Optional
from app.core.config import settings
from .schemas import ProjectCreate, ProjectResponse, UserWithProjects, IssueResponse
import secrets
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

    async def create_user(self, username: str, email: str, password: Optional[str] = None, firstname: Optional[str] = None, lastname: str = "", timezone: str = "UTC"):
        """
        Creates a user in Redmine. If the user already exists, returns the existing user.
        Uses the username as the default firstname if none is provided.

        timezone: IANA timezone name (e.g. 'Asia/Kolkata') stored in Redmine's
                  user preferences.time_zone. Must match Keycloak's timezone attribute
                  so that the value is consistent across both systems.
        """
        # Use provided firstname or fall back to username so we can identify them
        fname = firstname or username
        lname = lastname or "-"

        # 1. Check for existing user (idempotency — avoid duplicates on retry)
        existing_user = await self.get_user_by_email(email)
        if existing_user:
            logger.info(f"User {email} already exists in Redmine. Sync skipped.")
            return existing_user

        # 2. Create in Redmine with timezone in preferences so Redmine's
        #    timesheet / date displays match the user's configured timezone.
        async with httpx.AsyncClient() as client:
            payload = {
                    "user": {
                    "login": username,
                    "mail": email,
                    "firstname": fname,
                    "lastname": lname,
                    "password": password or secrets.token_urlsafe(16),
                    },
               "pref": {
                    "time_zone": timezone, 
                },
            }
            # payload = {
            #     "user": {
            #         "login": username,
            #         "mail": email,
            #         "firstname": fname,
            #         "lastname": lname,
            #         "password": password or secrets.token_urlsafe(16),
            #         "preferences": {
            #             "time_zone": timezone,
            #         },
            #     }
            # }
            try:
                response = await client.post(f"{self.url}/users.json", json=payload, headers=self.headers)
                if response.status_code == 201:
                    logger.info(f"Successfully synced user {email} to Redmine.")
                    return response.json().get("user")
                elif response.status_code == 422:
                    logger.warning(f"Redmine user creation failed: {response.text}")
                    existing = await self.get_user_by_login(username)
                    if existing:
                        logger.info(f"Found existing Redmine user {username} by login — returning it.")
                        return existing
                    return await self.get_user_by_email(email)
                response.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to sync user to Redmine: {e}")
                raise

    async def get_user_by_email(self, email: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.url}/users.json?name={email}", headers=self.headers)
            response.raise_for_status()
            users = response.json().get("users", [])
            for user in users:
                if user.get("mail") == email:
                    return user
            return None

    async def get_user_by_login(self, login: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.url}/users.json?name={login}", headers=self.headers)
            response.raise_for_status()
            users = response.json().get("users", [])
            for user in users:
                if user.get("login") == login:
                    return user
            return None

    async def get_all_users(self) -> list:
        """Fetch all active users from Redmine — lightweight list for dropdowns."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/users.json?limit=100&status=1",
                headers=self.headers
            )
            response.raise_for_status()
            users = response.json().get("users", [])
            return [
                {
                    "id": u["id"],
                    "login": u.get("login", ""),
                    "name": f"{u['firstname']} {u['lastname']}".strip(),
                    "email": u.get("mail", ""),
                }
                for u in users if u.get("id") != 1
            ]

    async def get_all_projects(self) -> list:
        """Fetch all projects from Redmine — lightweight list for dropdowns."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/projects.json?limit=100",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json().get("projects", [])

    async def get_project_members(self, project_id: int) -> list:
        """Fetch all members of a project with their roles."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/projects/{project_id}/memberships.json?include=user,roles",
                headers=self.headers
            )
            response.raise_for_status()
            memberships = response.json().get("memberships", [])
            result = []
            for m in memberships:
                user = m.get("user", {})
                roles = [r["name"] for r in m.get("roles", [])]
                result.append({
                    "user_id": user.get("id"),
                    "name": user.get("name", ""),
                    "email": user.get("mail", ""),
                    "roles": roles,
                })
            return result

    async def get_projects_for_user(self, user_id: int) -> List[ProjectResponse]:
        """
        Fetch projects for a specific user. 
        Strictly filters by the user's memberships to avoid showing all public projects 
        that the user might have access to but isn't explicitly assigned to.
        """
        async with httpx.AsyncClient() as client:
            # 1. Fetch user memberships to get the definitive list of assigned projects
            try:
                user_resp = await client.get(f"{self.url}/users/{user_id}.json?include=memberships", headers=self.headers)
                user_resp.raise_for_status()
                user_data = user_resp.json().get("user", {})
                memberships = user_data.get("memberships", [])
                assigned_project_ids = {m["project"]["id"] for m in memberships}
            except Exception as e:
                logger.error(f"Failed to fetch memberships for user {user_id}: {e}")
                return []

            if not assigned_project_ids:
                return []

            # 2. Fetch projects (filtering by user_id still helps reduce initial set and ensures visibility)
            # We use a high limit to ensure we get all projects the user might be in.
            response = await client.get(
                f"{self.url}/projects.json?user_id={user_id}&include=custom_fields&limit=100", 
                headers=self.headers
            )
            response.raise_for_status()
            projects_data = response.json().get("projects", [])

            projects = []
            for p in projects_data:
                # 3. STRICT FILTER: Only include if the user has an explicit membership
                if p["id"] in assigned_project_ids:
                    custom_values = {cf["name"]: cf.get("value") for cf in p.get("custom_fields", [])}
                    projects.append(ProjectResponse(
                        id=p["id"],
                        name=p["name"],
                        identifier=p["identifier"],
                        city=custom_values.get("City", ""),
                        customerName=p["name"], # mapping name to customerName for now
                        customerOfficeLocation=custom_values.get("Customer Office Location", ""),
                        projectType=custom_values.get("Project Type", ""),
                        status="active" if p.get("status") == 1 else "closed" if p.get("status") == 5 else "archived"
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
                    assigned_to_name=i.get("assigned_to", {}).get("name") if i.get("assigned_to") else None,
                    created_on=i["created_on"],
                    updated_on=i["updated_on"]
                ))
            return issues

    async def get_issues_for_project(self, project_id: int, assigned_to_id: int = None) -> List[IssueResponse]:
        async with httpx.AsyncClient() as client:
            url = f"{self.url}/issues.json?project_id={project_id}"
            if assigned_to_id is not None:
                url += f"&assigned_to_id={assigned_to_id}"
            response = await client.get(url, headers=self.headers)
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
                    assigned_to_name=i.get("assigned_to", {}).get("name") if i.get("assigned_to") else None,
                    created_on=i["created_on"],
                    updated_on=i["updated_on"]
                ))
            return issues

    async def get_issue_by_id(self, issue_id: int) -> Optional[dict]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.url}/issues/{issue_id}.json", headers=self.headers)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json().get("issue")
            except Exception as e:
                logger.error(f"Error fetching issue {issue_id} from Redmine: {e}")
                return None

    async def search_projects(self, query: str, limit: int = 5) -> list:
        all_projects = await self.get_all_projects()
        q = query.lower()
        matched = [p for p in all_projects if q in p.get("name", "").lower()]
        matched = matched[:limit]

        result = []
        for p in matched:
            cf = {c["name"]: c.get("value", "") for c in p.get("custom_fields", []) if c.get("name") in ("Project Type",)}
            members = await self.get_project_members(p["id"])
            result.append({
                "id": str(p["id"]),
                "name": p["name"],
                "identifier": p.get("identifier", ""),
                "type": cf.get("Project Type", ""),
                "status": "active" if p.get("status") == 1 else "closed",
                "memberCount": len(members),
            })
        return result

    async def search_people(self, project_ids: list, limit: int = 5) -> list:
        seen = set()
        result = []
        all_projects = await self.get_all_projects()
        project_map = {p["id"]: p["name"] for p in all_projects}

        for pid in project_ids:
            members = await self.get_project_members(pid)
            project_name = project_map.get(pid, "")
            for m in members:
                if m["user_id"] in seen:
                    continue
                seen.add(m["user_id"])
                result.append({
                    "id": str(m["user_id"]),
                    "name": m["name"],
                    "role": ", ".join(m["roles"]),
                    "projectName": project_name,
                })
                if len(result) >= limit:
                    return result
        return result

    async def add_user_to_project(self, user_id: int, project_id: int, role_id: int = None) -> bool:
        """Add a user to a Redmine project. Defaults to Developer role if not specified."""
        if role_id is None:
            role_id = await self._get_developer_role_id()
        if not role_id:
            raise Exception("Role not found in Redmine")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/projects/{project_id}/memberships.json",
                json={"membership": {"user_id": user_id, "role_ids": [role_id]}},
                headers=self.headers,
            )
        return resp.status_code in (201, 200)

    async def get_roles(self) -> list:
        """Fetch all Redmine roles."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.url}/roles.json", headers=self.headers)
            resp.raise_for_status()
            return resp.json().get("roles", [])

    async def update_user(self, user_id: int, data: dict) -> bool:
        """Update a Redmine user's profile fields."""
        payload: dict = {"user": {}}
        if "login" in data:
            payload["user"]["login"] = data["login"]
        if "firstname" in data:
            payload["user"]["firstname"] = data["firstname"]
        if "lastname" in data:
            payload["user"]["lastname"] = data["lastname"]
        if "mail" in data:
            payload["user"]["mail"] = data["mail"]

        if not payload["user"]:
            return True

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.url}/users/{user_id}.json",
                json=payload,
                headers=self.headers,
            )
        return resp.status_code in (200, 204)

    async def remove_user_from_project(self, user_id: int, project_id: int) -> bool:
        """Remove a user from a Redmine project."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}/projects/{project_id}/memberships.json?include=user",
                headers=self.headers,
            )
            resp.raise_for_status()
            memberships = resp.json().get("memberships", [])
            membership_id = next(
                (m["id"] for m in memberships if m.get("user", {}).get("id") == user_id),
                None,
            )
            if not membership_id:
                return False

            resp = await client.delete(
                f"{self.url}/memberships/{membership_id}.json",
                headers=self.headers,
            )
        return resp.status_code in (200, 204)

    async def _get_developer_role_id(self) -> int:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.url}/roles.json", headers=self.headers)
            resp.raise_for_status()
            roles = resp.json().get("roles", [])
            for r in roles:
                if r.get("name", "").lower() == "developer":
                    return r["id"]
        return 0


redmine_service = RedmineService()

