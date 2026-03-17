from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50))  # bat, copart, etc.
    source_id: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(500))

    make: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    year: Mapped[int] = mapped_column(Integer)
    trim: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)

    current_bid_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_now_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    reserve_met: Mapped[bool | None] = mapped_column(nullable=True)

    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    damage_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    image_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    engine_cc: Mapped[int | None] = mapped_column(Integer, nullable=True)

    auction_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # BR market data (populated by FIPE/market scrapers)
    br_price_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    br_price_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    br_price_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    br_listings_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fipe_price_brl: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<Vehicle {self.year} {self.make} {self.model} ({self.source})>"


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(Integer, index=True)
    price_usd: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
