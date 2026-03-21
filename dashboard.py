"""Streamlit dashboard for Car Auction Deal Finder.

Run with: streamlit run dashboard.py
"""

import json
import os

from dotenv import load_dotenv
load_dotenv()

# Also load Streamlit secrets into env vars (for Streamlit Cloud)
_SECRETS_KEYS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DATABASE_URL",
    "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO",
]
try:
    import streamlit as st
    for key in _SECRETS_KEYS:
        if key not in os.environ or not os.environ[key]:
            try:
                val = st.secrets[key]
                if val:
                    os.environ[key] = str(val)
            except (KeyError, FileNotFoundError):
                pass
except Exception:
    pass

import pandas as pd
import streamlit as st
from sqlalchemy import select, func

from models.database import get_session, init_db, DATABASE_URL
from models.deal import Deal
from models.monitored_auction import MonitoredAuction
from models.vehicle import Vehicle, PriceHistory
from models.br_listing import BRMarketListing, BRPriceSnapshot
from models.watchlist import WatchlistItem
from engine.monitor_engine import MonitorEngine
from engine.price_history import BRMarketAnalyzer
from engine.currency import get_usd_brl_rate, _cache as _currency_cache
from notifications.telegram_commands import TelegramCommandHandler

init_db()

# Start Telegram bot polling (once per Streamlit session)
if "telegram_bot_started" not in st.session_state:
    _token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if _token:
        _bot = TelegramCommandHandler()
        if not _bot.bot_token:
            _bot.bot_token = _token
            _bot.api_base = f"https://api.telegram.org/bot{_token}"
        _bot.start_polling()
        st.session_state["telegram_bot_started"] = True
        st.session_state["telegram_bot_instance"] = _bot
    else:
        st.session_state["telegram_bot_started"] = False

# =============================================================================
# STYLING
# =============================================================================

THEME_CSS = """
<style>
    /* Clean sans-serif base */
    html, body, [class*="css"] {
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
    }

    /* Header */
    .main-header {
        padding: 1.5rem 0 0.75rem 0;
        border-bottom: 2px solid rgba(255, 255, 255, 0.15);
        margin-bottom: 1.5rem;
    }
    .main-header h1 {
        font-size: 1.6rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0;
    }
    .main-header p {
        font-size: 0.85rem;
        opacity: 0.5;
        margin: 0.25rem 0 0 0;
    }

    /* Metric cards */
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 0.8rem 1rem;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        opacity: 0.6;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.3rem;
        font-weight: 700;
    }

    /* Section headers */
    .section-header {
        font-size: 1rem;
        font-weight: 600;
        padding: 0.8rem 0 0.4rem 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        margin: 1.5rem 0 1rem 0;
    }

    /* Dataframes */
    .stDataFrame {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Hide default Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
"""


# =============================================================================
# HELPERS
# =============================================================================

def section(title: str):
    """Render a styled section header."""
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def get_deals_df():
    """Load active deals with vehicle data."""
    session = get_session()
    try:
        deals = session.execute(
            select(Deal).where(Deal.is_active == True).order_by(Deal.score.desc())  # noqa: E712
        ).scalars().all()

        rows = []
        for deal in deals:
            vehicle = session.get(Vehicle, deal.vehicle_id)
            if not vehicle:
                continue

            breakdown = {}
            if deal.score_breakdown:
                try:
                    breakdown = json.loads(deal.score_breakdown)
                except (json.JSONDecodeError, TypeError):
                    pass

            rows.append({
                "Score": round(deal.score, 1),
                "Year": int(vehicle.year),
                "Make": vehicle.make,
                "Model": vehicle.model,
                "Trim": vehicle.trim or "",
                "Bid (USD)": int(round(deal.auction_price_usd)),
                "Custo Total (BRL)": int(round(deal.total_landed_cost_brl)),
                "Venda BR (BRL)": int(round(deal.estimated_sale_price_brl)),
                "Lucro (BRL)": int(round(deal.estimated_profit_brl)),
                "Margem %": round(deal.margin_pct, 1),
                "Mileage": int(vehicle.mileage or 0),
                "Title": vehicle.title_status or "N/A",
                "Source": vehicle.source,
                "Cambio": round(deal.usd_brl_rate, 2),
                "URL": vehicle.url,
                "Score Margin": round(breakdown.get("margin", 0), 1),
                "Score Liquidity": round(breakdown.get("liquidity", 0), 1),
                "Score Condition": round(breakdown.get("condition", 0), 1),
                "Score Time": round(breakdown.get("time_remaining", 0), 1),
                "Score History": round(breakdown.get("price_history", 0), 1),
            })

        return rows
    finally:
        session.close()


def get_stats():
    """Get summary statistics."""
    session = get_session()
    try:
        active_vehicles = session.execute(
            select(func.count(Vehicle.id)).where(Vehicle.is_active == True)  # noqa: E712
        ).scalar() or 0
        active_deals = session.execute(
            select(func.count(Deal.id)).where(Deal.is_active == True)  # noqa: E712
        ).scalar() or 0
        br_listings = session.execute(
            select(func.count(BRMarketListing.id)).where(BRMarketListing.is_active == True)  # noqa: E712
        ).scalar() or 0
        try:
            watchlist_items = session.execute(
                select(func.count(WatchlistItem.id)).where(WatchlistItem.is_active == True)  # noqa: E712
            ).scalar() or 0
        except Exception:
            session.rollback()
            watchlist_items = 0

        avg_score = session.execute(
            select(func.avg(Deal.score)).where(Deal.is_active == True)  # noqa: E712
        ).scalar() or 0
        avg_margin = session.execute(
            select(func.avg(Deal.margin_pct)).where(Deal.is_active == True)  # noqa: E712
        ).scalar() or 0

        sources = {}
        for src in ["bat", "copart", "carsandbids", "hemmings"]:
            count = session.execute(
                select(func.count(Vehicle.id)).where(
                    Vehicle.source == src, Vehicle.is_active == True  # noqa: E712
                )
            ).scalar() or 0
            if count:
                sources[src] = count

        return {
            "vehicles": active_vehicles,
            "deals": active_deals,
            "br_listings": br_listings,
            "watchlist": watchlist_items,
            "avg_score": round(avg_score, 1),
            "avg_margin": round(avg_margin, 1),
            "sources": sources,
        }
    finally:
        session.close()


def get_price_history(vehicle_id: int):
    """Get price history for a vehicle."""
    session = get_session()
    try:
        entries = session.execute(
            select(PriceHistory)
            .where(PriceHistory.vehicle_id == vehicle_id)
            .order_by(PriceHistory.recorded_at.asc())
        ).scalars().all()
        return [{"timestamp": e.recorded_at, "price": e.price_usd} for e in entries]
    finally:
        session.close()


def get_market_snapshots(make: str, model: str, year: int):
    """Get BR market price snapshots for trend chart."""
    session = get_session()
    try:
        snapshots = session.execute(
            select(BRPriceSnapshot).where(
                func.lower(BRPriceSnapshot.make) == make.lower(),
                func.lower(BRPriceSnapshot.model).contains(model.lower().split()[0]),
                BRPriceSnapshot.year == year,
            ).order_by(BRPriceSnapshot.snapshot_date.asc())
        ).scalars().all()
        return [
            {
                "date": s.snapshot_date,
                "avg": s.avg_price_brl,
                "min": s.min_price_brl,
                "max": s.max_price_brl,
                "median": s.median_price_brl,
                "count": s.listings_count,
            }
            for s in snapshots
        ]
    finally:
        session.close()


def _describe_from_url(url: str) -> dict:
    """Extract car description from a listing URL slug for display purposes."""
    import re
    info: dict = {}

    # Copart: /lot/78272755/Photos/clean-title-2006-mercedes-benz-slk-55-amg-pa-philadelphia
    slug_match = re.search(r"/lot/\d+(?:/Photos)?/(.+?)(?:\?|$)", url)
    if slug_match:
        slug = slug_match.group(1).lower()
        # Title status
        for ts in ["clean-title", "salvage-title", "rebuilt-title"]:
            if ts in slug:
                info["title"] = ts.replace("-title", "")
                slug = slug.replace(ts + "-", "")
                break
        # Year
        year_m = re.search(r"(\d{4})", slug)
        if year_m:
            info["year"] = int(year_m.group(1))
            slug = slug[:year_m.start()] + slug[year_m.end():]
            slug = slug.strip("-")
        # Make / model from remaining slug (strip trailing state-city)
        parts = [p for p in slug.split("-") if p]
        if len(parts) >= 3 and len(parts[-2]) == 2:
            parts = parts[:-2]
        elif len(parts) >= 2 and len(parts[-1]) == 2:
            parts = parts[:-1]
        if parts:
            info["make"] = parts[0].title()
            info["model"] = " ".join(p.title() for p in parts[1:]) if len(parts) > 1 else ""
        return info

    # BaT / Cars & Bids: slug usually has make-model-year
    slug_match = re.search(r"/([^/]+?)/?(?:\?|$)", url)
    if slug_match:
        slug = slug_match.group(1)
        parts = slug.replace("_", "-").split("-")
        year_m = re.search(r"(\d{4})", slug)
        if year_m:
            info["year"] = int(year_m.group(1))
        info["make"] = parts[0].title() if parts else ""
        info["model"] = " ".join(p.title() for p in parts[1:4]) if len(parts) > 1 else ""

    return info


def _save_manual_edit(vehicle_id, bid, mileage, damage, location, vin, engine_str):
    """Save manually entered vehicle data."""
    import re
    from scrapers.copart import CopartScraper
    session = get_session()
    try:
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle:
            return
        if bid and bid > 0:
            old_bid = vehicle.current_bid_usd
            vehicle.current_bid_usd = float(bid)
            # Record price history if bid changed
            if old_bid != float(bid):
                from datetime import datetime, timezone
                session.add(PriceHistory(
                    vehicle_id=vehicle.id,
                    price_usd=float(bid),
                    recorded_at=datetime.now(timezone.utc),
                ))
        if mileage and mileage > 0:
            vehicle.mileage = int(mileage)
        if damage:
            vehicle.damage_description = damage
        if location:
            # Parse "PA - Philadelphia" or "PA"
            loc_match = re.match(r"(\w{2})\s*-?\s*(.*)", location.strip())
            if loc_match:
                vehicle.location_state = loc_match.group(1).upper()
                city = loc_match.group(2).strip()
                if city:
                    vehicle.location_city = city
        if vin and len(vin) == 17:
            vehicle.vin = vin
        if engine_str:
            engine_match = re.search(r"(\d+\.?\d*)\s*[lL]", engine_str)
            if engine_match:
                vehicle.engine_cc = int(float(engine_match.group(1)) * 1000)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _remove_auction(auction_id):
    """Remove a monitored auction."""
    session = get_session()
    try:
        auction = session.get(MonitoredAuction, auction_id)
        if auction:
            auction.is_active = False
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def force_fetch_auction(auction):
    """Force-fetch data for a monitored auction. Returns (success, message)."""
    from datetime import datetime, timezone
    from scrapers.copart import CopartScraper
    from scrapers.bring_a_trailer import BringATrailerScraper
    from scrapers.cars_and_bids import CarsAndBidsScraper
    from scrapers.hemmings import HemmingsScraper

    scraper_map = {
        "copart": CopartScraper,
        "bat": BringATrailerScraper,
        "carsandbids": CarsAndBidsScraper,
        "hemmings": HemmingsScraper,
    }

    session = get_session()
    try:
        # Re-fetch the auction inside this session
        auction = session.get(MonitoredAuction, auction.id)
        scraper = scraper_map[auction.source]()
        vehicle = scraper.fetch_single_listing(auction.url)
        if not vehicle:
            return False, "Nao foi possivel obter dados do listing."

        existing_v = session.execute(
            select(Vehicle).where(
                Vehicle.source == vehicle.source,
                Vehicle.source_id == vehicle.source_id,
            )
        ).scalar_one_or_none()

        if existing_v:
            if vehicle.current_bid_usd:
                existing_v.current_bid_usd = vehicle.current_bid_usd
            if vehicle.auction_end:
                existing_v.auction_end = vehicle.auction_end
            if vehicle.title_status:
                existing_v.title_status = vehicle.title_status
            if vehicle.year:
                existing_v.year = vehicle.year
            if vehicle.make:
                existing_v.make = vehicle.make
            if vehicle.model:
                existing_v.model = vehicle.model
            if vehicle.mileage:
                existing_v.mileage = vehicle.mileage
            if vehicle.damage_description:
                existing_v.damage_description = vehicle.damage_description
            if vehicle.location_state:
                existing_v.location_state = vehicle.location_state
            if vehicle.location_city:
                existing_v.location_city = vehicle.location_city
            if vehicle.vin:
                existing_v.vin = vehicle.vin
            if vehicle.engine_cc:
                existing_v.engine_cc = vehicle.engine_cc
            if vehicle.trim:
                existing_v.trim = vehicle.trim
            existing_v.is_active = True
            vehicle = existing_v
        else:
            session.add(vehicle)
            session.flush()

        auction.vehicle_id = vehicle.id
        auction.last_checked_at = datetime.now(timezone.utc)

        # Record price history if we have a bid
        if vehicle.current_bid_usd and vehicle.current_bid_usd > 0:
            session.add(PriceHistory(
                vehicle_id=vehicle.id,
                price_usd=vehicle.current_bid_usd,
                recorded_at=datetime.now(timezone.utc),
            ))

        session.commit()

        return True, (
            f"Atualizado: {vehicle.year} {vehicle.make} {vehicle.model} — "
            f"Bid: ${vehicle.current_bid_usd or 0:,.0f}"
        )
    except Exception as e:
        session.rollback()
        return False, f"Erro no fetch: {e}"
    finally:
        session.close()


SOURCE_LABELS = {
    "bat": "Bring a Trailer",
    "copart": "Copart",
    "carsandbids": "Cars & Bids",
    "hemmings": "Hemmings",
}


# =============================================================================
# APP CONFIG
# =============================================================================

st.set_page_config(
    page_title="Car Auction Deal Finder",
    layout="wide",
)

st.markdown(THEME_CSS, unsafe_allow_html=True)

# --- Header ---
st.markdown(
    '<div class="main-header">'
    "<h1>Car Auction Deal Finder</h1>"
    "<p>Monitoramento de leiloes US &middot; Analise de importacao &middot; Mercado BR</p>"
    "</div>",
    unsafe_allow_html=True,
)

# --- Sidebar ---
with st.sidebar:
    st.markdown("### Navegacao")
    page = st.radio(
        "Selecionar pagina",
        ["Dashboard", "Deals", "Monitorados", "Analise de Mercado", "Calculadora ROI"],
        label_visibility="collapsed",
    )
    st.divider()
    _bot_ok = st.session_state.get("telegram_bot_started", False)
    _db_type = "PostgreSQL" if DATABASE_URL else "SQLite"
    st.caption(f"Bot Telegram: {'Ativo' if _bot_ok else 'Inativo'}")
    st.caption(f"Banco: {_db_type}")

# =============================================================================
# DASHBOARD
# =============================================================================
if page == "Dashboard":
    stats = get_stats()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Veiculos Ativos", stats["vehicles"])
    col2.metric("Deals Ativos", stats["deals"])
    col3.metric("Score Medio", stats["avg_score"])
    col4.metric("Margem Media", f"{stats['avg_margin']}%")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Listings BR", stats["br_listings"])
    col6.metric("Watchlist", stats["watchlist"])
    try:
        usd_brl = get_usd_brl_rate()
        source = _currency_cache.get("source", "fallback")
        label = "USD/BRL (PTAX)" if source == "bcb" else "USD/BRL (fallback)"
        col7.metric(label, f"R$ {usd_brl:.4f}")
    except Exception:
        col7.metric("USD/BRL", "Indisponivel")

    # Sources breakdown
    section("Veiculos por Fonte")
    if stats["sources"]:
        chart_data = {SOURCE_LABELS.get(k, k): v for k, v in stats["sources"].items()}
        st.bar_chart(chart_data)
    else:
        st.info("Nenhum veiculo no banco ainda. Execute o pipeline primeiro.")

    # Top deals
    section("Top 5 Deals")
    rows = get_deals_df()
    if rows:
        df = pd.DataFrame(rows[:5])
        display_cols = ["Score", "Year", "Make", "Model", "Bid (USD)", "Lucro (BRL)", "Margem %", "Source"]
        st.dataframe(
            df[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Score": st.column_config.NumberColumn(format="%.1f"),
                "Bid (USD)": st.column_config.NumberColumn(format="$ %d"),
                "Lucro (BRL)": st.column_config.NumberColumn(format="R$ %d"),
                "Margem %": st.column_config.NumberColumn(format="%.1f %%"),
            },
        )
    else:
        st.info("Nenhum deal encontrado.")

# =============================================================================
# DEALS
# =============================================================================
elif page == "Deals":
    section("Todos os Deals Ativos")

    rows = get_deals_df()
    if not rows:
        st.info("Nenhum deal encontrado. Execute o pipeline primeiro.")
    else:
        df = pd.DataFrame(rows)

        # Filters
        with st.container():
            col1, col2, col3 = st.columns(3)
            with col1:
                min_score = st.slider("Score minimo", 0, 100, 0)
            with col2:
                makes = sorted(df["Make"].unique())
                selected_makes = st.multiselect("Marcas", makes, default=makes)
            with col3:
                sources = sorted(df["Source"].unique())
                selected_sources = st.multiselect("Fontes", sources, default=sources)

        mask = (
            (df["Score"] >= min_score)
            & df["Make"].isin(selected_makes)
            & df["Source"].isin(selected_sources)
        )
        filtered = df[mask]

        st.caption(f"Mostrando {len(filtered)} de {len(df)} deals")

        display_cols = [
            "Score", "Year", "Make", "Model", "Trim", "Bid (USD)",
            "Custo Total (BRL)", "Venda BR (BRL)", "Lucro (BRL)",
            "Margem %", "Mileage", "Title", "Source", "Cambio",
        ]
        st.dataframe(
            filtered[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Score": st.column_config.NumberColumn(format="%.1f"),
                "Bid (USD)": st.column_config.NumberColumn(format="$ %d"),
                "Custo Total (BRL)": st.column_config.NumberColumn(format="R$ %d"),
                "Venda BR (BRL)": st.column_config.NumberColumn(format="R$ %d"),
                "Lucro (BRL)": st.column_config.NumberColumn(format="R$ %d"),
                "Margem %": st.column_config.NumberColumn(format="%.1f %%"),
                "Mileage": st.column_config.NumberColumn(format="%d km"),
                "Cambio": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        # Score breakdown
        section("Detalhes do Score")
        if not filtered.empty:
            selected_idx = st.selectbox(
                "Selecionar deal",
                range(len(filtered)),
                format_func=lambda i: (
                    f"{filtered.iloc[i]['Year']} {filtered.iloc[i]['Make']} "
                    f"{filtered.iloc[i]['Model']} (Score: {filtered.iloc[i]['Score']})"
                ),
            )
            selected = filtered.iloc[selected_idx]

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Margem", f"{selected['Score Margin']:.0f}")
            col2.metric("Liquidez", f"{selected['Score Liquidity']:.0f}")
            col3.metric("Condicao", f"{selected['Score Condition']:.0f}")
            col4.metric("Urgencia", f"{selected['Score Time']:.0f}")
            col5.metric("Historico", f"{selected['Score History']:.0f}")

            st.markdown(f"[Abrir listing]({selected['URL']})")

# =============================================================================
# MONITORADOS
# =============================================================================
elif page == "Monitorados":
    section("Leiloes Monitorados")

    # Add URL form
    with st.form("add_monitor", clear_on_submit=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            monitor_url = st.text_input(
                "URL do leilao",
                placeholder="https://www.copart.com/lot/78272755",
            )
        with col2:
            st.write("")  # spacing
            submitted = st.form_submit_button("Monitorar")

    if submitted and monitor_url:
        parsed = TelegramCommandHandler.parse_auction_url(monitor_url)
        if parsed:
            source, source_id = parsed
            session = get_session()
            try:
                existing = session.execute(
                    select(MonitoredAuction).where(
                        MonitoredAuction.source_id == source_id,
                        MonitoredAuction.is_active == True,  # noqa: E712
                    )
                ).scalar_one_or_none()

                if existing:
                    st.warning(f"Este leilao ja esta sendo monitorado (#{existing.id}).")
                else:
                    from datetime import datetime, timezone
                    from scrapers.copart import CopartScraper
                    from scrapers.bring_a_trailer import BringATrailerScraper
                    from scrapers.cars_and_bids import CarsAndBidsScraper
                    from scrapers.hemmings import HemmingsScraper

                    auction = MonitoredAuction(
                        source=source,
                        source_id=source_id,
                        url=monitor_url.strip(),
                        chat_id="dashboard",
                    )
                    session.add(auction)
                    session.flush()

                    # Immediate fetch
                    scraper_map = {
                        "copart": CopartScraper,
                        "bat": BringATrailerScraper,
                        "carsandbids": CarsAndBidsScraper,
                        "hemmings": HemmingsScraper,
                    }
                    scraper = scraper_map[source]()
                    try:
                        vehicle = scraper.fetch_single_listing(monitor_url.strip())
                        if vehicle:
                            existing_v = session.execute(
                                select(Vehicle).where(
                                    Vehicle.source == vehicle.source,
                                    Vehicle.source_id == vehicle.source_id,
                                )
                            ).scalar_one_or_none()

                            if existing_v:
                                if vehicle.current_bid_usd:
                                    existing_v.current_bid_usd = vehicle.current_bid_usd
                                if vehicle.auction_end:
                                    existing_v.auction_end = vehicle.auction_end
                                if vehicle.title_status:
                                    existing_v.title_status = vehicle.title_status
                                existing_v.is_active = True
                                vehicle = existing_v
                            else:
                                session.add(vehicle)
                                session.flush()

                            auction.vehicle_id = vehicle.id
                            auction.last_checked_at = datetime.now(timezone.utc)
                            st.success(
                                f"Monitorando: {vehicle.year} {vehicle.make} {vehicle.model} "
                                f"(#{auction.id})"
                            )
                        else:
                            st.warning("Nao foi possivel obter dados. O leilao sera verificado no proximo ciclo.")
                    except Exception as e:
                        st.warning(f"Fetch inicial falhou: {e}. Sera tentado novamente.")

                    session.commit()
            except Exception as e:
                session.rollback()
                st.error(f"Erro: {e}")
            finally:
                session.close()
        else:
            st.error("URL nao reconhecida. Use URLs do Copart, BaT, Cars & Bids ou Hemmings.")

    # Fetch all button
    if st.button("Forcar Fetch Todos"):
        session_tmp = get_session()
        try:
            all_auctions = session_tmp.execute(
                select(MonitoredAuction).where(MonitoredAuction.is_active == True)  # noqa: E712
            ).scalars().all()
            if not all_auctions:
                st.info("Nenhum leilao monitorado.")
            else:
                progress = st.progress(0)
                for i, a in enumerate(all_auctions):
                    ok, msg = force_fetch_auction(a)
                    if ok:
                        st.success(f"#{a.id}: {msg}")
                    else:
                        st.warning(f"#{a.id}: {msg}")
                    progress.progress((i + 1) / len(all_auctions))
                st.rerun()
        finally:
            session_tmp.close()

    # List monitored auctions
    session = get_session()
    try:
        auctions = session.execute(
            select(MonitoredAuction).where(MonitoredAuction.is_active == True)  # noqa: E712
        ).scalars().all()

        if not auctions:
            st.info("Nenhum leilao monitorado. Cole uma URL acima para comecar.")
        else:
            monitor_engine = MonitorEngine()
            rows = []
            for a in auctions:
                if not a.vehicle_id:
                    desc = _describe_from_url(a.url)
                    car_name = f"{desc.get('year', '')} {desc.get('make', '')} {desc.get('model', '')}".strip()
                    rows.append({
                        "ID": a.id,
                        "Veiculo": car_name or "Aguardando fetch",
                        "Bid (USD)": "—",
                        "Km": "—",
                        "Dano": "—",
                        "Titulo": desc.get("title", "—"),
                        "Local": "—",
                        "Tempo": "—",
                    })
                    continue

                vehicle = session.get(Vehicle, a.vehicle_id)
                if not vehicle:
                    continue

                analysis = monitor_engine.analyze(vehicle)
                has_end = vehicle.auction_end is not None
                tempo = MonitorEngine.format_time_remaining(
                    analysis.get("tempo_restante_s"), has_auction_end=has_end
                )

                bid_str = f"$ {int(vehicle.current_bid_usd):,}" if vehicle.current_bid_usd else "—"
                km_str = f"{vehicle.mileage:,} mi" if vehicle.mileage else "—"
                location = ""
                if vehicle.location_city and vehicle.location_state:
                    location = f"{vehicle.location_city}, {vehicle.location_state}"
                elif vehicle.location_state:
                    location = vehicle.location_state
                car_name = f"{vehicle.year} {vehicle.make} {vehicle.model}".strip()
                if vehicle.trim:
                    car_name += f" {vehicle.trim}"

                rows.append({
                    "ID": a.id,
                    "Veiculo": car_name,
                    "Bid (USD)": bid_str,
                    "Km": km_str,
                    "Dano": vehicle.damage_description or "—",
                    "Titulo": vehicle.title_status or "—",
                    "Local": location or "—",
                    "Tempo": tempo,
                })

            if rows:
                df_mon = pd.DataFrame(rows)
                st.dataframe(
                    df_mon,
                    use_container_width=True,
                    hide_index=True,
                )

                # Expanders with details and fetch buttons
                section("Detalhes")
                for a in auctions:
                    if not a.vehicle_id:
                        desc = _describe_from_url(a.url)
                        desc_str = f"{desc.get('year', '')} {desc.get('make', '')} {desc.get('model', '')}".strip()
                        label = f"[{a.source}] #{a.id} — {desc_str or 'Aguardando fetch'}"
                        with st.expander(label):
                            st.write(f"**URL:** {a.url}")
                            if st.button("Forcar Fetch", key=f"fetch_{a.id}"):
                                with st.spinner("Buscando dados..."):
                                    ok, msg = force_fetch_auction(a)
                                if ok:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                        continue

                    vehicle = session.get(Vehicle, a.vehicle_id)
                    if not vehicle:
                        continue

                    car_label = f"{vehicle.year} {vehicle.make} {vehicle.model}"
                    if vehicle.trim:
                        car_label += f" {vehicle.trim}"
                    label = f"[{a.source.upper()}] {car_label} — ${vehicle.current_bid_usd or 0:,.0f}"
                    with st.expander(label):
                        # --- Key metrics ---
                        analysis = monitor_engine.analyze(vehicle)
                        has_end = vehicle.auction_end is not None
                        tempo = MonitorEngine.format_time_remaining(
                            analysis.get("tempo_restante_s"), has_auction_end=has_end
                        )

                        # Vehicle info header
                        st.markdown(f"### {car_label}")
                        if vehicle.engine_cc:
                            st.caption(f"Motor: {vehicle.engine_cc/1000:.1f}L | {vehicle.title_status or 'N/A'} title")
                        col_btn, col_link, col_remove = st.columns([1, 2, 1])
                        with col_btn:
                            if st.button("Atualizar", key=f"fetch_{a.id}"):
                                with st.spinner("Buscando dados..."):
                                    ok, msg = force_fetch_auction(a)
                                if ok:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with col_link:
                            st.markdown(f"[Abrir no {a.source.title()}]({vehicle.url})")
                        with col_remove:
                            if st.button("Remover", key=f"remove_{a.id}", type="secondary"):
                                _remove_auction(a.id)
                                st.rerun()

                        # --- Auction metrics row ---
                        st.divider()
                        m1, m2, m3, m4, m5 = st.columns(5)
                        m1.metric("Bid Atual", f"${vehicle.current_bid_usd or 0:,.0f}")
                        m2.metric("Milhas", f"{vehicle.mileage:,}" if vehicle.mileage else "—")
                        m3.metric("Titulo", (vehicle.title_status or "—").title())
                        m4.metric("Tempo", tempo)
                        m5.metric("Cambio", f"R$ {analysis.get('usd_brl_rate', 0):.2f}" if analysis else "—")

                        # --- Profit analysis ---
                        if analysis and analysis.get("custo_total_brl"):
                            st.divider()
                            st.markdown("#### Analise de Importacao")
                            p1, p2, p3, p4 = st.columns(4)
                            custo = analysis["custo_total_brl"]
                            p1.metric("Custo Total BR", f"R$ {custo:,.0f}")

                            venda = analysis.get("venda_estimada_brl")
                            if venda:
                                p2.metric("Venda Estimada", f"R$ {venda:,.0f}")
                                lucro = analysis.get("lucro_brl", 0)
                                margem = analysis.get("margem_pct", 0)
                                delta_color = "normal" if lucro and lucro > 0 else "inverse"
                                p3.metric(
                                    "Lucro Estimado",
                                    f"R$ {lucro:,.0f}" if lucro else "—",
                                    delta=f"{margem:.1f}%" if margem else None,
                                    delta_color=delta_color,
                                )
                                # Verdict
                                if margem and margem > 20:
                                    p4.metric("Veredicto", "Excelente")
                                    p4.success("Margem alta!")
                                elif margem and margem > 10:
                                    p4.metric("Veredicto", "Bom")
                                    p4.info("Margem positiva")
                                elif margem and margem > 0:
                                    p4.metric("Veredicto", "Apertado")
                                    p4.warning("Margem baixa")
                                else:
                                    p4.metric("Veredicto", "Prejuizo")
                                    p4.error("Sem margem")
                            else:
                                p2.metric("Venda Estimada", "Sem dados BR")
                                p3.metric("Lucro Estimado", "—")
                                p4.info("Adicione dados do mercado BR para calcular lucro")

                            # Cost breakdown expander
                            with st.expander("Ver decomposicao de custos"):
                                bc1, bc2, bc3 = st.columns(3)
                                bc1.write(f"**Leilao:** ${vehicle.current_bid_usd or 0:,.0f}")
                                bc1.write(f"**CIF (BRL):** R$ {analysis.get('cif_brl', 0):,.0f}")
                                bc2.write(f"**Impostos:** R$ {analysis.get('total_taxes_brl', 0):,.0f}")
                                bc2.write(f"**Cambio:** R$ {analysis.get('usd_brl_rate', 0):.2f}")
                                bc3.write(f"**Custo Total:** R$ {custo:,.0f}")
                                if venda:
                                    bc3.write(f"**Venda Est.:** R$ {venda:,.0f}")

                        # --- Vehicle details ---
                        st.divider()
                        d1, d2 = st.columns(2)
                        with d1:
                            if vehicle.damage_description:
                                st.write(f"**Dano:** {vehicle.damage_description}")
                            if vehicle.location_city or vehicle.location_state:
                                loc = f"{vehicle.location_city or ''}, {vehicle.location_state or ''}".strip(", ")
                                st.write(f"**Local:** {loc}")
                        with d2:
                            if vehicle.vin:
                                st.write(f"**VIN:** `{vehicle.vin}`")
                            if vehicle.engine_cc:
                                st.write(f"**Motor:** {vehicle.engine_cc/1000:.1f}L ({vehicle.engine_cc}cc)")

                        # --- Price history ---
                        st.divider()
                        history = get_price_history(vehicle.id)
                        if history and len(history) >= 2:
                            st.markdown("#### Historico de Bids")
                            hist_df = pd.DataFrame(history)
                            st.line_chart(hist_df.set_index("timestamp")["price"])
                            with st.expander(f"Ver {len(history)} registos"):
                                tbl = pd.DataFrame(history)
                                tbl.columns = ["Data/Hora", "Bid (USD)"]
                                tbl["Bid (USD)"] = tbl["Bid (USD)"].apply(lambda x: f"${x:,.0f}")
                                tbl["Data/Hora"] = pd.to_datetime(tbl["Data/Hora"]).dt.strftime("%d/%m %H:%M")
                                st.dataframe(tbl, use_container_width=True, hide_index=True)
                        elif history and len(history) == 1:
                            st.info(
                                f"1 registo de bid: **${history[0]['price']:,.0f}** "
                                f"em {history[0]['timestamp'].strftime('%d/%m %H:%M') if hasattr(history[0]['timestamp'], 'strftime') else history[0]['timestamp']}. "
                                "Faca mais fetches para ver a evolucao."
                            )
                        else:
                            st.caption("Sem historico de bids. Clique 'Atualizar' para registar.")

                        # --- Manual edit (collapsed) ---
                        with st.expander("Editar dados manualmente"):
                            with st.form(key=f"edit_{a.id}"):
                                ecol1, ecol2, ecol3 = st.columns(3)
                                new_bid = ecol1.number_input(
                                    "Bid (USD)", value=float(vehicle.current_bid_usd or 0),
                                    min_value=0.0, step=50.0, key=f"bid_{a.id}"
                                )
                                new_mileage = ecol2.number_input(
                                    "Milhas", value=int(vehicle.mileage or 0),
                                    min_value=0, step=1000, key=f"mi_{a.id}"
                                )
                                new_damage = ecol3.text_input(
                                    "Dano", value=vehicle.damage_description or "",
                                    key=f"dmg_{a.id}"
                                )
                                ecol4, ecol5, ecol6 = st.columns(3)
                                new_location = ecol4.text_input(
                                    "Local (ex: PA - Philadelphia)",
                                    value=f"{vehicle.location_state or ''} - {vehicle.location_city or ''}".strip(" -"),
                                    key=f"loc_{a.id}"
                                )
                                new_vin = ecol5.text_input(
                                    "VIN", value=vehicle.vin or "", key=f"vin_{a.id}"
                                )
                                new_engine = ecol6.text_input(
                                    "Motor (ex: 5.5L)",
                                    value=f"{vehicle.engine_cc/1000:.1f}L" if vehicle.engine_cc else "",
                                    key=f"eng_{a.id}"
                                )
                                if st.form_submit_button("Salvar"):
                                    _save_manual_edit(
                                        vehicle.id, new_bid, new_mileage, new_damage,
                                        new_location, new_vin, new_engine
                                    )
                                    st.success("Dados atualizados!")
                                    st.rerun()

        # =================================================================
        # WATCHLIST (Telegram /watch) — integrado em Monitorados
        # =================================================================
        # WATCHLIST — adicionar + listar
        # =================================================================
        st.divider()
        section("Watchlist")

        # --- Add watch form ---
        with st.form("add_watch", clear_on_submit=True):
            st.caption("Adicionar veiculo a watchlist (tambem pode usar /watch no Telegram)")
            wc1, wc2, wc3 = st.columns(3)
            with wc1:
                w_make = st.text_input("Marca", placeholder="Porsche")
                w_model = st.text_input("Modelo", placeholder="911")
            with wc2:
                w_year_min = st.number_input("Ano de", value=0, min_value=0, max_value=2030, step=1)
                w_year_max = st.number_input("Ano ate", value=0, min_value=0, max_value=2030, step=1)
            with wc3:
                w_keywords = st.text_input("Keywords", placeholder="993, Turbo")
                w_max_price = st.number_input("Preco max (USD)", value=0, min_value=0, step=1000)
            w_submitted = st.form_submit_button("Adicionar a Watchlist")

        if w_submitted and w_make and w_model:
            try:
                new_watch = WatchlistItem(
                    make=w_make.strip(),
                    model=w_model.strip(),
                    year_min=w_year_min if w_year_min > 0 else None,
                    year_max=w_year_max if w_year_max > 0 else None,
                    keywords=w_keywords.strip() if w_keywords.strip() else None,
                    max_price_usd=float(w_max_price) if w_max_price > 0 else None,
                    chat_id="dashboard",
                )
                session.add(new_watch)
                session.commit()
                year_str = ""
                if w_year_min > 0 and w_year_max > 0:
                    year_str = f" ({w_year_min}-{w_year_max})"
                price_str = f" | Max ${w_max_price:,.0f}" if w_max_price > 0 else ""
                st.success(f"Adicionado: {w_make} {w_model}{year_str}{price_str}")
                st.rerun()
            except Exception as e:
                session.rollback()
                st.error(f"Erro: {e}")

        # --- List watchlist items ---
        try:
            watch_items = session.execute(
                select(WatchlistItem).where(WatchlistItem.is_active == True)  # noqa: E712
            ).scalars().all()
        except Exception:
            session.rollback()
            watch_items = []
            st.warning("Tabela watchlist ainda nao disponivel. Execute o pipeline para criar.")

        if not watch_items:
            st.caption("Nenhum item na watchlist.")
        else:
            for item in watch_items:
                # Build label
                label_parts = []
                if item.year_min and item.year_max:
                    label_parts.append(f"{item.year_min}-{item.year_max}")
                elif item.year:
                    label_parts.append(str(item.year))
                label_parts.append(item.make or "")
                label_parts.append(item.model or "")
                if item.vin:
                    label_parts.append(f"VIN:{item.vin}")
                label = " ".join(p for p in label_parts if p).strip()
                extras = []
                if item.keywords:
                    extras.append(f"kw: {item.keywords}")
                if item.max_price_usd:
                    extras.append(f"max: ${item.max_price_usd:,.0f}")
                if extras:
                    label += f" [{', '.join(extras)}]"

                with st.expander(f"#{item.id} — {label}"):
                    if item.vehicle_id:
                        vehicle = session.get(Vehicle, item.vehicle_id)
                        if vehicle:
                            deal = session.execute(
                                select(Deal).where(
                                    Deal.vehicle_id == vehicle.id,
                                    Deal.is_active == True,  # noqa: E712
                                )
                            ).scalar_one_or_none()

                            col1, col2, col3 = st.columns(3)
                            col1.write(f"**Bid:** ${vehicle.current_bid_usd or 0:,.0f}")
                            col2.write(f"**Source:** {vehicle.source}")
                            if deal:
                                col3.write(f"**Score:** {deal.score:.0f} | **Margem:** {deal.margin_pct:.1f}%")

                            history = get_price_history(vehicle.id)
                            if history:
                                hist_df = pd.DataFrame(history)
                                st.line_chart(hist_df.set_index("timestamp")["price"])

                            st.markdown(f"[Abrir listing]({vehicle.url})")
                    else:
                        st.write("Veiculo ainda nao encontrado no banco de dados.")

                    if item.notes:
                        st.caption(f"Notas: {item.notes}")
                    col_info, col_remove = st.columns([3, 1])
                    with col_info:
                        st.caption(f"Adicionado: {item.created_at.strftime('%d/%m/%Y %H:%M')}")
                    with col_remove:
                        if st.button("Remover", key=f"rmwatch_{item.id}", type="secondary"):
                            try:
                                db_item = session.get(WatchlistItem, item.id)
                                if db_item:
                                    db_item.is_active = False
                                    session.commit()
                                st.rerun()
                            except Exception:
                                session.rollback()

    finally:
        session.close()

# =============================================================================
# ANALISE DE MERCADO
# =============================================================================
elif page == "Analise de Mercado":
    section("Analise de Mercado BR")

    session = get_session()
    try:
        makes_result = session.execute(
            select(Vehicle.make).where(Vehicle.is_active == True).distinct()  # noqa: E712
        ).scalars().all()
    finally:
        session.close()

    if not makes_result:
        st.info("Nenhum veiculo no banco.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            selected_make = st.selectbox("Marca", sorted(makes_result))
        with col2:
            session = get_session()
            try:
                models_result = session.execute(
                    select(Vehicle.model).where(
                        Vehicle.make == selected_make, Vehicle.is_active == True  # noqa: E712
                    ).distinct()
                ).scalars().all()
            finally:
                session.close()
            selected_model = st.selectbox("Modelo", sorted(models_result) if models_result else [])
        with col3:
            session = get_session()
            try:
                years_result = session.execute(
                    select(Vehicle.year).where(
                        Vehicle.make == selected_make,
                        Vehicle.model == selected_model,
                        Vehicle.is_active == True,  # noqa: E712
                    ).distinct()
                ).scalars().all()
            finally:
                session.close()
            selected_year = st.selectbox("Ano", sorted(years_result, reverse=True) if years_result else [])

        if selected_make and selected_model and selected_year:
            snapshots = get_market_snapshots(selected_make, selected_model, selected_year)
            if snapshots:
                snap_df = pd.DataFrame(snapshots)
                st.line_chart(snap_df.set_index("date")[["avg", "min", "max"]])

                latest = snapshots[-1]
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Preco Medio", f"R$ {latest['avg']:,.0f}")
                col2.metric("Minimo", f"R$ {latest['min']:,.0f}")
                col3.metric("Maximo", f"R$ {latest['max']:,.0f}")
                col4.metric("Anuncios", latest["count"])
            else:
                st.info("Sem dados de mercado BR para este veiculo.")

            session = get_session()
            try:
                vehicles = session.execute(
                    select(Vehicle).where(
                        Vehicle.make == selected_make,
                        Vehicle.model == selected_model,
                        Vehicle.year == selected_year,
                        Vehicle.is_active == True,  # noqa: E712
                    )
                ).scalars().all()

                for v in vehicles:
                    with st.expander(f"[{v.source}] ${v.current_bid_usd or 0:,.0f} — {v.url.split('/')[-1][:50]}"):
                        history = get_price_history(v.id)
                        if history:
                            hist_df = pd.DataFrame(history)
                            st.line_chart(hist_df.set_index("timestamp")["price"])
                        else:
                            st.write("Sem historico de preco.")
                        st.markdown(f"[Ver listing]({v.url})")
            finally:
                session.close()

# =============================================================================
# CALCULADORA ROI
# =============================================================================
elif page == "Calculadora ROI":
    section("Calculadora — Importacao de carros da Copart (EUA → Brasil)")

    # --- Tab selector ---
    calc_tab = st.radio(
        "Secao",
        ["Parametros", "Resultado", "Detalhamento impostos"],
        horizontal=True,
        label_visibility="collapsed",
    )

    # --- Session state defaults ---
    _defaults = {
        "calc_lance": 8000, "calc_buyerfee": 900, "calc_misc_us": 200,
        "calc_frete_ocean": 1200, "calc_ptax": 5.80, "calc_venda": 120000,
        "calc_cilin": "1.0-2.0L (IPI 13%)", "calc_despachante": 4000,
        "calc_porto": 2000, "calc_frete_br": 1500, "calc_mecanica": 8000,
        "calc_vend_cost": 2000, "calc_ir_type": "PJ Simples Nacional ~ 6%",
    }
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # --- IPI rates map ---
    _IPI_MAP = {
        "≤ 1.0L (IPI 7%)": 0.07,
        "1.0-2.0L (IPI 13%)": 0.13,
        "2.0-3.0L (IPI 25%)": 0.25,
        "> 3.0L (IPI 55%)": 0.55,
    }
    _IR_MAP = {
        "PF — IRPF 27.5%": 0.275,
        "PJ Simples Nacional ~ 6%": 0.06,
        "PJ Lucro Presumido ~ 13.5%": 0.135,
    }

    def _fmt_brl(n):
        return f"R$ {n:,.0f}".replace(",", ".")

    def _fmt_usd(n):
        return f"US$ {n:,.0f}".replace(",", ".")

    def _pct(n):
        return f"{n * 100:.1f}%"

    # =========================
    # PARAMETROS TAB
    # =========================
    if calc_tab == "Parametros":
        col_left, col_right = st.columns(2)

        with col_left:
            # Compra no leilao
            st.markdown('<div class="section-header">COMPRA NO LEILAO (EUA)</div>', unsafe_allow_html=True)
            st.session_state["calc_lance"] = st.number_input(
                "Preco no leilao (USD)", value=st.session_state["calc_lance"],
                step=500, min_value=0, key="inp_lance",
            )
            st.session_state["calc_buyerfee"] = st.number_input(
                "Taxa Copart (buyer fee)", value=st.session_state["calc_buyerfee"],
                step=50, min_value=0, key="inp_buyerfee",
            )
            st.session_state["calc_misc_us"] = st.number_input(
                "Storage / titulo / misc", value=st.session_state["calc_misc_us"],
                step=50, min_value=0, key="inp_misc",
            )
            st.session_state["calc_frete_ocean"] = st.number_input(
                "Frete EUA → porto BR (USD)", value=st.session_state["calc_frete_ocean"],
                step=100, min_value=0, key="inp_frete_ocean",
            )

            # Cambio e referencias
            st.markdown('<div class="section-header">CAMBIO E REFERENCIAS</div>', unsafe_allow_html=True)
            st.session_state["calc_ptax"] = st.slider(
                "USD/BRL (ptax)", min_value=4.50, max_value=7.00,
                value=st.session_state["calc_ptax"], step=0.05, key="inp_ptax",
            )
            st.session_state["calc_venda"] = st.number_input(
                "Preco venda BR (BRL)", value=st.session_state["calc_venda"],
                step=5000, min_value=0, key="inp_venda",
            )
            st.session_state["calc_cilin"] = st.selectbox(
                "Cilindradas do motor",
                list(_IPI_MAP.keys()),
                index=list(_IPI_MAP.keys()).index(st.session_state["calc_cilin"]),
                key="inp_cilin",
            )

        with col_right:
            # Custos nacionais
            st.markdown('<div class="section-header">CUSTOS NACIONAIS (BRL)</div>', unsafe_allow_html=True)
            st.session_state["calc_despachante"] = st.number_input(
                "Despachante / agente importacao", value=st.session_state["calc_despachante"],
                step=500, min_value=0, key="inp_desp",
            )

            # AFRMM is calculated
            afrmm_preview = st.session_state["calc_frete_ocean"] * st.session_state["calc_ptax"] * 0.25
            st.markdown(f"**AFRMM (25% do frete maritimo):** {_fmt_brl(afrmm_preview)}")

            st.session_state["calc_porto"] = st.number_input(
                "Armazenagem / capatazia porto", value=st.session_state["calc_porto"],
                step=200, min_value=0, key="inp_porto",
            )
            st.session_state["calc_frete_br"] = st.number_input(
                "Frete porto → destino BR", value=st.session_state["calc_frete_br"],
                step=200, min_value=0, key="inp_frete_br",
            )
            st.session_state["calc_mecanica"] = st.number_input(
                "Funilaria / latoaria / revisao", value=st.session_state["calc_mecanica"],
                step=1000, min_value=0, key="inp_mec",
            )
            st.session_state["calc_vend_cost"] = st.number_input(
                "Custo de venda (anuncio/comissao)", value=st.session_state["calc_vend_cost"],
                step=500, min_value=0, key="inp_vend_cost",
            )

            # Estrutura societaria
            st.markdown('<div class="section-header">ESTRUTURA SOCIETARIA (PJ OU PF?)</div>', unsafe_allow_html=True)
            st.session_state["calc_ir_type"] = st.selectbox(
                "Imposto sobre lucro",
                list(_IR_MAP.keys()),
                index=list(_IR_MAP.keys()).index(st.session_state["calc_ir_type"]),
                key="inp_ir",
            )

    # =========================
    # CALCULATION ENGINE (runs for all tabs)
    # =========================
    ptax = st.session_state["calc_ptax"]
    lance = st.session_state["calc_lance"]
    buyerfee = st.session_state["calc_buyerfee"]
    misc_us = st.session_state["calc_misc_us"]
    frete_ocean = st.session_state["calc_frete_ocean"]
    venda = st.session_state["calc_venda"]
    ipi_rate = _IPI_MAP[st.session_state["calc_cilin"]]
    despachante = st.session_state["calc_despachante"]
    porto = st.session_state["calc_porto"]
    frete_br = st.session_state["calc_frete_br"]
    mecanica = st.session_state["calc_mecanica"]
    vend_cost = st.session_state["calc_vend_cost"]
    ir_rate = _IR_MAP[st.session_state["calc_ir_type"]]

    # CIF
    total_usd_cif = lance + buyerfee + misc_us + frete_ocean
    VA = total_usd_cif * ptax  # Valor aduaneiro

    # Impostos (cascata)
    II = VA * 0.35
    IPI_base = VA + II
    IPI = IPI_base * ipi_rate
    PIS_COFINS_base = VA + II + IPI
    PIS = PIS_COFINS_base * 0.021
    COFINS = PIS_COFINS_base * 0.0965
    base_pre_icms = VA + II + IPI + PIS + COFINS
    ICMS = base_pre_icms / (1 - 0.12) * 0.12
    total_impostos = II + IPI + PIS + COFINS + ICMS

    # Outros custos
    AFRMM = frete_ocean * ptax * 0.25
    IOF = total_usd_cif * ptax * 0.0038

    custo_total = VA + total_impostos + AFRMM + IOF + despachante + porto + frete_br + mecanica + vend_cost
    lucro_bruto = venda - custo_total
    IR = max(0, lucro_bruto * ir_rate)
    lucro_liquido = lucro_bruto - IR
    roi = lucro_liquido / custo_total if custo_total > 0 else 0
    margem = lucro_bruto / venda if venda > 0 else 0

    # =========================
    # RESULTADO TAB
    # =========================
    if calc_tab == "Resultado":
        # Top metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Custo total all-in (BRL)", _fmt_brl(custo_total))
        m2.metric("Receita bruta (BRL)", _fmt_brl(venda))
        if lucro_liquido >= 0:
            m3.metric("Lucro liquido (BRL)", _fmt_brl(lucro_liquido), delta=_pct(roi))
        else:
            m3.metric("Lucro liquido (BRL)", _fmt_brl(lucro_liquido), delta=_pct(roi), delta_color="inverse")
        m4.metric("ROI sobre capital", _pct(roi))

        m5, m6, m7 = st.columns(3)
        m5.metric("Impostos import. (BRL)", _fmt_brl(total_impostos))
        pct_imp = total_impostos / custo_total if custo_total > 0 else 0
        m6.metric("% custo = impostos", f"{_pct(pct_imp)} do custo")
        m7.metric("Margem bruta s/ IR", _pct(margem))

        # Fluxo simplificado
        st.markdown('<div class="section-header">FLUXO SIMPLIFICADO</div>', unsafe_allow_html=True)

        breakdown_rows = [
            ("Preco leilao (USD→BRL)", VA, ""),
            ("Buyer fee + storage (USD→BRL)", (buyerfee + misc_us) * ptax, ""),
            ("Frete maritimo (USD→BRL)", frete_ocean * ptax, ""),
            ("─── Subtotal CIF", VA, "subtotal"),
            (f"II — Imposto de Importacao (35%)", II, "imp"),
            (f"IPI ({_pct(ipi_rate)})", IPI, "imp"),
            ("PIS (2.1%)", PIS, "imp"),
            ("COFINS (9.65%)", COFINS, "imp"),
            ("ICMS SP (12% por dentro)", ICMS, "imp"),
            ("─── Total impostos importacao", total_impostos, "subtotal"),
            ("AFRMM (25% do frete maritimo)", AFRMM, ""),
            ("IOF cambio remessa (~0.38%)", IOF, ""),
            ("Despachante / agente", float(despachante), ""),
            ("Armazenagem + capatazia porto", float(porto), ""),
            ("Frete porto → destino", float(frete_br), ""),
            ("Funilaria / mecanica / revisao", float(mecanica), ""),
            ("Custo de venda (anuncios, etc.)", float(vend_cost), ""),
        ]

        # Build HTML table
        html_rows = '<tr><th style="text-align:left;padding:6px 8px;font-weight:500;color:#888;font-size:12px;border-bottom:1px solid #eee">Item</th>'
        html_rows += '<th style="text-align:right;padding:6px 8px;font-weight:500;color:#888;font-size:12px;border-bottom:1px solid #eee">Valor (BRL)</th></tr>'

        for label, value, cls in breakdown_rows:
            style = ""
            td_style = "padding:6px 8px;border-bottom:0.5px solid #eee;font-size:13px;"
            if cls == "subtotal":
                td_style += "font-weight:500;border-top:1px solid #ccc;background:#f5f5f3;"
            elif cls == "imp":
                td_style += "color:#185FA5;"
            html_rows += f'<tr><td style="{td_style}">{label}</td><td style="{td_style}text-align:right;font-weight:500">{_fmt_brl(value)}</td></tr>'

        # Total line
        total_style = "padding:6px 8px;font-weight:500;border-top:2px solid #ccc;background:#f5f5f3;font-size:13px;"
        html_rows += f'<tr><td style="{total_style}">══ CUSTO TOTAL ALL-IN</td><td style="{total_style}text-align:right">{_fmt_brl(custo_total)}</td></tr>'

        # Profit lines
        pos_style = "padding:6px 8px;border-bottom:0.5px solid #eee;font-size:13px;color:#3b6d11;"
        neg_style = "padding:6px 8px;border-bottom:0.5px solid #eee;font-size:13px;color:#a32d2d;"
        neutral_style = "padding:6px 8px;border-bottom:0.5px solid #eee;font-size:13px;"

        html_rows += f'<tr><td style="{pos_style}">Receita de venda</td><td style="{pos_style}text-align:right;font-weight:500">{_fmt_brl(venda)}</td></tr>'

        lb_style = pos_style if lucro_bruto >= 0 else neg_style
        html_rows += f'<tr><td style="{lb_style}">Lucro bruto</td><td style="{lb_style}text-align:right;font-weight:500">{_fmt_brl(lucro_bruto)}</td></tr>'

        html_rows += f'<tr><td style="{neg_style}">IR/tributo sobre lucro ({_pct(ir_rate)})</td><td style="{neg_style}text-align:right;font-weight:500">{_fmt_brl(-IR)}</td></tr>'

        ll_style = pos_style if lucro_liquido >= 0 else neg_style
        final_style = ll_style.replace("border-bottom:0.5px solid #eee;", "border-top:2px solid #ccc;background:#f5f5f3;")
        html_rows += f'<tr><td style="{final_style}font-weight:600">══ LUCRO LIQUIDO</td><td style="{final_style}text-align:right;font-weight:600">{_fmt_brl(lucro_liquido)}</td></tr>'

        st.markdown(
            f'<div style="background:#fff;border:0.5px solid #e0e0d8;border-radius:12px;padding:1rem 1.25rem">'
            f'<table style="width:100%;border-collapse:collapse">{html_rows}</table></div>',
            unsafe_allow_html=True,
        )

        # Warning note
        st.markdown(
            '<div style="font-size:12px;color:#777;margin-top:.75rem;line-height:1.6;padding:.75rem;'
            'background:#fffbf0;border-left:3px solid #f0c040;border-radius:4px">'
            '⚠️ <b>Atencao:</b> carros salvage/rebuild americanos podem ter dificuldade de regularizacao '
            'no DETRAN dependendo do estado. Verifique a legislacao de laudos veiculares. '
            'Nao inclui IOF sobre remessa cambial (~1.1% para PJ ou 0.38% PF). '
            'Homologacao INMETRO e DENATRAN pode ser obrigatoria.</div>',
            unsafe_allow_html=True,
        )

    # =========================
    # DETALHAMENTO IMPOSTOS TAB
    # =========================
    if calc_tab == "Detalhamento impostos":
        st.markdown('<div class="section-header">MEMORIA DE CALCULO — TRIBUTOS DE IMPORTACAO</div>', unsafe_allow_html=True)

        imp_rows = [
            ("Valor aduaneiro (CIF x cambio)", "Base", VA),
            ("II — Imposto de Importacao", "35% x VA", II),
            ("Base IPI", "VA + II", IPI_base),
            ("IPI", f"{_pct(ipi_rate)} x base IPI", IPI),
            ("Base PIS/COFINS", "VA + II + IPI", PIS_COFINS_base),
            ("PIS", "2.1%", PIS),
            ("COFINS", "9.65%", COFINS),
            ("Base ICMS (pre-ICMS)", "soma anterior", base_pre_icms),
            ('ICMS SP (12% "por dentro")', "base/(1-12%)x12%", ICMS),
        ]

        html_imp = '<tr><th style="text-align:left;padding:6px 8px;font-weight:500;color:#888;font-size:12px;border-bottom:1px solid #eee">Tributo</th>'
        html_imp += '<th style="text-align:left;padding:6px 8px;font-weight:500;color:#888;font-size:12px;border-bottom:1px solid #eee">Aliquota / base</th>'
        html_imp += '<th style="text-align:right;padding:6px 8px;font-weight:500;color:#888;font-size:12px;border-bottom:1px solid #eee">Valor</th></tr>'

        td_base = "padding:6px 8px;border-bottom:0.5px solid #eee;font-size:13px;"
        for label, aliq, value in imp_rows:
            html_imp += (
                f'<tr><td style="{td_base}">{label}</td>'
                f'<td style="{td_base}color:#999;font-size:12px">{aliq}</td>'
                f'<td style="{td_base}text-align:right;font-weight:500">{_fmt_brl(value)}</td></tr>'
            )

        # Totals
        total_td = "padding:6px 8px;font-weight:500;border-top:1px solid #ccc;background:#f5f5f3;font-size:13px;"
        html_imp += f'<tr><td style="{total_td}">TOTAL IMPOSTOS</td><td style="{total_td}"></td><td style="{total_td}text-align:right">{_fmt_brl(total_impostos)}</td></tr>'
        pct_va = total_impostos / VA if VA > 0 else 0
        html_imp += f'<tr><td style="{total_td}">% do valor aduaneiro</td><td style="{total_td}"></td><td style="{total_td}text-align:right">{_pct(pct_va)}</td></tr>'

        st.markdown(
            f'<div style="background:#fff;border:0.5px solid #e0e0d8;border-radius:12px;padding:1rem 1.25rem">'
            f'<table style="width:100%;border-collapse:collapse">{html_imp}</table></div>',
            unsafe_allow_html=True,
        )

        # Explanation note
        st.markdown(
            '<div style="font-size:12px;color:#777;margin-top:.75rem;line-height:1.6;padding:.75rem;'
            'background:#fffbf0;border-left:3px solid #f0c040;border-radius:4px">'
            '<b>Base de calculo (BC):</b> Para fins alfandegarios, a base e o <i>valor aduaneiro</i> = '
            'preco CIF (Cost Insurance Freight em USD) x cambio PTAX.<br><br>'
            '<b>Cascata dos impostos:</b> II incide sobre BC → IPI incide sobre (BC + II) → '
            'PIS/COFINS incidem sobre (BC + II + IPI) → ICMS incide sobre tudo incluindo ele mesmo '
            '(base "por dentro"), variando por estado. Os percentuais acima usam SP como referencia '
            '(ICMS 12% via formula de dentro = ~13.6% sobre valor excl.).<br><br>'
            '<b>Regimes especiais:</b> Importacao por PJ pode aproveitar credito de PIS/COFINS '
            '(regime nao-cumulativo) se tributada pelo Lucro Real — nao considerado aqui (modelo conservador).'
            '</div>',
            unsafe_allow_html=True,
        )
