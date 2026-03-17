"""Watchlist and user preferences models."""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class WatchlistItem(Base):
    """A vehicle being watched by the user (tracked by VIN or vehicle ID)."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    chat_id: Mapped[str] = mapped_column(String(50), index=True)  # Telegram chat ID
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        label = self.vin or f"{self.year} {self.make} {self.model}"
        return f"<WatchlistItem {label}>"


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
