from app.services.database.base_service import BaseService
from app.models.leave import Leave
from sqlalchemy.orm import Session

class LeaveService(BaseService[Leave]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)
