#!/usr/bin/env python3
"""wintitle.py — waybar module: the focused window's title, cleaned up and cut to MAX characters.

Replaces waybar's hyprland/window. That module ellipsizes inside GTK, and GTK would rather shrink
an ellipsizing label than move the centre island. Cutting the text here instead gives the label
its full width; MAX keeps the centre section compact, so it stays put as titles change length.
Prints one JSON line per change (return-type json).
"""
import json, os, re, socket, subprocess, time

MAX = 30  # max width of the title, in characters (longer ones end in …)

CLEANUP = [
    re.compile(r"^[^\w(]+\s+"),                      # spinner / status glyphs: "✳ Claude Code"
    re.compile(r"^\(\d+\)\s+"),                      # unread counts: "(1063) Video"
    re.compile(r"\s+[-—]\s+(Google Chrome|Mozilla Firefox|Visual Studio Code)$"),
    re.compile(r"\s+-\s+YouTube$"),
]


POPUPS = ("TUI.float", "panels.")   # popup panels (and the backdrop behind one), as in wsgroups


def hypr(what):
    try:
        return json.loads(subprocess.run(["hyprctl", what, "-j"], capture_output=True, text=True).stdout or "null")
    except json.JSONDecodeError:
        return None


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


def title():
    t = focused().get("title", "").strip()
    for rx in CLEANUP:
        t = rx.sub("", t)
    full = t
    if len(t) > MAX:
        t = t[:MAX - 1].rstrip() + "…"
    return {"text": t, "tooltip": full if full != t else "", "class": "empty" if not t else "window"}


def main():
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket2.sock")
    wanted = (b"activewindow>>", b"windowtitle>>", b"workspace>>", b"closewindow>>", b"focusedmon>>")
    last = None

    def emit():
        nonlocal last
        out = json.dumps(title(), ensure_ascii=False)
        if out != last:
            print(out, flush=True)
            last = out

    while True:
        emit()
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock)
            buf = b""
            while data := s.recv(4096):
                buf += data
                *events, buf = buf.split(b"\n")
                if any(e.startswith(wanted) for e in events):
                    emit()
        except OSError:
            time.sleep(2)


if __name__ == "__main__":
    main()
