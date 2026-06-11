# MongoDB → PostgreSQL Migration Guide

## Current State

- **MongoDB** (`attendance_db`): 9 collections used by the app
- **PostgreSQL** (`keycloak-db`): Only used by Keycloak (separate database)
- **ORM**: `motor` (async MongoDB driver) + `pymongo`
- **Schemas**: Pydantic models define shapes; no DB-level schema enforcement

## Target State

- **PostgreSQL** (new database): All app data in relational tables
- **ORM**: `sqlalchemy[asyncio]` + `asyncpg`
- **Migrations**: Alembic
- **UUIDs** replace MongoDB ObjectIds

---

## Phase 1: Infrastructure

### 1.1 Add new PostgreSQL instance

Add a new PostgreSQL service (e.g. `app-db`) to your Docker Compose, separate from `keycloak-db`:

```yaml
services:
  app-db:
    image: postgres:16
    environment:
      POSTGRES_DB: attendance_app
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app-password
    volumes:
      - app-db-data:/var/lib/postgresql/data
    ports:
      - "5433:5432"

volumes:
  app-db-data:
```

### 1.2 Add config & install deps

**`app/core/config.py`** — add a new DB URL:

```python
DATABASE_APP_URL: str = "postgresql+asyncpg://app:app-password@app-db:5432/attendance_app"
```

**Install async deps** (already have `asyncpg` and `sqlalchemy` in pyproject.toml; add `alembic`):

```bash
uv add alembic
```

---

## Phase 2: Async SQLAlchemy Setup

### 2.1 Create async engine & session

Replace `app/core/database.py` with an async setup:

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.core.models import Base

engine = create_async_engine(settings.DATABASE_APP_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
```

### 2.2 Add `DATABASE_APP_URL` to Settings

```python
# app/core/config.py
DATABASE_APP_URL: str = "postgresql+asyncpg://app:app-password@app-db:5432/attendance_app"
```

---

## Phase 3: SQLAlchemy Models

One model per MongoDB collection. Place in `app/models/`.

### 3.1 Example models

**`app/models/shift.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Float, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, nullable=False, index=True)
    user_name = Column(String(255))
    user_email = Column(String(255), index=True)
    project_id = Column(Integer, nullable=False, index=True)
    project_name = Column(String(255))
    shift = Column(String(50))
    work_status = Column(String(50))
    work_address = Column(String(500), default="N/A")
    leave_type = Column(String(50), nullable=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    end_date = Column(String(10), nullable=True)
    shift_start_time = Column(String(5))
    shift_end_time = Column(String(5))
    shift_start_utc = Column(DateTime(timezone=True), nullable=True)
    shift_end_utc = Column(DateTime(timezone=True), nullable=True)
    shift_name = Column(String(100))
    timezone = Column(String(50), default="Asia/Kolkata")
    country = Column(String(10), default="")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
```

Full set of models needed (all in `app/models/`):

| Model | Table | Replaces MongoDB Collection |
|-------|-------|---------------------------|
| `Shift` | `shifts` | `shifts` |
| `ShiftDefinition` | `shift_definitions` | `shift_definitions` |
| `Leave` | `leaves` | `leaves` |
| `Holiday` | `holidays` | `holidays` |
| `LeaveBalance` | `leave_balances` | `leave_balances` |
| `Notification` | `notifications` | `notifications` |
| `Recording` | `recordings` | `recordings` |
| `Location` | `locations` | `locations` |
| `SystemSetting` | `system_settings` | `system_settings` |

**`app/models/__init__.py`** — export all models and `Base`:

```python
from app.core.models import Base
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.leave import Leave, LeaveStatus, LeaveType
from app.models.holiday import Holiday
from app.models.leave_balance import LeaveBalance
from app.models.notification import Notification
from app.models.recording import Recording
from app.models.location import Location
from app.models.system_setting import SystemSetting

__all__ = [
    "Base", "Shift", "ShiftDefinition", "Leave",
    "Holiday", "LeaveBalance", "Notification",
    "Recording", "Location", "SystemSetting",
]
```

### 3.2 Alembic setup

```bash
cd apps/backend
uv run alembic init alembic
```

Edit `alembic/env.py` to use your async models:

```python
from app.models import Base
target_metadata = Base.metadata
```

Generate initial migration:

```bash
uv run alembic revision --autogenerate -m "initial models"
uv run alembic upgrade head
```

---

## Phase 4: Data Migration Script

One-time script to copy MongoDB data to PostgreSQL. Place in `app/scripts/`.

```python
"""
app/scripts/migrate_mongo_to_pg.py

Usage: uv run python -m app.scripts.migrate_mongo_to_pg
"""
import asyncio
import uuid
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Base, Shift, ShiftDefinition, Leave, Holiday, \
    LeaveBalance, Notification, Recording, Location, SystemSetting


async def migrate():
    # Connect to MongoDB
    mongo_client = AsyncIOMotorClient(settings.MONGO_URL)
    mongo_db = mongo_client[settings.MONGO_DB_NAME]

    # Connect to PostgreSQL
    engine = create_async_engine(settings.DATABASE_APP_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        # Migrate each collection
        async for doc in mongo_db.shifts.find():
            session.add(Shift(
                id=uuid.uuid4(),
                user_id=doc["userId"],
                user_name=doc.get("userName"),
                user_email=doc.get("userEmail"),
                project_id=doc["projectId"],
                project_name=doc.get("projectName"),
                shift=doc.get("shift"),
                work_status=doc.get("workStatus"),
                work_address=doc.get("workAddress", "N/A"),
                leave_type=doc.get("leaveType"),
                date=doc["date"],
                end_date=doc.get("endDate"),
                shift_start_time=doc.get("shiftStartTime"),
                shift_end_time=doc.get("shiftEndTime"),
                shift_start_utc=doc.get("shiftStartUTC"),
                shift_end_utc=doc.get("shiftEndUTC"),
                shift_name=doc.get("shiftName"),
                timezone=doc.get("timezone", "Asia/Kolkata"),
                country=doc.get("country", ""),
                created_at=doc.get("createdAt"),
                updated_at=doc.get("updatedAt"),
            ))

        await session.commit()

    # ... repeat for all collections

    print("Migration complete")
    mongo_client.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
```

---

## Phase 5: Repository Layer

Create repository classes to replace direct MongoDB calls in services.

**`app/repositories/shift_repository.py`**

```python
from uuid import UUID
from typing import Optional, List
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.shift import Shift


class ShiftRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> Shift:
        shift = Shift(**data)
        self.session.add(shift)
        await self.session.commit()
        await self.session.refresh(shift)
        return shift

    async def find_by_id(self, shift_id: UUID) -> Optional[Shift]:
        result = await self.session.execute(
            select(Shift).where(Shift.id == shift_id)
        )
        return result.scalar_one_or_none()

    async def find_by_user_id(self, user_id: int) -> List[Shift]:
        result = await self.session.execute(
            select(Shift).where(Shift.user_id == user_id)
            .order_by(Shift.created_at.desc())
        )
        return list(result.scalars().all())

    async def find_by_date(self, date_str: str, email: Optional[str] = None) -> List[Shift]:
        query = select(Shift).where(Shift.date == date_str)
        if email:
            query = query.where(Shift.user_email == email)
        result = await self.session.execute(query.order_by(Shift.user_name))
        return list(result.scalars().all())

    async def find_by_date_range(self, start: str, end: str, user_id: Optional[int] = None) -> List[Shift]:
        query = select(Shift).where(Shift.date.between(start, end))
        if user_id is not None:
            query = query.where(Shift.user_id == user_id)
        result = await self.session.execute(query.order_by(Shift.date))
        return list(result.scalars().all())

    async def update(self, shift_id: UUID, data: dict) -> Optional[Shift]:
        shift = await self.find_by_id(shift_id)
        if not shift:
            return None
        for key, value in data.items():
            setattr(shift, key, value)
        await self.session.commit()
        await self.session.refresh(shift)
        return shift

    async def delete(self, shift_id: UUID) -> bool:
        result = await self.session.execute(
            delete(Shift).where(Shift.id == shift_id)
        )
        await self.session.commit()
        return result.rowcount > 0
```

---

## Phase 6: Refactor Services

Update existing services to use repositories instead of MongoDB.

**Before (MongoDB):**

```python
# app/features/shifts/service.py
async def create_shift(self, db, data: dict) -> dict:
    result = await db.shifts.insert_one(data)
    created = await db.shifts.find_one({"_id": result.inserted_id})
    return _serialize_shift(created)
```

**After (PostgreSQL):**

```python
# app/features/shifts/service.py
from app.repositories.shift_repository import ShiftRepository

class ShiftService:
    async def create_shift(self, session: AsyncSession, data: dict) -> dict:
        repo = ShiftRepository(session)
        shift = await repo.create(data)
        return shift.to_dict()  # define to_dict() on model
```

---

## Phase 7: Update Route Dependencies

Replace `get_mongodb()` with `get_db()` in route handlers.

**Before:**

```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shift(
    payload: ShiftCreate,
    db=Depends(get_mongodb),          # MongoDB
    current_user: dict = Depends(get_current_user),
):
```

**After:**

```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shift(
    payload: ShiftCreate,
    session: AsyncSession = Depends(get_db),  # PostgreSQL
    current_user: dict = Depends(get_current_user),
):
    shift_service = ShiftService()
    result = await shift_service.create_shift(session, payload.model_dump())
    return result
```

---

## Phase 8: Key Design Decisions

### IDs
| MongoDB | PostgreSQL |
|---------|------------|
| `_id` (ObjectId) | `id` (UUID) — `sqlalchemy.dialects.postgresql.UUID` |
| `userId` (int, from Redmine) | Keep as `Integer` (FK to Redmine, not local) |
| `projectId` (int, from Redmine) | Keep as `Integer` (FK to Redmine, not local) |

### Enums
Use SQLAlchemy enums for status fields:

```python
import enum
from sqlalchemy import Enum as SAEnum

class LeaveStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

# In model:
status = Column(SAEnum(LeaveStatus), default=LeaveStatus.PENDING, nullable=False)
```

### Indexes
Add indexes for all frequently queried fields:
- `shifts`: `(user_id, date)`, `user_email`, `project_id`, `date`
- `leaves`: `user_id`, `(user_id, status)`, `status`
- `notifications`: `(user_id, is_read)`, `created_at`
- `locations`: `email` (unique)
- `recordings`: `email`
- `holidays`: `(country_code, holiday_date)` (unique)

### Unique Constraints
- `shifts`: `unique_constraint("uq_shift_per_day", "user_id", "date")`
- `shift_definitions`: `unique_constraint("uq_shift_code", "shift_code")`
- `holidays`: `unique_constraint("uq_holiday", "country_code", "holiday_date")`
- `locations`: `unique_constraint("uq_user_location", "email")`

---

## Phase 9: Migration Order

Implement features one at a time to minimize risk:

| Order | Feature | Complexity | Notes |
|-------|---------|------------|-------|
| 1 | Settings | Trivial | Single row, no relations |
| 2 | Locations | Trivial | Upsert by email |
| 3 | Shift Definitions | Low | Standalone CRUD |
| 4 | Holidays | Low | Standalone upsert |
| 5 | Notifications | Low | FK to nothing local |
| 6 | Recordings | Medium | MinIO integration stays same |
| 7 | Leave Balances | Medium | Standalone |
| 8 | Leaves | High | Overlap queries, notifications, approvals |
| 9 | Shifts | High | Date-range queries, bulk ops |
| 10 | Remove MongoDB | Cleanup | Remove motor, pymongo deps, mongodb.py |

---

## Phase 10: Rollout

1. **Add `DATABASE_APP_URL` to `.env`** (new PG URL)
2. **Run Alembic migrations** on deploy
3. **Run data migration script** (one-time, can be re-run idempotently)
4. **Deploy with feature flags** — each feature flips from MongoDB to PostgreSQL
5. **Run both databases in parallel** during transition
6. **Validate data integrity** — compare row counts, spot-check records
7. **Drop MongoDB collections** only after all features verified
8. **Remove MongoDB code + dependencies** in final cleanup PR

---

## Rollback Plan

If something goes wrong:

1. Keep MongoDB running and populated during the entire migration
2. Each feature can be individually reverted to MongoDB by swapping the dependency back
3. Data migration script is read-from-MongoDB, write-to-PostgreSQL — MongoDB is never modified
4. Final switch is just changing the route dependency

---

## Files to Create/Modify

| Action | File |
|--------|------|
| Create | `app/models/shift.py` |
| Create | `app/models/shift_definition.py` |
| Create | `app/models/leave.py` |
| Create | `app/models/holiday.py` |
| Create | `app/models/leave_balance.py` |
| Create | `app/models/notification.py` |
| Create | `app/models/recording.py` |
| Create | `app/models/location.py` |
| Create | `app/models/system_setting.py` |
| Create | `app/models/__init__.py` |
| Create | `app/repositories/shift_repository.py` |
| Create | `app/repositories/leave_repository.py` |
| Create | `app/repositories/notification_repository.py` |
| Create | `app/repositories/recording_repository.py` |
| Create | `app/repositories/location_repository.py` |
| Create | `app/repositories/settings_repository.py` |
| Create | `app/scripts/migrate_mongo_to_pg.py` |
| Modify | `app/core/database.py` (async SQLAlchemy) |
| Modify | `app/core/config.py` (add DATABASE_APP_URL) |
| Modify | `app/core/models.py` (add common columns mixin) |
| Modify | Each feature's `services/*.py` (use repositories) |
| Modify | Each feature's `routes.py` (use `get_db` instead of `get_mongodb`) |
| Modify | `app/main.py` (update lifespan to init new DB) |
| Delete | `app/core/mongodb.py` (after migration complete) |
