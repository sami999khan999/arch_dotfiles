#!/usr/bin/env python3
"""clock.py — waybar module: the clock, in place of waybar's built-in one.

  clock.py               the module itself (waybar exec): one JSON line per minute, and at once
                         when the view or the tooltip's month changes
  clock.py toggle        click: switch between the short and the long view
  clock.py scroll up     scroll: the tooltip calendar shows the previous / next month
  clock.py scroll down

Same look as the built-in clock: short "Thu 01 Oct  22:43", long "Thursday  01 Oct  wk 40  22:43",
a month calendar as the tooltip. It's a script so other scripts can know which view is on: the
visualizer (visualizer.py) needs the clock's width to fill the bar without pushing anything off.
"""
import calendar, datetime, json, os, signal, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
try:   # the colour theme (local/bin/theme): colours here are Tokyo Night's, recolored to the active theme's
    from themelib import recolor
except ImportError:
    def recolor(text, tid=None):
        return text

RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = f"{RUN}/waybar-clock.json"   # {"long": bool, "month": offset of the tooltip's month}
PID = f"{RUN}/waybar-clock.pid"

DIM, TEXT, LINE, ACCENT = (recolor(c) for c in ("#565f89", "#c0caf5", "#3b4261", "#6b8fe0"))   # the colour theme (local/bin/theme)
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
    return {"long": bool(s.get("long")), "month": int(s.get("month", 0))}


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def plain_text(long, now=None):
    """What the bar shows, without markup (its length is what the visualizer needs)."""
    now = now or datetime.datetime.now()
    if long:
        return f"{now:%A}  {now:%d %b}  wk {now:%V}  {now:{time_fmt()}}"
    return f"{now:%a %d %b}  {now:{time_fmt()}}"


def markup(long, now):
    if long:
        return (f"<span color='{DIM}'>{now:%A}</span>  {now:%d %b}  "
                f"<span color='{DIM}'>wk</span> {now:%V}  <b>{now:{time_fmt()}}</b>")
    return f"<span color='{DIM}'>{now:%a %d %b}</span>  <b>{now:{time_fmt()}}</b>"


def month_calendar(offset, today):
    """The tooltip: a month with ISO week numbers on the left, today highlighted."""
    y, m = divmod(today.year * 12 + today.month - 1 + offset, 12)
    m += 1
    title = datetime.date(y, m, 1).strftime("%B %Y")
    lines = [f"<span color='{TEXT}'><b>{title:^24}</b></span>",
             f"<span color='{DIM}'>    Mo Tu We Th Fr Sa Su</span>"]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(y, m):
        cells = []
        for d in week:
            if d.month != m:
                cells.append("  ")
            elif d == today:
                cells.append(f"<span color='{ACCENT}'><b><u>{d.day:2}</u></b></span>")
            else:
                cells.append(f"{d.day:2}")
        wk = week[3].isocalendar()[1]   # Thursday decides the ISO week
        lines.append(f"<span color='{LINE}'>{wk:2}</span>  " + " ".join(cells))
    return "<tt>" + "\n".join(lines) + "</tt>"


def emit():
    state, now = load(), datetime.datetime.now()
    print(json.dumps({"text": markup(state["long"], now),
                      "tooltip": month_calendar(state["month"], now.date()),
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
    """Redraw the clock now, and let the visualizer recount (the long view is wider)."""
    try:
        os.kill(int(open(PID).read()), signal.SIGUSR1)
    except (OSError, ValueError):
        pass
    try:
        os.kill(int(open(f"{RUN}/waybar-visualizer.pid").read()), signal.SIGUSR1)
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
    elif argv[1] == "scroll" and len(argv) == 3:
        state = load()
        state["month"] += -1 if argv[2] == "up" else 1
        save(state)
        poke()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
