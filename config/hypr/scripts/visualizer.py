#!/usr/bin/env python3
"""visualizer.py — waybar module: a graph that moves with whatever audio is playing (cava).

  visualizer.py                the module itself (waybar's custom/visualizer)
  visualizer.py cycle          the next style (Super + Alt + B, right click), off last
  visualizer.py set <style>    that style

Styles (cava's frame "0;3;7;…" drawn in block or braille characters):
  bars     bars (▁▂▃…█) with a little space between them
  solid    the same, side by side: one strip (the lowest bar where it's quiet, never a hole)
  mirror   bars mirrored: the bass in the middle, the treble out at both ends
  dots     braille: two columns of four dots per character, twice as fine as the bars
  wave     a moving line of braille dots: three sine waves added up, one each for the bass, the
           mids and the treble, each as tall as its band is loud, each drifting at its own speed
  off      nothing

The style is kept in $XDG_STATE_HOME/visualizer-style (it survives a restart); every running copy
(waybar runs one per bar) is told by SIGUSR2 to the pids in PID_DIR. Silent for SILENT_AFTER
seconds: prints empty text, so waybar hides the module until something plays again. Click:
play / pause.

The graph fills the gap between the group count + window title block (right after the workspace
buttons) and the right section of the bar, to the pixel. The gap is measured on the bar itself: a
one-pixel-high screenshot of the bar (grim) finds the divider before the block and the first one of
the right section; the block's width is its text (Pango) and its padding. That's done once waybar has redrawn after a change it hears of (the title, the play button, an indicator, the
clock) and every 5 s for what nothing announces (the code-backup icon). Silent, it does
nothing at all: no events followed, no timer; it catches up when the graph starts. Without a
measurement (grim missing, the dividers not found) the gap is worked out from what's on the right:
the window title and the group count (their text), the play button, the idle / do-not-disturb
indicators and the clock's view (short or long). cava always makes RAW_BARS; each shown bar is the
loudest of its share of them. SIGUSR1: recount now (clock.py and toggle.sh send it when the clock's
view or an indicator changes).

While nothing plays (silent, or style off: then it can't tell) the title may be long (TITLE_LONG, SIGUSR1 to
wintitle.py), and GTK cuts it to the room it has (max-length): the gap is the title's, and the right
section is never pushed. While something plays it stays short, the graph shown or not.

It's the last module of the left section and may shrink (max-length; the bar has no centre
section, see config.jsonc): GTK then shrinks it rather than push the right section, so the clock and
the power button can't move whatever happens. The sums below just make it fill the gap exactly.
"""
import importlib.machinery, importlib.util, json, math, os, shutil, signal, subprocess, sys, threading, time

# The reference: on the 1366 px screen the gap is GAP_REF px with a TITLE_REF-character title, the
# group count "⊞ 1/1", the play button showing, no indicator and the short clock. Every difference
# from that is added or taken off below. A wider screen adds its width. (The bar has no tray: apps
# with a tray icon have their own workspace.)
# measured with the Settings button on the right (the agentmux one left again), less the frame
# counter that came after (custom/fps "󰓅 60/60": 20 px padding + 7 characters = 69 px)
# then 202 px more when the controls (Setups … volume at 3 digits) went into the hub's one icon (bartips)
# and 93 px less for the network speed in the stats (custom/netspeed: two small sparklines), 62 px less
# for the storage chip (custom/storage; the GPU's shows only with an NVIDIA / AMD card, not modelled),
# and 93 px more again when the network speed left the bar
GAP_REF, TITLE_REF = 267, 30
COUNT_REF = "\U000f0570 1/1"   # the group count's text in the reference
CLOCK_REF_LEN = 17             # "Thu 01 Oct  22:43": the short clock
CHAR_PX = 7.0         # px per character of the 12 px bar font (JetBrains Mono; measured with Pango)
EMPTY_PX = 38         # px freed on an empty workspace besides the title text: the title's padding
                      # (14) and the count " 1/1" (28), less the 4 px the lone icon gets back
PLAYER_PX = 30        # px: the play button with its divider
INDICATOR_PX = 19     # px: the idle-off indicator, when shown
BELL_PX = 28          # px: the notification bell with its divider (always shown; not in GAP_REF)
LOAD_PAD_PX = 12      # px: the busy-CPU warning's padding (custom/load, scripts/loadwatch.py), when shown
LOAD_STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "loadwatch.json")
PADDING = 28          # px: this module's own left + right padding (style.css #custom-visualizer)
GLYPH_PX = 8.0        # px: one block or braille character at 13 px (measured with Pango)
MIN_GAP_PX = 1.0      # px: the least space between two bars ("bars", "mirror")
MIN_ROOM_PX = 80      # px: with less room than this the graph hides: a few bars squeezed in reads as
                      # clutter, not as a graph
HAIR = " "       # hair space: exactly 1 px in this font (measured with Pango). The bars are
                      # spaced with these, not with Pango letter_spacing: a label with letter spacing
                      # gets its last character cut ("…") even when it has all the room it asked for

BLOCK_PX = 31         # px of the group count + title block besides their text: the divider before it and
                      # the paddings, the count's 10 + 6 and the title's 4 + 10 (style.css)
BLOCK_EMPTY_PX = 21   # an empty workspace: the divider and the count's even padding (10 + 10), no title
POWER_PX = 40         # px: the power button's divider is closer than this to the right end of the bar
PROBE_Y = 2           # px from the top: the bar row screenshotted to find the dividers (above the
                      # bars, whose soft top edge at y 4 is near the divider colour)
DIVIDER = (0x2F, 0x35, 0x4D)   # a section divider as drawn: @line at 70 % over the bar (style.css)

STYLES = ["bars", "solid", "mirror", "dots", "wave", "off"]
RAW_BARS = 64         # what cava makes; more than ever fit
FPS = 25              # frames per second (cava's framerate; also how often waybar can redraw)
SILENT_AFTER = 2.0    # seconds of silence before the module hides
MEASURE_EVERY = 5     # s: while it shows, the gap is measured again this often (and after each change it
                      # hears of: a change on the right that nothing announces, the code-backup icon)
SETTLE = 0.3          # s: after a change, waybar has redrawn the bar by then (the measurement sees the bar)
BLOCKS = "▁▂▃▄▅▆▇█"
# braille dots from the bottom up: the left column (dots 7 3 2 1), the right one (8 6 5 4)
DOTS_LEFT, DOTS_RIGHT = (0x40, 0x04, 0x02, 0x01), (0x80, 0x20, 0x10, 0x08)
# "wave": (share of cava's bars, waves across the whole graph, drift in cycles per second); the bass a
# long slow swell, the treble short quick ripples; opposite drifts so they weave
WAVES = [((0, 8), 1.5, 0.35), ((8, 28), 4.0, -0.8), ((28, 64), 9.0, 1.6)]
EASE = 0.25           # "wave": how fast its height follows the sound (per frame; 1 = at once)

# sleep_timer: cava goes quiet after that many seconds of silence. Longer than SILENT_AFTER, so the
# loop still sees the silence and hides the module (at 1 it never did, and kept measuring the bar)
CONFIG = f"""
[general]
bars = {RAW_BARS}
framerate = {FPS}
sleep_timer = 3

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
sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from hyprsock import follow   # the event socket, reconnecting with a pause
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PID_DIR = os.path.join(RUN, "waybar-visualizer")   # one file per running copy, named by its pid
STATE_FILE = os.path.join(RUN, "waybar-visualizer.state")
TITLE_LONG = os.path.join(RUN, "waybar-wintitle.long")   # "1": the title may be long (wintitle.py)
TITLE_PIDS = os.path.join(RUN, "waybar-wintitle")         # its copies, told by SIGUSR1
STYLE_FILE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "visualizer-style")


def read_style():
    try:
        style = open(STYLE_FILE).read().strip()
    except OSError:
        style = ""
    return style if style in STYLES else STYLES[0]


def set_style(style):
    """Keep the style and tell every running copy (one per bar)."""
    os.makedirs(os.path.dirname(STYLE_FILE), exist_ok=True)
    with open(STYLE_FILE, "w") as f:
        f.write(style)
    for name in os.listdir(PID_DIR) if os.path.isdir(PID_DIR) else []:
        try:
            os.kill(int(name), signal.SIGUSR2)
        except (ValueError, ProcessLookupError, PermissionError):
            os.remove(os.path.join(PID_DIR, name))


def load(name, path):
    """Another bar script as a module, to read exactly what it shows."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def screen_width():
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout
        return min(m["width"] / m.get("scale", 1) for m in json.loads(out))
    except (OSError, ValueError, KeyError):
        return 1366


state = {"title": "x" * TITLE_REF, "count": COUNT_REF, "player": True, "indicators": 0, "load": "",
         "clock": CLOCK_REF_LEN, "style": STYLES[0], "shown": 16, "gaps": [1] * 15,
         "lead": 0, "trail": 0, "playing": False, "covered": False, "empty": False, "long": None}

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


_anchors = None   # (the first divider, the power button's): neither ever moves
_moved = None     # other ones seen once: taken if the next measurement sees them too


def anchored(first, last, w):
    """Whether the screenshot shows the bar settled. The divider after the workspace buttons and the power
    button's never move: elsewhere, the right section was pushed (a title too long) or the bar was still
    being drawn (just out of fullscreen everything sat 30 px to the right). A real change (the screen, the
    workspace buttons) shows twice in a row and is taken."""
    global _anchors, _moved
    if last < w - POWER_PX * w / WIDTH:   # the power button isn't at the right end
        return False
    if (first, last) == _anchors:
        return True
    if _anchors is None or (first, last) == _moved:
        _anchors, _moved = (first, last), None
        return True
    _moved = (first, last)
    settle()   # look again once the bar has settled
    return False


def measured_gap(plain):
    """The gap as the bar draws it now, in a one-pixel-high screenshot (grim, PPM): from where the group
    count + title block ends with the title at its usual length (plain) to the right section's first
    divider. The block starts at the first divider (the workspace buttons end there) and is its text and
    padding wide, so where it ends is worked out (Pango), not seen: while the graph is hidden the title
    is padded on to the right section and nothing marks it. None if it can't tell."""
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
    if len(lines) < 2 or lines[0] > w / 2 or not anchored(lines[0], lines[-1], w):
        return None
    scale = w / WIDTH   # screenshot px per bar px (a scaled screen)
    if plain:
        block = BLOCK_PX + text_px(state["count"]) + text_px(plain)
    else:   # no title (an empty workspace, or no room left for one): the count alone
        block = (BLOCK_EMPTY_PX if state["empty"] else BLOCK_PX - 14) + text_px(state["count"])
    end = lines[0] + block * scale   # where the graph's divider is (or would be)
    right = next((x for x in lines if x > end + 1), None)   # past the graph's own divider
    if right is None:
        return None
    return (right - end - 1) / scale


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
    gap -= s["indicators"] * INDICATOR_PX + BELL_PX
    if s["load"]:
        gap -= LOAD_PAD_PX + text_px(s["load"])
    gap -= (s["clock"] - CLOCK_REF_LEN) * CHAR_PX
    return gap


LOCK = threading.Lock()   # one recount at a time: Pango isn't thread-safe (two threads measuring
                          # text at once aborted the whole script: "fc_thread_func: code should not
                          # be reached"), and the bars froze until waybar restarted it
WAKE = threading.Event()  # SIGUSR1 / SIGUSR2: the tick thread recounts now (not the handler itself,
                          # which could run in the middle of a Pango call on the main thread)
SETTLING = threading.Event()   # settle(): one recount SETTLE s after the last of a burst of changes


def recount():
    """How many characters fit now and the hair spaces around them, so the graph fills the gap
    exactly (read by the loop). The gap is measured while it shows; silent, the sums do."""
    with LOCK:
        _recount()


def settle():
    """Recount once waybar has redrawn the right side after a change: one timer for a burst of events
    (a thread and a screenshot per event was the old way)."""
    SETTLING.set()


def settler():
    while True:
        SETTLING.wait()
        while SETTLING.is_set():   # each new change restarts the wait
            SETTLING.clear()
            time.sleep(SETTLE)
        recount()


def covered():
    """A fullscreen window over the bar (a video): nothing of the bar shows, so nothing to measure."""
    ws = (wsgroups.query("activeworkspace") or {}).get("id")
    return any(c["workspace"]["id"] == ws and c.get("fullscreen") == 2 for c in wsgroups.query("clients") or [])


def _recount():
    s = state
    s["covered"] = covered()
    if s["covered"]:   # nothing drawn meanwhile; its fullscreen>> event recounts once it's gone
        return
    set_long(not s["playing"] or s["style"] == "off")
    if not s["playing"] or s["style"] == "off":   # no graph: nothing to measure
        s["shown"] = 0
        return
    read_windows()   # the count and title the block's width is worked out from, as they are now
    s["title"] = wintitle.title(False)["text"]
    measured = measured_gap(s["title"])
    gap = measured if measured is not None else modelled_gap()
    s["measured"] = measured is not None
    room = gap - PADDING
    if room < MIN_ROOM_PX:
        room = 0   # n comes out 0: frames() hides the module until a recount finds room again
    if s["style"] in ("bars", "mirror"):
        # n bars and the n - 1 spaces between them fill the room; none after the last bar, so both
        # ends of the block have the same padding. The px left after the bars are shared out over
        # the n - 1 gaps as whole hair spaces (some gaps one wider than others), to the pixel
        n = int((room + MIN_GAP_PX) / (GLYPH_PX + MIN_GAP_PX))
        n = n if n >= 2 else 0
        spare = max(int(room - n * GLYPH_PX), 0)
        s["shown"], s["lead"], s["trail"] = n, 0, 0
        s["gaps"] = [spare * (i + 1) // max(n - 1, 1) - spare * i // max(n - 1, 1) for i in range(max(n - 1, 0))]
    else:
        # side by side ("solid", "dots", "wave"): what the whole characters leave is shared out before
        # and after them (hair spaces), so both ends have the same padding (all of it before made the
        # left 3 px wider than the right)
        n = int(room // GLYPH_PX)
        spare = max(int(room - n * GLYPH_PX), 0)
        s["shown"], s["gaps"], s["lead"], s["trail"] = (n if n >= 2 else 0), [], spare // 2, spare - spare // 2
    try:   # what it thinks the bar looks like, for debugging (cat $XDG_RUNTIME_DIR/waybar-visualizer.state)
        with open(STATE_FILE, "w") as f:
            json.dump({**s, "gap": round(gap, 1), "room": round(room, 1)}, f, ensure_ascii=False)
    except OSError:
        pass


def set_long(on):
    """Tell the title whether it may be long (wintitle.py): nothing plays."""
    if on == state["long"]:
        return
    state["long"] = on
    try:
        with open(TITLE_LONG, "w") as f:
            f.write("1" if on else "0")
        for pid in os.listdir(TITLE_PIDS):
            try:
                os.kill(int(pid), signal.SIGUSR1)
            except (ValueError, OSError):
                pass
    except OSError:
        pass
    settle()   # measure again once the bar has redrawn the title


def shares(levels, n):
    """n values out of cava's: each the loudest of its share."""
    raw = len(levels)
    return [max(levels[i * raw // n:(i + 1) * raw // n] or [0]) for i in range(n)]


def draw(levels):
    """One frame in the current style, exactly as wide as the room (state's shown, gaps, lead)."""
    s, n = state, state["shown"]
    block = lambda v: BLOCKS[min(v, len(BLOCKS) - 1)] if v else " "   # silent: blank
    if s["style"] == "bars":
        chars = [block(v) for v in shares(levels, n)]
    elif s["style"] == "mirror":
        half = shares(levels, (n + 1) // 2)   # bass first: it goes in the middle
        chars = [block(v) for v in list(reversed(half))[:n // 2] + half]
    elif s["style"] == "wave":
        return HAIR * s["lead"] + wave(levels, n) + HAIR * s["trail"]
    elif s["style"] == "solid":
        return HAIR * s["lead"] + "".join(BLOCKS[min(v, len(BLOCKS) - 1)] for v in shares(levels, n)) + HAIR * s["trail"]
    else:   # dots: two columns per character, 0-4 dots each
        cols = shares(levels, 2 * n)
        dots = lambda v: (v * 4 + len(BLOCKS) - 2) // (len(BLOCKS) - 1)   # 0..7 → 0..4, any sound shows
        out = ""
        for left, right in zip(cols[0::2], cols[1::2]):
            bits = sum(DOTS_LEFT[:dots(left)]) + sum(DOTS_RIGHT[:dots(right)])
            out += chr(0x2800 + bits) if bits else " "
        return HAIR * s["lead"] + out + HAIR * s["trail"]
    return chars[0] + "".join(HAIR * g + c for g, c in zip(s["gaps"], chars[1:]))


_heights = [0.0] * len(WAVES)   # "wave": each band's height now, eased towards the sound


def wave(levels, n):
    """n braille characters drawing one line: the sum of WAVES, each as tall as its band is loud. One
    dot per column (four rows); where the line climbs or falls more than a row between two columns,
    the dots between are filled, so it stays one unbroken line."""
    t, top = time.time(), len(BLOCKS) - 1
    for i, ((lo, hi), _, _) in enumerate(WAVES):
        band = levels[lo * len(levels) // RAW_BARS:hi * len(levels) // RAW_BARS] or [0]
        _heights[i] += (sum(band) / len(band) / top - _heights[i]) * EASE
    cols = 2 * n
    ys = [sum(h * math.sin(2 * math.pi * (k * x / cols + speed * t)) for h, (_, k, speed) in zip(_heights, WAVES))
          for x in range(cols)]
    # the line's own peak is the top: the waves seldom peak together, so scaled by their sum it kept to
    # the middle rows. Then as tall as the sound is loud: quiet, a small ripple; music, all 4 dot rows
    peak = max(map(abs, ys)) or 1
    loud = min(1.0, 0.25 + sum(_heights) / 1.2)
    rows = [min(3, max(0, round((y / peak * loud + 1) / 2 * 3))) for y in ys]   # 0: the bottom row
    out = ""
    for c in range(0, cols, 2):
        bits = 0
        for col, dots in ((c, DOTS_LEFT), (c + 1, DOTS_RIGHT)):
            prev = rows[col - 1] if col else rows[col]
            for r in range(min(prev, rows[col]), max(prev, rows[col]) + 1) if abs(prev - rows[col]) > 1 else (rows[col],):
                bits |= dots[r]
        out += chr(0x2800 + bits)
    return out


def read_windows():
    state["title"] = wintitle.title()["text"]
    status = wsgroups.bar_status()
    state["count"], state["empty"] = status["text"], "empty" in status["class"]


def follow_windows():
    """Hyprland's event socket: the title and the group count change with the focused window. Only
    while sound plays: silent, nothing is drawn (the title may be long), and catch_up() reads them when
    it starts."""
    wanted = (b"activewindow>>", b"workspace>>", b"openwindow>>", b"closewindow>>", b"movewindow>>",
              b"fullscreen>>")

    def events(lines):
        if not state["playing"]:
            return
        # another window's title (a spinner in a terminal elsewhere) changes nothing on the bar
        if any(e.startswith(wanted) or (e.startswith(b"windowtitle>>") and
                                         "0x" + e[13:].decode(errors="ignore") == wintitle.shown)
               for e in lines):
            read_windows()
            settle()
    follow(events)


def follow_player():
    """The play button shows while a player is playing or paused: playerctl reports each change."""
    while True:
        p = subprocess.Popen(["playerctl", "--follow", "status"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        for line in p.stdout:
            state["player"] = line.strip() in ("Playing", "Paused")
            settle()
        p.wait()
        state["player"] = False
        settle()
        time.sleep(2)


def indicators():
    """Indicators shown besides the bell: idle off (scripts/indicator.sh)."""
    return int(subprocess.run(["pgrep", "-x", "hypridle"], capture_output=True).returncode != 0)


def read_rest():
    state["indicators"] = indicators()
    try:   # the busy-CPU warning's text, "" while hidden (loadwatch.py; it sends SIGUSR1 on a change)
        state["load"] = json.load(open(LOAD_STATE)).get("text", "")
    except (OSError, ValueError):
        state["load"] = ""
    state["clock"] = len(clock.plain_text(clock.load()["long"]))


def catch_up():
    """Everything the gap depends on, read afresh: the graph starts showing (silent, nobody follows
    them)."""
    read_windows()
    read_rest()
    state["style"] = read_style()


def tick():
    """SIGUSR1 (the clock's view, an indicator, the busy warning) / SIGUSR2 (the style): read them and
    recount. While sound plays, also every MEASURE_EVERY s (for what nothing announces, like the
    code-backup icon); silent, it sleeps until a signal."""
    signalled = True
    while True:
        if signalled:
            read_rest()
            state["style"] = read_style()
            pause_cava(state["style"] == "off")
        else:
            state["clock"] = len(clock.plain_text(clock.load()["long"]))   # the long view's day at midnight
        recount()
        signalled = WAKE.wait(MEASURE_EVERY if state["playing"] else None)
        WAKE.clear()


cava = None
CAVA_ON = threading.Event()   # a style other than "off": main() runs cava


def pause_cava(off):
    """Style "off": cava is ended, not just ignored, so nothing reads the sound 25 times a second; the
    next style starts it again. Ended, not frozen (SIGSTOP): a frozen cava stays in PipeWire's graph
    and can't answer it, so once its sink changed (the Bluetooth earbuds dropping their link while
    idle) every stream waited on it and nothing played anywhere, until it was let go."""
    if off:
        time.sleep(0.2)   # the loop has seen "off" by now and hidden the module
        if state["style"] == "off" and cava is not None and cava.poll() is None:
            cava.terminate()
    else:
        CAVA_ON.set()


def emit(text, cls):
    print(json.dumps({"text": text, "class": cls, "tooltip": ""}, ensure_ascii=False), flush=True)


def main():
    global WIDTH, wintitle, clock, wsgroups, cava
    WIDTH = screen_width()
    wintitle = load("wintitle", os.path.join(HERE, "wintitle.py"))
    clock = load("clock", os.path.join(HERE, "clock.py"))
    import wsgroups   # ~/.local/lib/hypr, on the path for hyprsock
    emit("", "silent")
    while not shutil.which("cava"):
        time.sleep(10)           # not installed (yet): stay hidden, start once it is
    conf = os.path.join(RUN, "waybar-visualizer.conf")
    with open(conf, "w") as f:
        f.write(CONFIG)
    read_rest()
    state["style"] = read_style()
    text_px(COUNT_REF)   # Pango set up here, on one thread, before the others start
    os.makedirs(PID_DIR, exist_ok=True)
    me = os.path.join(PID_DIR, str(os.getpid()))   # who to signal: by pid, a name match would hit editors too
    open(me, "w").close()
    signal.signal(signal.SIGUSR1, lambda *_: WAKE.set())
    signal.signal(signal.SIGUSR2, lambda *_: WAKE.set())
    signal.signal(signal.SIGTERM, lambda *_: (os.path.exists(me) and os.remove(me), os._exit(0)))
    for watcher in (follow_windows, follow_player, tick, settler):
        threading.Thread(target=watcher, daemon=True).start()   # tick sets CAVA_ON unless the style is "off"
    last = None
    while True:
        CAVA_ON.clear()   # before the check: a style set after it sets the event again
        if state["style"] == "off":
            CAVA_ON.wait()
            continue
        cava = subprocess.Popen(["cava", "-p", conf], stdout=subprocess.PIPE, text=True, bufsize=1)
        last = frames(last)
        code = cava.wait()
        if state["style"] != "off":   # it died by itself: waybar starts the script again
            raise SystemExit(code)
        state["playing"] = False   # quiet, cava may have sent no frame that saw "off"
        if last != ("", "silent"):
            emit("", "silent")
            last = ("", "silent")


def frames(last):
    """cava's frames, drawn, until it ends (style "off" ends it). Returns the last thing shown."""
    heard = 0.0
    for line in cava.stdout:
        hushed = False
        levels = [int(v) for v in line.strip().rstrip(";").split(";") if v.isdigit()]
        now = time.time()
        if any(levels):
            heard = now
        if state["style"] == "off" or now - heard > SILENT_AFTER or not levels:
            out = ("", "silent")
            if state["playing"]:
                state["playing"] = False
                hushed = True
        elif not state["playing"]:   # starts showing: measure the gap first
            catch_up()
            state["playing"] = True
            recount()
            WAKE.set()   # the tick thread measures every MEASURE_EVERY s from now
            continue
        elif state["covered"] or not state["shown"]:   # under a fullscreen window, or no room (the
            out = ("", "silent")                          # title has it): still measured, nothing drawn
        else:
            out = (draw(levels), state["style"])
        if out != last:
            emit(*out)
            last = out
        if hushed:    # once waybar has hidden the graph: the title block takes the gap
            settle()
    return last


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["cycle"]:
        set_style(STYLES[(STYLES.index(read_style()) + 1) % len(STYLES)])
    elif len(args) == 2 and args[0] == "set" and args[1] in STYLES:
        set_style(args[1])
    elif not args:
        main()
    else:
        sys.exit(__doc__)
