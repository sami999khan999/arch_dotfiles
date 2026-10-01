#!/usr/bin/env python3
# keysgui.py — the shortcut list as a GTK window (Tokyo Night). Super + K opens it; running it
# again closes it. Reads binds.lua, apps.conf and workspaces.conf live (keys.py does the parsing),
# so it never goes stale. A bind shows its trailing "-- comment" as its description.
#
#   type   search (every word must match)     ↑↓ PgUp PgDn  scroll     Esc  clear, then close
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, clear, label, rule_heading, run, scrolled
import keys


class Shortcuts(View):
    title, subtitle = "Shortcuts", "every keybind, live from the config"
    interval = 5.0   # binds.lua / apps.conf are re-read this often
    hints = [("type", "search"), ("↑↓ PgUp PgDn", "scroll"), ("Esc", "clear / close")]
    css = """
    .keys-list > row { padding: 3px 4px; }
    .keys-list > row:hover { background: transparent; }
    .keys-head { margin-top: 18px; }
    /* the Control Center card: a bare search line, like the terminal card */
    .keys-card-search, .keys-card-search:focus-within { background: transparent; border: none; padding: 0 4px; }
    .cc-card .keys-list > row { padding: 0 4px; }
    .cc-card .keys-list .keys-head { margin-top: 10px; }
    """
    icon = "\U000f030c"   # as the terminal card

    @property
    def tile_info(self):
        return self.count.get_text()

    def __init__(self):
        super().__init__()
        self.data = []

    def build(self):
        # a plain Entry, not a SearchEntry: that one draws a magnifier and a clear icon
        self.search = Gtk.Entry(placeholder_text="type to search…" if self.compact else "Search shortcuts…",
                                hexpand=True)
        self.search.connect("changed", lambda *_: self.paint())
        self.count = label("", "dim")
        if self.compact:   # the card: the count is in its header, the search is a bare line
            self.hints = [("↑↓", "scroll"), ("esc", "clear")]
            self.search.add_css_class("keys-card-search")
            top = box(False, 10, label("\uf002", "accent"), self.search)
        else:
            top = box(False, 14, self.search, self.count)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("keys-list")
        self.scroll = scrolled(self.list)
        self.scroll.set_margin_top(6)

        page = box(True, 0, top, self.scroll)
        if not self.compact:
            for edge in ("start", "end", "top", "bottom"):
                getattr(page, f"set_margin_{edge}")(18)
        self.data = keys.sections()
        self.paint()
        if not self.compact:   # a Control Center tile mustn't take the keyboard from the others
            GLib.idle_add(lambda: self.search.grab_focus() and False)   # once the window is up
        return page

    def paint(self):
        clear(self.list)
        keycol = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # the key column's width
        words = self.search.get_text().lower().split()
        shown = 0
        for title, items in self.data:
            hits = [(k, d) for k, d in items if all(w in f"{k} {d}".lower() for w in words)]
            if not hits:
                continue
            head = rule_heading(title)
            if shown:
                head.add_css_class("keys-head")
            row = Gtk.ListBoxRow(child=head, activatable=False)
            self.list.append(row)
            for k, d in hits:
                key = label(k, "amber")
                keycol.add_widget(key)
                line = box(False, 16 if self.compact else 24, key, label(d, ellipsize=True))
                self.list.append(Gtk.ListBoxRow(child=line, activatable=False))
            shown += len(hits)
        if not shown:
            self.list.append(Gtk.ListBoxRow(child=label(f"No shortcut matches “{self.search.get_text()}”", "dim"),
                                            activatable=False))
        self.count.set_text(f"{shown} shortcuts")
        self.scroll.get_vadjustment().set_value(0)

    def refresh(self):
        data = keys.sections()
        if data != self.data:
            self.data = data
            adj = self.scroll.get_vadjustment()
            at = adj.get_value()
            self.paint()
            adj.set_value(at)

    def escape(self):
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.close()

    def key(self, keyval, state):
        adj = self.scroll.get_vadjustment()
        step, page = 30, adj.get_page_size() * 0.9
        moves = {Gdk.KEY_Down: step, Gdk.KEY_Up: -step, Gdk.KEY_Page_Down: page, Gdk.KEY_Page_Up: -page}
        if keyval in moves:
            adj.set_value(adj.get_value() + moves[keyval])
            return True
        if keyval == Gdk.KEY_Escape:
            self.escape()
            return True
        if not self.typing():
            ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if ch.isprintable() and ch != "\0" and not state & Gdk.ModifierType.CONTROL_MASK:
                self.search.grab_focus()                 # typing anywhere searches
                self.search.set_text(self.search.get_text() + ch)
                self.search.set_position(-1)
                return True
            if keyval == Gdk.KEY_Home:
                adj.set_value(0)
                return True
            if keyval == Gdk.KEY_End:
                adj.set_value(adj.get_upper())
                return True
        return False


def make():
    """The view, for run() here and for the Control Center."""
    return Shortcuts()


def main():
    run(make(), "panels.shortcuts", (760, 600))


if __name__ == "__main__":
    main()
