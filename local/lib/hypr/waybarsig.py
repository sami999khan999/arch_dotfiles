# waybarsig.py — send waybar a refresh signal (SIGRTMIN+n: a custom module's "signal"), without starting
# pkill (a process that reads all of /proc) each time. waybar runs once per screen (scripts/waybars.py):
# the pids are looked up once and checked before each use (a bar restarts after a crash, a screen comes
# or goes). Used by wsbar.py, notifications.py and codesync.
import os, signal

_pids = []


def _is_waybar(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip() == "waybar"
    except OSError:
        return False


def _find():
    return [int(pid) for pid in os.listdir("/proc") if pid.isdigit() and _is_waybar(pid)]


def send(n):
    """SIGRTMIN+n to every waybar that runs."""
    global _pids
    if not _pids or not all(_is_waybar(pid) for pid in _pids):   # a bar was restarted: a fresh look
        _pids = _find()
    for pid in _pids:
        try:
            os.kill(pid, signal.SIGRTMIN + n)
        except OSError:
            pass
