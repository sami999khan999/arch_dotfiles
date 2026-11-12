#!/usr/bin/env python3
"""memory.py — waybar module (custom/memory, in the stats): how much memory is used, and its card.

The bar shows the share in use ("󰘚 58%", as waybar's memory module did: what's not available). Its card
(bartips): used of total, swap, the memory sticks (slot, size, type, speed: dmidecode, saved by root in
/run/hwinfo by system/hwinfo — run system/root-setup.sh once), a memory stick drawn with what it holds
(used by apps, cache, buffers, free) and the apps holding the most. Every DELAY s it reads /proc/meminfo;
the apps (each one's cgroup, usage.py) only every APPS_EVERY s. Click: System → Apps.
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
from bartip import card, gib, level
import usage

DELAY = 5
APPS_EVERY = 15
ICON = "\U000f061a"   # memory
HWINFO = "/run/hwinfo/memory.txt"


def meminfo():
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) * 1024
    return out


def sticks():
    """[(slot, size, type, speed)] of the installed sticks, from dmidecode's output; None without it."""
    try:
        text = open(HWINFO).read()
    except OSError:
        return None
    out = []
    for dev in text.split("Memory Device")[1:]:
        f = dict(re.findall(r"^\s*([^:\n]+):\s*(.*)$", dev, re.M))
        if not f.get("Size") or "No Module" in f["Size"]:
            continue
        speed = f.get("Configured Memory Speed") or f.get("Speed") or ""
        out.append((f.get("Locator", "?"), f["Size"].replace(" GB", " GiB"), f.get("Type", ""),
                    speed.replace("Unknown", "")))
    return out


def apps():
    """[(name, bytes)]: the four apps / services / containers holding the most, each its cgroup's memory
    (usage.py, as System → Apps counts it: shared memory once, an app's helpers in it)."""
    per = {}
    for path in usage.leaves():
        pids = usage.read(f"{usage.ROOT}/{path}/cgroup.procs").split()
        if not pids:
            continue
        name, _ = usage.describe(path, pids)
        per[name] = per.get(name, 0) + int(usage.read(f"{usage.ROOT}/{path}/memory.current", "0").strip() or 0)
    return sorted(per.items(), key=lambda kv: -kv[1])[:4]


def main():
    top, top_at = [], 0
    dimms = sticks()
    while True:
        m = meminfo()
        total = m["MemTotal"]
        free, buffers = m["MemFree"], m.get("Buffers", 0)
        cached = m.get("Cached", 0) + m.get("SReclaimable", 0) - m.get("Shmem", 0)
        used = total - free - buffers - cached
        in_use = (total - m["MemAvailable"]) / total * 100
        swap_used = m.get("SwapTotal", 0) - m.get("SwapFree", 0)
        if time.monotonic() - top_at > APPS_EVERY:
            top, top_at = apps(), time.monotonic()
            dimms = sticks() if dimms is None else dimms   # root's file may have come since
        blocks = [{"type": "head", "icon": ICON, "title": "Memory", "detail": f"{gib(total)}",
                   "right": f"{in_use:.0f}%", "color": level(in_use, 75, 90)},
                  {"type": "kv", "rows": [
                      ["in use", f"{gib(total - m['MemAvailable'])} of {gib(total)}", level(in_use, 75, 90)],
                      ["swap", f"{gib(swap_used)} of {gib(m.get('SwapTotal', 0))}" if m.get("SwapTotal") else "none"]]},
                  {"type": "stack", "style": "dimm", "segments": [
                      {"label": "Apps", "value": used / total * 100, "color": "accent"},
                      {"label": "Cache", "value": cached / total * 100, "color": "amber"},
                      {"label": "Buffers", "value": buffers / total * 100, "color": "green"},
                      {"label": "Free", "value": free / total * 100, "color": "overlay"}]}]
        if dimms:
            blocks.append({"type": "table", "rows": [[slot, sz, kind, speed] for slot, sz, kind, speed in dimms]})
        elif dimms is None:
            blocks.append({"type": "text", "text": "Sticks: run system/root-setup.sh", "color": "muted"})
        blocks.append({"type": "rule"})
        blocks.append({"type": "table", "rows": [[name, gib(rss)] for name, rss in top]})
        blocks.append({"type": "hints", "text": "Click: what's using it (System)"})
        print(json.dumps({"text": f"{ICON}{in_use:3.0f}%", "tooltip": card(blocks),
                          "class": level(in_use, 75, 90)}, ensure_ascii=False), flush=True)
        time.sleep(DELAY)


if __name__ == "__main__":
    main()
