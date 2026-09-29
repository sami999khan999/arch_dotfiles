#!/usr/bin/env python3
# audiopanel.py — audio card (Tokyo Night) for the Control Center: outputs, inputs and what's playing.
#   ↑↓ select   ←→ volume   m mute   enter make default   w wiremix (everything else)   q quit
import json, subprocess
from panelkit import (Panel, run, suspend, FG, DIM, ACCENT, KEY, TRACK, RED, BOLD, RESET,
                      fit, frame, card, spread, header, section, hints, highlight)

INTERVAL = 1.0
STEP = 5          # volume step, percent
MAX = 100         # the → key stops here


def pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True).stdout


def listing(kind):
    try:
        return json.loads(pactl("-f", "json", "list", kind) or "[]")
    except json.JSONDecodeError:
        return []


def percent(vol):
    """Average of the channels, in percent."""
    vals = [int(v["value_percent"].rstrip("%")) for v in vol.values()] or [0]
    return round(sum(vals) / len(vals))


def tidy(name):
    for junk in (" Analog Stereo", " Analog Mono", " Digital Stereo (HDMI)", " Audio Codec"):
        name = name.replace(junk, "")
    return name


def load():
    """Rows to draw: ("#", title) section headings and ("sink"|"source"|"input", item) entries."""
    default_sink, default_source = pactl("get-default-sink").strip(), pactl("get-default-source").strip()
    rows = [("#", "Output")]
    for s in listing("sinks"):
        rows.append(("sink", {"id": s["index"], "name": tidy(s["description"]), "vol": percent(s["volume"]),
                              "mute": s["mute"], "default": s["name"] == default_sink, "key": s["name"]}))
    rows += [("", ""), ("#", "Input")]
    for s in listing("sources"):
        if s.get("monitor_source"):  # "Monitor of …" loopbacks aren't microphones
            continue
        rows.append(("source", {"id": s["index"], "name": tidy(s["description"]), "vol": percent(s["volume"]),
                                "mute": s["mute"], "default": s["name"] == default_source, "key": s["name"]}))
    rows += [("", ""), ("#", "Playing")]
    inputs = listing("sink-inputs")
    for s in inputs:
        p = s["properties"]
        name = p.get("application.name") or p.get("media.name") or "stream"
        rows.append(("input", {"id": s["index"], "name": name, "vol": percent(s["volume"]),
                               "mute": s["mute"], "default": False, "key": None}))
    if not inputs:
        rows.append(("none", {}))
    return rows


def device_row(kind, d, w):
    bw = max(w - 32, 6)
    n = round(bw * min(d["vol"], 100) / 100)
    mark = f"{ACCENT}●{RESET}" if d["default"] else (f"{TRACK}○{RESET}" if kind != "input" else " ")
    if d["mute"]:
        meter, pct = f"{TRACK}{'━' * bw}{RESET}", f"{RED}mute{RESET}"
    else:
        meter, pct = f"{ACCENT}{'━' * n}{TRACK}{'━' * (bw - n)}{RESET}", f"{FG}{d['vol']:>3}%{RESET}"
    name = f"{FG}{BOLD if d['default'] else ''}{fit(d['name'], 22):<22}{RESET}"
    return spread(f"{mark} {name}  {meter}", pct, w)


def render(rows, sel):
    cols, height, w, pad = frame(90)
    body = []
    for i, (kind, d) in enumerate(rows):
        if kind == "#":
            body.append(section(d, w))
        elif kind == "none":
            body.append(f"  {DIM}nothing playing{RESET}")
        elif kind:
            row = device_row(kind, d, w)
            body.append(highlight(row, w) if i == sel else row)
        else:
            body.append("")
    out = next((d["name"] for k, d in rows if k == "sink" and d["default"]), "no output")
    head = header("\U000f057e", "Audio", fit(out, w - 12), w)
    foot = hints([("←→", "volume"), ("m", "mute"), ("enter", "default"), ("w", "wiremix")], w)
    return card(head, body, foot, height, pad)


def act(kind, d, key):
    what = {"sink": "sink", "source": "source", "input": "sink-input"}[kind]
    if key in ("LEFT", "RIGHT"):
        vol = max(0, min(d["vol"] + (STEP if key == "RIGHT" else -STEP), MAX))
        pactl(f"set-{what}-volume", str(d["id"]), f"{vol}%")
    elif key == "m":
        pactl(f"set-{what}-mute", str(d["id"]), "toggle")
    elif key == "ENTER" and kind in ("sink", "source"):
        pactl(f"set-default-{kind}", d["key"])


class AudioPanel(Panel):
    interval = INTERVAL

    def __init__(self):
        self.rows, self.sel = [], None

    def tick(self):
        self.rows = load()
        picks = self.picks()
        if self.sel not in picks and picks:  # start on the default output; keep the place when a stream ends
            self.sel = (min(picks, key=lambda i: abs(i - self.sel)) if self.sel is not None
                        else next((i for i in picks if self.rows[i][1]["default"]), picks[0]))

    def picks(self):
        return [i for i, (k, _) in enumerate(self.rows) if k in ("sink", "source", "input")]

    def draw(self, w, h):
        return render(self.rows, self.sel)

    def key(self, k):
        picks = self.picks()
        if k in ("q", "ESC"):
            return "quit"
        if k in ("UP", "k", "DOWN", "j", "WHEELUP", "WHEELDOWN") and picks:
            pos = picks.index(self.sel) + (1 if k in ("DOWN", "j", "WHEELDOWN") else -1)
            self.sel = picks[max(0, min(pos, len(picks) - 1))]
            return
        if k == "w":
            suspend(["wiremix"])
        elif self.sel is not None:
            act(*self.rows[self.sel], {"h": "LEFT", "l": "RIGHT"}.get(k, k))
        self.tick()


if __name__ == "__main__":
    run(AudioPanel())
