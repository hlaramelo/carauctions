#!/usr/bin/env python3
"""Car Auction Deal Finder - Main entry point.

Usage:
    python main.py              # Run full pipeline once
    python main.py --schedule   # Run with APScheduler (continuous)
    python main.py --scrape     # Only run scraping
    python main.py --score      # Only run scoring
    python main.py --alerts     # Only run alerts
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from models.database import init_db
from scheduler import (
    run_bat_scrape,
    run_br_market_scrape,
    run_br_market_enrichment,
    run_cars_and_bids_scrape,
    run_copart_scrape,
    run_daily_digest,
    run_deal_scoring,
    run_fipe_enrichment,
    run_hemmings_scrape,
    run_monitored_auctions,
    run_price_history_recording,
    run_alerts,
    run_full_pipeline,
    run_watchlist_matching,
)

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
)
# File logging (skip if logs dir can't be created, e.g. on Railway)
try:
    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/carauctions_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="DEBUG",
    )
except Exception:
    pass


def run_scheduled():
    """Run the pipeline on a schedule using APScheduler."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    from config import load_settings

    settings = load_settings()
    bat_interval = (
        settings.get("scraping", {})
        .get("bring_a_trailer", {})
        .get("interval_minutes", 15)
    )

    sched = BlockingScheduler()

    br_market_interval = (
        settings.get("scraping", {})
        .get("br_market", {})
        .get("interval_hours", 4)
    )

    copart_interval = (
        settings.get("scraping", {})
        .get("copart", {})
        .get("interval_minutes", 30)
    )
    secondary_interval = (
        settings.get("scraping", {})
        .get("secondary_sources", {})
        .get("interval_minutes", 60)
    )

    # === US Auction Scrapers ===
    sched.add_job(run_bat_scrape, "interval", minutes=bat_interval, id="bat_scrape")
    sched.add_job(run_copart_scrape, "interval", minutes=copart_interval, id="copart_scrape")
    sched.add_job(run_cars_and_bids_scrape, "interval", minutes=secondary_interval, id="cab_scrape")
    sched.add_job(run_hemmings_scrape, "interval", minutes=secondary_interval, id="hemmings_scrape")

    # === Price Tracking ===
    sched.add_job(
        run_price_history_recording, "interval", minutes=bat_interval, id="price_history",
        start_date="2024-01-01 00:01:00",
    )

    # === Enrichment ===
    sched.add_job(run_fipe_enrichment, "interval", hours=2, id="fipe_enrichment")
    sched.add_job(run_br_market_scrape, "interval", hours=br_market_interval, id="br_market_scrape")
    sched.add_job(
        run_br_market_enrichment, "interval", hours=br_market_interval, id="br_market_enrichment",
        start_date="2024-01-01 00:10:00",
    )

    # === Scoring & Alerts ===
    sched.add_job(
        run_deal_scoring, "interval", minutes=bat_interval, id="deal_scoring",
        start_date="2024-01-01 00:02:00",
    )
    sched.add_job(run_alerts, "interval", minutes=30, id="alerts")
    sched.add_job(run_monitored_auctions, "interval", minutes=5, id="monitored_auctions")
    sched.add_job(run_watchlist_matching, "interval", minutes=15, id="watchlist_matching")
    sched.add_job(run_daily_digest, "cron", hour=8, minute=0, id="daily_digest")

    logger.info("Scheduler started. Press Ctrl+C to exit.")
    logger.info(f"  BaT scrape: every {bat_interval} min")
    logger.info(f"  Copart scrape: every {copart_interval} min")
    logger.info(f"  Cars & Bids / Hemmings: every {secondary_interval} min")
    logger.info("  Price history recording: after each scrape")
    logger.info("  FIPE enrichment: every 2 hours")
    logger.info(f"  BR market scrape: every {br_market_interval} hours")
    logger.info(f"  Deal scoring: every {bat_interval} min")
    logger.info("  Alerts: every 30 min")
    logger.info("  Monitored auctions: every 5 min")
    logger.info("  Watchlist matching: every 15 min")
    logger.info("  Daily digest email: 08:00")

    # Start Telegram command bot (background polling)
    from notifications.telegram_commands import TelegramCommandHandler
    bot = TelegramCommandHandler()
    bot.start_polling()

    # Run initial pipeline
    run_full_pipeline()

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        bot.stop_polling()
        logger.info("Scheduler stopped.")


def main():
    parser = argparse.ArgumentParser(description="Car Auction Deal Finder")
    parser.add_argument(
        "--schedule", action="store_true", help="Run with scheduler (continuous)"
    )
    parser.add_argument("--scrape", action="store_true", help="Only run scraping")
    parser.add_argument("--score", action="store_true", help="Only run scoring")
    parser.add_argument("--alerts", action="store_true", help="Only run alerts")
    parser.add_argument("--init-db", action="store_true", help="Initialize database")

    args = parser.parse_args()

    if args.init_db:
        init_db()
        logger.info("Database initialized.")
        return

    if args.schedule:
        init_db()
        run_scheduled()
    elif args.scrape:
        init_db()
        run_bat_scrape()
        run_copart_scrape()
        run_cars_and_bids_scrape()
        run_hemmings_scrape()
        run_price_history_recording()
        run_fipe_enrichment()
        run_br_market_scrape()
        run_br_market_enrichment()
    elif args.score:
        run_deal_scoring()
    elif args.alerts:
        run_alerts()
    else:
        # Default: run full pipeline once
        run_full_pipeline()


if __name__ == "__main__":
    main()
