import logging
from datetime import datetime, timezone, date
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.services.database.shift_service import ShiftService as PGShiftService, ShiftDefinitionService as PGShiftDefinitionService
from sqlalchemy import select
from sqlalchemy import update as sql_update, delete as sql_delete

logger = logging.getLogger(__name__)


class ShiftService:
    """Business logic for shift management now backed by PostgreSQL."""

    def _svc(self, db: Session) -> PGShiftService:
        return PGShiftService(db)

    def _def_svc(self, db: Session) -> PGShiftDefinitionService:
        return PGShiftDefinitionService(db)

    async def create_shift(self, db: Session, data: dict, current_user: dict = None) -> dict:
        svc = self._svc(db)
        d = date.fromisoformat(data["date"])
        end = date.fromisoformat(data.get("endDate", data["date"]))

        keycloak_id = data.get("keycloakUserId", "")
        if not keycloak_id:
            redmine_uid = data.get("userId")
            if redmine_uid:
                emp = db.execute(select(EmployeeMaster).where(EmployeeMaster.redmine_user_id == redmine_uid)).scalars().first()
                if emp:
                    keycloak_id = emp.keycloak_user_id
        if not keycloak_id and current_user:
            keycloak_id = current_user.get("sub", "")
        if not keycloak_id:
            keycloak_id = data.get("userEmail", "")

        ws = data.get("workStatus", "OFFICE").upper()
        work_location_map = {"PRESENT": "OFFICE", "ON-SITE": "CUSTOMER_SITE", "REMOTE": "REMOTE", "WFH": "REMOTE", "CUSTOMER_SITE": "CUSTOMER_SITE", "HOME": "CUSTOMER_SITE"}
        if ws in work_location_map:
            ws = work_location_map[ws]

        work_address = data.get("workAddress", "N/A")
        if work_address == "N/A":
            emp = db.execute(select(EmployeeMaster).where(EmployeeMaster.keycloak_user_id == keycloak_id)).scalars().first()
            if emp and emp.location_id:
                office = db.execute(select(OfficeLocation).where(OfficeLocation.id == emp.location_id)).scalars().first()
                if office:
                    work_address = office.address or f"{office.name}, {office.city or ''}".strip(", ")

        start_time = None
        end_time = None
        if data.get("shiftStartTime"):
            try:
                start_time = datetime.strptime(data["shiftStartTime"][:5], "%H:%M").time()
            except ValueError:
                pass
        if data.get("shiftEndTime"):
            try:
                end_time = datetime.strptime(data["shiftEndTime"][:5], "%H:%M").time()
            except ValueError:
                pass

        shift = svc.create(Shift,
            keycloak_user_id=keycloak_id,
            redmine_user_id=data.get("userId"),
            user_email=data.get("userEmail", ""),
            date=d,
            end_date=end,
            shift_code=data.get("shift", ""),
            project_id=data.get("projectId"),
            project_name=data.get("projectName"),
            work_location_status=ws,
            work_address=work_address,
            shift_start_time=start_time,
            shift_end_time=end_time,
            shift_start_utc=data.get("shiftStartUTC"),
            shift_end_utc=data.get("shiftEndUTC"),
            country=data.get("country", ""),
        )
        return {
            "id": str(shift.id),
            "userId": shift.redmine_user_id,
            "userEmail": shift.user_email,
            "date": shift.date.isoformat() if shift.date else None,
            "endDate": shift.end_date.isoformat() if shift.end_date else None,
            "shift": shift.shift_code,
            "projectId": shift.project_id,
            "projectName": shift.project_name,
            "workStatus": shift.work_location_status,
            "workAddress": shift.work_address,
            "shiftStartTime": shift.shift_start_time.isoformat() if shift.shift_start_time else None,
            "shiftEndTime": shift.shift_end_time.isoformat() if shift.shift_end_time else None,
            "shiftStartUTC": shift.shift_start_utc,
            "shiftEndUTC": shift.shift_end_utc,
            "country": shift.country,
            "createdAt": shift.created_at,
            "updatedAt": shift.updated_at,
        }

    async def get_shift_by_id(self, db: Session, shift_id: str) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_one(Shift, id=shift_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_shifts_by_user_id(self, db: Session, user_id: int) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_user_id(user_id)
        return [self._serialize(s) for s in shifts]

    async def get_shifts_by_email(self, db: Session, email: str) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_email(email)
        return [self._serialize(s) for s in shifts]

    async def get_current_shift(self, db: Session, user_id: int) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_current(user_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_active_shift(self, db: Session, user_id: int) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_active(user_id)
        if not shift:
            return None
        return self._serialize(shift)

    async def get_stats(self, db: Session) -> dict:
        svc = self._svc(db)
        return svc.get_stats()

    async def get_shifts_by_date(self, db: Session, date_str: str, email: str = None) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_date(date_str, email)
        return [self._serialize(s) for s in shifts]

    async def get_shifts_by_date_range(self, db: Session, start_date: str, end_date: str, user_id: int = None) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_by_date_range(start_date, end_date, user_id)
        return [self._serialize(s) for s in shifts]

    async def get_shift_history_by_email(self, db: Session, email: str, limit: int = 10, skip: int = 0) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.fetch_history_by_email(email, limit, skip)
        return [
            {
                "id": str(s.id),
                "userId": s.redmine_user_id,
                "userEmail": s.user_email,
                "date": s.date.isoformat() if s.date else None,
                "endDate": s.end_date.isoformat() if s.end_date else None,
                "shift": s.shift_code,
                "projectId": s.project_id,
                "projectName": s.project_name,
                "workStatus": s.work_location_status,
                "workAddress": s.work_address,
                "shiftStartTime": s.shift_start_time.isoformat() if s.shift_start_time else None,
                "shiftEndTime": s.shift_end_time.isoformat() if s.shift_end_time else None,
                "createdAt": s.created_at,
            }
            for s in shifts
        ]

    async def update_shift(self, db: Session, shift_id: str, data: dict) -> Optional[dict]:
        svc = self._svc(db)
        shift = svc.fetch_one(Shift, id=shift_id)
        if not shift:
            return None
        mapping = {
            "shift": "shift_code",
            "workStatus": "work_location_status",
            "workAddress": "work_address",
            "shiftStartTime": "shift_start_time",
            "shiftEndTime": "shift_end_time",
            "shiftStartUTC": "shift_start_utc",
            "shiftEndUTC": "shift_end_utc",
            "projectId": "project_id",
            "projectName": "project_name",
            "endDate": "end_date",
        }
        update_data = {}
        for mongo_key, pg_col in mapping.items():
            if mongo_key in data and pg_col:
                val = data[mongo_key]
                if pg_col == "end_date" and isinstance(val, str):
                    val = date.fromisoformat(val)
                update_data[pg_col] = val
        if update_data:
            svc.update(Shift, shift.id, **update_data)
        return await self.get_shift_by_id(db, shift_id)

    async def delete_shift(self, db: Session, shift_id: str) -> bool:
        svc = self._svc(db)
        return svc.delete(Shift, shift_id)

    async def create_shift_definition(self, db: Session, data: dict) -> dict:
        svc = self._def_svc(db)
        existing = svc.fetch_by_code(data["shiftCode"])
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Shift code '{data['shiftCode']}' already exists.",
            )
        start = datetime.strptime(data.get("startTime", "00:00")[:5], "%H:%M").time()
        end = datetime.strptime(data.get("endTime", "00:00")[:5], "%H:%M").time()
        sd = svc.create(ShiftDefinition,
            shift_code=data["shiftCode"],
            shift_name=data.get("shiftName", ""),
            start_time=start,
            end_time=end,
            timezone=data.get("timezone", "Asia/Kolkata"),
            country=data.get("country", ""),
        )
        return self._serialize_def(sd)

    async def get_all_shift_definitions(self, db: Session) -> List[dict]:
        svc = self._def_svc(db)
        defs = svc.fetch_all()
        return [self._serialize_def(d) for d in defs]

    async def get_shift_definition_by_code(self, db: Session, shift_code: str) -> Optional[dict]:
        svc = self._def_svc(db)
        sd = svc.fetch_by_code(shift_code)
        if not sd:
            return None
        return self._serialize_def(sd)

    async def get_shift_definition(self, db: Session, shift_id: str) -> Optional[dict]:
        svc = self._def_svc(db)
        sd = svc.fetch_one(ShiftDefinition, shift_id=shift_id)
        if not sd:
            return None
        return self._serialize_def(sd)

    async def update_shift_definition(self, db: Session, shift_id: str, data: dict) -> Optional[dict]:
        svc = self._def_svc(db)
        existing = svc.fetch_one(ShiftDefinition, shift_id=shift_id)
        if not existing:
            return None
        if data.get("shiftCode") and data["shiftCode"] != existing.shift_code:
            dup = svc.fetch_by_code(data["shiftCode"])
            if dup:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Shift code '{data['shiftCode']}' already exists.",
                )
        update_data = {}
        if "shiftCode" in data:
            update_data["shift_code"] = data["shiftCode"]
        if "shiftName" in data:
            update_data["shift_name"] = data["shiftName"]
        if "startTime" in data:
            update_data["start_time"] = datetime.strptime(data["startTime"][:5], "%H:%M").time()
        if "endTime" in data:
            update_data["end_time"] = datetime.strptime(data["endTime"][:5], "%H:%M").time()
        if "timezone" in data:
            update_data["timezone"] = data["timezone"]
        if "country" in data:
            update_data["country"] = data["country"]
        if update_data:
            svc.db.execute(
                sql_update(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id).values(**update_data)
            )
            svc.db.commit()
        return await self.get_shift_definition(db, shift_id)

    async def delete_shift_definition(self, db: Session, shift_id: str) -> bool:
        svc = self._def_svc(db)
        result = svc.db.execute(sql_delete(ShiftDefinition).where(ShiftDefinition.shift_id == shift_id))
        svc.db.commit()
        return result.rowcount > 0

    async def search_shifts(self, db: Session, query: str, limit: int = 5) -> List[dict]:
        svc = self._svc(db)
        shifts = svc.search_by_project(query, limit)
        result = []
        for s in shifts:
            result.append({
                "id": f"shift_{s.id}",
                "date": s.date.isoformat() if s.date else "",
                "startTime": s.shift_start_time.isoformat() if s.shift_start_time else "",
                "endTime": s.shift_end_time.isoformat() if s.shift_end_time else "",
                "status": s.work_location_status or "YET_TO_START",
                "projectName": "",
                "employeeName": s.user_email,
            })
        return result

    def _serialize(self, shift: Shift) -> dict:
        return {
            "id": str(shift.id),
            "userId": shift.redmine_user_id,
            "userEmail": shift.user_email,
            "date": shift.date.isoformat() if shift.date else None,
            "endDate": shift.end_date.isoformat() if shift.end_date else None,
            "shift": shift.shift_code,
            "projectId": shift.project_id,
            "projectName": shift.project_name,
            "workStatus": shift.work_location_status,
            "workAddress": shift.work_address,
            "shiftStartTime": shift.shift_start_time.isoformat() if shift.shift_start_time else None,
            "shiftEndTime": shift.shift_end_time.isoformat() if shift.shift_end_time else None,
            "shiftStartUTC": shift.shift_start_utc,
            "shiftEndUTC": shift.shift_end_utc,
            "country": shift.country,
            "createdAt": shift.created_at,
            "updatedAt": shift.updated_at,
        }

    def _serialize_def(self, sd: ShiftDefinition) -> dict:
        return {
            "_id": str(sd.shift_id),
            "shiftCode": sd.shift_code,
            "shiftName": sd.shift_name,
            "startTime": sd.start_time.isoformat() if sd.start_time else None,
            "endTime": sd.end_time.isoformat() if sd.end_time else None,
            "timezone": sd.timezone,
            "country": sd.country,
            "createdAt": sd.created_at,
            "updatedAt": sd.updated_at,
        }


shift_service = ShiftService()
