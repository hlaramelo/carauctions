import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent


def load_settings() -> dict:
    """Load main settings from settings.yaml."""
    with open(CONFIG_DIR / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def load_import_costs() -> dict:
    """Load import cost parameters from import_costs.yaml."""
    with open(CONFIG_DIR / "import_costs.yaml", "r") as f:
        return yaml.safe_load(f)
