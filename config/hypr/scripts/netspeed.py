#!/usr/bin/env python3
"""netspeed.py — waybar module (custom/netspeed, in the stats): the network's speed as two sparklines.

Every DELAY s it reads /proc/net/dev (one small file, no process started) and prints one JSON line: two
small lines, download over upload, each the last SPARK readings as block characters (▁▂▃▅▇: the trend
over 20 s, each scaled to its own peak in that time, never below FLOOR so a quiet line stays flat) and
the speed now, download in the accent colour, upload in amber (both see-through, quiet); all dimmed while
next to nothing moves. The speed is always four characters ("  1K", "736K", "1.2M"): the chip never
changes width.
It adds up every physical interface (the cable, Wi-Fi, a phone's hotspot: the download manager uses two
at once), not the virtual ones (Docker's bridges, VPN tunnels, lo). The tooltip has the speeds and each
interface's share. Click: the network panel.
"""
import json, os, sys, time
from collections import deque

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
try:   # the colour theme (local/bin/theme): colours here are Tokyo Night's, recolored to the active theme's
    from themelib import recolor
except ImportError:
    def recolor(text, tid=None):
        return text


DELAY = 2            # s between readings
IDLE = 2 * 1024      # B/s: below this, both ways, the module dims
SPARK = 10           # readings in each sparkline
FLOOR = 64 * 1024    # B/s: the least a sparkline is scaled to
BLOCKS = "▁▂▃▄▅▆▇█"
ACCENT, AMBER, MUTED = (recolor(c) for c in ("#6b8fe0", "#e0af68", "#565f89"))


def physical():
    """Interfaces backed by a device (/sys/class/net/<name>/device): not bridges, tunnels or lo."""
    try:
        return {n for n in os.listdir("/sys/class/net") if os.path.exists(f"/sys/class/net/{n}/device")}
    except OSError:
        return set()


def counters(names):
    """{interface: (bytes received, bytes sent)} for the given interfaces."""
    out = {}
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, data = line.split(":", 1)
            name = name.strip()
            if name in names:
                fields = data.split()
                out[name] = (int(fields[0]), int(fields[8]))
    return out


def short(rate):
    """B/s in four characters: "  0K", " 12K", "380K", "1.2M", " 45M", "1.1G"."""
    for unit, size in (("G", 1 << 30), ("M", 1 << 20)):
        if rate >= size * 0.95:
            v = rate / size
            return f"{v:.1f}{unit}" if v < 9.95 else f"{v:3.0f}{unit}"
    return f"{rate / 1024:3.0f}K"


def spark(history):
    """The readings as block characters, scaled to their peak (FLOOR at least)."""
    top = max(max(history), FLOOR)
    return "".join(BLOCKS[min(len(BLOCKS) - 1, int(v / top * len(BLOCKS)))] for v in history)


def long(rate):
    """B/s for the tooltip: "380 KB/s", "1.2 MB/s"."""
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if rate >= size:
            return f"{rate / size:.1f} {unit}/s"
    return f"{rate:.0f} B/s"


def main():
    names = physical()
    before, t0 = counters(names), time.monotonic()
    downs, ups = deque([0] * SPARK, maxlen=SPARK), deque([0] * SPARK, maxlen=SPARK)
    while True:
        time.sleep(DELAY)
        names = physical()   # a hotspot plugged in, a dongle pulled out
        now, t1 = counters(names), time.monotonic()
        dt = max(t1 - t0, 0.001)
        rates = {n: ((now[n][0] - before[n][0]) / dt, (now[n][1] - before[n][1]) / dt)
                 for n in now if n in before}
        before, t0 = now, t1
        down = sum(r[0] for r in rates.values())
        up = sum(r[1] for r in rates.values())
        downs.append(down)
        ups.append(up)
        lines = ["<b>Network speed</b>",
                 f"{long(down)}  <span alpha='55%'>download</span>",
                 f"{long(up)}  <span alpha='55%'>upload</span>"]
        busy = {n: r for n, r in rates.items() if r[0] + r[1] >= IDLE}
        if len(busy) > 1:   # two networks at once: what each carries
            lines += [f"{long(r[0])} ↓ {long(r[1])} ↑  <span alpha='55%'>{n}</span>"
                      for n, r in sorted(busy.items())]
        lines.append("<span alpha='55%'>Click: networks</span>")
        idle = down < IDLE and up < IDLE
        d_col, u_col = (MUTED, MUTED) if idle else (ACCENT, AMBER)
        # the graphs see-through (alpha): the hues stay, quieter than the numbers beside them
        text = (f"<span line_height='1.0'><span color='{d_col}' alpha='55%'>{spark(downs)}</span> {short(down)}\n"
                f"<span color='{u_col}' alpha='55%'>{spark(ups)}</span> {short(up)}</span>")
        print(json.dumps({"text": text, "tooltip": "\n".join(lines), "class": "idle" if idle else "moving"},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
