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
from pydantic import BaseModel, EmailStr, Field, field_validator
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
    password: str = Field(...)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("Username can only contain letters, numbers, and underscores")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class SignupResponse(BaseModel):
    message: str
    user_id: str
