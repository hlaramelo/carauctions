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

    def fetch_single_listing(self, url: str) -> Vehicle | None:
        """Fetch a single Copart lot by URL using the detail API."""
        lot_match = re.search(r"/lot/(\d+)", url)
        if not lot_match:
            logger.error(f"[Copart] Cannot extract lot number from URL: {url}")
            return None

        lot_number = lot_match.group(1)
        logger.info(f"[Copart] Fetching single lot: {lot_number}")

        # Try detail API
        try:
            time.sleep(self.request_delay)
            response = self.session.get(
                COPART_DETAIL_URL,
                params={"lotNumber": lot_number},
                timeout=15,
            )
            if response.status_code != 403:
                response.raise_for_status()
                data = response.json()
                logger.debug(f"[Copart] Detail API keys: {list(data.get('data', {}).keys())}")

                # Try multiple possible structures
                lot_data = (
                    data.get("data", {}).get("lotDetails")
                    or data.get("data", {}).get("lotDetail")
                    or data.get("data", {})
                )
                if lot_data:
                    logger.debug(f"[Copart] Lot data keys: {list(lot_data.keys())[:20]}")
                    vehicle = self._parse_result(lot_data)
                    if vehicle:
                        return vehicle
                    logger.warning(f"[Copart] Parse returned None, lot_data sample: make={lot_data.get('mkn')}, makeName={lot_data.get('makeName')}, ln={lot_data.get('ln')}")
            else:
                logger.warning("[Copart] Blocked (403) on detail API")
        except requests.RequestException as e:
            logger.error(f"[Copart] Detail API failed for lot {lot_number}: {e}")
        except ValueError:
            logger.error("[Copart] Invalid JSON from detail API")

        # Fallback: try to parse info from the URL slug itself
        vehicle = self._parse_from_url(url, lot_number)
        if vehicle:
            logger.info(f"[Copart] Parsed from URL: {vehicle.year} {vehicle.make} {vehicle.model}")
        return vehicle

    def _parse_from_url(self, url: str, lot_number: str) -> Vehicle | None:
        """Fallback: extract basic info from URL slug when API fails."""
        # URL pattern: /lot/78272755/clean-title-2006-mercedes-benz-slk-55-amg-pa-philadelphia
        slug_match = re.search(r"/lot/\d+/?\??[^/]*$", url)
        if not slug_match:
            # Try from Photos URL pattern
            slug_match = re.search(r"/lot/\d+/Photos/(.+?)(?:\?|$)", url)
        else:
            slug_match = re.search(r"/lot/\d+/(.+?)(?:\?|$)", url)

        if not slug_match:
            return None

        slug = slug_match.group(1).lower()
        # e.g. "clean-title-2006-mercedes-benz-slk-55-amg-pa-philadelphia"

        # Extract title status
        title_status = "unknown"
        for ts in ["clean-title", "salvage-title", "rebuilt-title"]:
            if ts in slug:
                title_status = ts.replace("-title", "")
                slug = slug.replace(ts + "-", "")
                break

        # Extract year (4 digits)
        year_match = re.search(r"(\d{4})", slug)
        if not year_match:
            return None
        year = int(year_match.group(1))
        slug = slug[:year_match.start()] + slug[year_match.end():]
        slug = slug.strip("-")

        # Remaining slug has make-model-...-state-city
        # Remove trailing state abbreviation and city
        parts = [p for p in slug.split("-") if p]
        # Remove last 1-2 parts that are likely state/city
        if len(parts) >= 3:
            # Check if second to last is a 2-letter state
            if len(parts[-2]) == 2:
                location_state = parts[-2].upper()
                parts = parts[:-2]
            elif len(parts[-1]) == 2:
                location_state = parts[-1].upper()
                parts = parts[:-1]
            else:
                location_state = None
        else:
            location_state = None

        if not parts:
            return None

        # First part is make, rest is model
        make = parts[0]
        model = " ".join(parts[1:]) if len(parts) > 1 else ""

        return Vehicle(
            source=self.SOURCE_NAME,
            source_id=f"copart_{lot_number}",
            url=f"https://www.copart.com/lot/{lot_number}",
            make=make.title(),
            model=model.title() if model else "",
            year=year,
            title_status=title_status,
            location_state=location_state,
        )

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
        """Parse a single Copart result (handles both search API and detail API fields)."""
        try:
            # Lot number: search API uses "ln", detail API uses "ln" or "lotNumberStr"
            lot_number = str(item.get("ln") or item.get("lotNumberStr") or "")
            if not lot_number:
                return None

            # Make/Model/Year: search API uses abbreviated, detail API uses full names
            make = item.get("mkn") or item.get("mkn") or item.get("makeName") or item.get("makeDesc") or ""
            model = item.get("mmod") or item.get("modelName") or item.get("modelDesc") or ""
            year = item.get("lcy") or item.get("lcy") or item.get("lotYear") or item.get("yr") or 0

            if not make or not year:
                return None

            # Current bid: search API nests in dynamicLotDetails, detail API may have it at top level
            dynamic = item.get("dynamicLotDetails") or {}
            current_bid = (
                dynamic.get("currentBid")
                or item.get("currentBid")
                or item.get("highBidAmount")
                or 0
            )
            buy_now = item.get("bnp") or item.get("buyNowPrice") or None

            # Mileage
            mileage_str = str(item.get("orr") or item.get("odometerReading") or item.get("odometer") or "0")
            mileage = self._parse_mileage(mileage_str)

            # Title
            title_raw = item.get("tims") or item.get("titleStatus") or item.get("titleCode") or ""
            title_status = self._normalize_title_status(title_raw)

            # Damage
            primary_damage = item.get("dd") or item.get("primaryDamage") or item.get("damageDescription") or ""
            secondary_damage = item.get("sdd") or item.get("secondaryDamage") or ""
            damage_desc = ", ".join(filter(None, [primary_damage, secondary_damage]))

            # Location
            location = item.get("yn") or item.get("yardName") or item.get("facilityName") or ""
            state_match = re.search(r"- (\w{2})$", location) if location else None
            location_state = state_match.group(1) if state_match else None
            location_city = location.split(" - ")[0].strip() if location else None

            # Auction end
            auction_end = None
            auction_ts = dynamic.get("saleDate") or item.get("saleDate") or item.get("auctionDate")
            if auction_ts:
                try:
                    ts = auction_ts if isinstance(auction_ts, (int, float)) else int(auction_ts)
                    # Copart timestamps are in milliseconds
                    if ts > 1e12:
                        ts = ts / 1000
                    auction_end = datetime.fromtimestamp(ts, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    pass

            # Images
            image_url = item.get("imageUrl") or item.get("tims") or ""
            if not image_url or "http" not in str(image_url):
                image_url = f"https://cs.copart.com/v1/AUTH_svc.pdoc00001/{lot_number}/1.jpg"

            # Engine
            engine_str = item.get("egn") or item.get("engineSize") or item.get("engineType") or ""
            engine_cc = self._parse_engine_cc(engine_str)

            # VIN
            vin = item.get("fv") or item.get("vin") or ""

            # Trim
            trim = item.get("trim") or item.get("seriesName") or ""

            url = f"https://www.copart.com/lot/{lot_number}"

            return Vehicle(
                source=self.SOURCE_NAME,
                source_id=f"copart_{lot_number}",
                url=url,
                make=make.title(),
                model=model.title() if model else "",
                trim=trim.title() if trim else None,
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
