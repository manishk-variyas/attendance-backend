"""
Audit logging for security events.

This module provides functions to log security-related events
for compliance and monitoring. All audit logs go to:
- logs/audit.log (JSON format, for log aggregation)
- Console (stdout, for Docker logging)

Audit events include:
- Login attempts (success/failure)
- Logout events
- Session refresh events
- Backchannel logout (SSO logout)
- Security events (warnings/errors)

Each audit log includes:
- Action type (login, logout, etc.)
- Username (when available)
- Client IP address
- Correlation ID (for tracing the request)
- Timestamp (added automatically by the logging system)
"""
import logging
from typing import Any, Optional

# Dedicated logger for audit events
audit_logger = logging.getLogger("app.audit")


def log_login(
    correlation_id: str,
    username: str,
    success: bool,
    client_ip: str = "-",
    extra: Optional[dict] = None,
) -> None:
    """
    Log a login attempt (success or failure).
    
    Args:
        correlation_id: Request correlation ID for tracing
        username: The username that attempted to login
        success: True if login succeeded, False if it failed
        client_ip: IP address of the client
        extra: Additional data to include in the log
    """
    status = "success" if success else "failed"
    audit_logger.info(
        f"User login attempt: {username} ({status})",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "action": "login",
                "username": username,
                "status": status,
                "client_ip": client_ip,
                **(extra or {}),
            },
        },
    )


def log_logout(
    correlation_id: str,
    username: str = "-",
    client_ip: str = "-",
    extra: Optional[dict] = None,
) -> None:
    """
    Log a logout event.
    
    Args:
        correlation_id: Request correlation ID for tracing
        username: The user that logged out
        client_ip: IP address of the client
        extra: Additional data to include in the log
    """
    audit_logger.info(
        f"User logout: {username}",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "action": "logout",
                "username": username,
                "client_ip": client_ip,
                **(extra or {}),
            },
        },
    )


def log_session_refresh(
    correlation_id: str,
    username: str = "-",
    success: bool = True,
    client_ip: str = "-",
    extra: Optional[dict] = None,
) -> None:
    """
    Log a session refresh event.
    
    Args:
        correlation_id: Request correlation ID for tracing
        username: The user whose session was refreshed
        success: True if refresh succeeded, False if it failed
        client_ip: IP address of the client
        extra: Additional data to include in the log
    """
    status = "success" if success else "failed"
    audit_logger.info(
        f"Session refresh: {username} ({status})",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "action": "session_refresh",
                "username": username,
                "status": status,
                "client_ip": client_ip,
                **(extra or {}),
            },
        },
    )


def log_backchannel_logout(
    correlation_id: str,
    user_sub: str,
    sessions_deleted: int = 0,
    extra: Optional[dict] = None,
) -> None:
    """
    Log a backchannel logout event (SSO logout from Keycloak).
    
    Args:
        correlation_id: Request correlation ID for tracing
        user_sub: Keycloak user ID (sub) that was logged out
        sessions_deleted: Number of sessions that were deleted
        extra: Additional data to include in the log
    """
    audit_logger.info(
        f"Backchannel logout: user {user_sub} ({sessions_deleted} session(s) deleted)",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "action": "backchannel_logout",
                "user_sub": user_sub,
                "sessions_deleted": sessions_deleted,
                "source": "keycloak",
                **(extra or {}),
            },
        },
    )


def log_security_event(
    correlation_id: str,
    event: str,
    detail: str,
    severity: str = "warning",
    extra: Optional[dict] = None,
) -> None:
    """
    Log a security-related event (warnings/errors).
    
    Args:
        correlation_id: Request correlation ID for tracing
        event: Short name of the event (e.g., "backchannel_logout")
        detail: Detailed description of what happened
        severity: "warning" or "error" (affects log level)
        extra: Additional data to include in the log
    """
    level = logging.WARNING if severity == "warning" else logging.ERROR
    audit_logger.log(
        level,
        f"Security event: {event} - {detail}",
        extra={
            "correlation_id": correlation_id,
            "extra_data": {
                "action": "security_event",
                "event": event,
                "detail": detail,
                "severity": severity,
                **(extra or {}),
            },
        },
    )
