"""Bring a Trailer scraper - extracts active auction listings."""

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle
from scrapers.base import BaseScraper


class BringATrailerScraper(BaseScraper):
    """Scraper for Bring a Trailer (bringatrailer.com) auctions."""

    SOURCE_NAME = "bat"

    def __init__(self):
        super().__init__()
        settings = load_settings()
        bat_config = settings.get("scraping", {}).get("bring_a_trailer", {})
        self.base_url = bat_config.get("base_url", "https://bringatrailer.com")

    def scrape_listings(self) -> list[Vehicle]:
        """Scrape active auction listings from BaT."""
        vehicles = []
        logger.info("[BaT] Starting scrape of active auctions...")

        # BaT has an auctions page with active listings
        html = self.fetch_page(f"{self.base_url}/auctions/")
        if not html:
            logger.error("[BaT] Failed to fetch auctions page")
            return vehicles

        soup = BeautifulSoup(html, "lxml")
        listing_items = soup.select(".auctions-item, .listing-card, .auction-item")

        if not listing_items:
            # Try alternative selectors for BaT's layout
            listing_items = soup.select("a[href*='/listing/']")
            listing_items = self._deduplicate_links(listing_items)

        logger.info(f"[BaT] Found {len(listing_items)} listing links on auctions page")

        for item in listing_items:
            try:
                vehicle = self._parse_listing_card(item)
                if vehicle:
                    vehicles.append(vehicle)
            except Exception as e:
                logger.warning(f"[BaT] Error parsing listing card: {e}")
                continue

        logger.info(f"[BaT] Successfully scraped {len(vehicles)} vehicles")
        return vehicles

    def fetch_single_listing(self, url: str) -> Vehicle | None:
        """Fetch a single BaT listing by URL."""
        return self.scrape_listing_detail(url)

    def scrape_listing_detail(self, url: str) -> Vehicle | None:
        """Scrape detailed info from a single listing page."""
        html = self.fetch_page(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        return self._parse_detail_page(soup, url)

    def _parse_listing_card(self, element) -> Vehicle | None:
        """Parse a listing card/link from the auctions page."""
        # Extract URL
        url = element.get("href", "") if element.name == "a" else ""
        if not url:
            link = element.find("a", href=True)
            if link:
                url = link["href"]
        if not url or "/listing/" not in url:
            return None

        if not url.startswith("http"):
            url = self.base_url + url

        # Extract source_id from URL
        source_id = self._extract_source_id(url)
        if not source_id:
            return None

        # Try to extract basic info from the card text
        text = element.get_text(strip=True)
        year, make, model = self._parse_title(text)

        if not make:
            return None

        # Extract current bid if visible
        bid = self._extract_bid(element)

        # Extract image
        img = element.find("img")
        image_url = img.get("src", "") if img else ""

        vehicle = Vehicle(
            source=self.SOURCE_NAME,
            source_id=source_id,
            url=url,
            make=make,
            model=model,
            year=year or 0,
            current_bid_usd=bid,
            image_urls=json.dumps([image_url]) if image_url else None,
        )

        return vehicle

    def _parse_detail_page(self, soup: BeautifulSoup, url: str) -> Vehicle | None:
        """Parse a full listing detail page for comprehensive vehicle data."""
        source_id = self._extract_source_id(url)

        # Title parsing
        title_el = soup.find("h1", class_="post-title") or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""
        year, make, model = self._parse_title(title)

        if not make:
            return None

        # Current bid
        bid = None
        bid_el = soup.select_one(".info-value.bid-value, .current-bid .dollar, .bid-value")
        if bid_el:
            bid = self._parse_price(bid_el.get_text())

        # Mileage
        mileage = None
        essentials = soup.select(".listing-essentials-item, .essentials li, .detail-item")
        for item in essentials:
            text = item.get_text(strip=True).lower()
            if "mile" in text or "km" in text:
                mileage = self._parse_number(text)
                break

        # VIN
        vin = None
        for item in essentials:
            text = item.get_text(strip=True)
            if "VIN" in text or len(text) == 17 and text.isalnum():
                vin_match = re.search(r"[A-HJ-NPR-Z0-9]{17}", text)
                if vin_match:
                    vin = vin_match.group()
                    break

        # Location
        location_state = None
        location_city = None
        for item in essentials:
            text = item.get_text(strip=True)
            state_match = re.search(r",\s*([A-Z]{2})$", text)
            if state_match:
                location_state = state_match.group(1)
                location_city = text.split(",")[0].strip()
                break

        # Auction end time
        auction_end = None
        timer_el = soup.select_one("[data-end], .auction-timer, .countdown")
        if timer_el:
            end_str = timer_el.get("data-end", "")
            if end_str:
                try:
                    auction_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

        # Images
        images = []
        for img in soup.select(".gallery img, .carousel img, .listing-image img"):
            src = img.get("src", "") or img.get("data-src", "")
            if src and "thumb" not in src.lower():
                images.append(src)

        # Title status
        title_status = "clean"  # BaT typically lists clean-title vehicles
        for item in essentials:
            text = item.get_text(strip=True).lower()
            if "salvage" in text:
                title_status = "salvage"
            elif "rebuilt" in text:
                title_status = "rebuilt"

        vehicle = Vehicle(
            source=self.SOURCE_NAME,
            source_id=source_id,
            url=url,
            make=make,
            model=model,
            year=year or 0,
            vin=vin,
            current_bid_usd=bid,
            mileage=mileage,
            title_status=title_status,
            location_state=location_state,
            location_city=location_city,
            image_urls=json.dumps(images[:10]) if images else None,
            auction_end=auction_end,
        )

        return vehicle

    @staticmethod
    def _extract_source_id(url: str) -> str | None:
        """Extract a unique listing ID from a BaT URL."""
        match = re.search(r"/listing/([^/?]+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_title(title: str) -> tuple[int | None, str, str]:
        """Parse a BaT listing title into year, make, model.

        BaT titles are typically like: '2020 Porsche 911 Carrera S'
        """
        if not title:
            return None, "", ""

        # Clean up common prefixes
        title = re.sub(r"^(No Reserve:\s*|Reserve Met:\s*)", "", title, flags=re.IGNORECASE)

        # Extract year
        year_match = re.match(r"(\d{4})\s+", title)
        year = int(year_match.group(1)) if year_match else None

        rest = title[year_match.end():].strip() if year_match else title.strip()

        # Known makes for splitting make/model
        known_makes = [
            "Alfa Romeo", "Aston Martin", "Mercedes-Benz", "Land Rover",
            "Rolls-Royce", "De Tomaso",
            "Porsche", "BMW", "Ferrari", "Lamborghini", "McLaren", "Audi",
            "Bentley", "Maserati", "Jaguar", "Lotus", "Toyota", "Honda",
            "Nissan", "Ford", "Chevrolet", "Dodge", "Jeep", "Tesla",
            "Volkswagen", "Volvo", "Lexus", "Acura", "Infiniti", "Genesis",
            "Cadillac", "Lincoln", "Buick", "GMC", "RAM", "Chrysler",
            "Subaru", "Mazda", "Hyundai", "Kia", "Mini", "Fiat",
            "Mitsubishi", "Suzuki", "Peugeot", "Renault", "Citroën",
        ]

        for make in known_makes:
            if rest.lower().startswith(make.lower()):
                model = rest[len(make):].strip()
                return year, make, model

        # Fallback: first word is make, rest is model
        parts = rest.split(None, 1)
        if len(parts) >= 2:
            return year, parts[0], parts[1]
        elif parts:
            return year, parts[0], ""
        return year, "", ""

    @staticmethod
    def _extract_bid(element) -> float | None:
        """Extract current bid price from a listing element."""
        bid_el = element.select_one(".bid-value, .current-bid, .dollar")
        if bid_el:
            return BringATrailerScraper._parse_price(bid_el.get_text())
        # Try to find price in text
        text = element.get_text()
        match = re.search(r"\$[\d,]+", text)
        if match:
            return BringATrailerScraper._parse_price(match.group())
        return None

    @staticmethod
    def _parse_price(text: str) -> float | None:
        """Parse a price string like '$45,000' into a float."""
        if not text:
            return None
        cleaned = re.sub(r"[^\d.]", "", text)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_number(text: str) -> int | None:
        """Parse a number from text like '45,234 miles'."""
        match = re.search(r"[\d,]+", text)
        if match:
            try:
                return int(match.group().replace(",", ""))
            except ValueError:
                return None
        return None

    @staticmethod
    def _deduplicate_links(links) -> list:
        """Remove duplicate links based on href."""
        seen = set()
        unique = []
        for link in links:
            href = link.get("href", "")
            if href and href not in seen:
                seen.add(href)
                unique.append(link)
        return unique
