"""Import cost calculator for US -> Brazil vehicle imports."""

from dataclasses import dataclass

from config import load_import_costs
from engine.currency import get_usd_brl_rate


@dataclass
class CostBreakdown:
    """Detailed breakdown of all import costs."""

    auction_price_usd: float
    inland_freight_usd: float
    ocean_freight_usd: float
    insurance_usd: float
    cif_usd: float  # Cost + Insurance + Freight
    cif_brl: float

    imposto_importacao_brl: float
    ipi_brl: float
    pis_cofins_brl: float
    icms_brl: float
    total_taxes_brl: float

    despachante_brl: float
    armazenagem_brl: float
    homologacao_brl: float
    registro_brl: float
    total_other_brl: float

    total_landed_cost_brl: float
    usd_brl_rate: float


class ImportCostCalculator:
    """Calculate total landed cost for importing a vehicle from US to Brazil."""

    def __init__(self):
        self.costs = load_import_costs()

    def calculate(
        self,
        auction_price_usd: float,
        engine_cc: int | None = None,
        destination_state: str = "SP",
        usd_brl_rate: float | None = None,
    ) -> CostBreakdown:
        """Calculate full import cost breakdown.

        Args:
            auction_price_usd: Winning bid / purchase price in USD.
            engine_cc: Engine displacement in cc (for IPI calculation).
            destination_state: Brazilian state code (for ICMS).
            usd_brl_rate: Override exchange rate (uses BCB API if None).
        """
        if usd_brl_rate is None:
            usd_brl_rate = get_usd_brl_rate()

        shipping = self.costs["shipping"]
        taxes = self.costs["taxes"]
        other = self.costs["other_costs"]

        # Shipping costs (USD)
        inland_freight = shipping["inland_freight_us_usd"]
        ocean_freight = shipping["ocean_freight_usd"]
        insurance = auction_price_usd * shipping["insurance_pct"]

        # CIF = Cost + Insurance + Freight
        cif_usd = auction_price_usd + inland_freight + ocean_freight + insurance
        cif_brl = cif_usd * usd_brl_rate

        # Imposto de Importacao (II)
        ii_rate = taxes["imposto_importacao_pct"]
        ii_brl = cif_brl * ii_rate

        # IPI (based on engine displacement)
        ipi_rate = self._get_ipi_rate(engine_cc, taxes["ipi"])
        ipi_base = cif_brl + ii_brl
        ipi_brl = ipi_base * ipi_rate

        # PIS/COFINS
        pis_cofins_rate = taxes["pis_cofins_pct"]
        pis_cofins_brl = (cif_brl + ii_brl) * pis_cofins_rate

        # ICMS
        icms_rates = taxes["icms"]
        icms_rate = icms_rates.get(destination_state, icms_rates["default"])
        # ICMS is calculated "por dentro" (included in its own base)
        icms_base = cif_brl + ii_brl + ipi_brl + pis_cofins_brl
        icms_brl = icms_base / (1 - icms_rate) * icms_rate

        total_taxes = ii_brl + ipi_brl + pis_cofins_brl + icms_brl

        # Other fixed costs (BRL)
        despachante = other["despachante_brl"]
        armazenagem = other["armazenagem_brl"]
        homologacao = other["homologacao_brl"]
        registro = other["registro_brl"]
        total_other = despachante + armazenagem + homologacao + registro

        total_landed = cif_brl + total_taxes + total_other

        return CostBreakdown(
            auction_price_usd=auction_price_usd,
            inland_freight_usd=inland_freight,
            ocean_freight_usd=ocean_freight,
            insurance_usd=insurance,
            cif_usd=cif_usd,
            cif_brl=cif_brl,
            imposto_importacao_brl=ii_brl,
            ipi_brl=ipi_brl,
            pis_cofins_brl=pis_cofins_brl,
            icms_brl=icms_brl,
            total_taxes_brl=total_taxes,
            despachante_brl=despachante,
            armazenagem_brl=armazenagem,
            homologacao_brl=homologacao,
            registro_brl=registro,
            total_other_brl=total_other,
            total_landed_cost_brl=total_landed,
            usd_brl_rate=usd_brl_rate,
        )

    def estimated_profit(
        self,
        auction_price_usd: float,
        br_sale_price_brl: float,
        engine_cc: int | None = None,
        destination_state: str = "SP",
    ) -> tuple[float, float]:
        """Calculate estimated profit and margin.

        Returns:
            Tuple of (profit_brl, margin_pct).
        """
        breakdown = self.calculate(auction_price_usd, engine_cc, destination_state)
        profit = br_sale_price_brl - breakdown.total_landed_cost_brl
        margin = (profit / breakdown.total_landed_cost_brl) * 100 if breakdown.total_landed_cost_brl > 0 else 0
        return profit, margin

    @staticmethod
    def _get_ipi_rate(engine_cc: int | None, ipi_table: list[dict]) -> float:
        """Get IPI rate based on engine displacement."""
        if engine_cc is None:
            # Default to highest bracket for luxury/exotic cars
            return ipi_table[-1]["rate"]
        for bracket in ipi_table:
            if engine_cc <= bracket["max_cc"]:
                return bracket["rate"]
        return ipi_table[-1]["rate"]
