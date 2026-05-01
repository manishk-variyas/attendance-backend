"""
Pydantic schemas for authentication.

This module defines the request and response models for auth endpoints.
These schemas are used for:
- Validating incoming request data (request bodies, query params)
- Documenting the API (used by FastAPI to generate OpenAPI docs)
- Serializing response data

Schemas vs Models:
- Schemas (here): API input/output validation (Pydantic)
- Models (models/): Database tables (SQLAlchemy)
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List


class UserResponse(BaseModel):
    """
    Response schema for user data.
    
    Returned by:
    - /auth/login (with message)
    - /auth/refresh (with message)
    - /api/me (directly)
    """
    sub: str  # Keycloak user ID (unique identifier)
    username: str
    email: Optional[str] = None
    roles: List[str] = []


class LoginRequest(BaseModel):
    """Request schema for the login endpoint."""
    username: str
    password: str


class SessionResponse(BaseModel):
    """
    Response schema for login/refresh endpoints.
    
    Includes a message and the user data.
    The session cookie is set separately in the response headers.
    """
    message: str
    user: UserResponse


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=8)


class SignupResponse(BaseModel):
    message: str
    user_id: str
