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
4. Backend creates a session in the database with a session ID
5. Backend sets an HTTP-only cookie with the session ID
6. Browser only gets the session cookie, not the actual JWTs
"""
import logging
import secrets
import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException, Depends, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, SessionLocal
from app.core.limiter import limiter, LOGIN_RATE_LIMIT
from app.features.auth.services.session import create_session, get_session, delete_session, enforce_session_limit, delete_sessions_by_sub
from app.features.auth.services.keycloak import refresh_keycloak_token, revoke_keycloak_token, create_keycloak_user, set_keycloak_password, get_realm_roles, add_realm_role_to_user, get_keycloak_user_by_email
from app.features.auth.dependencies import get_current_user, require_admin
from app.models.employee_master import EmployeeMaster
from app.models.password_reset import PasswordResetToken
from app.services.database.base_service import BaseService
from app.utils.jwt import get_user_info_from_token
from app.utils.audit import log_login, log_logout, log_session_refresh, log_backchannel_logout, log_security_event
from app.utils.email import send_password_reset_email, send_password_changed_email
from app.middleware.logging import _get_client_ip
from app.features.auth.schemas import LoginRequest, SignupRequest, SignupResponse, ForgotPasswordRequest, ResetPasswordRequest
from app.features.redmine.service import redmine_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Login endpoint - authenticates user with Keycloak and creates a server-side session.
    
    How it works:
    1. Receives username and password from the request body
    2. Sends these to Keycloak's token endpoint (with client credentials)
    3. If valid, Keycloak returns access_token, refresh_token, etc.
    4. Extracts user info from the access token
    5. Stores the refresh token in a new session ID
    6. Sets an HTTP-only cookie with the session ID (browser never sees the JWTs)
    """
    import httpx

    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)

    # Send credentials to Keycloak to verify
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                "username": payload.username,
                "password": payload.password,
                "scope": "openid profile email offline_access",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # If Keycloak says no, log the failure and return 401
    if response.status_code != 200:
        logger.warning(f"Login failed for user: {payload.username}")
        log_login(correlation_id, payload.username, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Extract tokens from Keycloak's response
    tokens = response.json()
    # Decode the JWT to get user info (username, email, roles)
    user_data = get_user_info_from_token(tokens.get("access_token"))

    # Make sure we got a valid user ID from the token
    if not user_data.get("sub"):
        log_login(correlation_id, payload.username, success=False, client_ip=client_ip, extra={"reason": "missing_sub"})
        raise HTTPException(status_code=401, detail="Failed to extract user info")

    # Block deactivated employees (admins bypass)
    email = user_data.get("email")
    roles = user_data.get("roles", [])
    is_active = True
    if email and "Admin" not in roles:
        svc = BaseService[EmployeeMaster](db)
        emp = svc.fetch_one(EmployeeMaster, user_email=email)
        if emp and not emp.is_active:
            log_login(correlation_id, payload.username, success=False, client_ip=client_ip, extra={"reason": "account_deactivated"})
            raise HTTPException(status_code=403, detail="Account is deactivated. Contact admin.")
        is_active = emp.is_active if emp else True

    # Enforce single session per user
    session_id = await create_session(user_data, tokens.get("refresh_token"), is_active=is_active)
    await enforce_session_limit(user_data.get("sub"), max_sessions=1)

    log_login(correlation_id, payload.username, success=True, client_ip=client_ip, extra={"user_sub": user_data.get("sub")})

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
@limiter.limit("20/minute")
async def refresh_session_endpoint(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Refresh an existing session using the Keycloak refresh token.
    
    How it works:
    1. Gets the session ID from the cookie
    2. Looks up the session to get the Keycloak refresh token
    3. Sends the refresh token to Keycloak to get new tokens
    4. Creates a new session with the new refresh token
    5. Deletes the old session
    6. Sets a new session cookie
    """
    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)

    # Get the session cookie from the request
    old_session_id = request.cookies.get("session_id")
    if not old_session_id:
        log_session_refresh(correlation_id, success=False, client_ip=client_ip)
        raise HTTPException(status_code=401, detail="No session")

    # Look up the session in the database
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
        "is_active": session_data.get("is_active", True),
    }

    # Create new session with new refresh token, delete old session
    new_session_id = await create_session(user_data, new_tokens.get("refresh_token"), is_active=session_data.get("is_active", True))
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
@limiter.limit("10/minute")
async def logout(request: Request):
    """
    Logout endpoint - ends the user's session.
    
    How it works:
    1. Gets the session ID from the cookie
    2. Looks up the session to get the Keycloak refresh token
    3. Tells Keycloak to revoke the refresh token
    4. Deletes the session from the database
    5. Clears the session cookie
    """
    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)
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

        # Remove the session from the database
        await delete_session(session_id)

    log_logout(correlation_id, username=username, success=True, client_ip=client_ip)

    # Clear the session cookie in the browser
    response = JSONResponse({"message": "Logout successful"})
    response.delete_cookie("session_id", path="/")
    return response


@router.post("/backchannel-logout")
@limiter.limit("30/minute")
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
    4. Find and delete all sessions for that user in memory
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

        # Find and delete all in-memory sessions that belong to this user
        deleted = await delete_sessions_by_sub(sub)

        log_backchannel_logout(correlation_id, user_sub=sub, sessions_deleted=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backchannel logout error: {e}")
        raise HTTPException(status_code=400, detail="Invalid logout token")

    # Return 204 No Content as per spec
    return JSONResponse(status_code=204, content=None)


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    """
    Forgot Password — sends a password reset link to the user's email.
    Always returns success to prevent email enumeration.
    """
    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)

    try:
        kc_user = await get_keycloak_user_by_email(payload.email)
        if not kc_user:
            log_security_event(
                correlation_id, "forgot_password",
                f"Email not found: {payload.email}",
                severity="info",
                extra={"email": payload.email, "client_ip": client_ip},
            )
            return {"message": "If the email is registered, a reset link has been sent."}

        db = SessionLocal()
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            daily_count = db.query(PasswordResetToken).filter(
                PasswordResetToken.user_sub == kc_user["id"],
                PasswordResetToken.created_at >= today,
            ).count()

            if daily_count >= 5:
                log_security_event(
                    correlation_id, "forgot_password",
                    f"Daily limit reached for {payload.email}",
                    severity="warning",
                    extra={"email": payload.email, "client_ip": client_ip},
                )
                return {"message": "You've reached the maximum password reset requests for today. Please try again tomorrow."}
        finally:
            db.close()

        token_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expire_minutes = settings.RESET_TOKEN_EXPIRE_MINUTES
        claims = {
            "sub": kc_user["id"],
            "email": kc_user["email"],
            "jti": token_id,
            "purpose": "password_reset",
            "iat": now,
            "exp": now + timedelta(minutes=expire_minutes),
        }
        reset_token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        token_hash = hashlib.sha256(reset_token.encode()).hexdigest()

        db = SessionLocal()
        try:
            rt = PasswordResetToken(
                id=token_id,
                user_sub=kc_user["id"],
                token_hash=token_hash,
                expires_at=now + timedelta(minutes=expire_minutes),
            )
            db.add(rt)
            db.commit()
        finally:
            db.close()

        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"
        await send_password_reset_email(kc_user["email"], reset_link)

        log_security_event(
            correlation_id, "forgot_password",
            f"Reset email sent to {kc_user['email']}",
            severity="info",
            extra={"email": kc_user["email"], "client_ip": client_ip},
        )

    except Exception as e:
        logger.error(f"Forgot password error: {e}")

    return {"message": "If the email is registered, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, payload: ResetPasswordRequest):
    """
    Reset Password — validates the reset token from email and sets a new password
    in both Keycloak and Redmine. Invalidates all existing sessions.
    """
    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)

    try:
        claims = jwt.decode(
            payload.token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["sub", "email", "jti", "exp", "purpose"]},
        )
    except JWTError:
        log_security_event(
            correlation_id, "password_reset",
            "Invalid or expired reset token",
            severity="warning", extra={"client_ip": client_ip},
        )
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    if claims.get("purpose") != "password_reset":
        raise HTTPException(status_code=400, detail="Invalid reset link.")

    token_id = claims["jti"]
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    user_sub = claims["sub"]
    user_email = claims.get("email", "")

    db = SessionLocal()
    try:
        stored = db.query(PasswordResetToken).filter(
            PasswordResetToken.id == token_id,
            PasswordResetToken.token_hash == token_hash,
        ).first()

        if not stored or stored.used:
            raise HTTPException(status_code=400, detail="This reset link has already been used or is invalid.")

        if stored.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

        if stored.reset_attempts >= 5:
            await asyncio.sleep(5)
            raise HTTPException(status_code=400, detail="Too many failed attempts. Please request a new reset link.")

        success = await set_keycloak_password(user_sub, payload.password)
        if not success:
            stored.reset_attempts += 1
            db.commit()
            delay = min(2 ** stored.reset_attempts, 30)
            await asyncio.sleep(delay)
            log_security_event(
                correlation_id, "password_reset",
                f"Keycloak password update failed for {user_sub} (attempt {stored.reset_attempts})",
                severity="error", extra={"client_ip": client_ip},
            )
            raise HTTPException(status_code=500, detail="Failed to reset password. Please try again.")

        if user_email:
            try:
                rm_user = await redmine_service.get_user_by_email(user_email)
                if rm_user and rm_user.get("id"):
                    await redmine_service.update_user(rm_user["id"], {"password": payload.password})
                    logger.info(f"Redmine password synced for {user_email}")
                else:
                    logger.warning(f"Redmine user not found for {user_email} — creating sync user")
                    await redmine_service.create_user(
                        user_email.split("@")[0],
                        user_email,
                        password=payload.password,
                    )
            except Exception as e:
                logger.error(f"Redmine password sync failed for {user_email}: {e}")

        deleted = await delete_sessions_by_sub(user_sub)
        logger.info(f"Invalidated {deleted} sessions for user {user_sub} after password reset")

        stored.used = True
        db.commit()

        log_security_event(
            correlation_id, "password_reset",
            f"Password reset successful for {user_email}",
            severity="info",
            extra={"user_sub": user_sub, "email": user_email, "sessions_deleted": deleted, "client_ip": client_ip},
        )

        if user_email:
            await send_password_changed_email(user_email)

    finally:
        db.close()

    return {"message": "Password reset successful. You can now login with your new password."}


@router.post("/signup")
@limiter.limit("5/minute")
async def signup(
    request: Request,
    signup_data: SignupRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    # Enforce admin-only access — the dependency chain resolves
    # get_current_user first, then checks for the "admin" realm role.
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    """
    Signup endpoint — admin only. Creates a new user in Keycloak
    and syncs them to Redmine in the background.

    Requires a valid session with the "admin" realm role.
    """
    correlation_id = request.state.correlation_id
    client_ip = _get_client_ip(request)

    try:
        logger.info(f"Admin {current_user.get('username')} is creating user {signup_data.username}")
        user_id = await create_keycloak_user(signup_data.username, signup_data.email)
        await set_keycloak_password(user_id, signup_data.password)

        # Assign Keycloak realm role (defaults to "Technical Resource")
        await add_realm_role_to_user(user_id, signup_data.role)

        logger.info(f"User {signup_data.username} created via signup")
        log_security_event(
            correlation_id,
            "signup",
            f"User {signup_data.username} created",
            severity="info",
            extra={"username": signup_data.username, "client_ip": client_ip},
        )

        # Sync to Redmine in the background
        background_tasks.add_task(
            redmine_service.create_user,
            username=signup_data.username,
            email=signup_data.email,
            password=signup_data.password,
        )

        # Store timezone in our own employee_master table
        svc = BaseService[EmployeeMaster](db)
        emp = svc.fetch_one(EmployeeMaster, user_email=signup_data.email)
        if emp:
            svc.update(EmployeeMaster, emp.id, timezone=signup_data.timezone)

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


@router.get("/roles")
async def list_keycloak_roles(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_admin),
):
    """Return all Keycloak realm roles. Admin only."""
    return await get_realm_roles()
