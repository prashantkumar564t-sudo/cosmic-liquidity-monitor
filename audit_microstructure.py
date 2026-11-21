"""
audit_microstructure.py
Deep exploratory and microstructure audit of mobile_sync/cosmic_options.duckdb (Read-Only)
"""

import json
from pathlib import Path

import duckdb

DB_PATH = Path("mobile_sync/cosmic_options.duckdb").resolve()


def run_audit():
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    print("=" * 95)
    print("       COSMIC TRADE OPTIONS MICROSTRUCTURE & EXPLORATORY AUDIT REPORT        ")
    print("=" * 95)

    # ---------------------------------------------------------
    # SECTION 1: TIMELINE & DATA STRUCTURE
    # ---------------------------------------------------------
    print("\n[SECTION 1: TIMELINE & DATA STRUCTURE]")
    print("-" * 65)

    tables = conn.execute("SHOW TABLES").fetchall()
    print("Tables & Row Counts:")
    for tbl in tables:
        tname = tbl[0]
        cnt = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
        print(f"  • {tname:<25} : {cnt:,} rows")

    # Distinct timestamps/cycles audit
    # Check deduplication on cycle_id = 1
    cycle_ts = conn.execute("""
        SELECT
            cycle_id,
            CAST(captured_at AS VARCHAR) as ts_str,
            epoch(captured_at) as ts_epoch,
            COUNT(*) as contract_count
        FROM (
            SELECT DISTINCT captured_at, cycle_id, symbol
            FROM option_chain_snapshots
        )
        GROUP BY cycle_id, captured_at
        ORDER BY ts_epoch
    """).fetchall()

    first_cycle = cycle_ts[0]
    last_cycle = cycle_ts[-1]
    start_ts_str = first_cycle[1]
    end_ts_str = last_cycle[1]
    duration_sec = last_cycle[2] - first_cycle[2]

    # Calculate intervals between distinct cycles
    distinct_cycles = conn.execute("""
        WITH distinct_c AS (
            SELECT DISTINCT cycle_id, epoch(captured_at) as ep, CAST(captured_at AS VARCHAR) as ts
            FROM option_chain_snapshots
        ),
        ordered AS (
            SELECT cycle_id, ep, ts,
                   ep - LAG(ep) OVER (ORDER BY ep) as step_sec
            FROM distinct_c
        )
        SELECT
            ROUND(MIN(step_sec), 2) as min_step,
            ROUND(AVG(step_sec), 2) as avg_step,
            ROUND(MAX(step_sec), 2) as max_step,
            COUNT(*) as unique_snapshots
        FROM ordered
        WHERE step_sec IS NOT NULL
    """).fetchone()

    min_step, avg_step, max_step, num_transitions = distinct_cycles
    total_unique_cycles = num_transitions + 1

    print(f"Total Unique Snapshots   : {total_unique_cycles} cycles recorded")
    print(f"Start Timestamp (IST)    : {start_ts_str}")
    print(f"End Timestamp (IST)      : {end_ts_str}")
    print(f"Total Duration           : {duration_sec:.1f} seconds ({duration_sec / 60:.2f} minutes)")
    print(f"Cycle Intervals          : Min: {min_step}s | Avg: {avg_step}s | Max: {max_step}s (Target: 5.0s)")

    # Sample l2_book_json inspection
    sample_row = conn.execute("""
        SELECT symbol, strike, option_type, l2_book_json
        FROM option_chain_snapshots
        WHERE strike = 58000 AND option_type = 'CALL'
        LIMIT 1
    """).fetchone()

    sym, stk, otype, raw_json_str = sample_row
    book_data = json.loads(raw_json_str)
    bids = book_data.get("bids", [])
    asks = book_data.get("asks", [])

    print(f"\nOrder Book L2 JSON Structure Sample ({sym}):")
    print("  Top 3 Bids:")
    for i, b in enumerate(bids[:3], 1):
        print(f"    Level {i}: Price = ${float(b['price']):.1f} | Qty = {float(b['qty']):.4f} BTC")
    print("  Top 3 Asks:")
    for i, a in enumerate(asks[:3], 1):
        print(f"    Level {i}: Price = ${float(a['price']):.1f} | Qty = {float(a['qty']):.4f} BTC")

    # ---------------------------------------------------------
    # SECTION 2: OPTION CHAIN STRIKE COVERAGE
    # ---------------------------------------------------------
    print("\n[SECTION 2: OPTION CHAIN STRIKE COVERAGE]")
    print("-" * 65)

    strike_stats = conn.execute("""
        SELECT
            COUNT(DISTINCT strike) as num_strikes,
            MIN(strike) as min_strike,
            MAX(strike) as max_strike,
            COUNT(*) FILTER (WHERE option_type = 'CALL') / COUNT(DISTINCT cycle_id) as avg_calls_per_cycle,
            COUNT(*) FILTER (WHERE option_type = 'PUT') / COUNT(DISTINCT cycle_id) as avg_puts_per_cycle
        FROM option_chain_snapshots
    """).fetchone()

    num_strikes, min_stk, max_stk, calls_per_cycle, puts_per_cycle = strike_stats

    # Check step size
    strikes = [
        r[0] for r in conn.execute("SELECT DISTINCT strike FROM option_chain_snapshots ORDER BY strike").fetchall()
    ]
    steps = [round(strikes[i + 1] - strikes[i], 2) for i in range(len(strikes) - 1)]
    unique_steps = sorted(set(steps))

    print(f"Unique Strikes Recorded  : {num_strikes} strikes")
    print(f"Strike Grid Range        : ${min_stk:,.0f} to ${max_stk:,.0f}")
    print(f"Strike Step Size         : ${unique_steps[0]:.0f} uniform spacing (Verified: {unique_steps})")
    print(
        f"Contracts per Cycle      : {int(calls_per_cycle)} Calls + {int(puts_per_cycle)} Puts = {int(calls_per_cycle + puts_per_cycle)} total per cycle"
    )

    # ---------------------------------------------------------
    # SECTION 3: UNDERLYING MOVEMENT (PUT-CALL PARITY)
    # ---------------------------------------------------------
    print("\n[SECTION 3: UNDERLYING BTC MOVEMENT DURING WINDOW]")
    print("-" * 65)

    # Implied spot calculation per cycle using Put-Call parity at Strike 58000 (closest to delta 0.50):
    # Parity: Spot ~ Strike + Call_Mark - Put_Mark
    parity_data = conn.execute("""
        WITH c_data AS (
            SELECT cycle_id, CAST(captured_at AS VARCHAR) as ts, strike, mark_price as call_mark, delta as call_delta
            FROM option_chain_snapshots
            WHERE option_type = 'CALL'
        ),
        p_data AS (
            SELECT cycle_id, strike, mark_price as put_mark
            FROM option_chain_snapshots
            WHERE option_type = 'PUT'
        ),
        joined AS (
            SELECT
                c.cycle_id,
                c.ts,
                c.strike,
                c.call_mark,
                p.put_mark,
                c.strike + c.call_mark - p.put_mark as implied_spot,
                abs(c.call_delta - 0.5) as delta_gap
            FROM c_data c
            JOIN p_data p ON c.cycle_id = p.cycle_id AND c.strike = p.strike
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY cycle_id ORDER BY delta_gap) as rn
            FROM joined
        )
        SELECT cycle_id, ts, strike, call_mark, put_mark, ROUND(implied_spot, 2) as implied_spot
        FROM ranked
        WHERE rn = 1
        ORDER BY cycle_id
    """).fetchall()

    spots = [r[5] for r in parity_data]
    start_spot = spots[0]
    end_spot = spots[-1]
    high_spot = max(spots)
    low_spot = min(spots)
    spot_range = round(high_spot - low_spot, 2)
    net_change = round(end_spot - start_spot, 2)

    print("Estimation Method        : ATM Put-Call Parity: S = K + C_mark - P_mark (at delta ~ 0.50)")
    print(f"Start Implied Spot (IST) : ${start_spot:,.2f}  (at {parity_data[0][1]})")
    print(f"End Implied Spot (IST)   : ${end_spot:,.2f}  (at {parity_data[-1][1]})")
    print(f"High Implied Spot        : ${high_spot:,.2f}")
    print(f"Low Implied Spot         : ${low_spot:,.2f}")
    print(f"High-Low Price Range     : ${spot_range:,.2f} USD points ({(spot_range / start_spot) * 100:.3f}%)")
    print(f"Net Window Move          : {net_change:+.2f} USD points")

    # ---------------------------------------------------------
    # SECTION 4: PRICE & ORDER BOOK MICROSTRUCTURE IMPACT
    # ---------------------------------------------------------
    print("\n[SECTION 4: PRICE & ORDER BOOK MICROSTRUCTURE IMPACT]")
    print("-" * 65)

    # Find the single largest cycle-to-cycle change in mark price
    largest_jump = conn.execute("""
        WITH delta_moves AS (
            SELECT
                cycle_id,
                CAST(captured_at AS VARCHAR) as ts,
                symbol,
                strike,
                option_type,
                mark_price,
                spread,
                best_bid,
                best_ask,
                LAG(cycle_id) OVER (PARTITION BY symbol ORDER BY cycle_id) as prev_cycle,
                LAG(mark_price) OVER (PARTITION BY symbol ORDER BY cycle_id) as prev_mark,
                LAG(spread) OVER (PARTITION BY symbol ORDER BY cycle_id) as prev_spread,
                abs(mark_price - LAG(mark_price) OVER (PARTITION BY symbol ORDER BY cycle_id)) as mark_delta
            FROM option_chain_snapshots
            WHERE strike BETWEEN 57000 AND 59000
        )
        SELECT cycle_id, prev_cycle, ts, symbol, strike, option_type, prev_mark, mark_price, mark_delta, prev_spread, spread
        FROM delta_moves
        WHERE prev_mark IS NOT NULL
        ORDER BY mark_delta DESC
        LIMIT 1
    """).fetchone()

    spike_cycle, prev_cycle, spike_ts, spike_sym, spike_stk, spike_otype, p_mark, n_mark, m_diff, p_sp, n_sp = (
        largest_jump
    )
    print("Single Largest Volatility Event:")
    print(f"  • Event Cycle          : Cycle {spike_cycle} (vs Cycle {prev_cycle}) at {spike_ts}")
    print(f"  • Contract             : {spike_sym} ({spike_otype} Strike ${spike_stk:,.0f})")
    print(f"  • Mark Price Shift     : ${p_mark:.2f} -> ${n_mark:.2f} (Δ ${m_diff:+.2f})")
    print(f"  • Spread Shift         : ${p_sp:.2f} -> ${n_sp:.2f} (Δ {n_sp - p_sp:+.2f})")

    # Extract top 3 L2 levels before vs during move
    before_book = conn.execute(
        f"SELECT l2_book_json FROM option_chain_snapshots WHERE cycle_id = {prev_cycle} AND symbol = '{spike_sym}' LIMIT 1"
    ).fetchone()[0]
    after_book = conn.execute(
        f"SELECT l2_book_json FROM option_chain_snapshots WHERE cycle_id = {spike_cycle} AND symbol = '{spike_sym}' LIMIT 1"
    ).fetchone()[0]

    b_bids = json.loads(before_book).get("bids", [])[:3]
    b_asks = json.loads(before_book).get("asks", [])[:3]
    a_bids = json.loads(after_book).get("bids", [])[:3]
    a_asks = json.loads(after_book).get("asks", [])[:3]

    print("\nMicrostructure Depth Comparison (Top 3 Levels):")
    print(f"  BEFORE (Cycle {prev_cycle}):")
    print(f"    Bids : {[(float(x['price']), float(x['qty'])) for x in b_bids]}")
    print(f"    Asks : {[(float(x['price']), float(x['qty'])) for x in b_asks]}")
    print(f"  DURING MOVE (Cycle {spike_cycle}):")
    print(f"    Bids : {[(float(x['price']), float(x['qty'])) for x in a_bids]}")
    print(f"    Asks : {[(float(x['price']), float(x['qty'])) for x in a_asks]}")

    # Microstructure Progression Table
    # Track ATM (58000 C & P) + OTM Call (58600 C, delta ~0.25) + OTM Put (57400 P, delta ~ -0.25)
    key_symbols = ["BTC-21NOV26-58000-C", "BTC-21NOV26-58000-P", "BTC-21NOV26-58600-C", "BTC-21NOV26-57400-P"]
    key_cycles = [parity_data[0][0], spike_cycle, parity_data[-1][0]]

    # Map cycle to implied spot
    spot_map = {r[0]: (r[1], r[5]) for r in parity_data}

    print("\nMicrostructure Tracking Table across Key Timestamps (Start, Spike, End):")
    print("=" * 140)
    print(
        f"{'IST_TIME':<22} | {'SPOT ($)':<9} | {'SYMBOL':<20} | {'MARK':<7} | {'BID/ASK':<15} | {'SPREAD ($)':<10} | {'SPREAD %':<8} | {'BID LOTS':<9} | {'ASK LOTS':<9} | {'DEPTH LOTS'}"
    )
    print("=" * 140)

    for cid in key_cycles:
        ts_str, s_val = spot_map.get(cid, ("N/A", 0.0))
        for ksym in key_symbols:
            row = conn.execute(f"""
                SELECT
                    symbol,
                    mark_price,
                    best_bid,
                    best_ask,
                    spread,
                    ROUND((spread / best_ask) * 100, 3) as spread_pct,
                    top_bid_lots,
                    top_ask_lots,
                    bid_depth_lots + ask_depth_lots as total_depth_lots
                FROM option_chain_snapshots
                WHERE cycle_id = {cid} AND symbol = '{ksym}'
                LIMIT 1
            """).fetchone()

            if row:
                sym_name, mark_p, b_bid, b_ask, sp_val, sp_pct, tb_lots, ta_lots, tot_lots = row
                ba_str = f"{b_bid:.1f}/{b_ask:.1f}" if b_bid is not None and b_ask is not None else "-/-"
                sp_str = f"${sp_val:.1f}" if sp_val is not None else "-"
                spp_str = f"{sp_pct:.2f}%" if sp_pct is not None else "-"
                print(
                    f"{ts_str:<22} | ${s_val:<8.1f} | {sym_name:<20} | ${mark_p:<6.1f} | {ba_str:<15} | {sp_str:<10} | {spp_str:<8} | {tb_lots:<9.0f} | {ta_lots:<9.0f} | {tot_lots:<10.0f}"
                )
        print("-" * 140)


if __name__ == "__main__":
    run_audit()
