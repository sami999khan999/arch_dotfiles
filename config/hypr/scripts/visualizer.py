#!/usr/bin/env python3
"""visualizer.py — waybar module: bars that move with whatever audio is playing (cava).

Runs cava with raw text output and turns each frame ("0;3;7;…") into block characters (▁▂▃…█).
The bars fill the gap between the workspace buttons and the right section of the bar, to the pixel.
The gap is measured on the bar itself while the bars show: a one-pixel-high screenshot of the bar
(grim) finds the divider after the workspace buttons and the first one of the right section, every
second and just after a window change, so any change on the right (a title, the play button, an
indicator, the clock, the code-backup icon) is followed within a second, to the pixel. Without a
measurement (grim missing, the dividers not found) the gap is worked out from what's on the right:
the window title and the group count (their text), the play button, the idle / do-not-disturb
indicators and the clock's view (short or long).
cava always makes RAW_BARS; each shown bar is the loudest of its share of them.
Silent for SILENT_AFTER seconds: prints empty text, so waybar hides the module until something
plays again. Prints one JSON line per change (return-type json). SIGUSR1: recount now (clock.py
and toggle.sh send it when the clock's view or an indicator changes, to the pid in PID_FILE).

It's the last module of the left section and may shrink (max-length; the bar has no centre
section, see config.jsonc): GTK then shrinks it rather than push the right section, so the clock and
the power button can't move whatever happens. The sums below just make it fill the gap exactly.
"""
import importlib.machinery, importlib.util, json, os, shutil, signal, socket, subprocess, threading, time

# The reference: on the 1366 px screen the gap is GAP_REF px with a TITLE_REF-character title, the
# group count "⊞ 1/1", the play button showing, no indicator and the short clock. Every difference
# from that is added or taken off below. A wider screen adds its width. (The bar has no tray: apps
# with a tray icon have their own workspace.)
GAP_REF, TITLE_REF = 196, 30   # measured with the Settings button on the right (the agentmux one left again)
COUNT_REF = "\U000f0570 1/1"   # the group count's text in the reference
CLOCK_REF_LEN = 17             # "Thu 01 Oct  22:43": the short clock
CHAR_PX = 7.0         # px per character of the 12 px bar font (JetBrains Mono; measured with Pango)
EMPTY_PX = 38         # px freed on an empty workspace besides the title text: the title's padding
                      # (14) and the count " 1/1" (28), less the 4 px the lone icon gets back
PLAYER_PX = 30        # px: the play button with its divider
INDICATOR_PX = 19     # px per active indicator (idle off / do not disturb)
PADDING = 28          # px: this module's own left + right padding (style.css #custom-visualizer)
GLYPH_PX = 8.0        # px: one block character at 13 px (measured with Pango)
MIN_GAP_PX = 1.0      # px: the least space between two bars
FIT_SLACK = 0         # px kept free: none, the gap is measured to the pixel (text_px), and a
                      # pixel left over showed as a dark line between the hover block and the divider
HAIR = "\u200a"       # hair space: exactly 1 px in this font (measured with Pango). The bars are
                      # spaced with these, not with Pango letter_spacing: a label with letter spacing
                      # gets its last character cut ("…") even when it has all the room it asked for

PROBE_Y = 4           # px from the top: the bar row screenshotted to find the dividers (above the bars)
DIVIDER = (0x2F, 0x35, 0x4D)   # a section divider as drawn: @line at 70 % over the bar (style.css)

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

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PID_FILE = os.path.join(RUN, "waybar-visualizer.pid")   # clock.py / toggle.sh signal this pid
STATE_FILE = os.path.join(RUN, "waybar-visualizer.state")


def load(name, path):
    """Another bar script as a module, to read exactly what it shows."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


wintitle = load("wintitle", os.path.join(HERE, "wintitle.py"))
clock = load("clock", os.path.join(HERE, "clock.py"))
wsgroups = load("wsgroups", os.path.expanduser("~/.local/bin/wsgroups"))


def screen_width():
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout
        return min(m["width"] / m.get("scale", 1) for m in json.loads(out))
    except (OSError, ValueError, KeyError):
        return 1366


WIDTH = screen_width()
state = {"title": "x" * TITLE_REF, "count": COUNT_REF, "player": True,
         "indicators": 0, "clock": CLOCK_REF_LEN, "shown": 16, "gaps": [1] * 15, "playing": False}


_layout = None


def text_px(text):
    """How wide text is in the bar's 12 px font, as GTK draws it (Pango, with the same font fallback:
    an emoji or a CJK character in a title is wider than CHAR_PX)."""
    global _layout
    if _layout is None:
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo
        import cairo
        _layout = PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)))
        _layout.set_font_description(Pango.FontDescription.from_string("JetBrainsMono Nerd Font 12px"))
    _layout.set_text(text, -1)
    return _layout.get_extents()[1].width / 1024   # Pango units


def measured_gap():
    """The gap as the bar draws it now: from the workspace buttons' divider to the right section's
    first one, in a one-pixel-high screenshot (grim, PPM). None if it can't tell."""
    try:
        out = subprocess.run(["grim", "-g", f"0,{PROBE_Y} {int(WIDTH)}x1", "-t", "ppm", "-"],
                             capture_output=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    head = out.split(b"\n", 3)
    if len(head) < 4 or head[0] != b"P6":
        return None
    px, w = head[3], int(head[1].split()[0])

    def divider(x):
        r, g, b = px[3 * x:3 * x + 3]
        return abs(r - DIVIDER[0]) <= 4 and abs(g - DIVIDER[1]) <= 4 and abs(b - DIVIDER[2]) <= 5
    lines = [x for x in range(1, w - 1) if divider(x) and not divider(x - 1) and not divider(x + 1)]
    if len(lines) < 2 or lines[0] > w / 2:
        return None
    return (lines[1] - lines[0] - 1) * WIDTH / w   # px as the bar counts them (a scaled screen)


def modelled_gap():
    """The gap worked out from what's on the right (when it can't be measured)."""
    s = state
    gap = GAP_REF + WIDTH - 1366
    if s["title"]:
        gap += TITLE_REF * CHAR_PX - text_px(s["title"]) + text_px(COUNT_REF) - text_px(s["count"])
    else:
        gap += TITLE_REF * CHAR_PX + EMPTY_PX
    if not s["player"]:
        gap += PLAYER_PX
    gap -= s["indicators"] * INDICATOR_PX
    gap -= (s["clock"] - CLOCK_REF_LEN) * CHAR_PX
    return gap


LOCK = threading.Lock()   # one recount at a time: Pango isn't thread-safe (two threads measuring
                          # text at once aborted the whole script: "fc_thread_func: code should not
                          # be reached"), and the bars froze until waybar restarted it
WAKE = threading.Event()  # SIGUSR1: the tick thread recounts now (not the handler itself, which
                          # could run in the middle of a Pango call on the main thread)


def recount():
    """How many bars fit now and how far apart, so they fill the gap exactly (read by the loop).
    The gap is measured while the bars show; silent, the sums do (no screenshots while hidden)."""
    with LOCK:
        _recount()


def _recount():
    s = state
    measured = measured_gap() if s["playing"] else None
    gap = measured if measured is not None else modelled_gap()
    s["measured"] = measured is not None
    room = gap - PADDING - FIT_SLACK
    # n bars and the n - 1 spaces between them fill the room; none after the last bar, so both
    # ends of the block have the same padding
    n = fit(room)
    # the px left after the bars, shared out over the n - 1 gaps as whole hair spaces (some gaps one
    # wider than others), so the bars fill the room to the pixel; none after the last bar
    spare = max(int(room - n * GLYPH_PX), 0)
    s["shown"], s["gaps"] = n, [spare * (i + 1) // max(n - 1, 1) - spare * i // max(n - 1, 1)
                                for i in range(max(n - 1, 0))]
    try:   # what it thinks the bar looks like, for debugging (cat $XDG_RUNTIME_DIR/waybar-visualizer.state)
        with open(STATE_FILE, "w") as f:
            json.dump({**s, "gap": round(gap, 1), "room": round(room, 1)}, f, ensure_ascii=False)
    except OSError:
        pass


def fit(room):
    """How many bars fit in room px (0 if not even two)."""
    n = int((room + MIN_GAP_PX) / (GLYPH_PX + MIN_GAP_PX))
    return n if n >= 2 else 0


def read_windows():
    state["title"] = wintitle.title()["text"]
    state["count"] = wsgroups.bar_status()["text"]


def follow_windows():
    """Hyprland's event socket: the title and the group count change with the focused window."""
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket2.sock")
    wanted = (b"activewindow>>", b"windowtitle>>", b"workspace>>", b"openwindow>>", b"closewindow>>",
              b"movewindow>>")
    while True:
        read_windows()
        recount()
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock)
            buf = b""
            while data := s.recv(4096):
                buf += data
                *events, buf = buf.split(b"\n")
                if any(e.startswith(wanted) for e in events):
                    read_windows()
                    recount()
                    # again once waybar has redrawn the right side (the measurement sees the bar)
                    threading.Timer(0.3, recount).start()
        except OSError:
            time.sleep(2)


def follow_player():
    """The play button shows while a player is playing or paused: playerctl reports each change."""
    while True:
        p = subprocess.Popen(["playerctl", "--follow", "status"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        for line in p.stdout:
            state["player"] = line.strip() in ("Playing", "Paused")
            recount()
        p.wait()
        state["player"] = False
        recount()
        time.sleep(2)


def indicators():
    """Active indicators, by the same tests as scripts/indicator.sh."""
    idle_off = subprocess.run(["pgrep", "-x", "hypridle"], capture_output=True).returncode != 0
    mode = subprocess.run(["makoctl", "mode"], capture_output=True, text=True).stdout.split()
    return int(idle_off) + int("do-not-disturb" in mode)


def read_rest():
    state["indicators"] = indicators()
    state["clock"] = len(clock.plain_text(clock.load()["long"]))


def tick():
    """Indicators and the clock's view: every second (and at once on SIGUSR1)."""
    while True:
        read_rest()
        recount()
        WAKE.wait(1)
        WAKE.clear()


def emit(text, cls):
    print(json.dumps({"text": text, "class": cls, "tooltip": ""}, ensure_ascii=False), flush=True)


def main():
    emit("", "silent")
    while not shutil.which("cava"):
        time.sleep(10)           # not installed (yet): stay hidden, start once it is
    conf = os.path.join(RUN, "waybar-visualizer.conf")
    with open(conf, "w") as f:
        f.write(CONFIG)
    read_rest()
    text_px(COUNT_REF)   # Pango set up here, on one thread, before the others start
    signal.signal(signal.SIGUSR1, lambda *_: WAKE.set())
    with open(PID_FILE, "w") as f:   # who to signal: by pid, a name match would hit editors too
        f.write(str(os.getpid()))
    for watcher in (follow_windows, follow_player, tick):
        threading.Thread(target=watcher, daemon=True).start()
    cava = subprocess.Popen(["cava", "-p", conf], stdout=subprocess.PIPE, text=True, bufsize=1)
    last, heard = None, 0.0
    for line in cava.stdout:
        levels = [int(v) for v in line.strip().rstrip(";").split(";") if v.isdigit()]
        now = time.time()
        if any(levels):
            heard = now
        n = state["shown"]
        if now - heard > SILENT_AFTER or not levels or not n:
            out = ("", "silent")
            state["playing"] = False
        elif not state["playing"]:   # starts showing: measure the gap first
            state["playing"] = True
            recount()
            continue
        else:
            raw = len(levels)
            bars = (max(levels[i * raw // n:(i + 1) * raw // n] or [0]) for i in range(n))
            chars = [BLOCKS[min(v, len(BLOCKS) - 1)] if v else " " for v in bars]   # silent: blank
            gaps = state["gaps"]
            out = (chars[0] + "".join(HAIR * g + c for g, c in zip(gaps, chars[1:])), "playing")
        if out != last:
            emit(*out)
            last = out
    raise SystemExit(cava.wait())


if __name__ == "__main__":
    main()
