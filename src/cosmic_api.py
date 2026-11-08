import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import json
import time
import threading
import msgpack
import websockets
from src.config import WS_URL, SYMBOL

class CosmicWSClient:
    def __init__(self, ws_url: str = WS_URL, symbol: str = SYMBOL):
        self.ws_url = ws_url
        self.symbol = symbol
        self.depth_symbol = f"{symbol}:depth"
        
        self.latest_bids = []
        self.latest_asks = []
        self.last_update_ts = 0.0
        self.running = False
        self.thread = None

    async def _listen(self):
        # Cosmic Trade headers (Origin is critical for DEX WebSockets)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://cosmic.trade"
        }
        
        while self.running:
            try:
                print(f"[*] Connecting to {self.ws_url}...")
                
                # Handling header argument across different websockets library versions
                try:
                    ws_ctx = websockets.connect(self.ws_url, additional_headers=headers)
                except TypeError:
                    ws_ctx = websockets.connect(self.ws_url, extra_headers=headers)

                async with ws_ctx as ws:
                    print("[+] WebSocket Connected successfully!")
                    
                    # 1. Subscribe to depth
                    sub_depth = json.dumps({"action": "subscribe", "symbols": [self.depth_symbol]})
                    await ws.send(sub_depth)
                    print(f"[->] Sent: {sub_depth}")
                    
                    # 2. Subscribe to base ticker as well
                    sub_ticker = json.dumps({"action": "subscribe", "symbols": [self.symbol]})
                    await ws.send(sub_ticker)
                    
                    while self.running:
                        raw_data = await ws.recv()
                        self.last_update_ts = time.time()
                        
                        # Decode MsgPack
                        if isinstance(raw_data, bytes):
                            payload = msgpack.unpackb(raw_data, raw=False)
                        else:
                            payload = json.loads(raw_data)

                        if isinstance(payload, dict):
                            # Debug: first time payload structure dekhne ke liye
                            keys = list(payload.keys())
                            if "b" in payload:
                                self.latest_bids = [[float(p), float(q)] for p, q in payload["b"]]
                            if "a" in payload:
                                self.latest_asks = [[float(p), float(q)] for p, q in payload["a"]]
                                
            except Exception as e:
                print(f"[!] WebSocket Connection Error: {type(e).__name__} -> {e}")
                await asyncio.sleep(2.0)

    def _run_event_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._listen())

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def get_latest_orderbook(self):
        return {
            "success": len(self.latest_bids) > 0 or len(self.latest_asks) > 0,
            "bids": self.latest_bids.copy(),
            "asks": self.latest_asks.copy(),
            "timestamp": self.last_update_ts
        }

if __name__ == "__main__":
    client = CosmicWSClient()
    client.start()
    
    # 6 seconds sampling
    for i in range(6):
        time.sleep(1)
        data = client.get_latest_orderbook()
        print(f"[{i+1}] Bids: {len(data['bids'])} | Asks: {len(data['asks'])}")
        if data["success"] and data["bids"]:
            print(f"    Sample Bid: {data['bids'][0]}")
    client.stop()