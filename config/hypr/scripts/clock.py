#!/usr/bin/env python3
"""clock.py — waybar module: the clock, in place of waybar's built-in one.

  clock.py               the module itself (waybar exec): one JSON line per minute, and at once
                         when the view changes
  clock.py toggle        click: switch between the short and the long view

Same look as the built-in clock: short "Thu 01 Oct  22:43", long "Thursday  01 Oct  wk 40  22:43".
The month calendar shown on hover is drawn by the bar's tooltips (config/waybar/bartips).
It's a script so other scripts can know which view is on: the
visualizer (visualizer.py) needs the clock's width to fill the bar without pushing anything off.
"""
import datetime, json, os, signal, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
try:   # the colour theme (local/bin/theme): colours here are Tokyo Night's, recolored to the active theme's
    from themelib import recolor
except ImportError:
    def recolor(text, tid=None):
        return text

RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = f"{RUN}/waybar-clock.json"   # {"long": bool}
PID = f"{RUN}/waybar-clock.pid"

DIM, SUB = (recolor(c) for c in ("#565f89", "#a9b1d6"))   # the colour theme (local/bin/theme)
SETTINGS = os.path.expanduser("~/.config/hypr/settings.json")   # the Settings panel's 24-hour switch


def time_fmt():
    """%H:%M, or %I:%M %p when the Settings panel turned 24-hour time off (read on every draw, so
    the panel's SIGUSR1 shows the change at once)."""
    try:
        h24 = json.load(open(SETTINGS)).get("clock", {}).get("h24", True)
    except (OSError, ValueError):
        h24 = True
    return "%H:%M" if h24 else "%I:%M %p"


def load():
    try:
        s = json.load(open(STATE))
    except (OSError, ValueError):
        s = {}
    return {"long": bool(s.get("long"))}


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def plain_text(long, now=None):
    """What the bar shows, without markup (its length is what the visualizer needs)."""
    now = now or datetime.datetime.now()
    if long:
        return f"{now:%A}  {now:%d %b}  wk {now:%V}  {now:{time_fmt()}}"
    return f"{now:%a %d %b}  {now:{time_fmt()}}"


def time_markup(now):
    """The time in bold, a 12-hour clock's AM / PM smaller and dimmed after it."""
    t = f"{now:{time_fmt()}}"
    if t[-2:] in ("AM", "PM"):
        return f"<b>{t[:-3]}</b><span size='small' color='{DIM}'> {t[-2:]}</span>"
    return f"<b>{t}</b>"


def markup(long, now):
    """Day name dimmed, the date a step brighter, the time brightest."""
    if long:
        return (f"<span color='{DIM}'>{now:%A}</span>  <span color='{SUB}'>{now:%d %b}</span>  "
                f"<span color='{DIM}'>wk {now:%V}</span>  {time_markup(now)}")
    return f"<span color='{DIM}'>{now:%a}</span> <span color='{SUB}'>{now:%d %b}</span>  {time_markup(now)}"


def emit():
    state, now = load(), datetime.datetime.now()
    print(json.dumps({"text": markup(state["long"], now),
                      "class": "long" if state["long"] else "short"}, ensure_ascii=False), flush=True)


def serve():
    with open(PID, "w") as f:
        f.write(str(os.getpid()))
    # a signal doesn't cut the sleep short (Python resumes it), so the handler redraws itself
    signal.signal(signal.SIGUSR1, lambda *_: emit())
    while True:
        emit()
        now = time.time()
        time.sleep(60 - now % 60 + 0.05)   # until just past the next minute


def poke():
    """Redraw the clock now, and let the visualizer recount (the long view is wider): every copy of
    visualizer.py (one per bar) has its pid as a file name in waybar-visualizer/."""
    try:
        os.kill(int(open(PID).read()), signal.SIGUSR1)
    except (OSError, ValueError):
        pass
    for folder in ("waybar-visualizer",):
        try:
            pids = os.listdir(f"{RUN}/{folder}")
        except OSError:
            pids = []
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGUSR1)
            except (OSError, ValueError):
                pass


def main(argv):
    if len(argv) == 1:
        serve()
    elif argv[1] == "toggle":
        state = load()
        state["long"] = not state["long"]
        save(state)
        poke()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
