import csv
from pathlib import Path

METRICS_FILE = Path(__file__).resolve().parent / "data" / "analysis" / "cosmic_btc_metrics.csv"

def generate_telemetry_report():
    if not METRICS_FILE.exists():
        print(f"[!] Error: {METRICS_FILE} not found. Run the monitor first.")
        return

    with open(METRICS_FILE, mode="r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if not reader:
        print("[!] No data rows found in CSV.")
        return

    total_snapshots = len(reader)
    mid_prices = [float(r["mid_price"]) for r in reader]
    spreads = [float(r["spread"]) for r in reader]
    imbalances = [float(r["imbalance"]) for r in reader]
    slip_10k = [float(r["slippage_10k_pct"]) for r in reader]
    slip_50k = [float(r["slippage_50k_pct"]) for r in reader]
    rams = [float(r["ram_usage_mb"]) for r in reader if float(r.get("ram_usage_mb", 0)) > 0]
    top3_bids = [float(r["top3_bid_lots"]) for r in reader]
    top3_asks = [float(r["top3_ask_lots"]) for r in reader]

    price_min = min(mid_prices)
    price_max = max(mid_prices)
    price_drift = round(price_max - price_min, 2)
    avg_spread = round(sum(spreads) / total_snapshots, 2)
    avg_slip_50k = round(sum(slip_50k) / total_snapshots, 6)
    max_slip_50k = round(max(slip_50k), 6)
    
    ram_min = min(rams) if rams else 0.0
    ram_max = max(rams) if rams else 0.0
    ram_avg = round(sum(rams) / len(rams), 2) if rams else 0.0

    print("\n" + "=" * 65)
    print("       COSMIC BTC TELEMETRY & MEMORY BENCHMARK REPORT        ")
    print("=" * 65)
    print(f"Total Snapshots Captured : {total_snapshots} (at 1 req/sec)")
    print(f"Duration                 : {round(total_snapshots / 60, 2)} Minutes")
    print("-" * 65)
    print(f"Mid Price Range          : ${price_min} -> ${price_max} (Drift: ${price_drift})")
    print(f"Average Spread           : ${avg_spread}")
    print(f"Average Top-3 Depth      : Bids: {round(sum(top3_bids)/total_snapshots, 1)} Lots | Asks: {round(sum(top3_asks)/total_snapshots, 1)} Lots")
    print(f"Order Flow Imbalance     : Min: {min(imbalances):+.4f} | Max: {max(imbalances):+.4f}")
    print(f"50k Slippage (Ladder)    : Avg: {avg_slip_50k}% | Worst-case: {max_slip_50k}%")
    print("-" * 65)
    print(f"PROCESS RAM FOOTPRINT    : Min: {ram_min} MB | Max: {ram_max} MB | Avg: {ram_avg} MB")
    if ram_max - ram_min < 5.0:
        print("Memory Stability Status  : [HEALTHY] Zero Memory Leak Detected (<5MB variation)")
    else:
        print("Memory Stability Status  : [WARNING] Memory creeping up, check snapshot buffer")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    generate_telemetry_report()