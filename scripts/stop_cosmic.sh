#!/data/data/com.termux/files/usr/bin/bash
# stop_cosmic.sh
# Gracefully stops Cosmic Liquidity Monitor so DuckDB commits and flushes cleanly
SESSION="cosmic_monitor"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sending SIGINT (Ctrl+C) to $SESSION for graceful DuckDB shutdown..."
    tmux send-keys -t "$SESSION" C-c
    # Allow up to 6 seconds for DuckDB connection close & buffer sync
    for i in {1..6}; do
        if ! pgrep -f "fetch_option_chain.py" >/dev/null; then
            break
        fi
        sleep 1
    done
    tmux kill-session -t "$SESSION" 2>/dev/null
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $SESSION stopped cleanly."
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No active $SESSION session found."
fi
