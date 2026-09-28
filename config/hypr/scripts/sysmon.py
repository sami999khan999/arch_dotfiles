#!/usr/bin/env python3
# sysmon.py — minimal system monitor for the rice (Tokyo Night). q / Esc to quit.
import os, sys, time, select, shutil, termios, tty, subprocess
from datetime import datetime

INTERVAL = 1.5
TOP_APPS = 8
PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS = os.sysconf("SC_CLK_TCK")

def rgb(h): return f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
FG, DIM, ACCENT = rgb("#c0caf5"), rgb("#565f89"), rgb("#7aa2f7")
GREEN, YELLOW, RED, TRACK = rgb("#9ece6a"), rgb("#e0af68"), rgb("#f7768e"), rgb("#292e42")
BOLD, RESET = "\033[1m", "\033[0m"

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

def visible_len(text):
    out, skip = 0, False
    for ch in text:
        if ch == "\033": skip = True
        elif skip and ch == "m": skip = False
        elif not skip: out += 1
    return out

ICONS = {"CPU": "\U000f0ee0", "Memory": "\U000f035b", "Disk": "\U000f02ca", "GPU": "\U000f08ae"}
MODEL = cpu_model()

def render(cpu, temp, mem, dsk, g, apps):
    cols, rows = shutil.get_terminal_size()
    w = min(cols - 6, 76)
    pad = " " * max((cols - w) // 2, 0)
    out = []

    def line(left, right=""):
        gap = max(w - visible_len(left) - visible_len(right), 1)
        out.append(pad + left + " " * gap + right)

    def section(title):
        out.append(pad + f"{ACCENT}{BOLD}{title}{RESET} {TRACK}{'─' * (w - len(title) - 1)}{RESET}")
        out.append("")

    narrow = w < 64  # e.g. a Control Center pane: drop the detail column, keep bars readable

    def metric(name, pct, detail):
        bw = max(w - 18, 6) if narrow else w - 40
        if narrow:
            detail = ""
        line(f"{ACCENT}{ICONS[name]}{RESET}  {FG}{name:<8}{RESET}{bar(pct, bw)}  {level(pct)}{BOLD}{pct:>3.0f}%{RESET}",
             f"{DIM}{detail}{RESET}")

    line(f"{FG}{BOLD}{MODEL}{RESET}", f"{DIM}up {uptime()}  ·  {RESET}{FG}{datetime.now():%H:%M}{RESET}")
    out.append("")
    section("System")
    metric("CPU", cpu, f"{temp:.0f}°C" if temp else "")
    metric("Memory", mem[0] / mem[1] * 100, f"{size(mem[0])} / {size(mem[1])}")
    metric("Disk", dsk[0] / dsk[1] * 100, f"{size(dsk[0])} / {size(dsk[1])}")
    if g:
        metric("GPU", g[0], f"{g[1]:.0f}°C")
    out.append("")
    section("Apps")
    room = max(rows - len(out) - 4, 1)
    biggest = max((m for _, (_, m) in apps[:1]), default=1)
    for name, (c, m) in apps[:min(TOP_APPS, room)]:
        name = name[:1].upper() + name[1:]
        n = max(round(12 * m / biggest), 1)
        mbar = ACCENT + "━" * n + TRACK + "━" * (12 - n) + RESET
        if narrow:
            line(f"{FG}{name[:max(w - 17, 4)]}{RESET}", f"{level(c)}{c:>5.1f}%{RESET} {DIM}{size(m):>8}{RESET}")
        else:
            line(f"{FG}{name[:w - 40]}{RESET}",
                 f"{level(c)}{c:>5.1f}%{RESET}   {mbar} {DIM}{size(m):>9}{RESET}")
    out.append("")
    out.append(pad + " " * ((w - 7) // 2) + f"{DIM}q close{RESET}")

    top = max((rows - len(out)) // 2, 0)
    sys.stdout.write("\033[H\033[2J" + "\n" * top + "\n".join(out))
    sys.stdout.flush()

def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l")  # alt screen, hide cursor
    try:
        t0, i0 = cpu_ticks()
        p0 = processes()
        wait = 0.4
        while True:
            r, _, _ = select.select([sys.stdin], [], [], wait)
            if r and sys.stdin.read(1) in ("q", "\x1b"):
                break
            t1, i1 = cpu_ticks()
            p1 = processes()
            dt = t1 - t0
            cpu = (1 - (i1 - i0) / dt) * 100 if dt else 0
            render(cpu, cpu_temp(), memory(), disk(), gpu(), top_apps(p0, p1, dt))
            t0, i0, p0 = t1, i1, p1
            wait = INTERVAL
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?7h\033[?25h\033[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

if __name__ == "__main__":
    main()
