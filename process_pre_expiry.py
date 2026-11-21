import os
import time

import duckdb

csv_gz_path = "mobile_sync/pre_expiry_20261121.csv.gz"
parquet_path = "mobile_sync/pre_expiry_20261121.parquet"

columns = [
    "timestamp_us",
    "datetime_ist",
    "underlying",
    "symbol",
    "expiry_date",
    "expiry_bucket",
    "option_type",
    "strike",
    "delta",
    "delta_band",
    "iv",
    "spot_price",
    "spot_1m_change_pct",
    "mark_price",
    "ltp",
    "best_bid",
    "best_ask",
    "bid_size",
    "ask_size",
    "volume_24h",
    "oi",
    "spread_vs_mark_pct",
    "bid_slippage_pct",
    "ltp_mark_spike_pct",
    "quote_changed",
]

print(f"Reading {csv_gz_path} into DuckDB with exact schema and writing to {parquet_path}...")
t0 = time.time()

con = duckdb.connect()

col_names_str = ", ".join(f"'{c}'" for c in columns)

# Convert to parquet with explicit columns
con.execute(f"""
    COPY (
        SELECT * FROM read_csv('{csv_gz_path}', names=[{col_names_str}], header=false)
    ) TO '{parquet_path}' (FORMAT PARQUET, COMPRESSION ZSTD);
""")

parquet_size_mb = os.path.getsize(parquet_path) / (1024 * 1024)
print(f"Parquet generated in {round(time.time() - t0, 2)}s, size: {parquet_size_mb:.2f} MB")

print("\n" + "=" * 95)
print("     DELTA OPTIONS MICROSTRUCTURE: PRE-EXPIRY WINDOW AUDIT (2026-11-21)")
print("=" * 95)

# Overall summary
summary_sql = f"""
SELECT
    MIN(datetime_ist) AS ist_start,
    MAX(datetime_ist) AS ist_end,
    COUNT(*) AS total_rows,
    COUNT(DISTINCT underlying) AS unique_underlyings,
    COUNT(DISTINCT symbol) AS unique_symbols,
    COUNT(DISTINCT strike) AS unique_strikes,
    MIN(strike) AS min_strike,
    MAX(strike) AS max_strike
FROM read_parquet('{parquet_path}');
"""
res = con.execute(summary_sql).fetchone()

print("\n[WINDOW OVERVIEW]")
print(f"  • IST Start Time       : {res[0]}")
print(f"  • IST End Time         : {res[1]}")
print(f"  • Total Rows Captured  : {res[2]:,}")
print(f"  • Unique Underlyings   : {res[3]}")
print(f"  • Unique Symbols       : {res[4]}")
print(f"  • Unique Strikes       : {res[5]} (Range: ${res[6]} - ${res[7]})")


# Helper function to print tables cleanly
def print_query(title, sql):
    print(f"\n[{title}]")
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    # Compute column widths
    widths = [len(c) for c in cols]
    str_rows = []
    for r in rows:
        row_strs = [str(x) if x is not None else "" for x in r]
        str_rows.append(row_strs)
        for i, val in enumerate(row_strs):
            widths[i] = max(widths[i], len(val))
    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    sep = "-+-".join("-" * widths[i] for i in range(len(cols)))
    print(header)
    print(sep)
    for sr in str_rows:
        print(" | ".join(sr[i].ljust(widths[i]) for i in range(len(sr))))


# Breakdown by Underlying
underlying_sql = f"""
SELECT
    underlying,
    COUNT(*) AS rows,
    COUNT(DISTINCT symbol) AS symbols,
    COUNT(DISTINCT strike) AS strikes,
    ROUND(MIN(spot_price), 2) AS min_spot,
    ROUND(MAX(spot_price), 2) AS max_spot
FROM read_parquet('{parquet_path}')
GROUP BY underlying
ORDER BY rows DESC;
"""
print_query("BREAKDOWN BY UNDERLYING", underlying_sql)

# Breakdown by delta_band / delta ranges
delta_bands_sql = f"""
SELECT
    delta_band,
    COUNT(*) AS count,
    ROUND(AVG(best_ask - best_bid), 4) AS avg_spread_usd,
    ROUND(AVG(spread_vs_mark_pct), 4) AS avg_spread_pct,
    ROUND(AVG(mark_price), 2) AS avg_mark,
    ROUND(AVG(bid_size), 2) AS avg_bid_size,
    ROUND(AVG(ask_size), 2) AS avg_ask_size
FROM read_parquet('{parquet_path}')
WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL AND best_ask >= best_bid
GROUP BY delta_band
ORDER BY avg_spread_usd DESC;
"""
print_query("DELTA BANDS & BID-ASK SPREAD SUMMARY", delta_bands_sql)

# ATM vs 15-Delta OTM specific analysis
atm_15delta_sql = f"""
WITH categorized AS (
    SELECT
        underlying,
        symbol,
        strike,
        option_type,
        delta,
        mark_price,
        best_bid,
        best_ask,
        (best_ask - best_bid) AS spread_usd,
        spread_vs_mark_pct,
        CASE
            WHEN ABS(ABS(delta) - 0.50) <= 0.05 THEN 'ATM (0.45 - 0.55 Delta)'
            WHEN ABS(ABS(delta) - 0.15) <= 0.05 THEN '15-Delta OTM (0.10 - 0.20 Delta)'
            ELSE 'Other'
        END AS category
    FROM read_parquet('{parquet_path}')
    WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL AND best_ask >= best_bid
)
SELECT
    category,
    underlying,
    COUNT(*) AS row_count,
    ROUND(AVG(mark_price), 2) AS avg_mark,
    ROUND(AVG(spread_usd), 4) AS avg_spread_usd,
    ROUND(AVG(spread_vs_mark_pct), 4) AS avg_spread_pct,
    ROUND(MIN(spread_usd), 4) AS min_spread_usd,
    ROUND(MAX(spread_usd), 4) AS max_spread_usd
FROM categorized
WHERE category IN ('ATM (0.45 - 0.55 Delta)', '15-Delta OTM (0.10 - 0.20 Delta)')
GROUP BY category, underlying
ORDER BY category, underlying;
"""
print_query("TARGET SPREAD ANALYSIS: ATM vs 15-DELTA OTM BY UNDERLYING", atm_15delta_sql)

# Overall weighted average for ATM and 15-Delta OTM across the window
overall_atm_15_sql = f"""
WITH categorized AS (
    SELECT
        (best_ask - best_bid) AS spread_usd,
        spread_vs_mark_pct,
        CASE
            WHEN ABS(ABS(delta) - 0.50) <= 0.05 THEN 'ATM (0.45 - 0.55 Delta)'
            WHEN ABS(ABS(delta) - 0.15) <= 0.05 THEN '15-Delta OTM (0.10 - 0.20 Delta)'
            ELSE 'Other'
        END AS category
    FROM read_parquet('{parquet_path}')
    WHERE best_ask IS NOT NULL AND best_bid IS NOT NULL AND best_ask >= best_bid
)
SELECT
    category,
    COUNT(*) AS total_samples,
    ROUND(AVG(spread_usd), 4) AS avg_spread_usd,
    ROUND(AVG(spread_vs_mark_pct), 4) AS avg_spread_pct
FROM categorized
WHERE category IN ('ATM (0.45 - 0.55 Delta)', '15-Delta OTM (0.10 - 0.20 Delta)')
GROUP BY category
ORDER BY category;
"""
print_query("OVERALL COMBINED ATM vs 15-DELTA OTM SPREAD", overall_atm_15_sql)
