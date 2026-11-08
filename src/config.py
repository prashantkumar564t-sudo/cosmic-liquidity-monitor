from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
ANALYSIS_DATA_DIR = DATA_DIR / "analysis"
LOGS_DIR = BASE_DIR / "logs"

for folder in [RAW_DATA_DIR, ANALYSIS_DATA_DIR, LOGS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Exchange & Market Config
EXCHANGE_NAME = "Cosmic"
SYMBOL = "BTCUSDT"
WS_URL = "wss://cosmic.trade/feeds"

# Polling / Sampling Config
POLL_INTERVAL_SEC = 1.0     # 1 snapshot per second save
REQUEST_TIMEOUT = 10.0

# Capital simulation tiers (USDT)
SLIPPAGE_TEST_SIZES = [1_000, 5_000, 10_000, 25_000, 50_000]
TAKER_FEE_RATE = 0.0005     # 0.05%