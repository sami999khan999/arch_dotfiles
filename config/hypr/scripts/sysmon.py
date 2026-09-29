#!/usr/bin/env python3
# sysmon.py — minimal system monitor for the rice (Tokyo Night). q / Esc to quit.
import os, subprocess
from datetime import datetime

INTERVAL = 1.5
TOP_APPS = 8
PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS = os.sysconf("SC_CLK_TCK")

from panelkit import (Panel, run, CARD, CHROME, FG, DIM, ACCENT, GREEN, YELLOW, RED, TRACK, BOLD, RESET,
                      fit, frame, card, spread, header, section, hints)

def level(p): return GREEN if p < 60 else YELLOW if p < 85 else RED

def bar(p, width):
    n = round(width * min(p, 100) / 100)
    return level(p) + "━" * n + TRACK + "━" * (width - n) + RESET

def size(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.1f} {unit}" if unit in ("GB", "TB") else f"{b:.0f} {unit}"
        b /= 1024

def cpu_ticks():
    with open("/proc/stat") as f:
        v = list(map(int, f.readline().split()[1:]))
    return sum(v), v[3] + v[4]  # total, idle+iowait

def cpu_temp():
    base = "/sys/class/hwmon"
    for h in os.listdir(base):
        try:
            if open(f"{base}/{h}/name").read().strip() in ("coretemp", "k10temp"):
                return int(open(f"{base}/{h}/temp1_input").read()) / 1000
        except OSError:
            pass
    return None

def memory():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0]) * 1024
    return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]

def disk():
    s = os.statvfs("/")
    total = s.f_blocks * s.f_frsize
    return total - s.f_bavail * s.f_frsize, total

def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=1).stdout
        util, temp = map(float, out.split("\n")[0].split(","))
        return util, temp
    except Exception:
        return None

def app_name(pid, comm):
    if len(comm) < 15:  # comm is cut at 15 chars; only then fall back to the executable name
        return comm
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe")).removesuffix(" (deleted)")
    except OSError:
        return comm

def processes():
    procs = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
        except OSError:
            continue
        comm = raw[raw.index("(") + 1:raw.rindex(")")]
        fields = raw[raw.rindex(")") + 2:].split()
        rss = int(fields[21]) * PAGE
        if rss == 0:  # kernel threads
            continue
        procs[pid] = (app_name(pid, comm), int(fields[11]) + int(fields[12]), rss)
    return procs

def top_apps(prev, cur, dtotal):
    apps = {}
    for pid, (name, ticks, rss) in cur.items():
        cpu = (ticks - prev[pid][1]) / dtotal * 100 if pid in prev and dtotal else 0
        a = apps.setdefault(name, [0.0, 0])
        a[0] += cpu
        a[1] += rss
    return sorted(apps.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)

def cpu_model():
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                name = line.split(":", 1)[1]
                for junk in ("Intel(R) Core(TM) ", "AMD ", " CPU", "(R)", "(TM)"):
                    name = name.replace(junk, "")
                return name.split("@")[0].strip()
    return "CPU"

def uptime():
    m = int(float(open("/proc/uptime").read().split()[0]) // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"

ICONS = {"CPU": "\U000f0ee0", "Memory": "\U000f035b", "Disk": "\U000f02ca", "GPU": "\U000f08ae"}
MODEL = cpu_model()

def short(b):
    """Compact size for a narrow card: 4.6G, 916M."""
    n, unit = size(b).split()
    return f"{float(n):.0f}{unit[0]}" if float(n) >= 100 or unit in ("B", "KB", "MB") else f"{n}{unit[0]}"

def load():
    one, five, _, procs = open("/proc/loadavg").read().split()[:4]
    return one, five, procs.split("/")[1]

def render(cpu, temp, mem, dsk, g, apps):
    """The card's screen text."""
    cols, rows, w, pad = frame(84)
    narrow = w < 64  # a Control Center card: short details, no memory bars
    body = []

    def line(left, right=""):
        body.append(spread(left, right, w))

    def metric(name, pct, detail, brief):
        detail = brief if narrow else detail
        dw = 10 if narrow else 19
        bw = max(w - 17 - dw - 2, 6)
        line(f"{ACCENT}{ICONS[name]}{RESET}  {FG}{name:<8}{RESET}{bar(pct, bw)}  {level(pct)}{BOLD}{pct:>3.0f}%{RESET}",
             f"{DIM}{detail}{RESET}")

    t = f"{temp:.0f}°C" if temp else ""
    body.append(section("Resources", w))
    metric("CPU", cpu, t, t)
    metric("Memory", mem[0] / mem[1] * 100, f"{size(mem[0])} / {size(mem[1])}", f"{short(mem[0])}/{short(mem[1])}")
    metric("Disk", dsk[0] / dsk[1] * 100, f"{size(dsk[0])} / {size(dsk[1])}", f"{short(dsk[0])}/{short(dsk[1])}")
    if g:
        metric("GPU", g[0], f"{g[1]:.0f}°C", f"{g[1]:.0f}°C")
    body.append("")
    body.append(section("Apps", w, "cpu" + " " * (4 if narrow else 17) + "memory"))
    room = max(rows - CHROME - len(body), 1)
    biggest = max((m for _, (_, m) in apps[:1]), default=1)
    for name, (c, m) in apps[:min(TOP_APPS, room)]:
        name = name[:1].upper() + name[1:]
        n = max(round(12 * m / biggest), 1)
        mbar = ACCENT + "━" * n + TRACK + "━" * (12 - n) + RESET
        if narrow:
            line(f"{FG}{fit(name, w - 18)}{RESET}", f"{level(c)}{c:>5.1f}%{RESET}  {DIM}{size(m):>8}{RESET}")
        else:
            line(f"{FG}{fit(name, w - 36)}{RESET}", f"{level(c)}{c:>5.1f}%{RESET}  {mbar} {DIM}{size(m):>8}{RESET}")

    one, five, procs = load()
    info = f"{DIM}load {RESET}{FG}{one}{RESET}{DIM} · {five}   {procs} processes{RESET}"
    foot = info if CARD else spread(hints([("q", "close")], w), info, w)
    head = header("\U000f035b", "System", f"{MODEL} · up {uptime()} · {datetime.now():%H:%M}", w)
    return card(head, body, foot, rows, pad, center=True)

class SystemPanel(Panel):
    interval = INTERVAL

    def __init__(self):
        self.t0, self.i0 = cpu_ticks()
        self.p0 = processes()
        self.stats = None

    def tick(self):
        t1, i1 = cpu_ticks()
        p1 = processes()
        dt = t1 - self.t0
        cpu = (1 - (i1 - self.i0) / dt) * 100 if dt else 0
        self.stats = (cpu, cpu_temp(), memory(), disk(), gpu(), top_apps(self.p0, p1, dt))
        self.t0, self.i0, self.p0 = t1, i1, p1

    def draw(self, w, h):
        return render(*self.stats) if self.stats else ""

    def key(self, k):
        if k in ("q", "ESC"):
            return "quit"


if __name__ == "__main__":
    import time
    time.sleep(0.4)  # a first CPU sample needs a moment between two readings
    run(SystemPanel())
