"""
Database setup for SQLAlchemy ORM.

This module configures the connection to PostgreSQL using SQLAlchemy.
It provides:
- engine: The database engine that manages connections
- SessionLocal: A session factory for creating database sessions
- init_db(): Creates all tables defined in models
- get_db(): FastAPI dependency that provides a database session

Note: Currently the database is mainly used by Keycloak (user data).
This backend stores sessions in PostgreSQL.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.core.models import Base


# Connection pooling: pgbouncer (transaction mode) owns pooling, so the
# client holds no pool by default (NullPool). QueuePool math for reference:
# workers x (pool_size + max_overflow) must stay under Postgres max_connections.
# Configure via DB_POOL_CLASS=null|queue, DB_POOL_SIZE, DB_MAX_OVERFLOW.
_pool_kwargs: dict = {"pool_pre_ping": True, "poolclass": NullPool}
if settings.DB_POOL_CLASS == "queue":
    from sqlalchemy.pool import QueuePool
    _pool_kwargs = {
        "pool_pre_ping": True,
        "poolclass": QueuePool,
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
    }
engine = create_engine(settings.DATABASE_URL, **_pool_kwargs)

# Create a session factory - each session is a new database transaction
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all database tables based on SQLAlchemy models."""
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Run any pending schema migrations."""
    with engine.connect() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='shifts' AND column_name='project_id'
                ) THEN
                    ALTER TABLE shifts ADD COLUMN project_id INTEGER;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='system_settings' AND column_name='website_url'
                ) THEN
                    ALTER TABLE system_settings ADD COLUMN website_url VARCHAR(255);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='shifts' AND column_name='checkin_reminder_sent'
                ) THEN
                    ALTER TABLE shifts ADD COLUMN checkin_reminder_sent BOOLEAN DEFAULT FALSE;
                END IF;
            END $$;
        """))
        conn.commit()

        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='checkout_reminder_grace_hours') THEN
                    ALTER TABLE system_settings ADD COLUMN checkout_reminder_grace_hours INTEGER DEFAULT 2;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='checkin_reminder_delay_minutes') THEN
                    ALTER TABLE system_settings ADD COLUMN checkin_reminder_delay_minutes INTEGER DEFAULT 30;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='auto_checkout_enabled') THEN
                    ALTER TABLE system_settings ADD COLUMN auto_checkout_enabled BOOLEAN DEFAULT TRUE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='auto_checkout_cutoff_time') THEN
                    ALTER TABLE system_settings ADD COLUMN auto_checkout_cutoff_time TIME DEFAULT '22:00';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='background_content_type') THEN
                    ALTER TABLE system_settings ADD COLUMN background_content_type VARCHAR(100);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='background_overlay_color') THEN
                    ALTER TABLE system_settings ADD COLUMN background_overlay_color VARCHAR(7);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='trademark') THEN
                    ALTER TABLE system_settings ADD COLUMN trademark VARCHAR(255) DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='website_url') THEN
                    ALTER TABLE system_settings ADD COLUMN website_url VARCHAR(255) DEFAULT '';
                END IF;
            END $$;
        """))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS leave_types (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code VARCHAR(20) UNIQUE NOT NULL,
                name VARCHAR(100) UNIQUE NOT NULL,
                is_paid BOOLEAN NOT NULL DEFAULT TRUE,
                carry_forward_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                carry_forward_cap FLOAT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.commit()

        conn.execute(text("""
            ALTER TABLE leave_types DROP COLUMN IF EXISTS description
        """))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employee_leave_balances (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                keycloak_user_id VARCHAR(255) NOT NULL,
                leave_type_id UUID NOT NULL REFERENCES leave_types(id),
                fiscal_year INTEGER NOT NULL,
                carry_forward DOUBLE PRECISION NOT NULL DEFAULT 0,
                adjustment DOUBLE PRECISION NOT NULL DEFAULT 0,
                modified_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (keycloak_user_id, leave_type_id, fiscal_year)
            )
        """))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employee_leave_balance_monthly (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                leave_balance_id UUID NOT NULL REFERENCES employee_leave_balances(id) ON DELETE CASCADE,
                month INTEGER NOT NULL,
                accrued DOUBLE PRECISION NOT NULL DEFAULT 0,
                UNIQUE (leave_balance_id, month)
            )
        """))
        conn.commit()

        conn.execute(text("""
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;
            UPDATE sessions SET last_activity_at = NOW() WHERE last_activity_at IS NULL;
        """))
        conn.commit()

        conn.execute(text("""
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS logged_out_at TIMESTAMPTZ;
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS display_id VARCHAR(36);
            CREATE UNIQUE INDEX IF NOT EXISTS ix_sessions_display_id ON sessions(display_id);
        """))
        conn.commit()

        conn.execute(text("""
            DO $$
            BEGIN
                ALTER TABLE leaves ALTER COLUMN leave_type TYPE VARCHAR(20);
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='leaves' AND column_name='leave_type_id'
                ) THEN
                    ALTER TABLE leaves ADD COLUMN leave_type_id UUID REFERENCES leave_types(id);
                END IF;
                UPDATE leaves l
                   SET leave_type_id = lt.id
                  FROM leave_types lt
                 WHERE lt.code = l.leave_type
                   AND l.leave_type_id IS NULL;
                CREATE INDEX IF NOT EXISTS ix_leaves_leave_type_id ON leaves(leave_type_id);
            END $$;
        """))
        conn.commit()

        conn.execute(text("""
            DROP TABLE IF EXISTS leave_balances
        """))
        conn.commit()

        conn.execute(text("""
            DROP TABLE IF EXISTS recordings
        """))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shift_attendance (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                shift_id UUID NOT NULL UNIQUE REFERENCES shifts(id),
                keycloak_user_id VARCHAR(255) NOT NULL,
                attendance_date DATE NOT NULL,
                shift_code VARCHAR(50),
                check_in_time TIMESTAMPTZ,
                check_in_lat DOUBLE PRECISION,
                check_in_lng DOUBLE PRECISION,
                check_in_location_name VARCHAR(500),
                check_out_time TIMESTAMPTZ,
                check_out_lat DOUBLE PRECISION,
                check_out_lng DOUBLE PRECISION,
                check_out_location_name VARCHAR(500),
                work_location_status VARCHAR(50),
                is_late BOOLEAN NOT NULL DEFAULT FALSE,
                status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
                total_hours DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.commit()

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_shift_attendance_keycloak ON shift_attendance(keycloak_user_id);
            CREATE INDEX IF NOT EXISTS idx_shift_attendance_date ON shift_attendance(attendance_date);
        """))
        conn.commit()

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_shifts_work_location_status ON shifts(work_location_status);
            CREATE INDEX IF NOT EXISTS idx_shifts_project_id ON shifts(project_id);
            CREATE INDEX IF NOT EXISTS idx_attendance_work_location_status ON attendance(work_location_status);
            CREATE INDEX IF NOT EXISTS idx_leaves_leave_type ON leaves(leave_type);
            CREATE INDEX IF NOT EXISTS idx_employee_master_status ON employee_master(status);
            CREATE INDEX IF NOT EXISTS idx_wfh_requests_status ON wfh_requests(status);
            CREATE INDEX IF NOT EXISTS idx_wfh_requests_start_date ON wfh_requests(start_date);
        """))
        conn.commit()

        # login_attempts table for per-IP brute-force protection
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                identifier VARCHAR(255) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                failure_count INTEGER NOT NULL DEFAULT 0,
                first_failure_at TIMESTAMPTZ,
                blocked_until TIMESTAMPTZ,
                last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT chk_login_attempts_kind CHECK (kind IN ('ip', 'email'))
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_login_attempts_identifier_kind ON login_attempts(identifier, kind)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_login_attempts_kind_blocked ON login_attempts(kind, blocked_until)"))
        conn.commit()

        # Trusted device fingerprints for new-device login detection
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trusted_device_hashes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                keycloak_user_id VARCHAR(255) NOT NULL,
                fingerprint VARCHAR(64) NOT NULL,
                device_label VARCHAR(255),
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (keycloak_user_id, fingerprint)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trusted_device_user ON trusted_device_hashes(keycloak_user_id)"))
        conn.execute(text("ALTER TABLE trusted_device_hashes ADD COLUMN IF NOT EXISTS device_token_hash VARCHAR(64)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trusted_device_token ON trusted_device_hashes(device_token_hash)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS login_alert_cooldown (
                keycloak_user_id VARCHAR(255) PRIMARY KEY,
                last_sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.commit()

        # Notifications table for in-app user notifications (replaces old schema)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id         UUID NOT NULL REFERENCES employee_master(id) ON DELETE CASCADE,
                category        VARCHAR(32) NOT NULL,
                title           VARCHAR(255) NOT NULL,
                message         TEXT NOT NULL,
                priority        VARCHAR(16) NOT NULL DEFAULT 'normal',
                actor_id        UUID REFERENCES employee_master(id) ON DELETE SET NULL,
                actor_name      VARCHAR(255),
                link            VARCHAR(512),
                extra           JSONB NOT NULL DEFAULT '{}'::jsonb,
                read_at         TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications (user_id, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications (user_id, created_at DESC) WHERE read_at IS NULL"))
        conn.commit()

        # Master states table (scopes holidays by state). See docs/features/state-level-holidays.md
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS states (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                country_code VARCHAR(2) NOT NULL,
                code VARCHAR(10) NOT NULL UNIQUE,
                short_code VARCHAR(10),
                gst_code VARCHAR(2),
                name VARCHAR(100) NOT NULL,
                type VARCHAR(20) NOT NULL DEFAULT 'state',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_states_country ON states(country_code)"))
        conn.commit()

        # Seed India states + union territories (idempotent).
        conn.execute(text("""
            INSERT INTO states (country_code, code, short_code, gst_code, name, type) VALUES
                ('IN','IN-AP','AP','37','Andhra Pradesh','state'),
                ('IN','IN-AR','AR','12','Arunachal Pradesh','state'),
                ('IN','IN-AS','AS','18','Assam','state'),
                ('IN','IN-BR','BR','10','Bihar','state'),
                ('IN','IN-CG','CG','22','Chhattisgarh','state'),
                ('IN','IN-GA','GA','30','Goa','state'),
                ('IN','IN-GJ','GJ','24','Gujarat','state'),
                ('IN','IN-HR','HR','06','Haryana','state'),
                ('IN','IN-HP','HP','02','Himachal Pradesh','state'),
                ('IN','IN-JH','JH','20','Jharkhand','state'),
                ('IN','IN-KA','KA','29','Karnataka','state'),
                ('IN','IN-KL','KL','32','Kerala','state'),
                ('IN','IN-MP','MP','23','Madhya Pradesh','state'),
                ('IN','IN-MH','MH','27','Maharashtra','state'),
                ('IN','IN-MN','MN','14','Manipur','state'),
                ('IN','IN-ML','ML','17','Meghalaya','state'),
                ('IN','IN-MZ','MZ','15','Mizoram','state'),
                ('IN','IN-NL','NL','13','Nagaland','state'),
                ('IN','IN-OD','OD','21','Odisha','state'),
                ('IN','IN-PB','PB','03','Punjab','state'),
                ('IN','IN-RJ','RJ','08','Rajasthan','state'),
                ('IN','IN-SK','SK','11','Sikkim','state'),
                ('IN','IN-TN','TN','33','Tamil Nadu','state'),
                ('IN','IN-TS','TS','36','Telangana','state'),
                ('IN','IN-TR','TR','16','Tripura','state'),
                ('IN','IN-UP','UP','09','Uttar Pradesh','state'),
                ('IN','IN-UT','UT','05','Uttarakhand','state'),
                ('IN','IN-WB','WB','19','West Bengal','state'),
                ('IN','IN-AN','AN','35','Andaman and Nicobar Islands','union_territory'),
                ('IN','IN-CH','CH','04','Chandigarh','union_territory'),
                ('IN','IN-DH','DH','26','Dadra and Nagar Haveli and Daman and Diu','union_territory'),
                ('IN','IN-DL','DL','07','Delhi','union_territory'),
                ('IN','IN-JK','JK','01','Jammu and Kashmir','union_territory'),
                ('IN','IN-LA','LA','38','Ladakh','union_territory'),
                ('IN','IN-LD','LD','31','Lakshadweep','union_territory'),
                ('IN','IN-PY','PY','34','Puducherry','union_territory')
            ON CONFLICT (code) DO NOTHING
        """))
        conn.commit()

        # Holidays: region = state code (states.code), NULL = national/all.
        conn.execute(text("""
            UPDATE holidays SET region = NULL
             WHERE region IS NOT NULL
               AND region NOT IN (SELECT code FROM states);
        """))
        conn.execute(text("ALTER TABLE holidays ALTER COLUMN region TYPE VARCHAR(10)"))
        conn.execute(text("ALTER TABLE holidays ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()"))
        conn.execute(text("ALTER TABLE holidays ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"))
        conn.execute(text("ALTER TABLE holidays DROP CONSTRAINT IF EXISTS uq_holiday_per_region"))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_holidays_scope
              ON holidays (country_code, COALESCE(region, ''), holiday_date, holiday_name)
        """))
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_holidays_region') THEN
                    ALTER TABLE holidays ADD CONSTRAINT fk_holidays_region
                        FOREIGN KEY (region) REFERENCES states(code)
                        ON UPDATE CASCADE ON DELETE RESTRICT;
                END IF;
            END $$;
        """))
        conn.commit()

        # State link for offices + employees (scopes holidays by state).
        conn.execute(text("ALTER TABLE office_locations ADD COLUMN IF NOT EXISTS state_code VARCHAR(10)"))
        conn.execute(text("ALTER TABLE employee_master ADD COLUMN IF NOT EXISTS state_code VARCHAR(10)"))
        conn.commit()

        # Backfill office states from city (only when empty).
        conn.execute(text("""
            UPDATE office_locations SET state_code = CASE
                WHEN city ILIKE '%delhi%' THEN 'IN-DL'
                WHEN city ILIKE '%hyderabad%' OR city ILIKE '%medchal%' THEN 'IN-TS'
                WHEN city ILIKE '%gujrat%' OR city ILIKE '%gujarat%' OR city ILIKE '%bharuch%' THEN 'IN-GJ'
                WHEN city ILIKE '%bengaluru%' OR city ILIKE '%bangalore%' THEN 'IN-KA'
                WHEN city ILIKE '%gurgaon%' OR city ILIKE '%gurugram%' THEN 'IN-HR'
                WHEN city ILIKE '%noida%' THEN 'IN-UP'
                ELSE state_code
            END
            WHERE state_code IS NULL AND city IS NOT NULL
        """))
        conn.commit()

        # Employees inherit their office's state (only when empty).
        conn.execute(text("""
            UPDATE employee_master em SET state_code = ol.state_code
            FROM office_locations ol
            WHERE em.location_id = ol.id AND em.state_code IS NULL AND ol.state_code IS NOT NULL
        """))
        conn.commit()

        # FKs to states(code).
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_office_locations_state') THEN
                    ALTER TABLE office_locations ADD CONSTRAINT fk_office_locations_state
                        FOREIGN KEY (state_code) REFERENCES states(code)
                        ON UPDATE CASCADE ON DELETE SET NULL;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_employee_master_state') THEN
                    ALTER TABLE employee_master ADD CONSTRAINT fk_employee_master_state
                        FOREIGN KEY (state_code) REFERENCES states(code)
                        ON UPDATE CASCADE ON DELETE SET NULL;
                END IF;
            END $$;
        """))
        conn.commit()

        # Attendance ↔ shift/office links: tie a punch to its exact roster shift
        # and record the actual (geofenced) office separately from the expected one.
        conn.execute(text("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS shift_id UUID"))
        conn.execute(text("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS actual_location_id UUID"))
        conn.commit()
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_attendance_shift') THEN
                    ALTER TABLE attendance ADD CONSTRAINT fk_attendance_shift
                        FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE SET NULL;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_attendance_actual_location') THEN
                    ALTER TABLE attendance ADD CONSTRAINT fk_attendance_actual_location
                        FOREIGN KEY (actual_location_id) REFERENCES office_locations(id) ON DELETE SET NULL;
                END IF;
            END $$;
        """))
        conn.commit()
        # Backfill: link existing punches to their roster shift, and set the
        # actual office for office days (WFH rows keep NULL actual).
        conn.execute(text("""
            UPDATE attendance a SET shift_id = s.id
            FROM shifts s
            WHERE a.shift_id IS NULL
              AND s.keycloak_user_id = a.keycloak_user_id
              AND s.date = a.attendance_date
              AND s.shift_code = a.shift_code
        """))
        conn.execute(text("""
            UPDATE attendance a SET actual_location_id = a.location_id
            WHERE a.actual_location_id IS NULL
              AND a.work_location_status = 'OFFICE'
              AND a.location_id IS NOT NULL
        """))
        conn.commit()

        # Per-shift punches (multi-shift source of truth). See
        # docs/multi-shift-punch-plan.md. Created + backfilled; not yet wired
        # into the write paths (phase 1 = foundation, no behavior change).
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS attendance_punches (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                shift_id UUID UNIQUE REFERENCES shifts(id) ON DELETE SET NULL,
                keycloak_user_id VARCHAR(255) NOT NULL,
                attendance_date DATE NOT NULL,
                shift_code VARCHAR(50) REFERENCES shift_master(shift_code),
                check_in_time TIMESTAMPTZ,
                check_in_lat NUMERIC(10,8),
                check_in_lng NUMERIC(11,8),
                check_in_location_name VARCHAR(500),
                check_in_location_id UUID REFERENCES office_locations(id) ON DELETE SET NULL,
                check_out_time TIMESTAMPTZ,
                check_out_lat NUMERIC(10,8),
                check_out_lng NUMERIC(11,8),
                check_out_location_name VARCHAR(500),
                check_out_location_id UUID REFERENCES office_locations(id) ON DELETE SET NULL,
                work_location_status VARCHAR(20),
                is_late BOOLEAN NOT NULL DEFAULT false,
                total_hours NUMERIC(5,2),
                status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
                is_manual_entry BOOLEAN NOT NULL DEFAULT false,
                modified_by VARCHAR(255),
                remarks VARCHAR(500),
                is_synced BOOLEAN NOT NULL DEFAULT false,
                server_received_at TIMESTAMPTZ,
                is_deleted BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_punches_user_date ON attendance_punches (keycloak_user_id, attendance_date)"))
        # One ad-hoc punch (no shift) per user/day.
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_punches_adhoc
              ON attendance_punches (keycloak_user_id, attendance_date)
              WHERE shift_id IS NULL
        """))
        conn.commit()
        # Backfill: one punch per existing attendance row (idempotent).
        conn.execute(text("""
            INSERT INTO attendance_punches (
                shift_id, keycloak_user_id, attendance_date, shift_code,
                check_in_time, check_in_lat, check_in_lng, check_in_location_name, check_in_location_id,
                check_out_time, check_out_lat, check_out_lng, check_out_location_name,
                work_location_status, is_late, total_hours, status,
                is_manual_entry, modified_by, remarks, is_synced, server_received_at, is_deleted,
                created_at, updated_at
            )
            SELECT
                a.shift_id, a.keycloak_user_id, a.attendance_date, a.shift_code,
                a.check_in_time, a.check_in_lat, a.check_in_lng, a.check_in_location_name, a.actual_location_id,
                a.check_out_time, a.check_out_lat, a.check_out_lng, a.check_out_location_name,
                a.work_location_status::text, a.is_late, a.total_hours,
                CASE WHEN a.check_out_time IS NOT NULL THEN 'completed'
                     WHEN a.check_in_time IS NOT NULL THEN 'in_progress'
                     ELSE 'absent' END,
                a.is_manual_entry, a.modified_by, a.remarks, a.is_synced, a.server_received_at, a.is_deleted,
                a.created_at, a.updated_at
            FROM attendance a
            WHERE COALESCE(a.is_deleted, false) = false
              AND NOT EXISTS (
                  SELECT 1 FROM attendance_punches p
                  WHERE p.keycloak_user_id = a.keycloak_user_id
                    AND p.attendance_date = a.attendance_date
                    AND p.shift_id IS NOT DISTINCT FROM a.shift_id
              )
        """))
        conn.commit()

        # attendance.total_hours: generated span -> stored column so the rollup
        # can write the SUM of punch hours (multi-shift days with gaps).
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'attendance' AND column_name = 'total_hours'
                      AND is_generated = 'ALWAYS'
                ) THEN
                    ALTER TABLE attendance ALTER COLUMN total_hours DROP EXPRESSION;
                END IF;
            END $$;
        """))
        conn.commit()

        # Device proof: which device/agent performed each check-in/out.
        conn.execute(text("ALTER TABLE attendance_punches ADD COLUMN IF NOT EXISTS check_in_device VARCHAR(100)"))
        conn.execute(text("ALTER TABLE attendance_punches ADD COLUMN IF NOT EXISTS check_out_device VARCHAR(100)"))
        conn.execute(text("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS check_in_device VARCHAR(100)"))
        conn.execute(text("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS check_out_device VARCHAR(100)"))
        conn.commit()

        # Shift-master-level timing policy. Was company-wide / hardcoded; now the
        # shift is the single source of truth. Backfills from the current company
        # row so behavior is unchanged on the first deploy, then enforces
        # NOT NULL. See docs/shift-level-timing-policy.md.
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'shift_master' AND column_name = 'auto_checkout_enabled'
                ) THEN
                    ALTER TABLE shift_master ADD COLUMN auto_checkout_enabled BOOLEAN;
                    ALTER TABLE shift_master ADD COLUMN auto_checkout_grace_minutes INTEGER;
                    ALTER TABLE shift_master ADD COLUMN grace_minutes INTEGER;
                    ALTER TABLE shift_master ADD COLUMN checkin_reminder_delay_minutes INTEGER;
                    ALTER TABLE shift_master ADD COLUMN checkout_reminder_grace_hours INTEGER;
                    ALTER TABLE shift_master ADD COLUMN early_checkin_limit_minutes INTEGER;
                    ALTER TABLE shift_master ADD COLUMN checkin_cutoff_minutes INTEGER;
                    ALTER TABLE shift_master ADD COLUMN half_day_minutes INTEGER;

                    UPDATE shift_master SET
                        auto_checkout_enabled = COALESCE((SELECT auto_checkout_enabled FROM system_settings WHERE id = 'company'), true),
                        auto_checkout_grace_minutes = 60,
                        grace_minutes = COALESCE((SELECT grace_minutes FROM system_settings WHERE id = 'company'), 15),
                        checkin_reminder_delay_minutes = COALESCE((SELECT checkin_reminder_delay_minutes FROM system_settings WHERE id = 'company'), 30),
                        checkout_reminder_grace_hours = COALESCE((SELECT checkout_reminder_grace_hours FROM system_settings WHERE id = 'company'), 2),
                        early_checkin_limit_minutes = 60,
                        checkin_cutoff_minutes = 120,
                        half_day_minutes = 240;

                    ALTER TABLE shift_master ALTER COLUMN auto_checkout_enabled SET DEFAULT true;
                    ALTER TABLE shift_master ALTER COLUMN auto_checkout_enabled SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN auto_checkout_grace_minutes SET DEFAULT 60;
                    ALTER TABLE shift_master ALTER COLUMN auto_checkout_grace_minutes SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN grace_minutes SET DEFAULT 15;
                    ALTER TABLE shift_master ALTER COLUMN grace_minutes SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN checkin_reminder_delay_minutes SET DEFAULT 30;
                    ALTER TABLE shift_master ALTER COLUMN checkin_reminder_delay_minutes SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN checkout_reminder_grace_hours SET DEFAULT 2;
                    ALTER TABLE shift_master ALTER COLUMN checkout_reminder_grace_hours SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN early_checkin_limit_minutes SET DEFAULT 60;
                    ALTER TABLE shift_master ALTER COLUMN early_checkin_limit_minutes SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN checkin_cutoff_minutes SET DEFAULT 120;
                    ALTER TABLE shift_master ALTER COLUMN checkin_cutoff_minutes SET NOT NULL;
                    ALTER TABLE shift_master ALTER COLUMN half_day_minutes SET DEFAULT 240;
                    ALTER TABLE shift_master ALTER COLUMN half_day_minutes SET NOT NULL;
                END IF;
            END $$;
        """))
        conn.commit()

        # work_location enum: add OUT_OF_OFFICE for far check-ins on office shifts.
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction.
        auto_conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        auto_conn.execute(text("ALTER TYPE work_location ADD VALUE IF NOT EXISTS 'OUT_OF_OFFICE'"))
        auto_conn.close()


def get_db():
    """
    FastAPI dependency that provides a database session.
    
    How it works:
    1. Creates a new database session
    2. Yields it to the endpoint function
    3. Closes the session after the request is done (even if there's an error)
    
    Usage:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
