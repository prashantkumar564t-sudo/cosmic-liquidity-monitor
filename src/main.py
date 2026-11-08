import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import time
import os
from datetime import datetime
from src.config import POLL_INTERVAL_SEC
from src.cosmic_api import CosmicWSClient
from src.orderbook import OrderBookEvaluator
from src.slippage import ExecutionSimulator
from src.storage import StorageManager
from monitor.ram_monitor import get_process_memory_mb

def prevent_sleep():
    """Windows sleep lock. Termux uses termux-wake-lock from shell."""
    if os.name == 'nt':
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception:
            pass

def restore_sleep():
    if os.name == 'nt':
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass

def run_monitor(max_snapshots: int = 43200):
    prevent_sleep()
    client = CosmicWSClient()
    storage = StorageManager()

    hours = round(max_snapshots / 3600, 1)
    print(f"\n{'='*65}")
    print(f"[*] Starting Cosmic BTC Liquidity Monitor")
    print(f"[*] Target: {max_snapshots} snapshots (~{hours} Hours)")
    print(f"[*] Platform: {sys.platform} | Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")

    client.start()
    time.sleep(2)

    snapshots_taken = 0
    start_time = time.time()

    try:
        while snapshots_taken < max_snapshots:
            start_loop = time.perf_counter()
            now_ts = time.time()

            book = client.get_latest_orderbook()
            if book["success"]:
                metrics = OrderBookEvaluator.compute_metrics(book["bids"], book["asks"])
                sim_10k = ExecutionSimulator.simulate_market_buy(book["asks"], 10_000)
                sim_50k = ExecutionSimulator.simulate_market_buy(book["asks"], 50_000)
                ram_mb = get_process_memory_mb()

                storage.write_snapshot(now_ts, book, metrics, sim_10k, sim_50k, ram_mb)
                snapshots_taken += 1

                # Throttled console output (every 10 snapshots or during spikes)
                is_spike = abs(metrics.get('imbalance', 0)) > 0.15 or metrics.get('spread', 0) > 3.0
                if snapshots_taken % 10 == 0 or is_spike:
                    elapsed_min = round((time.time() - start_time) / 60, 1)
                    tag = " [SPIKE DETECTED] " if is_spike else ""
                    mid = metrics['mid_price']
                    sp = metrics['spread']
                    b_lots = metrics['top3_bid_lots']
                    a_lots = metrics['top3_ask_lots']
                    imb = metrics['imbalance']
                    print(f"[{snapshots_taken:05d}/{max_snapshots} - {elapsed_min}m] Mid: ${mid} | Spread: ${sp} | Top3 Lots (B/A): {b_lots}/{a_lots} | Imb: {imb:+.4f} | RAM: {ram_mb} MB{tag}")
            else:
                print("[*] Syncing WebSocket feed...")

            elapsed = time.perf_counter() - start_loop
            sleep_time = max(0.0, POLL_INTERVAL_SEC - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n[!] Process stopped by user.")
    finally:
        client.stop()
        restore_sleep()
        print(f"\n[+] Finished. Total snapshots recorded: {snapshots_taken}")

if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 43200
    run_monitor(max_snapshots=count)