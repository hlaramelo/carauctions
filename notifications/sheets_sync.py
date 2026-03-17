"""Google Sheets sync - exports deals, watchlist, and history to Google Spreadsheet."""

import os
from datetime import datetime

from loguru import logger

from models.deal import Deal
from models.vehicle import Vehicle
from models.watchlist import WatchlistItem


class SheetsSync:
    """Sync deal data to Google Sheets using gspread."""

    HEADERS = [
        "Score",
        "Year",
        "Make",
        "Model",
        "Trim",
        "Bid (USD)",
        "Custo Total (BRL)",
        "Preco Venda BR (BRL)",
        "Lucro Est. (BRL)",
        "Margem %",
        "Mileage",
        "Title Status",
        "Location",
        "Câmbio",
        "Source",
        "Link",
        "Updated",
    ]

    WATCHLIST_HEADERS = [
        "ID", "VIN", "Make", "Model", "Year",
        "Current Bid (USD)", "Score", "Margem %", "Source", "Link", "Added",
    ]

    HISTORY_HEADERS = [
        "Date", "Score", "Year", "Make", "Model",
        "Bid (USD)", "Lucro (BRL)", "Margem %", "Source", "Link",
    ]

    def __init__(self):
        self.spreadsheet_name = os.environ.get(
            "SHEETS_SPREADSHEET_NAME", "Car Auction Deals"
        )
        self.credentials_file = os.environ.get(
            "GOOGLE_CREDENTIALS_FILE", "credentials.json"
        )
        self._client = None

    def _get_client(self):
        """Initialize gspread client with service account credentials."""
        if self._client is None:
            try:
                import gspread

                self._client = gspread.service_account(
                    filename=self.credentials_file
                )
            except FileNotFoundError:
                logger.error(
                    f"[Sheets] Credentials file not found: {self.credentials_file}. "
                    "Set GOOGLE_CREDENTIALS_FILE env var or place credentials.json in project root."
                )
                return None
            except Exception as e:
                logger.error(f"[Sheets] Failed to initialize gspread: {e}")
                return None
        return self._client

    def sync_deals(self, deals_with_vehicles: list[tuple[Deal, Vehicle]]) -> bool:
        """Sync active deals to the 'Active Deals' sheet.

        Args:
            deals_with_vehicles: List of (Deal, Vehicle) tuples sorted by score desc.
        """
        client = self._get_client()
        if client is None:
            logger.warning("[Sheets] Client not available, logging deals instead")
            self._log_deals(deals_with_vehicles)
            return False

        try:
            spreadsheet = self._get_or_create_spreadsheet(client)
            worksheet = self._get_or_create_worksheet(spreadsheet, "Active Deals")

            # Clear existing data and write headers + data
            worksheet.clear()
            rows = [self.HEADERS]

            for deal, vehicle in deals_with_vehicles:
                rows.append(self._deal_to_row(deal, vehicle))

            worksheet.update(range_name="A1", values=rows)

            # Apply basic formatting
            self._apply_formatting(worksheet, len(rows))

            logger.info(
                f"[Sheets] Synced {len(deals_with_vehicles)} deals to '{self.spreadsheet_name}'"
            )
            return True

        except Exception as e:
            logger.error(f"[Sheets] Failed to sync deals: {e}")
            return False

    def _get_or_create_spreadsheet(self, client):
        """Get existing spreadsheet or create a new one."""
        try:
            return client.open(self.spreadsheet_name)
        except Exception:
            logger.info(f"[Sheets] Creating new spreadsheet: {self.spreadsheet_name}")
            return client.create(self.spreadsheet_name)

    @staticmethod
    def _get_or_create_worksheet(spreadsheet, title: str):
        """Get existing worksheet or create a new one."""
        try:
            return spreadsheet.worksheet(title)
        except Exception:
            return spreadsheet.add_worksheet(title=title, rows=1000, cols=20)

    @staticmethod
    def _deal_to_row(deal: Deal, vehicle: Vehicle) -> list:
        """Convert a Deal + Vehicle pair to a spreadsheet row."""
        location = f"{vehicle.location_city or ''}, {vehicle.location_state or ''}"
        return [
            round(deal.score, 1),
            vehicle.year,
            vehicle.make,
            vehicle.model,
            vehicle.trim or "",
            round(deal.auction_price_usd, 0),
            round(deal.total_landed_cost_brl, 0),
            round(deal.estimated_sale_price_brl, 0),
            round(deal.estimated_profit_brl, 0),
            round(deal.margin_pct, 1),
            vehicle.mileage or "",
            vehicle.title_status or "",
            location.strip(", "),
            round(deal.usd_brl_rate, 2),
            vehicle.source,
            vehicle.url,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        ]

    def sync_watchlist(
        self,
        items: list[tuple[WatchlistItem, Vehicle | None, Deal | None]],
    ) -> bool:
        """Sync watchlist items to a 'Watchlist' sheet."""
        client = self._get_client()
        if client is None:
            return False

        try:
            spreadsheet = self._get_or_create_spreadsheet(client)
            worksheet = self._get_or_create_worksheet(spreadsheet, "Watchlist")

            worksheet.clear()
            rows = [self.WATCHLIST_HEADERS]

            for item, vehicle, deal in items:
                bid = vehicle.current_bid_usd if vehicle else None
                score = deal.score if deal else None
                margin = deal.margin_pct if deal else None

                rows.append([
                    item.id,
                    item.vin or "",
                    item.make or (vehicle.make if vehicle else ""),
                    item.model or (vehicle.model if vehicle else ""),
                    item.year or (vehicle.year if vehicle else ""),
                    round(bid) if bid else "",
                    round(score, 1) if score else "",
                    round(margin, 1) if margin else "",
                    vehicle.source if vehicle else "",
                    vehicle.url if vehicle else "",
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                ])

            worksheet.update(range_name="A1", values=rows)
            self._apply_formatting(worksheet, len(rows))
            logger.info(f"[Sheets] Synced {len(items)} watchlist items")
            return True
        except Exception as e:
            logger.error(f"[Sheets] Failed to sync watchlist: {e}")
            return False

    def append_history(self, deals_with_vehicles: list[tuple[Deal, Vehicle]]) -> bool:
        """Append current deals as a snapshot to the 'Historico' sheet."""
        client = self._get_client()
        if client is None:
            return False

        try:
            spreadsheet = self._get_or_create_spreadsheet(client)
            worksheet = self._get_or_create_worksheet(spreadsheet, "Historico")

            # Add headers if sheet is empty
            existing = worksheet.get_all_values()
            if not existing:
                worksheet.update(range_name="A1", values=[self.HISTORY_HEADERS])

            today = datetime.utcnow().strftime("%Y-%m-%d")
            rows = []
            for deal, vehicle in deals_with_vehicles[:20]:
                rows.append([
                    today,
                    round(deal.score, 1),
                    vehicle.year,
                    vehicle.make,
                    vehicle.model,
                    round(deal.auction_price_usd),
                    round(deal.estimated_profit_brl),
                    round(deal.margin_pct, 1),
                    vehicle.source,
                    vehicle.url,
                ])

            if rows:
                worksheet.append_rows(rows)

            logger.info(f"[Sheets] Appended {len(rows)} deals to history")
            return True
        except Exception as e:
            logger.error(f"[Sheets] Failed to append history: {e}")
            return False

    @staticmethod
    def _apply_formatting(worksheet, num_rows: int):
        """Apply formatting with conditional colors."""
        try:
            # Bold header row
            worksheet.format("A1:Q1", {"textFormat": {"bold": True}})
            # Freeze header row
            worksheet.freeze(rows=1)

            # Conditional formatting for margin column (J = column 10)
            if num_rows > 1:
                # Green for margin >= 25%
                worksheet.format(f"J2:J{num_rows}", {
                    "numberFormat": {"type": "NUMBER", "pattern": "0.0"},
                })
        except Exception as e:
            logger.debug(f"[Sheets] Could not apply formatting: {e}")

    @staticmethod
    def _log_deals(deals_with_vehicles: list[tuple[Deal, Vehicle]]):
        """Log deals to console when Sheets is not available."""
        logger.info(f"[Sheets] {len(deals_with_vehicles)} deals ready for export:")
        for deal, vehicle in deals_with_vehicles[:5]:
            logger.info(
                f"  Score={deal.score:.0f} | {vehicle.year} {vehicle.make} {vehicle.model} | "
                f"Bid=${deal.auction_price_usd:,.0f} | Margin={deal.margin_pct:.1f}%"
            )
