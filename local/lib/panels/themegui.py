#!/usr/bin/env python3
# themegui.py — the colour theme picker (GTK 4): Super + Shift + T, or Settings → Theme & fonts. Running
# it again closes it.
#
# A row of every theme's preview (config/themes/<id>/preview.png): the chosen one large with the accent
# border, the others smaller and dimmed, its name below. Enter (or a click on the chosen one) applies it:
# `theme apply <id>` (local/bin/theme) makes the files and reloads what's running. No preview picture
# (a theme you made): a sketch of its colours is drawn instead.
#
#   ← →  choose     Enter  apply     Esc  close
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, label, recolor, run
from gi.repository import GdkPixbuf

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
import themelib

THEME = os.path.expanduser("~/.local/bin/theme")
BIG, SMALL = (540, 304), (320, 180)   # the chosen preview, the others (a 1366 × 768 screenshot, scaled)
CSS = """
.theme-card { border: 2px solid transparent; padding: 0; }
.theme-card.chosen { border-color: #6b8fe0; }
.theme-card picture, .theme-card .sketch { opacity: .5; }
.theme-card.chosen picture, .theme-card.chosen .sketch { opacity: 1; }
.theme-name { font-size: 15pt; font-weight: 700; }
.theme-note { color: #565f89; }
.theme-on { color: #9ece6a; font-weight: 700; }
"""
# the roles drawn as the sketch's blocks and the strip of dots under a name
STRIP = ("base", "overlay", "accent", "alert", "green", "amber", "magenta", "cyan")


def hexrgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def sketch(colors, size):
    """A stand-in preview from the theme's colours: a bar, two panes with a title line, a selection."""
    c = {k: hexrgb(v) for k, v in colors.items()}
    area = Gtk.DrawingArea(css_classes=["sketch"])   # sized by show_chosen, like a picture

    def draw(_a, cr, w, h):
        def rect(col, x, y, rw, rh):
            cr.set_source_rgb(*c[col])
            cr.rectangle(x, y, rw, rh)
            cr.fill()
        rect("bg", 0, 0, w, h)
        rect("base", 0, 0, w, 22)
        rect("line", 0, 21, w, 1)
        for i in range(4):
            rect("accent" if i == 0 else "muted", 10 + i * 18, 8, 10, 6)
        pane = (w - 30) / 2
        for x in (10, 20 + pane):
            rect("base", x, 34, pane, h - 44)
            rect("accent", x + 10, 46, 46, 3)
            rect("overlay", x + 10, 49, pane - 20, 2)
            for r in range(5):
                rect("overlay" if r == 1 else "base", x + 6, 62 + r * 22, pane - 12, 18)
                rect(("green", "amber", "alert", "cyan", "magenta")[r], x + 12, 68 + r * 22, 8, 6)
                rect("subtext", x + 28, 69 + r * 22, 60 + r * 14, 4)
    area.set_draw_func(draw)
    return area


class Themes(View):
    title = "Theme"
    icon = "\U000f03d8"   # palette
    subtitle = "colours of the whole desktop"
    interval = 0
    hints = [("←→", "choose"), ("Enter", "apply"), ("Esc", "close")]
    css = CSS

    def __init__(self):
        super().__init__()
        self.ids = themelib.ids()
        self.now = themelib.active()
        self.chosen = self.ids.index(self.now)
        self.cards = []
        self.textures = {}

    def build(self):
        self.row = box(False, 24)
        self.row.set_halign(Gtk.Align.CENTER)
        self.row.set_valign(Gtk.Align.CENTER)
        self.scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vexpand=True)
        self.scroller.set_child(self.row)
        for i, tid in enumerate(self.ids):
            self.cards.append(self.card(i, tid))
            self.row.append(self.cards[-1][0])
        self.name = label("", "theme-name", xalign=0.5)
        self.note = label("", "theme-note", xalign=0.5)
        self.strip = Gtk.DrawingArea(content_width=8 * 22, content_height=10, halign=Gtk.Align.CENTER)
        self.strip.set_draw_func(self.draw_strip)
        foot = box(True, 4, self.name, self.note, self.strip)
        foot.set_margin_bottom(14)
        page = box(True, 10, self.scroller, foot)
        page.set_margin_top(16)
        # the keys before any widget: the header's Close button has the focus when the window opens,
        # and would take Enter for itself
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, keyval, _code, state: self.key(keyval, state))
        self.window.add_controller(keys)
        self.show_chosen()
        return page

    def card(self, i, tid):
        data = themelib.load(tid)
        path = os.path.join(themelib.THEMES, tid, "preview.png")
        if os.path.exists(path):
            shot = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
            shot.path = path   # scaled to the card's size in show_chosen: a picture's own size is the screenshot's
        else:
            shot = sketch(data["colors"], BIG)
        frame = box(False, 0, shot, classes=("theme-card",))
        frame.set_valign(Gtk.Align.CENTER)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.click(i))
        frame.add_controller(click)
        return frame, shot, data

    def scaled(self, path, size):
        """The preview at exactly size (kept: switching back and forth doesn't load it again)."""
        key = (path, size)
        if key not in self.textures:
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size[0], size[1], False)
                self.textures[key] = Gdk.Texture.new_for_pixbuf(pix)
            except GLib.Error:
                self.textures[key] = None
        return self.textures[key]

    def draw_strip(self, _a, cr, w, h):
        colors = self.cards[self.chosen][2]["colors"]
        for n, role in enumerate(STRIP):
            cr.set_source_rgb(*hexrgb(colors[role]))
            cr.rectangle(n * 22 + 2, 0, 18, h)
            cr.fill()

    def show_chosen(self):
        for i, (frame, shot, data) in enumerate(self.cards):
            size = BIG if i == self.chosen else SMALL
            shot.set_size_request(*size)
            if getattr(shot, "path", None):
                shot.set_paintable(self.scaled(shot.path, size))
            (frame.add_css_class if i == self.chosen else frame.remove_css_class)("chosen")
        data = self.cards[self.chosen][2]
        self.name.set_markup(f"{GLib.markup_escape_text(data['name'])}"
                             + (f"  <span foreground='{recolor('#9ece6a')}' size='small'>in use</span>"
                                if self.ids[self.chosen] == self.now else ""))
        self.note.set_text(data.get("description", ""))
        self.strip.queue_draw()
        GLib.idle_add(self.center)

    def center(self):
        """Scroll the row so the chosen card is in the middle."""
        frame = self.cards[self.chosen][0]
        adj = self.scroller.get_hadjustment()
        ok, rect = frame.compute_bounds(self.row)
        if ok:
            adj.set_value(rect.get_x() + rect.get_width() / 2 - adj.get_page_size() / 2)
        return False

    def move(self, step):
        self.chosen = (self.chosen + step) % len(self.ids)
        self.show_chosen()

    def click(self, i):
        if i == self.chosen:
            self.apply()
        else:
            self.chosen = i
            self.show_chosen()

    def apply(self):
        tid = self.ids[self.chosen]
        self.close()
        if tid != self.now:   # the popup first: applying restarts the bar and the apps behind it
            subprocess.Popen([THEME, "apply", tid], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Left, Gdk.KEY_h):
            self.move(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_l):
            self.move(1)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.apply()
        else:
            return False
        return True


def make():
    return Themes()


def main():
    run(make(), "panels.theme", (1060, 560))


if __name__ == "__main__":
    main()
