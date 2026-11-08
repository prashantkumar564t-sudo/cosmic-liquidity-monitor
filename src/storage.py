import csv
import json
from pathlib import Path
from src.config import RAW_DATA_DIR, ANALYSIS_DATA_DIR

class StorageManager:
    def __init__(self):
        self.metrics_file = ANALYSIS_DATA_DIR / "cosmic_btc_metrics.csv"
        self._init_csv()

    def _init_csv(self):
        if not self.metrics_file.exists():
            headers = [
                "timestamp", "mid_price", "spread", "spread_pct",
                "top3_bid_lots", "top3_ask_lots",
                "total_bid_lots", "total_ask_lots",
                "imbalance", "slippage_10k_pct", "slippage_50k_pct",
                "ram_usage_mb"
            ]
            with open(self.metrics_file, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

    def write_snapshot(self, timestamp: float, raw_book: dict, metrics: dict, sim_10k: dict, sim_50k: dict, ram_mb: float):
        # 1. Raw L2 Snapshot (JSON Sink)
        raw_file = RAW_DATA_DIR / f"l2_{int(timestamp * 1000)}.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(raw_book, f)

        # 2. Refined Metrics CSV Sink
        row = [
            round(timestamp, 3),
            metrics.get("mid_price", 0),
            metrics.get("spread", 0),
            metrics.get("spread_pct", 0),
            metrics.get("top3_bid_lots", 0),
            metrics.get("top3_ask_lots", 0),
            metrics.get("total_bid_lots", 0),
            metrics.get("total_ask_lots", 0),
            metrics.get("imbalance", 0),
            sim_10k.get("slippage_pct", 0),
            sim_50k.get("slippage_pct", 0),
            ram_mb
        ]
        with open(self.metrics_file, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)