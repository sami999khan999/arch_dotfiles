#!/usr/bin/env python3
# clipgui.py — Clipboard (Super + V): the clipboard history, kept by elephant's clipboard provider (the same
# one walker -m clipboard shows, Super + Ctrl + V), with pinning, a preview and more.
#
#   left    tabs: All, Pinned, Text, Images, Links (with counts)
#   middle  the history, newest first (pinned ones in their place, marked; walker keeps them on top); typing searches
#   right   the selected item in full (text, image), its actions
#
# Keys: Enter copy and close · Shift+Enter paste into the window under the panel · Ctrl+P pin / unpin · Delete delete
# (a pinned one asks once) · Ctrl+E edit · Ctrl+O open a link · Ctrl+S send to the phone (LocalSend) ·
# Ctrl+1…5 or Ctrl+Tab the tabs · Ctrl+Shift+Delete clear the history (pinned items stay; asks once).
# Elephant is driven through its command line (elephant query / activate, a few ms each): nothing polls,
# the list is read again after each change.
import json, os, re, subprocess, sys, threading, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, clear, label, run, scrolled

ICON = "\U000f0a38"   # nf-md-clipboard_text_outline
PIN = "\U000f0403"    # nf-md-pin
GLYPH = {"text": "\U000f09a8", "image": "\U000f0976", "link": "\U000f0339"}   # text, image_outline, link_variant
TABS = [("all", "All"), ("pinned", "Pinned"), ("text", "Text"), ("image", "Images"), ("link", "Links")]
TERMINALS = {"kitty", "agentmux", "ghostty", "Alacritty", "konsole"}   # paste there is Ctrl+Shift+V
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
SHOWN = 300   # rows drawn at most (the history can hold thousands); the search goes through all of them
LINK = re.compile(r"^(https?://|www\.)\S+$")
CSS = """
.clip-tabs > row { padding: 8px 14px; }
.clip-list > row { padding: 6px 10px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.clip-glyph { min-width: 20px; }
.clip-text { font-family: "JetBrainsMono Nerd Font"; }
.clip-actions button { padding: 4px 10px; }
.clip-search { margin: 10px 10px 4px 10px; }
.clip-middle { border-right: 1px solid alpha(#3b4261, .7); }
"""


# ---- elephant ----------------------------------------------------------------------------------------------
def elephant(*args, timeout=5):
    try:
        return subprocess.run(["elephant", *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load():
    """Every item in the history, newest first: dicts with id, kind, text, path, when, pinned."""
    # a filter picked in walker (Ctrl+I: only images…) is elephant's state and would hide items here
    state = elephant("state", "--json", "clipboard")
    if state and '"show_combined"' not in state:
        elephant("activate", "clipboard;;show_combined;;")
    items = []
    for line in elephant("query", "--json", "clipboard;;10000;false").splitlines():
        try:
            it = json.loads(line)["item"]
        except (ValueError, KeyError):
            continue
        is_file = it.get("preview_type") == "file"
        text = it.get("text", "")
        when = parse_date(it.get("subtext", ""))
        items.append({"id": it["identifier"], "text": text, "path": it.get("preview", "") if is_file else "",
                      "kind": "image" if is_file else "link" if LINK.match(text.strip()) else "text",
                      "when": when, "pinned": "unpin" in it.get("actions", [])})
    # elephant lists pinned items first (pinned_on_top, for walker); here they keep their place in time
    items.sort(key=lambda it: -it["when"])
    return items


def parse_date(text):
    """Elephant's "Tue, 27 Oct 2026 11:11:00 +0600" as a timestamp (by hand: strptime's %a / %b follow the
    locale GTK sets, and this one isn't English); 0 if it isn't one."""
    try:
        _day, d, mon, y, hms, tz = text.split()
        h, mi, se = (int(x) for x in hms.split(":"))
        off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * (-1 if tz[0] == "-" else 1)
        return datetime(int(y), MONTHS.index(mon) + 1, int(d), h, mi, se, tzinfo=timezone(off)).timestamp()
    except ValueError:
        return 0


def act(item_id, action):
    elephant("activate", f"clipboard;{item_id};{action};;")


def ago(ts):
    if not ts:
        return ""
    s = time.time() - ts
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    if s < 7 * 86400:
        return time.strftime("%a %H:%M", time.localtime(ts))
    return time.strftime("%d %b", time.localtime(ts))


def first_line(text, n=200):
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return line[:n]


def paste_later():
    """Run as `clipgui.py --paste` once the panel has closed: Ctrl+V (Ctrl+Shift+V in a terminal) to the window
    that has the focus again."""
    sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
    import hyprsock
    time.sleep(0.25)   # the popup and its backdrop gone, the focus back on the window under them
    w = hyprsock.query("activewindow") or {}
    cls = w.get("class", "")
    mods = "CTRL SHIFT" if cls in TERMINALS or cls.startswith("code-term-") else "CTRL"
    hyprsock.request(f'dispatch hl.dsp.send_shortcut({{ mods = "{mods}", key = "v" }})')


class Clipboard(View):
    icon = ICON
    title = "Clipboard"
    interval = 0
    css = CSS
    hints = [("Enter", "copy"), ("Shift+Enter", "paste"), ("Ctrl+P", "pin"), ("Del", "delete"),
             ("Ctrl+E", "edit"), ("Ctrl+1…5", "tabs"), ("Esc", "clear / close")]

    def __init__(self):
        super().__init__()
        self.items, self.tab, self.selected = [], "all", None
        self.armed, self.armed_timer, self.armed_button = None, 0, None
        self.paused = False
        self.subtitle = "history, newest first"

    # ---- layout ------------------------------------------------------------------------------------------
    def header_extra(self):
        self.pause_b = button("Pause", lambda: self.toggle_pause(), "flat", tooltip="Stop / start recording copies")
        return [self.pause_b]

    def build(self):
        self.tabs = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["clip-tabs"])
        self.tabs.set_margin_top(8)
        self.tab_labels = {}
        for key, name in TABS:
            count = label("", "dim")
            self.tab_labels[key] = count
            text = label(name, "sub")
            text.set_hexpand(True)
            row = Gtk.ListBoxRow(child=box(False, 8, text, count))
            row.tab = key
            row.set_focusable(False)
            self.tabs.append(row)
        self.tabs.select_row(self.tabs.get_row_at_index(0))
        self.tabs.connect("row-selected", self.tab_selected)
        self.clear_b = button("Clear history", lambda: self.clear_history(), "flat", "danger",
                              tooltip="Delete everything that isn't pinned (Ctrl+Shift+Delete)")
        self.clear_b.set_margin_start(10)
        self.clear_b.set_margin_bottom(10)
        self.clear_b.set_halign(Gtk.Align.START)
        side = box(True, 0, scrolled(self.tabs), self.clear_b, classes=("side",))
        side.set_size_request(180, -1)

        # a plain Entry, not a SearchEntry (that one draws a magnifier and a clear icon)
        self.search = Gtk.Entry(placeholder_text="Search…", hexpand=True, css_classes=["clip-search"])
        self.search.connect("changed", lambda *_: self.paint_list())
        enter = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)   # Shift+Enter too
        enter.connect("key-pressed", lambda _c, kv, _k, st: kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                      and (self.copy(paste=bool(st & Gdk.ModifierType.SHIFT_MASK)) or True))
        self.search.add_controller(enter)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["clip-list"])
        self.list.connect("row-selected", self.row_selected)
        self.list.connect("row-activated", lambda *_: self.copy())
        self.scroll = scrolled(self.list)
        middle = box(True, 0, self.search, self.scroll, classes=("clip-middle",))
        middle.set_size_request(380, -1)
        middle.set_hexpand(False)   # the search's hexpand would spread to it: the preview gets the rest

        self.detail = box(True, 10)
        for edge in ("start", "end", "top", "bottom"):
            getattr(self.detail, f"set_margin_{edge}")(18)
        page = scrolled(self.detail)
        page.set_hexpand(True)   # the detail takes the rest of the width (a wrapped line otherwise wraps per word)
        self.reload()
        GLib.idle_add(lambda: self.search.grab_focus() and False)
        return box(False, 0, side, middle, page)

    def leave_text(self):
        row = self.list.get_selected_row()
        (row.grab_focus() if row else self.window.set_focus(None))

    # ---- data --------------------------------------------------------------------------------------------
    def reload(self, then=None):
        """Read the history in a thread, then repaint (keeping the selection)."""
        def work():
            items = load()
            paused = '"paused"' in elephant("state", "--json", "clipboard")
            GLib.idle_add(self.loaded, items, paused, then)
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, items, paused, then):
        self.items, self.paused = items, paused
        self.pause_b.set_label("Resume" if paused else "Pause")
        self.set_subtitle("paused: copies aren't recorded" if paused else "history, newest first")
        if then:
            then()
        self.paint_list()

    def shown_items(self):
        q = self.search.get_text().strip().lower()
        out = []
        for it in self.items:
            if self.tab == "pinned" and not it["pinned"] or self.tab not in ("all", "pinned") and it["kind"] != self.tab:
                continue
            if q and (it["kind"] == "image" or q not in it["text"].lower()):
                continue
            out.append(it)
        return out

    def current(self):
        return next((it for it in self.items if it["id"] == self.selected), None)

    # ---- painting ----------------------------------------------------------------------------------------
    def paint_list(self):
        for key, _ in TABS:
            n = sum(1 for it in self.items if key == "all" or (it["pinned"] if key == "pinned" else it["kind"] == key))
            self.tab_labels[key].set_text(str(n) if n else "")
        self.painting = True
        clear(self.list)
        shown = self.shown_items()
        if shown and not any(it["id"] == self.selected for it in shown):
            self.selected = shown[0]["id"]
        for it in shown[:SHOWN]:
            glyph = label(PIN if it["pinned"] else GLYPH[it["kind"]], "amber" if it["pinned"] else "dim",
                          "clip-glyph")
            text = "Image" if it["kind"] == "image" else first_line(it["text"]) or "(blank)"
            row = Gtk.ListBoxRow(child=box(False, 8, glyph, label(text, "sub", ellipsize=True),
                                           label(ago(it["when"]), "dim")))
            row.item = it["id"]
            self.list.append(row)
            if it["id"] == self.selected:
                self.list.select_row(row)
        if len(shown) > SHOWN:
            more = label(f"{len(shown) - SHOWN} older — search to find them", "dim", xalign=0.5)
            self.list.append(Gtk.ListBoxRow(child=more, activatable=False, selectable=False))
        if not shown:
            empty = {"pinned": "Nothing pinned yet. Ctrl+P on an item keeps it here, and through Clear history.",
                     "image": "No images copied.", "link": "No links copied."}.get(self.tab, "Nothing copied yet.")
            if self.search.get_text().strip():
                empty = f"Nothing matches “{self.search.get_text().strip()}”."
            empty = label(empty, "dim", wrap=True)
            empty.set_max_width_chars(40)
            empty.set_margin_start(10)
            empty.set_margin_top(6)
            self.list.append(Gtk.ListBoxRow(child=empty, activatable=False, selectable=False))
            self.selected = None
        self.painting = False
        self.paint_detail()

    def row_selected(self, _l, row):
        if row is not None and not self.painting and getattr(row, "item", None):
            self.selected = row.item
            self.disarm()
            self.paint_detail()

    def tab_selected(self, _l, row):
        if row is not None and row.tab != self.tab:
            self.tab = row.tab
            self.paint_list()
            self.scroll.get_vadjustment().set_value(0)

    def set_tab(self, key):
        idx = [k for k, _ in TABS].index(key)
        self.tabs.select_row(self.tabs.get_row_at_index(idx))

    def paint_detail(self):
        clear(self.detail)
        it = self.current()
        if not it:
            self.detail.append(label("Copy something (Ctrl+C) and it shows here." if not self.items else
                                     "Nothing selected.", "dim", wrap=True))
            return
        what = {"image": "Image", "link": "Link", "text": "Text"}[it["kind"]]
        if it["kind"] != "image":
            n = len(it["text"])
            lines = it["text"].count("\n") + 1
            what += f" · {n} character{'s' * (n != 1)}" + (f" · {lines} lines" if lines > 1 else "")
        head = box(False, 8, label(what, "bold", ellipsize=True), label(ago(it["when"]), "dim"))
        if it["pinned"]:
            head.prepend(label(PIN, "amber"))
        self.detail.append(head)

        acts = box(False, 6, classes=("clip-actions",))
        acts.append(button("Copy", lambda: self.copy(), "primary", tooltip="Enter"))
        acts.append(button("Paste", lambda: self.copy(paste=True), "flat", tooltip="Into the window under the panel (Shift+Enter)"))
        acts.append(button("Unpin" if it["pinned"] else "Pin", lambda: self.toggle_pin(), "flat", tooltip="Ctrl+P"))
        if it["kind"] == "link":
            acts.append(button("Open", lambda: self.open_link(), "flat", tooltip="In the browser (Ctrl+O)"))
        acts.append(button("Edit", lambda: self.edit(), "flat", tooltip="In the editor; saved back to the history (Ctrl+E)"))
        acts.append(button("Phone", lambda: self.send_phone(), "flat", tooltip="Send it with LocalSend (Ctrl+S)"))
        self.delete_b = button("Delete", lambda: self.delete(), "flat", "danger", tooltip="Delete")
        acts.append(self.delete_b)
        self.detail.append(acts)

        if it["kind"] == "image":
            pic = Gtk.Picture.new_for_filename(it["path"])
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_can_shrink(True)
            pic.set_vexpand(True)
            pic.set_size_request(-1, 300)
            self.detail.append(pic)
            self.detail.append(label(it["path"], "dim", ellipsize=True))
        else:
            text = it["text"] if len(it["text"]) < 20000 else it["text"][:20000] + "\n…"
            body = label(text, "clip-text", wrap=True)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_selectable(True)
            body.set_focusable(False)   # selectable labels take the focus (and the keys) otherwise
            body.set_valign(Gtk.Align.START)
            body.set_hexpand(True)
            self.detail.append(body)

    # ---- actions -----------------------------------------------------------------------------------------
    def copy(self, paste=False):
        it = self.current()
        if not it:
            return
        act(it["id"], "copy")
        self.close()
        if paste:   # once the panel is gone and the window under it has the focus again
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--paste"], start_new_session=True)

    def toggle_pin(self):
        it = self.current()
        if it:
            act(it["id"], "unpin" if it["pinned"] else "pin")
            self.reload()

    def delete(self):
        it = self.current()
        if not it:
            return
        if it["pinned"] and not self.ask("delete", self.delete_b, "Delete?"):
            return
        shown = [i["id"] for i in self.shown_items()]
        pos = shown.index(it["id"]) if it["id"] in shown else 0
        after = shown[pos + 1] if pos + 1 < len(shown) else shown[pos - 1] if pos else None

        def work():
            if it["pinned"]:   # elephant only removes unpinned items
                act(it["id"], "unpin")
            act(it["id"], "remove")
            GLib.idle_add(self.reload, lambda: setattr(self, "selected", after))
        threading.Thread(target=work, daemon=True).start()

    def clear_history(self):
        if not self.ask("clear", self.clear_b, "Clear all?"):
            return
        doomed = [it["id"] for it in self.items if not it["pinned"]]

        def work():
            for i in doomed:   # one by one: elephant's own "remove_all" doesn't say what it does with pins
                act(i, "remove")
            GLib.idle_add(self.say, f"Deleted {len(doomed)} item{'s' * (len(doomed) != 1)}; pinned ones kept")
            GLib.idle_add(self.reload)
        threading.Thread(target=work, daemon=True).start()

    def ask(self, what, b, question):
        """True if what was asked within the last 3 s (do it); otherwise ask: the button turns red."""
        if self.armed == what:
            self.disarm()
            return True
        self.disarm()
        self.armed, self.armed_button = what, (b, b.get_label())
        b.add_css_class("armed")
        b.set_label(question)
        self.armed_timer = GLib.timeout_add(3000, self.disarm)
        return False

    def disarm(self):
        if self.armed_timer:
            GLib.source_remove(self.armed_timer)
            self.armed_timer = 0
        if self.armed_button:
            b, text = self.armed_button
            b.remove_css_class("armed")
            b.set_label(text)
        self.armed, self.armed_button = None, None
        return False

    def edit(self):
        it = self.current()
        if it:
            threading.Thread(target=act, args=(it["id"], "edit"), daemon=True).start()
            self.close()

    def open_link(self):
        it = self.current()
        if it and it["kind"] == "link":
            url = it["text"].strip()
            url = url if "://" in url else "https://" + url
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.close()

    def send_phone(self):
        it = self.current()
        if not it:
            return
        path = it["path"]
        if it["kind"] != "image":   # LocalSend sends files: the text as a .txt
            path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "clipboard.txt")
            with open(path, "w") as f:
                f.write(it["text"])
        subprocess.Popen([os.path.expanduser("~/.local/bin/lsend"), "send", path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def toggle_pause(self):
        def work():
            elephant("activate", f"clipboard;;{'unpause' if self.paused else 'pause'};;")
            GLib.idle_add(self.reload)
        threading.Thread(target=work, daemon=True).start()

    # ---- keys --------------------------------------------------------------------------------------------
    def move(self, step):
        shown = self.shown_items()
        ids = [it["id"] for it in shown[:SHOWN]]
        if not ids:
            return
        pos = ids.index(self.selected) if self.selected in ids else 0
        pos = max(0, min(len(ids) - 1, pos + step))
        row = self.list.get_row_at_index(pos)
        self.list.select_row(row)
        row.grab_focus() if not self.typing() else None
        adj = self.scroll.get_vadjustment()   # keep the selected row in view while the search has the keys
        ok, rect = row.compute_bounds(self.list)
        if ok:
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())

    def key(self, keyval, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
        if keyval == Gdk.KEY_Escape:   # the search has the keyboard: gtkkit leaves Esc to the view
            self.search.set_text("") if self.search.get_text() else self.close()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.copy(paste=bool(shift))
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.move({Gdk.KEY_Down: 1, Gdk.KEY_Up: -1, Gdk.KEY_Page_Down: 10, Gdk.KEY_Page_Up: -10}[keyval])
        elif ctrl and name in "12345" and len(name) == 1:
            self.set_tab(TABS[int(name) - 1][0])
        elif ctrl and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            keys = [k for k, _ in TABS]
            self.set_tab(keys[(keys.index(self.tab) + (-1 if shift else 1)) % len(keys)])
        elif ctrl and shift and keyval == Gdk.KEY_Delete:
            self.clear_history()
        elif keyval == Gdk.KEY_Delete and (not self.typing() or not self.search.get_text()):
            self.delete()
        elif ctrl and name == "p":
            self.toggle_pin()
        elif ctrl and name == "e":
            self.edit()
        elif ctrl and name == "o":
            self.open_link()
        elif ctrl and name == "s":
            self.send_phone()
        elif not self.typing() and not ctrl:
            ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if not (ch.isprintable() and ch != "\0"):
                return False
            self.search.grab_focus()   # typing anywhere searches
            self.search.set_text(self.search.get_text() + ch)
            self.search.set_position(-1)
        else:
            return False
        return True


def make():
    return Clipboard()


def main():
    if sys.argv[1:] == ["--paste"]:
        return paste_later()
    run(make(), "panels.clip", (1040, 600))


if __name__ == "__main__":
    main()
