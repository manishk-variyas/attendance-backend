from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func
from app.services.database.base_service import BaseService
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from datetime import datetime, timezone, date


class ShiftService(BaseService[Shift]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch_by_user_id(self, redmine_user_id: int) -> List[Shift]:
        stmt = select(Shift).where(Shift.redmine_user_id == redmine_user_id).order_by(Shift.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def fetch_by_email(self, email: str) -> List[Shift]:
        stmt = select(Shift).where(Shift.user_email == email).order_by(Shift.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def fetch_current(self, redmine_user_id: int) -> Optional[Shift]:
        stmt = select(Shift).where(Shift.redmine_user_id == redmine_user_id).order_by(Shift.created_at.desc()).limit(1)
        return self.db.execute(stmt).scalars().first()

    def fetch_active(self, redmine_user_id: int) -> Optional[Shift]:
        now_dt = datetime.now(timezone.utc)
        stmt = select(Shift).where(
            Shift.redmine_user_id == redmine_user_id,
            or_(
                Shift.work_location_status == "OFFICE",
                Shift.shift_end_utc > now_dt,
            ),
        ).order_by(Shift.created_at.desc()).limit(1)
        return self.db.execute(stmt).scalars().first()

    def get_stats(self) -> dict:
        total = self.db.execute(select(func.count(Shift.id))).scalar() or 0
        active = self.db.execute(select(func.count(Shift.id)).where(Shift.work_location_status == "OFFICE")).scalar() or 0
        leave = self.db.execute(select(func.count(Shift.id)).where(Shift.work_location_status == "LEAVE")).scalar() or 0
        users = self.db.execute(select(Shift.redmine_user_id).distinct()).scalars().all()
        return {
            "total_shifts": total,
            "active_shifts": active,
            "completed_shifts": total - active - leave,
            "leave_shifts": leave,
            "users_with_shifts": len(users),
        }

    def fetch_by_date(self, date_str: str, email: str = None) -> List[Shift]:
        d = date.fromisoformat(date_str)
        stmt = select(Shift).where(Shift.date == d)
        if email:
            stmt = stmt.where(Shift.user_email == email)
        stmt = stmt.order_by(Shift.created_at)
        return list(self.db.execute(stmt).scalars().all())

    def fetch_by_date_range(self, start_date: str, end_date: str, redmine_user_id: int = None) -> List[Shift]:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        stmt = select(Shift).where(Shift.date >= sd, Shift.date <= ed)
        if redmine_user_id is not None:
            stmt = stmt.where(Shift.redmine_user_id == redmine_user_id)
        stmt = stmt.order_by(Shift.date)
        return list(self.db.execute(stmt).scalars().all())

    def fetch_history_by_email(self, email: str, limit: int = 10, skip: int = 0) -> List[Shift]:
        stmt = select(Shift).where(Shift.user_email == email).order_by(Shift.created_at.desc()).offset(skip).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def search_by_project(self, query: str, limit: int = 5) -> List[Shift]:
        q = query.lower()
        stmt = select(Shift).where(Shift.shift_code.ilike(f"%{q}%")).order_by(Shift.date.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def delete_by_user_and_date(self, redmine_user_id: int, date_str: str):
        d = date.fromisoformat(date_str)
        stmt = select(Shift).where(Shift.redmine_user_id == redmine_user_id, Shift.date == d)
        shifts = list(self.db.execute(stmt).scalars().all())
        for s in shifts:
            self.db.delete(s)
        self.db.commit()

    def fetch_by_date_and_user(self, redmine_user_id: int, date_str: str) -> Optional[Shift]:
        d = date.fromisoformat(date_str)
        stmt = select(Shift).where(Shift.redmine_user_id == redmine_user_id, Shift.date == d)
        return self.db.execute(stmt).scalars().first()


class ShiftDefinitionService(BaseService[ShiftDefinition]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch_by_code(self, code: str) -> Optional[ShiftDefinition]:
        stmt = select(ShiftDefinition).where(ShiftDefinition.shift_code == code)
        return self.db.execute(stmt).scalars().first()

    def fetch_all(self) -> List[ShiftDefinition]:
        stmt = select(ShiftDefinition).order_by(ShiftDefinition.shift_code)
        return list(self.db.execute(stmt).scalars().all())
