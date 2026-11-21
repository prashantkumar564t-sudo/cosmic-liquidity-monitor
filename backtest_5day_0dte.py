"""5-Day Multi-Entry 0DTE BTC Iron Condor Backtest (2026-11-17 to 2026-11-21).

Simulates defined-risk Iron Condors ($400 hedge wings) across all 5 recorded expiries
comparing 3 entry windows (14:30 IST, 15:30 IST, and 16:30/16:45 IST) held to settlement.
Includes exact Delta Exchange India options fees (0.03% spot capped at 5% premium) + 18% GST.
"""

from collections import defaultdict

import duckdb

PARQUET_PATH = "mobile_sync/btc_0dte_5day_pre_expiry.parquet"
WING_WIDTH = 400.0


def calc_leg_fee_with_gst(spot: float, premium: float) -> float:
    """Calculate Delta Exchange India options taker fee + 18% GST per leg.

    Fee formula: min(0.0003 * Spot, 0.05 * Premium) * 1.18.
    """
    if premium <= 0:
        return 0.0
    base_fee = min(0.0003 * spot, 0.05 * premium)
    return base_fee * 1.18


def run_condor_trade(
    con: duckdb.DuckDBPyConnection,
    date_str: str,
    target_entry_str: str,
    target_delta: float,
    wing_width: float = WING_WIDTH,
) -> dict | None:
    """Simulate a single Iron Condor trade on a given date and entry time."""
    # Find ticks for this date
    ticks = con.execute(f"""
        SELECT datetime_ist, spot_price
        FROM read_parquet('{PARQUET_PATH}')
        WHERE CAST(datetime_ist AS DATE) = '{date_str}'
        GROUP BY datetime_ist, spot_price
        ORDER BY datetime_ist ASC;
    """).fetchall()

    if not ticks:
        return None

    # Check if target entry time is reachable
    # Target entry format: 'HH:MM'
    matching_ticks = [t for t in ticks if t[0].strftime("%H:%M") >= target_entry_str]
    if not matching_ticks:
        return None

    entry_tick = matching_ticks[0]
    entry_ts = entry_tick[0]
    entry_spot = entry_tick[1]
    last_tick = ticks[-1]
    exit_ts = last_tick[0]
    exit_spot = last_tick[1]

    # Available options at entry_ts
    entry_opts = con.execute(f"""
        SELECT symbol, option_type, strike, delta, mark_price, best_bid, best_ask
        FROM read_parquet('{PARQUET_PATH}')
        WHERE datetime_ist = '{entry_ts}'
          AND best_bid IS NOT NULL AND best_ask IS NOT NULL
        ORDER BY strike ASC;
    """).fetchall()

    calls = [o for o in entry_opts if o[1] == "CE"]
    puts = [o for o in entry_opts if o[1] == "PE"]

    if not calls or not puts:
        return None

    # Select Short Call and Short Put closest to target_delta
    sc_opt = min(calls, key=lambda x: abs(x[3] - target_delta))
    sp_opt = min(puts, key=lambda x: abs(abs(x[3]) - target_delta))

    sc_k = sc_opt[2]
    sp_k = sp_opt[2]
    lc_k = sc_k + wing_width
    lp_k = sp_k - wing_width

    # Find long wing options at entry
    lc_opts = [o for o in calls if o[2] == lc_k]
    lp_opts = [o for o in puts if o[2] == lp_k]

    if not lc_opts or not lp_opts:
        # Fallback to closest available strike for wing if exact +400 is missing
        lc_opts = [min(calls, key=lambda x: abs(x[2] - lc_k))]
        lp_opts = [min(puts, key=lambda x: abs(x[2] - lp_k))]

    lc_opt = lc_opts[0]
    lp_opt = lp_opts[0]

    # 1. Entry Prices
    sc_entry_bid = sc_opt[5]
    sp_entry_bid = sp_opt[5]
    lc_entry_ask = lc_opt[6]
    lp_entry_ask = lp_opt[6]

    gross_credit = sc_entry_bid + sp_entry_bid
    wing_debit = lc_entry_ask + lp_entry_ask
    net_credit = gross_credit - wing_debit
    max_risk = wing_width - net_credit

    # 2. Exit Prices at final available timestamp (settlement)
    exit_opts = con.execute(f"""
        SELECT symbol, option_type, strike, best_bid, best_ask
        FROM read_parquet('{PARQUET_PATH}')
        WHERE datetime_ist = '{exit_ts}'
        ORDER BY strike ASC;
    """).fetchall()

    by_sym_exit = {o[0]: o for o in exit_opts}

    def get_exit_price(sym, opt_type, strike, is_short):
        row = by_sym_exit.get(sym)
        if row:
            if is_short:
                return row[4] if row[4] is not None else 0.0  # Buy back at best_ask
            else:
                return row[3] if row[3] is not None else 0.0  # Sell at best_bid
        # Intrinsic value fallback if tick missing
        if opt_type == "CE":
            intrinsic = max(0.0, exit_spot - strike)
        else:
            intrinsic = max(0.0, strike - exit_spot)
        return intrinsic

    sc_exit_ask = get_exit_price(sc_opt[0], "CE", sc_k, is_short=True)
    sp_exit_ask = get_exit_price(sp_opt[0], "PE", sp_k, is_short=True)
    lc_exit_bid = get_exit_price(lc_opt[0], "CE", lc_k, is_short=False)
    lp_exit_bid = get_exit_price(lp_opt[0], "PE", lp_k, is_short=False)

    # Leg PnLs
    sc_pnl = sc_entry_bid - sc_exit_ask
    sp_pnl = sp_entry_bid - sp_exit_ask
    lc_pnl = lc_exit_bid - lc_entry_ask
    lp_pnl = lp_exit_bid - lp_entry_ask

    gross_pnl = sc_pnl + sp_pnl + lc_pnl + lp_pnl

    # Fees with GST
    entry_fees = (
        calc_leg_fee_with_gst(entry_spot, sc_entry_bid)
        + calc_leg_fee_with_gst(entry_spot, sp_entry_bid)
        + calc_leg_fee_with_gst(entry_spot, lc_entry_ask)
        + calc_leg_fee_with_gst(entry_spot, lp_entry_ask)
    )

    exit_fees = (
        calc_leg_fee_with_gst(exit_spot, sc_exit_ask)
        + calc_leg_fee_with_gst(exit_spot, sp_exit_ask)
        + calc_leg_fee_with_gst(exit_spot, lc_exit_bid)
        + calc_leg_fee_with_gst(exit_spot, lp_exit_bid)
    )

    total_fees = entry_fees + exit_fees
    net_pnl = gross_pnl - total_fees
    roi_on_risk = (net_pnl / max_risk) * 100 if max_risk > 0 else 0.0

    return {
        "date": date_str,
        "entry_ts": entry_ts.strftime("%H:%M:%S"),
        "exit_ts": exit_ts.strftime("%H:%M:%S"),
        "entry_spot": entry_spot,
        "exit_spot": exit_spot,
        "short_strikes": f"{int(sc_k)}C / {int(sp_k)}P",
        "wing_strikes": f"{int(lc_k)}C / {int(lp_k)}P",
        "sc_delta": sc_opt[3],
        "sp_delta": sp_opt[3],
        "net_credit": net_credit,
        "max_risk": max_risk,
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "roi_risk": roi_on_risk,
        "result": "WIN" if net_pnl > 0 else "LOSS",
    }


def main():
    con = duckdb.connect()

    print("=" * 135)
    print("           5-DAY MULTI-ENTRY 0DTE BTC IRON CONDOR BACKTEST (2026-11-17 to 2026-11-21)")
    print("=" * 135)
    print("Fee Schedule: Delta Exchange India Taker Fee (0.03% spot capped at 5% premium) + 18% GST per leg.")
    print(
        "Long Wings  : +$400 OTM Call and -$400 OTM Put | Hold to Final Available Pre-Settlement Tick (~17:25-17:30 IST)\n"
    )

    days = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]
    entry_windows = [
        ("14:30", "14:30 IST (3h pre-expiry)"),
        ("15:30", "15:30 IST (2h pre-expiry)"),
        ("16:30", "16:30 IST (1h pre-expiry)"),
    ]
    deltas = [(0.20, "~20 Delta"), (0.15, "~15 Delta")]

    all_results = []

    # Run Day-by-Day backtest
    for delta_val, delta_lbl in deltas:
        print(f"\n{'=' * 60} {delta_lbl.upper()} IRON CONDOR {'=' * 60}")
        for entry_target, entry_label in entry_windows:
            print(f"\n--- Entry Window: {entry_label} ---")
            print(
                f"{'Date':<10} | {'Actual Entry':<12} | {'Short Strikes':<17} | {'Net Credit':<11} | "
                f"{'Gross PnL':<11} | {'Fees + GST':<11} | {'Net PnL ($)':<12} | {'ROI on Risk':<12} | {'Result'}"
            )
            print("-" * 115)

            for d in days:
                res = run_condor_trade(con, d, entry_target, delta_val)
                if not res:
                    print(
                        f"{d:<10} | N/A (Feed active after 16:45)    | -                 | -           | -           | -           | -            | -            | N/A"
                    )
                    continue

                all_results.append((delta_lbl, entry_label, res))

                credit_str = f"${res['net_credit']:<9.2f}"
                gross_str = f"${res['gross_pnl']:<9.2f}"
                fee_str = f"${res['total_fees']:<9.2f}"
                net_str = f"${res['net_pnl']:<10.2f}"
                roi_str = f"{res['roi_risk']:<10.2f}%"

                print(
                    f"{d:<10} | {res['entry_ts']:<12} | {res['short_strikes']:<17} | {credit_str} | "
                    f"{gross_str} | {fee_str} | {net_str} | {roi_str} | {res['result']}"
                )

    # ---------------------------------------------------------
    # 5-Day Summary Table
    # ---------------------------------------------------------
    print("\n\n" + "=" * 115)
    print("                          5-DAY STRATEGY PERFORMANCE SUMMARY TABLE")
    print("=" * 115)
    print(
        f"{'Delta Bucket':<13} | {'Entry Window':<26} | {'Trades':<7} | {'Win Rate %':<11} | "
        f"{'Total Net PnL':<15} | {'Avg ROI/Trade %':<16} | {'Worst Day Loss'}"
    )
    print("-" * 115)

    grouped = defaultdict(list)
    for delta_lbl, entry_lbl, res in all_results:
        grouped[(delta_lbl, entry_lbl)].append(res)

    for (delta_lbl, entry_lbl), trades in grouped.items():
        total_trades = len(trades)
        wins = sum(1 for t in trades if t["net_pnl"] > 0)
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
        total_net_pnl = sum(t["net_pnl"] for t in trades)
        avg_roi = sum(t["roi_risk"] for t in trades) / total_trades if total_trades > 0 else 0.0
        worst_loss = min(t["net_pnl"] for t in trades) if trades else 0.0

        pnl_str = f"${total_net_pnl:+,.2f}"
        roi_str = f"{avg_roi:+,.2f}%"
        worst_str = f"${worst_loss:+,.2f}"

        print(
            f"{delta_lbl:<13} | {entry_lbl:<26} | {total_trades:<7} | {win_rate:<10.1f}% | "
            f"{pnl_str:<15} | {roi_str:<16} | {worst_str}"
        )

    print("=" * 115)


if __name__ == "__main__":
    main()
