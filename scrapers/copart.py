"""Copart scraper - US salvage/clean title auction listings.

Uses Selenium with headless Chromium to render JS-heavy pages.
Falls back to API, then HTML parsing, then URL slug extraction.
"""

import json
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import load_settings
from models.vehicle import Vehicle
from scrapers.base import BaseScraper


def _get_selenium_driver():
    """Create a headless Chromium Selenium driver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # Try common chromedriver locations
    for driver_path in ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"]:
        try:
            service = Service(executable_path=driver_path)
            return webdriver.Chrome(service=service, options=options)
        except Exception:
            continue

    # Fallback: let Selenium find it
    return webdriver.Chrome(options=options)

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

        # Fallback 2: Selenium with headless Chromium (renders JS)
        try:
            logger.info("[Copart] Trying Selenium headless browser...")
            vehicle = self._fetch_with_selenium(url, lot_number)
            if vehicle and vehicle.current_bid_usd:
                logger.info(
                    f"[Copart] Selenium success: {vehicle.year} {vehicle.make} {vehicle.model} "
                    f"bid=${vehicle.current_bid_usd}"
                )
                return vehicle
        except Exception as e:
            logger.warning(f"[Copart] Selenium fallback failed: {e}")

        # Fallback 3: parse info from the URL slug itself
        vehicle = self._parse_from_url(url, lot_number)
        if vehicle:
            logger.info(f"[Copart] Parsed from URL: {vehicle.year} {vehicle.make} {vehicle.model}")
        return vehicle

    def _fetch_with_selenium(self, url: str, lot_number: str) -> Vehicle | None:
        """Use Selenium headless Chromium to render Copart page and extract data."""
        driver = None
        try:
            driver = _get_selenium_driver()
            driver.get(url)

            # Wait for page to render (Copart loads data via JS)
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            # Wait up to 15s for bid element to appear
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-uname='lotdetailCurrentBidValue'], .bid-price, #lot-details"))
                )
            except Exception:
                logger.debug("[Copart] Timeout waiting for bid element, parsing what we have")

            time.sleep(2)  # Extra wait for dynamic content
            html = driver.page_source

            soup = BeautifulSoup(html, "lxml")

            # Parse title for year/make/model
            title_el = soup.find("h1") or soup.find("title")
            title_text = title_el.get_text(strip=True) if title_el else ""
            logger.debug(f"[Copart/Selenium] Page title: {title_text}")

            year, make, model = None, "", ""
            year_m = re.search(r"(\d{4})", title_text)
            if year_m:
                year = int(year_m.group(1))
                after_year = title_text.split(str(year), 1)[-1].strip()
                # Remove trailing lot info like "Lot #78272755"
                after_year = re.sub(r"\s*Lot\s*#?\d+.*", "", after_year, flags=re.I).strip()
                parts = after_year.split(None, 1)
                if parts:
                    make = parts[0]
                    model = parts[1] if len(parts) > 1 else ""

            # Current bid
            current_bid = None
            for selector in [
                "[data-uname='lotdetailCurrentBidValue']",
                ".bid-price",
                "[data-uname='lotdetailBidNowValue']",
            ]:
                el = soup.select_one(selector)
                if el:
                    price_text = el.get_text(strip=True)
                    price_clean = re.sub(r"[^\d.]", "", price_text)
                    if price_clean:
                        current_bid = float(price_clean)
                        break

            # Also try searching for price pattern near "Current bid" text
            if not current_bid:
                for el in soup.find_all(string=re.compile(r"\$[\d,]+")):
                    price_clean = re.sub(r"[^\d.]", "", el.strip())
                    if price_clean:
                        val = float(price_clean)
                        if 10 < val < 1_000_000:  # Reasonable bid range
                            current_bid = val
                            break

            # Mileage
            mileage = None
            for label_text in ["Odometer", "odometer"]:
                label_el = soup.find(string=re.compile(label_text, re.I))
                if label_el:
                    parent = label_el.find_parent("tr") or label_el.find_parent("div") or label_el.find_parent()
                    if parent:
                        text = parent.get_text()
                        mi_match = re.search(r"([\d,]+)\s*(?:mi|mile|actual|exempt)", text, re.I)
                        if mi_match:
                            mileage = int(mi_match.group(1).replace(",", ""))
                            break

            # Primary damage
            damage = None
            for label_text in ["Primary damage", "Primary Damage"]:
                label_el = soup.find(string=re.compile(label_text, re.I))
                if label_el:
                    parent = label_el.find_parent("tr") or label_el.find_parent("div") or label_el.find_parent()
                    if parent:
                        # Get text after the label
                        full_text = parent.get_text(separator="|")
                        parts = full_text.split("|")
                        for i, p in enumerate(parts):
                            if "primary damage" in p.lower() and i + 1 < len(parts):
                                damage = parts[i + 1].strip()
                                break
                    break

            # Secondary damage
            secondary = None
            label_el = soup.find(string=re.compile("Secondary damage", re.I))
            if label_el:
                parent = label_el.find_parent("tr") or label_el.find_parent("div") or label_el.find_parent()
                if parent:
                    full_text = parent.get_text(separator="|")
                    parts = full_text.split("|")
                    for i, p in enumerate(parts):
                        if "secondary damage" in p.lower() and i + 1 < len(parts):
                            secondary = parts[i + 1].strip()
                            break
            if damage and secondary:
                damage = f"{damage}, {secondary}"

            # Location (Sale name)
            location_state, location_city = None, None
            sale_el = soup.find(string=re.compile("Sale name|Location", re.I))
            if sale_el:
                parent = sale_el.find_parent()
                if parent:
                    text = parent.get_text()
                    loc_match = re.search(r"(\w{2})\s*-\s*([A-Za-z\s]+)", text)
                    if loc_match:
                        location_state = loc_match.group(1).upper()
                        location_city = loc_match.group(2).strip().title()

            # VIN
            vin = None
            vin_el = soup.find(string=re.compile(r"VIN", re.I))
            if vin_el:
                parent = vin_el.find_parent()
                if parent:
                    vin_match = re.search(r"[A-HJ-NPR-Z0-9]{17}", parent.get_text())
                    if vin_match:
                        vin = vin_match.group(0)

            # Engine
            engine_cc = None
            engine_el = soup.find(string=re.compile("Engine type|Engine", re.I))
            if engine_el:
                parent = engine_el.find_parent()
                if parent:
                    eng_match = re.search(r"(\d+\.?\d*)\s*[lL]", parent.get_text())
                    if eng_match:
                        engine_cc = int(float(eng_match.group(1)) * 1000)

            # Title status
            title_status = "unknown"
            title_el = soup.find(string=re.compile("Title code|Title", re.I))
            if title_el:
                parent = title_el.find_parent()
                if parent:
                    text = parent.get_text().lower()
                    if "clean" in text or "certificate" in text:
                        title_status = "clean"
                    elif "salvage" in text:
                        title_status = "salvage"
                    elif "rebuilt" in text:
                        title_status = "rebuilt"

            # Auction end date
            auction_end = None
            countdown_el = soup.find(string=re.compile("Auction countdown|Sale date", re.I))
            if countdown_el:
                parent = countdown_el.find_parent()
                if parent:
                    text = parent.get_text()
                    # Parse "1D 17H 21min" style countdown
                    d_match = re.search(r"(\d+)\s*[dD]", text)
                    h_match = re.search(r"(\d+)\s*[hH]", text)
                    m_match = re.search(r"(\d+)\s*min", text, re.I)
                    if d_match or h_match or m_match:
                        days = int(d_match.group(1)) if d_match else 0
                        hours = int(h_match.group(1)) if h_match else 0
                        mins = int(m_match.group(1)) if m_match else 0
                        from datetime import timedelta
                        auction_end = datetime.now(timezone.utc) + timedelta(days=days, hours=hours, minutes=mins)

            # Images - grab from page and Copart CDN
            image_list = []
            # Try page images first
            for img in soup.find_all("img"):
                src = img.get("src", "") or img.get("data-src", "")
                if src and ("cs.copart.com" in src or "cdnimg" in src) and "lot" in src.lower():
                    if src not in image_list:
                        image_list.append(src)
            # Fallback: Copart CDN pattern
            if not image_list:
                for i in range(1, 6):
                    image_list.append(
                        f"https://cs.copart.com/v1/AUTH_svc.pdoc00001/{lot_number}/{i}.jpg"
                    )

            # Fallback: get URL-based data for anything missing
            url_vehicle = self._parse_from_url(url, lot_number)

            if not year and url_vehicle:
                year = url_vehicle.year
                make = url_vehicle.make
                model = url_vehicle.model

            if not year:
                return None

            logger.debug(
                f"[Copart/Selenium] Extracted: bid={current_bid}, mi={mileage}, "
                f"damage={damage}, loc={location_state}, vin={vin}, images={len(image_list)}"
            )

            return Vehicle(
                source=self.SOURCE_NAME,
                source_id=f"copart_{lot_number}",
                url=f"https://www.copart.com/lot/{lot_number}",
                make=(make or "").title(),
                model=(model or "").title(),
                year=year,
                trim=None,
                vin=vin,
                current_bid_usd=current_bid,
                mileage=mileage,
                title_status=title_status or (url_vehicle.title_status if url_vehicle else "unknown"),
                damage_description=damage,
                location_state=location_state or (url_vehicle.location_state if url_vehicle else None),
                location_city=location_city,
                engine_cc=engine_cc,
                auction_end=auction_end,
                image_urls=json.dumps(image_list) if image_list else None,
            )
        except Exception as e:
            logger.error(f"[Copart] Selenium error: {e}")
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _parse_from_html(self, html: str, lot_number: str, url: str) -> Vehicle | None:
        """Parse vehicle data from Copart HTML page."""
        try:
            soup = BeautifulSoup(html, "lxml")

            # Try to find JSON-LD or embedded lot data in script tags
            for script in soup.find_all("script"):
                text = script.string or ""
                # Look for lot data in JavaScript variables
                lot_json_match = re.search(r'lotDetails\s*[=:]\s*({.+?});', text, re.DOTALL)
                if lot_json_match:
                    try:
                        lot_data = json.loads(lot_json_match.group(1))
                        return self._parse_result(lot_data)
                    except (json.JSONDecodeError, Exception):
                        pass

                # Look for JSON-LD structured data
                if '"@type"' in text and '"Vehicle"' in text:
                    try:
                        ld_data = json.loads(text)
                        if isinstance(ld_data, list):
                            ld_data = ld_data[0]
                        # Extract from schema.org Vehicle format
                        name = ld_data.get("name", "")
                        year_m = re.search(r"(\d{4})", name)
                        if year_m:
                            year = int(year_m.group(1))
                            rest = name.replace(year_m.group(1), "").strip()
                            parts = rest.split(None, 1)
                            make = parts[0] if parts else ""
                            model = parts[1] if len(parts) > 1 else ""
                            bid = None
                            offers = ld_data.get("offers", {})
                            if offers:
                                bid = offers.get("price")
                            return Vehicle(
                                source=self.SOURCE_NAME,
                                source_id=f"copart_{lot_number}",
                                url=url,
                                make=make.title(),
                                model=model.title(),
                                year=year,
                                current_bid_usd=float(bid) if bid else None,
                            )
                    except (json.JSONDecodeError, Exception):
                        pass

            # Direct HTML element parsing
            title_el = soup.find("h1") or soup.find("title")
            title_text = title_el.get_text(strip=True) if title_el else ""

            year_m = re.search(r"(\d{4})", title_text)
            year = int(year_m.group(1)) if year_m else None

            # Extract bid from page
            current_bid = None
            bid_el = soup.find(string=re.compile(r"Current bid", re.I))
            if bid_el:
                parent = bid_el.find_parent()
                if parent:
                    price_text = parent.find_next(string=re.compile(r"\$[\d,]+"))
                    if price_text:
                        price_clean = re.sub(r"[^\d.]", "", price_text)
                        if price_clean:
                            current_bid = float(price_clean)

            # Also try meta tags for price
            if not current_bid:
                price_meta = soup.find("meta", {"property": "product:price:amount"})
                if price_meta:
                    try:
                        current_bid = float(price_meta.get("content", 0))
                    except (ValueError, TypeError):
                        pass

            # Extract mileage
            mileage = None
            odo_el = soup.find(string=re.compile(r"Odometer", re.I))
            if odo_el:
                parent = odo_el.find_parent()
                if parent:
                    val_el = parent.find_next(string=re.compile(r"[\d,]+"))
                    if val_el:
                        mileage = self._parse_mileage(val_el.strip())

            # Extract damage
            damage = None
            dmg_el = soup.find(string=re.compile(r"Primary damage", re.I))
            if dmg_el:
                parent = dmg_el.find_parent()
                if parent:
                    val_el = parent.find_next_sibling() or parent.find_next()
                    if val_el:
                        damage = val_el.get_text(strip=True)

            # Extract location from sale name
            location_state = None
            location_city = None
            sale_el = soup.find(string=re.compile(r"Sale name", re.I))
            if sale_el:
                parent = sale_el.find_parent()
                if parent:
                    val_el = parent.find_next(string=re.compile(r"\w{2}\s*-\s*\w+"))
                    if val_el:
                        loc_match = re.search(r"(\w{2})\s*-\s*(.+)", val_el.strip())
                        if loc_match:
                            location_state = loc_match.group(1).upper()
                            location_city = loc_match.group(2).strip().title()

            # Parse make/model from title
            make, model = "", ""
            if year and title_text:
                after_year = title_text.split(str(year), 1)[-1].strip()
                parts = after_year.split(None, 1)
                if parts:
                    make = parts[0]
                    model = parts[1] if len(parts) > 1 else ""

            # If we got at least year and make, return
            if year and make:
                # Get URL-based fallback data for anything missing
                url_vehicle = self._parse_from_url(url, lot_number)
                return Vehicle(
                    source=self.SOURCE_NAME,
                    source_id=f"copart_{lot_number}",
                    url=url,
                    make=make.title(),
                    model=model.title() if model else (url_vehicle.model if url_vehicle else ""),
                    year=year,
                    current_bid_usd=current_bid,
                    mileage=mileage,
                    damage_description=damage,
                    title_status=url_vehicle.title_status if url_vehicle else "unknown",
                    location_state=location_state or (url_vehicle.location_state if url_vehicle else None),
                    location_city=location_city,
                )

        except Exception as e:
            logger.warning(f"[Copart] HTML parsing error: {e}")

        return None

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
