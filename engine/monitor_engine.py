"""Monitor engine - analyzes individually monitored auctions.

Completely separate from DealScorer. Does not apply pipeline filters.
Reuses ImportCostCalculator and currency utilities.
"""

from datetime import datetime, timezone

from loguru import logger

from engine.cost_calculator import ImportCostCalculator
from engine.currency import get_usd_brl_rate
from models.vehicle import Vehicle


class MonitorEngine:
    """Analyze a monitored vehicle independently of the deals pipeline."""

    def __init__(self):
        self.calculator = ImportCostCalculator()

    def analyze(self, vehicle: Vehicle) -> dict:
        """Compute import cost analysis for a monitored vehicle.

        Returns a dict with all analysis fields, or empty dict on failure.
        """
        auction_price = vehicle.current_bid_usd or vehicle.buy_now_price_usd
        if not auction_price or auction_price <= 0:
            return {}

        usd_brl = get_usd_brl_rate()

        try:
            breakdown = self.calculator.calculate(
                auction_price_usd=auction_price,
                engine_cc=vehicle.engine_cc,
                usd_brl_rate=usd_brl,
            )
        except Exception as e:
            logger.error(f"[MonitorEngine] Cost calculation failed: {e}")
            return {}

        # Estimate BR sale price (use available market data)
        sale_price_brl = self._estimate_sale_price(vehicle)

        # Profit / margin
        lucro_brl = None
        margem_pct = None
        if sale_price_brl and sale_price_brl > 0:
            lucro_brl = sale_price_brl - breakdown.total_landed_cost_brl
            margem_pct = (lucro_brl / breakdown.total_landed_cost_brl) * 100

        # Time remaining
        tempo_restante = None
        if vehicle.auction_end:
            now = datetime.now(timezone.utc)
            delta = vehicle.auction_end - now
            if delta.total_seconds() > 0:
                tempo_restante = delta.total_seconds()

        return {
            "auction_price_usd": auction_price,
            "custo_total_brl": breakdown.total_landed_cost_brl,
            "venda_estimada_brl": sale_price_brl,
            "lucro_brl": lucro_brl,
            "margem_pct": margem_pct,
            "usd_brl_rate": usd_brl,
            "titulo": vehicle.title_status or "unknown",
            "tempo_restante_s": tempo_restante,
            "cif_brl": breakdown.cif_brl,
            "total_taxes_brl": breakdown.total_taxes_brl,
        }

    @staticmethod
    def _estimate_sale_price(vehicle: Vehicle) -> float | None:
        """Estimate the BR sale price using available data."""
        if vehicle.br_price_avg and vehicle.fipe_price_brl:
            return min(vehicle.br_price_avg, vehicle.fipe_price_brl * 1.1)
        if vehicle.br_price_avg:
            return vehicle.br_price_avg
        if vehicle.fipe_price_brl:
            return vehicle.fipe_price_brl
        return None

    @staticmethod
    def format_time_remaining(seconds: float | None, has_auction_end: bool = False) -> str:
        """Format remaining seconds into a human-readable string."""
        if seconds is None:
            return "Encerrado" if has_auction_end else "—"
        if seconds <= 0:
            return "Encerrado"
        hours = seconds / 3600
        if hours < 1:
            return f"{int(seconds / 60)} min"
        if hours < 24:
            return f"{int(hours)}h {int((seconds % 3600) / 60)}min"
        days = int(hours / 24)
        remaining_hours = int(hours % 24)
        return f"{days}d {remaining_hours}h"
