#!/usr/bin/env python3
# waybars.py — one waybar per screen, so a screen's bar can hide on its own (waybar's SIGUSR1 flips every
# bar of a process; modules/barhide.lua signals the pid of the bar on the screen whose workspace wants it).
# Run by waybar.service in place of waybar (config/waybar/waybar.service.conf, a drop-in linked by
# setup/install.sh), so `systemctl --user restart waybar` still restarts them all. Each waybar reads
# config.jsonc through a small per-screen file that adds "output"; a screen plugged in gets a bar,
# one unplugged loses it, and a bar that stops is started again after a second.
import json, os, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from hyprsock import follow, query

CONFIG = os.path.expanduser("~/.config/waybar/config.jsonc")
STYLE = os.path.expanduser("~/.config/waybar/style.css")
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "waybar")
TIPS = os.path.expanduser("~/.config/waybar/bartips/bartips")   # the bar's tooltips (bartips.c), built here

bars = {}   # screen name -> its waybar (Popen)
lock = threading.Lock()


def start(name):
    conf = os.path.join(RUN, f"{name}.jsonc")
    with open(conf, "w") as f:   # its own keys win over the included file's
        json.dump({"include": [CONFIG], "output": name}, f)
    bars[name] = subprocess.Popen(["waybar", "-c", conf, "-s", STYLE])


def sync():
    """A bar on every screen Hyprland has, none on a screen it no longer has."""
    monitors = query("monitors")
    if monitors is None:
        return
    names = {m["name"] for m in monitors}
    with lock:
        for name in names - bars.keys():
            start(name)
        for name in bars.keys() - names:
            bars.pop(name).terminate()


def on_events(events):
    if any(e.startswith((b"monitoradded", b"monitorremoved")) for e in events):
        sync()


def build_tips():
    """Compile bartips.c into the .so config.jsonc loads (cffi/bartips) when the source is newer: a fresh
    PC, or a change to it, then `systemctl --user restart waybar`. A failed build leaves the old .so (or
    none: the bars then show without tooltips)."""
    src, lib = TIPS + ".c", TIPS + ".so"
    folder = os.path.dirname(src)
    try:   # bartips.c includes the folder's other .c files (cards.c): any newer one rebuilds it
        newest = max(os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder) if f.endswith(".c"))
        if os.path.exists(lib) and os.path.getmtime(lib) >= newest:
            return
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "gtk+-3.0", "gtk-layer-shell-0", "json-glib-1.0"],
                               capture_output=True, text=True, check=True).stdout.split()
        subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-o", lib, src, *flags, "-lm"], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"waybars: bartips not built: {e}", file=sys.stderr)


def main():
    os.makedirs(RUN, exist_ok=True)
    build_tips()
    # systemd's reload (and a SIGUSR2 sent to this process) reaches every bar
    signal.signal(signal.SIGUSR2, lambda *_: [b.send_signal(signal.SIGUSR2) for b in list(bars.values())])
    threading.Thread(target=follow, args=(on_events, sync), daemon=True).start()
    while True:
        try:
            pid, _ = os.wait()
        except ChildProcessError:   # none started yet
            time.sleep(1)
            continue
        with lock:
            name = next((n for n, b in bars.items() if b.pid == pid), None)
            if name:
                del bars[name]
        if name:
            time.sleep(1)   # a bar that keeps crashing must not make a busy loop
            sync()


if __name__ == "__main__":
    main()
