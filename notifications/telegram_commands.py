"""Interactive Telegram bot - handles user commands via webhook/polling.

Commands:
  /start    - Welcome message and help
  /deals    - Show top 10 deals
  /watch    - Add vehicle to watchlist (/watch VIN or /watch Make Model Year)
  /unwatch  - Remove from watchlist (/unwatch VIN)
  /watchlist - Show current watchlist
  /monitor  - Monitor a specific auction (/monitor URL)
  /monitors - Show monitored auctions
  /unmonitor - Stop monitoring (/unmonitor ID)
  /filters  - Show active filters
  /pause    - Pause deal alerts
  /resume   - Resume deal alerts
  /status   - Show system status
  /help     - Show available commands
"""

import json
import os
import re
import threading

from loguru import logger
from sqlalchemy import select, func

from config import load_settings
from models.database import get_session, init_db
from models.deal import Deal
from models.monitored_auction import MonitoredAuction
from models.vehicle import Vehicle
from models.watchlist import WatchlistItem, UserPreferences


class TelegramCommandHandler:
    """Handles interactive Telegram bot commands."""

    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._polling = False

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    def start_polling(self):
        """Start polling for Telegram updates in a background thread."""
        if not self.is_configured:
            logger.warning("[TelegramBot] Bot token not set, commands disabled")
            return

        self._polling = True
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()
        logger.info("[TelegramBot] Command polling started")

    def stop_polling(self):
        self._polling = False

    def _poll_loop(self):
        """Long-poll for updates from Telegram."""
        import time
        import requests

        offset = 0
        while self._polling:
            try:
                response = requests.get(
                    f"{self.api_base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35,
                )
                if response.status_code != 200:
                    time.sleep(5)
                    continue

                data = response.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    self._handle_update(update)

            except requests.RequestException as e:
                logger.error(f"[TelegramBot] Polling error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"[TelegramBot] Unexpected error: {e}")
                time.sleep(5)

    def _handle_update(self, update: dict):
        """Route an incoming update to the appropriate command handler."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            return

        # Parse command and arguments
        parts = text.split(None, 1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Remove @botname suffix if present
        if "@" in command:
            command = command.split("@")[0]

        handlers = {
            "/start": self._cmd_start,
            "/help": self._cmd_start,
            "/deals": self._cmd_deals,
            "/watch": self._cmd_watch,
            "/unwatch": self._cmd_unwatch,
            "/watchlist": self._cmd_watchlist,
            "/monitor": self._cmd_monitor,
            "/monitors": self._cmd_monitors,
            "/unmonitor": self._cmd_unmonitor,
            "/filters": self._cmd_filters,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/status": self._cmd_status,
        }

        handler = handlers.get(command)
        if handler:
            try:
                handler(chat_id, args)
            except Exception as e:
                logger.error(f"[TelegramBot] Error handling {command}: {e}")
                self._send(chat_id, f"Erro ao processar comando: {e}")

    # --- Command handlers ---

    def _cmd_start(self, chat_id: str, _args: str):
        """Show help message."""
        self._send(chat_id, (
            "*Car Auction Deal Finder*\n\n"
            "Comandos disponiveis:\n\n"
            "/deals - Top 10 deals do momento\n"
            "/watch `VIN` - Acompanhar veiculo por VIN\n"
            "/watch `Make Model Year` - Acompanhar por spec\n"
            "/watch `Make Model 1993-1997` - Range de anos\n"
            "/watch `Make Model 1993-1997 keywords` - Com keywords\n"
            "/unwatch `VIN ou ID` - Parar de acompanhar\n"
            "/watchlist - Ver veiculos acompanhados\n\n"
            "*Monitoramento:*\n"
            "/monitor `URL` - Monitorar leilao especifico\n"
            "/monitors - Ver leiloes monitorados\n"
            "/unmonitor `ID` - Parar de monitorar\n\n"
            "/filters - Ver filtros ativos\n"
            "/pause - Pausar alertas\n"
            "/resume - Retomar alertas\n"
            "/status - Status do sistema"
        ))

    def _cmd_deals(self, chat_id: str, _args: str):
        """Show top 10 deals."""
        session = get_session()
        try:
            deals = session.execute(
                select(Deal).where(
                    Deal.is_active == True  # noqa: E712
                ).order_by(Deal.score.desc()).limit(10)
            ).scalars().all()

            if not deals:
                self._send(chat_id, "Nenhum deal ativo no momento.")
                return

            lines = ["*Top 10 Deals*\n"]
            for i, deal in enumerate(deals, 1):
                vehicle = session.get(Vehicle, deal.vehicle_id)
                if not vehicle:
                    continue

                source_emoji = {
                    "bat": "B", "copart": "C", "carsandbids": "A", "hemmings": "H"
                }.get(vehicle.source, "?")

                lines.append(
                    f"{i}. [{source_emoji}] *{vehicle.year} {vehicle.make} {vehicle.model}*\n"
                    f"   Score: {deal.score:.0f} | Margem: {deal.margin_pct:.1f}%\n"
                    f"   Bid: ${deal.auction_price_usd:,.0f} | Lucro: R${deal.estimated_profit_brl:,.0f}\n"
                    f"   [Ver]({vehicle.url})\n"
                )

            self._send(chat_id, "\n".join(lines))
        finally:
            session.close()

    # Year range pattern: "1993-1997" or "1993 1997"
    _YEAR_RANGE_RE = re.compile(r"(\d{4})\s*[-–]\s*(\d{4})")
    # Max price pattern: "max:30000" or "max:30k"
    _MAX_PRICE_RE = re.compile(r"max[:\s]?\$?(\d+\.?\d*)\s*k?", re.IGNORECASE)

    def _cmd_watch(self, chat_id: str, args: str):
        """Add a vehicle to the watchlist.

        Formats:
          /watch WVWZZZ3CZWE123456                        - by VIN
          /watch Porsche 911 2022                         - by make/model/year
          /watch Porsche 911 1993-1997                     - by make/model/year range
          /watch Porsche 911 1993-1997 993 Turbo           - with keywords
          /watch BMW M5 1988-1992 E34                      - with generation keyword
          /watch Porsche 911 1993-1997 993 max:30000       - with max price
        """
        if not args:
            self._send(chat_id, (
                "Uso:\n"
                "`/watch WVWZZZ3CZWE123456` - por VIN\n"
                "`/watch Porsche 911 2022` - por marca/modelo/ano\n"
                "`/watch Porsche 911 1993-1997` - range de anos\n"
                "`/watch Porsche 911 1993-1997 993 Turbo` - com keywords\n"
                "`/watch BMW M5 1985-1992 E28 E34` - com geracao\n"
                "`/watch Porsche 911 1993-1997 993 max:30000` - com preco max"
            ))
            return

        session = get_session()
        try:
            # Extract max price if present (remove from args before parsing)
            max_price = None
            price_match = self._MAX_PRICE_RE.search(args)
            if price_match:
                price_val = float(price_match.group(1))
                # Handle "30k" shorthand
                if args[price_match.end() - 1:price_match.end()].lower() == "k":
                    price_val *= 1000
                elif price_val < 1000:
                    # If user typed "max:30" they probably mean 30k
                    price_val *= 1000
                max_price = price_val
                # Remove the max:XXX from args
                args = args[:price_match.start()].strip() + " " + args[price_match.end():].strip()
                args = args.strip()

            # Check if it looks like a VIN (17 alphanumeric chars)
            cleaned = args.strip().upper()
            if len(cleaned) == 17 and cleaned.isalnum():
                # Watch by VIN
                existing = session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.vin == cleaned,
                        WatchlistItem.chat_id == chat_id,
                        WatchlistItem.is_active == True,  # noqa: E712
                    )
                ).scalar_one_or_none()

                if existing:
                    self._send(chat_id, f"VIN `{cleaned}` ja esta na sua watchlist.")
                    return

                # Try to find vehicle in DB
                vehicle = session.execute(
                    select(Vehicle).where(Vehicle.vin == cleaned)
                ).scalar_one_or_none()

                item = WatchlistItem(
                    vin=cleaned,
                    vehicle_id=vehicle.id if vehicle else None,
                    make=vehicle.make if vehicle else None,
                    model=vehicle.model if vehicle else None,
                    year=vehicle.year if vehicle else None,
                    max_price_usd=max_price,
                    chat_id=chat_id,
                )
                session.add(item)
                session.commit()

                price_str = f"\nPreco max: ${max_price:,.0f}" if max_price else ""
                if vehicle:
                    self._send(chat_id,
                        f"Adicionado a watchlist: *{vehicle.year} {vehicle.make} {vehicle.model}*\n"
                        f"VIN: `{cleaned}`{price_str}"
                    )
                else:
                    self._send(chat_id,
                        f"VIN `{cleaned}` adicionado a watchlist.\n"
                        f"Veiculo nao encontrado no banco ainda — sera monitorado quando aparecer.{price_str}"
                    )
            else:
                # Watch by make/model with optional year range, keywords, and max price
                parts = args.strip().split()
                if len(parts) < 2:
                    self._send(chat_id, "Formato: `/watch Make Model Ano` (ex: `/watch Porsche 911 2022`)")
                    return

                make = parts[0]
                remaining = " ".join(parts[1:])

                # Check for year range (e.g., "1993-1997")
                year = None
                year_min = None
                year_max = None
                keywords = None
                range_match = self._YEAR_RANGE_RE.search(remaining)

                if range_match:
                    year_min = int(range_match.group(1))
                    year_max = int(range_match.group(2))
                    # Everything before the range is model, everything after is keywords
                    before_range = remaining[:range_match.start()].strip()
                    after_range = remaining[range_match.end():].strip()
                    model = before_range
                    if after_range:
                        keywords = after_range
                else:
                    # Try single year at end
                    tokens = remaining.split()
                    if tokens[-1].isdigit() and len(tokens[-1]) == 4:
                        year = int(tokens.pop())
                        model = " ".join(tokens) if tokens else ""
                    else:
                        model = remaining
                        year = None

                item = WatchlistItem(
                    make=make,
                    model=model,
                    year=year,
                    year_min=year_min,
                    year_max=year_max,
                    keywords=keywords,
                    max_price_usd=max_price,
                    chat_id=chat_id,
                )
                session.add(item)
                session.commit()

                # Build confirmation message
                year_str = ""
                if year_min and year_max:
                    year_str = f" ({year_min}-{year_max})"
                elif year:
                    year_str = f" {year}"

                kw_str = f"\nKeywords: _{keywords}_" if keywords else ""
                price_str = f"\nPreco max: _${max_price:,.0f}_" if max_price else ""
                self._send(chat_id,
                    f"Adicionado a watchlist: *{make} {model}{year_str}*{kw_str}{price_str}\n"
                    f"Voce sera notificado quando aparecerem leiloes correspondentes."
                )

        except Exception as e:
            logger.error(f"Error in /watch: {e}")
            session.rollback()
            self._send(chat_id, f"Erro ao adicionar: {e}")
        finally:
            session.close()

    def _cmd_unwatch(self, chat_id: str, args: str):
        """Remove a vehicle from the watchlist."""
        if not args:
            self._send(chat_id, "Uso: `/unwatch VIN` ou `/unwatch ID`")
            return

        session = get_session()
        try:
            cleaned = args.strip().upper()

            # Try by VIN
            item = session.execute(
                select(WatchlistItem).where(
                    WatchlistItem.vin == cleaned,
                    WatchlistItem.chat_id == chat_id,
                    WatchlistItem.is_active == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            # Try by watchlist ID
            if not item and args.strip().isdigit():
                item = session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.id == int(args.strip()),
                        WatchlistItem.chat_id == chat_id,
                        WatchlistItem.is_active == True,  # noqa: E712
                    )
                ).scalar_one_or_none()

            if not item:
                self._send(chat_id, "Item nao encontrado na watchlist.")
                return

            item.is_active = False
            session.commit()
            label = item.vin or f"{item.make} {item.model}"
            self._send(chat_id, f"Removido da watchlist: *{label}*")
        finally:
            session.close()

    def _cmd_watchlist(self, chat_id: str, _args: str):
        """Show current watchlist."""
        session = get_session()
        try:
            items = session.execute(
                select(WatchlistItem).where(
                    WatchlistItem.chat_id == chat_id,
                    WatchlistItem.is_active == True,  # noqa: E712
                )
            ).scalars().all()

            if not items:
                self._send(chat_id, "Sua watchlist esta vazia.\nUse `/watch VIN` para adicionar.")
                return

            lines = ["*Sua Watchlist*\n"]
            for item in items:
                label = ""
                if item.vin:
                    label = f"VIN: `{item.vin}`"
                if item.make:
                    if item.year_min and item.year_max:
                        year_part = f"{item.year_min}-{item.year_max}"
                    elif item.year:
                        year_part = str(item.year)
                    else:
                        year_part = ""
                    spec = f"{year_part} {item.make} {item.model or ''}".strip()
                    if item.keywords:
                        spec += f" [{item.keywords}]"
                    if item.max_price_usd:
                        spec += f" max:${item.max_price_usd:,.0f}"
                    label = f"{spec} ({label})" if label else spec

                # Check for active deals
                deal_info = ""
                if item.vehicle_id:
                    deal = session.execute(
                        select(Deal).where(
                            Deal.vehicle_id == item.vehicle_id,
                            Deal.is_active == True,  # noqa: E712
                        )
                    ).scalar_one_or_none()
                    if deal:
                        deal_info = f" | Score: {deal.score:.0f} | Margem: {deal.margin_pct:.1f}%"

                lines.append(f"  #{item.id} - {label}{deal_info}")

            self._send(chat_id, "\n".join(lines))
        finally:
            session.close()

    def _cmd_filters(self, chat_id: str, _args: str):
        """Show active filters."""
        settings = load_settings()
        filters = settings.get("filters", {})

        makes = ", ".join(filters.get("makes", [])) or "Todas"
        titles = ", ".join(filters.get("title_status", [])) or "Todos"
        excluded = ", ".join(filters.get("exclude_states", [])) or "Nenhum"

        self._send(chat_id, (
            "*Filtros Ativos*\n\n"
            f"Ano minimo: {filters.get('min_year', 'N/A')}\n"
            f"Km maximo: {filters.get('max_mileage', 'N/A'):,}\n"
            f"Preco max USD: ${filters.get('max_auction_price_usd', 'N/A'):,}\n"
            f"Margem minima: {filters.get('min_margin_pct', 'N/A')}%\n"
            f"Title status: {titles}\n"
            f"Marcas: {makes}\n"
            f"Estados excluidos: {excluded}"
        ))

    def _cmd_pause(self, chat_id: str, _args: str):
        """Pause alerts for this user."""
        session = get_session()
        try:
            prefs = self._get_or_create_prefs(session, chat_id)
            prefs.alerts_paused = True
            session.commit()
            self._send(chat_id, "Alertas *pausados*. Use /resume para retomar.")
        finally:
            session.close()

    def _cmd_resume(self, chat_id: str, _args: str):
        """Resume alerts for this user."""
        session = get_session()
        try:
            prefs = self._get_or_create_prefs(session, chat_id)
            prefs.alerts_paused = False
            session.commit()
            self._send(chat_id, "Alertas *retomados*!")
        finally:
            session.close()

    def _cmd_status(self, chat_id: str, _args: str):
        """Show system status."""
        session = get_session()
        try:
            total_vehicles = session.execute(
                select(func.count(Vehicle.id)).where(Vehicle.is_active == True)  # noqa: E712
            ).scalar()
            total_deals = session.execute(
                select(func.count(Deal.id)).where(Deal.is_active == True)  # noqa: E712
            ).scalar()
            top_deal = session.execute(
                select(Deal).where(Deal.is_active == True).order_by(Deal.score.desc()).limit(1)  # noqa: E712
            ).scalar_one_or_none()

            # Count by source
            sources = {}
            for source_name in ["bat", "copart", "carsandbids", "hemmings"]:
                count = session.execute(
                    select(func.count(Vehicle.id)).where(
                        Vehicle.source == source_name,
                        Vehicle.is_active == True,  # noqa: E712
                    )
                ).scalar()
                if count:
                    sources[source_name] = count

            source_lines = "\n".join(f"  {k}: {v}" for k, v in sources.items()) or "  Nenhum"

            # User prefs
            prefs = session.execute(
                select(UserPreferences).where(UserPreferences.chat_id == chat_id)
            ).scalar_one_or_none()
            alert_status = "Pausados" if (prefs and prefs.alerts_paused) else "Ativos"

            watchlist_count = session.execute(
                select(func.count(WatchlistItem.id)).where(
                    WatchlistItem.chat_id == chat_id,
                    WatchlistItem.is_active == True,  # noqa: E712
                )
            ).scalar()

            top_info = ""
            if top_deal:
                v = session.get(Vehicle, top_deal.vehicle_id)
                if v:
                    top_info = (
                        f"\n*Melhor deal:*\n"
                        f"  {v.year} {v.make} {v.model}\n"
                        f"  Score: {top_deal.score:.0f} | Margem: {top_deal.margin_pct:.1f}%"
                    )

            self._send(chat_id, (
                "*Status do Sistema*\n\n"
                f"Veiculos ativos: {total_vehicles}\n"
                f"Deals ativos: {total_deals}\n"
                f"Alertas: {alert_status}\n"
                f"Watchlist: {watchlist_count} itens\n\n"
                f"*Fontes:*\n{source_lines}"
                f"{top_info}"
            ))
        finally:
            session.close()

    # --- Monitor commands ---

    # URL patterns for detecting auction source
    _URL_PATTERNS = [
        (re.compile(r"copart\.com/lot/(\d+)"), "copart", "copart_{0}"),
        (re.compile(r"bringatrailer\.com/listing/([^/?]+)"), "bat", "{0}"),
        (re.compile(r"carsandbids\.com/auctions/([^/?]+)"), "carsandbids", "cab_{0}"),
        (re.compile(r"hemmings\.com/classifieds/.*/(\d+)"), "hemmings", "hem_{0}"),
    ]

    @classmethod
    def parse_auction_url(cls, url: str) -> tuple[str, str] | None:
        """Parse an auction URL and return (source, source_id) or None."""
        for pattern, source, id_template in cls._URL_PATTERNS:
            match = pattern.search(url)
            if match:
                source_id = id_template.format(match.group(1))
                return source, source_id
        return None

    def _cmd_monitor(self, chat_id: str, args: str):
        """Monitor a specific auction by URL."""
        if not args:
            self._send(chat_id, (
                "Uso: `/monitor URL`\n\n"
                "Fontes suportadas:\n"
                "- copart.com/lot/...\n"
                "- bringatrailer.com/listing/...\n"
                "- carsandbids.com/auctions/...\n"
                "- hemmings.com/classifieds/..."
            ))
            return

        url = args.strip()
        parsed = self.parse_auction_url(url)
        if not parsed:
            self._send(chat_id, "URL nao reconhecida. Envie uma URL do Copart, BaT, Cars & Bids ou Hemmings.")
            return

        source, source_id = parsed

        session = get_session()
        try:
            # Check duplicate
            existing = session.execute(
                select(MonitoredAuction).where(
                    MonitoredAuction.source_id == source_id,
                    MonitoredAuction.is_active == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            if existing:
                self._send(chat_id, f"Este leilao ja esta sendo monitorado (#{existing.id}).")
                return

            auction = MonitoredAuction(
                source=source,
                source_id=source_id,
                url=url,
                chat_id=chat_id,
            )
            session.add(auction)
            session.commit()

            self._send(chat_id, f"Monitoramento ativado (#{auction.id})\nFonte: {source}\nBuscando dados...")

            # Immediate fetch
            self._fetch_and_report(chat_id, auction, session)

        except Exception as e:
            logger.error(f"Error in /monitor: {e}")
            session.rollback()
            self._send(chat_id, f"Erro ao monitorar: {e}")
        finally:
            session.close()

    def _fetch_and_report(self, chat_id: str, auction: MonitoredAuction, session):
        """Fetch auction data immediately and report back."""
        from datetime import datetime, timezone
        from scrapers.bring_a_trailer import BringATrailerScraper
        from scrapers.cars_and_bids import CarsAndBidsScraper
        from scrapers.copart import CopartScraper
        from scrapers.hemmings import HemmingsScraper
        from engine.monitor_engine import MonitorEngine

        scraper_map = {
            "copart": CopartScraper,
            "bat": BringATrailerScraper,
            "carsandbids": CarsAndBidsScraper,
            "hemmings": HemmingsScraper,
        }

        scraper_cls = scraper_map.get(auction.source)
        if not scraper_cls:
            self._send(chat_id, "Fonte nao suportada.")
            return

        try:
            scraper = scraper_cls()
            vehicle = scraper.fetch_single_listing(auction.url)
        except Exception as e:
            self._send(chat_id, f"Erro ao buscar dados: {e}")
            return

        if not vehicle:
            self._send(chat_id, "Nao foi possivel obter dados deste leilao. Verifique a URL.")
            return

        # Upsert vehicle
        existing_v = session.execute(
            select(Vehicle).where(
                Vehicle.source == vehicle.source,
                Vehicle.source_id == vehicle.source_id,
            )
        ).scalar_one_or_none()

        if existing_v:
            if vehicle.current_bid_usd:
                existing_v.current_bid_usd = vehicle.current_bid_usd
            if vehicle.buy_now_price_usd:
                existing_v.buy_now_price_usd = vehicle.buy_now_price_usd
            if vehicle.auction_end:
                existing_v.auction_end = vehicle.auction_end
            if vehicle.mileage:
                existing_v.mileage = vehicle.mileage
            if vehicle.title_status:
                existing_v.title_status = vehicle.title_status
            if vehicle.damage_description:
                existing_v.damage_description = vehicle.damage_description
            existing_v.is_active = True
            vehicle_obj = existing_v
        else:
            session.add(vehicle)
            session.flush()
            vehicle_obj = vehicle

        auction.vehicle_id = vehicle_obj.id
        auction.last_checked_at = datetime.now(timezone.utc)
        session.commit()

        # Analyze
        engine = MonitorEngine()
        analysis = engine.analyze(vehicle_obj)

        # Report
        bid_str = f"${vehicle_obj.current_bid_usd:,.0f}" if vehicle_obj.current_bid_usd else "N/A"
        title_str = vehicle_obj.title_status or "N/A"
        damage_str = f"\nDano: {vehicle_obj.damage_description}" if vehicle_obj.damage_description else ""

        lines = [
            f"*{vehicle_obj.year} {vehicle_obj.make} {vehicle_obj.model}*",
            f"Bid: {bid_str} | Titulo: {title_str}{damage_str}",
        ]

        if analysis.get("custo_total_brl"):
            lines.append(f"Custo total BR: R${analysis['custo_total_brl']:,.0f}")
        if analysis.get("venda_estimada_brl"):
            lines.append(f"Venda estimada: R${analysis['venda_estimada_brl']:,.0f}")
        if analysis.get("lucro_brl") is not None:
            lines.append(f"Lucro est.: R${analysis['lucro_brl']:,.0f} ({analysis['margem_pct']:.1f}%)")

        tempo = MonitorEngine.format_time_remaining(analysis.get("tempo_restante_s"))
        lines.append(f"Tempo restante: {tempo}")

        self._send(chat_id, "\n".join(lines))

    def _cmd_monitors(self, chat_id: str, _args: str):
        """List all actively monitored auctions."""
        session = get_session()
        try:
            auctions = session.execute(
                select(MonitoredAuction).where(
                    MonitoredAuction.chat_id == chat_id,
                    MonitoredAuction.is_active == True,  # noqa: E712
                )
            ).scalars().all()

            if not auctions:
                self._send(chat_id, "Nenhum leilao monitorado.\nUse `/monitor URL` para adicionar.")
                return

            from engine.monitor_engine import MonitorEngine

            lines = ["*Leiloes Monitorados*\n"]
            for a in auctions:
                if a.vehicle_id:
                    vehicle = session.get(Vehicle, a.vehicle_id)
                    if vehicle:
                        bid_str = f"${vehicle.current_bid_usd:,.0f}" if vehicle.current_bid_usd else "N/A"
                        engine = MonitorEngine()
                        analysis = engine.analyze(vehicle)
                        tempo = MonitorEngine.format_time_remaining(analysis.get("tempo_restante_s"))
                        margem = f" | Margem: {analysis['margem_pct']:.1f}%" if analysis.get("margem_pct") is not None else ""

                        lines.append(
                            f"#{a.id} [{a.source}] *{vehicle.year} {vehicle.make} {vehicle.model}*\n"
                            f"  Bid: {bid_str}{margem}\n"
                            f"  Tempo: {tempo}\n"
                            f"  [Ver]({a.url})\n"
                        )
                    else:
                        lines.append(f"#{a.id} [{a.source}] {a.url}\n  Dados pendentes\n")
                else:
                    lines.append(f"#{a.id} [{a.source}] {a.url}\n  Aguardando primeiro fetch\n")

            self._send(chat_id, "\n".join(lines))
        finally:
            session.close()

    def _cmd_unmonitor(self, chat_id: str, args: str):
        """Stop monitoring an auction."""
        if not args or not args.strip().isdigit():
            self._send(chat_id, "Uso: `/unmonitor ID` (ex: `/unmonitor 1`)")
            return

        auction_id = int(args.strip())
        session = get_session()
        try:
            auction = session.execute(
                select(MonitoredAuction).where(
                    MonitoredAuction.id == auction_id,
                    MonitoredAuction.chat_id == chat_id,
                    MonitoredAuction.is_active == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            if not auction:
                self._send(chat_id, f"Monitoramento #{auction_id} nao encontrado.")
                return

            auction.is_active = False
            session.commit()

            label = auction.url.split("/")[-1][:40]
            self._send(chat_id, f"Monitoramento #{auction_id} desativado ({label})")
        finally:
            session.close()

    # --- Helpers ---

    @staticmethod
    def _get_or_create_prefs(session, chat_id: str) -> UserPreferences:
        """Get or create user preferences."""
        prefs = session.execute(
            select(UserPreferences).where(UserPreferences.chat_id == chat_id)
        ).scalar_one_or_none()

        if not prefs:
            prefs = UserPreferences(chat_id=chat_id)
            session.add(prefs)
            session.flush()

        return prefs

    def _send(self, chat_id: str, text: str):
        """Send a message to a specific chat."""
        import requests

        if not self.is_configured:
            logger.info(f"[TelegramBot] -> {chat_id}: {text[:100]}...")
            return

        try:
            requests.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[TelegramBot] Failed to send: {e}")

    def is_user_paused(self, chat_id: str) -> bool:
        """Check if a user has alerts paused."""
        session = get_session()
        try:
            prefs = session.execute(
                select(UserPreferences).where(UserPreferences.chat_id == chat_id)
            ).scalar_one_or_none()
            return prefs.alerts_paused if prefs else False
        finally:
            session.close()
