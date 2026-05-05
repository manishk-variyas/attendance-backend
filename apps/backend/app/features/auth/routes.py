"""
Authentication routes for the backend API.

This module handles all auth-related endpoints:
- POST /auth/login - Login with username/password, creates session
- POST /auth/refresh - Refresh an existing session using refresh token
- POST/GET /auth/logout - Logout user, revoke tokens, delete session
- POST /auth/backchannel-logout - Keycloak-triggered logout (SSO logout)

How it works:
1. User sends credentials to /auth/login
2. Backend forwards to Keycloak to verify credentials
3. On success, Keycloak returns JWT tokens
4. Backend stores tokens in Redis with a session ID
5. Backend sets an HTTP-only cookie with the session ID
6. Browser only gets the session cookie, not the actual JWTs
"""
import json
import logging

from fastapi import APIRouter, Request, HTTPException, Depends, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.limiter import limiter, LOGIN_RATE_LIMIT
from app.features.auth.services.session import create_session, get_session, delete_session, get_redis
from app.features.auth.services.keycloak import refresh_keycloak_token, revoke_keycloak_token, create_keycloak_user, set_keycloak_password
from app.features.auth.dependencies import get_current_user
from app.utils.jwt import get_user_info_from_token
from app.utils.audit import log_login, log_logout, log_session_refresh, log_backchannel_logout, log_security_event
from app.features.auth.schemas import SignupRequest, SignupResponse
from app.features.redmine.service import redmine_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(request: Request, username: str, password: str):
    """
    Login endpoint - authenticates user with Keycloak and creates a server-side session.
    
    How it works:
    1. Receives username and password from the request
    2. Sends these to Keycloak's token endpoint (with client credentials)
    3. If valid, Keycloak returns access_token, refresh_token, etc.
    4. Extracts user info from the access token
    5. Stores the refresh token in Redis under a new session ID
    6. Sets an HTTP-only cookie with the session ID (browser never sees the JWTs)
    """
    import httpx

    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"

    # Send credentials to Keycloak to verify
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                "username": username,
                "password": password,
                "scope": "openid profile email offline_access",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # If Keycloak says no, log the failure and return 401
    if response.status_code != 200:
        logger.warning(f"Login failed for user: {username}")
        log_login(correlation_id, username, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Extract tokens from Keycloak's response
    tokens = response.json()
    # Decode the JWT to get user info (username, email, roles)
    user_data = get_user_info_from_token(tokens.get("access_token"))

    # Make sure we got a valid user ID from the token
    if not user_data.get("sub"):
        log_login(correlation_id, username, success=False, client_ip=client_ip, extra={"reason": "missing_sub"})
        raise HTTPException(status_code=401, detail="Failed to extract user info")

    # Create a new session in Redis with user data and refresh token
    session_id = await create_session(user_data, tokens.get("refresh_token"))

    log_login(correlation_id, username, success=True, client_ip=client_ip, extra={"user_sub": user_data.get("sub")})

    # Send response with session cookie
    response = JSONResponse({"message": "Login successful", "user": user_data})
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,  # Browser can't access this cookie via JavaScript (prevents XSS)
        secure=settings.COOKIE_SECURE,  # Only send over HTTPS in production
        samesite="lax",  # Protects against CSRF attacks
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@router.post("/refresh")
async def refresh_session_endpoint(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Refresh an existing session using the Keycloak refresh token.
    
    How it works:
    1. Gets the session ID from the cookie
    2. Looks up the session in Redis to get the Keycloak refresh token
    3. Sends the refresh token to Keycloak to get new tokens
    4. Creates a new session with the new refresh token
    5. Deletes the old session
    6. Sets a new session cookie
    """
    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"

    # Get the session cookie from the request
    old_session_id = request.cookies.get("session_id")
    if not old_session_id:
        log_session_refresh(correlation_id, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="No session")

    # Look up the session in Redis
    session_data = await get_session(old_session_id)
    if not session_data:
        log_session_refresh(correlation_id, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Session not found")

    # Get the Keycloak refresh token from the session
    refresh_token = session_data.get("kc_refresh_token")
    if not refresh_token:
        log_session_refresh(correlation_id, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="No refresh token available")

    try:
        # Ask Keycloak for new tokens using the refresh token
        new_tokens = await refresh_keycloak_token(refresh_token)
    except HTTPException:
        # If refresh fails, the refresh token is expired - delete session and ask user to login again
        await delete_session(old_session_id)
        log_session_refresh(correlation_id, username=current_user.get("username"), success=False, client_ip=client_ip)
        raise HTTPException(
            status_code=401, detail="Session expired, please login again"
        )

    # Keep the same user data, just update the refresh token
    user_data = {
        "sub": session_data.get("sub"),
        "username": session_data.get("username"),
        "email": session_data.get("email"),
        "roles": session_data.get("roles", []),
    }

    # Create new session with new refresh token, delete old session
    new_session_id = await create_session(user_data, new_tokens.get("refresh_token"))
    await delete_session(old_session_id)

    log_session_refresh(correlation_id, username=user_data.get("username"), success=True, client_ip=client_ip)

    # Set the new session cookie
    response = JSONResponse({"message": "Session refreshed", "user": user_data})
    response.set_cookie(
        key="session_id",
        value=new_session_id,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    """
    Logout endpoint - ends the user's session.
    
    How it works:
    1. Gets the session ID from the cookie
    2. Looks up the session to get the Keycloak refresh token
    3. Tells Keycloak to revoke the refresh token
    4. Deletes the session from Redis
    5. Clears the session cookie
    """
    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"
    username = "-"

    session_id = request.cookies.get("session_id")

    if session_id:
        session_data = await get_session(session_id)

        if session_data:
            username = session_data.get("username", "-")
            refresh_token = session_data.get("kc_refresh_token")
            # Tell Keycloak to revoke the refresh token so it can't be used again
            if refresh_token:
                await revoke_keycloak_token(refresh_token)

        # Remove the session from Redis
        await delete_session(session_id)

    log_logout(correlation_id, username=username, client_ip=client_ip)

    # Clear the session cookie in the browser
    response = JSONResponse({"message": "Logout successful"})
    response.delete_cookie("session_id", path="/")
    return response


@router.post("/backchannel-logout")
async def backchannel_logout(request: Request, logout_token: str = Form(...)):
    """
    Backchannel logout endpoint for Keycloak SSO logout.
    
    This is called by Keycloak (not the browser) when a user logs out from
    any connected app. It uses the 'backchannel' approach where Keycloak
    directly tells this backend to logout the user.
    
    How it works:
    1. Keycloak sends a logout token (a special JWT)
    2. We verify it has the backchannel logout event
    3. Extract the user ID (sub) from the token
    4. Find and delete all sessions for that user in Redis
    """
    from jose import jwt

    correlation_id = request.state.correlation_id

    try:
        # Decode the logout token to get the user info (without verifying signature)
        payload = jwt.get_unverified_claims(logout_token)
        events = payload.get("events", {})
        # Check that this is actually a backchannel logout token
        if "http://schemas.openid.net/event/backchannel-logout" not in events:
            log_security_event(correlation_id, "backchannel_logout", "Missing logout event claim", severity="warning")
            raise HTTPException(status_code=400, detail="Invalid logout token")

        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=400, detail="Missing sub claim in logout token")

        # Find all sessions in Redis that belong to this user
        r = await get_redis()
        keys = await r.keys("session:*")

        deleted = 0
        for key in keys:
            session_data = await r.get(key)
            if session_data:
                data = json.loads(session_data)
                if data.get("sub") == sub:
                    await r.delete(key)
                    deleted += 1

        log_backchannel_logout(correlation_id, user_sub=sub, sessions_deleted=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backchannel logout error: {e}")
        raise HTTPException(status_code=400, detail="Invalid logout token")

    # Return 204 No Content as per spec
    return JSONResponse(status_code=204, content=None)


@router.post("/signup")
@limiter.limit("5/minute")
async def signup(request: Request, signup_data: SignupRequest, background_tasks: BackgroundTasks):
    """
    Signup endpoint - create a new user in Keycloak.
    Syncs to Redmine in the background to keep the response fast.
    """
    correlation_id = request.state.correlation_id
    client_ip = request.client.host if request.client else "-"

    try:
        user_id = await create_keycloak_user(signup_data.username, signup_data.email)
        await set_keycloak_password(user_id, signup_data.password)

        logger.info(f"User {signup_data.username} created via signup")
        log_security_event(
            correlation_id,
            "signup",
            f"User {signup_data.username} created",
            severity="info",
            extra={"username": signup_data.username, "client_ip": client_ip},
        )

        # BEST PRACTICE: Sync to Redmine in the background
        # This keeps the signup request fast and responsive
        background_tasks.add_task(
            redmine_service.create_user, 
            signup_data.username, 
            signup_data.email
        )

        return SignupResponse(
            message="User created successfully",
            user_id=user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {e}")
        log_security_event(
            correlation_id,
            "signup",
            f"Signup failed: {str(e)}",
            severity="error",
        )
        raise HTTPException(status_code=500, detail="Failed to create user")
