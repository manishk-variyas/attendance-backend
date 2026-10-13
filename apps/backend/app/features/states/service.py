"""States master-data service. Admin-managed, read for everyone."""
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.state import State
from app.utils.sanitize import escape_like

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CODE_RE = re.compile(r"^[A-Z]{2,3}(-[A-Z0-9]{1,3})?$")
_GST_RE = re.compile(r"^[0-9]{2}$")


class StateService:
    def __init__(self, db: Session):
        self.db = db

    def list(
        self,
        country: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        active_only: bool = False,
    ) -> list[State]:
        q = self.db.query(State)
        if country and country.strip():
            q = q.filter(State.country_code == country.strip().upper())
        if active_only:
            q = q.filter(State.is_active.is_(True))
        elif is_active is not None:
            q = q.filter(State.is_active == is_active)
        if search and search.strip():
            term = search.strip()[:100]
            q = q.filter(or_(State.name.ilike(f"%{escape_like(term)}%", escape="\\"), State.code.ilike(f"%{escape_like(term)}%", escape="\\")))
        return q.order_by(State.name.asc()).all()

    def get(self, code: str) -> Optional[State]:
        return self.db.query(State).filter(State.code == (code or "").strip().upper()).first()

    def get_or_404(self, code: str) -> State:
        state = self.get(code)
        if not state:
            raise HTTPException(status_code=404, detail="State not found.")
        return state

    def create(self, data: dict) -> State:
        self._validate(data, partial=False)
        if self.get(data["code"]):
            raise HTTPException(status_code=409, detail=f"State code '{data['code']}' already exists.")
        state = State(**data)
        self.db.add(state)
        self.db.commit()
        self.db.refresh(state)
        return state

    def update(self, code: str, data: dict) -> State:
        state = self.get_or_404(code)
        if not data:
            raise HTTPException(status_code=400, detail="No fields to update.")
        self._validate(data, partial=True)
        if "code" in data and data["code"] != state.code and self.get(data["code"]):
            raise HTTPException(status_code=409, detail=f"State code '{data['code']}' already exists.")
        for field, value in data.items():
            setattr(state, field, value)
        state.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(state)
        return state

    def deactivate(self, code: str) -> None:
        """Soft delete — keep the row (history/references), mark inactive."""
        state = self.get_or_404(code)
        state.is_active = False
        state.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    @staticmethod
    def _validate(data: dict, partial: bool) -> None:
        if not partial or "country_code" in data:
            if not _COUNTRY_RE.match(data.get("country_code") or ""):
                raise HTTPException(status_code=400, detail="country_code must be a 2-letter ISO code (e.g. IN).")
        if not partial or "code" in data:
            if not _CODE_RE.match(data.get("code") or ""):
                raise HTTPException(status_code=400, detail="code must look like 'IN-UP' or 'UP'.")
        if not partial or "name" in data:
            if not (data.get("name") or "").strip():
                raise HTTPException(status_code=400, detail="name is required.")
        gst = data.get("gst_code")
        if gst is not None and not _GST_RE.match(gst):
            raise HTTPException(status_code=400, detail="gst_code must be 2 digits (e.g. 09).")


def state_exists(db: Session, code: Optional[str]) -> bool:
    """True if ``code`` is empty (nothing to check) or a valid ``states.code``."""
    if not code:
        return True
    return db.query(State.id).filter(State.code == code.strip().upper()).first() is not None


def state_name_map(db: Session, codes) -> dict:
    """Map a set of state codes to their display names: {code: name}."""
    codes = {c for c in codes if c}
    if not codes:
        return {}
    rows = db.query(State.code, State.name).filter(State.code.in_(codes)).all()
    return {r[0]: r[1] for r in rows}


