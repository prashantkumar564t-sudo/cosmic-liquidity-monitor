import sys
from src.main import run_monitor

if __name__ == "__main__":
    # Command-line arg se snapshots count pass kar sakte hain, default: 300
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    run_monitor(max_snapshots=count)