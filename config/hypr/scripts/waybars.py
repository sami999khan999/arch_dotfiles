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


def main():
    os.makedirs(RUN, exist_ok=True)
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
