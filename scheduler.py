"""Job scheduler - orchestrates scraping, scoring, and alerting."""

from loguru import logger
from sqlalchemy import select

from config import load_settings
from engine.deal_scorer import DealScorer
from models.database import get_session, init_db
from models.deal import Deal, AlertLog
from models.vehicle import Vehicle
from notifications.sheets_sync import SheetsSync
from notifications.telegram_bot import TelegramNotifier
from scrapers.bring_a_trailer import BringATrailerScraper
from scrapers.br_market.fipe import FipeClient


def run_bat_scrape():
    """Scrape Bring a Trailer and store listings."""
    logger.info("=== Starting BaT scrape job ===")
    scraper = BringATrailerScraper()
    vehicles = scraper.scrape_listings()

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
                # Update bid price and other changing fields
                if vehicle.current_bid_usd:
                    existing.current_bid_usd = vehicle.current_bid_usd
                if vehicle.auction_end:
                    existing.auction_end = vehicle.auction_end
                existing.is_active = True
                updated_count += 1
            else:
                session.add(vehicle)
                new_count += 1

        session.commit()
        logger.info(f"BaT scrape complete: {new_count} new, {updated_count} updated")
    except Exception as e:
        logger.error(f"Error saving BaT vehicles: {e}")
        session.rollback()
    finally:
        session.close()


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


def run_alerts():
    """Send alerts for high-scoring deals that haven't been alerted yet."""
    logger.info("=== Starting alerts job ===")
    settings = load_settings()
    min_score = settings.get("alerts", {}).get("telegram", {}).get("min_score", 60)

    telegram = TelegramNotifier()
    sheets = SheetsSync()
    session = get_session()

    try:
        # Get high-scoring active deals
        deals = session.execute(
            select(Deal).where(
                Deal.is_active == True,  # noqa: E712
                Deal.score >= min_score,
            ).order_by(Deal.score.desc())
        ).scalars().all()

        deals_with_vehicles = []
        for deal in deals:
            vehicle = session.get(Vehicle, deal.vehicle_id)
            if vehicle:
                deals_with_vehicles.append((deal, vehicle))

        # Send individual alerts for new deals (not yet alerted)
        for deal, vehicle in deals_with_vehicles:
            already_alerted = session.execute(
                select(AlertLog).where(
                    AlertLog.deal_id == deal.id,
                    AlertLog.channel == "telegram",
                )
            ).scalar_one_or_none()

            if not already_alerted:
                telegram.send_deal_alert(deal, vehicle)

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

        logger.info(f"Alerts complete: {len(deals_with_vehicles)} high-score deals")
    except Exception as e:
        logger.error(f"Error in alerts: {e}")
    finally:
        session.close()


def run_full_pipeline():
    """Run the complete pipeline: scrape -> enrich -> score -> alert."""
    logger.info("========================================")
    logger.info("Starting full pipeline run")
    logger.info("========================================")

    init_db()
    run_bat_scrape()
    run_fipe_enrichment()
    run_deal_scoring()
    run_alerts()

    logger.info("========================================")
    logger.info("Pipeline run complete")
    logger.info("========================================")
