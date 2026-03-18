"""Base scraper class with common interface and utilities."""

import time
from abc import ABC, abstractmethod

import requests
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle


class BaseScraper(ABC):
    """Abstract base class for all auction scrapers."""

    SOURCE_NAME: str = ""

    def __init__(self):
        settings = load_settings()
        scraping = settings.get("scraping", {})
        self.user_agent = scraping.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        self.request_delay = scraping.get("request_delay_seconds", 2)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

    @abstractmethod
    def scrape_listings(self) -> list[Vehicle]:
        """Scrape active listings and return a list of Vehicle objects.

        Must be implemented by each scraper.
        """
        ...

    def fetch_single_listing(self, url: str) -> Vehicle | None:
        """Fetch a single listing by URL. Override in subclasses that support it."""
        raise NotImplementedError(f"{self.SOURCE_NAME} does not support single listing fetch")

    def fetch_page(self, url: str, params: dict | None = None) -> str | None:
        """Fetch a page with rate limiting and error handling."""
        try:
            time.sleep(self.request_delay)
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"[{self.SOURCE_NAME}] Failed to fetch {url}: {e}")
            return None

    def fetch_json(self, url: str, params: dict | None = None) -> dict | None:
        """Fetch JSON data with rate limiting and error handling."""
        try:
            time.sleep(self.request_delay)
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"[{self.SOURCE_NAME}] Failed to fetch JSON from {url}: {e}")
            return None
