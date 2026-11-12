#!/usr/bin/env python3
"""gpu.py — waybar module (custom/gpu, in the stats): the graphics card's load, and its card.

NVIDIA: one `nvidia-smi … -l 2` left running (it prints a line every 2 s: no process started each time);
the apps on it from `nvidia-smi --query-compute-apps` every APPS_EVERY s. AMD: amdgpu's files in sysfs
(busy %, video memory, temperature, power, fan). Anything else (Intel's integrated graphics has no load
to read without root): the module stays hidden and this sleeps for good.
The bar shows the load ("󰢮 23%"); the card (bartips): the name, temperature, video memory, power, load
and fan, a chip drawn with a gauge for each, and the apps using it. Click: the System panel.
"""
import glob, json, os, subprocess, sys, threading, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from bartip import card, level, temp_colour

DELAY = 2
APPS_EVERY = 10
ICON = "\U000f08ae"   # expansion card
TEMP, VRAM, POWER, LOAD, FAN = "\U000f050f", "\U000f061a", "\U000f140b", "\U000f029a", "\U000f0210"
FIELDS = "name,temperature.gpu,memory.used,memory.total,power.draw,power.limit,utilization.gpu,fan.speed"


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def nvidia():
    """Readings from nvidia-smi's loop: {name, temp, used, total (MiB), power, limit (W), load, fan}."""
    p = subprocess.Popen(["nvidia-smi", f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits", "-l", str(DELAY)],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        f = [x.strip() for x in line.split(",")]
        if len(f) == 8:
            yield {"name": f[0], "temp": num(f[1]), "used": num(f[2]), "total": num(f[3]),
                   "power": num(f[4]), "limit": num(f[5]), "load": num(f[6]), "fan": num(f[7])}


def nvidia_apps():
    out = subprocess.run(["nvidia-smi", "--query-compute-apps=process_name,used_memory",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        if name:
            rows.append((os.path.basename(name.strip()), num(mem) or 0))
    return sorted(rows, key=lambda r: -r[1])[:4]


def amd_card():
    for dev in glob.glob("/sys/class/drm/card*/device"):
        if os.path.exists(f"{dev}/gpu_busy_percent"):
            return dev
    return None


def read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def amd(dev):
    hw = (glob.glob(f"{dev}/hwmon/hwmon*") or [None])[0]
    while True:
        pwm, pwm_max = num(read(f"{hw}/pwm1")) if hw else None, num(read(f"{hw}/pwm1_max")) if hw else None
        power = num(read(f"{hw}/power1_average")) or num(read(f"{hw}/power1_input")) if hw else None
        t = num(read(f"{hw}/temp1_input")) if hw else None
        yield {"name": "AMD Radeon", "temp": t / 1000 if t else None,
               "used": (num(read(f"{dev}/mem_info_vram_used")) or 0) / 2 ** 20,
               "total": (num(read(f"{dev}/mem_info_vram_total")) or 0) / 2 ** 20,
               "power": power / 1e6 if power else None, "limit": (num(read(f"{hw}/power1_cap")) or 0) / 1e6 if hw else None,
               "load": num(read(f"{dev}/gpu_busy_percent")),
               "fan": pwm / pwm_max * 100 if pwm is not None and pwm_max else None}
        time.sleep(DELAY)


def pct(a, b):
    return a / b * 100 if a is not None and b else 0


def main():
    if subprocess.run(["sh", "-c", "command -v nvidia-smi"], capture_output=True).returncode == 0:
        readings = nvidia()
        apps = nvidia_apps
    elif (dev := amd_card()):
        readings, apps = amd(dev), lambda: []
    else:
        print(json.dumps({"text": ""}), flush=True)   # no card to read: hidden
        threading.Event().wait()
        return
    top, top_at = [], 0
    for r in readings:
        if time.monotonic() - top_at > APPS_EVERY:
            top, top_at = apps(), time.monotonic()
        load, vram, power = r["load"] or 0, pct(r["used"], r["total"]), pct(r["power"], r["limit"])
        rows = []
        if r["temp"] is not None:
            rows.append(["temperature", f"{r['temp']:.0f}°C", temp_colour(r["temp"], 70, 83)])
        if r["total"]:
            rows.append(["video memory", f"{r['used']:.0f} of {r['total']:.0f} MiB", level(vram, 80, 95)])
        if r["power"] is not None:
            rows.append(["power", f"{r['power']:.0f} W" + (f" of {r['limit']:.0f} W" if r["limit"] else ""), level(power)])
        rows.append(["load", f"{load:.0f}%", level(load)])
        if r["fan"] is not None:
            rows.append(["fan", f"{r['fan']:.0f}%"])
        meters = [{"icon": LOAD, "value": load}, {"icon": VRAM, "value": vram}]
        if r["power"] is not None and r["limit"]:
            meters.append({"icon": POWER, "value": power})
        if r["fan"] is not None:
            meters.append({"icon": FAN, "value": r["fan"]})
        blocks = [{"type": "head", "icon": ICON, "title": "GPU", "detail": r["name"]},
                  {"type": "kv", "rows": rows},
                  {"type": "chip", "meters": meters}]
        if top:
            blocks += [{"type": "rule"}, {"type": "table", "rows": [[n, f"{m:.0f} MiB"] for n, m in top]}]
        blocks.append({"type": "hints", "text": "Click: GPU and the rest (System)"})
        print(json.dumps({"text": f"{ICON}{load:3.0f}%", "tooltip": card(blocks), "class": level(load)},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
