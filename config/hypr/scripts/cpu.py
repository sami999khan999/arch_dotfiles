#!/usr/bin/env python3
"""cpu.py — waybar module (custom/cpu, in the stats): the CPU's load, and its card.

The bar shows the load ("󰍛 74%", red from 90 %, as waybar's cpu module did). Its card (bartips): the
model and thread count, the load over the last few minutes, temperature, clock and load average, a
column per thread, and the apps using the most (usage.py: each app's cgroup, as System → Apps counts
it). Every DELAY s it reads /proc/stat, the sensor and the clocks; the apps every APPS_EVERY s (a walk
over the cgroups). Click: System.
"""
import glob, json, os, sys, time
from collections import deque

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
from bartip import card, level, temp_colour
import usage

DELAY = 3
APPS_EVERY = 6
HISTORY = 60          # readings in the graph: 3 minutes
ICON = "\U000f035b"   # chip


def ticks():
    """[(busy, total)] for the whole CPU first, then each thread."""
    out = []
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                break
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + v[4]
            out.append((sum(v) - idle, sum(v)))
    return out


def model():
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                name = line.split(":", 1)[1]
                for junk in ("(R)", "(TM)", "CPU", "Intel Core", "Processor", "with Radeon Graphics"):
                    name = name.replace(junk, " ")
                return " ".join(name.split("@")[0].split())
    return "CPU"


def sensor():
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(f"{h}/name").read().strip() in ("coretemp", "k10temp", "zenpower"):
                return f"{h}/temp1_input"
        except OSError:
            pass
    return None


def read_num(path, scale=1):
    try:
        return int(open(path).read()) / scale
    except (OSError, ValueError, TypeError):
        return None


def clock():
    """The threads' average clock now, GHz."""
    v = [read_num(p, 1e6) for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")]
    v = [x for x in v if x]
    return sum(v) / len(v) if v else None


def main():
    name, temp_file = model(), sensor()
    threads = os.cpu_count() or 1
    history = deque(maxlen=HISTORY)
    sampler, top, top_at = usage.Sampler(), [], 0
    sampler.sample()   # the first reading only sets the start
    before = ticks()
    while True:
        time.sleep(DELAY)
        now = ticks()
        pct = [(b1 - b0) / (t1 - t0) * 100 if t1 > t0 else 0 for (b0, t0), (b1, t1) in zip(before, now)]
        before = now
        total, cores = pct[0], pct[1:]
        history.append(round(total, 1))
        if time.monotonic() - top_at > APPS_EVERY:
            top = [(r["name"], r["cpu"] / threads) for r in sampler.sample()[:4] if r["cpu"] >= 1]
            top_at = time.monotonic()
        t, ghz, load = read_num(temp_file, 1000), clock(), os.getloadavg()
        rows = []
        if t is not None:
            rows.append(["temperature", f"{t:.0f}°C", temp_colour(t, 75, 90)])
        if ghz:
            rows.append(["clock", f"{ghz:.2f} GHz"])
        rows.append(["load average", f"{load[0]:.1f} · {load[1]:.1f} · {load[2]:.1f}"])
        blocks = [{"type": "head", "icon": ICON, "title": "CPU", "detail": f"{name} · {threads} threads",
                   "right": f"{total:.0f}%", "color": level(total)},
                  {"type": "spark", "values": list(history)},
                  {"type": "kv", "rows": rows},
                  {"type": "bars", "values": [round(c) for c in cores], "labels": [str(i) for i in range(len(cores))]}]
        if top:
            blocks += [{"type": "rule"},
                       {"type": "table", "rows": [[n, f"{c:.0f}%"] for n, c in top]}]
        blocks.append({"type": "hints", "text": "Click: what's using it (System)"})
        print(json.dumps({"text": f"{ICON}{total:3.0f}%", "tooltip": card(blocks),
                          "class": "critical" if total >= 90 else level(total)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
