"""Streamlit dashboard for Car Auction Deal Finder.

Run with: streamlit run dashboard.py
"""

import json

import streamlit as st
from sqlalchemy import select, func

from models.database import get_session, init_db
from models.deal import Deal
from models.vehicle import Vehicle, PriceHistory
from models.br_listing import BRMarketListing, BRPriceSnapshot
from models.watchlist import WatchlistItem
from engine.price_history import BRMarketAnalyzer
from engine.currency import get_usd_brl_rate

init_db()


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
                "Year": vehicle.year,
                "Make": vehicle.make,
                "Model": vehicle.model,
                "Trim": vehicle.trim or "",
                "Bid (USD)": round(deal.auction_price_usd),
                "Custo Total (BRL)": round(deal.total_landed_cost_brl),
                "Venda BR (BRL)": round(deal.estimated_sale_price_brl),
                "Lucro (BRL)": round(deal.estimated_profit_brl),
                "Margem %": round(deal.margin_pct, 1),
                "Mileage": vehicle.mileage or 0,
                "Title": vehicle.title_status or "N/A",
                "Source": vehicle.source,
                "Cambio": round(deal.usd_brl_rate, 2),
                "URL": vehicle.url,
                "Score Margin": breakdown.get("margin", 0),
                "Score Liquidity": breakdown.get("liquidity", 0),
                "Score Condition": breakdown.get("condition", 0),
                "Score Time": breakdown.get("time_remaining", 0),
                "Score History": breakdown.get("price_history", 0),
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

        # Average score and margin
        avg_score = session.execute(
            select(func.avg(Deal.score)).where(Deal.is_active == True)  # noqa: E712
        ).scalar() or 0
        avg_margin = session.execute(
            select(func.avg(Deal.margin_pct)).where(Deal.is_active == True)  # noqa: E712
        ).scalar() or 0

        # Source breakdown
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


# =============================================================================
# STREAMLIT APP
# =============================================================================

st.set_page_config(
    page_title="Car Auction Deal Finder",
    page_icon="🚗",
    layout="wide",
)

st.title("🚗 Car Auction Deal Finder")

# --- Sidebar ---
st.sidebar.header("Navegacao")
page = st.sidebar.radio("", ["Dashboard", "Deals", "Analise de Mercado", "Watchlist"])

# --- Dashboard Page ---
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
    from engine.currency import _cache as _currency_cache
    try:
        usd_brl = get_usd_brl_rate()
        source = _currency_cache.get("source", "fallback")
        label = "USD/BRL (PTAX)" if source == "bcb" else "USD/BRL (fallback)"
        col7.metric(label, f"R$ {usd_brl:.4f}")
    except Exception:
        col7.metric("USD/BRL", "Indisponivel")

    st.subheader("Veiculos por Fonte")
    if stats["sources"]:
        source_names = {
            "bat": "Bring a Trailer",
            "copart": "Copart",
            "carsandbids": "Cars & Bids",
            "hemmings": "Hemmings",
        }
        chart_data = {source_names.get(k, k): v for k, v in stats["sources"].items()}
        st.bar_chart(chart_data)
    else:
        st.info("Nenhum veiculo no banco ainda. Execute o pipeline primeiro.")

    # Top deals preview
    st.subheader("Top 5 Deals")
    rows = get_deals_df()
    if rows:
        import pandas as pd
        df = pd.DataFrame(rows[:5])
        display_cols = ["Score", "Year", "Make", "Model", "Bid (USD)", "Lucro (BRL)", "Margem %", "Source"]
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum deal encontrado.")

# --- Deals Page ---
elif page == "Deals":
    st.subheader("Todos os Deals Ativos")

    rows = get_deals_df()
    if not rows:
        st.info("Nenhum deal encontrado. Execute o pipeline primeiro.")
    else:
        import pandas as pd
        df = pd.DataFrame(rows)

        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            min_score = st.slider("Score minimo", 0, 100, 0)
        with col2:
            makes = sorted(df["Make"].unique())
            selected_makes = st.multiselect("Marcas", makes, default=makes)
        with col3:
            sources = sorted(df["Source"].unique())
            selected_sources = st.multiselect("Fontes", sources, default=sources)

        # Apply filters
        mask = (
            (df["Score"] >= min_score)
            & df["Make"].isin(selected_makes)
            & df["Source"].isin(selected_sources)
        )
        filtered = df[mask]

        st.write(f"Mostrando {len(filtered)} de {len(df)} deals")

        # Main table
        display_cols = [
            "Score", "Year", "Make", "Model", "Trim", "Bid (USD)",
            "Custo Total (BRL)", "Venda BR (BRL)", "Lucro (BRL)",
            "Margem %", "Mileage", "Title", "Source", "Cambio",
        ]
        st.dataframe(
            filtered[display_cols].style.background_gradient(
                subset=["Score", "Margem %"], cmap="RdYlGn"
            ),
            use_container_width=True,
            hide_index=True,
        )

        # Score breakdown for selected deal
        st.subheader("Detalhes do Score")
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

# --- Market Analysis Page ---
elif page == "Analise de Mercado":
    st.subheader("Analise de Mercado BR")

    session = get_session()
    try:
        # Get unique makes from vehicles
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
            # Get models for selected make
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
            # BR Market trend
            snapshots = get_market_snapshots(selected_make, selected_model, selected_year)
            if snapshots:
                import pandas as pd
                snap_df = pd.DataFrame(snapshots)
                st.line_chart(snap_df.set_index("date")[["avg", "min", "max"]])

                latest = snapshots[-1]
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Preco Medio", f"R${latest['avg']:,.0f}")
                col2.metric("Minimo", f"R${latest['min']:,.0f}")
                col3.metric("Maximo", f"R${latest['max']:,.0f}")
                col4.metric("Anuncios", latest["count"])
            else:
                st.info("Sem dados de mercado BR para este veiculo.")

            # Get auction vehicle details
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
                    with st.expander(f"[{v.source}] ${v.current_bid_usd or 0:,.0f} - {v.url.split('/')[-1][:50]}"):
                        history = get_price_history(v.id)
                        if history:
                            import pandas as pd
                            hist_df = pd.DataFrame(history)
                            st.line_chart(hist_df.set_index("timestamp")["price"])
                        else:
                            st.write("Sem historico de preco.")
                        st.markdown(f"[Ver listing]({v.url})")
            finally:
                session.close()

# --- Watchlist Page ---
elif page == "Watchlist":
    st.subheader("Watchlist")

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

                with st.expander(f"#{item.id} - {label}"):
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
                                import pandas as pd
                                hist_df = pd.DataFrame(history)
                                st.line_chart(hist_df.set_index("timestamp")["price"])

                            st.markdown(f"[Abrir listing]({vehicle.url})")
                    else:
                        st.write("Veiculo ainda nao encontrado no banco de dados.")

                    st.caption(f"Adicionado: {item.created_at.strftime('%d/%m/%Y %H:%M')}")
    finally:
        session.close()
