"""Job scheduler - orchestrates scraping, scoring, and alerting."""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from config import load_settings
from engine.deal_scorer import DealScorer
from engine.monitor_engine import MonitorEngine
from engine.price_history import PriceHistoryEngine, BRMarketAnalyzer
from models.br_listing import BRMarketListing
from models.database import get_session, init_db
from models.deal import Deal, AlertLog
from models.monitored_auction import MonitoredAuction
from models.vehicle import Vehicle
from notifications.email_sender import EmailSender
from notifications.sheets_sync import SheetsSync
from notifications.telegram_bot import TelegramNotifier
from models.watchlist import WatchlistItem
from scrapers.bring_a_trailer import BringATrailerScraper
from scrapers.cars_and_bids import CarsAndBidsScraper
from scrapers.copart import CopartScraper
from scrapers.hemmings import HemmingsScraper
from scrapers.br_market.fipe import FipeClient
from scrapers.br_market.webmotors import WebmotorsScraper
from scrapers.br_market.olx import OLXScraper


def _save_scraped_vehicles(vehicles: list[Vehicle], source_label: str):
    """Save scraped vehicles to database (upsert). Shared by all auction scrapers."""
    session = get_session()
    new_count = 0
    updated_count = 0

    try:
        for vehicle in vehicles:
            existing = session.execute(
                select(Vehicle).where(
                    Vehicle.source == vehicle.source,
                    Vehicle.source_id == vehicle.source_id,
                )
            ).scalar_one_or_none()

            if existing:
                if vehicle.current_bid_usd:
                    existing.current_bid_usd = vehicle.current_bid_usd
                if vehicle.buy_now_price_usd:
                    existing.buy_now_price_usd = vehicle.buy_now_price_usd
                if vehicle.auction_end:
                    existing.auction_end = vehicle.auction_end
                if vehicle.mileage:
                    existing.mileage = vehicle.mileage
                if vehicle.engine_cc:
                    existing.engine_cc = vehicle.engine_cc
                existing.is_active = True
                updated_count += 1
            else:
                session.add(vehicle)
                new_count += 1

        session.commit()
        logger.info(f"{source_label} scrape complete: {new_count} new, {updated_count} updated")
    except Exception as e:
        logger.error(f"Error saving {source_label} vehicles: {e}")
        session.rollback()
    finally:
        session.close()


def run_bat_scrape():
    """Scrape Bring a Trailer and store listings."""
    logger.info("=== Starting BaT scrape job ===")
    scraper = BringATrailerScraper()
    vehicles = scraper.scrape_listings()
    _save_scraped_vehicles(vehicles, "BaT")


def run_copart_scrape():
    """Scrape Copart and store listings."""
    logger.info("=== Starting Copart scrape job ===")
    scraper = CopartScraper()
    vehicles = scraper.scrape_listings()
    _save_scraped_vehicles(vehicles, "Copart")


def run_cars_and_bids_scrape():
    """Scrape Cars & Bids and store listings."""
    logger.info("=== Starting Cars & Bids scrape job ===")
    scraper = CarsAndBidsScraper()
    vehicles = scraper.scrape_listings()
    _save_scraped_vehicles(vehicles, "CarsAndBids")


def run_hemmings_scrape():
    """Scrape Hemmings and store listings."""
    logger.info("=== Starting Hemmings scrape job ===")
    scraper = HemmingsScraper()
    vehicles = scraper.scrape_listings()
    _save_scraped_vehicles(vehicles, "Hemmings")


def run_fipe_enrichment():
    """Enrich vehicles with FIPE price data."""
    logger.info("=== Starting FIPE enrichment job ===")
    fipe = FipeClient()
    session = get_session()

    try:
        # Get vehicles without FIPE price
        vehicles = session.execute(
            select(Vehicle).where(
                Vehicle.is_active == True,  # noqa: E712
                Vehicle.fipe_price_brl == None,  # noqa: E711
            )
        ).scalars().all()

        logger.info(f"Enriching {len(vehicles)} vehicles with FIPE prices")

        for vehicle in vehicles:
            if not vehicle.make or not vehicle.model or not vehicle.year:
                continue

            price = fipe.lookup_price(vehicle.make, vehicle.model, vehicle.year)
            if price:
                vehicle.fipe_price_brl = price
                logger.info(
                    f"  FIPE price for {vehicle.year} {vehicle.make} {vehicle.model}: "
                    f"R${price:,.0f}"
                )

        session.commit()
        logger.info("FIPE enrichment complete")
    except Exception as e:
        logger.error(f"Error in FIPE enrichment: {e}")
        session.rollback()
    finally:
        session.close()


def run_deal_scoring():
    """Score all active vehicles and create/update deals."""
    logger.info("=== Starting deal scoring job ===")
    scorer = DealScorer()
    session = get_session()

    try:
        vehicles = session.execute(
            select(Vehicle).where(Vehicle.is_active == True)  # noqa: E712
        ).scalars().all()

        new_deals = 0
        for vehicle in vehicles:
            deal = scorer.score_vehicle(vehicle)
            if deal is None:
                continue

            # Check if deal already exists for this vehicle
            existing_deal = session.execute(
                select(Deal).where(
                    Deal.vehicle_id == vehicle.id,
                    Deal.is_active == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            if existing_deal:
                # Update existing deal
                existing_deal.auction_price_usd = deal.auction_price_usd
                existing_deal.total_landed_cost_brl = deal.total_landed_cost_brl
                existing_deal.estimated_sale_price_brl = deal.estimated_sale_price_brl
                existing_deal.estimated_profit_brl = deal.estimated_profit_brl
                existing_deal.margin_pct = deal.margin_pct
                existing_deal.score = deal.score
                existing_deal.score_breakdown = deal.score_breakdown
                existing_deal.usd_brl_rate = deal.usd_brl_rate
            else:
                deal.vehicle_id = vehicle.id
                session.add(deal)
                new_deals += 1

        session.commit()
        logger.info(f"Deal scoring complete: {new_deals} new deals")
    except Exception as e:
        logger.error(f"Error in deal scoring: {e}")
        session.rollback()
    finally:
        session.close()


def run_br_market_scrape():
    """Scrape Brazilian marketplaces (Webmotors + OLX) for real market prices."""
    logger.info("=== Starting BR market scrape job ===")
    webmotors = WebmotorsScraper()
    olx = OLXScraper()
    session = get_session()

    try:
        # Get unique make/model/year combos from active vehicles
        vehicles = session.execute(
            select(Vehicle).where(Vehicle.is_active == True)  # noqa: E712
        ).scalars().all()

        seen_specs = set()
        total_new = 0

        for vehicle in vehicles:
            if not vehicle.make or not vehicle.model or not vehicle.year:
                continue

            spec_key = (vehicle.make.lower(), vehicle.model.lower().split()[0], vehicle.year)
            if spec_key in seen_specs:
                continue
            seen_specs.add(spec_key)

            # Scrape Webmotors
            try:
                wm_listings = webmotors.scrape_for_vehicle(vehicle.make, vehicle.model, vehicle.year)
                for data in wm_listings:
                    existing = session.execute(
                        select(BRMarketListing).where(
                            BRMarketListing.source_id == data["source_id"]
                        )
                    ).scalar_one_or_none()
                    if not existing:
                        listing = BRMarketListing(**data)
                        session.add(listing)
                        total_new += 1
            except Exception as e:
                logger.error(f"Webmotors scrape failed for {vehicle.make} {vehicle.model}: {e}")

            # Scrape OLX
            try:
                olx_listings = olx.scrape_for_vehicle(vehicle.make, vehicle.model, vehicle.year)
                for data in olx_listings:
                    existing = session.execute(
                        select(BRMarketListing).where(
                            BRMarketListing.source_id == data["source_id"]
                        )
                    ).scalar_one_or_none()
                    if not existing:
                        listing = BRMarketListing(**data)
                        session.add(listing)
                        total_new += 1
            except Exception as e:
                logger.error(f"OLX scrape failed for {vehicle.make} {vehicle.model}: {e}")

        session.commit()
        logger.info(f"BR market scrape complete: {total_new} new listings from {len(seen_specs)} vehicle specs")
    except Exception as e:
        logger.error(f"Error in BR market scrape: {e}")
        session.rollback()
    finally:
        session.close()


def run_br_market_enrichment():
    """Enrich vehicles with real market prices and create price snapshots."""
    logger.info("=== Starting BR market enrichment job ===")
    analyzer = BRMarketAnalyzer()
    session = get_session()

    try:
        vehicles = session.execute(
            select(Vehicle).where(Vehicle.is_active == True)  # noqa: E712
        ).scalars().all()

        enriched = 0
        snapshots = 0

        for vehicle in vehicles:
            if not vehicle.make or not vehicle.model or not vehicle.year:
                continue

            # Update vehicle with market price data
            if analyzer.enrich_vehicle_with_market_data(vehicle):
                enriched += 1

            # Create price snapshot for trend tracking
            snapshot = analyzer.create_price_snapshot(vehicle.make, vehicle.model, vehicle.year)
            if snapshot:
                snapshots += 1

        logger.info(f"BR market enrichment: {enriched} vehicles enriched, {snapshots} snapshots created")
    except Exception as e:
        logger.error(f"Error in BR market enrichment: {e}")
    finally:
        session.close()


def run_price_history_recording():
    """Record current auction prices for trend tracking."""
    logger.info("=== Starting price history recording ===")
    engine = PriceHistoryEngine()
    count = engine.record_all_active_prices()
    logger.info(f"Price history: {count} new price points recorded")


def run_alerts():
    """Send alerts for high-scoring deals that haven't been alerted yet."""
    logger.info("=== Starting alerts job ===")
    settings = load_settings()
    min_score = settings.get("alerts", {}).get("telegram", {}).get("min_score", 60)
    email_min_score = settings.get("alerts", {}).get("email", {}).get("min_score", 70)

    telegram = TelegramNotifier()
    email = EmailSender()
    sheets = SheetsSync()
    session = get_session()

    try:
        # Get high-scoring active deals
        deals = session.execute(
            select(Deal).where(
                Deal.is_active == True,  # noqa: E712
                Deal.score >= min(min_score, email_min_score),
            ).order_by(Deal.score.desc())
        ).scalars().all()

        deals_with_vehicles = []
        for deal in deals:
            vehicle = session.get(Vehicle, deal.vehicle_id)
            if vehicle:
                deals_with_vehicles.append((deal, vehicle))

        # Send individual alerts for new deals (not yet alerted)
        for deal, vehicle in deals_with_vehicles:
            # Telegram alerts
            if deal.score >= min_score:
                already_telegrammed = session.execute(
                    select(AlertLog).where(
                        AlertLog.deal_id == deal.id,
                        AlertLog.channel == "telegram",
                    )
                ).scalar_one_or_none()
                if not already_telegrammed:
                    telegram.send_deal_alert(deal, vehicle)

            # Email instant alerts for high-score deals
            if deal.score >= email_min_score:
                already_emailed = session.execute(
                    select(AlertLog).where(
                        AlertLog.deal_id == deal.id,
                        AlertLog.channel == "email",
                    )
                ).scalar_one_or_none()
                if not already_emailed:
                    email.send_deal_alert(deal, vehicle)

        # Sync all active deals to Google Sheets
        all_active_deals = session.execute(
            select(Deal).where(Deal.is_active == True).order_by(Deal.score.desc())  # noqa: E712
        ).scalars().all()

        all_deals_with_vehicles = []
        for deal in all_active_deals:
            vehicle = session.get(Vehicle, deal.vehicle_id)
            if vehicle:
                all_deals_with_vehicles.append((deal, vehicle))

        sheets.sync_deals(all_deals_with_vehicles)

        # Sync watchlist to sheets
        watchlist_items = session.execute(
            select(WatchlistItem).where(WatchlistItem.is_active == True)  # noqa: E712
        ).scalars().all()

        watchlist_data = []
        for item in watchlist_items:
            vehicle = session.get(Vehicle, item.vehicle_id) if item.vehicle_id else None
            deal = None
            if item.vehicle_id:
                deal = session.execute(
                    select(Deal).where(
                        Deal.vehicle_id == item.vehicle_id,
                        Deal.is_active == True,  # noqa: E712
                    )
                ).scalar_one_or_none()
            watchlist_data.append((item, vehicle, deal))

        sheets.sync_watchlist(watchlist_data)

        # Append to history sheet (daily snapshot)
        sheets.append_history(all_deals_with_vehicles)

        logger.info(f"Alerts complete: {len(deals_with_vehicles)} high-score deals")
    except Exception as e:
        logger.error(f"Error in alerts: {e}")
    finally:
        session.close()


def run_daily_digest():
    """Send daily email digest with top deals."""
    logger.info("=== Starting daily digest ===")
    email = EmailSender()
    session = get_session()

    try:
        deals = session.execute(
            select(Deal).where(
                Deal.is_active == True,  # noqa: E712
            ).order_by(Deal.score.desc()).limit(20)
        ).scalars().all()

        deals_with_vehicles = []
        for deal in deals:
            vehicle = session.get(Vehicle, deal.vehicle_id)
            if vehicle:
                deals_with_vehicles.append((deal, vehicle))

        email.send_daily_digest(deals_with_vehicles)
        logger.info(f"Daily digest sent with {len(deals_with_vehicles)} deals")
    except Exception as e:
        logger.error(f"Error sending daily digest: {e}")
    finally:
        session.close()


SCRAPER_MAP = {
    "copart": CopartScraper,
    "bat": BringATrailerScraper,
    "carsandbids": CarsAndBidsScraper,
    "hemmings": HemmingsScraper,
}

# Intervals in seconds
_MONITOR_NORMAL_INTERVAL = 3600   # 1 hour
_MONITOR_URGENT_INTERVAL = 300    # 5 min
_MONITOR_URGENT_THRESHOLD = 1800  # 30 min before auction end


def run_monitored_auctions():
    """Fetch updates for individually monitored auction listings.

    Frequency logic:
    - Normal: re-check if last_checked_at > 1h ago
    - Urgent: re-check every 5 min if auction ends in < 30 min
    """
    logger.info("=== Starting monitored auctions job ===")
    session = get_session()
    engine = MonitorEngine()
    price_engine = PriceHistoryEngine()
    telegram = TelegramNotifier()

    try:
        auctions = session.execute(
            select(MonitoredAuction).where(MonitoredAuction.is_active == True)  # noqa: E712
        ).scalars().all()

        if not auctions:
            logger.info("[Monitor] No active monitored auctions")
            return

        now = datetime.now(timezone.utc)
        checked = 0
        alerted = 0

        for auction in auctions:
            # Decide if this auction needs a check
            if not _should_check(auction, session, now):
                continue

            # Get the right scraper
            scraper_cls = SCRAPER_MAP.get(auction.source)
            if not scraper_cls:
                logger.warning(f"[Monitor] Unknown source: {auction.source}")
                continue

            scraper = scraper_cls()
            try:
                vehicle = scraper.fetch_single_listing(auction.url)
            except NotImplementedError:
                logger.warning(f"[Monitor] {auction.source} does not support single fetch")
                continue
            except Exception as e:
                logger.error(f"[Monitor] Failed to fetch {auction.url}: {e}")
                continue

            if not vehicle:
                logger.warning(f"[Monitor] No data returned for {auction.url}")
                auction.last_checked_at = now
                continue

            # Upsert vehicle
            existing = session.execute(
                select(Vehicle).where(
                    Vehicle.source == vehicle.source,
                    Vehicle.source_id == vehicle.source_id,
                )
            ).scalar_one_or_none()

            if existing:
                old_bid = existing.current_bid_usd
                if vehicle.current_bid_usd:
                    existing.current_bid_usd = vehicle.current_bid_usd
                if vehicle.buy_now_price_usd:
                    existing.buy_now_price_usd = vehicle.buy_now_price_usd
                if vehicle.auction_end:
                    existing.auction_end = vehicle.auction_end
                if vehicle.mileage:
                    existing.mileage = vehicle.mileage
                if vehicle.engine_cc:
                    existing.engine_cc = vehicle.engine_cc
                if vehicle.title_status:
                    existing.title_status = vehicle.title_status
                if vehicle.damage_description:
                    existing.damage_description = vehicle.damage_description
                if vehicle.vin:
                    existing.vin = vehicle.vin
                existing.is_active = True
                vehicle_obj = existing
            else:
                session.add(vehicle)
                session.flush()
                vehicle_obj = vehicle
                old_bid = None

            # Link auction to vehicle
            auction.vehicle_id = vehicle_obj.id
            auction.last_checked_at = now

            # Record price history
            price_engine.record_auction_price(vehicle_obj)

            # Analyze
            analysis = engine.analyze(vehicle_obj)

            # Check for alerts
            if old_bid and vehicle_obj.current_bid_usd and old_bid > 0:
                change_pct = ((vehicle_obj.current_bid_usd - old_bid) / old_bid) * 100
                if abs(change_pct) >= 5:
                    reason = f"Preco mudou {change_pct:+.1f}% (${old_bid:,.0f} → ${vehicle_obj.current_bid_usd:,.0f})"
                    telegram.send_monitor_alert(auction, vehicle_obj, analysis, reason)
                    alerted += 1

            # Urgency alert: auction ending in < 2h
            if analysis.get("tempo_restante_s") and analysis["tempo_restante_s"] < 7200:
                tempo_str = MonitorEngine.format_time_remaining(analysis["tempo_restante_s"])
                reason = f"Leilao encerra em {tempo_str}"
                telegram.send_monitor_alert(auction, vehicle_obj, analysis, reason)
                alerted += 1

            checked += 1

        session.commit()
        logger.info(f"[Monitor] Checked {checked} auctions, sent {alerted} alerts")
    except Exception as e:
        logger.error(f"Error in monitored auctions: {e}")
        session.rollback()
    finally:
        session.close()


def _should_check(auction: MonitoredAuction, session, now: datetime) -> bool:
    """Determine if a monitored auction needs to be re-fetched."""
    if auction.last_checked_at is None:
        return True  # Never checked

    elapsed = (now - auction.last_checked_at.replace(tzinfo=timezone.utc)).total_seconds()

    # If we have a linked vehicle, check if auction is ending soon
    if auction.vehicle_id:
        vehicle = session.get(Vehicle, auction.vehicle_id)
        if vehicle and vehicle.auction_end:
            time_left = (vehicle.auction_end - now).total_seconds()
            if time_left <= _MONITOR_URGENT_THRESHOLD:
                return elapsed >= _MONITOR_URGENT_INTERVAL
            if time_left <= 0:
                # Auction ended — no need to keep checking
                return False

    return elapsed >= _MONITOR_NORMAL_INTERVAL


def run_full_pipeline():
    """Run the complete pipeline: scrape -> enrich -> score -> alert."""
    logger.info("========================================")
    logger.info("Starting full pipeline run")
    logger.info("========================================")

    init_db()

    # Scrape all US auction sources
    run_bat_scrape()
    run_copart_scrape()
    run_cars_and_bids_scrape()
    run_hemmings_scrape()

    # Record price history and enrich with BR data
    run_price_history_recording()
    run_fipe_enrichment()
    run_br_market_scrape()
    run_br_market_enrichment()

    # Score and alert
    run_deal_scoring()
    run_alerts()

    # Monitor individual listings
    run_monitored_auctions()

    logger.info("========================================")
    logger.info("Pipeline run complete")
    logger.info("========================================")
