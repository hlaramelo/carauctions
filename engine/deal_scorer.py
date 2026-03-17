"""Deal scoring engine - ranks vehicle deals by opportunity quality."""

import json
from datetime import datetime, timezone

from loguru import logger

from config import load_settings
from engine.cost_calculator import ImportCostCalculator
from engine.currency import get_usd_brl_rate
from engine.price_history import PriceHistoryEngine, BRMarketAnalyzer
from models.vehicle import Vehicle
from models.deal import Deal


class DealScorer:
    """Score vehicle deals based on configurable weighted factors."""

    def __init__(self):
        settings = load_settings()
        self.weights = settings["scoring"]["weights"]
        self.filters = settings["filters"]
        self.calculator = ImportCostCalculator()
        self.price_history = PriceHistoryEngine()
        self.market_analyzer = BRMarketAnalyzer()

    def passes_filters(self, vehicle: Vehicle) -> bool:
        """Check if a vehicle passes the configured filters."""
        if vehicle.year and vehicle.year < self.filters.get("min_year", 0):
            return False
        if vehicle.mileage and vehicle.mileage > self.filters.get("max_mileage", 999999):
            return False
        if vehicle.title_status:
            allowed = self.filters.get("title_status", [])
            if allowed and vehicle.title_status.lower() not in [s.lower() for s in allowed]:
                return False
        if vehicle.current_bid_usd and vehicle.current_bid_usd > self.filters.get(
            "max_auction_price_usd", 999999
        ):
            return False
        makes = self.filters.get("makes", [])
        if makes and vehicle.make not in makes:
            return False
        excluded_states = self.filters.get("exclude_states", [])
        if vehicle.location_state and vehicle.location_state in excluded_states:
            return False
        return True

    def score_vehicle(self, vehicle: Vehicle) -> Deal | None:
        """Score a vehicle and create a Deal if it passes filters.

        Returns None if the vehicle doesn't pass filters or has insufficient data.
        """
        if not self.passes_filters(vehicle):
            return None

        auction_price = vehicle.current_bid_usd or vehicle.buy_now_price_usd
        if not auction_price:
            return None

        # Prefer real market avg (Webmotors/OLX) over FIPE reference
        br_price = vehicle.br_price_avg or vehicle.fipe_price_brl
        if not br_price:
            logger.debug(f"No BR price data for {vehicle}, skipping scoring")
            return None

        # If we have both market and FIPE data, use conservative estimate
        # (lower of market avg and FIPE to avoid overestimating)
        if vehicle.br_price_avg and vehicle.fipe_price_brl:
            br_price = min(vehicle.br_price_avg, vehicle.fipe_price_brl * 1.1)

        usd_brl = get_usd_brl_rate()

        breakdown = self.calculator.calculate(
            auction_price_usd=auction_price,
            engine_cc=vehicle.engine_cc,
            usd_brl_rate=usd_brl,
        )

        profit = br_price - breakdown.total_landed_cost_brl
        margin = (profit / breakdown.total_landed_cost_brl) * 100 if breakdown.total_landed_cost_brl > 0 else 0

        if margin < self.filters.get("min_margin_pct", 0):
            return None

        # Calculate individual scores (0-100 each)
        scores = {}

        # 1. Margin score (0-100)
        scores["margin"] = min(100, max(0, margin * 2))  # 50% margin = 100 score

        # 2. Liquidity score (0-100) - listings count + BR market trend
        scores["liquidity"] = self._score_liquidity(vehicle)

        # 3. Condition score (0-100)
        scores["condition"] = self._score_condition(vehicle)

        # 4. Time remaining score (0-100)
        scores["time_remaining"] = self._score_time_remaining(vehicle)

        # 5. Price history score (0-100) - auction bid trends + BR market direction
        scores["price_history"] = self._score_combined_history(vehicle)

        # Weighted total
        total_score = sum(
            scores[factor] * self.weights[factor] for factor in self.weights
        )

        score_breakdown = json.dumps(scores)

        deal = Deal(
            vehicle_id=vehicle.id,
            auction_price_usd=auction_price,
            total_landed_cost_brl=breakdown.total_landed_cost_brl,
            estimated_sale_price_brl=br_price,
            estimated_profit_brl=profit,
            margin_pct=margin,
            score=total_score,
            score_breakdown=score_breakdown,
            usd_brl_rate=usd_brl,
        )

        return deal

    def _score_liquidity(self, vehicle: Vehicle) -> float:
        """Score liquidity: listings count + market velocity (appreciating market = easier sell)."""
        listings = vehicle.br_listings_count or 0

        # Base score from listings count
        base = min(100, listings * 5)  # 20+ listings = 100

        # Boost if BR market is appreciating (easier to sell at good price)
        if vehicle.make and vehicle.model and vehicle.year:
            trend = self.market_analyzer.get_market_trend(
                vehicle.make, vehicle.model, vehicle.year
            )
            if trend["direction"] == "appreciating":
                base = min(100, base + 15)  # Appreciating market = bonus
            elif trend["direction"] == "depreciating":
                base = max(0, base - 10)  # Depreciating = penalty

        return base

    def _score_combined_history(self, vehicle: Vehicle) -> float:
        """Combined score from auction bid trends and BR market direction.

        Auction bid trends (60% of this sub-score):
        - Falling bids = great opportunity
        - Bidding wars = lower score

        BR market trends (40% of this sub-score):
        - Appreciating BR market = car gaining value = good
        - Depreciating = might sell for less than expected = bad
        """
        # Auction bid trend score
        auction_score = self.price_history.score_price_history(vehicle.id)

        # BR market trend score
        market_score = 50.0  # Neutral default
        if vehicle.make and vehicle.model and vehicle.year:
            trend = self.market_analyzer.get_market_trend(
                vehicle.make, vehicle.model, vehicle.year
            )
            change = trend["change_pct"]
            if trend["direction"] != "insufficient_data":
                if change >= 5:
                    market_score = 85.0  # Appreciating well
                elif change >= 2:
                    market_score = 70.0
                elif change >= -2:
                    market_score = 55.0  # Stable
                elif change >= -5:
                    market_score = 35.0  # Slight depreciation
                else:
                    market_score = 20.0  # Depreciating fast

        # Weighted combination
        return auction_score * 0.6 + market_score * 0.4

    @staticmethod
    def _score_condition(vehicle: Vehicle) -> float:
        """Score vehicle condition based on mileage, title status, damage."""
        score = 70.0  # Base score

        # Mileage factor
        if vehicle.mileage is not None:
            if vehicle.mileage < 10000:
                score += 20
            elif vehicle.mileage < 30000:
                score += 10
            elif vehicle.mileage > 80000:
                score -= 20
            elif vehicle.mileage > 50000:
                score -= 10

        # Title status
        if vehicle.title_status:
            status = vehicle.title_status.lower()
            if status == "clean":
                score += 10
            elif status == "rebuilt":
                score -= 10
            elif status == "salvage":
                score -= 30

        return min(100, max(0, score))

    @staticmethod
    def _score_time_remaining(vehicle: Vehicle) -> float:
        """Score based on auction urgency - ending soon = higher score."""
        if not vehicle.auction_end:
            return 50.0  # Neutral if no end time

        now = datetime.now(timezone.utc)
        end = vehicle.auction_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        hours_left = (end - now).total_seconds() / 3600

        if hours_left <= 0:
            return 0  # Already ended
        elif hours_left <= 2:
            return 100  # Ending very soon
        elif hours_left <= 6:
            return 80
        elif hours_left <= 24:
            return 60
        elif hours_left <= 72:
            return 40
        else:
            return 20
