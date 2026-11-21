"""Backtest Spot-Breach & Spread-Level Stop-Loss Exit Rules for 0DTE BTC Iron Condors.

Compares:
1. Baseline (Hold to Expiry)
2. Spot-Breach Exit (0 pts buffer: spot touches/crosses short strike)
3. Early Buffer Exit ($100 before short strike)
4. 2x Credit Stop-Loss (Spread-level: buyback cost >= 2x initial spread credit)
5. 3x Credit Stop-Loss (Spread-level: buyback cost >= 3x initial spread credit)

Includes realistic Bid/Ask execution friction + Delta Exchange India fees & 18% GST.
"""

from collections import defaultdict

import duckdb

PARQUET_PATH = "mobile_sync/btc_0dte_5day_pre_expiry.parquet"
WING_WIDTH = 400.0


def calc_leg_fee(spot: float, premium: float) -> float:
    """Delta Exchange India options taker fee (0.03% spot capped at 5% premium) + 18% GST."""
    if premium <= 0:
        return 0.0
    base_fee = min(0.0003 * spot, 0.05 * premium)
    return base_fee * 1.18


def run_trade_with_rule(
    con: duckdb.DuckDBPyConnection,
    date_str: str,
    target_delta: float,
    rule_type: str,  # 'baseline', 'spot_0pt', 'spot_100pt', 'sl_2x', 'sl_3x'
    wing_width: float = WING_WIDTH,
) -> dict | None:
    # Get all distinct ticks and spot prices for this date in chronological order
    ticks = con.execute(f"""
        SELECT datetime_ist, spot_price
        FROM read_parquet('{PARQUET_PATH}')
        WHERE CAST(datetime_ist AS DATE) = '{date_str}'
        GROUP BY datetime_ist, spot_price
        ORDER BY datetime_ist ASC;
    """).fetchall()

    if not ticks:
        return None

    entry_tick = ticks[0]
    entry_ts = entry_tick[0]
    entry_spot = entry_tick[1]
    last_tick = ticks[-1]
    final_ts = last_tick[0]
    final_spot = last_tick[1]

    # Load options at entry
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

    sc_opt = min(calls, key=lambda x: abs(x[3] - target_delta))
    sp_opt = min(puts, key=lambda x: abs(abs(x[3]) - target_delta))

    sc_k = sc_opt[2]
    sp_k = sp_opt[2]
    lc_k = sc_k + wing_width
    lp_k = sp_k - wing_width

    lc_opts = [o for o in calls if o[2] == lc_k]
    lp_opts = [o for o in puts if o[2] == lp_k]

    if not lc_opts or not lp_opts:
        lc_opts = [min(calls, key=lambda x: abs(x[2] - lc_k))]
        lp_opts = [min(puts, key=lambda x: abs(x[2] - lp_k))]

    lc_opt = lc_opts[0]
    lp_opt = lp_opts[0]

    # Entry Prices
    sc_sym = sc_opt[0]
    sp_sym = sp_opt[0]
    lc_sym = lc_opt[0]
    lp_sym = lp_opt[0]

    sc_entry_bid = sc_opt[5]
    sp_entry_bid = sp_opt[5]
    lc_entry_ask = lc_opt[6]
    lp_entry_ask = lp_opt[6]

    call_credit = sc_entry_bid - lc_entry_ask
    put_credit = sp_entry_bid - lp_entry_ask
    net_condor_credit = call_credit + put_credit
    max_risk = wing_width - net_condor_credit

    # Entry fees
    entry_fees = (
        calc_leg_fee(entry_spot, sc_entry_bid)
        + calc_leg_fee(entry_spot, sp_entry_bid)
        + calc_leg_fee(entry_spot, lc_entry_ask)
        + calc_leg_fee(entry_spot, lp_entry_ask)
    )

    # Load time-series for the 4 legs across the entire window
    target_syms = f"'{sc_sym}', '{sp_sym}', '{lc_sym}', '{lp_sym}'"
    series_rows = con.execute(f"""
        SELECT datetime_ist, spot_price, symbol, best_bid, best_ask
        FROM read_parquet('{PARQUET_PATH}')
        WHERE symbol IN ({target_syms})
        ORDER BY datetime_ist ASC;
    """).fetchall()

    by_ts_sym = defaultdict(dict)
    for r in series_rows:
        ts = r[0]
        sym = r[2]
        by_ts_sym[ts][sym] = {"bid": r[3], "ask": r[4], "spot": r[1]}

    # Track leg states
    # Call Spread: Active / Closed early
    call_closed = False
    call_exit_sc_ask = None
    call_exit_lc_bid = None
    call_exit_spot = final_spot
    call_close_reason = "Expired"

    # Put Spread: Active / Closed early
    put_closed = False
    put_exit_sp_ask = None
    put_exit_lp_bid = None
    put_exit_spot = final_spot
    put_close_reason = "Expired"

    if rule_type != "baseline":
        for tick in ticks[1:]:
            t = tick[0]
            spot = tick[1]
            tick_data = by_ts_sym.get(t, {})

            # 1. Call Spread Check
            if not call_closed:
                sc_data = tick_data.get(sc_sym)
                lc_data = tick_data.get(lc_sym)

                if sc_data and lc_data and sc_data["ask"] is not None and lc_data["bid"] is not None:
                    sc_curr_ask = sc_data["ask"]
                    lc_curr_bid = lc_data["bid"]
                    call_spread_cost = sc_curr_ask - lc_curr_bid

                    trigger_call = False
                    reason = ""

                    if rule_type == "spot_0pt" and spot >= sc_k:
                        trigger_call = True
                        reason = f"Spot Breach >= {int(sc_k)}"
                    elif rule_type == "spot_100pt" and spot >= (sc_k - 100):
                        trigger_call = True
                        reason = f"Spot Buffer >= {int(sc_k - 100)}"
                    elif rule_type == "sl_2x" and call_credit > 0 and call_spread_cost >= (2.0 * call_credit):
                        trigger_call = True
                        reason = f"2x Credit SL (${call_spread_cost:.1f} >= 2x${call_credit:.1f})"
                    elif rule_type == "sl_3x" and call_credit > 0 and call_spread_cost >= (3.0 * call_credit):
                        trigger_call = True
                        reason = f"3x Credit SL (${call_spread_cost:.1f} >= 3x${call_credit:.1f})"

                    if trigger_call:
                        call_closed = True
                        call_exit_sc_ask = sc_curr_ask
                        call_exit_lc_bid = lc_curr_bid
                        call_exit_spot = spot
                        call_close_reason = reason

            # 2. Put Spread Check
            if not put_closed:
                sp_data = tick_data.get(sp_sym)
                lp_data = tick_data.get(lp_sym)

                if sp_data and lp_data and sp_data["ask"] is not None and lp_data["bid"] is not None:
                    sp_curr_ask = sp_data["ask"]
                    lp_curr_bid = lp_data["bid"]
                    put_spread_cost = sp_curr_ask - lp_curr_bid

                    trigger_put = False
                    reason = ""

                    if rule_type == "spot_0pt" and spot <= sp_k:
                        trigger_put = True
                        reason = f"Spot Breach <= {int(sp_k)}"
                    elif rule_type == "spot_100pt" and spot <= (sp_k + 100):
                        trigger_put = True
                        reason = f"Spot Buffer <= {int(sp_k + 100)}"
                    elif rule_type == "sl_2x" and put_credit > 0 and put_spread_cost >= (2.0 * put_credit):
                        trigger_put = True
                        reason = f"2x Credit SL (${put_spread_cost:.1f} >= 2x${put_credit:.1f})"
                    elif rule_type == "sl_3x" and put_credit > 0 and put_spread_cost >= (3.0 * put_credit):
                        trigger_put = True
                        reason = f"3x Credit SL (${put_spread_cost:.1f} >= 3x${put_credit:.1f})"

                    if trigger_put:
                        put_closed = True
                        put_exit_sp_ask = sp_curr_ask
                        put_exit_lp_bid = lp_curr_bid
                        put_exit_spot = spot
                        put_close_reason = reason

    # Final tick values for any leg that held to expiry
    final_data = by_ts_sym.get(final_ts, {})

    if not call_closed:
        sc_d = final_data.get(sc_sym, {})
        lc_d = final_data.get(lc_sym, {})
        call_exit_sc_ask = sc_d.get("ask") if sc_d.get("ask") is not None else max(0.0, final_spot - sc_k)
        call_exit_lc_bid = lc_d.get("bid") if lc_d.get("bid") is not None else max(0.0, final_spot - lc_k)

    if not put_closed:
        sp_d = final_data.get(sp_sym, {})
        lp_d = final_data.get(lp_sym, {})
        put_exit_sp_ask = sp_d.get("ask") if sp_d.get("ask") is not None else max(0.0, sp_k - final_spot)
        put_exit_lp_bid = lp_d.get("bid") if lp_d.get("bid") is not None else max(0.0, lp_k - final_spot)

    # Compute PnL and exit fees
    # Call leg PnL & Fee
    sc_pnl = sc_entry_bid - call_exit_sc_ask
    lc_pnl = call_exit_lc_bid - lc_entry_ask
    call_spread_pnl = sc_pnl + lc_pnl
    call_exit_fee = calc_leg_fee(call_exit_spot, call_exit_sc_ask) + calc_leg_fee(call_exit_spot, call_exit_lc_bid)

    # Put leg PnL & Fee
    sp_pnl = sp_entry_bid - put_exit_sp_ask
    lp_pnl = put_exit_lp_bid - lp_entry_ask
    put_spread_pnl = sp_pnl + lp_pnl
    put_exit_fee = calc_leg_fee(put_exit_spot, put_exit_sp_ask) + calc_leg_fee(put_exit_spot, put_exit_lp_bid)

    gross_pnl = call_spread_pnl + put_spread_pnl
    total_fees = entry_fees + call_exit_fee + put_exit_fee
    net_pnl = gross_pnl - total_fees
    roi_on_risk = (net_pnl / max_risk) * 100 if max_risk > 0 else 0.0

    return {
        "date": date_str,
        "rule": rule_type,
        "delta": target_delta,
        "short_strikes": f"{int(sc_k)}C/{int(sp_k)}P",
        "net_credit": net_condor_credit,
        "max_risk": max_risk,
        "call_reason": call_close_reason,
        "put_reason": put_close_reason,
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "roi_risk": roi_on_risk,
    }


def main():
    con = duckdb.connect()

    print("=" * 125)
    print("      0DTE BTC IRON CONDOR: SPOT-BREACH & SPREAD-SL EXIT RULES COMPARISON (NOV 17 - NOV 21)")
    print("=" * 125)
    print(
        "Friction: Entry at best_bid/best_ask + Exit at best_ask/best_bid + Delta India fees (0.03% capped @ 5%) + 18% GST."
    )
    print("Strategy: Hold Safe Side to Expiry, Close Only the Threatened Spread on Trigger.\n")

    days = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]

    rules = [
        ("baseline", "1. Baseline (Hold to Expiry)"),
        ("spot_0pt", "2. Spot-Breach (0 pt: Spot >= Short Strike)"),
        ("spot_100pt", "3. Early Buffer ($100 before Short Strike)"),
        ("sl_2x", "4. 2x Credit Stop-Loss (Spread-level)"),
        ("sl_3x", "5. 3x Credit Stop-Loss (Spread-level)"),
    ]

    deltas = [(0.20, "~20 Delta"), (0.15, "~15 Delta")]

    summary_rows = []

    for delta_val, delta_lbl in deltas:
        for rule_code, rule_name in rules:
            day_results = []
            for d in days:
                res = run_trade_with_rule(con, d, delta_val, rule_code)
                if res:
                    day_results.append(res)

            nov17_res = next((r for r in day_results if r["date"] == "2026-11-17"), None)
            nov17_pnl = nov17_res["net_pnl"] if nov17_res else 0.0

            total_pnl = sum(r["net_pnl"] for r in day_results)
            wins = [r for r in day_results if r["net_pnl"] > 0]
            losses = [r for r in day_results if r["net_pnl"] <= 0]
            win_rate = (len(wins) / len(day_results)) * 100 if day_results else 0.0

            gross_wins = sum(r["net_pnl"] for r in wins)
            gross_losses = abs(sum(r["net_pnl"] for r in losses))

            if gross_losses > 0:
                profit_factor_str = f"{gross_wins / gross_losses:.2f}"
            else:
                profit_factor_str = "Inf (100% Win)"

            summary_rows.append(
                {
                    "rule": rule_name,
                    "delta": delta_lbl,
                    "nov17_loss": nov17_pnl,
                    "total_pnl": total_pnl,
                    "win_rate": win_rate,
                    "profit_factor": profit_factor_str,
                    "day_results": day_results,
                }
            )

    # Print Comparison Table
    print(
        f"{'Exit Rule':<45} | {'Delta':<10} | {'Nov 17 Loss ($)':<16} | "
        f"{'5-Day Net PnL ($)':<18} | {'Win Rate %':<11} | {'Profit Factor'}"
    )
    print("-" * 125)

    curr_delta = None
    for r in summary_rows:
        if curr_delta is not None and curr_delta != r["delta"]:
            print("-" * 125)
        curr_delta = r["delta"]

        nov17_str = f"${r['nov17_loss']:+,.2f}"
        pnl_str = f"${r['total_pnl']:+,.2f}"
        win_str = f"{r['win_rate']:.1f}%"

        print(
            f"{r['rule']:<45} | {r['delta']:<10} | {nov17_str:<16} | "
            f"{pnl_str:<18} | {win_str:<11} | {r['profit_factor']}"
        )

    print("=" * 125)

    # Detailed Nov 17 Behavior by Rule
    print("\n[NOV 17 TREND DAY DEEP DIVE: HOW EACH RULE MITIGATED THE BREACH]")
    for r in summary_rows:
        n17 = next((x for x in r["day_results"] if x["date"] == "2026-11-17"), None)
        if n17:
            print(f"  • {r['delta']} [{r['rule']}]:")
            print(f"      Call Spread: {n17['call_reason']} | Put Spread: {n17['put_reason']}")
            print(
                f"      Gross PnL: ${n17['gross_pnl']:.2f} | Fees+GST: ${n17['total_fees']:.2f} | Net PnL: ${n17['net_pnl']:.2f}\n"
            )


if __name__ == "__main__":
    main()
