"""
fetch_option_chain.py
Continuous High-Frequency Option Chain & Full L2 Orderbook Liquidity Ingestion for Cosmic Trade.
Directly persists high-resolution snapshots every 5 seconds into local DuckDB (data/analysis/cosmic_options.duckdb).
"""

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = ANALYSIS_DIR / "cosmic_options.duckdb"
DEFAULT_CSV_PATH = ANALYSIS_DIR / "option_chain_20261121.csv"
DEFAULT_JSON_PATH = ANALYSIS_DIR / "option_chain_l2_20261121.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Origin": "https://cosmic.trade",
    "Accept": "application/json",
}


class DuckDBClient:
    """Unified DuckDB Client supporting native python module or C-API (Termux aarch64)."""

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self.is_native = False
        try:
            import duckdb

            self.conn = duckdb.connect(self.db_path)
            self.is_native = True
        except ImportError:
            import ctypes

            self.ctypes = ctypes

            class DuckDBResult(ctypes.Structure):
                _fields_ = [
                    ("deprecated_column_count", ctypes.c_uint64),
                    ("deprecated_row_count", ctypes.c_uint64),
                    ("deprecated_rows_changed", ctypes.c_uint64),
                    ("deprecated_columns", ctypes.c_void_p),
                    ("deprecated_error_message", ctypes.c_char_p),
                    ("internal_data", ctypes.c_void_p),
                ]

            self.DuckDBResult = DuckDBResult

            lib_path = None
            for p in [
                "/data/data/com.termux/files/usr/lib/libduckdb.so",
                "/usr/lib/libduckdb.so",
                "/usr/local/lib/libduckdb.so",
                "libduckdb.so",
            ]:
                if os.path.exists(p):
                    lib_path = p
                    break
            if not lib_path:
                lib_path = "libduckdb.so"
            self.lib = ctypes.CDLL(lib_path)

            self.lib.duckdb_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
            self.lib.duckdb_open.restype = ctypes.c_int32

            self.lib.duckdb_connect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            self.lib.duckdb_connect.restype = ctypes.c_int32

            self.lib.duckdb_disconnect.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            self.lib.duckdb_disconnect.restype = None

            self.lib.duckdb_close.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            self.lib.duckdb_close.restype = None

            self.lib.duckdb_query.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(DuckDBResult)]
            self.lib.duckdb_query.restype = ctypes.c_int32

            self.lib.duckdb_destroy_result.argtypes = [ctypes.POINTER(DuckDBResult)]
            self.lib.duckdb_destroy_result.restype = None

            self.lib.duckdb_value_int64.argtypes = [ctypes.POINTER(DuckDBResult), ctypes.c_uint64, ctypes.c_uint64]
            self.lib.duckdb_value_int64.restype = ctypes.c_int64

            self.db = ctypes.c_void_p()
            self.con = ctypes.c_void_p()
            self.lib.duckdb_open(self.db_path.encode("utf-8"), ctypes.byref(self.db))
            self.lib.duckdb_connect(self.db, ctypes.byref(self.con))

        self._create_schema()

    def _create_schema(self):
        sql = """
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            captured_at TIMESTAMP WITH TIME ZONE,
            cycle_id INTEGER,
            underlying VARCHAR,
            expiry VARCHAR,
            symbol VARCHAR,
            strike DOUBLE,
            option_type VARCHAR,
            mark_price DOUBLE,
            best_bid DOUBLE,
            best_ask DOUBLE,
            spread DOUBLE,
            top_bid_lots DOUBLE,
            top_ask_lots DOUBLE,
            bid_depth_lots DOUBLE,
            ask_depth_lots DOUBLE,
            bid_depth_usd DOUBLE,
            ask_depth_usd DOUBLE,
            iv DOUBLE,
            delta DOUBLE,
            gamma DOUBLE,
            vega DOUBLE,
            theta DOUBLE,
            l2_bid_levels INTEGER,
            l2_ask_levels INTEGER,
            l2_book_json JSON
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON option_chain_snapshots(captured_at);
        CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON option_chain_snapshots(symbol);
        CREATE INDEX IF NOT EXISTS idx_snapshots_strike ON option_chain_snapshots(strike);
        """
        self.execute(sql)

    def execute(self, sql: str):
        if self.is_native:
            return self.conn.execute(sql)
        else:
            res = self.DuckDBResult()
            self.lib.duckdb_query(self.con, sql.encode("utf-8"), self.ctypes.byref(res))
            self.lib.duckdb_destroy_result(self.ctypes.byref(res))

    def insert_records(self, records: list):
        if not records:
            return
        if self.is_native:
            insert_sql = """
                INSERT INTO option_chain_snapshots VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """
            self.conn.executemany(insert_sql, records)
        else:

            def esc(val):
                if val is None:
                    return "NULL"
                if isinstance(val, (int, float)):
                    return str(val)
                if isinstance(val, datetime):
                    return f"TIMESTAMPTZ '{val.isoformat()}'"
                clean = str(val).replace("'", "''")
                return f"'{clean}'"

            row_strs = ["(" + ", ".join(esc(c) for c in r) + ")" for r in records]
            sql = "INSERT INTO option_chain_snapshots VALUES " + ", ".join(row_strs) + ";"
            self.execute(sql)

    def get_total_rows(self) -> int:
        if self.is_native:
            return self.conn.execute("SELECT COUNT(*) FROM option_chain_snapshots").fetchone()[0]
        else:
            try:
                res = self.DuckDBResult()
                if (
                    self.lib.duckdb_query(
                        self.con, b"SELECT COUNT(*) FROM option_chain_snapshots;", self.ctypes.byref(res)
                    )
                    == 0
                ):
                    count = self.lib.duckdb_value_int64(self.ctypes.byref(res), 0, 0)
                    self.lib.duckdb_destroy_result(self.ctypes.byref(res))
                    return int(count)
                return 0
            except Exception:
                return 0

    def export_parquet(self, output_path: Path) -> tuple[int, int]:
        """Exports option_chain_snapshots table to Parquet using DuckDB ZSTD compression.

        Returns (total_rows, file_size_bytes).
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out_str = str(output_path.resolve()).replace("\\", "/")

        try:
            self.execute("CHECKPOINT;")
        except Exception:
            pass

        self.execute(f"COPY option_chain_snapshots TO '{out_str}' (FORMAT PARQUET, COMPRESSION ZSTD);")
        file_size = output_path.stat().st_size if output_path.exists() else 0
        total_rows = self.get_total_rows()
        return total_rows, file_size

    def close(self):
        try:
            self.execute("CHECKPOINT;")
        except Exception:
            pass
        if self.is_native:
            self.conn.close()
        else:
            self.lib.duckdb_disconnect(self.ctypes.byref(self.con))
            self.lib.duckdb_close(self.ctypes.byref(self.db))


def http_get_json(url: str, timeout: float = 6.0, retries: int = 2) -> dict | list | None:
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(0.3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
                continue
            return None
    return None


def fetch_l2_book(symbol: str) -> dict:
    url = f"https://cosmic.trade/api/v1/orderbook/{symbol}"
    data = http_get_json(url, timeout=5.0)
    if not data or not isinstance(data, dict):
        return {"symbol": symbol, "bids": [], "asks": [], "sequence": 0}
    return data


def parse_orderbook_liquidity(book: dict, contract_size: float = 0.001) -> dict:
    raw_bids = book.get("bids", [])
    raw_asks = book.get("asks", [])

    bids = []
    for item in raw_bids:
        try:
            bids.append((float(item["price"]), float(item["qty"])))
        except (KeyError, ValueError, TypeError):
            continue

    asks = []
    for item in raw_asks:
        try:
            asks.append((float(item["price"]), float(item["qty"])))
        except (KeyError, ValueError, TypeError):
            continue

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    spread = round(best_ask - best_bid, 4) if (best_bid is not None and best_ask is not None) else None

    lot_multiplier = 1.0 / contract_size if contract_size > 0 else 1000.0

    top_bid_lots = round(bids[0][1] * lot_multiplier, 2) if bids else 0.0
    top_ask_lots = round(asks[0][1] * lot_multiplier, 2) if asks else 0.0

    total_bid_qty = sum(q for _, q in bids)
    total_ask_qty = sum(q for _, q in asks)
    bid_depth_lots = round(total_bid_qty * lot_multiplier, 2)
    ask_depth_lots = round(total_ask_qty * lot_multiplier, 2)

    bid_depth_usd = round(sum(p * q for p, q in bids), 2)
    ask_depth_usd = round(sum(p * q for p, q in asks), 2)

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "top_bid_lots": top_bid_lots,
        "top_ask_lots": top_ask_lots,
        "bid_depth_lots": bid_depth_lots,
        "ask_depth_lots": ask_depth_lots,
        "bid_depth_usd": bid_depth_usd,
        "ask_depth_usd": ask_depth_usd,
        "l2_bid_levels": len(bids),
        "l2_ask_levels": len(asks),
    }


def parse_expiry_dt(raw_ts) -> datetime | None:
    """Robust parser for diverse expiry timestamps: ISO strings, epoch timestamps, or formatted dates."""
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
        if val > 1e11:  # Milliseconds
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)

    ts_str = str(raw_ts).strip()
    if not ts_str:
        return None

    try:
        clean_ts = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d%b%y", "%d%b%Y"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def resolve_target_expiry(expiries: list, target_date_str: str | None) -> dict | None:
    """Select requested target expiry, or auto-detect nearest active future expiry."""
    if not expiries:
        return None

    if target_date_str and target_date_str.strip().lower() != "auto":
        tgt = target_date_str.strip().upper()
        # 1. Direct substring match against raw expiry timestamp
        for exp in expiries:
            ts_str = str(exp.get("expiry_ts", "")).upper()
            if tgt in ts_str:
                return exp
        # 2. Date-based match via parse_expiry_dt
        for exp in expiries:
            dt = parse_expiry_dt(exp.get("expiry_ts"))
            if dt:
                if (
                    tgt in dt.strftime("%Y-%m-%d")
                    or tgt in dt.strftime("%d%b%y").upper()
                    or tgt in dt.strftime("%d%b%Y").upper()
                ):
                    return exp

    now_utc = datetime.now(timezone.utc)
    future_expiries = []
    for exp in expiries:
        dt = parse_expiry_dt(exp.get("expiry_ts"))
        if dt and dt > now_utc:
            future_expiries.append((dt, exp))

    if future_expiries:
        future_expiries.sort(key=lambda x: x[0])
        return future_expiries[0][1]

    # Fallback to first available expiry
    return expiries[0] if expiries else None


def run_collection_cycle(
    conn: DuckDBClient,
    cycle_id: int,
    underlying: str = "BTC",
    target_expiry_str: str | None = None,
    workers: int = 12,
    save_csv: bool = False,
    save_json: bool = False,
) -> tuple[int, str, float, str]:
    t0 = time.perf_counter()
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    norm_underlying = underlying.strip().upper()

    # 1. Fetch chain metadata
    chain_url = f"https://cosmic.trade/api/v1/options/chain?underlying={norm_underlying}"
    raw_chain = http_get_json(chain_url, timeout=5.0)
    if not raw_chain or not isinstance(raw_chain, dict):
        return 0, "N/A", 0.0, "Failed to fetch option chain metadata"

    expiries = raw_chain.get("expiries")
    if expiries is None or not isinstance(expiries, list):
        return 0, "N/A", 0.0, "Invalid expiries format in API response"

    if len(expiries) == 0:
        return 0, "N/A", 0.0, f"No active option expiries listed on exchange for {norm_underlying}"

    matched_exp = resolve_target_expiry(expiries, target_expiry_str)
    if not matched_exp:
        return 0, "N/A", 0.0, "No matching active expiry found"

    expiry_ts = str(matched_exp.get("expiry_ts", ""))
    strikes_data = matched_exp.get("strikes", [])
    if not strikes_data:
        return 0, expiry_ts, 0.0, "No strike data in matched expiry"

    # 2. Prepare contracts metadata
    symbols_to_fetch = []
    contract_meta = {}

    for item in strikes_data:
        try:
            strike_val = float(item["strike"])
        except (KeyError, ValueError, TypeError):
            continue
        for side_key in ["call", "put"]:
            contract = item.get(side_key)
            if not contract or not isinstance(contract, dict):
                continue
            symbol = contract.get("symbol")
            if not symbol:
                continue
            symbols_to_fetch.append(symbol)
            try:
                contract_meta[symbol] = {
                    "strike": strike_val,
                    "side": side_key.upper(),
                    "symbol": symbol,
                    "mark": float(contract.get("mark", 0.0) or 0.0),
                    "iv": float(contract.get("iv", 0.0) or 0.0),
                    "delta": float(contract.get("delta", 0.0) or 0.0),
                    "gamma": float(contract.get("gamma", 0.0) or 0.0),
                    "vega": float(contract.get("vega", 0.0) or 0.0),
                    "theta": float(contract.get("theta", 0.0) or 0.0),
                    "contract_size": float(contract.get("contract_size", 0.001) or 0.001),
                }
            except (ValueError, TypeError):
                continue

    # 3. Concurrent L2 orderbook fetch
    l2_books = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_l2_book, sym): sym for sym in symbols_to_fetch}
        for future, sym in futures.items():
            try:
                l2_books[sym] = future.result()
            except Exception:
                l2_books[sym] = {"symbol": sym, "bids": [], "asks": []}

    # 4. Build records for insertion
    records = []
    csv_rows = []
    atm_summary = ""
    min_delta_diff = float("inf")

    for sym in symbols_to_fetch:
        meta = contract_meta[sym]
        book = l2_books.get(sym, {})
        liq = parse_orderbook_liquidity(book, contract_size=meta["contract_size"])
        book_json_str = json.dumps(book)

        record = (
            now_utc,
            cycle_id,
            norm_underlying,
            expiry_ts,
            sym,
            meta["strike"],
            meta["side"],
            meta["mark"],
            liq["best_bid"],
            liq["best_ask"],
            liq["spread"],
            liq["top_bid_lots"],
            liq["top_ask_lots"],
            liq["bid_depth_lots"],
            liq["ask_depth_lots"],
            liq["bid_depth_usd"],
            liq["ask_depth_usd"],
            meta["iv"],
            meta["delta"],
            meta["gamma"],
            meta["vega"],
            meta["theta"],
            liq["l2_bid_levels"],
            liq["l2_ask_levels"],
            book_json_str,
        )
        records.append(record)

        if save_csv:
            csv_rows.append(
                {
                    "timestamp": now_iso,
                    "underlying": norm_underlying,
                    "expiry": expiry_ts,
                    "strike": meta["strike"],
                    "side": meta["side"],
                    "symbol": sym,
                    "mark_price": meta["mark"],
                    "best_bid": liq["best_bid"],
                    "best_ask": liq["best_ask"],
                    "spread": liq["spread"],
                    "bid_depth_lots": liq["bid_depth_lots"],
                    "ask_depth_lots": liq["ask_depth_lots"],
                    "bid_depth_usd": liq["bid_depth_usd"],
                    "ask_depth_usd": liq["ask_depth_usd"],
                    "iv": meta["iv"],
                    "delta": meta["delta"],
                }
            )

        # Track ATM Call
        if meta["side"] == "CALL":
            diff = abs(meta["delta"] - 0.5)
            if diff < min_delta_diff:
                min_delta_diff = diff
                atm_summary = f"${meta['strike']:,.0f} (Mark: ${meta['mark']:.1f})"

    # 5. Insert directly into DuckDB
    conn.insert_records(records)

    # 6. Optional CSV / JSON snapshots
    if save_csv and csv_rows:
        with open(DEFAULT_CSV_PATH, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    if save_json:
        with open(DEFAULT_JSON_PATH, mode="w", encoding="utf-8") as f:
            json.dump(
                {
                    "underlying": norm_underlying,
                    "expiry": expiry_ts,
                    "captured_at_utc": now_iso,
                    "contracts_count": len(l2_books),
                    "books": l2_books,
                },
                f,
                indent=2,
            )

    duration = time.perf_counter() - t0
    return len(records), expiry_ts, duration, atm_summary


def parse_stop_at(stop_at_str: str | None) -> datetime | None:
    """Parse a stop-at time string (e.g. '17:15', '17:15:00', '2026-11-21 17:15:00') into local datetime."""
    if not stop_at_str:
        return None
    stop_at_str = stop_at_str.strip()
    now_local = datetime.now()
    # Try HH:MM or HH:MM:SS
    for fmt in ["%H:%M", "%H:%M:%S"]:
        try:
            t = datetime.strptime(stop_at_str, fmt).time()
            return datetime.combine(now_local.date(), t)
        except ValueError:
            pass
    # Try full datetime formats
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(stop_at_str, fmt)
        except ValueError:
            pass
    print(f"[!] Warning: Could not parse --stop-at '{stop_at_str}'. Ignoring stop time.")
    return None


def start_option_chain_daemon(
    interval: float = 5.0,
    max_cycles: int | None = None,
    stop_at_dt: datetime | None = None,
    duration_seconds: float | None = None,
    duration_minutes: float | None = None,
    export_parquet: str | None = None,
    underlying: str | tuple[str, ...] = ("BTC", "ETH"),
    target_expiry: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    workers: int = 12,
    save_csv: bool = True,
    save_json: bool = True,
):
    underlyings_list = (
        tuple(u.strip().upper() for u in underlying.split(",") if u.strip())
        if isinstance(underlying, str)
        else tuple(underlying)
    )

    if duration_minutes is not None and duration_minutes > 0:
        duration_seconds = duration_minutes * 60.0

    stop_info = "UNTIL STOPPED (Ctrl+C)"
    if stop_at_dt:
        stop_info = f"STOP AT {stop_at_dt.strftime('%H:%M:%S')} Local"
    elif duration_seconds:
        mins = duration_seconds / 60.0
        stop_info = f"DURATION {mins:.1f}m ({duration_seconds:.0f}s)"
    elif max_cycles:
        stop_info = f"MAX {max_cycles} CYCLES"

    print(f"\n{'=' * 85}")
    print("[*] Cosmic Trade Live Option Chain & DuckDB Ingestion Daemon")
    print(
        f"[*] Underlyings: {', '.join(underlyings_list)} | Interval: {interval:.1f}s | Target Expiry: {target_expiry or 'AUTO-ROLL'}"
    )
    print(f"[*] Schedule: {stop_info} | DuckDB Target: {db_path}")
    print(f"[*] Started at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'=' * 85}\n")

    conn = DuckDBClient(db_path)
    cycle = 1
    total_inserted = 0
    start_epoch = time.time()
    end_epoch = start_epoch + duration_seconds if duration_seconds else None

    try:
        while True:
            # Check stop conditions before or at start of cycle
            now_local = datetime.now()
            if stop_at_dt and now_local >= stop_at_dt:
                print(f"\n[+] Scheduled stop time reached ({stop_at_dt.strftime('%H:%M:%S')}). Exiting gracefully.")
                break
            if end_epoch and time.time() >= end_epoch:
                print(
                    f"\n[+] Scheduled duration reached ({duration_seconds:.0f}s / {duration_seconds / 60:.1f}m). Exiting gracefully."
                )
                break

            cycle_start = time.perf_counter()
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            cycle_reports = []
            for und in underlyings_list:
                count, expiry, duration, atm_info = run_collection_cycle(
                    conn=conn,
                    cycle_id=cycle,
                    underlying=und,
                    target_expiry_str=target_expiry,
                    workers=workers,
                    save_csv=save_csv,
                    save_json=save_json,
                )
                total_inserted += count
                if count > 0:
                    cycle_reports.append(f"{und}: {count} contracts (Exp: {expiry}, {atm_info})")
                else:
                    cycle_reports.append(f"{und}: 0 contracts ({atm_info})")

            db_total = conn.get_total_rows()
            report_line = " | ".join(cycle_reports)
            print(f"[Cycle {cycle:04d} | {now_str} UTC] {report_line} | DB Rows: {db_total:,}")

            if max_cycles and cycle >= max_cycles:
                print(f"\n[+] Completed requested {max_cycles} cycles.")
                break

            cycle += 1
            elapsed = time.perf_counter() - cycle_start
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n[!] Daemon stopped by user (Ctrl+C).")
    finally:
        # Check if Parquet export should run
        # Triggered if export_parquet is passed OR if timed run (duration_seconds > 0)
        should_export = False
        target_parquet_path = None

        if export_parquet and export_parquet.lower() not in ("none", "false", "0"):
            should_export = True
            if export_parquet.lower() != "auto":
                target_parquet_path = Path(export_parquet)
        elif (duration_seconds and duration_seconds > 0) and (
            export_parquet is None or export_parquet.lower() != "none"
        ):
            should_export = True

        if should_export:
            if target_parquet_path is None:
                ist_tz = timezone(timedelta(hours=5, minutes=30))
                ist_str = datetime.now(ist_tz).strftime("%Y%m%d_%H%M_IST")
                export_dir = BASE_DIR / "exports"
                export_dir.mkdir(parents=True, exist_ok=True)
                target_parquet_path = export_dir / f"cosmic_evening_{ist_str}.parquet"
            else:
                target_parquet_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                rows, fsize = conn.export_parquet(target_parquet_path)
                fsize_mb = fsize / (1024 * 1024)
                print(f"\n{'=' * 85}")
                print("[+] Parquet Export Generated (DuckDB ZSTD Compressed):")
                print(f"    Path: {target_parquet_path}")
                print(f"    Total Rows: {rows:,}")
                print(f"    File Size: {fsize_mb:.2f} MB ({fsize:,} bytes)")
                print(f"{'=' * 85}\n")
            except Exception as e:
                print(f"[!] Error exporting Parquet: {e}")

        conn.close()
        print(f"[+] DuckDB connection safely closed. Total records inserted this session: {total_inserted:,}\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cosmic Trade continuous Option Chain & L2 Liquidity DuckDB Ingestion."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling loop interval in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Maximum cycles to run before exiting (default: None, infinite continuous mode)",
    )
    parser.add_argument(
        "--duration-minutes",
        type=int,
        default=0,
        help="Run duration in minutes for timed cloud runs (default: 0 = manual/infinite)",
    )
    parser.add_argument(
        "--stop-at",
        type=str,
        default=None,
        help="Local time to stop collection (e.g. '17:15' or '17:15:00')",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Run duration in seconds before auto-stopping",
    )
    parser.add_argument(
        "--export-parquet",
        type=str,
        default=None,
        nargs="?",
        const="auto",
        help="Export snapshots to Parquet with ZSTD compression upon exit. Optional path or auto (default: ./exports/cosmic_evening_YYYYMMDD_HHMM_IST.parquet)",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="BTC,ETH",
        help="Comma-separated underlying assets (default: BTC,ETH)",
    )
    parser.add_argument(
        "--expiry",
        type=str,
        default="auto",
        help="Target expiry date (e.g. '2026-11-21' or 'auto' for nearest active)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help=f"Path to DuckDB database file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="Concurrent worker threads for L2 orderbook fetching (default: 12)",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Disable overwriting latest CSV snapshot",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Disable overwriting latest L2 JSON snapshot",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    stop_dt = parse_stop_at(args.stop_at)
    duration_mins = args.duration_minutes if args.duration_minutes > 0 else None
    start_option_chain_daemon(
        interval=args.interval,
        max_cycles=args.max_cycles,
        stop_at_dt=stop_dt,
        duration_seconds=args.duration,
        duration_minutes=duration_mins,
        export_parquet=args.export_parquet,
        underlying=args.underlying.upper(),
        target_expiry=args.expiry if args.expiry != "auto" else None,
        db_path=Path(args.db_path),
        workers=args.workers,
        save_csv=not args.no_csv,
        save_json=not args.no_json,
    )
