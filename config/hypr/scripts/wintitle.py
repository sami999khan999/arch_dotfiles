#!/usr/bin/env python3
"""wintitle.py — waybar module: the focused window's title, cleaned up and cut to MAX characters.

Replaces waybar's hyprland/window. That module ellipsizes inside GTK, and GTK would rather shrink
an ellipsizing label than move the centre island. Cutting the text here instead gives the label
its full width; MAX keeps it small while something plays (the whole title is in its tooltip). Prints one
JSON line per change (return-type json).

While nothing plays (the audio graph's silent) the title may be long: visualizer.py writes 1 to
waybar-wintitle.long and sends SIGUSR1 to the pids in $XDG_RUNTIME_DIR/waybar-wintitle/ (one copy per bar),
and the whole title goes out. The module has max-length (config.jsonc), so GTK cuts it ("…") to the room
left of the right section rather than push it: the power button can't move, whatever the right side does.
"""
import json, os, re, signal, sys

MAX = 13  # max width of the title while something plays, in characters (longer ones end in …)
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PID_DIR = os.path.join(RUN, "waybar-wintitle")
LONG_FILE = os.path.join(RUN, "waybar-wintitle.long")   # "1": nothing plays (visualizer.py)

CLEANUP = [
    re.compile(r"^[^\w(]+\s+"),                      # spinner / status glyphs: "✳ Claude Code"
    re.compile(r"^\(\d+\)\s+"),                      # unread counts: "(1063) Video"
    re.compile(r"\s+[-—]\s+(Google Chrome|Mozilla Firefox|Visual Studio Code)$"),
    re.compile(r"\s+-\s+YouTube$"),
]


POPUPS = ("TUI.float", "panels.")   # popup panels (and the backdrop behind one), as in wsgroups
shown = ""   # the address of the window whose title shows ("" on an empty workspace)


sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from hyprsock import follow, query as hypr   # the socket, not a hyprctl process per question


def focused():
    """The focused window ({} on an empty workspace); while a popup has the focus, the window it
    opened over (the title doesn't change under a popup)."""
    active = hypr("activewindow") or {}
    if not active.get("class", "").startswith(POPUPS):
        return active
    recent = sorted((c for c in hypr("clients") or [] if c.get("focusHistoryID", -1) >= 0),
                    key=lambda c: c["focusHistoryID"])
    ws = active["workspace"]["id"]
    return next((c for c in recent if not c["class"].startswith(POPUPS) and c["workspace"]["id"] == ws), {})


def long_allowed():
    """Whether the title may be long: nothing plays (visualizer.py)."""
    try:
        return open(LONG_FILE).read().strip() == "1"
    except OSError:
        return False


def title(long=None):
    """The title as the bar shows it: cut to MAX, or whole while `long` (None: what visualizer.py says)."""
    global shown
    win = focused()
    shown = win.get("address", "")
    t = win.get("title", "").strip()
    for rx in CLEANUP:
        t = rx.sub("", t)
    full = t
    if len(t) > MAX and not (long_allowed() if long is None else long):
        t = t[:MAX - 1].rstrip() + "…"   # long, GTK cuts it to the room (max-length)
    return {"text": t, "tooltip": full if len(full) > MAX else "", "class": "empty" if not t else "window"}


WANTED = (b"activewindow>>", b"workspace>>", b"closewindow>>", b"focusedmon>>")


def concerns(events):
    """Whether these events can change the title. A title change of the focused window also comes as
    activewindow; windowtitle counts only for the window shown under a popup: any other window's
    title (a spinner in a terminal on another workspace) is none of the bar's business."""
    for e in events:
        if e.startswith(WANTED):
            return True
        if e.startswith(b"windowtitle>>") and "0x" + e[13:].decode(errors="ignore") == shown:
            return True
    return False


def main():
    last = None

    def emit():
        nonlocal last
        out = json.dumps(title(), ensure_ascii=False)
        if out != last:
            print(out, flush=True)
            last = out

    os.makedirs(PID_DIR, exist_ok=True)
    me = os.path.join(PID_DIR, str(os.getpid()))   # signalled by pid: a name match hits editors too
    open(me, "w").close()
    signal.signal(signal.SIGUSR1, lambda *_: emit())   # something started or stopped playing
    signal.signal(signal.SIGTERM, lambda *_: (os.path.exists(me) and os.remove(me), os._exit(0)))
    follow(lambda events: concerns(events) and emit(), emit)


if __name__ == "__main__":
    main()
