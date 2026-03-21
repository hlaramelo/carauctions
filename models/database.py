import os
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# Use DATABASE_URL env var for cloud PostgreSQL, fallback to local SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Streamlit secrets support
if not DATABASE_URL:
    try:
        import streamlit as st
        DATABASE_URL = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError, Exception):
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


def _run_migrations():
    """Add missing columns to existing tables (lightweight migration).

    SQLAlchemy's create_all() only creates NEW tables, it never alters
    existing ones.  This function inspects the live schema and ADDs any
    columns that are defined in the models but missing in the database.
    """
    migration_columns = [
        # ---- watchlist ----
        ("watchlist", "vehicle_id", "INTEGER"),
        ("watchlist", "vin", "VARCHAR(17)"),
        ("watchlist", "make", "VARCHAR(100)"),
        ("watchlist", "model", "VARCHAR(100)"),
        ("watchlist", "year", "INTEGER"),
        ("watchlist", "year_min", "INTEGER"),
        ("watchlist", "year_max", "INTEGER"),
        ("watchlist", "keywords", "VARCHAR(500)"),
        ("watchlist", "max_price_usd", "FLOAT"),
        ("watchlist", "notes", "TEXT"),
        ("watchlist", "chat_id", "VARCHAR(50)"),
        ("watchlist", "is_active", "BOOLEAN DEFAULT TRUE"),
        ("watchlist", "notified_vehicle_ids", "TEXT"),
        ("watchlist", "created_at", "TIMESTAMP"),
        # ---- vehicles ----
        ("vehicles", "trim", "VARCHAR(200)"),
        ("vehicles", "vin", "VARCHAR(17)"),
        ("vehicles", "buy_now_price_usd", "FLOAT"),
        ("vehicles", "reserve_met", "BOOLEAN"),
        ("vehicles", "damage_description", "TEXT"),
        ("vehicles", "location_state", "VARCHAR(2)"),
        ("vehicles", "location_city", "VARCHAR(100)"),
        ("vehicles", "engine_cc", "INTEGER"),
        ("vehicles", "auction_end", "TIMESTAMP"),
        ("vehicles", "br_price_avg", "FLOAT"),
        ("vehicles", "br_price_min", "FLOAT"),
        ("vehicles", "br_price_max", "FLOAT"),
        ("vehicles", "br_listings_count", "INTEGER"),
        ("vehicles", "fipe_price_brl", "FLOAT"),
        # ---- monitored_auctions ----
        ("monitored_auctions", "vehicle_id", "INTEGER"),
        ("monitored_auctions", "last_checked_at", "TIMESTAMP"),
        ("monitored_auctions", "chat_id", "VARCHAR(50)"),
        # ---- user_preferences ----
        ("user_preferences", "alerts_paused", "BOOLEAN DEFAULT FALSE"),
        ("user_preferences", "custom_filters", "TEXT"),
    ]
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        with engine.begin() as conn:
            for table, column, col_type in migration_columns:
                if table not in tables:
                    continue
                existing = [c["name"] for c in inspector.get_columns(table)]
                if column not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                    ))
                    logger.info(f"[DB] Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"[DB] Migration check failed (non-fatal): {e}")


def init_db():
    """Create all tables and run lightweight migrations."""
    from models.vehicle import Vehicle  # noqa: F401
    from models.deal import Deal, AlertLog  # noqa: F401
    from models.br_listing import BRMarketListing, BRPriceSnapshot  # noqa: F401
    from models.watchlist import WatchlistItem, UserPreferences  # noqa: F401
    from models.monitored_auction import MonitoredAuction  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_migrations()


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()
