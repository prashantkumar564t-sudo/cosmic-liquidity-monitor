#!/data/data/com.termux/files/usr/bin/bash
cd /data/data/com.termux/files/home/cosmic-liquidity-monitor
python3 -u fetch_option_chain.py --interval 5 --underlying BTC,ETH
