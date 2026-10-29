from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every JAP table inherits from this, so Alembic sees it in Base.metadata."""
