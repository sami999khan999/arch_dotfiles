#!/usr/bin/env python3
"""visualizer.py — waybar module: bars that move with whatever audio is playing (cava).

Runs cava with raw text output and turns each frame ("0;3;7;…") into block characters (▁▂▃…█).
The bars fill the gap between the workspace buttons and the right section of the bar, to the pixel.
Everything on the right that can change width is followed live, so the bars make room and nothing
gets pushed off the screen: the window title and the group count (their text), the play button,
the tray icons, the idle / do-not-disturb indicators and the clock's view (short or long).
cava always makes RAW_BARS; each shown bar is the loudest of its share of them.
Silent for SILENT_AFTER seconds: prints empty text, so waybar hides the module until something
plays again. Prints one JSON line per change (return-type json). SIGUSR1: recount now (clock.py
and toggle.sh send it when the clock's view or an indicator changes, to the pid in PID_FILE).

It sits in the bar's centre slot and may shrink (max-length in config.jsonc): GTK lays it out in
the space the two sides leave and never lets it push them, so the clock and the power button can't
move whatever happens. The sums below just make it fill that space exactly.
"""
import importlib.machinery, importlib.util, json, os, shutil, signal, socket, subprocess, threading, time

# The reference: on the 1366 px screen the gap is GAP_REF px with a TITLE_REF-character title, the
# group count "⊞ 1/1", the play button showing, TRAY_REF tray icons, no indicator and the short
# clock. Every difference from that is added or taken off below. A wider screen adds its width.
GAP_REF, TITLE_REF = 192, 30
COUNT_REF = "\U000f0570 1/1"   # the group count's text in the reference
CLOCK_REF_LEN = 17             # "Thu 01 Oct  22:43": the short clock
CHAR_PX = 7.0         # px per character of the 12 px bar font (JetBrains Mono; measured with Pango)
TRAY_REF = 1          # tray icons in the reference
TRAY_ICON_PX = 29     # px per tray icon: 12 px icon + 17 px spacing (config.jsonc "tray")
TRAY_PAD = 3          # px: the tray's own padding (20) less one spacing; 0 with no icons (hidden)
EMPTY_PX = 38         # px freed on an empty workspace besides the title text: the title's padding
                      # (14) and the count " 1/1" (28), less the 4 px the lone icon gets back
PLAYER_PX = 30        # px: the play button with its divider
INDICATOR_PX = 19     # px per active indicator (idle off / do not disturb)
PADDING = 28          # px: this module's own left + right padding (style.css #custom-visualizer)
GLYPH_PX = 8.0        # px: one block character at 13 px (measured with Pango)
MIN_GAP_PX = 1.0      # px: the least space between two bars
FIT_SLACK = 4         # px kept free: a label even 1 px too wide is ellipsized by GTK (a lone "…")
PANGO_PER_PX = 1024   # Pango letter_spacing units per pixel (measured)

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
state = {"title": "x" * TITLE_REF, "count": COUNT_REF, "player": True, "tray": TRAY_REF,
         "indicators": 0, "clock": CLOCK_REF_LEN, "shown": 16, "spacing": 1.0}


def tray_px(icons):
    return icons * TRAY_ICON_PX + TRAY_PAD if icons else 0


def recount():
    """How many bars fit now and how far apart, so they fill the gap exactly (read by the loop)."""
    s = state
    gap = GAP_REF + WIDTH - 1366
    if s["title"]:
        gap += (TITLE_REF - len(s["title"])) * CHAR_PX + (len(COUNT_REF) - len(s["count"])) * CHAR_PX
    else:
        gap += TITLE_REF * CHAR_PX + EMPTY_PX
    if not s["player"]:
        gap += PLAYER_PX
    gap += tray_px(TRAY_REF) - tray_px(s["tray"])
    gap -= s["indicators"] * INDICATOR_PX
    gap -= (s["clock"] - CLOCK_REF_LEN) * CHAR_PX
    room = gap - PADDING - FIT_SLACK
    # n bars and the n - 1 spaces between them fill the room; none after the last bar, so both
    # ends of the block have the same padding
    n = fit(room)
    s["shown"], s["spacing"] = n, (max((room - n * GLYPH_PX) / (n - 1), 0) if n > 1 else 0)


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


def tray_icons():
    """How many tray icons there are: the StatusNotifier watcher's list, the same one waybar shows."""
    out = subprocess.run(["busctl", "--user", "get-property", "org.kde.StatusNotifierWatcher",
                          "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                          "RegisteredStatusNotifierItems"], capture_output=True, text=True).stdout.split()
    return int(out[1]) if len(out) > 1 and out[1].isdigit() else 0


def indicators():
    """Active indicators, by the same tests as scripts/indicator.sh."""
    idle_off = subprocess.run(["pgrep", "-x", "hypridle"], capture_output=True).returncode != 0
    mode = subprocess.run(["makoctl", "mode"], capture_output=True, text=True).stdout.split()
    return int(idle_off) + int("do-not-disturb" in mode)


def read_rest():
    state["tray"] = tray_icons()
    state["indicators"] = indicators()
    state["clock"] = len(clock.plain_text(clock.load()["long"]))


def tick():
    """Tray icons, indicators and the clock's view: every second (and at once on SIGUSR1)."""
    while True:
        read_rest()
        recount()
        time.sleep(1)


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
    signal.signal(signal.SIGUSR1, lambda *_: (read_rest(), recount()))
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
        else:
            raw = len(levels)
            bars = (max(levels[i * raw // n:(i + 1) * raw // n] or [0]) for i in range(n))
            # a silent bar is blank, not ▁: no grey floor line along an idle stretch
            text = "".join(BLOCKS[min(v, len(BLOCKS) - 1)] if v else " " for v in bars)
            spacing = round(state["spacing"] * PANGO_PER_PX)
            # letter spacing goes after each character: leave it off the last one
            out = (f'<span letter_spacing="{spacing}">{text[:-1]}</span>{text[-1]}', "playing")
        if out != last:
            emit(*out)
            last = out
    raise SystemExit(cava.wait())


if __name__ == "__main__":
    main()
