"""Copart scraper - US salvage/clean title auction listings.

Copart uses a dynamic JS frontend, so we rely on their search API
which the frontend calls. This avoids needing Selenium/Playwright.
"""

import re
import time
from datetime import datetime, timezone

import requests
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle
from scrapers.base import BaseScraper

# Copart's internal search API used by their frontend
COPART_SEARCH_URL = "https://www.copart.com/public/lots/search"
COPART_DETAIL_URL = "https://www.copart.com/public/data/lotdetails/solr"


class CopartScraper(BaseScraper):
    """Scraper for Copart (copart.com) auction listings."""

    SOURCE_NAME = "copart"

    def __init__(self):
        super().__init__()
        settings = load_settings()
        copart_config = settings.get("scraping", {}).get("copart", {})
        self.max_pages = copart_config.get("max_pages", 5)
        self.filters = settings.get("filters", {})

        # Copart needs specific headers for their API
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://www.copart.com/lotSearchResults/",
        })

    def scrape_listings(self) -> list[Vehicle]:
        """Scrape active auction listings from Copart matching our filters."""
        vehicles = []
        makes = self.filters.get("makes", [])

        if not makes:
            logger.warning("[Copart] No makes configured in filters, skipping")
            return vehicles

        for make in makes:
            try:
                make_vehicles = self._search_make(make)
                vehicles.extend(make_vehicles)
            except Exception as e:
                logger.error(f"[Copart] Error scraping {make}: {e}")

        logger.info(f"[Copart] Total scraped: {len(vehicles)} vehicles")
        return vehicles

    def _search_make(self, make: str) -> list[Vehicle]:
        """Search Copart for a specific make."""
        vehicles = []
        min_year = self.filters.get("min_year", 2018)

        for page in range(self.max_pages):
            time.sleep(self.request_delay)

            payload = {
                "query": ["*"],
                "filter": {
                    "MAKE": [make.upper()],
                    "YEAR": [f"{min_year} TO 2027"],
                    "TITL": ["CLEAN TITLE", "REBUILT TITLE"],
                },
                "sort": ["auction_date_type desc"],
                "page": page,
                "size": 50,
            }

            try:
                response = self.session.post(
                    COPART_SEARCH_URL, json=payload, timeout=15
                )
                if response.status_code == 403:
                    logger.warning("[Copart] Blocked (403). May need proxy rotation.")
                    break
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                logger.error(f"[Copart] Search API failed (page {page}): {e}")
                break
            except ValueError:
                logger.error("[Copart] Invalid JSON response")
                break

            results = data.get("data", {}).get("results", {})
            content = results.get("content", [])
            if not content:
                break

            for item in content:
                vehicle = self._parse_result(item)
                if vehicle:
                    vehicles.append(vehicle)

            total_elements = results.get("totalElements", 0)
            if (page + 1) * 50 >= total_elements:
                break

        logger.info(f"[Copart] Found {len(vehicles)} listings for {make}")
        return vehicles

    def _parse_result(self, item: dict) -> Vehicle | None:
        """Parse a single Copart search result."""
        try:
            lot_number = str(item.get("ln", ""))
            if not lot_number:
                return None

            make = item.get("mkn", "")
            model = item.get("mmod", "")
            year = item.get("lcy", 0)

            if not make or not year:
                return None

            # Current bid
            current_bid = item.get("dynamicLotDetails", {}).get("currentBid", 0)
            buy_now = item.get("bnp", None)

            # Mileage
            mileage_str = str(item.get("orr", "0"))
            mileage = self._parse_mileage(mileage_str)

            # Title
            title_raw = item.get("tims", "")
            title_status = self._normalize_title_status(title_raw)

            # Damage
            primary_damage = item.get("dd", "")
            secondary_damage = item.get("sdd", "")
            damage_desc = ", ".join(filter(None, [primary_damage, secondary_damage]))

            # Location
            location = item.get("yn", "")
            state_match = re.search(r"- (\w{2})$", location) if location else None
            location_state = state_match.group(1) if state_match else None
            location_city = location.split(" - ")[0].strip() if location else None

            # Auction end
            auction_end = None
            auction_ts = item.get("dynamicLotDetails", {}).get("saleDate")
            if auction_ts:
                try:
                    auction_end = datetime.fromtimestamp(auction_ts / 1000, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    pass

            # Images
            image_url = item.get("tims", "")
            if not image_url:
                image_url = f"https://cs.copart.com/v1/AUTH_svc.pdoc00001/{lot_number}/1.jpg"

            # Engine
            engine_str = item.get("egn", "")
            engine_cc = self._parse_engine_cc(engine_str)

            # VIN
            vin = item.get("fv", "")

            url = f"https://www.copart.com/lot/{lot_number}"

            return Vehicle(
                source=self.SOURCE_NAME,
                source_id=f"copart_{lot_number}",
                url=url,
                make=make.title(),
                model=model.title() if model else "",
                year=int(year),
                vin=vin if vin and len(vin) == 17 else None,
                current_bid_usd=float(current_bid) if current_bid else None,
                buy_now_price_usd=float(buy_now) if buy_now else None,
                mileage=mileage,
                title_status=title_status,
                damage_description=damage_desc or None,
                location_state=location_state,
                location_city=location_city,
                image_urls=f'["{image_url}"]' if image_url else None,
                engine_cc=engine_cc,
                auction_end=auction_end,
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"[Copart] Failed to parse result: {e}")
            return None

    @staticmethod
    def _parse_mileage(text: str) -> int | None:
        cleaned = re.sub(r"[^\d]", "", text)
        if cleaned:
            val = int(cleaned)
            return val if val < 500000 else None  # Sanity check
        return None

    @staticmethod
    def _normalize_title_status(title: str) -> str:
        title_lower = title.lower() if title else ""
        if "salvage" in title_lower:
            return "salvage"
        elif "rebuilt" in title_lower:
            return "rebuilt"
        elif "clean" in title_lower:
            return "clean"
        return "unknown"

    @staticmethod
    def _parse_engine_cc(engine_str: str) -> int | None:
        """Parse engine displacement from string like '3.0L' into cc."""
        if not engine_str:
            return None
        match = re.search(r"(\d+\.?\d*)\s*[lL]", engine_str)
        if match:
            liters = float(match.group(1))
            return int(liters * 1000)
        return None
