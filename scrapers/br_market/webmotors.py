"""Webmotors scraper - Brazilian vehicle marketplace prices."""

import re
import time

import requests
from loguru import logger

from config import load_settings

# Webmotors has a public search API used by their frontend
WEBMOTORS_API = "https://www.webmotors.com.br/api/search/car"

# Mapping of common makes to Webmotors make IDs
MAKE_MAP = {
    "porsche": "PORSCHE",
    "bmw": "BMW",
    "mercedes-benz": "MERCEDES-BENZ",
    "ferrari": "FERRARI",
    "lamborghini": "LAMBORGHINI",
    "audi": "AUDI",
    "mclaren": "MCLAREN",
    "aston martin": "ASTON MARTIN",
    "bentley": "BENTLEY",
    "maserati": "MASERATI",
    "land rover": "LAND ROVER",
    "jaguar": "JAGUAR",
    "volvo": "VOLVO",
    "volkswagen": "VOLKSWAGEN",
    "toyota": "TOYOTA",
    "ford": "FORD",
    "chevrolet": "CHEVROLET",
    "rolls-royce": "ROLLS-ROYCE",
}


class WebmotorsScraper:
    """Scraper for Webmotors vehicle listings."""

    SOURCE_NAME = "webmotors"

    def __init__(self):
        settings = load_settings()
        scraping = settings.get("scraping", {})
        self.request_delay = scraping.get("request_delay_seconds", 2)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": scraping.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "Referer": "https://www.webmotors.com.br/",
        })

    def search_listings(self, make: str, model: str, year_from: int, year_to: int | None = None) -> list[dict]:
        """Search Webmotors for vehicle listings.

        Returns a list of parsed listing dicts with price, mileage, location, etc.
        """
        wm_make = MAKE_MAP.get(make.lower(), make.upper())
        year_to = year_to or year_from

        all_listings = []
        page = 1
        max_pages = 5  # Limit to avoid excessive requests

        while page <= max_pages:
            time.sleep(self.request_delay)

            params = {
                "url": f"https://www.webmotors.com.br/carros/{wm_make.lower().replace(' ', '-')}/"
                       f"{model.lower().replace(' ', '-')}",
                "make1": wm_make,
                "model1": model.upper(),
                "yearfrom": str(year_from),
                "yearto": str(year_to),
                "page": str(page),
                "order": "1",  # Sort by price
                "showMenu": "true",
                "showCount": "true",
                "showBreadCrumb": "true",
            }

            try:
                response = self.session.get(WEBMOTORS_API, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                logger.error(f"[Webmotors] API request failed (page {page}): {e}")
                break
            except ValueError:
                logger.error(f"[Webmotors] Invalid JSON response for {make} {model}")
                break

            search_results = data.get("SearchResults", [])
            if not search_results:
                break

            for item in search_results:
                listing = self._parse_listing(item)
                if listing:
                    all_listings.append(listing)

            # Check if there are more pages
            total_count = data.get("Count", 0)
            if page * 24 >= total_count:  # 24 results per page
                break

            page += 1

        logger.info(f"[Webmotors] Found {len(all_listings)} listings for {make} {model} ({year_from}-{year_to})")
        return all_listings

    def _parse_listing(self, item: dict) -> dict | None:
        """Parse a single Webmotors search result into a normalized dict."""
        try:
            spec = item.get("Specification", {})
            seller = item.get("Seller", {})

            price = item.get("Prices", {}).get("Price", 0)
            if not price or price <= 0:
                return None

            # Extract mileage (km)
            mileage_str = spec.get("Odometer", "0")
            mileage = self._parse_number(str(mileage_str))

            # Extract location
            city = seller.get("City", "")
            state = seller.get("State", "")

            listing_id = str(item.get("UniqueId", ""))
            if not listing_id:
                return None

            return {
                "source": self.SOURCE_NAME,
                "source_id": f"wm_{listing_id}",
                "url": f"https://www.webmotors.com.br/comprar/{listing_id}",
                "make": spec.get("Make", {}).get("Value", ""),
                "model": spec.get("Model", {}).get("Value", ""),
                "year": spec.get("YearFabrication", 0),
                "trim": spec.get("Version", {}).get("Value", ""),
                "price_brl": float(price),
                "mileage_km": mileage,
                "location_state": state[:2] if state else None,
                "location_city": city,
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"[Webmotors] Failed to parse listing: {e}")
            return None

    @staticmethod
    def _parse_number(text: str) -> int:
        """Parse a number string, removing formatting."""
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else 0

    def scrape_for_vehicle(self, make: str, model: str, year: int) -> list[dict]:
        """Convenience method to search for a specific vehicle spec.

        Searches year-1 to year+1 to capture similar listings.
        """
        return self.search_listings(make, model, year - 1, year + 1)
