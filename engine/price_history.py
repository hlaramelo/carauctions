"""Price history engine - tracks auction prices and BR market trends over time."""

import statistics
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, func

from models.br_listing import BRMarketListing, BRPriceSnapshot
from models.database import get_session
from models.vehicle import PriceHistory, Vehicle


class PriceHistoryEngine:
    """Tracks and analyzes price history for vehicles and the BR market."""

    def record_auction_price(self, vehicle: Vehicle) -> None:
        """Record the current auction price for a vehicle in price history."""
        price = vehicle.current_bid_usd or vehicle.buy_now_price_usd
        if not price:
            return

        session = get_session()
        try:
            # Only record if price changed since last entry
            last_entry = session.execute(
                select(PriceHistory)
                .where(PriceHistory.vehicle_id == vehicle.id)
                .order_by(PriceHistory.recorded_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if last_entry and last_entry.price_usd == price:
                return  # No change

            entry = PriceHistory(
                vehicle_id=vehicle.id,
                price_usd=price,
            )
            session.add(entry)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to record price history for vehicle {vehicle.id}: {e}")
            session.rollback()
        finally:
            session.close()

    def record_all_active_prices(self) -> int:
        """Record current prices for all active vehicles. Returns count recorded."""
        session = get_session()
        try:
            vehicles = session.execute(
                select(Vehicle).where(Vehicle.is_active == True)  # noqa: E712
            ).scalars().all()

            count = 0
            for vehicle in vehicles:
                price = vehicle.current_bid_usd or vehicle.buy_now_price_usd
                if not price:
                    continue

                last_entry = session.execute(
                    select(PriceHistory)
                    .where(PriceHistory.vehicle_id == vehicle.id)
                    .order_by(PriceHistory.recorded_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

                if last_entry and last_entry.price_usd == price:
                    continue

                session.add(PriceHistory(vehicle_id=vehicle.id, price_usd=price))
                count += 1

            session.commit()
            return count
        except Exception as e:
            logger.error(f"Failed to record prices: {e}")
            session.rollback()
            return 0
        finally:
            session.close()

    def get_price_trend(self, vehicle_id: int, hours: int = 48) -> dict:
        """Analyze price trend for a specific vehicle over a time window.

        Returns:
            dict with keys:
                - direction: "rising", "falling", "stable", "insufficient_data"
                - change_pct: percentage change from first to last price
                - prices: list of (timestamp, price) tuples
                - bid_count: number of distinct price points
        """
        session = get_session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            entries = session.execute(
                select(PriceHistory)
                .where(
                    PriceHistory.vehicle_id == vehicle_id,
                    PriceHistory.recorded_at >= cutoff,
                )
                .order_by(PriceHistory.recorded_at.asc())
            ).scalars().all()

            if len(entries) < 2:
                return {
                    "direction": "insufficient_data",
                    "change_pct": 0.0,
                    "prices": [(e.recorded_at, e.price_usd) for e in entries],
                    "bid_count": len(entries),
                }

            first_price = entries[0].price_usd
            last_price = entries[-1].price_usd
            change_pct = ((last_price - first_price) / first_price) * 100 if first_price > 0 else 0

            if change_pct > 3:
                direction = "rising"
            elif change_pct < -3:
                direction = "falling"
            else:
                direction = "stable"

            return {
                "direction": direction,
                "change_pct": round(change_pct, 2),
                "prices": [(e.recorded_at, e.price_usd) for e in entries],
                "bid_count": len(entries),
            }
        finally:
            session.close()

    def score_price_history(self, vehicle_id: int) -> float:
        """Calculate a 0-100 score based on price trend analysis.

        Scoring logic:
        - Falling auction price = good for buyer = higher score
        - Rising quickly = bidding war = lower score
        - Stable = neutral
        - More bid activity = more competitive = slightly lower score
        """
        trend = self.get_price_trend(vehicle_id)

        if trend["direction"] == "insufficient_data":
            return 50.0  # Neutral when no data

        change = trend["change_pct"]
        bid_count = trend["bid_count"]

        # Base score from price direction
        if change <= -10:
            score = 90.0  # Price dropping significantly - great opportunity
        elif change <= -5:
            score = 80.0
        elif change <= -2:
            score = 70.0
        elif change <= 2:
            score = 55.0  # Stable - slightly above neutral
        elif change <= 10:
            score = 40.0  # Rising moderately
        elif change <= 20:
            score = 25.0  # Rising fast - bidding war
        else:
            score = 10.0  # Price exploding - skip

        # Adjust for bid activity (more bids = more competitive)
        if bid_count > 15:
            score -= 10  # Very active auction
        elif bid_count > 8:
            score -= 5

        return max(0.0, min(100.0, score))


class BRMarketAnalyzer:
    """Analyzes Brazilian market data from Webmotors and OLX listings."""

    def create_price_snapshot(self, make: str, model: str, year: int) -> BRPriceSnapshot | None:
        """Create an aggregated price snapshot from all BR market listings for a vehicle spec.

        Combines data from all sources (webmotors, olx) into a single snapshot.
        """
        session = get_session()
        try:
            listings = session.execute(
                select(BRMarketListing).where(
                    func.lower(BRMarketListing.make) == make.lower(),
                    func.lower(BRMarketListing.model).contains(model.lower().split()[0]),
                    BRMarketListing.year.between(year - 1, year + 1),
                    BRMarketListing.is_active == True,  # noqa: E712
                )
            ).scalars().all()

            if not listings:
                return None

            prices = [l.price_brl for l in listings]

            snapshot = BRPriceSnapshot(
                make=make,
                model=model,
                year=year,
                avg_price_brl=statistics.mean(prices),
                min_price_brl=min(prices),
                max_price_brl=max(prices),
                median_price_brl=statistics.median(prices),
                listings_count=len(prices),
                source="combined",
            )
            session.add(snapshot)
            session.commit()

            logger.info(
                f"[BRMarket] Snapshot for {year} {make} {model}: "
                f"avg=R${snapshot.avg_price_brl:,.0f} "
                f"({snapshot.listings_count} listings)"
            )
            return snapshot
        except Exception as e:
            logger.error(f"Failed to create price snapshot: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def get_market_trend(self, make: str, model: str, year: int, days: int = 30) -> dict:
        """Analyze BR market price trend over time using snapshots.

        Returns:
            dict with keys:
                - direction: "appreciating", "depreciating", "stable", "insufficient_data"
                - change_pct: percentage change from oldest to newest snapshot
                - snapshots: list of snapshot dicts
        """
        session = get_session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            snapshots = session.execute(
                select(BRPriceSnapshot).where(
                    func.lower(BRPriceSnapshot.make) == make.lower(),
                    func.lower(BRPriceSnapshot.model).contains(model.lower().split()[0]),
                    BRPriceSnapshot.year == year,
                    BRPriceSnapshot.snapshot_date >= cutoff,
                ).order_by(BRPriceSnapshot.snapshot_date.asc())
            ).scalars().all()

            if len(snapshots) < 2:
                return {
                    "direction": "insufficient_data",
                    "change_pct": 0.0,
                    "snapshots": [],
                }

            first_avg = snapshots[0].avg_price_brl
            last_avg = snapshots[-1].avg_price_brl
            change_pct = ((last_avg - first_avg) / first_avg) * 100 if first_avg > 0 else 0

            if change_pct > 3:
                direction = "appreciating"
            elif change_pct < -3:
                direction = "depreciating"
            else:
                direction = "stable"

            return {
                "direction": direction,
                "change_pct": round(change_pct, 2),
                "snapshots": [
                    {
                        "date": s.snapshot_date.isoformat(),
                        "avg": s.avg_price_brl,
                        "median": s.median_price_brl,
                        "count": s.listings_count,
                    }
                    for s in snapshots
                ],
            }
        finally:
            session.close()

    def enrich_vehicle_with_market_data(self, vehicle: Vehicle) -> bool:
        """Update a vehicle's BR market price fields from market listings.

        Returns True if data was updated.
        """
        if not vehicle.make or not vehicle.model or not vehicle.year:
            return False

        session = get_session()
        try:
            listings = session.execute(
                select(BRMarketListing).where(
                    func.lower(BRMarketListing.make) == vehicle.make.lower(),
                    func.lower(BRMarketListing.model).contains(
                        vehicle.model.lower().split()[0]
                    ),
                    BRMarketListing.year.between(vehicle.year - 1, vehicle.year + 1),
                    BRMarketListing.is_active == True,  # noqa: E712
                )
            ).scalars().all()

            if not listings:
                return False

            prices = [l.price_brl for l in listings]

            # Update vehicle with market data (need to merge into correct session)
            vehicle_session = get_session()
            try:
                v = vehicle_session.get(Vehicle, vehicle.id)
                if v:
                    v.br_price_avg = statistics.mean(prices)
                    v.br_price_min = min(prices)
                    v.br_price_max = max(prices)
                    v.br_listings_count = len(prices)
                    vehicle_session.commit()
                    return True
            except Exception as e:
                logger.error(f"Failed to update vehicle market data: {e}")
                vehicle_session.rollback()
            finally:
                vehicle_session.close()

            return False
        finally:
            session.close()
