#!/usr/bin/env python3
# audiogui.py — the audio panel as a GTK window (Tokyo Night): outputs, inputs and what's playing.
# Running it again closes it. Also a section of the Control Center.
#
#   each row   name, volume slider, percentage, Mute, Make default (outputs and inputs)
#   keys       ↑↓ select   ←→ volume   m mute   Enter make default   w wiremix (everything else)
#
# Reading and changing the volumes is audiopanel.py's (pactl); this only draws it.
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gtk, View, box, button, clear, label, rule_heading, run, scrolled
import audiopanel

CSS = """
scale trough { background: #292e42; border: none; min-height: 4px; padding: 0; }
scale highlight { background: #6b8fe0; border: none; min-height: 4px; margin: 0; }
scale slider { background: #c0caf5; border: none; min-width: 12px; min-height: 12px; margin: -5px; box-shadow: none; }
scale:disabled highlight { background: #565f89; }
/* the Control Center card: a thin volume line, no knob, like the terminal card's meter */
.audio-tile scale trough, .audio-tile scale highlight { min-height: 2px; }
.audio-tile scale slider { min-width: 0; min-height: 0; margin: 0; padding: 0; background: transparent; }
.audio-tile scale { min-height: 0; padding: 0; margin: 0; }
.audio-tile scale contents, .audio-tile scale trough { margin: 0; padding: 0; }
.audio-tile scale:disabled trough { background: #292e42; }
.audio-tile scale:disabled highlight { background: transparent; }
.red { color: #f7768e; }
"""
WHAT = {"sink": "sink", "source": "source", "input": "sink-input"}   # pactl's word for each kind
TUI = os.path.expanduser("~/.config/hypr/scripts/tui.sh")


class Audio(View):
    title = "Audio"
    interval = audiopanel.INTERVAL
    hints = [("↑↓", "select"), ("←→", "volume"), ("m", "mute"), ("Enter", "default"), ("w", "wiremix"),
             ("Esc", "close")]
    css = CSS
    icon = "\U000f057e"   # as the terminal card

    @property
    def tile_info(self):
        return next((d["name"] for k, d in self.rows if k == "sink" and d["default"]), "no output")

    def __init__(self):
        super().__init__()
        self.rows = []          # audiopanel.load(): ("#", title) headings and (kind, device) entries
        self.shape = None       # what the list was built from; rebuilt only when it changes
        self.widgets = {}       # (kind, id) -> {"row", "name", "scale", "pct", "mute", "default"}
        self.sel = None         # (kind, id) of the selected row, kept across rebuilds
        self.updating = False   # while refresh() moves the sliders, their changes aren't user input

    popup = "~/.local/lib/panels/audiogui.py"

    def header_extra(self):
        return [button("Wiremix", self.wiremix, "flat", tooltip="Everything else: per-app routing, profiles (w)")]

    def build(self):
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        if self.compact:
            self.list.add_css_class("audio-tile")
            self.hints = [("←→", "volume"), ("m", "mute"), ("enter", "default"), ("w", "wiremix")]
        self.list.connect("row-selected", lambda _l, row: row and setattr(self, "sel", row.key))
        self.list.set_activate_on_single_click(False)   # a click selects; Enter / double-click makes default
        self.list.connect("row-activated", lambda _l, row: self.act("ENTER", row.key))
        wrap = box(True, 0, self.list)
        for edge in ("start", "end"):
            getattr(wrap, f"set_margin_{edge}")(8)
        wrap.set_margin_bottom(12)
        wrap.set_margin_top(4 if self.compact else 12)
        self.refresh()
        return scrolled(wrap)

    # ---- drawing ---------------------------------------------------------------------------------
    def refresh(self):
        self.rows = audiopanel.load()
        shape = [(k, d.get("id"), d.get("name")) if k not in ("#", "") else (k, d) for k, d in self.rows]
        if shape != self.shape:
            self.shape = shape
            self.rebuild()
        self.update()

    def rebuild(self):
        clear(self.list)
        self.widgets = {}
        for kind, d in self.rows:
            if kind == "#":
                head = rule_heading(d)
                if self.compact and d != "Output":
                    head.set_margin_top(8)
                row = Gtk.ListBoxRow(selectable=False, activatable=False, child=head)
                row.key = None
            elif kind == "none":
                row = Gtk.ListBoxRow(selectable=False, activatable=False, child=label("nothing playing", "dim"))
                row.key = None
            elif kind:
                row = self.device_row(kind, d)
            else:
                continue
            self.list.append(row)
        keys = [k for k in self.widgets]
        if self.sel not in keys and keys:   # start on the default output
            self.sel = next((k for k, d in self.devices() if d["default"] and k[0] == "sink"), keys[0])
        if self.sel in self.widgets:
            row = self.widgets[self.sel]["row"]
            self.list.select_row(row)
            row.grab_focus()

    def device_row(self, kind, d):
        key = (kind, d["id"])
        name = label(d["name"], ellipsize=True)
        name.set_hexpand(False)   # the slider takes the spare width
        name.set_size_request(150 if self.compact else 220, -1)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, audiopanel.STEP)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(90 if self.compact else 160, -1)
        scale.connect("value-changed", lambda s, k=key: self.slid(k, s.get_value()))
        if self.compact:   # the card's line is a meter, not a slider: a click (to focus the card)
            scale.set_can_target(False)   # mustn't set the volume; ←→ change it, like the terminal card
            scale.set_focusable(False)
        pct = label("", xalign=1.0)
        pct.set_size_request(48 if self.compact else 56, -1)
        mute = button("Mute", lambda k=key: self.act("m", k))
        mute.set_size_request(70 if self.compact else 92, -1)
        # a dot for the default device (hollow for the others), as on the Control Center card
        mark = label(" " if kind == "input" else "\u25cb", "dim")
        if self.compact:
            line = box(False, 8, mark, name, scale, pct)
        else:
            line = box(False, 12, mark, name, scale, pct, mute)
        default = None
        if kind != "input" and not self.compact:   # compact (a Control Center tile): Enter / double-click
            default = button("Make default", lambda k=key: self.act("ENTER", k))
            default.set_size_request(140, -1)
            line.append(default)
        elif not self.compact:   # a playing app has no default: keep the space so every slider lines up
            gap = Gtk.Box()
            gap.set_size_request(140, -1)
            line.append(gap)
        row = Gtk.ListBoxRow(child=line)
        row.key = key
        self.widgets[key] = {"row": row, "name": name, "scale": scale, "pct": pct, "mute": mute, "default": default,
                             "mark": mark}
        return row

    def update(self):
        """Values into the existing rows. A slider being dragged is left alone."""
        self.updating = True
        for key, d in self.devices():
            w = self.widgets.get(key)
            if not w:
                continue
            if not w["scale"].get_state_flags() & Gtk.StateFlags.ACTIVE:
                w["scale"].set_value(min(d["vol"], 100))
            w["scale"].set_sensitive(not d["mute"])
            w["pct"].set_text(("mute" if self.compact else "muted") if d["mute"] else f"{d['vol']}%")
            if w["mark"] and key[0] != "input":
                w["mark"].set_text("\u25cf" if d["default"] else "\u25cb")
                (w["mark"].add_css_class if d["default"] else w["mark"].remove_css_class)("accent")
            (w["pct"].add_css_class if d["mute"] else w["pct"].remove_css_class)("red")
            w["mute"].set_label("Unmute" if d["mute"] else "Mute")
            (w["name"].add_css_class if d["default"] else w["name"].remove_css_class)("bold")
            if w["default"]:
                w["default"].set_label("Default" if d["default"] else "Make default")
                w["default"].set_sensitive(not d["default"])
        self.updating = False

    def devices(self):
        return [((k, d["id"]), d) for k, d in self.rows if k in WHAT]

    # ---- actions ---------------------------------------------------------------------------------
    def slid(self, key, value):
        if self.updating:
            return
        audiopanel.pactl(f"set-{WHAT[key[0]]}-volume", str(key[1]), f"{round(value)}%")

    def act(self, k, key=None):
        """audiopanel's keys (LEFT/RIGHT volume, m mute, ENTER default) on a device."""
        key = key or self.sel
        d = dict(self.devices()).get(key)
        if not d:
            return
        audiopanel.act(key[0], d, k)
        if k == "ENTER" and key[0] != "input":
            self.say(f"{d['name']} is now the default {'output' if key[0] == 'sink' else 'input'}")
        self.refresh()

    def wiremix(self):
        subprocess.Popen([TUI, "wiremix"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def key(self, keyval, state):
        if self.typing():
            return False
        name = Gdk.keyval_name(keyval) or ""
        if name in ("Left", "Right", "h", "l"):
            self.act("LEFT" if name in ("Left", "h") else "RIGHT")
        elif name == "m":
            self.act("m")
        elif name == "w":
            self.wiremix()
        else:
            return False
        return True


def make():
    """The view, for run() here and for the Control Center."""
    return Audio()


def main():
    run(make(), "panels.audio", (900, 520))


if __name__ == "__main__":
    main()
