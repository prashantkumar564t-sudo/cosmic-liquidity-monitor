"""0DTE Pre-Expiry Strategy Simulation & Microstructure Audit.

Simulates selling Short Strangles across delta buckets (~10, ~15, ~20, ~25 Delta)
with leg-wise Stop Loss variations (30%, 50%, 100%, No SL) using realistic
bid-ask execution friction on BTC 0DTE options.
"""

import duckdb

PARQUET_PATH = "mobile_sync/pre_expiry_20261121.parquet"


def main():
    con = duckdb.connect()

    print("=" * 110)
    print("       0DTE PRE-EXPIRY SHORT STRANGLE STRATEGY SIMULATION (2026-11-21)")
    print("=" * 110)

    # ---------------------------------------------------------
    # 1. Strict 0DTE BTC Filter & Baseline Stats
    # ---------------------------------------------------------
    overview_sql = f"""
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT strike) AS unique_strikes,
        MIN(strike) AS min_strike,
        MAX(strike) AS max_strike,
        MIN(datetime_ist) AS start_ist,
        MAX(datetime_ist) AS end_ist,
        COUNT(DISTINCT datetime_ist) AS unique_ticks
    FROM read_parquet('{PARQUET_PATH}')
    WHERE underlying = 'BTC' AND expiry_date = '2026-11-21';
    """
    ov = con.execute(overview_sql).fetchone()

    print("\n[1. 0DTE BTC DATASET BASELINE]")
    print(f"  • Total 0DTE Rows Captured : {ov[0]:,}")
    print(f"  • Unique Strikes Tracked   : {ov[1]} (Range: ${ov[2]} to ${ov[3]})")
    print(f"  • IST Time Window          : {ov[4]} -> {ov[5]} ({ov[6]} unique ticks)")

    # Compute ATM and 15-Delta stats across the 0DTE window
    stats_sql = f"""
    WITH categorized AS (
        SELECT
            delta,
            mark_price,
            best_bid,
            best_ask,
            (best_ask - best_bid) AS spread_usd,
            spread_vs_mark_pct,
            CASE
                WHEN ABS(ABS(delta) - 0.50) <= 0.05 THEN 'ATM (~0.50 Delta)'
                WHEN ABS(ABS(delta) - 0.15) <= 0.05 THEN '15-Delta OTM (~0.15 Delta)'
                ELSE NULL
            END AS delta_cat
        FROM read_parquet('{PARQUET_PATH}')
        WHERE underlying = 'BTC' AND expiry_date = '2026-11-21'
          AND best_bid IS NOT NULL AND best_ask IS NOT NULL AND best_ask >= best_bid
    )
    SELECT
        delta_cat,
        COUNT(*) AS samples,
        ROUND(AVG(mark_price), 2) AS avg_mark,
        ROUND(AVG(best_bid), 2) AS avg_bid,
        ROUND(AVG(best_ask), 2) AS avg_ask,
        ROUND(AVG(spread_usd), 2) AS avg_spread_usd,
        ROUND(AVG(spread_vs_mark_pct), 2) AS avg_spread_pct
    FROM categorized
    WHERE delta_cat IS NOT NULL
    GROUP BY delta_cat
    ORDER BY delta_cat;
    """
    stats_rows = con.execute(stats_sql).fetchall()

    print("\n[0DTE LIQUIDITY & SPREAD BASELINE]")
    print(
        f"  {'Category':<26} | {'Samples':<7} | {'Avg Mark':<9} | {'Avg Bid':<8} | {'Avg Ask':<8} | {'Avg Spread $':<12} | {'Avg Spread %'}"
    )
    print("  " + "-" * 95)
    for r in stats_rows:
        print(
            f"  {r[0]:<26} | {r[1]:<7} | ${r[2]:<8.2f} | ${r[3]:<7.2f} | ${r[4]:<7.2f} | ${r[5]:<11.2f} | {r[6]:.2f}%"
        )

    # ---------------------------------------------------------
    # 2. Extract Full Tick Time Series for Simulation
    # ---------------------------------------------------------
    query = f"""
        SELECT
            datetime_ist,
            symbol,
            strike,
            option_type,
            delta,
            mark_price,
            best_bid,
            best_ask,
            spread_vs_mark_pct
        FROM read_parquet('{PARQUET_PATH}')
        WHERE underlying = 'BTC' AND expiry_date = '2026-11-21'
        ORDER BY datetime_ist ASC;
    """
    cur = con.execute(query)
    cols = [d[0] for d in cur.description]
    all_rows = cur.fetchall()

    by_symbol: dict[str, list[dict]] = {}
    for r in all_rows:
        sym = r[1]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append({cols[i]: r[i] for i in range(len(cols))})

    # Delta pairs mapped to closest strikes at entry timestamp (16:45:08 IST)
    # Entry BTC Spot was ~$57,960.40
    pairs = [
        ("~25 Delta", "C-BTC-58200-211126", "P-BTC-57800-211126", 58200, 57800),
        ("~20 Delta", "C-BTC-58200-211126", "P-BTC-57600-211126", 58200, 57600),
        ("~15 Delta", "C-BTC-58400-211126", "P-BTC-57600-211126", 58400, 57600),
        ("~10 Delta", "C-BTC-58400-211126", "P-BTC-57400-211126", 58400, 57400),
    ]

    sl_options = [
        (0.30, "30% SL"),
        (0.50, "50% SL"),
        (1.00, "100% SL"),
        (None, "No SL"),
    ]

    results = []

    for delta_label, c_sym, p_sym, k_c, k_p in pairs:
        c_series = by_symbol[c_sym]
        p_series = by_symbol[p_sym]

        c_entry_bid = c_series[0]["best_bid"]
        p_entry_bid = p_series[0]["best_bid"]
        entry_credit = c_entry_bid + p_entry_bid

        c_entry_delta = c_series[0]["delta"]
        p_entry_delta = p_series[0]["delta"]

        for sl_val, sl_label in sl_options:
            # 1. Call Simulation
            c_sl_trigger = c_entry_bid * (1 + sl_val) if sl_val is not None else float("inf")
            c_status = f"Expired @ {c_series[-1]['datetime_ist'].strftime('%H:%M:%S')}"
            c_exit_price = c_series[-1]["best_ask"]
            c_exit_idx = len(c_series) - 1

            for i, tick in enumerate(c_series[1:], start=1):
                if tick["best_ask"] is not None and tick["best_ask"] >= c_sl_trigger:
                    c_status = f"SL Hit @ {tick['datetime_ist'].strftime('%H:%M:%S')}"
                    c_exit_price = tick["best_ask"]
                    c_exit_idx = i
                    break

            # 2. Put Simulation
            p_sl_trigger = p_entry_bid * (1 + sl_val) if sl_val is not None else float("inf")
            p_status = f"Expired @ {p_series[-1]['datetime_ist'].strftime('%H:%M:%S')}"
            p_exit_price = p_series[-1]["best_ask"]
            p_exit_idx = len(p_series) - 1

            for i, tick in enumerate(p_series[1:], start=1):
                if tick["best_ask"] is not None and tick["best_ask"] >= p_sl_trigger:
                    p_status = f"SL Hit @ {tick['datetime_ist'].strftime('%H:%M:%S')}"
                    p_exit_price = tick["best_ask"]
                    p_exit_idx = i
                    break

            # Max spread observed on either leg during active trade
            c_spreads = [
                t["spread_vs_mark_pct"] for t in c_series[: c_exit_idx + 1] if t["spread_vs_mark_pct"] is not None
            ]
            p_spreads = [
                t["spread_vs_mark_pct"] for t in p_series[: p_exit_idx + 1] if t["spread_vs_mark_pct"] is not None
            ]
            max_spread = max(c_spreads + p_spreads) if (c_spreads or p_spreads) else 0.0

            # PnL computation: Sold at best_bid, bought back at best_ask
            c_pnl = c_entry_bid - c_exit_price
            p_pnl = p_entry_bid - p_exit_price
            net_pnl = c_pnl + p_pnl
            roi_on_credit = (net_pnl / entry_credit) * 100

            results.append(
                {
                    "delta_bucket": delta_label,
                    "sl_rule": sl_label,
                    "strikes": f"{int(k_c)}C / {int(k_p)}P",
                    "entry_credit": entry_credit,
                    "call_status": c_status,
                    "put_status": p_status,
                    "net_pnl": net_pnl,
                    "roi_credit": roi_on_credit,
                    "max_spread_pct": max_spread,
                    "call_exit": c_exit_price,
                    "put_exit": p_exit_price,
                    "call_delta": c_entry_delta,
                    "put_delta": p_entry_delta,
                }
            )

    # ---------------------------------------------------------
    # 3. Output Table
    # ---------------------------------------------------------
    print("\n" + "=" * 125)
    print("                    REALISTIC 0DTE SHORT STRANGLE SIMULATION RESULTS (16:45 -> 17:26 IST)")
    print("=" * 125)
    headers = [
        "Delta Bucket",
        "SL Rule",
        "Call/Put Strikes",
        "Entry Credit",
        "Call Status (Exit $)",
        "Put Status (Exit $)",
        "Net PnL ($)",
        "ROI %",
        "Max Spread %",
    ]
    print(
        f"{headers[0]:<13} | {headers[1]:<8} | {headers[2]:<17} | {headers[3]:<12} | "
        f"{headers[4]:<23} | {headers[5]:<23} | {headers[6]:<11} | {headers[7]:<8} | {headers[8]}"
    )
    print("-" * 135)

    current_delta = None
    for r in results:
        if current_delta is not None and current_delta != r["delta_bucket"]:
            print("-" * 135)
        current_delta = r["delta_bucket"]

        c_stat_str = f"{r['call_status']} (${r['call_exit']:.1f})"
        p_stat_str = f"{r['put_status']} (${r['put_exit']:.1f})"
        pnl_str = f"${r['net_pnl']:+,.2f}"
        roi_str = f"{r['roi_credit']:+,.1f}%"
        credit_str = f"${r['entry_credit']:.2f}"
        spread_str = f"{r['max_spread_pct']:.1f}%"

        print(
            f"{r['delta_bucket']:<13} | {r['sl_rule']:<8} | {r['strikes']:<17} | {credit_str:<12} | "
            f"{c_stat_str:<23} | {p_stat_str:<23} | {pnl_str:<11} | {roi_str:<8} | {spread_str}"
        )

    print("=" * 135)


if __name__ == "__main__":
    main()
