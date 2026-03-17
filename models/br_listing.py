"""Brazilian market listing model - stores real market prices from Webmotors, OLX, etc."""

from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class BRMarketListing(Base):
    """A vehicle listing from a Brazilian marketplace (Webmotors, OLX, etc.)."""

    __tablename__ = "br_market_listings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50))  # webmotors, olx
    source_id: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(500))

    make: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    trim: Mapped[str | None] = mapped_column(String(200), nullable=True)

    price_brl: Mapped[float] = mapped_column(Float)
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    location_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<BRMarketListing {self.year} {self.make} {self.model} R${self.price_brl:,.0f} ({self.source})>"


class BRPriceSnapshot(Base):
    """Aggregated price snapshot for a make/model/year in the BR market."""

    __tablename__ = "br_price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    make: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)

    avg_price_brl: Mapped[float] = mapped_column(Float)
    min_price_brl: Mapped[float] = mapped_column(Float)
    max_price_brl: Mapped[float] = mapped_column(Float)
    median_price_brl: Mapped[float] = mapped_column(Float)
    listings_count: Mapped[int] = mapped_column(Integer)

    source: Mapped[str] = mapped_column(String(50))  # webmotors, olx, combined
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return (
            f"<BRPriceSnapshot {self.year} {self.make} {self.model} "
            f"avg=R${self.avg_price_brl:,.0f} ({self.listings_count} listings)>"
        )
