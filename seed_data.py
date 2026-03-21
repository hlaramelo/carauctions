"""Seed the database with sample BMW M5 E28 (1980s) listings."""

import json
from datetime import datetime, timedelta

from models.database import init_db, get_session
from models.vehicle import Vehicle, PriceHistory
from models.deal import Deal
from models.br_listing import BRMarketListing, BRPriceSnapshot
from engine.currency import get_usd_brl_rate

init_db()

# BMW M5 E28 data - top of the line 1980s M5
# The E28 M5 was produced 1985-1988, powered by the M88/3 3.5L inline-6 (282 hp)
VEHICLES = [
    {
        "source": "bat",
        "source_id": "bat-bmw-m5-e28-001",
        "url": "https://bringatrailer.com/listing/1988-bmw-m5-e28-001",
        "make": "BMW",
        "model": "M5",
        "year": 1988,
        "trim": "E28 3.5L Inline-6",
        "vin": "WBSDC9304J2875001",
        "current_bid_usd": 62000,
        "mileage": 78500,
        "title_status": "Clean",
        "location_state": "CA",
        "location_city": "Los Angeles",
        "auction_end": datetime.utcnow() + timedelta(days=3, hours=8),
        "br_price_avg": 450000,
        "br_price_min": 350000,
        "br_price_max": 600000,
        "br_listings_count": 4,
        "fipe_price_brl": 420000,
    },
    {
        "source": "bat",
        "source_id": "bat-bmw-m5-e28-002",
        "url": "https://bringatrailer.com/listing/1987-bmw-m5-e28-002",
        "make": "BMW",
        "model": "M5",
        "year": 1987,
        "trim": "E28 3.5L Inline-6",
        "vin": "WBSDC9304H2861002",
        "current_bid_usd": 48000,
        "mileage": 112000,
        "title_status": "Clean",
        "location_state": "FL",
        "location_city": "Miami",
        "auction_end": datetime.utcnow() + timedelta(days=5, hours=2),
        "br_price_avg": 420000,
        "br_price_min": 320000,
        "br_price_max": 550000,
        "br_listings_count": 3,
        "fipe_price_brl": 400000,
    },
    {
        "source": "bat",
        "source_id": "bat-bmw-m5-e28-003",
        "url": "https://bringatrailer.com/listing/1986-bmw-m5-e28-003",
        "make": "BMW",
        "model": "M5",
        "year": 1986,
        "trim": "E28 3.5L Inline-6",
        "vin": "WBSDC9304G2843003",
        "current_bid_usd": 55000,
        "mileage": 65000,
        "title_status": "Clean",
        "location_state": "TX",
        "location_city": "Houston",
        "auction_end": datetime.utcnow() + timedelta(days=1, hours=12),
        "br_price_avg": 430000,
        "br_price_min": 330000,
        "br_price_max": 580000,
        "br_listings_count": 3,
        "fipe_price_brl": 410000,
    },
    {
        "source": "carsandbids",
        "source_id": "cab-bmw-m5-e28-004",
        "url": "https://carsandbids.com/auctions/1988-bmw-m5-e28-004",
        "make": "BMW",
        "model": "M5",
        "year": 1988,
        "trim": "E28 3.5L Inline-6 - Diamantschwarz",
        "vin": "WBSDC9304J2879004",
        "current_bid_usd": 71000,
        "mileage": 45200,
        "title_status": "Clean",
        "location_state": "NY",
        "location_city": "New York",
        "auction_end": datetime.utcnow() + timedelta(days=2, hours=6),
        "br_price_avg": 450000,
        "br_price_min": 350000,
        "br_price_max": 600000,
        "br_listings_count": 4,
        "fipe_price_brl": 420000,
    },
    {
        "source": "hemmings",
        "source_id": "hem-bmw-m5-e28-005",
        "url": "https://hemmings.com/classifieds/1985-bmw-m5-e28-005",
        "make": "BMW",
        "model": "M5",
        "year": 1985,
        "trim": "E28 3.5L Inline-6 - Euro Spec",
        "vin": "WBSDC9304F2831005",
        "current_bid_usd": 85000,
        "buy_now_price_usd": 95000,
        "mileage": 32000,
        "title_status": "Clean",
        "location_state": "CT",
        "location_city": "Greenwich",
        "auction_end": datetime.utcnow() + timedelta(days=7),
        "br_price_avg": 500000,
        "br_price_min": 400000,
        "br_price_max": 700000,
        "br_listings_count": 2,
        "fipe_price_brl": 480000,
    },
    {
        "source": "bat",
        "source_id": "bat-bmw-m5-e28-006",
        "url": "https://bringatrailer.com/listing/1987-bmw-m5-e28-006",
        "make": "BMW",
        "model": "M5",
        "year": 1987,
        "trim": "E28 3.5L Inline-6 - Salmon Silver",
        "vin": "WBSDC9304H2867006",
        "current_bid_usd": 42000,
        "mileage": 145000,
        "title_status": "Clean",
        "location_state": "OR",
        "location_city": "Portland",
        "auction_end": datetime.utcnow() + timedelta(days=4, hours=10),
        "br_price_avg": 380000,
        "br_price_min": 280000,
        "br_price_max": 480000,
        "br_listings_count": 3,
        "fipe_price_brl": 370000,
    },
    {
        "source": "copart",
        "source_id": "cop-bmw-m5-e28-007",
        "url": "https://copart.com/lot/1986-bmw-m5-e28-007",
        "make": "BMW",
        "model": "M5",
        "year": 1986,
        "trim": "E28 3.5L Inline-6",
        "vin": "WBSDC9304G2849007",
        "current_bid_usd": 28000,
        "mileage": 98000,
        "title_status": "Salvage",
        "damage_description": "Front-end damage, runs and drives. M88 engine intact.",
        "location_state": "GA",
        "location_city": "Atlanta",
        "auction_end": datetime.utcnow() + timedelta(days=2),
        "br_price_avg": 430000,
        "br_price_min": 330000,
        "br_price_max": 580000,
        "br_listings_count": 3,
        "fipe_price_brl": 410000,
    },
    {
        "source": "bat",
        "source_id": "bat-bmw-m5-e28-008",
        "url": "https://bringatrailer.com/listing/1988-bmw-m5-e28-008",
        "make": "BMW",
        "model": "M5",
        "year": 1988,
        "trim": "E28 3.5L Inline-6 - Royal Blue",
        "vin": "WBSDC9304J2881008",
        "current_bid_usd": 58000,
        "mileage": 89000,
        "title_status": "Clean",
        "location_state": "IL",
        "location_city": "Chicago",
        "auction_end": datetime.utcnow() + timedelta(hours=18),
        "br_price_avg": 450000,
        "br_price_min": 350000,
        "br_price_max": 600000,
        "br_listings_count": 4,
        "fipe_price_brl": 420000,
    },
]

# USD/BRL rate from BCB (Banco Central do Brasil)
USD_BRL = get_usd_brl_rate()

# Cost assumptions for importing to Brazil
SHIPPING_USD = 3500
IMPORT_TAX_PCT = 0.35  # ~35% (II + IPI + ICMS + PIS/COFINS)
CUSTOMS_BROKER_BRL = 8000


def calc_deal(vehicle_data):
    """Calculate deal metrics for a vehicle."""
    bid = vehicle_data["current_bid_usd"]
    br_avg = vehicle_data["br_price_avg"]

    # Total landed cost
    total_usd = bid + SHIPPING_USD
    total_brl = total_usd * USD_BRL * (1 + IMPORT_TAX_PCT) + CUSTOMS_BROKER_BRL

    # Estimated sale price (use avg BR market price with 10% discount for quick sale)
    sale_price = br_avg * 0.90

    profit = sale_price - total_brl
    margin = (profit / total_brl) * 100 if total_brl > 0 else 0

    # Score components (0-25 each, total 0-100)
    margin_score = min(25, max(0, margin * 1.5))
    liquidity_score = min(25, (vehicle_data.get("br_listings_count", 0) / 5) * 25)

    mileage = vehicle_data.get("mileage", 100000)
    condition_score = 25 if vehicle_data.get("title_status") == "Clean" else 8
    if mileage < 50000:
        condition_score = min(25, condition_score + 5)
    elif mileage > 120000:
        condition_score = max(0, condition_score - 5)

    days_left = (vehicle_data["auction_end"] - datetime.utcnow()).total_seconds() / 86400
    time_score = min(25, max(0, (7 - days_left) * 4))

    total_score = margin_score + liquidity_score + condition_score + time_score

    return {
        "auction_price_usd": bid,
        "total_landed_cost_brl": round(total_brl),
        "estimated_sale_price_brl": round(sale_price),
        "estimated_profit_brl": round(profit),
        "margin_pct": round(margin, 1),
        "score": round(total_score, 1),
        "score_breakdown": json.dumps({
            "margin": round(margin_score, 1),
            "liquidity": round(liquidity_score, 1),
            "condition": round(condition_score, 1),
            "time_remaining": round(time_score, 1),
            "price_history": 0,
        }),
        "usd_brl_rate": USD_BRL,
    }


def seed():
    session = get_session()
    try:
        # Clear existing data
        session.query(PriceHistory).delete()
        session.query(Deal).delete()
        session.query(BRPriceSnapshot).delete()
        session.query(BRMarketListing).delete()
        session.query(Vehicle).delete()
        session.commit()

        vehicle_ids = []
        for v_data in VEHICLES:
            vehicle = Vehicle(
                source=v_data["source"],
                source_id=v_data["source_id"],
                url=v_data["url"],
                make=v_data["make"],
                model=v_data["model"],
                year=v_data["year"],
                trim=v_data.get("trim"),
                vin=v_data.get("vin"),
                current_bid_usd=v_data.get("current_bid_usd"),
                buy_now_price_usd=v_data.get("buy_now_price_usd"),
                mileage=v_data.get("mileage"),
                title_status=v_data.get("title_status"),
                damage_description=v_data.get("damage_description"),
                location_state=v_data.get("location_state"),
                location_city=v_data.get("location_city"),
                auction_end=v_data.get("auction_end"),
                is_active=True,
                br_price_avg=v_data.get("br_price_avg"),
                br_price_min=v_data.get("br_price_min"),
                br_price_max=v_data.get("br_price_max"),
                br_listings_count=v_data.get("br_listings_count"),
                fipe_price_brl=v_data.get("fipe_price_brl"),
            )
            session.add(vehicle)
            session.flush()
            vehicle_ids.append(vehicle.id)

            # Create deal
            deal_data = calc_deal(v_data)
            deal = Deal(vehicle_id=vehicle.id, **deal_data)
            session.add(deal)

            # Create price history (simulate last 5 days of bidding)
            base_bid = v_data["current_bid_usd"] * 0.4
            for day in range(5, 0, -1):
                progress = (5 - day) / 5
                price = base_bid + (v_data["current_bid_usd"] - base_bid) * progress
                ph = PriceHistory(
                    vehicle_id=vehicle.id,
                    price_usd=round(price),
                    recorded_at=datetime.utcnow() - timedelta(days=day),
                )
                session.add(ph)

            # Final current price
            ph = PriceHistory(
                vehicle_id=vehicle.id,
                price_usd=v_data["current_bid_usd"],
                recorded_at=datetime.utcnow(),
            )
            session.add(ph)

        # BR Market snapshots (monthly trend data for M5s)
        for year in [1985, 1986, 1987, 1988]:
            base_price = {1985: 480000, 1986: 410000, 1987: 400000, 1988: 420000}[year]
            for month_offset in range(12, 0, -1):
                snap_date = datetime.utcnow() - timedelta(days=month_offset * 30)
                # Prices trending up ~2% per month for classic M5s
                growth = 1 + (12 - month_offset) * 0.02
                avg_price = base_price * growth
                snap = BRPriceSnapshot(
                    make="BMW",
                    model="M5",
                    year=year,
                    snapshot_date=snap_date,
                    avg_price_brl=round(avg_price),
                    min_price_brl=round(avg_price * 0.75),
                    max_price_brl=round(avg_price * 1.35),
                    median_price_brl=round(avg_price * 0.98),
                    listings_count=3 + (month_offset % 3),
                    source="combined",
                )
                session.add(snap)

        # BR Market active listings
        br_listings = [
            {"make": "BMW", "model": "M5 E28", "year": 1988, "price_brl": 520000,
             "mileage_km": 75000, "location_city": "Sao Paulo", "location_state": "SP",
             "url": "https://webmotors.com.br/bmw-m5-1988-001", "source": "webmotors"},
            {"make": "BMW", "model": "M5 E28", "year": 1987, "price_brl": 380000,
             "mileage_km": 120000, "location_city": "Rio de Janeiro", "location_state": "RJ",
             "url": "https://webmotors.com.br/bmw-m5-1987-002", "source": "webmotors"},
            {"make": "BMW", "model": "M5 E28", "year": 1986, "price_brl": 450000,
             "mileage_km": 68000, "location_city": "Curitiba", "location_state": "PR",
             "url": "https://olx.com.br/bmw-m5-1986-003", "source": "olx"},
            {"make": "BMW", "model": "M5 E28", "year": 1988, "price_brl": 600000,
             "mileage_km": 35000, "location_city": "Brasilia", "location_state": "DF",
             "url": "https://webmotors.com.br/bmw-m5-1988-004", "source": "webmotors"},
            {"make": "BMW", "model": "M5 E28", "year": 1985, "price_brl": 690000,
             "mileage_km": 42000, "location_city": "Belo Horizonte", "location_state": "MG",
             "url": "https://olx.com.br/bmw-m5-1985-005", "source": "olx"},
        ]

        for bl in br_listings:
            listing = BRMarketListing(
                source=bl["source"],
                source_id=f"{bl['source']}-{bl['url'].split('/')[-1]}",
                url=bl["url"],
                make=bl["make"],
                model=bl["model"],
                year=bl["year"],
                price_brl=bl["price_brl"],
                mileage_km=bl.get("mileage_km"),
                location_city=bl.get("location_city"),
                location_state=bl.get("location_state"),
                is_active=True,
            )
            session.add(listing)

        session.commit()
        print(f"Seeded {len(VEHICLES)} BMW M5 E28 vehicles with deals, price history, and BR market data.")
        print("Refresh the Streamlit dashboard to see the data!")

    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    seed()
