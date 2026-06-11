import logging
from typing import TypeVar, Generic, Type, Any, List, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import select, update as sql_update, delete as sql_delete, text
from sqlalchemy.exc import SQLAlchemyError

# Set up a generic logger for database operations
logger = logging.getLogger(__name__)

# Define a generic type variable for SQLAlchemy models
T = TypeVar("T")

class BaseService(Generic[T]):
    """
    A secure base service/repository layer for SQLAlchemy operations.
    
    This class enforces the use of SQLAlchemy 2.0 paradigms, which utilize
    parameterized queries to prevent SQL injection vulnerabilities. It also
    safely handles database exceptions to prevent leaking schema or raw SQL
    error details to the client.
    """

    def __init__(self, db_session: Session):
        """
        Initialize the service with a database session.
        """
        self.db = db_session

    def fetch_one(self, model: Type[T], **filters: Any) -> Optional[T]:
        """
        Securely fetches a single record matching the given filters.
        """
        try:
            stmt = select(model).filter_by(**filters)
            result = self.db.execute(stmt)
            return result.scalars().first()
        except SQLAlchemyError as e:
            logger.error(f"Database error during fetch_one for model {model.__name__}: {e}")
            raise RuntimeError("An internal error occurred while retrieving data.")

    def fetch_all(self, model: Type[T], limit: int = 100, offset: int = 0, **filters: Any) -> List[T]:
        """
        Securely fetches multiple records matching the filters with pagination.
        """
        try:
            stmt = select(model).filter_by(**filters).limit(limit).offset(offset)
            result = self.db.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as e:
            logger.error(f"Database error during fetch_all for model {model.__name__}: {e}")
            raise RuntimeError("An internal error occurred while retrieving data.")

    def create(self, model: Type[T], **data: Any) -> T:
        """
        Securely inserts a new record into the database.
        """
        try:
            instance = model(**data)
            self.db.add(instance)
            self.db.commit()
            self.db.refresh(instance)
            return instance
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error during create for model {model.__name__}: {e}")
            raise RuntimeError("An internal error occurred while saving data.")

    def update(self, model: Type[T], record_id: Any, **data: Any) -> Optional[T]:
        """
        Securely updates an existing record in the database.
        Assumes the model has a primary key named 'id'.
        """
        try:
            stmt = sql_update(model).where(model.id == record_id).values(**data).returning(model)
            result = self.db.execute(stmt)
            self.db.commit()
            return result.scalars().first()
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error during update for model {model.__name__}: {e}")
            raise RuntimeError("An internal error occurred while updating data.")

    def delete(self, model: Type[T], record_id: Any) -> bool:
        """
        Securely deletes a record from the database.
        Assumes the model has a primary key named 'id'.
        """
        try:
            stmt = sql_delete(model).where(model.id == record_id)
            result = self.db.execute(stmt)
            self.db.commit()
            return result.rowcount > 0
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error during delete for model {model.__name__}: {e}")
            raise RuntimeError("An internal error occurred while deleting data.")

    def execute_raw(self, sql_query: str, params: Dict[str, Any] = None) -> Any:
        """
        Executes a raw SQL query safely using parameterized inputs.
        
        WARNING: NEVER use Python string formatting (e.g., f-strings or .format()) 
        to inject variables into `sql_query`. This will lead to SQL injection.
        Always pass variables via the `params` dictionary.
        
        Example:
            # VULNERABLE (DO NOT DO THIS):
            service.execute_raw(f"SELECT * FROM users WHERE name = '{name}'")
            
            # SECURE (DO THIS):
            service.execute_raw("SELECT * FROM users WHERE name = :name", {"name": "John"})
        """
        try:
            stmt = text(sql_query)
            result = self.db.execute(stmt, params or {})
            self.db.commit()
            
            # For SELECT queries, you can call result.fetchall()
            # For INSERT/UPDATE/DELETE, result.rowcount gives affected rows
            return result
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error during raw query execution: {e}")
            raise RuntimeError("An internal error occurred while executing raw query.")
