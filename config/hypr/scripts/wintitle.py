#!/usr/bin/env python3
"""wintitle.py — waybar module: the focused window's title, cleaned up and cut to MAX characters.

Replaces waybar's hyprland/window. That module ellipsizes inside GTK, and GTK would rather shrink
an ellipsizing label than move the centre island. Cutting the text here instead gives the label
its full width; MAX keeps it short enough that the right section never runs off the screen.
Prints one JSON line per change (return-type json).
"""
import json, os, re, socket, subprocess, time

MAX = 34  # longest title that fits on a 1366px screen with the clock's long view (click) on the right

CLEANUP = [
    re.compile(r"^[^\w(]+\s+"),                      # spinner / status glyphs: "✳ Claude Code"
    re.compile(r"^\(\d+\)\s+"),                      # unread counts: "(1063) Video"
    re.compile(r"\s+[-—]\s+(Google Chrome|Mozilla Firefox|Visual Studio Code)$"),
    re.compile(r"\s+-\s+YouTube$"),
]


def title():
    try:
        win = json.loads(subprocess.run(["hyprctl", "activewindow", "-j"],
                                        capture_output=True, text=True).stdout or "{}")
    except json.JSONDecodeError:
        win = {}
    t = (win or {}).get("title", "").strip()
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
