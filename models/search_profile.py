"""Search profile model - saved filter presets for quick deal filtering."""

import json
from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class SearchProfile(Base):
    """A saved search/filter profile for quickly switching between deal criteria.

    Examples:
    - "Classicos Muscle <$30k" — makes=[Chevrolet,Ford,Dodge], min_year=1965, max_year=1975, max_price=30000
    - "Luxo Moderno" — makes=[Porsche,BMW,Mercedes-Benz], min_year=2015, min_score=60
    - "Salvage Deals" — title_status=[salvage], min_margin=25
    """

    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Filters (stored as JSON where lists are needed)
    makes_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of makes
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of sources
    title_status_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    min_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<SearchProfile #{self.id} '{self.name}'>"

    @property
    def makes(self) -> list[str]:
        if not self.makes_json:
            return []
        return json.loads(self.makes_json)

    @makes.setter
    def makes(self, value: list[str]):
        self.makes_json = json.dumps(value) if value else None

    @property
    def sources(self) -> list[str]:
        if not self.sources_json:
            return []
        return json.loads(self.sources_json)

    @sources.setter
    def sources(self, value: list[str]):
        self.sources_json = json.dumps(value) if value else None

    @property
    def title_statuses(self) -> list[str]:
        if not self.title_status_json:
            return []
        return json.loads(self.title_status_json)

    @title_statuses.setter
    def title_statuses(self, value: list[str]):
        self.title_status_json = json.dumps(value) if value else None
