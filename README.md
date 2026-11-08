# Cosmic BTC Liquidity Monitor & Order Book Profiler

A lightweight, high-frequency Level-2 order book telemetry engine for Cosmic Trade (`BTCUSDT` perpetual contracts). Engineered for cross-platform execution (Windows, Linux, Termux on Android) with continuous process memory profiling and conditional anomaly logging.

---

## Architecture Highlights

- **Native Binary Streaming**: Direct WebSocket connection decoding MsgPack payloads from Cosmic feeds.
- **Contract & Lot Normalization**: Tracks liquidity depth in standard futures lots (1 Lot = 0.001 BTC).
- **Execution Ladder Simulator**: Models fill prices and slippage for $10,000 and $50,000 market orders.
- **Cross-Platform Telemetry**: Working Set RAM tracking via `ctypes` on Windows and `/proc/self/status` on Linux/Termux.
- **Selective Anomaly Logging**: Streams clean tabular CSV records continuously while dumping raw JSON snapshots only during liquidity drops or imbalance spikes.

---

## Directory Layout

```text
├── src/
│   ├── config.py           # Endpoints, symbols, intervals
│   ├── cosmic_api.py       # WebSocket + MsgPack client
│   ├── orderbook.py        # Spread, depth and imbalance math
│   ├── slippage.py         # Ladder walking simulation
│   ├── storage.py          # Dual CSV & Anomaly JSON sink
│   └── main.py             # Event loop with drift correction
├── monitor/
│   └── ram_monitor.py      # OS memory tracker
├── data/
│   ├── raw/                # Anomaly JSON snapshots
│   └── analysis/           # Metrics CSV
├── logs/                   # System telemetry
├── generate_report.py      # Benchmark reporter
└── launcher.py             # Process launcher