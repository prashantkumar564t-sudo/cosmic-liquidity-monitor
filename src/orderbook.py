class OrderBookEvaluator:
    LOT_SIZE_BTC = 0.001  # 1 lot = 0.001 BTC

    @staticmethod
    def compute_metrics(bids: list, asks: list) -> dict:
        if not bids or not asks:
            return {}

        bids = sorted(bids, key=lambda x: x[0], reverse=True)
        asks = sorted(asks, key=lambda x: x[0])

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask) * 100 if best_ask else 0.0
        mid_price = (best_bid + best_ask) / 2

        # 1. Top 3 Levels Depth (in Lots)
        top3_bid_btc = sum(q for _, q in bids[:3])
        top3_ask_btc = sum(q for _, q in asks[:3])
        top3_bid_lots = round(top3_bid_btc / OrderBookEvaluator.LOT_SIZE_BTC, 1)
        top3_ask_lots = round(top3_ask_btc / OrderBookEvaluator.LOT_SIZE_BTC, 1)

        # 2. Total 20 Levels Depth (in Lots and USD)
        total_bid_btc = sum(q for _, q in bids)
        total_ask_btc = sum(q for _, q in asks)
        total_bid_lots = round(total_bid_btc / OrderBookEvaluator.LOT_SIZE_BTC, 1)
        total_ask_lots = round(total_ask_btc / OrderBookEvaluator.LOT_SIZE_BTC, 1)

        total_bid_usd = sum(p * q for p, q in bids)
        total_ask_usd = sum(p * q for p, q in asks)

        # 3. Imbalance Index [-1.0 to +1.0]
        depth_sum = total_bid_usd + total_ask_usd
        imbalance = (total_bid_usd - total_ask_usd) / depth_sum if depth_sum > 0 else 0.0

        return {
            "best_bid": round(best_bid, 2),
            "best_ask": round(best_ask, 2),
            "mid_price": round(mid_price, 2),
            "spread": round(spread, 2),
            "spread_pct": round(spread_pct, 6),
            "top3_bid_lots": top3_bid_lots,
            "top3_ask_lots": top3_ask_lots,
            "total_bid_lots": total_bid_lots,
            "total_ask_lots": total_ask_lots,
            "bid_depth_usd": round(total_bid_usd, 2),
            "ask_depth_usd": round(total_ask_usd, 2),
            "imbalance": round(imbalance, 4)
        }