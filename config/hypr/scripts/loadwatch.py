#!/usr/bin/env python3
"""loadwatch.py — waybar module (custom/load): a warning that shows only while the CPU stays busy, with
what's causing it, e.g. "󰍛 starter-kits·1" (an agentmux thread running a test suite).

Every 5 s it reads /proc/stat (no process started). Busy (BUSY % of all cores or more) for SHOW_AFTER
seconds: it names the biggest source from the cgroups (local/lib/panels/usage.py: each app, service,
container and agentmux thread, their finished processes counted too) and shows; back under CALM for
HIDE_AFTER seconds: it hides. Click: the System panel's Apps tab (sysgui.py --tab Apps).

The visualizer (waybar) fills the gap left of the right section, so it's told when the warning comes,
goes or changes width: its text in $XDG_RUNTIME_DIR/loadwatch.json, SIGUSR1 to its pids, as toggle.sh
does for the idle indicator.
"""
import json, os, signal, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
import usage

EVERY = 5          # s between readings
BUSY, CALM = 85, 70        # % of all cores: shows from BUSY, hides under CALM
SHOW_AFTER, HIDE_AFTER = 30, 30   # s
GLYPH = "\U000f035b"       # the cpu module's chip
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = os.path.join(RUN, "loadwatch.json")
VISUALIZER = os.path.join(RUN, "waybar-visualizer")
MAX_NAME = 16


def busy_ticks():
    v = [int(x) for x in usage.read("/proc/stat").split("\n", 1)[0].split()[1:]]
    return sum(v), v[3] + v[4]   # total, idle + iowait


def emit(out, moved=True):
    """Print the module's line; moved (the text came, went or changed): tell the visualizer."""
    print(json.dumps(out, ensure_ascii=False), flush=True)
    if not moved:   # only the tooltip's numbers
        return
    try:
        with open(STATE, "w") as f:
            json.dump({"text": out["text"]}, f, ensure_ascii=False)
    except OSError:
        pass
    for pid in os.listdir(VISUALIZER) if os.path.isdir(VISUALIZER) else []:
        try:
            os.kill(int(pid), signal.SIGUSR1)   # recount the gap
        except (OSError, ValueError):
            pass


def warning(rows, pct, since):
    """The module's JSON while busy: the top source in the text, the top three in the tooltip."""
    top = [r for r in rows if r["cpu"] >= 5][:3]
    name = top[0]["name"] if top else "busy"
    name = name if len(name) <= MAX_NAME else name[:MAX_NAME - 1] + "…"
    lines = [f"CPU {pct:.0f}% for {int(time.time() - since) // 60 or 1} min"]
    for r in top:
        what = ", ".join(f"{n} {p:.0f}%" for n, p in r["top"])
        lines.append(f"{r['name']} ({r['kind']}) {r['cpu']:.0f}%" + (f": {what}" if what else ""))
    lines.append("(100% is one whole core)\nClick: what's using the CPU")
    return {"text": f"{GLYPH} {name}", "tooltip": "\n".join(lines), "class": "busy"}


def main():
    shown, busy_since, calm_since, sampler, last = False, None, None, None, None
    prev = busy_ticks()
    emit({"text": ""})
    while True:
        time.sleep(EVERY)
        cur = busy_ticks()
        dt = cur[0] - prev[0]
        pct = (1 - (cur[1] - prev[1]) / dt) * 100 if dt else 0
        prev, now = cur, time.time()
        if pct >= BUSY:
            busy_since, calm_since = busy_since or now, None
        elif pct < CALM:
            busy_since, calm_since = None, calm_since or now
        if busy_since and sampler is None:
            sampler = usage.Sampler()   # its first reading now: by SHOW_AFTER it has the numbers
            sampler.sample()
        if not shown and busy_since and now - busy_since >= SHOW_AFTER:
            shown = True
        elif shown and calm_since and now - calm_since >= HIDE_AFTER:
            shown, sampler = False, None
        if not busy_since and not shown:
            sampler = None
        out = warning(sampler.sample(), pct, busy_since or now) if shown and sampler else {"text": ""}
        if out != last:
            emit(out, moved=out["text"] != (last or {}).get("text"))
            last = out


if __name__ == "__main__":
    main()
