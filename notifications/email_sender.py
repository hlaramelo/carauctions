"""Email alerts - instant deal notifications and daily digest."""

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

from models.deal import Deal, AlertLog
from models.vehicle import Vehicle
from models.database import get_session


class EmailSender:
    """Send deal alerts via email (SMTP)."""

    def __init__(self):
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.from_email = os.environ.get("EMAIL_FROM", self.smtp_user)
        self.to_emails = [
            e.strip()
            for e in os.environ.get("EMAIL_TO", "").split(",")
            if e.strip()
        ]

        if not self.is_configured:
            logger.warning(
                "[Email] SMTP_USER, SMTP_PASSWORD, and/or EMAIL_TO not set. "
                "Email alerts will be logged but not sent."
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.to_emails)

    def send_deal_alert(self, deal: Deal, vehicle: Vehicle) -> bool:
        """Send an instant alert for a single high-score deal."""
        subject = (
            f"Deal Alert: {vehicle.year} {vehicle.make} {vehicle.model} "
            f"(Score: {deal.score:.0f})"
        )
        html_body = self._format_deal_html(deal, vehicle)

        success = self._send_email(subject, html_body)
        if success:
            self._log_alert(deal.id, "email")
        return success

    def send_daily_digest(self, deals_with_vehicles: list[tuple[Deal, Vehicle]]) -> bool:
        """Send a daily digest email with the top deals."""
        if not deals_with_vehicles:
            logger.info("[Email] No deals for daily digest")
            return False

        today = datetime.utcnow().strftime("%d/%m/%Y")
        subject = f"Car Auction Deals - Resumo Diario ({today})"
        html_body = self._format_digest_html(deals_with_vehicles, today)

        return self._send_email(subject, html_body)

    def _send_email(self, subject: str, html_body: str) -> bool:
        """Send an HTML email via SMTP."""
        if not self.is_configured:
            logger.info(f"[Email] Would send email: {subject}")
            logger.debug(f"[Email] Body preview: {html_body[:200]}...")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = ", ".join(self.to_emails)

        # Plain text fallback
        plain_text = html_body.replace("<br>", "\n").replace("</p>", "\n")
        import re
        plain_text = re.sub(r"<[^>]+>", "", plain_text)

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(f"[Email] Sent: {subject}")
            return True
        except smtplib.SMTPException as e:
            logger.error(f"[Email] SMTP error: {e}")
            return False
        except OSError as e:
            logger.error(f"[Email] Connection error: {e}")
            return False

    @staticmethod
    def _format_deal_html(deal: Deal, vehicle: Vehicle) -> str:
        """Format a single deal as an HTML email."""
        mileage_str = f"{vehicle.mileage:,} mi" if vehicle.mileage else "N/A"
        location = f"{vehicle.location_city or ''}, {vehicle.location_state or 'N/A'}"
        trim_str = f"<em>{vehicle.trim}</em><br>" if vehicle.trim else ""

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #1a1a2e; color: white; padding: 15px 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">Deal Alert (Score: {deal.score:.0f}/100)</h2>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 0 0 8px 8px;">
                <h3 style="margin-top: 0;">{vehicle.year} {vehicle.make} {vehicle.model}</h3>
                {trim_str}

                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Bid Atual</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">${deal.auction_price_usd:,.0f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Custo Total BR</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">R${deal.total_landed_cost_brl:,.0f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Preco Venda BR</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">R${deal.estimated_sale_price_brl:,.0f}</td>
                    </tr>
                    <tr style="background: #e8f5e9;">
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Lucro Estimado</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee; color: #2e7d32; font-weight: bold;">
                            R${deal.estimated_profit_brl:,.0f} ({deal.margin_pct:.1f}%)
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Kilometragem</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">{mileage_str}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Localizacao</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">{location}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Title Status</strong></td>
                        <td style="padding: 8px; border-bottom: 1px solid #eee;">{vehicle.title_status or 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px;"><strong>Cambio</strong></td>
                        <td style="padding: 8px;">R${deal.usd_brl_rate:.2f}</td>
                    </tr>
                </table>

                <div style="margin-top: 20px; text-align: center;">
                    <a href="{vehicle.url}" style="background: #1a1a2e; color: white; padding: 12px 24px;
                       text-decoration: none; border-radius: 5px; display: inline-block;">
                        Ver no Site
                    </a>
                </div>
            </div>
        </body>
        </html>
        """

    @staticmethod
    def _format_digest_html(deals_with_vehicles: list[tuple[Deal, Vehicle]], date: str) -> str:
        """Format a daily digest as an HTML email."""
        rows = ""
        for i, (deal, vehicle) in enumerate(deals_with_vehicles[:20], 1):
            margin_color = "#2e7d32" if deal.margin_pct >= 25 else ("#f57f17" if deal.margin_pct >= 15 else "#c62828")
            rows += f"""
            <tr>
                <td style="padding: 6px; border-bottom: 1px solid #eee; text-align: center;">{deal.score:.0f}</td>
                <td style="padding: 6px; border-bottom: 1px solid #eee;">
                    <a href="{vehicle.url}">{vehicle.year} {vehicle.make} {vehicle.model}</a>
                </td>
                <td style="padding: 6px; border-bottom: 1px solid #eee; text-align: right;">${deal.auction_price_usd:,.0f}</td>
                <td style="padding: 6px; border-bottom: 1px solid #eee; text-align: right;">R${deal.estimated_profit_brl:,.0f}</td>
                <td style="padding: 6px; border-bottom: 1px solid #eee; text-align: right; color: {margin_color}; font-weight: bold;">
                    {deal.margin_pct:.1f}%
                </td>
                <td style="padding: 6px; border-bottom: 1px solid #eee;">{vehicle.source}</td>
            </tr>
            """

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto;">
            <div style="background: #1a1a2e; color: white; padding: 15px 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">Resumo Diario de Deals - {date}</h2>
                <p style="margin: 5px 0 0; opacity: 0.8;">{len(deals_with_vehicles)} deals encontrados</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 15px; border-radius: 0 0 8px 8px; overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                    <thead>
                        <tr style="background: #f5f5f5;">
                            <th style="padding: 8px; text-align: center;">Score</th>
                            <th style="padding: 8px; text-align: left;">Veiculo</th>
                            <th style="padding: 8px; text-align: right;">Bid (USD)</th>
                            <th style="padding: 8px; text-align: right;">Lucro Est.</th>
                            <th style="padding: 8px; text-align: right;">Margem</th>
                            <th style="padding: 8px; text-align: left;">Fonte</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
            <p style="color: #888; font-size: 12px; margin-top: 15px;">
                Gerado automaticamente pelo Car Auction Deal Finder
            </p>
        </body>
        </html>
        """

    @staticmethod
    def _log_alert(deal_id: int, channel: str):
        """Log that an alert was sent."""
        session = get_session()
        try:
            log = AlertLog(deal_id=deal_id, channel=channel)
            session.add(log)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to log email alert: {e}")
            session.rollback()
        finally:
            session.close()
