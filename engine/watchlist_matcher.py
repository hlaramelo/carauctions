"""Watchlist matching engine - matches vehicles against user watchlist items.

Supports:
- Exact VIN matching
- Make/model matching with year ranges
- Keyword matching against model, trim, and listing title
- Model aliases (e.g., "993" → also search "911", "M5" → also search "5 Series")
"""

from loguru import logger
from sqlalchemy import select

from models.database import get_session
from models.vehicle import Vehicle
from models.watchlist import WatchlistItem


# Maps common generation/chassis codes to the base model name used on auction sites.
# Key: alias (lowercased), Value: list of (make, model) that it maps to.
# This allows users to search for "993 Turbo" and match "Porsche 911" listings.
MODEL_ALIASES: dict[str, list[tuple[str, str]]] = {
    # Porsche 911 generations
    "930": [("Porsche", "911")],
    "964": [("Porsche", "911")],
    "993": [("Porsche", "911")],
    "996": [("Porsche", "911")],
    "997": [("Porsche", "911")],
    "991": [("Porsche", "911")],
    "992": [("Porsche", "911")],
    # Porsche other
    "944": [("Porsche", "944")],
    "928": [("Porsche", "928")],
    "356": [("Porsche", "356")],
    "550": [("Porsche", "550")],
    # BMW chassis codes
    "e12": [("BMW", "5 Series"), ("BMW", "M535i")],
    "e28": [("BMW", "5 Series"), ("BMW", "M5")],
    "e34": [("BMW", "5 Series"), ("BMW", "M5")],
    "e39": [("BMW", "5 Series"), ("BMW", "M5")],
    "e60": [("BMW", "5 Series"), ("BMW", "M5")],
    "f10": [("BMW", "5 Series"), ("BMW", "M5")],
    "e30": [("BMW", "3 Series"), ("BMW", "M3")],
    "e36": [("BMW", "3 Series"), ("BMW", "M3")],
    "e46": [("BMW", "3 Series"), ("BMW", "M3")],
    "e90": [("BMW", "3 Series"), ("BMW", "M3")],
    "f80": [("BMW", "3 Series"), ("BMW", "M3")],
    "e24": [("BMW", "6 Series"), ("BMW", "M6")],
    "e63": [("BMW", "6 Series"), ("BMW", "M6")],
    "e26": [("BMW", "M1")],
    "e31": [("BMW", "8 Series")],
    "z3": [("BMW", "Z3"), ("BMW", "M Roadster"), ("BMW", "M Coupe")],
    "z4": [("BMW", "Z4")],
    "z8": [("BMW", "Z8")],
    # Mercedes chassis codes
    "w124": [("Mercedes-Benz", "E-Class"), ("Mercedes-Benz", "300E"), ("Mercedes-Benz", "500E")],
    "w126": [("Mercedes-Benz", "S-Class"), ("Mercedes-Benz", "560SEL")],
    "w140": [("Mercedes-Benz", "S-Class")],
    "w201": [("Mercedes-Benz", "190E")],
    "r107": [("Mercedes-Benz", "SL"), ("Mercedes-Benz", "560SL")],
    "r129": [("Mercedes-Benz", "SL")],
    "w113": [("Mercedes-Benz", "SL"), ("Mercedes-Benz", "Pagoda")],
    "c107": [("Mercedes-Benz", "SLC")],
    # Ferrari
    "f40": [("Ferrari", "F40")],
    "f50": [("Ferrari", "F50")],
    "308": [("Ferrari", "308")],
    "328": [("Ferrari", "328")],
    "348": [("Ferrari", "348")],
    "355": [("Ferrari", "355"), ("Ferrari", "F355")],
    "360": [("Ferrari", "360")],
    "430": [("Ferrari", "F430"), ("Ferrari", "430")],
    "458": [("Ferrari", "458")],
    "488": [("Ferrari", "488")],
    "testarossa": [("Ferrari", "Testarossa")],
    "dino": [("Ferrari", "Dino"), ("Ferrari", "246")],
    # Lamborghini
    "countach": [("Lamborghini", "Countach")],
    "diablo": [("Lamborghini", "Diablo")],
    "murcielago": [("Lamborghini", "Murcielago"), ("Lamborghini", "Murciélago")],
    # Aston Martin
    "db5": [("Aston Martin", "DB5")],
    "db6": [("Aston Martin", "DB6")],
    "db7": [("Aston Martin", "DB7")],
    "db9": [("Aston Martin", "DB9")],
    "v8 vantage": [("Aston Martin", "V8 Vantage"), ("Aston Martin", "Vantage")],
}


def _text_contains_any(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _vehicle_matches_watch(vehicle: Vehicle, watch: WatchlistItem) -> bool:
    """Check if a vehicle matches a watchlist item."""
    # VIN match (exact)
    if watch.vin:
        return vehicle.vin and vehicle.vin.upper() == watch.vin.upper()

    # Make match (required for spec-based watches)
    if watch.make:
        if not vehicle.make:
            return False
        if vehicle.make.lower() != watch.make.lower():
            return False

    # Model match (fuzzy — check if watch model is contained in vehicle model)
    if watch.model:
        if not vehicle.model:
            return False
        if watch.model.lower() not in vehicle.model.lower():
            return False

    # Year match (single year, or range)
    if watch.year_min is not None and watch.year_max is not None:
        if not vehicle.year:
            return False
        if not (watch.year_min <= vehicle.year <= watch.year_max):
            return False
    elif watch.year is not None:
        if not vehicle.year or vehicle.year != watch.year:
            return False

    # Keyword match — all keywords must be found in model+trim+title
    if watch.keywords:
        kw_list = [k.strip() for k in watch.keywords.split(",") if k.strip()]
        if not kw_list:
            return True

        # Build searchable text from vehicle fields
        searchable = " ".join(filter(None, [
            vehicle.model,
            vehicle.trim,
            vehicle.damage_description,  # sometimes contains model details
        ])).lower()

        # Also check the URL (often contains model details like "993-turbo")
        if vehicle.url:
            searchable += " " + vehicle.url.lower().replace("-", " ")

        for kw in kw_list:
            if kw.lower() not in searchable:
                return False

    return True


def find_matching_vehicles(watch: WatchlistItem, only_new: bool = True) -> list[Vehicle]:
    """Find all active vehicles that match a watchlist item.

    Args:
        watch: The watchlist item to match against
        only_new: If True, excludes vehicles already notified for this watch

    Returns:
        List of matching Vehicle objects
    """
    session = get_session()
    try:
        # Build base query
        query = select(Vehicle).where(Vehicle.is_active == True)  # noqa: E712

        # Pre-filter by make if specified (reduces DB scan)
        if watch.make:
            query = query.where(Vehicle.make == watch.make)

        # Pre-filter by year range if specified
        if watch.year_min is not None and watch.year_max is not None:
            query = query.where(
                Vehicle.year >= watch.year_min,
                Vehicle.year <= watch.year_max,
            )
        elif watch.year is not None:
            query = query.where(Vehicle.year == watch.year)

        # Pre-filter by VIN if specified
        if watch.vin:
            query = query.where(Vehicle.vin == watch.vin)

        vehicles = session.execute(query).scalars().all()

        # Apply fine-grained matching (model, keywords, etc.)
        matched = [v for v in vehicles if _vehicle_matches_watch(v, watch)]

        # Filter out already-notified vehicles
        if only_new:
            notified = watch.get_notified_ids()
            matched = [v for v in matched if v.id not in notified]

        return matched

    finally:
        session.close()


def match_all_watchlists() -> list[tuple[WatchlistItem, list[Vehicle]]]:
    """Match all active watchlist items against current vehicles.

    Returns:
        List of (watch_item, matching_vehicles) tuples where matching_vehicles
        only includes vehicles not yet notified for that watch.
    """
    session = get_session()
    results = []

    try:
        watches = session.execute(
            select(WatchlistItem).where(WatchlistItem.is_active == True)  # noqa: E712
        ).scalars().all()

        if not watches:
            return results

        for watch in watches:
            matched = find_matching_vehicles(watch, only_new=True)
            if matched:
                results.append((watch, matched))
                logger.info(
                    f"[WatchlistMatcher] Watch #{watch.id} ({watch.make} {watch.model}): "
                    f"{len(matched)} new matches"
                )

        return results

    finally:
        session.close()
