from datetime import date, timedelta, datetime, timezone
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.wfh_request import WfhRequest
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.employee_master import EmployeeMaster
from app.models.holiday import Holiday


SORT_COLUMNS = {"created_at", "start_date", "status", "user_email"}


class WfhService:

    def get_pending(self, db: Session) -> List[WfhRequest]:
        return db.query(WfhRequest).filter(
            WfhRequest.status == "pending"
        ).order_by(WfhRequest.created_at.desc()).all()

    def get_my_requests(self, db: Session, keycloak_user_id: str) -> List[WfhRequest]:
        return db.query(WfhRequest).filter(
            WfhRequest.keycloak_user_id == keycloak_user_id
        ).order_by(WfhRequest.created_at.desc()).all()

    def get_my_requests_paginated(self, db: Session, keycloak_user_id: str,
                                   status: str | None, search: str | None,
                                   sort_by: str, sort_dir: str,
                                   page: int, page_size: int) -> tuple[List[WfhRequest], int]:
        query = db.query(WfhRequest).filter(WfhRequest.keycloak_user_id == keycloak_user_id)
        if status:
            query = query.filter(WfhRequest.status == status)
        if search:
            query = query.filter(WfhRequest.reason.ilike(f"%{search}%"))
        return self._apply_pagination(query, sort_by, sort_dir, page, page_size)

    def get_managed_requests_paginated(self, db: Session, target_emails: list | None,
                                        status: str | None, search: str | None,
                                        from_dt: date | None, to_dt: date | None,
                                        sort_by: str, sort_dir: str,
                                        page: int, page_size: int) -> tuple[List[WfhRequest], int]:
        query = db.query(WfhRequest)
        if target_emails is not None:
            query = query.filter(WfhRequest.user_email.in_(target_emails))
        if status:
            query = query.filter(WfhRequest.status == status)
        if from_dt:
            query = query.filter(WfhRequest.start_date >= from_dt)
        if to_dt:
            query = query.filter(WfhRequest.start_date <= to_dt)
        if search:
            matching = db.query(EmployeeMaster.user_email).filter(
                EmployeeMaster.user_email.ilike(f"%{search}%") |
                EmployeeMaster.first_name.ilike(f"%{search}%") |
                EmployeeMaster.last_name.ilike(f"%{search}%")
            ).all()
            emails = {e[0] for e in matching}
            if emails:
                query = query.filter(WfhRequest.user_email.in_(emails))
            else:
                query = query.filter(WfhRequest.user_email == "__no_match__")
        return self._apply_pagination(query, sort_by, sort_dir, page, page_size)

    def get_stats(self, db: Session, keycloak_user_id: str,
                  month: int | None, year: int | None) -> dict:
        now = datetime.now(timezone.utc)
        if month is None:
            month = now.month
        if year is None:
            year = now.year

        query = db.query(WfhRequest).filter(
            WfhRequest.keycloak_user_id == keycloak_user_id,
        )
        if year:
            query = query.filter(func.extract("year", WfhRequest.start_date) == year)
        if month:
            query = query.filter(func.extract("month", WfhRequest.start_date) == month)

        total = query.count()
        pending = query.filter(WfhRequest.status == "pending").count()
        approved = query.filter(WfhRequest.status == "approved").count()
        rejected = query.filter(WfhRequest.status == "rejected").count()

        return {
            "total": total,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
        }

    def _apply_pagination(self, query, sort_by: str, sort_dir: str,
                          page: int, page_size: int) -> tuple[List[WfhRequest], int]:
        sort_by = sort_by if sort_by in SORT_COLUMNS else "created_at"
        sort_dir = sort_dir if sort_dir in ("asc", "desc") else "desc"
        column = getattr(WfhRequest, sort_by)
        query = query.order_by(column.desc() if sort_dir == "desc" else column.asc())

        total = query.count()
        offset = (page - 1) * page_size
        results = query.offset(offset).limit(page_size).all()
        return results, total

    def create(self, db: Session, keycloak_user_id: str, user_email: str,
               start_date: date, end_date: date, resuming_date: date | None,
               reason: str | None, comment: str | None, contact_number: str | None,
               approver_id: int | None, project_id: int | None, project_name: str | None,
               skip_weekends: bool, skip_holidays: bool) -> tuple[List[str], int, dict]:
        request_ids = []
        created = 0
        skipped = {"weekends": 0, "holidays": 0}

        current = start_date
        while current <= end_date:
            if skip_weekends and current.weekday() >= 5:
                skipped["weekends"] += 1
                current += timedelta(days=1)
                continue
            if skip_holidays:
                holiday = db.query(Holiday).filter(Holiday.holiday_date == current).first()
                if holiday:
                    skipped["holidays"] += 1
                    current += timedelta(days=1)
                    continue

            existing = db.query(WfhRequest).filter(
                WfhRequest.keycloak_user_id == keycloak_user_id,
                WfhRequest.start_date <= current,
                WfhRequest.end_date >= current,
                WfhRequest.status.in_(["pending", "approved"]),
            ).first()
            if existing:
                current += timedelta(days=1)
                continue

            wfh = WfhRequest(
                keycloak_user_id=keycloak_user_id,
                user_email=user_email,
                start_date=current,
                end_date=current,
                resuming_date=resuming_date,
                reason=reason,
                comment=comment,
                contact_number=contact_number,
                approver_id=approver_id,
                project_id=project_id,
                project_name=project_name,
            )
            db.add(wfh)
            db.flush()
            request_ids.append(str(wfh.id))
            created += 1
            current += timedelta(days=1)

        db.commit()
        return request_ids, created, skipped

    def approve(self, db: Session, request_id: str, reviewer_email: str) -> Optional[WfhRequest]:
        wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
        if not wfh or wfh.status != "pending":
            return None
        wfh.status = "approved"
        wfh.reviewed_by = reviewer_email
        wfh.updated_at = datetime.now(timezone.utc)
        self._ensure_wfh_shift(db, wfh)
        db.commit()
        db.refresh(wfh)
        return wfh

    def reject(self, db: Session, request_id: str, reviewer_email: str,
               reason: str | None) -> Optional[WfhRequest]:
        wfh = db.query(WfhRequest).filter(WfhRequest.id == request_id).first()
        if not wfh or wfh.status != "pending":
            return None
        wfh.status = "rejected"
        wfh.reviewed_by = reviewer_email
        wfh.reject_reason = reason
        wfh.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(wfh)
        return wfh

    def _ensure_wfh_shift(self, db: Session, wfh: WfhRequest):
        existing = db.query(Shift).filter(
            Shift.keycloak_user_id == wfh.keycloak_user_id,
            Shift.date == wfh.start_date,
        ).first()
        if existing:
            existing.work_location_status = "WFH"
            return

        sd = db.query(ShiftDefinition).first()
        emp = db.query(EmployeeMaster).filter(
            EmployeeMaster.keycloak_user_id == wfh.keycloak_user_id
        ).first()

        shift = Shift(
            keycloak_user_id=wfh.keycloak_user_id,
            user_email=wfh.user_email,
            date=wfh.start_date,
            end_date=wfh.start_date,
            shift_code=sd.shift_code if sd else "general",
            work_location_status="WFH",
            work_address="Home",
            redmine_user_id=emp.redmine_user_id if emp else None,
            project_id=wfh.project_id or (emp.project_id if emp and getattr(emp, "project_id", None) else None),
            status="Yet to start",
            per_diem_eligible=False,
            conveyance_eligible=False,
        )
        db.add(shift)


wfh_service = WfhService()
