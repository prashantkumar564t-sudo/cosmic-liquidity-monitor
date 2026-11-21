#!/usr/bin/env python3
"""notify_telegram.py
Lightweight Telegram Notification Dispatcher for Cosmic Liquidity Monitor.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
If missing, logs info and exits with code 0 (fail-safe for CI/CD runs).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_file_info(file_path_str: str | None) -> tuple[int | None, str | None]:
    """Helper to get row count and human-readable size for a parquet/data file."""
    if not file_path_str:
        return None, None
    p = Path(file_path_str)
    if not p.exists():
        return None, None
    size_bytes = p.stat().st_size
    size_str = f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes >= 1024 * 1024 else f"{size_bytes / 1024:.1f} KB"
    rows = None
    try:
        import duckdb

        con = duckdb.connect()
        try:
            con.execute("SET TimeZone = 'UTC';")
        except Exception:
            pass
        out_sql = str(p.resolve()).replace("\\", "/")
        res = con.query(f"SELECT COUNT(*) FROM read_parquet('{out_sql}')").fetchone()
        if res:
            rows = int(res[0])
    except Exception:
        pass
    return rows, size_str


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return resp_data.get("ok", False)
    except Exception as e:
        print(f"[!] Warning: Failed to send Telegram message: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Cosmic Telegram Run Notification Dispatcher")
    parser.add_argument("--date", type=str, default=None, help="Run date (default: today IST)")
    parser.add_argument("--duration", type=str, default="300 min", help="Run duration string")
    parser.add_argument("--rows", type=str, default=None, help="Total records captured")
    parser.add_argument("--size", type=str, default=None, help="Parquet file size string")
    parser.add_argument(
        "--drive-status",
        type=str,
        default="Verified / Synced",
        help="Google Drive upload status",
    )
    parser.add_argument(
        "--parquet-file",
        type=str,
        default=None,
        help="Optional path to exported parquet file for auto-detecting size & rows",
    )
    parser.add_argument("--message", type=str, default=None, help="Optional raw custom message override")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram secrets not configured — skipping notification")
        sys.exit(0)

    # Auto-inspect parquet if provided
    detected_rows, detected_size = get_file_info(args.parquet_file)
    final_rows = args.rows or (f"{detected_rows:,}" if detected_rows is not None else "N/A")
    final_size = args.size or detected_size or "N/A"

    ist_tz = timezone(timedelta(hours=5, minutes=30))
    date_str = args.date or datetime.now(ist_tz).strftime("%Y-%m-%d")

    if args.message:
        text = args.message
    else:
        text = (
            "🌌 <b>[COSMIC LIQUIDITY MONITOR]</b>\n"
            "🚀 <b>Evening Run Report</b>\n\n"
            f"📅 <b>Date:</b> {date_str} (IST)\n"
            f"⏱ <b>Duration:</b> {args.duration}\n"
            f"📊 <b>Total Rows:</b> {final_rows}\n"
            f"📦 <b>Parquet Size:</b> {final_size}\n"
            f"☁️ <b>Drive Recheck:</b> {args.drive_status}\n"
        )

    success = send_telegram_message(token, chat_id, text)
    if success:
        print("[+] Telegram notification sent successfully.")
    else:
        print("[!] Telegram notification dispatch encountered an error, exiting safely.")

    sys.exit(0)


if __name__ == "__main__":
    main()
