from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(Integer, index=True)

    auction_price_usd: Mapped[float] = mapped_column(Float)
    total_landed_cost_brl: Mapped[float] = mapped_column(Float)
    estimated_sale_price_brl: Mapped[float] = mapped_column(Float)
    estimated_profit_brl: Mapped[float] = mapped_column(Float)
    margin_pct: Mapped[float] = mapped_column(Float)

    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    usd_brl_rate: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Deal vehicle_id={self.vehicle_id} score={self.score:.1f} margin={self.margin_pct:.1f}%>"


class AlertLog(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # telegram, email, sheets
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
