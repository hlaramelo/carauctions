"""Interactive Telegram bot - handles user commands via webhook/polling.

Commands:
  /start   - Welcome message and help
  /deals   - Show top 10 deals
  /watch   - Add vehicle to watchlist (/watch VIN or /watch Make Model Year)
  /unwatch - Remove from watchlist (/unwatch VIN)
  /watchlist - Show current watchlist
  /filters - Show active filters
  /pause   - Pause deal alerts
  /resume  - Resume deal alerts
  /status  - Show system status
  /help    - Show available commands
"""

import json
import os
import threading

from loguru import logger
from sqlalchemy import select, func

from config import load_settings
from models.database import get_session, init_db
from models.deal import Deal
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
            "/unwatch `VIN` - Parar de acompanhar\n"
            "/watchlist - Ver veiculos acompanhados\n"
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

    def _cmd_watch(self, chat_id: str, args: str):
        """Add a vehicle to the watchlist."""
        if not args:
            self._send(chat_id, (
                "Uso:\n"
                "`/watch WVWZZZ3CZWE123456` - por VIN\n"
                "`/watch Porsche 911 2022` - por marca/modelo/ano"
            ))
            return

        session = get_session()
        try:
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
                    chat_id=chat_id,
                )
                session.add(item)
                session.commit()

                if vehicle:
                    self._send(chat_id,
                        f"Adicionado a watchlist: *{vehicle.year} {vehicle.make} {vehicle.model}*\n"
                        f"VIN: `{cleaned}`"
                    )
                else:
                    self._send(chat_id,
                        f"VIN `{cleaned}` adicionado a watchlist.\n"
                        "Veiculo nao encontrado no banco ainda — sera monitorado quando aparecer."
                    )
            else:
                # Watch by make/model/year
                parts = args.strip().split()
                if len(parts) < 2:
                    self._send(chat_id, "Formato: `/watch Make Model Year` (ex: `/watch Porsche 911 2022`)")
                    return

                # Try to extract year (last token if numeric)
                year = None
                if parts[-1].isdigit() and len(parts[-1]) == 4:
                    year = int(parts.pop())

                make = parts[0]
                model = " ".join(parts[1:]) if len(parts) > 1 else ""

                item = WatchlistItem(
                    make=make,
                    model=model,
                    year=year,
                    chat_id=chat_id,
                )
                session.add(item)
                session.commit()

                year_str = f" {year}" if year else ""
                self._send(chat_id, f"Adicionado a watchlist: *{make} {model}{year_str}*")

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
                    spec = f"{item.year or ''} {item.make} {item.model or ''}".strip()
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
