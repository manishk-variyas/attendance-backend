from fastapi import APIRouter, Depends, Request, HTTPException, status, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from app.features.auth.dependencies import get_current_user
from app.features.auth.routes import login as auth_login_logic
import httpx
from app.core.config import settings

router = APIRouter(prefix="/admin-dashboard", tags=["admin"])

# Setup templates
templates = Jinja2Templates(directory="app/features/admin/templates")

async def get_optional_user(request: Request):
    """Silent check for user to avoid auto-raising 401."""
    try:
        return await get_current_user(request)
    except:
        return None

async def require_admin_ui(request: Request, user: dict = Depends(get_optional_user)):
    """Security dependency that REDIRECTS to login instead of erroring."""
    if not user:
        return None # Controller will handle redirect
    if "admin" not in user.get("roles", []):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="login.html", 
        context={"error": None}
    )

@router.post("/login")
async def admin_login_handler(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle the admin login form and set the session cookie."""
    try:
        from app.features.auth.routes import login as perform_login
        response = await perform_login(request, username, password)
        
        if response.status_code == 200:
            import json
            user_data = json.loads(response.body.decode()).get("user", {})
            if "admin" not in user_data.get("roles", []):
                return templates.TemplateResponse(
                    request=request,
                    name="login.html", 
                    context={"error": "Access denied. Admin role required."}
                )
            
            redirect = RedirectResponse(url="/admin-dashboard/", status_code=303)
            for cookie in response.headers.getlist("set-cookie"):
                redirect.headers.append("set-cookie", cookie)
            return redirect
            
    except Exception as e:
        return templates.TemplateResponse(
            request=request,
            name="login.html", 
            context={"error": "Invalid username or password"}
        )

@router.get("/my-roles")
async def check_my_roles(user: dict = Depends(get_current_user)):
    """Debug endpoint to see what roles the current user has."""
    return {
        "username": user.get("username"),
        "roles": user.get("roles"),
        "is_admin": "admin" in user.get("roles", [])
    }

@router.get("/", response_class=HTMLResponse)
async def roster_dashboard(request: Request, user: dict = Depends(require_admin_ui)):
    if not user:
        return RedirectResponse(url="/admin-dashboard/login")
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html", 
        context={"user": user}
    )

@router.get("/roster", response_class=HTMLResponse)
async def roster_page(request: Request, user: dict = Depends(require_admin_ui)):
    if not user:
        return RedirectResponse(url="/admin-dashboard/login")
    return templates.TemplateResponse(
        request=request,
        name="roster.html",
        context={"user": user}
    )
