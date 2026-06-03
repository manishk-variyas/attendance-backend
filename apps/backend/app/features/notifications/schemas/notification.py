from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class NotificationBase(BaseModel):
    user_id: str
    type: str
    message: str
    from_user: str
    reference_id: Optional[str] = None
    is_read: bool = False

class NotificationCreate(BaseModel):
    user_id: str
    type: str
    message: str
    from_user: str
    reference_id: Optional[str] = None

class NotificationResponse(NotificationBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True

class UnreadCountResponse(BaseModel):
    count: int
