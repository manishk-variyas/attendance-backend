# PostGIS Integration: Setup, Architecture & Data Flow

**No AI/LLM content. Pure infrastructure, schema, SQL, and data flow.**

---

## 1. What PostGIS Does Here

PostGIS replaces a Python haversine-distance function with an in-database spatial index for determining whether an employee's GPS check-in location falls inside their assigned office's geofence.

| Operation | Before | After |
|---|---|---|
| Geofence check | Python `_haversine()` math, ~30ms | PostGIS `ST_DWithin`, ~0.1ms |
| Distance from office | Not available | `ST_Distance`, returned alongside geofence |
| Spatial index | None | GIST index on `office_locations.geom` |

---

## 2. Infrastructure

### 2.1 Docker Compose

The PostGIS database is defined in `infra/postgres/docker-compose.yml` and included by the root `infra/docker-compose.yml`:

```
infra/docker-compose.yml
  └── postgres/docker-compose.yml   # postgis/postgis:17-3.5
```

**File:** `infra/postgres/docker-compose.yml`
```yaml
services:
  postgres:
    image: postgis/postgis:17-3.5
    container_name: backend_postgres
    restart: always
    environment:
      POSTGRES_DB: ${BACKEND_POSTGRES_DB:-attendance}
      POSTGRES_USER: ${BACKEND_POSTGRES_USER:-backend_user}
      POSTGRES_PASSWORD: ${BACKEND_POSTGRES_PASSWORD:-backend_password}
    volumes:
      - backend_postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    networks:
      - infra-network
```

### 2.2 Two Separate PostgreSQL Instances

| Instance | Image | Database | Purpose | Has PostGIS? |
|---|---|---|---|---|
| `keycloak-db` | `postgres:16` | `keycloak` | Keycloak IAM data | No |
| `postgres` | `postgis/postgis:17-3.5` | `attendance` | App data + geofencing | **Yes** |

### 2.3 Environment Variables

**`infra/.env`** (line 37-39) — passed to docker-compose:
```
BACKEND_POSTGRES_DB=attendance
BACKEND_POSTGRES_USER=backend_user
BACKEND_POSTGRES_PASSWORD=backend_password
```

**`infra/backend/.env`** (line 21) — loaded by the backend app:
```
DATABASE_URL=postgresql://backend_user:backend_password@postgres:5432/attendance
```

The backend connects to the PostGIS database (not the Keycloak database).

---

## 3. Database Schema

### 3.1 Tables Involved

```
office_locations          user_locations          attendance
├─ id (UUID PK)           ├─ id (UUID PK)         ├─ id (UUID PK)
├─ name                   ├─ email (UNIQUE)       ├─ keycloak_user_id (INDEX)
├─ latitude (Float)       ├─ latitude (Float)     ├─ attendance_date (INDEX)
├─ longitude (Float)      ├─ longitude (Float)    ├─ check_in_time
├─ radius_meters (Float)  ├─ location_name        ├─ check_in_lat / check_in_lng
├─ geom GEOMETRY(Point,4326) ← THE POSTGIS COLUMN ├─ check_in_location_name
└─ created_by             └─ updated_at           ├─ check_out_time
                                                   ├─ check_out_lat / check_out_lng
employee_master                                   ├─ check_out_location_name
├─ keycloak_user_id                               ├─ location_id (FK → office_locations)
├─ location_id (FK → office_locations)            ├─ work_location_status ← SET BY POSTGIS
└─ user_email                                     └─ ...
```

### 3.2 The PostGIS Geometry Column

**File:** `apps/backend/app/models/office_location.py:31`
```python
from geoalchemy2 import Geometry

geom = Column(Geometry("POINT", srid=4326), nullable=True)
```

- `SRID 4326` = WGS84 (GPS coordinates — lat/lng in degrees)
- `POINT` stores a single (x=longitude, y=latitude) point
- `nullable=True` because the column is populated by application code after row creation, not by the ORM insert

### 3.3 One-Time Setup SQL

Must be run once after initializing the database:

```sql
-- 1. Enable the PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Add the geometry column
ALTER TABLE office_locations
  ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326);

-- 3. Backfill geometry from existing lat/lng
UPDATE office_locations
  SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);

-- 4. Create spatial index (enables sub-millisecond lookups)
CREATE INDEX IF NOT EXISTS idx_office_geom
  ON office_locations USING GIST(geom);

-- 5. Add location_name column to user_locations
ALTER TABLE user_locations
  ADD COLUMN IF NOT EXISTS location_name VARCHAR(500);
```

---

## 4. Python Integration

### 4.1 Dependency

**File:** `apps/backend/pyproject.toml:10`
```toml
"geoalchemy2>=0.15.0",
```

GeoAlchemy2 bridges the PostGIS `GEOMETRY` type to SQLAlchemy. It is used **only in the model definition** (`office_location.py`). All spatial queries use raw `text()` SQL, not the ORM.

### 4.2 Database Connection

**File:** `apps/backend/app/core/config.py:45`
```python
DATABASE_URL: str = "postgresql://keycloak:keycloak@keycloak-db:5432/keycloak"  # default
```

**File:** `apps/backend/app/core/database.py:22`
```python
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

`init_db()` (`database.py:28`) calls `Base.metadata.create_all()` and then runs inline migrations for missing columns. Note: it does **not** create the PostGIS extension or spatial index — those must be done manually.

### 4.3 Geometry Sync on Admin CRUD

Whenever an admin creates or updates an `office_location`, the application manually syncs the `geom` column from `latitude`/`longitude`. This ensures `geom` never drifts.

**File:** `apps/backend/app/features/location/admin_routes.py:59` (create)
```python
db.execute(text(
    "UPDATE office_locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) WHERE id = :id"
), {"id": str(loc.id)})
db.commit()
```

**File:** `apps/backend/app/features/location/admin_routes.py:109-114` (update, only when lat/lng changed)
```python
if "latitude" in update_data or "longitude" in update_data:
    db.execute(text("""
        UPDATE office_locations
        SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        WHERE id = :id
    """), {"id": location_id})
    db.commit()
```

Note: `ST_MakePoint(longitude, latitude)` — PostGIS uses (x=longitude, y=latitude) order.

### 4.4 The Two Spatial SQL Queries

#### 4.4.1 Geofence Check Only (Attendance Check-In)

**File:** `apps/backend/app/features/attendance/routes.py:25-31`
```python
GEOFENCE_SQL = text("""
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
""")
```

- `ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)` — creates a PostGIS point from user's GPS coords
- `::geography` — casts to geography type so distance is calculated in **meters** (not degrees)
- `ST_DWithin(a, b, radius)` — returns `true` if `a` is within `radius` meters of `b`
- Hits the GIST spatial index for sub-millisecond performance
- Called at: `attendance/routes.py:49-52`

#### 4.4.2 Geofence + Distance (User Location Lookup)

**File:** `apps/backend/app/features/location/routes.py:18-24`
```python
GEOFENCE_SQL = text("""
    SELECT
        ST_DWithin(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography, o.radius_meters) AS inside,
        ROUND(ST_Distance(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, o.geom::geography))::INT AS distance_m
    FROM office_locations o
    WHERE o.id = :office_id
""")
```

Returns both fields in a single GIST-indexed query. Called at: `location/routes.py:106-109`

### 4.5 Reverse Geocoding (GPS → Place Name)

Currently uses the **Nominatim HTTP API** with an in-memory GPS-rounded cache.

**File:** `apps/backend/app/features/attendance/routes.py:56-75`
```python
_GEO_CACHE: dict = {}

async def _reverse_geocode(lat: float, lng: float) -> str:
    cache_key = (round(lat, 3), round(lng, 3))  # ~100m precision
    if cache_key in _GEO_CACHE:
        return _GEO_CACHE[cache_key]
    # HTTP call to nominatim.openstreetmap.org
    ...
```

Rounding to 3 decimal places (~100m granularity) ensures 300 users checking in from the same office make only 1 HTTP call total.

---

## 5. Data Flow

### 5.1 Employee Check-In

```
POST /api/attendance/check-in
{
  latitude: 28.468,
  longitude: 77.481
}
```

**Step-by-step** (`attendance/routes.py:88-182`):

| Step | Code Location | What Happens |
|---|---|---|
| 1. Auth | line 92-93 | Validate JWT, extract `keycloak_user_id` |
| 2. Duplicate check | line 98-105 | Query `attendance` for same user + today → reject if exists |
| 3. Shift lookup | line 114-128 | Find today's shift from `shifts` table, get `start_time` from `shift_definition` |
| 4. Late check | line 136-144 | Compare `now > start_time + grace_minutes` |
| 5. Office lookup | line 146-149 | Get `employee_master.location_id` for this user |
| 6. **PostGIS GEO** | line 150 | `_determine_work_location(db, lat, lng, location_id)` → `SELECT ST_DWithin(...)` → "OFFICE" or "WFH" |
| 7. Reverse geocode | line 151 | `_reverse_geocode(lat, lng)` → Nominatim → "Knowledge Park III, ..." |
| 8. Insert | line 153-167 | INSERT into `attendance` with all fields |
| 9. Sync user location | line 170-179 | UPSERT into `user_locations` with GPS + location name |
| 10. Return | line 182 | JSON response with status, isLate, workLocationStatus, locationName |

### 5.2 Employee Check-Out

```
POST /api/attendance/check-out
{
  latitude: 28.469,
  longitude: 77.480
}
```

**Step-by-step** (`attendance/routes.py:185-244`):

| Step | Code Location | What Happens |
|---|---|---|
| 1. Auth | line 192 | Validate JWT |
| 2. Lookup | line 195-204 | Find today's check-in → reject if missing or already checked out |
| 3. Set check-out | line 206-209 | `attendance.check_out_time = now()`, save checkout GPS + location name |
| 4. Early-leave check | line 211-226 | If `check_out_time < shift_end_time` → append "Early leave" to remarks |
| 5. Sync user location | line 228-238 | UPSERT into `user_locations` with checkout GPS |
| 6. Return | line 244 | JSON response |

### 5.3 Admin Views Employee Location

```
GET /api/location/vishal.singh@variyaslabs.com
```

**Step-by-step** (`location/routes.py:71-114`):

| Step | Code Location | What Happens |
|---|---|---|
| 1. Auth | line 71-81 | Validate JWT, check email match or admin role |
| 2. Read cache | line 83 | `LocationService.fetch_by_email(email)` → `user_locations` row |
| 3. Office lookup | line 101-103 | `employee_master.location_id` → `office_locations` |
| 4. **PostGIS GEO** | line 106-109 | `SELECT ST_DWithin(...) AS inside, ST_Distance(...) AS distance_m` |
| 5. Assemble response | line 90-112 | Includes `office_name`, `distance_from_office`, `is_inside_office` |
| 6. Return | line 114 | JSON — zero Nominatim calls (location_name comes from stored cache) |

### 5.4 Admin Creates/Updates Office

```
POST /api/admin/office-locations
{
  name: "Noida Office",
  latitude: 28.4679,
  longitude: 77.4811,
  radius_meters: 300
}

PUT /api/admin/office-locations/{id}
{
  latitude: 28.4680,
  longitude: 77.4820
}
```

**Step-by-step** (`location/admin_routes.py:34-61` and `89-117`):

| Step | What Happens |
|---|---|
| 1. Auth | Require admin role (`require_admin` dependency) |
| 2. Duplicate check (create only) | Check name uniqueness |
| 3. INSERT/UPDATE | Standard CRUD via `BaseService` |
| 4. **Sync geom** | `UPDATE office_locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) WHERE id = :id` |
| 5. Return | Response with id, name, lat, lng, radius, timestamps |

---

## 6. Operations

### 6.1 Starting the Stack

```bash
# Set environment
export $(cat infra/.env | xargs) && export $(cat infra/backend/.env | xargs)

# Start all services
docker compose -f infra/docker-compose.yml up -d

# Verify PostGIS is running
docker exec backend_postgres psql -U backend_user -d attendance -c "SELECT PostGIS_Version();"
```

### 6.2 Running the One-Time Setup

```bash
docker exec -i backend_postgres psql -U backend_user -d attendance <<'EOF'
CREATE EXTENSION IF NOT EXISTS postgis;
ALTER TABLE office_locations ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326);
UPDATE office_locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);
CREATE INDEX IF NOT EXISTS idx_office_geom ON office_locations USING GIST(geom);
ALTER TABLE user_locations ADD COLUMN IF NOT EXISTS location_name VARCHAR(500);
EOF
```

### 6.3 Verify the Spatial Index Works

```bash
docker exec backend_postgres psql -U backend_user -d attendance -c "
  EXPLAIN ANALYZE
  SELECT ST_DWithin(
    ST_SetSRID(ST_MakePoint(77.481, 28.468), 4326)::geography,
    geom::geography,
    300
  )
  FROM office_locations;
"
```

Look for `Index Scan using idx_office_geom` in the output — this confirms the GIST index is being used.

### 6.4 Troubleshooting

| Symptom | Check |
|---|---|
| `function st_setSRID(geometry, integer) does not exist` | PostGIS extension not enabled. Run `CREATE EXTENSION IF NOT EXISTS postgis;` |
| Column `geom` not found | Never created. Run the one-time setup above. |
| All geofence checks return "WFH" | `geom` may be `NULL`. Run `SELECT id, name, geom FROM office_locations;` — if `geom` is null, run the backfill UPDATE. |
| Slow queries (>100ms) | GIST index may be missing. Run `SELECT * FROM pg_indexes WHERE tablename = 'office_locations';` |
| `latitude`/`longitude` and `geom` out of sync | Someone updated lat/lng without triggering the sync. Run `UPDATE office_locations SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);` |
| Nominatim rate limiting | In-memory cache should handle this. If cache is cold after restart, it will call Nominatim once per unique ~100m GPS grid cell. |
| `location_name` is blank in `GET /api/location/{email}` | The employee never checked in after the `location_name` column was added. The next check-in will populate it. |

---

## 7. File Index

| File | Role |
|---|---|
| `infra/postgres/docker-compose.yml` | PostGIS 17-3.5 Docker image |
| `infra/.env` | Database credentials |
| `infra/backend/.env` | `DATABASE_URL` for backend → PostGIS |
| `apps/backend/pyproject.toml` | `geoalchemy2>=0.15.0` dependency |
| `apps/backend/app/core/config.py` | Settings class — `DATABASE_URL` default |
| `apps/backend/app/core/database.py` | Engine, session factory, `init_db()`, inline migrations |
| `apps/backend/app/core/models.py` | `declarative_base()` |
| `apps/backend/app/models/office_location.py` | Model — `geom = Column(Geometry("POINT", srid=4326))` |
| `apps/backend/app/models/attendance.py` | Model — stores `work_location_status` determined by PostGIS |
| `apps/backend/app/models/location.py` | Model — stores user GPS + `location_name` |
| `apps/backend/app/features/attendance/routes.py` | Check-in / check-out routes — PostGIS geofence + Nominatim geocode |
| `apps/backend/app/features/location/routes.py` | User location GET — PostGIS distance + geofence |
| `apps/backend/app/features/location/admin_routes.py` | Office admin CRUD — auto-syncs `geom` |
| `apps/backend/app/services/database/base_service.py` | Generic CRUD base class with `execute_raw()` |
| `apps/backend/app/services/database/location_service.py` | `upsert()` for `user_locations` |
| `apps/backend/app/services/database/office_location_service.py` | Thin wrapper around `BaseService` for office locations |
| `apps/backend/app/features/location/schemas/office_location.py` | Pydantic create/update/response schemas |
| `apps/backend/app/features/location/schemas/location.py` | Pydantic save/response schemas |

---

## 8. Query Reference

### Spatial — All query patterns used in this codebase

```sql
-- 1. Geofence Check (inside/outside)
SELECT ST_DWithin(
    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
    (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
    :radius
);

-- 2. Geofence + Distance (one query)
SELECT
    ST_DWithin(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
               o.geom::geography, o.radius_meters) AS inside,
    ROUND(ST_Distance(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                       o.geom::geography))::INT AS distance_m
FROM office_locations o
WHERE o.id = :office_id;

-- 3. Set/Update geometry from lat/lng
UPDATE office_locations
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);
-- WHERE id = :id  (use WHERE for single-row updates)
```
