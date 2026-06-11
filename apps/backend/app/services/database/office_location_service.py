import logging
from typing import Optional
from sqlalchemy.orm import Session
from app.services.database.base_service import BaseService
from app.models.office_location import OfficeLocation

logger = logging.getLogger(__name__)


class OfficeLocationService(BaseService[OfficeLocation]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)
