#!/usr/bin/env python3
"""storage.py — waybar module (custom/storage, in the stats): the system drive's use, and a card of every drive.

The bar shows how full the drive holding / is ("󰋊 32%"). Its card (bartips) has a part per drive: what
it's for (System, Data, Windows…), the model, size and kind (NVMe / SSD / HDD), its temperature, health
and wear (SMART, saved by root in /run/hwinfo by system/hwinfo: run system/root-setup.sh once), what's
being read and written now, and a bar of how full it is. Every DELAY s it reads /proc/diskstats, the
mounted filesystems' sizes and the sensors: small files, no process started (lsblk only when a drive
comes or goes). Click: the Storage panel.
"""
import json, os, glob, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from bartip import card, level, size, temp_colour

DELAY = 3
HWINFO = "/run/hwinfo"
DISK = "\U000f02ca"   # a drive (filled, like the other stats' icons)
KINDS = {"nvme": "NVMe", "ssd": "SSD", "hdd": "HDD"}
WARM = {"nvme": (60, 70), "ssd": (50, 60), "hdd": (45, 55)}   # °C: getting warm, too hot
ROLES = {"/": "System", "/mnt/data": "Data", "/home": "Home"}


def drives():
    """[{name, model, size, kind, mounts: [(partition, mountpoint)], label}] from lsblk."""
    try:
        tree = json.loads(subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,MODEL,ROTA,TRAN,TYPE,FSTYPE,LABEL,MOUNTPOINTS"],
            capture_output=True, text=True).stdout)["blockdevices"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for d in tree:
        if d.get("type") != "disk" or d["name"].startswith(("zram", "loop")):
            continue
        mounts, labels = [], []
        for p in d.get("children") or []:
            points = [m for m in p.get("mountpoints") or [] if m and m != "[SWAP]"]
            if points:   # a btrfs partition is mounted many times: / (or the shortest) stands for it
                mounts.append((p["name"], "/" if "/" in points else min(points, key=len)))
            if p.get("label"):
                labels.append(p["label"])
            if p.get("fstype") == "ntfs" and not p.get("label") and int(p.get("size") or 0) > 50e9:
                labels.append("Windows")
        kind = "nvme" if d.get("tran") == "nvme" else "hdd" if d.get("rota") else "ssd"
        out.append({"name": d["name"], "model": " ".join((d.get("model") or d["name"]).replace("_", " ").split()[:2]),
                    "size": int(d.get("size") or 0), "kind": kind, "mounts": mounts, "labels": labels})
    return out


def role(d):
    for _, m in d["mounts"]:
        if m in ROLES:
            return ROLES[m]
    if d["labels"]:
        return d["labels"][0]
    return os.path.basename(d["mounts"][0][1]) if d["mounts"] else "Not mounted"


def usage(d):
    """(used, total) bytes over its mounted filesystems; None when nothing is mounted."""
    used = total = 0
    for _, m in d["mounts"]:
        try:
            st = os.statvfs(m)
        except OSError:
            continue
        total += st.f_blocks * st.f_frsize
        used += (st.f_blocks - st.f_bfree) * st.f_frsize
    return (used, total) if total else None


def sectors():
    """{disk: (sectors read, sectors written)}"""
    out = {}
    with open("/proc/diskstats") as f:
        for line in f:
            p = line.split()
            out[p[2]] = (int(p[5]), int(p[9]))
    return out


def temperature(name):
    """°C from the drive's own sensor (NVMe's, or drivetemp's for SATA), None without one."""
    for path in glob.glob(f"/sys/block/{name}/device/hwmon*/temp1_input") + \
            glob.glob(f"/sys/block/{name}/device/hwmon/hwmon*/temp1_input"):
        try:
            return int(open(path).read()) / 1000
        except (OSError, ValueError):
            pass
    return None


def smart(name):
    """What SMART says: health, life left (%), written (bytes), power-on hours; {} without /run/hwinfo."""
    try:
        s = json.load(open(f"{HWINFO}/smart-{name}.json"))
    except (OSError, ValueError):
        return {}
    out = {}
    if "smart_status" in s:
        out["ok"] = s["smart_status"].get("passed")
    out["hours"] = (s.get("power_on_time") or {}).get("hours")
    nv = s.get("nvme_smart_health_information_log")
    if nv:
        out["life"] = max(0, 100 - nv.get("percentage_used", 0))
        out["written"] = nv.get("data_units_written", 0) * 512000
    for a in (s.get("ata_smart_attributes") or {}).get("table", []):
        if a.get("id") in (231, 169, 202, 233) and "life" not in out and 0 < a.get("value", 0) <= 100:
            out["life"] = a["value"]   # SSD life left (the vendors' attribute differs)
        if a.get("id") == 241:   # LBAs written
            out["written"] = (a.get("raw") or {}).get("value", 0) * 512
    return out


def rate(n):
    return f"{size(n)}/s"


def main():
    disks, seen = drives(), sorted(os.listdir("/sys/block"))
    before, t0 = sectors(), time.monotonic()
    while True:
        time.sleep(DELAY)
        now_seen = sorted(os.listdir("/sys/block"))
        if now_seen != seen:   # a drive plugged in or out
            disks, seen = drives(), now_seen
        now, t1 = sectors(), time.monotonic()
        dt = max(t1 - t0, 0.001)
        blocks = [{"type": "head", "icon": DISK, "title": "Storage",
                   "detail": f"{len(disks)} drive{'s' if len(disks) != 1 else ''}"}]
        system_pct = None
        for d in disks:
            r0, w0 = before.get(d["name"], (0, 0))
            r1, w1 = now.get(d["name"], (0, 0))
            read, write = (r1 - r0) * 512 / dt, (w1 - w0) * 512 / dt
            t, sm, use = temperature(d["name"]), smart(d["name"]), usage(d)
            blocks.append({"type": "rule"})
            blocks.append({"type": "section", "icon": DISK, "title": role(d),
                           "detail": f"{d['model']} · {size(d['size'])} · {KINDS[d['kind']]}",
                           "right": f"{t:.0f}°C" if t is not None else None, "color": temp_colour(t, *WARM[d["kind"]])})
            rows = []
            if sm.get("ok") is not None or "life" in sm:
                health = "OK" if sm.get("ok") else "Failing" if sm.get("ok") is False else "?"
                if "life" in sm:
                    health += f" · {sm['life']}% life left"
                rows.append(["health", health, "green" if sm.get("ok") and sm.get("life", 100) > 20
                              else "alert" if sm.get("ok") is False else "amber"])
            elif not os.path.isdir(HWINFO):
                rows.append(["health", "run system/root-setup.sh", "muted"])
            if sm.get("written"):
                rows.append(["written", size(sm["written"])])
            rows.append(["read · write", f"{rate(read)} · {rate(write)}"])
            blocks.append({"type": "kv", "rows": rows})
            if use:
                pct = use[0] / use[1] * 100
                blocks.append({"type": "meter", "value": pct, "right": f"{pct:.0f}%",
                               "left": f"{size(use[1] - use[0])} free of {size(use[1])}"})
                if any(m == "/" for _, m in d["mounts"]):
                    system_pct = pct
        before, t0 = now, t1
        blocks.append({"type": "hints", "text": "Click: what fills them (Storage)"})
        shown = f"{system_pct:3.0f}%" if system_pct is not None else "  ?"
        print(json.dumps({"text": f"{DISK}{shown}", "tooltip": card(blocks),
                          "class": level(system_pct or 0, 80, 90)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
