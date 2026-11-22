#!/data/data/com.termux/files/usr/bin/bash
# start_cosmic.sh
# Dual-Window automated launch for Cosmic Liquidity Monitor
SESSION="cosmic_monitor"
PROJECT_DIR="/data/data/com.termux/files/home/cosmic-liquidity-monitor"

mkdir -p "$PROJECT_DIR/logs"

# Ensure no orphaned collector process holds the DuckDB lock
pkill -f "fetch_option_chain.py" 2>/dev/null
sleep 1

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Session $SESSION already exists. Restarting..."
    tmux kill-session -t "$SESSION" 2>/dev/null
    sleep 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $SESSION with BTC & ETH options ingestion..."
tmux new-session -d -s "$SESSION"
tmux send-keys -t "$SESSION" "cd $PROJECT_DIR && python3 -u fetch_option_chain.py --interval 5 --underlying BTC,ETH >> logs/cosmic_monitor.log 2>&1" C-m
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Session $SESSION started successfully."
