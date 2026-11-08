import time
from playwright.sync_api import sync_playwright

# अगर आप ब्राउज़र में किसी खास ट्रेडिंग पेज पर जाते हैं (जैसे cosmic.trade/trade), तो वह URL यहाँ डालें:
TARGET_URL = "https://cosmic.trade"

def run_sniffer():
    print(f"[*] Sniffing WebSocket frames from {TARGET_URL}...")
    
    with sync_playwright() as p:
        # Browser open (विंडो दिखेगी ताकि आप देख सकें कि पेज लोड हो रहा है)
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_websocket(ws):
            print(f"\n[WEBSOCKET OPENED] -> {ws.url}")

            # 1. ब्राउज़र ने सर्वर को क्या भेजा (Subscription Payload)
            def on_frame_sent(payload):
                print(f"\n---> [CLIENT SENT / SUBSCRIBE]:\n{payload}\n")

            # 2. सर्वर से क्या डेटा आया (Raw Orderbook / Ticker)
            def on_frame_received(payload):
                text = str(payload)
                # बहुत लंबे डेटा को पहले 300 अक्षरों तक ही दिखाएंगे
                preview = text if len(text) < 300 else text[:300] + "..."
                print(f"<--- [SERVER RECEIVED]: {preview}")

            ws.on("framesent", on_frame_sent)
            ws.on("framereceived", on_frame_received)

        page.on("websocket", handle_websocket)

        print("[*] Navigating to page (20 seconds window)...")
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            # 20 सेकंड इंतज़ार करेंगे ताकि लाइव टिकर/ऑर्डरबुक के फ्रेम्स आ जाएं
            time.sleep(20)
        except Exception as e:
            print(f"[!] Notice: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_sniffer()