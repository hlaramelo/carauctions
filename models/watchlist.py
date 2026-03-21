"""Watchlist and user preferences models."""

from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class WatchlistItem(Base):
    """A vehicle being watched by the user (tracked by VIN or spec).

    Supports flexible matching:
    - By VIN (exact match)
    - By make/model with year range (e.g., Porsche 911 1993-1997)
    - With keywords that match against model, trim, or listing title
      (e.g., "993 Turbo" to find Porsche 911 993 Turbo listings)
    - With max price filter (e.g., max:30000 to only match bids under $30k)
    """

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)  # comma-separated
    max_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    chat_id: Mapped[str] = mapped_column(String(50), index=True)  # Telegram chat ID
    is_active: Mapped[bool] = mapped_column(default=True)
    notified_vehicle_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        label = self.vin or f"{self.year_min or self.year or ''}-{self.year_max or ''} {self.make} {self.model}"
        return f"<WatchlistItem {label}>"

    def get_notified_ids(self) -> set[int]:
        """Return set of vehicle IDs already notified for this watch."""
        import json
        if not self.notified_vehicle_ids:
            return set()
        return set(json.loads(self.notified_vehicle_ids))

    def add_notified_id(self, vehicle_id: int):
        """Add a vehicle ID to the notified set."""
        import json
        ids = self.get_notified_ids()
        ids.add(vehicle_id)
        self.notified_vehicle_ids = json.dumps(list(ids))


class UserPreferences(Base):
    """Per-user preferences and state (pause/resume, custom filters)."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    alerts_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_filters: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON overrides
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        status = "paused" if self.alerts_paused else "active"
        return f"<UserPreferences chat={self.chat_id} {status}>"
