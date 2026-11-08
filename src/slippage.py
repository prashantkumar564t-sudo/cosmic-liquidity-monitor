from src.config import TAKER_FEE_RATE

class ExecutionSimulator:
    @staticmethod
    def simulate_market_buy(asks: list, budget_usd: float) -> dict:
        """
        Asks ladder walk करके effective fill price और nominal slippage निकालना
        """
        if not asks or budget_usd <= 0:
            return {"slippage_pct": 0.0, "effective_price": 0.0, "fully_filled": False}

        sorted_asks = sorted(asks, key=lambda x: x[0])
        best_ask = sorted_asks[0][0]
        remaining_budget = budget_usd
        total_qty_bought = 0.0
        total_cost = 0.0

        for price, qty in sorted_asks:
            level_nominal_value = price * qty
            if remaining_budget <= level_nominal_value:
                fill_qty = remaining_budget / price
                total_qty_bought += fill_qty
                total_cost += remaining_budget
                remaining_budget = 0.0
                break
            else:
                total_qty_bought += qty
                total_cost += level_nominal_value
                remaining_budget -= level_nominal_value

        if total_qty_bought == 0:
            return {"slippage_pct": 0.0, "effective_price": 0.0, "fully_filled": False}

        effective_price = total_cost / total_qty_bought
        nominal_slippage = effective_price - best_ask
        slippage_pct = (nominal_slippage / best_ask) * 100
        taker_fee = total_cost * TAKER_FEE_RATE

        return {
            "budget": budget_usd,
            "effective_price": round(effective_price, 2),
            "slippage_usd": round(nominal_slippage, 4),
            "slippage_pct": round(slippage_pct, 6),
            "taker_fee": round(taker_fee, 4),
            "fully_filled": remaining_budget == 0
        }