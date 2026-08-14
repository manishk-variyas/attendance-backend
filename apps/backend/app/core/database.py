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
from app.core.config import settings
from app.core.models import Base


# Create the database engine with connection pooling
# pool_pre_ping=True checks connections are alive before using them
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

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
                    WHERE table_name='shifts' AND column_name='project_name'
                ) THEN
                    ALTER TABLE shifts ADD COLUMN project_name VARCHAR(255);
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
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='auto_checkout_enabled') THEN
                    ALTER TABLE system_settings ADD COLUMN auto_checkout_enabled BOOLEAN DEFAULT TRUE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='system_settings' AND column_name='auto_checkout_cutoff_time') THEN
                    ALTER TABLE system_settings ADD COLUMN auto_checkout_cutoff_time TIME DEFAULT '22:00';
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
