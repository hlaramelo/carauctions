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
import sys

from dotenv import load_dotenv
from loguru import logger

from models.database import init_db
from scheduler import (
    run_bat_scrape,
    run_deal_scoring,
    run_fipe_enrichment,
    run_alerts,
    run_full_pipeline,
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
logger.add(
    "logs/carauctions_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG",
)


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

    # BaT scraping every N minutes
    sched.add_job(run_bat_scrape, "interval", minutes=bat_interval, id="bat_scrape")

    # FIPE enrichment every 2 hours
    sched.add_job(run_fipe_enrichment, "interval", hours=2, id="fipe_enrichment")

    # Deal scoring after each scrape cycle (every N minutes, offset by 2 min)
    sched.add_job(
        run_deal_scoring, "interval", minutes=bat_interval, id="deal_scoring",
        start_date="2024-01-01 00:02:00",
    )

    # Alerts every 30 minutes
    sched.add_job(run_alerts, "interval", minutes=30, id="alerts")

    logger.info("Scheduler started. Press Ctrl+C to exit.")
    logger.info(f"  BaT scrape: every {bat_interval} min")
    logger.info("  FIPE enrichment: every 2 hours")
    logger.info(f"  Deal scoring: every {bat_interval} min")
    logger.info("  Alerts: every 30 min")

    # Run initial pipeline
    run_full_pipeline()

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
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
        run_fipe_enrichment()
    elif args.score:
        run_deal_scoring()
    elif args.alerts:
        run_alerts()
    else:
        # Default: run full pipeline once
        run_full_pipeline()


if __name__ == "__main__":
    main()
