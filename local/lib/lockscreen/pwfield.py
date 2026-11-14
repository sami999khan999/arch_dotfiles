#!/usr/bin/env python3
# The password field the lock screen, the login screen (ui.py's card) and the password prompt
# (panels/polkitgui.py) share: the password drawn as squares popping in (Dots), a sweep along its foot while it's
# checked (Sweep), its look by state (FIELD_CSS: focus, Caps Lock, wrong, right, checking, ready) and the
# keys under it as key caps. Light: no NumPy here (the animations are ui.py's), so the prompt starts fast.
import os
import sys
import time

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "theme"))
import themelib  # noqa: E402


def rgba(hex_, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(themelib.recolor(hex_))
    c.alpha = alpha
    return c


def rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def mix(a, b, t):
    """Gdk colour a → b by t (0..1)."""
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = (x + (y - x) * t for x, y in
                                       zip((a.red, a.green, a.blue, a.alpha), (b.red, b.green, b.blue, b.alpha)))
    return c


def ease_back(t):
    """0 → 1 overshooting a little, for a dot popping in."""
    c = 2.2
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Dots(Gtk.Widget):
    """The password as squares, one per character, over the (then transparent) entry: each pops in, lit in the
    accent for a moment; the newest stay in view when there are more than fit. Its colour is the CSS one
    (.dots), so the field's states tint it."""
    SIZE, GAP, POP, GLOW = 8, 6, 0.16, 0.45

    def __init__(self):
        super().__init__(can_target=False, hexpand=True)
        self.set_css_name("dots")
        self.born = []          # when each dot came (monotonic)
        self.ticking = False
        self.accent = rgba("#6b8fe0")

    def sync(self, n):
        now = time.monotonic()
        self.born = self.born[:n] + [now] * max(n - len(self.born), 0)
        if not self.ticking and n:
            self.ticking = True
            self.add_tick_callback(self.tick)
        self.queue_draw()

    def tick(self, *_):
        self.queue_draw()
        alive = bool(self.born) and time.monotonic() - self.born[-1] < self.GLOW
        self.ticking = alive
        return GLib.SOURCE_CONTINUE if alive else GLib.SOURCE_REMOVE

    def do_snapshot(self, snap):
        if not self.born:
            return
        w, h = self.get_width(), self.get_height()
        step = self.SIZE + self.GAP
        fit = max((w + self.GAP) // step, 1)
        shown = self.born[-fit:]
        colour = self.get_color()
        now = time.monotonic()
        for i, born in enumerate(shown):
            age = now - born
            s = self.SIZE * (ease_back(min(age / self.POP, 1)) if age < self.POP else 1)
            if i == 0 and len(self.born) > fit:   # more to the left: the first one fades out
                c = mix(colour, Gdk.RGBA(red=colour.red, green=colour.green, blue=colour.blue, alpha=0), 0.6)
            else:
                c = mix(self.accent, colour, min(age / self.GLOW, 1))
            cx, cy = i * step + self.SIZE / 2, h / 2
            snap.append_color(c, rect(cx - s / 2, cy - s / 2, s, s))


class Sweep(Gtk.Widget):
    """A thin accent line sweeping along the field's foot while the password is being checked."""
    PERIOD = 1.1

    def __init__(self):
        super().__init__(can_target=False, valign=Gtk.Align.END, hexpand=True)
        self.set_size_request(-1, 2)
        self.on = False
        self.colour = rgba("#6b8fe0")

    def run(self, on):
        if on and not self.on:
            self.t0 = time.monotonic()
            self.add_tick_callback(self.tick)
        self.on = on
        self.queue_draw()

    def tick(self, *_):
        self.queue_draw()
        return GLib.SOURCE_CONTINUE if self.on else GLib.SOURCE_REMOVE

    def do_snapshot(self, snap):
        if not self.on:
            return
        w, h = self.get_width(), self.get_height()
        t = ((time.monotonic() - self.t0) % self.PERIOD) / self.PERIOD
        t = t * t * (3 - 2 * t)                     # smooth at both ends
        seg = w * 0.32
        x = -seg + (w + seg) * t
        snap.append_color(self.colour, rect(max(x, 0), 0, min(x + seg, w) - max(x, 0), h))


def keycaps(hints):
    """[(key, what)…] as one line of markup: each key on a key cap (like the bar's cards)."""
    cap = '<span background="#292e42" foreground="#a9b1d6" font_weight="bold"> {} </span>'
    return themelib.recolor("   ".join(cap.format(GLib.markup_escape_text(k)) + " " + GLib.markup_escape_text(w)
                                       for k, w in hints))


# inside a .lock-card: the field (.field: the icon, the entry under its Dots, the eye, the arrow) and its line (.msg)
FIELD_CSS = """
.lock-card .field {
  background: alpha(#16161e, 0.7); border: 1px solid alpha(#3b4261, 0.9);
  box-shadow: 0 0 0 3px transparent;
  transition: border-color 150ms ease, box-shadow 200ms ease, background-color 150ms ease;
}
.lock-card .field:focus-within { border-color: #6b8fe0; box-shadow: 0 0 0 3px alpha(#6b8fe0, 0.16); }
.lock-card .field.caps:focus-within { border-color: #e0af68; box-shadow: 0 0 0 3px alpha(#e0af68, 0.14); }
.lock-card .field.fail, .lock-card .field.fail:focus-within {
  border-color: #f7768e; box-shadow: 0 0 0 3px alpha(#f7768e, 0.16); background: alpha(#f7768e, 0.06);
}
.lock-card .field.ok, .lock-card .field.ok:focus-within {
  border-color: #9ece6a; box-shadow: 0 0 0 3px alpha(#9ece6a, 0.16);
}
.lock-card .field .icon { font-size: 15px; color: #565f89; min-width: 46px; transition: color 150ms ease; }
.lock-card .field:focus-within .icon { color: #6b8fe0; }
.lock-card .field.caps .icon { color: #e0af68; }
.lock-card .field.fail .icon { color: #f7768e; }
.lock-card .field.ok .icon { color: #9ece6a; }
.lock-card .field entry {
  min-height: 46px; padding: 0; font-size: 14px; color: #c0caf5; caret-color: #6b8fe0;
  background: none; border: none; border-radius: 0; box-shadow: none; outline: none;
}
.lock-card .field entry > text > placeholder { color: #565f89; }
/* hidden, the password is drawn as squares (Dots) over the entry: its own dots and caret go transparent */
.lock-card .field entry.masked > text { color: transparent; caret-color: transparent; }
.lock-card .field entry.masked > text > selection { background: none; color: transparent; }
.lock-card .field dots { color: #c0caf5; }
.lock-card .field.fail dots { color: #f7768e; }
.lock-card .field.ok dots { color: #9ece6a; }
.lock-card .field.busy dots { color: #565f89; }
.lock-card .field .layout {
  font-size: 10px; font-weight: 700; color: #a9b1d6; padding: 1px 5px; margin: 0 4px;
  border: 1px solid alpha(#3b4261, 0.9);
}
.lock-card .field button {
  min-width: 40px; min-height: 46px; padding: 0; font-size: 15px; color: #3b4261;
  background: none; border: none; border-radius: 0; box-shadow: none; outline: none;
  transition: color 150ms ease, background-color 150ms ease;
}
.lock-card .field button.eye { min-width: 36px; font-size: 14px; color: #565f89; }
.lock-card .field button.eye:hover { color: #c0caf5; background: alpha(#6b8fe0, 0.15); }
.lock-card .field button.eye.on { color: #6b8fe0; }
.lock-card .field button.go { min-width: 46px; border-left: 1px solid alpha(#3b4261, 0.6); }
.lock-card .field.ready button.go { color: #1a1b26; background: #6b8fe0; border-left-color: #6b8fe0; }
.lock-card .field.ready button.go:hover { background: #c0caf5; border-left-color: #c0caf5; }
.lock-card .field.ok button.go { color: #1a1b26; background: #9ece6a; border-left-color: #9ece6a; }
.lock-card .msg { font-size: 12px; color: #565f89; min-height: 18px; }
.lock-card .msg.caps { color: #e0af68; }
.lock-card .msg.fail { color: #f7768e; }
.lock-card .msg.ok { color: #9ece6a; }
"""
