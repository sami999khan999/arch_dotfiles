#!/usr/bin/env python3
"""visualizer.py — waybar module: bars that move with whatever audio is playing (cava).

Runs cava with raw text output and turns each frame ("0;3;7;…") into block characters (▁▂▃…█).
The bars fill the gap between the workspace buttons and the right section of the bar. That gap
changes with the window title's length (the title is only as wide as its text) and with whether
the play button shows, so the script follows both and shows as many bars as fit: cava always
makes RAW_BARS, and each shown bar is the loudest of its share of them.
Silent for SILENT_AFTER seconds: prints empty text, so waybar hides the module until something
plays again. Prints one JSON line per change (return-type json).
"""
import importlib.util, json, os, shutil, socket, subprocess, threading, time

# Measured on the 1366 px screen: the gap is 211 px with a 30-character title and the play
# button showing. Each title character is CHAR_PX wide; on an empty workspace the title hides
# (freeing its padding too) and the group count shrinks to its icon; no player frees the play
# button. A wider screen adds its extra width.
# The numbers on the right are fixed-width (config.jsonc), so nothing else moves the gap, and the
# bars fill it to the pixel: the spacing between them stretches to use up what whole bars leave.
GAP_REF, TITLE_REF = 211, 30
CHAR_PX = 7.3         # px per title character (JetBrains Mono, 12 px)
TITLE_PAD = 14        # px: the title's padding (style.css #custom-window)
COUNT_PX = 19         # px: the group count's " 1/1", gone on an empty workspace (wsgroups bar)
PLAYER_PX = 30        # px: the play button with its divider
PADDING = 28          # px: this module's own left + right padding (style.css #custom-visualizer)
GLYPH_PX = 7.94       # px: one block character at 13 px
MIN_GAP_PX = 1.0      # px: the least space between two bars
PANGO_PER_PX = 768    # Pango letter_spacing units per pixel (1024 per point, 96 dpi)

RAW_BARS = 64         # what cava makes; more than ever fit
FPS = 25              # frames per second (cava's framerate; also how often waybar can redraw)
SILENT_AFTER = 2.0    # seconds of silence before the module hides
BLOCKS = "▁▂▃▄▅▆▇█"

CONFIG = f"""
[general]
bars = {RAW_BARS}
framerate = {FPS}
sleep_timer = 1

[input]
method = pipewire

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {len(BLOCKS) - 1}
bar_delimiter = 59
frame_delimiter = 10

[smoothing]
noise_reduction = 77
"""

# the title exactly as the bar shows it (cleaned and cut), from the title module's own script
_spec = importlib.util.spec_from_file_location(
    "wintitle", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wintitle.py"))
wintitle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wintitle)


def screen_width():
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout
        return min(m["width"] / m.get("scale", 1) for m in json.loads(out))
    except (OSError, ValueError, KeyError):
        return 1366


WIDTH = screen_width()
state = {"title": "x" * TITLE_REF, "player": True, "shown": 16, "spacing": 1.0}


def recount():
    """How many bars fit now and how far apart, so they fill the gap exactly (read by the loop)."""
    length = len(state["title"])
    gap = GAP_REF + WIDTH - 1366 + (TITLE_REF - length) * CHAR_PX
    if not length:
        gap += TITLE_PAD + COUNT_PX
    if not state["player"]:
        gap += PLAYER_PX
    room = gap - PADDING
    n = max(4, int(room / (GLYPH_PX + MIN_GAP_PX)))
    state["shown"], state["spacing"] = n, max(room / n - GLYPH_PX, 0)


def follow_title():
    """Hyprland's event socket: recount when the focused window or its title changes."""
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket2.sock")
    wanted = (b"activewindow>>", b"windowtitle>>", b"workspace>>", b"closewindow>>")
    while True:
        state["title"] = wintitle.title()["text"]
        recount()
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock)
            buf = b""
            while data := s.recv(4096):
                buf += data
                *events, buf = buf.split(b"\n")
                if any(e.startswith(wanted) for e in events):
                    state["title"] = wintitle.title()["text"]
                    recount()
        except OSError:
            time.sleep(2)


def follow_player():
    """The play button shows while a player is playing or paused: check every 2 s."""
    while True:
        out = subprocess.run(["playerctl", "status"], capture_output=True, text=True).stdout.strip()
        state["player"] = out in ("Playing", "Paused")
        recount()
        time.sleep(2)


def emit(text, cls):
    print(json.dumps({"text": text, "class": cls, "tooltip": ""}, ensure_ascii=False), flush=True)


def main():
    emit("", "silent")
    while not shutil.which("cava"):
        time.sleep(10)           # not installed (yet): stay hidden, start once it is
    conf = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "waybar-visualizer.conf")
    with open(conf, "w") as f:
        f.write(CONFIG)
    for watcher in (follow_title, follow_player):
        threading.Thread(target=watcher, daemon=True).start()
    cava = subprocess.Popen(["cava", "-p", conf], stdout=subprocess.PIPE, text=True, bufsize=1)
    last, heard = None, 0.0
    for line in cava.stdout:
        levels = [int(v) for v in line.strip().rstrip(";").split(";") if v.isdigit()]
        now = time.time()
        if any(levels):
            heard = now
        if now - heard > SILENT_AFTER or not levels:
            out = ("", "silent")
        else:
            n, raw = state["shown"], len(levels)
            bars = (max(levels[i * raw // n:(i + 1) * raw // n] or [0]) for i in range(n))
            # a silent bar is blank, not ▁: no grey floor line along an idle stretch
            text = "".join(BLOCKS[min(v, len(BLOCKS) - 1)] if v else " " for v in bars)
            spacing = round(state["spacing"] * PANGO_PER_PX)
            out = (f'<span letter_spacing="{spacing}">{text}</span>', "playing")
        if out != last:
            emit(*out)
            last = out
    raise SystemExit(cava.wait())


if __name__ == "__main__":
    main()
