"""Request/response schemas for the states master data (admin-managed)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_STATE_TYPES = {"state", "union_territory"}


def _clean_upper(value: Optional[str]) -> Optional[str]:
    return value.strip().upper() if isinstance(value, str) and value.strip() else value


class StateCreate(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2, description="ISO country code, e.g. IN")
    code: str = Field(..., min_length=2, max_length=10, description="ISO subdivision code, e.g. IN-UP")
    short_code: Optional[str] = Field(None, max_length=10, description="Short code, e.g. UP")
    gst_code: Optional[str] = Field(None, max_length=2, description="GST state code, e.g. 09")
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field("state", description="state or union_territory")
    is_active: bool = True

    _upper = field_validator("country_code", "code", "short_code", "gst_code")(_clean_upper)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = (v or "state").strip().lower()
        if v not in _STATE_TYPES:
            raise ValueError("type must be 'state' or 'union_territory'")
        return v


class StateUpdate(BaseModel):
    country_code: Optional[str] = Field(None, min_length=2, max_length=2)
    code: Optional[str] = Field(None, min_length=2, max_length=10)
    short_code: Optional[str] = Field(None, max_length=10)
    gst_code: Optional[str] = Field(None, max_length=2)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[str] = None
    is_active: Optional[bool] = None

    _upper = field_validator("country_code", "code", "short_code", "gst_code")(_clean_upper)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in _STATE_TYPES:
            raise ValueError("type must be 'state' or 'union_territory'")
        return v


class StateResponse(BaseModel):
    id: str
    country_code: str
    code: str
    short_code: Optional[str] = None
    gst_code: Optional[str] = None
    name: str
    type: str
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StateOption(BaseModel):
    """Light shape for dropdowns."""
    code: str
    name: str
    short_code: Optional[str] = None
