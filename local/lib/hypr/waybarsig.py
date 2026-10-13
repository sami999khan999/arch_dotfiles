# waybarsig.py — send waybar a refresh signal (SIGRTMIN+n: a custom module's "signal"), without starting
# pkill (a process that reads all of /proc) each time. waybar's pid is looked up once and checked
# before each use (it restarts after a crash). Used by wsbar.py, notifications.py and codesync.
import os, signal

_pid = None


def _find():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip() == "waybar":
                        return int(pid)
            except OSError:
                pass
    return None


def send(n):
    """SIGRTMIN+n to waybar, if it runs."""
    global _pid
    for _ in range(2):   # the cached pid, then a fresh look if waybar was restarted
        try:
            if _pid is not None:
                with open(f"/proc/{_pid}/comm") as f:
                    if f.read().strip() == "waybar":
                        os.kill(_pid, signal.SIGRTMIN + n)
                        return
        except OSError:
            pass
        _pid = _find()
        if _pid is None:
            return
