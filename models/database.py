import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# Use DATABASE_URL env var for cloud PostgreSQL, fallback to local SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Streamlit secrets support
if not DATABASE_URL:
    try:
        import streamlit as st
        DATABASE_URL = st.secrets.get("DATABASE_URL", "")
    except Exception:
        pass

if DATABASE_URL:
    # Fix Heroku/Supabase style postgres:// -> postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
else:
    DB_PATH = Path(__file__).parent.parent / "carauctions.db"
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables."""
    from models.vehicle import Vehicle  # noqa: F401
    from models.deal import Deal, AlertLog  # noqa: F401
    from models.br_listing import BRMarketListing, BRPriceSnapshot  # noqa: F401
    from models.watchlist import WatchlistItem, UserPreferences  # noqa: F401
    from models.monitored_auction import MonitoredAuction  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()
