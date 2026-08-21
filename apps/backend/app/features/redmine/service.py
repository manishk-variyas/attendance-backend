import httpx
from typing import List, Optional
from fastapi import HTTPException
from app.core.config import settings
from .schemas import ProjectCreate, ProjectUpdate, ProjectResponse, UserWithProjects, IssueResponse
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

    async def create_user(self, username: str, email: str, password: Optional[str] = None, firstname: Optional[str] = None, lastname: str = ""):
        """Creates a user in Redmine. If already exists, returns existing user."""
        fname = firstname or username
        lname = lastname or "-"

        existing_user = await self.get_user_by_email(email)
        if existing_user:
            logger.info(f"User {email} already exists in Redmine. Sync skipped.")
            return existing_user

        async with httpx.AsyncClient() as client:
            payload = {
                "user": {
                    "login": username,
                    "mail": email,
                    "firstname": fname,
                    "lastname": lname,
                    "password": password or secrets.token_urlsafe(16),
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

    def _safe_identifier(self, customer_name: str, custom_identifier: Optional[str] = None) -> str:
        """Build a Redmine-safe identifier from customer name or custom identifier.

        Redmine identifiers must be 1..100 chars, start with a letter, and contain
        only lowercase letters, digits, dashes, and underscores.
        """
        import re
        raw = custom_identifier if custom_identifier and custom_identifier.strip() else customer_name
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        if not slug:
            slug = "project"
        if not slug[0].isalpha():
            slug = f"p-{slug}"
        return slug[:100]

    async def _change_project_status(self, client, project_id: int | str, status: str) -> None:
        """Redmine does not accept `status` on create/update payloads; it uses
        dedicated actions instead: close, reopen, archive, unarchive."""
        action = {"closed": "close", "archived": "archive"}.get(status.lower())
        if action:
            resp = await client.put(
                f"{self.url}/projects/{project_id}/{action}.json", headers=self.headers
            )
            resp.raise_for_status()
            return
        # "active": unarchive first (safe on all states), then reopen any closed project
        for action in ("unarchive", "reopen"):
            resp = await client.put(
                f"{self.url}/projects/{project_id}/{action}.json", headers=self.headers
            )
            resp.raise_for_status()

    async def create_project(self, data: ProjectCreate):
        # 1. Find the project owner (must already exist in Redmine)
        user = await self.get_user_by_email(data.email)
        if not user:
            raise HTTPException(status_code=404, detail=f"User with email {data.email} not found in Redmine")

        # 2. Resolve custom field IDs by name (skip any that don't exist)
        cfs = await self.get_custom_fields()
        cf_map = {cf["name"]: cf["id"] for cf in cfs}
        custom_fields = []
        for name, value in (
            ("City", data.city),
            ("Customer Office Location", data.customerOfficeLocation),
            ("Project Type", data.projectType),
        ):
            if value and name in cf_map:
                custom_fields.append({"id": cf_map[name], "value": value})

        # 3. Resolve the 'Manager' role by name (fallback: first role)
        roles = await self.get_roles()
        manager_role_id = next(
            (r["id"] for r in roles if r.get("name", "").lower() in ("manager", "project manager")),
            roles[0]["id"] if roles else None,
        )

        # 4. Safe unique identifier resolution
        base_identifier = self._safe_identifier(data.customerName, data.identifier)
        identifier = base_identifier

        async with httpx.AsyncClient() as client:
            # Pre-flight check: if a parent project was specified, ensure it exists
            if data.parent_id is not None:
                parent_resp = await client.get(
                    f"{self.url}/projects/{data.parent_id}.json", headers=self.headers
                )
                if parent_resp.status_code == 404:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Parent project '{data.parent_id}' not found in Redmine.",
                    )

            if not data.identifier:
                suffix = 1
                while suffix <= 10:
                    check_resp = await client.get(
                        f"{self.url}/projects/{identifier}.json", headers=self.headers
                    )
                    if check_resp.status_code == 404:
                        break
                    identifier = f"{base_identifier[:90]}-{suffix}"
                    suffix += 1
            else:
                check_resp = await client.get(
                    f"{self.url}/projects/{identifier}.json", headers=self.headers
                )
                if check_resp.status_code == 200:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Project identifier '{identifier}' already exists.",
                    )

            project_payload = {
                "name": data.customerName,
                "identifier": identifier,
                "description": data.description or "",
                "is_public": False,
                "custom_fields": custom_fields,
            }
            if data.parent_id is not None:
                project_payload["parent_id"] = data.parent_id
                project_payload["inherit_members"] = data.inherit_members
            payload = {"project": project_payload}

            resp = await client.post(f"{self.url}/projects.json", json=payload, headers=self.headers)
            if resp.status_code == 422:
                try:
                    err_json = resp.json()
                    errors = err_json.get("errors", [])
                except Exception:
                    errors = []
                errors_str = " ".join(str(e) for e in errors)
                if "identifier" in errors_str.lower():
                    raise HTTPException(status_code=409, detail=f"Project creation failed: {errors_str}")
                raise HTTPException(status_code=400, detail=f"Project creation failed: {errors_str or resp.text}")
            resp.raise_for_status()

            p_data = resp.json().get("project", {})
            p_id = p_data.get("id")

            # 6. Apply non-active status via dedicated Redmine action (status is ignored on create payload)
            if p_id and data.status and data.status.lower() != "active":
                await self._change_project_status(client, p_id, data.status)
                get_resp = await client.get(f"{self.url}/projects/{p_id}.json", headers=self.headers)
                if get_resp.status_code == 200:
                    p_data = get_resp.json().get("project", {})

            # 7. Add the owner as a member
            if p_id and manager_role_id:
                membership_payload = {
                    "membership": {"user_id": user["id"], "role_ids": [manager_role_id]}
                }
                member_resp = await client.post(
                    f"{self.url}/projects/{p_id}/memberships.json",
                    json=membership_payload,
                    headers=self.headers,
                )
                if member_resp.status_code not in (200, 201, 422):
                    logger.warning(
                        f"Failed to add member {data.email} to project {p_id}: "
                        f"{member_resp.status_code} - {member_resp.text}"
                    )

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

    def _build_issue_response(self, i: dict) -> IssueResponse:
        assigned = i.get("assigned_to")
        return IssueResponse(
            id=i["id"],
            subject=i["subject"],
            description=i.get("description"),
            status=i["status"]["name"],
            priority=i["priority"]["name"],
            tracker=i["tracker"]["name"],
            project_id=i["project"]["id"],
            project_name=i["project"]["name"],
            assigned_to_name=assigned["name"] if assigned else None,
            assigned_to_id=assigned["id"] if assigned else None,
            created_on=i["created_on"],
            updated_on=i["updated_on"],
            start_date=i.get("start_date"),
            due_date=i.get("due_date"),
            estimated_hours=i.get("estimated_hours"),
            done_ratio=i.get("done_ratio", 0),
            is_private=i.get("is_private", False),
            status_id=i["status"]["id"],
            priority_id=i["priority"]["id"],
            tracker_id=i["tracker"]["id"],
            author_id=i["author"]["id"],
            author_name=i["author"]["name"],
        )

    async def get_issues_for_user(self, user_id: int) -> List[IssueResponse]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/issues.json?assigned_to_id={user_id}&include=attachments,custom_fields",
                headers=self.headers,
            )
            response.raise_for_status()
            issues_data = response.json().get("issues", [])
            return [self._build_issue_response(i) for i in issues_data]

    async def get_issues_for_project(self, project_id: int, assigned_to_id: int = None) -> List[IssueResponse]:
        async with httpx.AsyncClient() as client:
            url = f"{self.url}/issues.json?project_id={project_id}&include=attachments,custom_fields"
            if assigned_to_id is not None:
                url += f"&assigned_to_id={assigned_to_id}"
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            issues_data = response.json().get("issues", [])
            return [self._build_issue_response(i) for i in issues_data]

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
        if "password" in data:
            payload["user"]["password"] = data["password"]

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

    async def upload_file(self, file_bytes: bytes, filename: str, content_type: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/uploads.json",
                content=file_bytes,
                headers={
                    "X-Redmine-API-Key": settings.REDMINE_API_KEY,
                    "Content-Type": "application/octet-stream",
                },
            )
            resp.raise_for_status()
            return resp.json().get("upload")

    async def create_issue(self, issue_attrs: dict, switch_user: str = None) -> dict:
        headers = {**self.headers}
        if switch_user:
            headers["X-Redmine-Switch-User"] = switch_user
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/issues.json",
                json={"issue": issue_attrs},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json().get("issue")

    async def update_issue(self, issue_id: int, issue_attrs: dict, switch_user: str = None) -> dict:
        headers = {**self.headers}
        if switch_user:
            headers["X-Redmine-Switch-User"] = switch_user
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.url}/issues/{issue_id}.json",
                json={"issue": issue_attrs},
                headers=headers,
            )
            resp.raise_for_status()
            # Redmine returns 204 No Content on success, re-fetch the issue
            issue = await self.get_issue_by_id(issue_id)
            if not issue:
                return {"id": issue_id}
            return issue


    async def delete_issue(self, issue_id: int) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/issues/{issue_id}.json",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return True


    async def delete_attachment(self, attachment_id: int) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/attachments/{attachment_id}.json",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return True

    async def update_project(self, project_id: int | str, data: ProjectUpdate):
        update_data = data.model_dump(exclude_unset=True)
        project_payload = {}

        if "customerName" in update_data and update_data["customerName"]:
            project_payload["name"] = update_data["customerName"]
        if "description" in update_data and update_data["description"] is not None:
            project_payload["description"] = update_data["description"]

        cfs = await self.get_custom_fields()
        cf_map = {cf["name"]: cf["id"] for cf in cfs}
        custom_fields = []
        for cf_key, cf_name in (
            ("city", "City"),
            ("customerOfficeLocation", "Customer Office Location"),
            ("projectType", "Project Type"),
        ):
            if cf_key in update_data and update_data[cf_key] is not None and cf_name in cf_map:
                custom_fields.append({"id": cf_map[cf_name], "value": update_data[cf_key]})

        if custom_fields:
            project_payload["custom_fields"] = custom_fields

        async with httpx.AsyncClient() as client:
            if project_payload:
                resp = await client.put(
                    f"{self.url}/projects/{project_id}.json",
                    json={"project": project_payload},
                    headers=self.headers,
                )
                if resp.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in Redmine.")
                elif resp.status_code == 422:
                    error_msg = "Project update failed."
                    try:
                        err_json = resp.json()
                        if "errors" in err_json:
                            error_msg = f"Project update failed: {', '.join(err_json['errors'])}"
                    except Exception:
                        pass
                    raise HTTPException(status_code=400, detail=error_msg)
                resp.raise_for_status()

            if "status" in update_data and update_data["status"]:
                await self._change_project_status(client, project_id, update_data["status"])

            if "email" in update_data and update_data["email"]:
                owner_email = update_data["email"]
                user = await self.get_user_by_email(owner_email)
                if not user:
                    raise HTTPException(status_code=404, detail=f"User with email {owner_email} not found in Redmine")

                roles = await self.get_roles()
                manager_role_id = next(
                    (r["id"] for r in roles if r.get("name", "").lower() in ("manager", "project manager")),
                    roles[0]["id"] if roles else None,
                )
                if manager_role_id:
                    membership_payload = {
                        "membership": {"user_id": user["id"], "role_ids": [manager_role_id]}
                    }
                    await client.post(
                        f"{self.url}/projects/{project_id}/memberships.json",
                        json=membership_payload,
                        headers=self.headers,
                    )

            get_resp = await client.get(f"{self.url}/projects/{project_id}.json", headers=self.headers)
            if get_resp.status_code == 200:
                return get_resp.json().get("project", {})
            return {"id": project_id, **project_payload}

    async def delete_project(self, project_id: int | str) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/projects/{project_id}.json",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return True


redmine_service = RedmineService()

