"""Hemmings scraper - classic and collector car listings."""

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle
from scrapers.base import BaseScraper

HEMMINGS_BASE = "https://www.hemmings.com"
HEMMINGS_SEARCH = f"{HEMMINGS_BASE}/classifieds/cars-for-sale"


class HemmingsScraper(BaseScraper):
    """Scraper for Hemmings (hemmings.com) vehicle listings."""

    SOURCE_NAME = "hemmings"

    def __init__(self):
        super().__init__()
        settings = load_settings()
        self.filters = settings.get("filters", {})
        self.max_pages = settings.get("scraping", {}).get("hemmings", {}).get("max_pages", 3)

    def scrape_listings(self) -> list[Vehicle]:
        """Scrape active listings from Hemmings matching our filters."""
        vehicles = []
        makes = self.filters.get("makes", [])

        if not makes:
            logger.warning("[Hemmings] No makes configured in filters, skipping")
            return vehicles

        for make in makes:
            try:
                make_vehicles = self._search_make(make)
                vehicles.extend(make_vehicles)
            except Exception as e:
                logger.error(f"[Hemmings] Error scraping {make}: {e}")

        logger.info(f"[Hemmings] Total scraped: {len(vehicles)} vehicles")
        return vehicles

    def _search_make(self, make: str) -> list[Vehicle]:
        """Search Hemmings for listings of a specific make."""
        vehicles = []
        make_slug = make.lower().replace(" ", "-").replace(".", "")
        min_year = self.filters.get("min_year", 2018)

        for page in range(1, self.max_pages + 1):
            url = f"{HEMMINGS_SEARCH}/{make_slug}"
            params = {
                "yearFrom": str(min_year),
                "page": str(page),
                "sort": "listed-date",
            }

            html = self.fetch_page(url, params=params)
            if not html:
                break

            page_vehicles = self._parse_search_page(html, make)
            if not page_vehicles:
                break

            vehicles.extend(page_vehicles)

        logger.info(f"[Hemmings] Found {len(vehicles)} listings for {make}")
        return vehicles

    def _parse_search_page(self, html: str, make_hint: str) -> list[Vehicle]:
        """Parse a Hemmings search results page."""
        soup = BeautifulSoup(html, "lxml")
        vehicles = []

        # Hemmings listing cards
        cards = soup.select(".listing-card, .vehicle-card, [data-listing-id]")
        if not cards:
            # Fallback: try to find listing links
            cards = soup.select("a[href*='/classifieds/cars-for-sale/']")

        for card in cards:
            vehicle = self._parse_card(card, make_hint)
            if vehicle:
                vehicles.append(vehicle)

        return vehicles

    def _parse_card(self, card, make_hint: str) -> Vehicle | None:
        """Parse a single listing card."""
        try:
            # Extract URL
            if card.name == "a":
                url = card.get("href", "")
            else:
                link = card.find("a", href=True)
                url = link.get("href", "") if link else ""

            if not url:
                return None
            if not url.startswith("http"):
                url = HEMMINGS_BASE + url

            # Extract listing ID
            listing_id = card.get("data-listing-id", "")
            if not listing_id:
                id_match = re.search(r"/(\d+)(?:\?|$)", url)
                listing_id = id_match.group(1) if id_match else ""
            if not listing_id:
                return None

            # Extract title text
            title_el = card.select_one("h2, h3, .listing-title, .vehicle-title")
            title_text = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)

            year, make, model = self._parse_title(title_text, make_hint)
            if not make:
                return None

            # Extract price
            price = None
            price_el = card.select_one(".listing-price, .price, [data-price]")
            if price_el:
                price_text = price_el.get("data-price", "") or price_el.get_text(strip=True)
                price = self._parse_price(price_text)
            if not price:
                # Search text for price pattern
                price_match = re.search(r"\$[\d,]+", card.get_text())
                if price_match:
                    price = self._parse_price(price_match.group())

            # Mileage
            mileage = None
            text = card.get_text(strip=True)
            mile_match = re.search(r"([\d,]+)\s*(?:miles?|mi)", text, re.IGNORECASE)
            if mile_match:
                mileage = int(mile_match.group(1).replace(",", ""))

            # Location
            location_state = None
            location_city = None
            loc_el = card.select_one(".location, .listing-location")
            if loc_el:
                loc_text = loc_el.get_text(strip=True)
                state_match = re.search(r",\s*([A-Z]{2})", loc_text)
                if state_match:
                    location_state = state_match.group(1)
                    location_city = loc_text.split(",")[0].strip()

            # Image
            img = card.find("img")
            image_url = ""
            if img:
                image_url = img.get("src", "") or img.get("data-src", "")

            return Vehicle(
                source=self.SOURCE_NAME,
                source_id=f"hem_{listing_id}",
                url=url,
                make=make,
                model=model,
                year=year or 0,
                current_bid_usd=price,  # Hemmings uses asking prices, not bids
                mileage=mileage,
                title_status="clean",  # Hemmings typically lists clean-title vehicles
                location_state=location_state,
                location_city=location_city,
                image_urls=json.dumps([image_url]) if image_url else None,
            )
        except Exception as e:
            logger.debug(f"[Hemmings] Failed to parse card: {e}")
            return None

    @staticmethod
    def _parse_title(title: str, make_hint: str) -> tuple[int | None, str, str]:
        """Parse a listing title like '2021 Porsche 911 Turbo S'."""
        if not title:
            return None, "", ""

        year_match = re.match(r"(\d{4})\s+", title)
        year = int(year_match.group(1)) if year_match else None
        rest = title[year_match.end():].strip() if year_match else title.strip()

        # Try to use the make hint
        if make_hint and rest.lower().startswith(make_hint.lower()):
            model = rest[len(make_hint):].strip()
            return year, make_hint, model

        # Fallback
        parts = rest.split(None, 1)
        if len(parts) >= 2:
            return year, parts[0], parts[1]
        elif parts:
            return year, parts[0], ""
        return year, "", ""

    @staticmethod
    def _parse_price(text: str) -> float | None:
        if not text:
            return None
        cleaned = re.sub(r"[^\d.]", "", text)
        try:
            val = float(cleaned)
            return val if val > 1000 else None  # Filter out nonsense
        except (ValueError, TypeError):
            return None
