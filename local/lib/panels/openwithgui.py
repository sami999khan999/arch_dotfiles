# openwithgui.py — "Open with…": the app chooser every app gets through the desktop portal (Images' / Videos' /
# Documents' Open with, a browser's "open" of a download, anything calling OpenURI with ask) is a window in the
# panels' look and the colour theme. Not run on its own: pickergui.py (the portal backend) shows one per
# AppChooser request. The apps are the ones the portal offers for the file's type (its default marked); type
# to filter, ↑/↓ and Enter (or a double click) open it with the chosen one, Esc or Cancel don't.
import os

from gtkkit import Gdk, Gio, GLib, Gtk, View, box, button, label, scrolled

G_OPEN = ""
CSS = """
.choose-list > row { padding: 8px 14px; }
.app-name { color: #c0caf5; font-weight: 700; }
.app-default { color: #e0af68; }
.choose-bar { border-top: 1px solid alpha(#3b4261, .7); padding-top: 10px; }
"""


def app_info(app_id):
    """The installed app for a portal choice (a desktop file's id without .desktop), or None."""
    try:
        return Gio.DesktopAppInfo.new(app_id + ".desktop")
    except TypeError:   # no such desktop file: the constructor returns NULL
        return None


class OpenWith(View):
    title, icon = "Open with", G_OPEN
    interval = 0
    css = CSS
    hints = [("Enter", "open"), ("↑/↓", "choose"), ("type", "filter"), ("Esc", "cancel")]

    def __init__(self, choices, opts, done):
        """done(response, choice) answers the request: 0 chosen (choice: the app's id), 1 cancelled."""
        super().__init__()
        self.opts, self.done, self.answered = opts, done, False
        self.choices = list(choices)
        name = opts.get("filename") or ""
        if not name and opts.get("uri"):
            name = os.path.basename(GLib.filename_from_uri(opts["uri"])[0]) if opts["uri"].startswith("file:") \
                else opts["uri"]
        self.subtitle = name
        self.ctype = opts.get("content_type") or ""

    def build(self):
        self.filter = Gtk.SearchEntry(placeholder_text="filter the apps", hexpand=True)
        self.filter.connect("search-changed", lambda *_: self.refilter())
        self.filter.connect("activate", lambda *_: self.choose())
        self.filter.connect("stop-search", lambda *_: self.cancel())
        keys = Gtk.EventControllerKey()   # ↑/↓ from the filter move through the list
        keys.connect("key-pressed", self.filter_key)
        self.filter.add_controller(keys)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=False)
        self.list.add_css_class("choose-list")
        self.list.set_filter_func(self.shows)
        self.list.connect("row-activated", lambda _l, row: self.finish(row.app_id))
        self.fill(self.opts.get("last_choice"))

        what = Gio.content_type_get_description(self.ctype) if self.ctype else ""
        bar = box(False, 8, label(f"Apps for {what}" if what else "Apps for this file", "dim", ellipsize=True),
                  button("Cancel", self.cancel, "flat"), button("Open", self.choose, "primary"),
                  classes=("choose-bar",))
        page = box(True, 10, self.filter, scrolled(self.list), bar)
        for side in ("top", "bottom", "start", "end"):
            getattr(page, f"set_margin_{side}")(14)
        GLib.idle_add(lambda: self.filter.grab_focus() and False)
        return page

    def fill(self, pick=None):
        """The rows for self.choices; `pick` (else the selected one, else the first) selected."""
        cur = self.list.get_selected_row()
        pick = pick or (cur.app_id if cur else None)
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        default = Gio.AppInfo.get_default_for_type(self.ctype, False) if self.ctype else None
        default_id = default.get_id().removesuffix(".desktop") if default else None
        names = {}
        infos = [(c, app_info(c)) for c in self.choices]
        for _c, info in infos:
            if info:
                names[info.get_display_name()] = names.get(info.get_display_name(), 0) + 1
        for c, info in infos:
            if info is None:
                continue
            name = info.get_display_name()
            # two apps of one name (Chrome's two desktop files): their ids tell them apart
            sub = c if names[name] > 1 else (info.get_description() or "")
            icon = Gtk.Image.new_from_gicon(info.get_icon()) if info.get_icon() else \
                Gtk.Image.new_from_icon_name("application-x-executable")
            icon.set_pixel_size(32)
            head = box(False, 8, label(name, "app-name"), *([label("default", "app-default")] if c == default_id else []))
            text = box(True, 2, head, *([label(sub, "dim", ellipsize=True)] if sub else []))
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            row = Gtk.ListBoxRow(child=box(False, 14, icon, text))
            row.app_id, row.search = c, f"{name} {c} {info.get_description() or ''}".casefold()
            self.list.append(row)
        self.select(next((r for r in self.rows() if r.app_id == pick), None))

    def rows(self):
        row = self.list.get_first_child()
        while row is not None:
            yield row
            row = row.get_next_sibling()

    def shows(self, row):
        q = self.filter.get_text().casefold().split()
        return all(w in row.search for w in q)

    def refilter(self):
        self.list.invalidate_filter()
        cur = self.list.get_selected_row()
        if cur is None or not self.shows(cur):
            self.select(None)

    def select(self, row):
        """Select `row`, or the first one shown."""
        if row is None or not self.shows(row):
            row = next((r for r in self.rows() if self.shows(r)), None)
        self.list.select_row(row)
        if row is not None:
            GLib.idle_add(lambda: row.get_mapped() and self.scroll_to(row) or False)

    def scroll_to(self, row):
        adj = self.list.get_parent().get_parent().get_vadjustment()   # listbox → viewport → scrolled window
        ok, rect = row.compute_bounds(self.list)
        if ok:
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())

    def move(self, d):
        shown = [r for r in self.rows() if self.shows(r)]
        if not shown:
            return
        cur = self.list.get_selected_row()
        i = shown.index(cur) + d if cur in shown else 0
        self.select(shown[max(0, min(i, len(shown) - 1))])

    def filter_key(self, _ctl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.move({Gdk.KEY_Down: 1, Gdk.KEY_Up: -1, Gdk.KEY_Page_Down: 5, Gdk.KEY_Page_Up: -5}[keyval])
            return True
        return False

    def update(self, choices):
        """The portal found more apps (UpdateChoices) while this was up."""
        self.choices = list(choices)
        self.fill()

    def choose(self):
        row = self.list.get_selected_row()
        if row is None or not self.shows(row):
            self.say("no app chosen", "bad")
            return
        self.finish(row.app_id)

    def finish(self, app_id):
        self.answered = True
        self.done(0, app_id)
        self.host.win.close()

    def cancelled(self):
        """Answer "cancelled", once (the window closing for any reason: Esc, Cancel, the backdrop)."""
        if not self.answered:
            self.answered = True
            self.done(1, None)

    def cancel(self):
        self.cancelled()
        self.host.win.close()

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.choose()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False
