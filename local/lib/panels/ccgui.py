#!/usr/bin/env python3
# ccgui.py — the Control Center as a GTK window: workspace 10's app (tiled, not a popup).
# Five cards, all visible at once, in the look of the terminal version (controlcenter.py):
#
#   ┌──────────── Workspaces ─────────────┐ ┌────────────── System ───────────────┐
#   └─────────────────────────────────────┘ └─────────────────────────────────────┘
#   ┌──── Shortcuts ────┐ ┌────── Audio ──────┐ ┌───── Network ─────┐
#   └───────────────────┘ └───────────────────┘ └───────────────────┘
#
# (Class "cc-card": GTK themes style ".card" with their own background.)
# Every card has the same chrome: icon + title with dim details on the right, an underline that's
# blue under the title, the content, and its own key hints on the last line. The content is the
# panel's compact view (wsgui, sysgui, keysgui, audiogui, netgui with compact=True).
# Click a card (or Tab into it) to focus it: blue border; keys go to it.
import importlib, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import GLib, Gtk, View, box, label, run

# (module, column, row, width) on a 6-column grid: two cards on top, three below
TILES = [("wsgui", 0, 0, 3), ("sysgui", 3, 0, 3),
         ("keysgui", 0, 1, 2), ("audiogui", 2, 1, 2), ("netgui", 4, 1, 2)]

CSS = """
.cc-card { border: 1px solid alpha(#3b4261, .9); padding: 10px 14px 8px 14px; }
.cc-card.focused { border-color: #6b8fe0; }
.card-icon { color: #6b8fe0; }
.card-title { font-weight: 700; }
.card-line-lead { min-height: 2px; background: #6b8fe0; }
.card-line { min-height: 2px; background: #292e42; }
.card-foot { margin-top: 6px; }
/* the cards' lists are dense, like the terminal: one line per row */
.cc-card list > row { padding: 1px 6px; border-left: none; }
.cc-card list > row:selected { background: #292e42; }
"""


class ControlCenter(View):
    title, subtitle = "Control Center", ""
    interval = 0.5   # how often to check which cards are due a refresh
    header = False   # the cards have their own
    footer = False   # …and their own key hints

    def __init__(self):
        super().__init__()
        self.views = [importlib.import_module(m).make() for m, *_ in TILES]
        self.css = CSS + "".join(v.css for v in self.views)
        self.current = self.views[0]
        self.cards, self.infos, self.foots = {}, {}, {}
        self.last = {}   # view -> time of its last refresh

    @property
    def hints(self):
        return self.current.hints

    def build(self):
        grid = Gtk.Grid(row_spacing=14, column_spacing=14, row_homogeneous=True, column_homogeneous=True)
        for edge in ("start", "end", "top", "bottom"):
            getattr(grid, f"set_margin_{edge}")(14)
        for v, (_, col, row, width) in zip(self.views, TILES):
            v.host = self.host           # say(), typing(), close() go to this window
            v.compact = True
            v.on_change = lambda v=v: self.paint_chrome(v)   # a card that updates on its own (no timer)
            grid.attach(self.card(v), col, row, width, 1)
        self.focus(self.current)
        GLib.idle_add(self.settle, grid)
        return grid

    def card(self, v):
        lead = box(False, 10, label(v.icon, "card-icon"), label(v.title, "card-title"))
        info = label("", "dim", xalign=1.0, ellipsize=True)
        self.infos[v] = info
        # the underline: blue under the icon and title, dark grey after
        under_lead = Gtk.Box(css_classes=["card-line-lead"])
        group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        group.add_widget(lead)
        group.add_widget(under_lead)
        underline = box(False, 0, under_lead, Gtk.Box(hexpand=True, css_classes=["card-line"]))
        underline.set_margin_top(6)
        underline.set_margin_bottom(10)
        content = v.build()
        content.set_vexpand(True)
        foot = label("", "card-foot", markup=True, ellipsize=True)
        self.foots[v] = foot
        card = box(True, 0, box(False, 12, lead, info), underline, content, foot, classes=("cc-card",))
        card.set_hexpand(True)
        click = Gtk.GestureClick()   # a click anywhere in the card focuses it
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", lambda *_, v=v: self.focus(v))
        card.add_controller(click)
        self.cards[v] = card
        self.paint_chrome(v)
        return card

    def paint_chrome(self, v):
        self.infos[v].set_text(v.tile_info)
        self.foots[v].set_markup(v.tile_footer)

    def settle(self, grid):
        """Every card starts at its top, and stays put when focus moves inside it: GTK otherwise
        scrolls a card to whatever widget gets the keyboard."""
        def walk(w):
            if isinstance(w, Gtk.Viewport):
                w.set_scroll_to_focus(False)
            if isinstance(w, Gtk.ScrolledWindow):
                w.get_vadjustment().set_value(0)
            c = w.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()
        walk(grid)
        return False

    def focus(self, v):
        self.cards[self.current].remove_css_class("focused")
        self.current = v
        self.cards[v].add_css_class("focused")

    def follow_focus(self):
        """Tab moves the keyboard between cards: the focused card is the one holding it."""
        w = self.window.get_focus() if self.window else None
        while w is not None:
            for v, card in self.cards.items():
                if w is card:
                    if v is not self.current:
                        self.focus(v)
                    return
            w = w.get_parent()

    def refresh(self):
        self.follow_focus()
        now = time.time()
        for v in self.views:
            if v.interval and now - self.last.get(v, 0) >= v.interval:
                self.last[v] = now
                v.refresh()
                self.paint_chrome(v)

    def hidden_refresh(self):
        """Workspace 10 isn't on a screen: only the cards that keep a history sample, on their schedule."""
        now = time.time()
        for v in self.views:
            if v.interval and now - self.last.get(v, 0) >= v.interval:
                self.last[v] = now
                v.hidden_refresh()

    def shown_changed(self, shown):
        now = time.time()
        for v in self.views:
            v.shown_changed(shown)
            if shown:
                self.last[v] = now
                self.paint_chrome(v)

    def key(self, keyval, state):
        self.follow_focus()
        handled = self.current.key(keyval, state)
        self.paint_chrome(self.current)
        return handled


def make():
    return ControlCenter()


def main():
    run(make(), "sami.controlcenter", (1356, 731), toggle=False)


if __name__ == "__main__":
    main()
