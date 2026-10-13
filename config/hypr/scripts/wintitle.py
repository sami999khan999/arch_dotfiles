#!/usr/bin/env python3
"""wintitle.py — waybar module: the focused window's title, cleaned up and cut to MAX characters.

Replaces waybar's hyprland/window. That module ellipsizes inside GTK, and GTK would rather shrink
an ellipsizing label than move the centre island. Cutting the text here instead gives the label
its full width; MAX keeps the centre section compact, so it stays put as titles change length.
Prints one JSON line per change (return-type json).

With a long title and the play button the right section fills the bar: while the busy-CPU warning shows
(custom/load, loadwatch.py) the title gives up its width, or the power button went off the screen.
loadwatch.py writes its text to $XDG_RUNTIME_DIR/loadwatch.json and sends SIGUSR1 to the pids in
$XDG_RUNTIME_DIR/waybar-wintitle/ (one copy per bar).
"""
import json, os, re, signal, sys

MAX = 30  # max width of the title, in characters (longer ones end in …)
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
LOAD_STATE = os.path.join(RUN, "loadwatch.json")
PID_DIR = os.path.join(RUN, "waybar-wintitle")

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


def room():
    """Characters the busy-CPU warning takes while it shows: its text (11px, a little narrower than the
    title's characters) and its padding."""
    try:
        text = json.load(open(LOAD_STATE)).get("text", "")
    except (OSError, ValueError):
        return 0
    return len(text) + 1 if text else 0


def title():
    global shown
    win = focused()
    shown = win.get("address", "")
    t = win.get("title", "").strip()
    for rx in CLEANUP:
        t = rx.sub("", t)
    full, most = t, MAX - room()
    if len(t) > most:
        t = t[:most - 1].rstrip() + "…"
    return {"text": t, "tooltip": full if full != t else "", "class": "empty" if not t else "window"}


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
    me = os.path.join(PID_DIR, str(os.getpid()))   # loadwatch signals by pid: a name match hits editors too
    open(me, "w").close()
    signal.signal(signal.SIGUSR1, lambda *_: emit())   # the warning came or went: the title's room changed
    signal.signal(signal.SIGTERM, lambda *_: (os.path.exists(me) and os.remove(me), os._exit(0)))
    follow(lambda events: concerns(events) and emit(), emit)


if __name__ == "__main__":
    main()
