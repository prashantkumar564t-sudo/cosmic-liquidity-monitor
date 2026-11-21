"""0DTE Defined-Risk Iron Condor Strategy Simulation (2026-11-21).

Simulates holding 0DTE Iron Condor positions across:
- Short delta: ~15 Delta (58400C / 57600P) & ~20 Delta (58200C / 57600P)
- Long wing offsets: $400 and $600 further OTM
- Entry at 16:45:08 IST (Sell shorts at best_bid, buy wings at best_ask)
- Hold to Expiry at 17:26:42 IST (Buy shorts at best_ask, sell wings at best_bid)
"""

import duckdb

PARQUET_PATH = "mobile_sync/pre_expiry_20261121.parquet"


def main():
    con = duckdb.connect()

    print("=" * 115)
    print("      0DTE DEFINED-RISK BTC IRON CONDOR SIMULATION (2026-11-21 | 16:45 -> 17:26 IST)")
    print("=" * 115)

    # Load 0DTE BTC time series
    query = f"""
        SELECT
            datetime_ist,
            symbol,
            strike,
            option_type,
            delta,
            mark_price,
            best_bid,
            best_ask
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

    # Available strikes inspection at entry

    # Configurations to test:
    # (Delta_Label, Short_Call_K, Short_Put_K, Wing_Width)
    configs = [
        ("~20 Delta", 58200, 57600, 400),
        ("~20 Delta", 58200, 57600, 600),
        ("~15 Delta", 58400, 57600, 400),
        ("~15 Delta", 58400, 57600, 600),
    ]

    results = []

    for delta_lbl, sc_k, sp_k, wing_w in configs:
        lc_k = sc_k + wing_w
        lp_k = sp_k - wing_w

        sc_sym = f"C-BTC-{sc_k}-211126"
        sp_sym = f"P-BTC-{sp_k}-211126"
        lc_sym = f"C-BTC-{lc_k}-211126"
        lp_sym = f"P-BTC-{lp_k}-211126"

        sc_series = by_symbol.get(sc_sym)
        sp_series = by_symbol.get(sp_sym)
        lc_series = by_symbol.get(lc_sym)
        lp_series = by_symbol.get(lp_sym)

        if not (sc_series and sp_series and lc_series and lp_series):
            print(f"Warning: Missing data for {sc_sym}, {sp_sym}, {lc_sym}, or {lp_sym}")
            continue

        # 1. Entry at 16:45:08 IST
        # Sell short legs at best_bid
        sc_entry_bid = sc_series[0]["best_bid"]
        sp_entry_bid = sp_series[0]["best_bid"]

        # Buy long wings at best_ask
        lc_entry_ask = lc_series[0]["best_ask"]
        lp_entry_ask = lp_series[0]["best_ask"]

        gross_credit = sc_entry_bid + sp_entry_bid
        wing_debit = lc_entry_ask + lp_entry_ask
        net_credit = gross_credit - wing_debit

        # Defined Max Risk = Wing Width - Net Credit
        max_risk = wing_w - net_credit

        # 2. Exit at 17:26:42 IST (Expiry / Settlement)
        # Buy back short legs at best_ask
        sc_exit_ask = sc_series[-1]["best_ask"]
        sp_exit_ask = sp_series[-1]["best_ask"]

        # Sell long wings at best_bid
        lc_exit_bid = lc_series[-1]["best_bid"]
        lp_exit_bid = lp_series[-1]["best_bid"]

        # PnL computation per leg
        sc_pnl = sc_entry_bid - sc_exit_ask
        sp_pnl = sp_entry_bid - sp_exit_ask
        lc_pnl = lc_exit_bid - lc_entry_ask
        lp_pnl = lp_exit_bid - lp_entry_ask

        net_pnl = sc_pnl + sp_pnl + lc_pnl + lp_pnl
        roi_on_risk = (net_pnl / max_risk) * 100 if max_risk > 0 else 0.0
        roi_on_credit = (net_pnl / net_credit) * 100 if net_credit > 0 else 0.0

        results.append(
            {
                "delta_label": delta_lbl,
                "short_strikes": f"{sc_k}C / {sp_k}P",
                "wing_strikes": f"{lc_k}C / {lp_k}P (+${wing_w})",
                "net_credit": net_credit,
                "max_risk": max_risk,
                "net_pnl": net_pnl,
                "roi_risk": roi_on_risk,
                "roi_credit": roi_on_credit,
                "sc_bid_ask": f"{sc_entry_bid} -> {sc_exit_ask}",
                "sp_bid_ask": f"{sp_entry_bid} -> {sp_exit_ask}",
                "lc_bid_ask": f"{lc_entry_ask} -> {lc_exit_bid}",
                "lp_bid_ask": f"{lp_entry_ask} -> {lp_exit_bid}",
            }
        )

    # Print clean results table
    print(
        f"\n{'Short Strikes':<18} | {'Wing Strikes':<22} | {'Net Credit':<12} | "
        f"{'Max Risk':<11} | {'Net PnL ($)':<12} | {'ROI on Risk %':<14} | {'ROI on Credit %'}"
    )
    print("-" * 115)

    for r in results:
        credit_str = f"${r['net_credit']:<10.2f}"
        risk_str = f"${r['max_risk']:<9.2f}"
        pnl_str = f"${r['net_pnl']:<10.2f}"
        roi_r_str = f"{r['roi_risk']:<12.2f}%"
        roi_c_str = f"{r['roi_credit']:<14.2f}%"
        print(
            f"{r['short_strikes']:<18} | {r['wing_strikes']:<22} | {credit_str} | "
            f"{risk_str} | {pnl_str} | {roi_r_str} | {roi_c_str}"
        )

    print("=" * 115)

    # Detailed Leg Breakdown
    print("\n[DETAILED 4-LEG EXECUTION BREAKDOWN (Entry -> Exit Prices)]")
    for r in results:
        print(f"\nConfiguration: Short {r['short_strikes']} with Wings {r['wing_strikes']}:")
        print(
            f"  • Short Call : Sold @ ${r['sc_bid_ask'].split(' -> ')[0]} | Bought @ ${r['sc_bid_ask'].split(' -> ')[1]}"
        )
        print(
            f"  • Short Put  : Sold @ ${r['sp_bid_ask'].split(' -> ')[0]} | Bought @ ${r['sp_bid_ask'].split(' -> ')[1]}"
        )
        print(
            f"  • Long Call  : Bought @ ${r['lc_bid_ask'].split(' -> ')[0]} | Sold @ ${r['lc_bid_ask'].split(' -> ')[1]}"
        )
        print(
            f"  • Long Put   : Bought @ ${r['lp_bid_ask'].split(' -> ')[0]} | Sold @ ${r['lp_bid_ask'].split(' -> ')[1]}"
        )
        print(
            f"  • Result     : Net PnL = ${r['net_pnl']:.2f} | Net Credit = ${r['net_credit']:.2f} | Max Risk = ${r['max_risk']:.2f}"
        )


if __name__ == "__main__":
    main()
