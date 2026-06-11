from app.services.database.base_service import BaseService
from app.models.holiday import Holiday
from sqlalchemy.orm import Session

class HolidayService(BaseService[Holiday]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)
