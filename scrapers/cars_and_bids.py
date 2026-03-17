"""Cars & Bids scraper - enthusiast car auction platform by Doug DeMuro.

Replaces PC Car Market (which has limited inventory). Cars & Bids is a
more active platform for the target segment of luxury/enthusiast cars.
"""

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle
from scrapers.base import BaseScraper

CARSANDBIDS_BASE = "https://carsandbids.com"


class CarsAndBidsScraper(BaseScraper):
    """Scraper for Cars & Bids (carsandbids.com) auction listings."""

    SOURCE_NAME = "carsandbids"

    def __init__(self):
        super().__init__()
        settings = load_settings()
        self.filters = settings.get("filters", {})
        self.max_pages = settings.get("scraping", {}).get("cars_and_bids", {}).get("max_pages", 3)

    def scrape_listings(self) -> list[Vehicle]:
        """Scrape active auction listings from Cars & Bids."""
        vehicles = []
        logger.info("[CarsAndBids] Starting scrape of active auctions...")

        for page in range(1, self.max_pages + 1):
            html = self.fetch_page(f"{CARSANDBIDS_BASE}/auctions", params={"page": str(page)})
            if not html:
                break

            page_vehicles = self._parse_auctions_page(html)
            if not page_vehicles:
                break

            vehicles.extend(page_vehicles)

        # Filter by configured makes
        makes = self.filters.get("makes", [])
        if makes:
            makes_lower = {m.lower() for m in makes}
            vehicles = [v for v in vehicles if v.make.lower() in makes_lower]

        logger.info(f"[CarsAndBids] Successfully scraped {len(vehicles)} vehicles")
        return vehicles

    def _parse_auctions_page(self, html: str) -> list[Vehicle]:
        """Parse the auctions listing page."""
        soup = BeautifulSoup(html, "lxml")
        vehicles = []

        # Cars & Bids auction cards
        cards = soup.select(".auction-card, .auction-item, [data-auction-id]")
        if not cards:
            cards = soup.select("a[href*='/auctions/']")
            cards = self._deduplicate_links(cards)

        for card in cards:
            vehicle = self._parse_auction_card(card)
            if vehicle:
                vehicles.append(vehicle)

        return vehicles

    def _parse_auction_card(self, card) -> Vehicle | None:
        """Parse a single auction card from the listings page."""
        try:
            # URL
            if card.name == "a":
                url = card.get("href", "")
            else:
                link = card.find("a", href=True)
                url = link.get("href", "") if link else ""

            if not url:
                return None
            if not url.startswith("http"):
                url = CARSANDBIDS_BASE + url

            # Extract auction ID
            auction_id = card.get("data-auction-id", "")
            if not auction_id:
                id_match = re.search(r"/auctions/([^/?]+)", url)
                auction_id = id_match.group(1) if id_match else ""
            if not auction_id:
                return None

            # Title
            title_el = card.select_one("h2, h3, .auction-title, .title")
            title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)

            year, make, model = self._parse_title(title)
            if not make:
                return None

            # Current bid
            bid = None
            bid_el = card.select_one(".current-bid, .bid-value, .auction-bid")
            if bid_el:
                bid = self._parse_price(bid_el.get_text())
            if not bid:
                bid_match = re.search(r"\$[\d,]+", card.get_text())
                if bid_match:
                    bid = self._parse_price(bid_match.group())

            # Time remaining
            auction_end = None
            time_el = card.select_one(".time-left, .auction-time, [data-end-time]")
            if time_el:
                end_str = time_el.get("data-end-time", "")
                if end_str:
                    try:
                        auction_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

            # Reserve status
            reserve_met = None
            text = card.get_text(strip=True).lower()
            if "no reserve" in text:
                reserve_met = True  # No reserve means it will sell
            elif "reserve met" in text:
                reserve_met = True
            elif "reserve not met" in text:
                reserve_met = False

            # Image
            img = card.find("img")
            image_url = ""
            if img:
                image_url = img.get("src", "") or img.get("data-src", "")

            # Mileage from card text
            mileage = None
            mile_match = re.search(r"([\d,]+)\s*(?:miles?|mi)", text)
            if mile_match:
                mileage = int(mile_match.group(1).replace(",", ""))

            min_year = self.filters.get("min_year", 0)
            if year and year < min_year:
                return None

            return Vehicle(
                source=self.SOURCE_NAME,
                source_id=f"cab_{auction_id}",
                url=url,
                make=make,
                model=model,
                year=year or 0,
                current_bid_usd=bid,
                reserve_met=reserve_met,
                mileage=mileage,
                title_status="clean",  # C&B typically lists clean-title
                image_urls=json.dumps([image_url]) if image_url else None,
                auction_end=auction_end,
            )
        except Exception as e:
            logger.debug(f"[CarsAndBids] Failed to parse card: {e}")
            return None

    @staticmethod
    def _parse_title(title: str) -> tuple[int | None, str, str]:
        """Parse a title like '2020 Porsche 911 Carrera S'."""
        if not title:
            return None, "", ""

        title = re.sub(r"^(No Reserve:\s*)", "", title, flags=re.IGNORECASE)

        year_match = re.match(r"(\d{4})\s+", title)
        year = int(year_match.group(1)) if year_match else None
        rest = title[year_match.end():].strip() if year_match else title.strip()

        known_makes = [
            "Alfa Romeo", "Aston Martin", "Mercedes-Benz", "Land Rover",
            "Rolls-Royce", "De Tomaso",
            "Porsche", "BMW", "Ferrari", "Lamborghini", "McLaren", "Audi",
            "Bentley", "Maserati", "Jaguar", "Lotus", "Toyota", "Honda",
            "Nissan", "Ford", "Chevrolet", "Dodge", "Jeep", "Tesla",
            "Volkswagen", "Volvo", "Lexus", "Acura", "Mini", "Fiat",
        ]

        for make in known_makes:
            if rest.lower().startswith(make.lower()):
                model = rest[len(make):].strip()
                return year, make, model

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
            return val if val > 100 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _deduplicate_links(links) -> list:
        seen = set()
        unique = []
        for link in links:
            href = link.get("href", "")
            if href and href not in seen:
                seen.add(href)
                unique.append(link)
        return unique
