"""Monitored auction model - tracks specific auction listings chosen by the user."""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class MonitoredAuction(Base):
    """A specific auction listing being actively monitored."""

    __tablename__ = "monitored_auctions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20))  # copart, bat, carsandbids, hemmings
    source_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(50), index=True)  # Telegram chat ID or "dashboard"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<MonitoredAuction {self.source}:{self.source_id} active={self.is_active}>"
