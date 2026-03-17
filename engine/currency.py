"""Currency conversion using Banco Central do Brasil (BCB) API."""

import time
from datetime import datetime

import requests
from loguru import logger

from config import load_settings

# BCB PTAX API endpoint
BCB_API_URL = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)"

_cache: dict = {"rate": None, "timestamp": 0}


def get_usd_brl_rate(force_refresh: bool = False) -> float:
    """Get current USD/BRL exchange rate from BCB.

    Uses a cache to avoid excessive API calls. Falls back to config default
    if the API is unavailable.
    """
    settings = load_settings()
    cache_ttl = settings.get("currency", {}).get("update_interval_hours", 6) * 3600

    if not force_refresh and _cache["rate"] and (time.time() - _cache["timestamp"]) < cache_ttl:
        return _cache["rate"]

    try:
        rate = _fetch_rate_from_bcb()
        _cache["rate"] = rate
        _cache["timestamp"] = time.time()
        logger.info(f"USD/BRL rate updated: {rate:.4f}")
        return rate
    except Exception as e:
        logger.warning(f"Failed to fetch USD/BRL rate from BCB: {e}")
        if _cache["rate"]:
            logger.info(f"Using cached rate: {_cache['rate']:.4f}")
            return _cache["rate"]
        default_rate = settings.get("currency", {}).get("default_usd_brl", 5.0)
        logger.info(f"Using default rate: {default_rate}")
        return default_rate


def _fetch_rate_from_bcb() -> float:
    """Fetch the latest PTAX sell rate from BCB API."""
    today = datetime.now().strftime("%m-%d-%Y")
    params = {
        "@dataCotacao": f"'{today}'",
        "$top": "1",
        "$orderby": "dataHoraCotacao desc",
        "$format": "json",
    }

    response = requests.get(BCB_API_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    values = data.get("value", [])
    if values:
        return float(values[0]["cotacaoVenda"])

    # If no quote for today (weekend/holiday), try previous business days
    for days_back in range(1, 5):
        from datetime import timedelta

        date = datetime.now() - timedelta(days=days_back)
        params["@dataCotacao"] = f"'{date.strftime('%m-%d-%Y')}'"
        response = requests.get(BCB_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        values = data.get("value", [])
        if values:
            return float(values[0]["cotacaoVenda"])

    raise ValueError("Could not fetch USD/BRL rate from BCB for the last 5 days")
