from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.services.database.base_service import BaseService
from app.models.recording import Recording
from datetime import datetime, timezone


class RecordingService(BaseService[Recording]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch_by_email(self, email: str) -> List[Recording]:
        stmt = select(Recording).where(Recording.user_email == email).order_by(Recording.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def fetch_all(self) -> List[Recording]:
        stmt = select(Recording).order_by(Recording.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def fetch_by_url(self, url: str) -> Optional[Recording]:
        stmt = select(Recording).where(Recording.recording_url == url)
        return self.db.execute(stmt).scalars().first()

    def mark_played(self, recording_id, email: str = None) -> bool:
        stmt = select(Recording).where(Recording.id == recording_id)
        if email:
            stmt = stmt.where(Recording.user_email == email)
        rec = self.db.execute(stmt).scalars().first()
        if not rec:
            return False
        rec.is_played = True
        self.db.commit()
        return True

    def soft_delete(self, recording_id) -> bool:
        rec = self.db.get(Recording, recording_id)
        if not rec:
            return False
        rec.is_deleted = True
        rec.deletion_date = datetime.now(timezone.utc)
        self.db.commit()
        return True
