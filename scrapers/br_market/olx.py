"""OLX Brasil scraper - Brazilian marketplace vehicle prices."""

import re
import time

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import load_settings

OLX_SEARCH_URL = "https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios"

# OLX uses slug-based URLs for makes
MAKE_SLUG_MAP = {
    "porsche": "porsche",
    "bmw": "bmw",
    "mercedes-benz": "mercedes-benz",
    "ferrari": "ferrari",
    "lamborghini": "lamborghini",
    "audi": "audi",
    "mclaren": "mclaren",
    "aston martin": "aston-martin",
    "bentley": "bentley",
    "maserati": "maserati",
    "land rover": "land-rover",
    "jaguar": "jaguar",
    "volvo": "volvo",
    "volkswagen": "volkswagen",
    "toyota": "toyota",
    "ford": "ford",
    "chevrolet": "chevrolet",
    "rolls-royce": "rolls-royce",
}


class OLXScraper:
    """Scraper for OLX Brasil vehicle listings."""

    SOURCE_NAME = "olx"

    def __init__(self):
        settings = load_settings()
        scraping = settings.get("scraping", {})
        self.request_delay = scraping.get("request_delay_seconds", 3)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": scraping.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9",
        })

    def search_listings(self, make: str, model: str, year_from: int, year_to: int | None = None) -> list[dict]:
        """Search OLX for vehicle listings by make/model/year range.

        Returns a list of parsed listing dicts.
        """
        make_slug = MAKE_SLUG_MAP.get(make.lower(), make.lower().replace(" ", "-"))
        model_slug = model.lower().replace(" ", "-")
        year_to = year_to or year_from

        all_listings = []
        max_pages = 3  # OLX is more aggressive with rate limiting

        for page in range(1, max_pages + 1):
            time.sleep(self.request_delay)

            # OLX search URL pattern
            url = f"{OLX_SEARCH_URL}/{make_slug}/{model_slug}"
            params = {
                "pe": str(year_to),
                "ps": str(year_from),
                "o": str(page),
            }

            try:
                response = self.session.get(url, params=params, timeout=15)
                if response.status_code == 403:
                    logger.warning("[OLX] Rate limited (403). Backing off.")
                    time.sleep(10)
                    break
                response.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"[OLX] Request failed (page {page}): {e}")
                break

            listings = self._parse_search_page(response.text, make)
            if not listings:
                break

            all_listings.extend(listings)

        logger.info(f"[OLX] Found {len(all_listings)} listings for {make} {model} ({year_from}-{year_to})")
        return all_listings

    def _parse_search_page(self, html: str, make: str) -> list[dict]:
        """Parse an OLX search results page."""
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # OLX uses ad cards in their search results
        ad_cards = soup.select("[data-ds-component='DS-NewAdCard-Link']")
        if not ad_cards:
            # Fallback: try generic ad card selectors
            ad_cards = soup.select("a[data-lurker-detail='list_id']")
        if not ad_cards:
            ad_cards = soup.select("li[data-adid] a")

        for card in ad_cards:
            listing = self._parse_ad_card(card, make)
            if listing:
                listings.append(listing)

        return listings

    def _parse_ad_card(self, card, make: str) -> dict | None:
        """Parse a single OLX ad card element."""
        try:
            # Extract URL and listing ID
            url = card.get("href", "")
            if not url:
                return None

            # Extract OLX listing ID from URL
            id_match = re.search(r"-(\d+)$", url.rstrip("/"))
            if not id_match:
                return None
            listing_id = id_match.group(1)

            # Extract price
            price_el = card.select_one("[data-ds-component='DS-Text']")
            if not price_el:
                price_el = card.select_one(".price, .m7nrfa-0")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = self._parse_brl_price(price_text)
            if not price or price < 10000:  # Filter out unrealistic prices
                return None

            # Extract title (usually "Make Model Year")
            title_el = card.select_one("h2") or card.select_one("[data-ds-component='DS-Text']")
            title = title_el.get_text(strip=True) if title_el else ""

            # Try to extract year from title
            year_match = re.search(r"20[12]\d", title)
            year = int(year_match.group()) if year_match else 0

            # Extract model from title
            model = title.replace(make, "").strip() if make in title else title

            # Extract location
            location_el = card.select_one("[data-ds-component='DS-Text']:last-child")
            location_text = location_el.get_text(strip=True) if location_el else ""
            city, state = self._parse_location(location_text)

            # Extract mileage if available
            mileage = None
            km_els = card.select("[data-ds-component='DS-Text']")
            for el in km_els:
                text = el.get_text(strip=True)
                km_match = re.search(r"([\d.]+)\s*km", text, re.IGNORECASE)
                if km_match:
                    mileage = self._parse_number(km_match.group(1))
                    break

            return {
                "source": self.SOURCE_NAME,
                "source_id": f"olx_{listing_id}",
                "url": url if url.startswith("http") else f"https://www.olx.com.br{url}",
                "make": make,
                "model": model,
                "year": year,
                "trim": "",
                "price_brl": price,
                "mileage_km": mileage,
                "location_state": state,
                "location_city": city,
            }
        except Exception as e:
            logger.debug(f"[OLX] Failed to parse ad card: {e}")
            return None

    @staticmethod
    def _parse_brl_price(text: str) -> float | None:
        """Parse a BRL price string like 'R$ 350.000' into a float."""
        if not text:
            return None
        cleaned = re.sub(r"[R$\s.]", "", text).replace(",", ".")
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_number(text: str) -> int:
        """Parse a number string, removing formatting."""
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else 0

    @staticmethod
    def _parse_location(text: str) -> tuple[str, str | None]:
        """Parse location text like 'São Paulo - SP' into (city, state)."""
        if not text:
            return ("", None)
        parts = text.split(" - ")
        city = parts[0].strip() if parts else ""
        state = parts[-1].strip()[:2] if len(parts) > 1 else None
        return (city, state)

    def scrape_for_vehicle(self, make: str, model: str, year: int) -> list[dict]:
        """Convenience method to search for a specific vehicle spec."""
        return self.search_listings(make, model, year - 1, year + 1)
