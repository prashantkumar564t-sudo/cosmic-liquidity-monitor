"""
monitor/ram_monitor.py
Cross-Platform Process Memory Tracker (Windows + Termux/Linux Standard Library)
"""

import sys
import os

def get_process_memory_mb(pid=None) -> float:
    """Returns Working Set (Windows) or VmRSS (Linux/Termux) in MB."""
    if pid is None:
        pid = os.getpid()

    # 1. Windows Ctypes Implementation
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('cb', wintypes.DWORD),
                    ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t),
                ]

            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
            )
            if not handle:
                return 0.0

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            success = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            ctypes.windll.kernel32.CloseHandle(handle)

            if success:
                return round(counters.WorkingSetSize / (1024 * 1024), 2)
            return 0.0
        except Exception:
            return 0.0

    # 2. Linux / Android Termux Implementation
    else:
        try:
            # /proc/self/status is the fastest and most accurate on Linux/Android
            path = f"/proc/{pid}/status"
            if not os.path.exists(path):
                path = "/proc/self/status"

            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # Format: VmRSS:     35400 kB
                        parts = line.split()
                        return round(float(parts[1]) / 1024.0, 2)
            
            # Fallback using resource module
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)
        except Exception:
            return 0.0

if __name__ == "__main__":
    print(f"Platform: {sys.platform} | Process PID: {os.getpid()} | Current RAM: {get_process_memory_mb()} MB")