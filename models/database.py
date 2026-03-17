from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = Path(__file__).parent.parent / "carauctions.db"


class Base(DeclarativeBase):
    pass


engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables."""
    from models.vehicle import Vehicle  # noqa: F401
    from models.deal import Deal, AlertLog  # noqa: F401
    from models.br_listing import BRMarketListing, BRPriceSnapshot  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()
