from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MARKET_DATA_DATABASE = PROJECT_ROOT / "data" / "prices.db"
