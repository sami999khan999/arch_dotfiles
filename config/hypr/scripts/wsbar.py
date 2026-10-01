#!/usr/bin/env python3
"""wsbar.py — state for waybar's workspace buttons (custom/ws1 … custom/ws10).

Why not waybar's hyprland/workspaces: its click sends the legacy `dispatch workspace N`, which the
Lua config rejects, so the buttons did nothing. Each custom/wsN button runs a Lua dispatch on click
instead, and reads its look from a small file this daemon keeps current:

    $XDG_RUNTIME_DIR/wsbar/N.json   {"text": icon, "class": "active|occupied|empty|urgent"}

After a change it sends SIGRTMIN+8 to waybar, which makes all ten buttons re-read their file; only
after a real change. The buttons have no tooltip: GTK hides a tooltip on every click, so it blinked
while clicking a button to cycle its windows (the group count by the title shows the same).
Started by waybar itself (custom/wsbar, which prints nothing and stays hidden).
"""
import json, os, signal, socket, subprocess, time

WORKSPACES = range(1, 11)
ICONS = {1: "\U000f0a1e", 2: "", 3: "", 7: "", 8: "\U000f066f", 9: "", 10: "\U000f056e"}
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "wsbar")
SIGNAL = 8
REFRESH = (b"workspace", b"createworkspace", b"destroyworkspace", b"focusedmon", b"openwindow",
           b"closewindow", b"movewindow", b"urgent", b"configreloaded")


def hypr(what):
    try:
        return json.loads(subprocess.run(["hyprctl", what, "-j"], capture_output=True, text=True).stdout)
    except (json.JSONDecodeError, OSError):
        return None


last = None   # what the buttons show now, to skip updates that change nothing


def write(urgent):
    global last
    active = (hypr("activeworkspace") or {}).get("id")
    counts = {w["id"]: w.get("windows", 0) for w in hypr("workspaces") or []}
    urgent.discard(active)
    states = {}
    for ws in WORKSPACES:
        n = counts.get(ws, 0)
        cls = "active" if ws == active else "urgent" if ws in urgent else "occupied" if n else "empty"
        states[ws] = {"text": ICONS.get(ws, str(ws % 10)), "class": cls}
    if states == last:
        return
    last = states
    for ws, state in states.items():
        path = os.path.join(RUN, f"{ws}.json")
        with open(path + ".tmp", "w") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)  # a button never reads a half-written file
    subprocess.run(["pkill", f"-RTMIN+{SIGNAL}", "-x", "waybar"])


def main():
    os.makedirs(RUN, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket2.sock")
    urgent = set()
    while True:
        write(urgent)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock)
            buf = b""
            while data := s.recv(4096):
                buf += data
                *events, buf = buf.split(b"\n")
                changed = False
                for e in events:
                    kind, _, arg = e.partition(b">>")
                    if kind == b"urgent":  # remember which workspace holds the urgent window
                        addr = "0x" + arg.decode(errors="ignore")
                        for c in hypr("clients") or []:
                            if c.get("address") == addr:
                                urgent.add(c["workspace"]["id"])
                    changed |= kind in REFRESH
                if changed:
                    time.sleep(0.02)  # let a burst of events (move + focus) settle
                    write(urgent)
        except OSError:
            time.sleep(2)


if __name__ == "__main__":
    main()
