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
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from zoneinfo import available_timezones
from app.features.redmine.constants import REDMINE_TO_IANA_TZ

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
    username: str = Field(..., min_length=1, max_length=125)
    password: str = Field(..., min_length=1, max_length=125)
    realm: Optional[str] = Field(None, max_length=50)  # Optional Keycloak realm; None → default realm


class SessionResponse(BaseModel):
    """
    Response schema for login/refresh endpoints.
    
    Includes a message and the user data.
    The session cookie is set separately in the response headers.
    """
    message: str
    user: UserResponse


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    email: EmailStr = Field(..., min_length=8, max_length=50)
    password: str = Field(..., max_length=20)
    timezone: str = Field(default="UTC", description="IANA timezone name, e.g. 'Asia/Kolkata'")
    role: str = Field(default="Technical Resource", description="Keycloak realm role")
    realm: Optional[str] = None  # Optional Keycloak realm; None → default realm
    first_name: Optional[str] = Field(
        default="NA",
        max_length=50,
        validation_alias=AliasChoices("first_name", "firstName"),
        description="User's first name",
    )
    last_name: Optional[str] = Field(
        default="NA",
        max_length=50,
        validation_alias=AliasChoices("last_name", "lastName"),
        description="User's last name",
    )

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 20:
            raise ValueError("Password must be at most 20 characters")
        return v

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> str:
        if not v or not v.strip():
            return "NA"
        return v.strip()

    @field_validator("timezone")
    @classmethod
    def coerce_timezone(cls, v: str) -> str:
        return REDMINE_TO_IANA_TZ.get(v, v)


# class SignupRequest(BaseModel):
#     username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
#     email: EmailStr
#     password: str = Field(...)
#     timezone: str = Field(default="UTC", description="IANA timezone name, e.g. 'Asia/Kolkata'")

#     @field_validator("username")
#     @classmethod
#     def validate_username(cls, v: str) -> str:
#         if not v.replace("_", "").isalnum():
#             raise ValueError("Username can only contain letters, numbers, and underscores")
#         return v

#     @field_validator("password")
#     @classmethod
#     def validate_password(cls, v: str) -> str:
#         if len(v) < 8:
#             raise ValueError("Password must be at least 8 characters")
#         return v


class SignupResponse(BaseModel):
    message: str
    user_id: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    realm: Optional[str] = None  # Optional Keycloak realm; None → default realm


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=2000)
    password: str = Field(..., min_length=8, max_length=20)

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Token must not be empty")
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 20:
            raise ValueError("Password must be at most 20 characters")
        return v
