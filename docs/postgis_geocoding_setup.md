# PostGIS Reverse Geocoding Setup

Replace Nominatim HTTP calls with in-database PostGIS queries for
sub-millisecond location name resolution on every check-in / check-out.

## Why

| Metric | Nominatim (current) | PostGIS (target) |
|---|---|---|
| Latency | 1–5 seconds | <1 ms |
| Rate limit | 1 req/sec | None |
| External dependency | Yes (HTTP + DNS) | None |
| Offline capable | No | Yes |
| Fails if | Network down | Never |

## 1. Enable PostGIS extension

```sql
-- Connect to the attendance database and run:
CREATE EXTENSION IF NOT EXISTS postgis;
```

## 2. Add geometry column to office_locations

```sql
ALTER TABLE office_locations
  ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326);

UPDATE office_locations
  SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);

CREATE INDEX IF NOT EXISTS idx_office_geom
  ON office_locations USING GIST(geom);
```

## 3. Download OSM India data

Go to: <https://download.geofabrik.de/asia/india.html>

Download the latest `india-latest.osm.pbf` file (~700 MB).

## 4. Import into PostGIS

Install `osm2pgsql`:

```bash
apt-get install osm2pgsql   # Debian/Ubuntu
brew install osm2pgsql       # macOS
```

Run the import:

```bash
osm2pgsql \
  --create \
  --database attendance \
  --username backend_user \
  --host localhost \
  --slim \
  --drop \
  --hstore \
  india-latest.osm.pbf
```

Creates tables:
- `planet_osm_point`
- `planet_osm_polygon`
- `planet_osm_line`
- `planet_osm_roads`

## 5. Create spatial indexes

```sql
CREATE INDEX IF NOT EXISTS idx_osm_point_geom
  ON planet_osm_point USING GIST(way);

CREATE INDEX IF NOT EXISTS idx_osm_polygon_geom
  ON planet_osm_polygon USING GIST(way);
```

## 6. Replace `_reverse_geocode()` in Python

File: `apps/backend/app/features/attendance/routes.py`

```python
from sqlalchemy import text

NAMES_QUERY = text("""
    SELECT COALESCE(
        (SELECT name FROM planet_osm_point
         WHERE ST_DWithin(way, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326), 500)
           AND name IS NOT NULL
         ORDER BY ST_Distance(way, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326))
         LIMIT 1),
        (SELECT name FROM planet_osm_polygon
         WHERE ST_DWithin(way, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326), 500)
           AND name IS NOT NULL
         ORDER BY ST_Distance(way, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326))
         LIMIT 1)
    ) AS location_name
""")


def _reverse_geocode(db: Session, lat: float, lng: float) -> str:
    result = db.execute(NAMES_QUERY, {"lat": lat, "lng": lng}).scalar()
    return result or ""
```

Check-in and check-out must pass `db` to `_reverse_geocode`:

```python
# In check_in / check_out
check_in_location_name = _reverse_geocode(db, payload.latitude, payload.longitude)
```

## 7. Geofence with PostGIS (optional)

Replace Python haversine with `ST_DWithin`:

```sql
SELECT ST_DWithin(
    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326),
    (SELECT geom FROM office_locations WHERE id = :office_id),
    :radius_meters
)
```

Returns `true` if inside geofence.

## Data refresh

Re-run `osm2pgsql` every 6 months to keep places up-to-date.
