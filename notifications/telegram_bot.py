"""Telegram bot for sending deal alerts and handling commands."""

import os

import requests
from loguru import logger

from models.deal import Deal, AlertLog
from models.monitored_auction import MonitoredAuction
from models.vehicle import Vehicle
from models.database import get_session


class TelegramNotifier:
    """Send deal alerts via Telegram Bot API."""

    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"

        if not self.bot_token or not self.chat_id:
            logger.warning(
                "[Telegram] TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set. "
                "Alerts will be logged but not sent."
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_deal_alert(self, deal: Deal, vehicle: Vehicle) -> bool:
        """Send a formatted deal alert message."""
        message = self._format_deal_message(deal, vehicle)

        if not self.is_configured:
            logger.info(f"[Telegram] Would send alert:\n{message}")
            return False

        success = self._send_message(message)

        if success:
            self._log_alert(deal.id, "telegram")

        return success

    def send_top_deals(self, deals: list[tuple[Deal, Vehicle]], title: str = "Top Deals") -> bool:
        """Send a summary of top deals."""
        if not deals:
            return False

        lines = [f"*{title}*\n"]
        for i, (deal, vehicle) in enumerate(deals[:10], 1):
            lines.append(
                f"{i}. *{vehicle.year} {vehicle.make} {vehicle.model}*\n"
                f"   Score: {deal.score:.0f} | Margem: {deal.margin_pct:.1f}%\n"
                f"   Bid: ${deal.auction_price_usd:,.0f} | Lucro est.: R${deal.estimated_profit_brl:,.0f}\n"
                f"   [Link]({vehicle.url})\n"
            )

        message = "\n".join(lines)

        if not self.is_configured:
            logger.info(f"[Telegram] Would send:\n{message}")
            return False

        return self._send_message(message)

    def send_monitor_alert(
        self,
        auction: MonitoredAuction,
        vehicle: Vehicle,
        analysis: dict,
        reason: str,
    ) -> bool:
        """Send an alert for a monitored auction."""
        from engine.monitor_engine import MonitorEngine

        tempo = MonitorEngine.format_time_remaining(analysis.get("tempo_restante_s"))
        bid_str = f"${analysis.get('auction_price_usd', 0):,.0f}"
        custo_str = f"R${analysis.get('custo_total_brl', 0):,.0f}"

        lines = [
            f"📡 *MONITOR UPDATE*",
            f"*{vehicle.year} {vehicle.make} {vehicle.model}*",
            f"",
            f"⚡ {reason}",
            f"",
            f"💰 Bid: {bid_str}",
            f"📊 Custo BR: {custo_str}",
        ]

        if analysis.get("lucro_brl") is not None:
            lines.append(f"📈 Lucro est.: R${analysis['lucro_brl']:,.0f} ({analysis['margem_pct']:.1f}%)")

        lines.extend([
            f"🏷️ Titulo: {analysis.get('titulo', 'N/A')}",
            f"⏰ Tempo: {tempo}",
            f"",
            f"[Ver listing]({vehicle.url})",
        ])

        message = "\n".join(lines)
        target_chat = auction.chat_id if auction.chat_id != "dashboard" else self.chat_id

        if not self.is_configured:
            logger.info(f"[Telegram] Would send monitor alert:\n{message}")
            return False

        return self._send_message_to(target_chat, message)

    def _send_message_to(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to a specific chat ID."""
        try:
            response = requests.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"[Telegram] Failed to send message to {chat_id}: {e}")
            return False

    def send_message(self, text: str) -> bool:
        """Send a plain text message."""
        if not self.is_configured:
            logger.info(f"[Telegram] Would send: {text}")
            return False
        return self._send_message(text)

    def _send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message via Telegram Bot API."""
        try:
            response = requests.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            response.raise_for_status()
            logger.info("[Telegram] Message sent successfully")
            return True
        except requests.RequestException as e:
            logger.error(f"[Telegram] Failed to send message: {e}")
            return False

    def send_watchlist_alert(
        self,
        watch: "WatchlistItem",
        vehicles: list[Vehicle],
    ) -> bool:
        """Send an alert when new vehicles match a watchlist item."""
        from models.watchlist import WatchlistItem  # noqa: F811

        # Build watch label
        if watch.year_min and watch.year_max:
            year_str = f"{watch.year_min}-{watch.year_max}"
        elif watch.year:
            year_str = str(watch.year)
        else:
            year_str = ""

        label = f"{year_str} {watch.make or ''} {watch.model or ''}".strip()
        if watch.keywords:
            label += f" [{watch.keywords}]"

        lines = [
            f"🔔 *WATCHLIST MATCH*",
            f"_{label}_\n",
            f"{len(vehicles)} novo(s) leilao(oes) encontrado(s):\n",
        ]

        for v in vehicles[:5]:  # Max 5 per message
            bid_str = f"${v.current_bid_usd:,.0f}" if v.current_bid_usd else "N/A"
            mileage_str = f"{v.mileage:,} mi" if v.mileage else "N/A"
            title_str = v.title_status or "N/A"
            lines.append(
                f"*{v.year} {v.make} {v.model}*\n"
                f"  💰 Bid: {bid_str} | {mileage_str} | {title_str}\n"
                f"  [Ver]({v.url})\n"
            )

        if len(vehicles) > 5:
            lines.append(f"_...e mais {len(vehicles) - 5} resultado(s)_")

        message = "\n".join(lines)
        target_chat = watch.chat_id

        if not self.bot_token:
            logger.info(f"[Telegram] Would send watchlist alert:\n{message}")
            return False

        return self._send_message_to(target_chat, message)

    @staticmethod
    def _format_deal_message(deal: Deal, vehicle: Vehicle) -> str:
        """Format a deal into a readable Telegram message."""
        reserve_info = ""
        if vehicle.reserve_met is not None:
            reserve_info = " (Reserve Met)" if vehicle.reserve_met else " (Reserve Not Met)"

        mileage_str = f"{vehicle.mileage:,} mi" if vehicle.mileage else "N/A"

        return (
            f"🚗 *NEW DEAL ALERT* (Score: {deal.score:.0f}/100)\n\n"
            f"*{vehicle.year} {vehicle.make} {vehicle.model}*\n"
            f"{'_' + vehicle.trim + '_' if vehicle.trim else ''}\n\n"
            f"💰 Bid atual: ${deal.auction_price_usd:,.0f}{reserve_info}\n"
            f"📊 Custo total BR: R${deal.total_landed_cost_brl:,.0f}\n"
            f"🏷️ Preco venda BR: R${deal.estimated_sale_price_brl:,.0f}\n"
            f"✅ Lucro estimado: R${deal.estimated_profit_brl:,.0f}\n"
            f"📈 Margem: {deal.margin_pct:.1f}%\n\n"
            f"📍 {vehicle.location_city or ''}, {vehicle.location_state or 'N/A'}\n"
            f"🔢 {mileage_str} | {vehicle.title_status or 'N/A'} title\n"
            f"💱 Câmbio: R${deal.usd_brl_rate:.2f}\n\n"
            f"[Ver no site]({vehicle.url})"
        )

    @staticmethod
    def _log_alert(deal_id: int, channel: str):
        """Log that an alert was sent to avoid duplicates."""
        session = get_session()
        try:
            log = AlertLog(deal_id=deal_id, channel=channel)
            session.add(log)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to log alert: {e}")
            session.rollback()
        finally:
            session.close()
