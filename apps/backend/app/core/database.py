"""
Database setup for SQLAlchemy ORM.

This module configures the connection to PostgreSQL using SQLAlchemy.
It provides:
- engine: The database engine that manages connections
- SessionLocal: A session factory for creating database sessions
- init_db(): Creates all tables defined in models
- get_db(): FastAPI dependency that provides a database session

Note: Currently the database is mainly used by Keycloak (user data).
This backend stores sessions in Redis, not PostgreSQL.
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
            END $$;
        """))

        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='attendance' AND column_name='is_synced'
                ) THEN
                    ALTER TABLE attendance ADD COLUMN is_synced BOOLEAN NOT NULL DEFAULT false;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='attendance' AND column_name='server_received_at'
                ) THEN
                    ALTER TABLE attendance ADD COLUMN server_received_at TIMESTAMPTZ;
                END IF;
            END $$;
        """))
        conn.commit()

        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='employee_master' AND column_name='profile_picture_url'
                ) THEN
                    ALTER TABLE employee_master ADD COLUMN profile_picture_url VARCHAR(500);
                END IF;
            END $$;
        """))
        conn.commit()

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_leaves_approval_status ON leaves(approval_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_leaves_start_date ON leaves(start_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_leaves_end_date ON leaves(end_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_shifts_end_date ON shifts(end_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_shifts_shift_code ON shifts(shift_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_attendance_check_out_time ON attendance(check_out_time)"))
        conn.commit()

        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leaves' AND column_name='cancelled_at') THEN
                    ALTER TABLE leaves ADD COLUMN cancelled_at TIMESTAMPTZ;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leaves' AND column_name='cancelled_by') THEN
                    ALTER TABLE leaves ADD COLUMN cancelled_by VARCHAR(255);
                END IF;
            END $$;
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
