"""Streamlit dashboard for Car Auction Deal Finder.

Run with: streamlit run dashboard.py
"""

import json

import pandas as pd
import streamlit as st
from sqlalchemy import select, func

from models.database import get_session, init_db
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
        watchlist_items = session.execute(
            select(func.count(WatchlistItem.id)).where(WatchlistItem.is_active == True)  # noqa: E712
        ).scalar() or 0

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
            if vehicle.image_urls:
                existing_v.image_urls = vehicle.image_urls
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
        ["Dashboard", "Deals", "Monitorados", "Analise de Mercado", "Watchlist"],
        label_visibility="collapsed",
    )

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
                    label = f"[{a.source}] {car_label}"
                    with st.expander(label):
                        col_btn, col_link = st.columns([1, 3])
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

                        analysis = monitor_engine.analyze(vehicle)
                        has_end = vehicle.auction_end is not None
                        tempo = MonitorEngine.format_time_remaining(
                            analysis.get("tempo_restante_s"), has_auction_end=has_end
                        )

                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Bid Atual", f"${vehicle.current_bid_usd or 0:,.0f}")
                        col2.metric("Km", f"{vehicle.mileage:,} mi" if vehicle.mileage else "—")
                        col3.metric("Titulo", vehicle.title_status or "—")
                        col4.metric("Tempo", tempo)

                        if vehicle.damage_description:
                            st.write(f"**Dano:** {vehicle.damage_description}")
                        if vehicle.location_city or vehicle.location_state:
                            loc = f"{vehicle.location_city or ''}, {vehicle.location_state or ''}".strip(", ")
                            st.write(f"**Local:** {loc}")
                        if vehicle.vin:
                            st.write(f"**VIN:** {vehicle.vin}")

                        # Price history chart
                        history = get_price_history(vehicle.id)
                        if history:
                            st.write("**Historico de Bid:**")
                            hist_df = pd.DataFrame(history)
                            st.line_chart(hist_df.set_index("timestamp")["price"])
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
# WATCHLIST
# =============================================================================
elif page == "Watchlist":
    section("Watchlist")

    session = get_session()
    try:
        items = session.execute(
            select(WatchlistItem).where(WatchlistItem.is_active == True)  # noqa: E712
        ).scalars().all()

        if not items:
            st.info("Watchlist vazia. Adicione itens via Telegram com /watch.")
        else:
            for item in items:
                label = item.vin or f"{item.year or ''} {item.make or ''} {item.model or ''}".strip()

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

                    st.caption(f"Adicionado: {item.created_at.strftime('%d/%m/%Y %H:%M')}")
    finally:
        session.close()
