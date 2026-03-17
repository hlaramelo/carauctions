"""FIPE API client - Brazilian vehicle reference prices."""

import requests
from loguru import logger

# FIPE API (parallelum open API)
FIPE_API_BASE = "https://parallelum.com.br/fipe/api/v1"

# Mapping of common US makes to FIPE brand codes
# These need to be discovered dynamically, but we cache them
_brand_cache: dict[str, int] = {}


class FipeClient:
    """Client for the FIPE (Tabela FIPE) vehicle pricing API."""

    def __init__(self):
        self.base_url = FIPE_API_BASE
        self.session = requests.Session()

    def get_brands(self) -> list[dict]:
        """Get all available car brands from FIPE."""
        try:
            response = self.session.get(f"{self.base_url}/carros/marcas", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"[FIPE] Failed to fetch brands: {e}")
            return []

    def get_models(self, brand_code: int) -> list[dict]:
        """Get all models for a given brand."""
        try:
            response = self.session.get(
                f"{self.base_url}/carros/marcas/{brand_code}/modelos", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get("modelos", [])
        except requests.RequestException as e:
            logger.error(f"[FIPE] Failed to fetch models for brand {brand_code}: {e}")
            return []

    def get_years(self, brand_code: int, model_code: int) -> list[dict]:
        """Get available years for a given brand/model."""
        try:
            response = self.session.get(
                f"{self.base_url}/carros/marcas/{brand_code}/modelos/{model_code}/anos",
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"[FIPE] Failed to fetch years: {e}")
            return []

    def get_price(self, brand_code: int, model_code: int, year_code: str) -> dict | None:
        """Get FIPE price for a specific brand/model/year."""
        try:
            response = self.session.get(
                f"{self.base_url}/carros/marcas/{brand_code}/modelos/{model_code}/anos/{year_code}",
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"[FIPE] Failed to fetch price: {e}")
            return None

    def find_brand_code(self, make: str) -> int | None:
        """Find the FIPE brand code for a given make name."""
        global _brand_cache
        make_lower = make.lower()

        if make_lower in _brand_cache:
            return _brand_cache[make_lower]

        brands = self.get_brands()
        # Build mapping of common names
        name_map = {
            "mercedes-benz": "mercedes-benz",
            "bmw": "bmw",
            "porsche": "porsche",
            "ferrari": "ferrari",
            "lamborghini": "lamborghini",
            "audi": "audi",
            "mclaren": "mclaren",
            "aston martin": "aston martin",
            "bentley": "bentley",
            "maserati": "maserati",
            "rolls-royce": "rolls-royce",
            "jaguar": "jaguar",
            "land rover": "land rover",
            "volvo": "volvo",
            "volkswagen": "volkswagen",
            "toyota": "toyota",
            "ford": "ford",
            "chevrolet": "chevrolet",
        }

        search_name = name_map.get(make_lower, make_lower)

        for brand in brands:
            if search_name in brand["nome"].lower():
                _brand_cache[make_lower] = brand["codigo"]
                return brand["codigo"]

        logger.warning(f"[FIPE] Brand not found: {make}")
        return None

    def find_model_code(self, brand_code: int, model_name: str) -> int | None:
        """Find the FIPE model code by searching model names."""
        models = self.get_models(brand_code)
        model_lower = model_name.lower().split()[0] if model_name else ""

        if not model_lower:
            return None

        # Try exact match first, then partial
        for m in models:
            if model_lower == m["nome"].lower():
                return m["codigo"]

        for m in models:
            if model_lower in m["nome"].lower():
                return m["codigo"]

        logger.debug(f"[FIPE] Model not found: {model_name} for brand {brand_code}")
        return None

    def lookup_price(self, make: str, model: str, year: int) -> float | None:
        """Look up FIPE price for a vehicle by make/model/year.

        Returns the price in BRL or None if not found.
        """
        brand_code = self.find_brand_code(make)
        if brand_code is None:
            return None

        model_code = self.find_model_code(brand_code, model)
        if model_code is None:
            return None

        years = self.get_years(brand_code, model_code)
        target_year_str = str(year)
        year_code = None

        for y in years:
            # FIPE year codes look like "2020-1" (gasoline) or "2020-3" (flex)
            if y["codigo"].startswith(target_year_str):
                year_code = y["codigo"]
                break

        if year_code is None:
            logger.debug(f"[FIPE] Year {year} not found for {make} {model}")
            return None

        price_data = self.get_price(brand_code, model_code, year_code)
        if not price_data:
            return None

        # Parse price string like "R$ 350.000,00"
        price_str = price_data.get("Valor", "")
        return self._parse_brl_price(price_str)

    @staticmethod
    def _parse_brl_price(price_str: str) -> float | None:
        """Parse a BRL price string like 'R$ 350.000,00' into a float."""
        if not price_str:
            return None
        import re

        cleaned = re.sub(r"[R$\s]", "", price_str)
        cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None
