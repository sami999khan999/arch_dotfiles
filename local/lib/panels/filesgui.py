#!/usr/bin/env python3
# filesgui.py — Files, the file manager (Super + E, workspace 7): the panels' look, in every colour theme.
#   filesgui.py [folder]      opens there (the running one goes there: xdg-open of a folder lands here)
# Places and drives on the left; a path bar, the folder as a sortable table (folders first), a filter.
# Open, rename, new folder / file, copy / cut / paste (also to and from other apps, as file URIs), to the
# trash (permanently with Shift+Delete, or where a drive has no trash, after asking), a terminal here.
# The folder is watched: what changes in it shows by itself. Remembered (~/.local/state/files): the last
# folder and whether hidden files show.
import gc, json, os, shutil, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gio, GLib, Gtk, Pango, PanelApp, View, box, button, clear, label, scrolled
from gi.repository import GObject

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or f"{HOME}/.local/state", "files", "state.json")
G_FOLDER, G_FILE, G_LINK = "\uf07b", "\uf15b", "\uf0c1"
G_HOME, G_DRIVE, G_ROOT, G_UP = "\uf015", "\uf0a0", "\uf0a0", "\uf062"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()   # English, like the rest (not the locale's)
# a file's glyph and its colour, by extension (Nerd Font; the rest: G_FILE, dim)
KINDS = [({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif", "heic"}, "\uf1c5", "magenta"),
         ({"mp4", "mkv", "webm", "mov", "avi"}, "\uf1c8", "magenta"),
         ({"mp3", "flac", "ogg", "wav", "m4a", "opus"}, "\uf1c7", "magenta"),
         ({"zip", "tar", "gz", "xz", "zst", "bz2", "7z", "rar", "tgz"}, "\uf1c6", "amber"),
         ({"pdf"}, "\uf1c1", "red"),
         ({"py", "js", "ts", "tsx", "jsx", "lua", "sh", "fish", "rs", "go", "c", "h", "cpp", "java", "rb", "php",
           "html", "css", "scss", "vue", "svelte"}, "\uf121", "cyan"),
         ({"json", "toml", "yaml", "yml", "conf", "ini", "jsonc", "xml", "env", "lock"}, "\ue615", "amber"),
         ({"md", "txt", "rst", "log", "csv"}, "\uf15c", "sub")]
CSS = """
.files-side > row { padding: 5px 10px; }
.files-side > row.place-head { padding: 12px 10px 4px 10px; }
.place-head-text { color: #565f89; font-weight: 700; font-size: 9pt; }
.place-glyph { min-width: 18px; color: #6b8fe0; }
.crumbs button.flat { padding: 3px 6px; }
.crumb-sep { color: #3b4261; }
.crumb-last { color: #c0caf5; font-weight: 700; }
.files columnview, .files columnview listview { background: transparent; color: #c0caf5; }
.files columnview > header > button { background: transparent; border: none; box-shadow: none; color: #565f89; padding: 4px 8px; }
.files columnview > header > button:hover { color: #c0caf5; }
.files columnview listview > row { padding: 0; }
.files columnview listview > row:hover { background: alpha(#292e42, .45); }
.files columnview listview > row:selected { background: #292e42; color: #c0caf5; }
.files columnview listview > row > cell { padding: 3px 8px; }
.file-glyph { min-width: 18px; }
.prompt { border-top: 1px solid alpha(#3b4261, .7); padding: 8px 2px 0 2px; }
.menu-btn { padding: 4px 14px; }
popover.files-menu > contents { padding: 4px; }
.info { color: #565f89; padding-top: 6px; }
button.danger-fill { background: alpha(#f7768e, .16); color: #f7768e; }
button.danger-fill:hover { background: alpha(#f7768e, .26); }
"""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def when(t):
    lt, now = time.localtime(t), time.localtime()
    hm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
    if lt[:3] == now[:3]:
        return f"Today {hm}"
    if time.localtime(time.time() - 86400)[:3] == lt[:3]:
        return f"Yesterday {hm}"
    day = f"{lt.tm_mday:02d} {MONTHS[lt.tm_mon - 1]}"
    return f"{day} {hm}" if lt.tm_year == now.tm_year else f"{day} {lt.tm_year}"


def kind_of(name, is_dir):
    if is_dir:
        return G_FOLDER, "accent", "Folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
    glyph, cls = next(((g, c) for exts, g, c in KINDS if ext in exts), (G_FILE, "dim"))
    ctype = Gio.content_type_guess(name, None)[0]
    return glyph, cls, Gio.content_type_get_description(ctype) if ctype else (ext.upper() or "File")


def free_name(folder, name):
    """name in folder, or "name (2)", "name (3)"… if it's taken."""
    if not os.path.lexists(os.path.join(folder, name)):
        return name
    stem, dot, ext = name.rpartition(".") if "." in name[1:] else (name, "", "")
    for n in range(2, 10000):
        cand = f"{stem} ({n}){dot}{ext}"
        if not os.path.lexists(os.path.join(folder, cand)):
            return cand
    return name


class Entry(GObject.Object):
    """One row: a file or folder of the shown folder."""
    def __init__(self, folder, de):
        super().__init__()
        self.name, self.path = de.name, os.path.join(folder, de.name)
        self.link = de.is_symlink()
        try:
            self.is_dir = de.is_dir()   # a link to a folder is a folder
            st = de.stat()
            self.size, self.mtime = st.st_size, st.st_mtime
        except OSError:   # a broken link
            self.is_dir, self.size, self.mtime = False, 0, 0
        self.glyph, self.cls, self.type = kind_of(self.name, self.is_dir)
        self.count = None   # a folder's item count, filled in after
        self.hidden = self.name.startswith(".")
        self.key = self.name.casefold()


class Files(View):
    icon = G_FOLDER
    title = "Files"
    state_file = STATE   # the file picker (pickergui.py) keeps its own
    interval = 0
    css = CSS
    hints = [("Enter", "open"), ("Bksp", "back"), ("F2", "rename"), ("Del", "trash"), ("Ctrl+C/X/V", "copy / cut / paste"),
             ("Ctrl+H", "hidden"), ("/", "filter"), ("F4", "terminal")]

    def __init__(self, start=None):
        super().__init__()
        try:
            with open(self.state_file) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        self.show_hidden = bool(self.state.get("hidden", False))
        self.cwd = None
        self.start = start if start and os.path.isdir(start) else self.state.get("dir", HOME)
        self.back, self.forward = [], []
        self.clip = None   # ("copy" | "cut", [paths]) we put on the clipboard
        self.monitor = None
        self.gen = 0       # the folder load in flight: an older one's result is dropped
        self.prompt_ok = None

    # ---- layout ------------------------------------------------------------------------------------
    def build(self):
        self.places = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.places.add_css_class("files-side")
        self.places.connect("row-activated", lambda _l, row: row.path and self.go(row.path))
        side = scrolled(self.places)
        side.add_css_class("side")
        side.set_size_request(210, -1)
        side.set_hexpand(False)
        side.set_margin_top(10)

        self.nav_back = button("\uf060", self.go_back, "flat", tooltip="back (Backspace, Alt+←)")
        self.nav_fwd = button("\uf061", self.go_forward, "flat", tooltip="forward (Alt+→)")
        up = button(G_UP, self.go_up, "flat", tooltip="the folder above (Alt+↑)")
        self.crumbs = box(False, 0, classes=("crumbs",))
        # a long path scrolls, its end in view, rather than widening the window past a small screen
        self.crumb_scroll = Gtk.ScrolledWindow(child=self.crumbs, hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                               hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.crumb_scroll.get_hadjustment().connect("changed", lambda a: a.set_value(a.get_upper()))   # laid out: the end
        self.path_entry = Gtk.Entry(hexpand=True)
        self.path_entry.connect("activate", lambda e: self.typed_path(e.get_text()))
        self.path_entry.set_visible(False)
        edit = Gtk.GestureClick()   # a click beside the crumbs: type a path (as Ctrl+L)
        edit.connect("released", lambda *_: self.edit_path())
        self.crumb_scroll.add_controller(edit)
        self.filter = Gtk.SearchEntry(placeholder_text="/ filter")
        self.filter.set_size_request(220, -1)
        self.filter.connect("search-changed", lambda *_: self.refilter())
        self.filter.connect("activate", lambda *_: self.table.grab_focus())
        bar = box(False, 4, self.nav_back, self.nav_fwd, up, self.crumb_scroll, self.path_entry, self.filter)

        self.store = Gio.ListStore(item_type=Entry)
        self.filt = Gtk.CustomFilter.new(self.visible)
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filt)
        self.table = Gtk.ColumnView(reorderable=False, show_row_separators=False, enable_rubberband=True)
        cols = {}
        for title, kind, sort_key, width in (("Name", "name", lambda e: e.key, 0),
                                             ("Size", "size", lambda e: e.count if e.is_dir else e.size, 110),
                                             ("Modified", "mtime", lambda e: e.mtime, 170),
                                             ("Type", "type", lambda e: e.type.casefold(), 190)):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self.cell_setup, kind)
            factory.connect("bind", self.cell_bind, kind)
            col = Gtk.ColumnViewColumn(title=title, factory=factory, expand=not width, resizable=True)
            if width:
                col.set_fixed_width(width)
            col.set_sorter(Gtk.CustomSorter.new(
                lambda a, b, _u, k=sort_key: (lambda x, y: (x > y) - (x < y))(k(a) or 0, k(b) or 0)))
            self.table.append_column(col)
            cols[kind] = col
        dirs_first = Gtk.CustomSorter.new(lambda a, b, _u: int(b.is_dir) - int(a.is_dir))
        sorter = Gtk.MultiSorter()
        sorter.append(dirs_first)        # folders stay first whichever way a column sorts
        sorter.append(self.table.get_sorter())
        self.sorted = Gtk.SortListModel(model=filtered, sorter=sorter)
        self.sel = Gtk.MultiSelection(model=self.sorted)
        self.sel.connect("selection-changed", lambda *_: self.update_info())
        self.table.set_model(self.sel)
        self.table.sort_by_column(cols["name"], Gtk.SortType.ASCENDING)
        self.table.connect("activate", lambda _t, pos: self.open_entry(self.sorted.get_item(pos)))
        empty = Gtk.GestureClick(button=3)   # right click on the empty part: the folder's menu
        empty.connect("pressed", lambda g, _n, x, y: self.menu(None, self.table, x, y))
        self.table.add_controller(empty)
        self.list_scroll = scrolled(self.table)
        self.list_scroll.add_css_class("files")
        self.empty_note = label("", "dim")
        self.empty_note.set_margin_top(12)

        # the question bar under the list: a name to type (rename, new folder) or a yes / no
        self.prompt_text = label("", "sub")
        self.prompt_entry = Gtk.Entry(hexpand=True)
        self.prompt_entry.connect("activate", lambda *_: self.prompt_go())
        self.prompt_yes = button("OK", self.prompt_go, "primary")
        self.prompt = box(False, 8, self.prompt_text, self.prompt_entry, self.prompt_yes,
                          button("Cancel", self.prompt_close, "flat"), classes=("prompt",))
        self.prompt.set_visible(False)
        self.info = label("", "info", ellipsize=True)

        main = box(True, 8, bar, self.list_scroll, self.empty_note, self.prompt, self.info)
        main.set_hexpand(True)
        self.main = main
        for edge in ("start", "end", "top", "bottom"):
            getattr(main, f"set_margin_{edge}")(12)
        self.paint_places()
        GLib.idle_add(lambda: self.go(self.start, remember=False) and False)
        return box(False, 0, side, main)

    def cell_setup(self, _f, item, kind):
        if kind == "name":
            w = box(False, 8, label("", "file-glyph"), label("", ellipsize=True))
            w.get_last_child().set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        else:
            w = label("", "dim" if kind != "size" else "sub", ellipsize=True)
            if kind == "size":
                w.set_xalign(1.0)
        menu = Gtk.GestureClick(button=3)   # right click on a row: its menu (selected first)
        menu.connect("pressed", lambda g, _n, x, y, item=item: self.row_menu(g, item, x, y))
        w.add_controller(menu)
        item.set_child(w)

    def cell_bind(self, _f, item, kind):
        e, w = item.get_item(), item.get_child()
        if kind == "name":
            glyph, name = w.get_first_child(), w.get_last_child()
            glyph.set_text(e.glyph if not e.link or e.is_dir else G_LINK)
            for c in ("accent", "magenta", "amber", "red", "cyan", "sub", "dim"):
                glyph.remove_css_class(c)
            glyph.add_css_class(e.cls)
            name.set_text(e.name + (" →" if e.link else ""))   # a link shows → (its target in the tooltip)
            name.set_tooltip_text(os.path.realpath(e.path) if e.link else None)
            name.set_opacity(0.6 if e.hidden else 1.0)
        elif kind == "size":
            w.set_text(("" if e.count is None else f"{e.count} item{'s' * (e.count != 1)}") if e.is_dir else human(e.size))
        elif kind == "mtime":
            w.set_text(when(e.mtime) if e.mtime else "")
        else:
            w.set_text(e.type)

    # ---- places ------------------------------------------------------------------------------------
    def paint_places(self):
        clear(self.places)
        dirs = {}
        try:   # the XDG folders, by their configured names
            for line in open(f"{HOME}/.config/user-dirs.dirs"):
                if line.startswith("XDG_") and "=" in line:
                    k, v = line.split("=", 1)
                    dirs[k[4:-4]] = os.path.expandvars(v.strip().strip('"'))
        except OSError:
            pass
        places = [(G_HOME, "Home", HOME)]
        for key, title, glyph in (("DESKTOP", "Desktop", "\uf108"), ("DOCUMENTS", "Documents", "\uf15c"),
                                  ("DOWNLOAD", "Downloads", "\uf019"), ("PICTURES", "Pictures", "\uf03e"),
                                  ("MUSIC", "Music", "\uf001"), ("VIDEOS", "Videos", "\uf03d")):
            p = dirs.get(key)
            if p and os.path.isdir(p) and p != HOME:
                places.append((glyph, title, p))
        for glyph, title, p in (("\uf121", "code", f"{HOME}/code"), ("\uf013", "dotfiles", f"{HOME}/dotfiles")):
            if os.path.isdir(p):
                places.append((glyph, title, p))
        drives = [(G_ROOT, "Root", "/")]
        try:
            for line in open("/proc/mounts"):
                mp = line.split()[1].replace("\\040", " ")
                if mp.startswith(("/mnt/", "/media/", f"/run/media/{os.environ.get('USER', '')}/")):
                    drives.append((G_DRIVE, os.path.basename(mp), mp))
        except OSError:
            pass
        for head, rows in (("PLACES", places), ("DRIVES", drives)):
            h = Gtk.ListBoxRow(child=label(head, "place-head-text"), selectable=False, activatable=False)
            h.add_css_class("place-head")
            h.path = None
            self.places.append(h)
            for glyph, title, p in rows:
                row = Gtk.ListBoxRow(child=box(False, 8, label(glyph, "place-glyph"), label(title, ellipsize=True)))
                row.set_tooltip_text(p)
                row.path = p
                self.places.append(row)

    def mark_place(self):
        """The place the folder is in (the deepest one that holds it) is the selected row."""
        best, best_len = None, -1
        row = self.places.get_first_child()
        while row:
            p = getattr(row, "path", None)
            if p and (self.cwd == p or self.cwd.startswith(p.rstrip("/") + "/")) and len(p) > best_len:
                best, best_len = row, len(p)
            row = row.get_next_sibling()
        self.places.select_row(best)

    # ---- going places -----------------------------------------------------------------------------
    def go(self, path, remember=True, keep=None):
        """Show path (a folder). keep: names to select again (a reload of the same folder)."""
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            self.say(f"not a folder: {path}", "bad")
            return
        if not os.access(path, os.R_OK | os.X_OK):
            self.say(f"no permission to open {path}", "bad")
            return
        same = path == self.cwd
        if remember and self.cwd and not same:
            self.back.append(self.cwd)
            self.forward.clear()
        if not same:
            self.filter.set_text("")
            self.prompt_close()
        self.cwd = path
        self.paint_crumbs()
        self.mark_place()
        self.nav_back.set_sensitive(bool(self.back))
        self.nav_fwd.set_sensitive(bool(self.forward))
        self.set_subtitle(path.replace(HOME, "~", 1))
        self.watch(path)
        self.save_state()
        self.gen += 1
        gen, scroll = self.gen, self.list_scroll.get_vadjustment().get_value() if same else 0
        threading.Thread(target=self.load, args=(path, gen, keep, scroll), daemon=True).start()

    def load(self, path, gen, keep, scroll):
        try:
            with os.scandir(path) as it:
                entries = [Entry(path, de) for de in it]
        except OSError as e:
            GLib.idle_add(lambda: self.say(f"can't read {path}: {e.strerror}", "bad") and False)
            entries = []
        GLib.idle_add(self.loaded, gen, entries, keep, scroll)
        for e in entries:   # then each folder's item count (one listing each), shown as they come
            if gen != self.gen:
                return
            if e.is_dir:
                try:
                    e.count = len(os.listdir(e.path))
                except OSError:
                    e.count = None
        GLib.idle_add(self.counted, gen)

    def loaded(self, gen, entries, keep, scroll):
        if gen != self.gen:
            return False
        self.store.splice(0, self.store.get_n_items(), entries)
        if keep:   # a reload: the same files selected, the list where it was
            for i in range(self.sorted.get_n_items()):
                if self.sorted.get_item(i).name in keep:
                    self.sel.select_item(i, False)
            GLib.idle_add(lambda: self.list_scroll.get_vadjustment().set_value(scroll) and False)
        elif self.sorted.get_n_items():
            self.table.scroll_to(0, None, Gtk.ListScrollFlags.FOCUS, None)
        self.update_info()
        if not keep:
            self.table.grab_focus()
        return False

    def counted(self, gen):
        if gen == self.gen:   # the Size column shows the counts now in (a sort by size included)
            self.store.items_changed(0, self.store.get_n_items(), self.store.get_n_items())
        return False

    def watch(self, path):
        """Changes in the folder (made here or elsewhere) reload it, a quarter second after the last."""
        if self.monitor:
            self.monitor.cancel()
        self.reload_timer = 0
        try:
            self.monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self.monitor = None
            return

        def changed(*_):
            if self.reload_timer:
                GLib.source_remove(self.reload_timer)
            self.reload_timer = GLib.timeout_add(250, self.reload)
        self.monitor.connect("changed", changed)

    def reload(self):
        self.reload_timer = 0
        if self.cwd and os.path.isdir(self.cwd):
            self.go(self.cwd, remember=False, keep={e.name for e in self.selected()})
        elif self.cwd:   # the folder itself went: the nearest one still there
            p = self.cwd
            while p != "/" and not os.path.isdir(p):
                p = os.path.dirname(p)
            self.go(p, remember=False)
        return False

    def go_back(self):
        if self.back:
            self.forward.append(self.cwd)
            self.go(self.back.pop(), remember=False)

    def go_forward(self):
        if self.forward:
            self.back.append(self.cwd)
            self.go(self.forward.pop(), remember=False)

    def go_up(self):
        if self.cwd != "/":
            child = os.path.basename(self.cwd)
            self.go(os.path.dirname(self.cwd), keep={child})

    def paint_crumbs(self):
        clear(self.crumbs)
        self.path_entry.set_visible(False)
        self.crumb_scroll.set_visible(True)
        if self.cwd == HOME or self.cwd.startswith(HOME + "/"):
            parts, base = [("\uf015 home", HOME)], HOME
            rest = self.cwd[len(HOME):].strip("/")
        else:
            parts, base, rest = [("/", "/")], "/", self.cwd.strip("/")
        for name in rest.split("/") if rest else []:
            base = os.path.join(base, name)
            parts.append((name, base))
        for i, (name, p) in enumerate(parts):
            last = i == len(parts) - 1
            b = button(name, lambda p=p: self.go(p), "flat")
            if last:
                b.get_child().add_css_class("crumb-last")
            self.crumbs.append(b)
            if not last:
                self.crumbs.append(label("\uf054", "crumb-sep"))

    def edit_path(self):
        self.crumb_scroll.set_visible(False)
        self.path_entry.set_visible(True)
        self.path_entry.set_text(self.cwd.replace(HOME, "~", 1) + ("/" if self.cwd != "/" else ""))
        self.path_entry.grab_focus()
        self.path_entry.set_position(-1)

    def typed_path(self, text):
        p = os.path.expanduser(text.strip())
        if os.path.isdir(p):
            self.go(p)
        elif os.path.exists(p):   # a file: its folder, with it selected
            self.go(os.path.dirname(p), keep={os.path.basename(p)})
        else:
            self.say(f"no such folder: {text}", "bad")
            return
        self.paint_crumbs()

    # ---- what's shown -------------------------------------------------------------------------------
    def visible(self, e):
        if e.hidden and not self.show_hidden:
            return False
        q = self.filter.get_text().strip().casefold() if hasattr(self, "filter") else ""
        return not q or q in e.key

    def refilter(self):
        self.filt.changed(Gtk.FilterChange.DIFFERENT)
        self.update_info()

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.save_state()
        self.refilter()
        self.say("hidden files " + ("shown" if self.show_hidden else "hidden"))

    def selected(self):
        bits = self.sel.get_selection()
        return [self.sorted.get_item(bits.get_nth(i)) for i in range(bits.get_size())]

    def update_info(self):
        n = self.sorted.get_n_items()
        items = [self.sorted.get_item(i) for i in range(n)]
        dirs = sum(e.is_dir for e in items)
        bits = [f"{dirs} folder{'s' * (dirs != 1)}", f"{n - dirs} file{'s' * (n - dirs != 1)}"]
        hidden = 0 if self.show_hidden else sum(1 for e in self.store if e.hidden)
        if hidden and not self.filter.get_text():
            bits.append(f"{hidden} hidden")
        other = self.store.get_n_items() - n - hidden   # the picker's file-type filter
        if other > 0 and not self.filter.get_text():
            bits.append(f"{other} of other types")
        sel = self.selected()
        if sel:
            size = sum(e.size for e in sel if not e.is_dir)
            bits.append(f"{len(sel)} selected" + (f", {human(size)}" if size else ""))
        try:
            st = os.statvfs(self.cwd)
            bits.append(f"{human(st.f_bavail * st.f_frsize)} free")
        except (OSError, TypeError):
            pass
        self.info.set_text("  ·  ".join(bits))
        q = self.filter.get_text().strip()
        self.empty_note.set_text(f"Nothing matches “{q}”." if q and not n else "This folder is empty." if not n else "")
        self.empty_note.set_visible(not n)

    def save_state(self):
        self.state.update(dir=self.cwd, hidden=self.show_hidden)
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self.state, f)
        except OSError:
            pass

    # ---- doing things -------------------------------------------------------------------------------
    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def open_entry(self, e):
        if e is None:
            return
        if e.is_dir:
            self.go(e.path)
        else:   # its default app (mimeapps.list)
            self.launch("xdg-open", e.path)

    def open_selected(self):
        sel = self.selected()
        if len(sel) == 1 and sel[0].is_dir:
            self.go(sel[0].path)
        for e in sel:
            if not e.is_dir:
                self.open_entry(e)

    def terminal(self):
        self.launch("kitty", "--directory", self.cwd)

    def ask(self, text, on_ok, name=None, ok="OK", danger=False):
        """The question bar: a name to type (name: its starting text) or a yes / no. on_ok(name or None)."""
        self.prompt_text.set_text(text)
        self.prompt_entry.set_visible(name is not None)
        self.prompt_yes.set_label(ok)
        (self.prompt_yes.add_css_class if danger else self.prompt_yes.remove_css_class)("danger-fill")
        (self.prompt_yes.remove_css_class if danger else self.prompt_yes.add_css_class)("primary")
        self.prompt_ok = on_ok
        self.prompt.set_visible(True)
        if name is not None:
            self.prompt_entry.set_text(name)
            self.prompt_entry.grab_focus()
            stem = name.rfind(".") if "." in name[1:] else len(name)   # the name selected, not its extension
            self.prompt_entry.select_region(0, stem)
        else:
            self.prompt_yes.grab_focus()

    def prompt_go(self):
        fn = self.prompt_ok
        value = self.prompt_entry.get_text().strip() if self.prompt_entry.get_visible() else None
        if self.prompt_entry.get_visible() and not value:
            return
        self.prompt_close()
        if fn:
            fn(value)

    def prompt_close(self):
        self.prompt_ok = None
        self.prompt.set_visible(False)
        self.table.grab_focus()

    def rename(self):
        sel = self.selected()
        if len(sel) != 1:
            self.say("select one file to rename", "bad")
            return
        e = sel[0]

        def done(new):
            if "/" in new or new in (".", ".."):
                self.say("a name can't have / in it", "bad")
            elif new != e.name and os.path.lexists(os.path.join(self.cwd, new)):
                self.say(f"“{new}” is already here", "bad")
            elif new != e.name:
                try:
                    os.rename(e.path, os.path.join(self.cwd, new))
                    self.go(self.cwd, remember=False, keep={new})
                except OSError as err:
                    self.say(f"couldn't rename: {err.strerror}", "bad")
        self.ask(f"Rename “{e.name}” to", done, name=e.name, ok="Rename")

    def new_folder(self, file=False):
        def done(name):
            if "/" in name:
                self.say("a name can't have / in it", "bad")
                return
            name = free_name(self.cwd, name)
            try:
                if file:
                    open(os.path.join(self.cwd, name), "x").close()
                else:
                    os.mkdir(os.path.join(self.cwd, name))
                self.go(self.cwd, remember=False, keep={name})
            except OSError as err:
                self.say(f"couldn't make it: {err.strerror}", "bad")
        self.ask("New file" if file else "New folder", done, name="new file.txt" if file else "New folder",
                 ok="Create")

    def trash(self, permanent=False):
        sel = self.selected()
        if not sel:
            return
        what = f"“{sel[0].name}”" if len(sel) == 1 else f"{len(sel)} items"
        if permanent:
            self.ask(f"Delete {what} permanently? This can't be undone.", lambda _v: self.delete_now(sel),
                     ok="Delete", danger=True)
            return

        def work():
            stuck = []
            for e in sel:
                try:
                    Gio.File.new_for_path(e.path).trash(None)
                except GLib.Error:
                    stuck.append(e)   # no trash on that drive (or no permission)

            def after():
                if stuck:
                    names = f"“{stuck[0].name}”" if len(stuck) == 1 else f"{len(stuck)} items"
                    self.ask(f"{names} can't go to the trash here. Delete permanently?",
                             lambda _v: self.delete_now(stuck), ok="Delete", danger=True)
                else:
                    self.say(f"{what} moved to the trash")
                return False
            GLib.idle_add(after)
        threading.Thread(target=work, daemon=True).start()

    def delete_now(self, entries):
        def work():
            errors = []
            for e in entries:
                try:
                    if e.is_dir and not e.link:
                        shutil.rmtree(e.path)
                    else:
                        os.remove(e.path)
                except OSError as err:
                    errors.append(f"{e.name}: {err.strerror}")
            GLib.idle_add(lambda: self.say(errors[0] if errors else f"deleted {len(entries)} item"
                                           + "s" * (len(entries) != 1), "bad" if errors else "ok") and False)
        threading.Thread(target=work, daemon=True).start()

    # ---- the clipboard: our own list, and file URIs for other apps ------------------------------------
    def copy(self, cut=False):
        sel = self.selected()
        if not sel:
            return
        paths = [e.path for e in sel]
        self.clip = ("cut" if cut else "copy", paths)
        uris = "\r\n".join(Gio.File.new_for_path(p).get_uri() for p in paths)
        gnome = ("cut" if cut else "copy") + "\n" + "\n".join(Gio.File.new_for_path(p).get_uri() for p in paths)
        provider = Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_bytes("x-special/gnome-copied-files", GLib.Bytes.new(gnome.encode())),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uris.encode())),
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new("\n".join(paths).encode()))])
        self.window.get_clipboard().set_content(provider)
        self.say(f"{'cut' if cut else 'copied'} {len(paths)} item{'s' * (len(paths) != 1)}" + (" (paste moves them)" if cut else ""))

    def copy_path(self):
        sel = self.selected()
        text = "\n".join(e.path for e in sel) if sel else self.cwd
        self.window.get_clipboard().set(text)
        self.say(f"path copied: {text if len(sel) <= 1 else f'{len(sel)} paths'}")

    def paste(self):
        """Ours first; else file URIs another app copied (Dolphin, a browser's download…)."""
        if self.clip:
            mode, paths = self.clip
            self.paste_paths(paths, move=mode == "cut")
            return
        cb = self.window.get_clipboard()

        def got(_cb, res):
            try:
                text = cb.read_text_finish(res) or ""
            except GLib.Error:
                text = ""
            paths = [Gio.File.new_for_uri(u.strip()).get_path() if u.strip().startswith("file://") else u.strip()
                     for u in text.splitlines() if u.strip()]
            paths = [p for p in paths if p and os.path.exists(p)]
            if paths:
                self.paste_paths(paths, move=False)
            else:
                self.say("nothing to paste", "bad")
        cb.read_text_async(None, got)

    def paste_paths(self, paths, move):
        dest = self.cwd
        for p in paths:
            if os.path.isdir(p) and (dest == p or dest.startswith(p.rstrip("/") + "/")):
                self.say(f"can't put “{os.path.basename(p)}” inside itself", "bad")
                return
        self.say(f"{'moving' if move else 'copying'} {len(paths)} item{'s' * (len(paths) != 1)}…", seconds=600)

        def work():
            done, errors, names = 0, [], set()
            for p in paths:
                if move and os.path.dirname(p.rstrip("/")) == dest:
                    continue   # already here
                name = free_name(dest, os.path.basename(p.rstrip("/")))
                target = os.path.join(dest, name)
                try:
                    if move:
                        shutil.move(p, target)
                    elif os.path.isdir(p) and not os.path.islink(p):
                        shutil.copytree(p, target, symlinks=True)
                    else:
                        shutil.copy2(p, target, follow_symlinks=False)
                    done += 1
                    names.add(name)
                except (OSError, shutil.Error) as err:
                    errors.append(f"{os.path.basename(p)}: {getattr(err, 'strerror', None) or err}")

            def after():
                if move and not errors:
                    self.clip = None   # moved: nothing left to paste
                self.say(errors[0] if errors else f"{'moved' if move else 'copied'} {done} item{'s' * (done != 1)}",
                         "bad" if errors else "ok")
                if self.cwd == dest:
                    self.go(dest, remember=False, keep=names)
                return False
            GLib.idle_add(after)
        threading.Thread(target=work, daemon=True).start()

    # ---- the right-click menu ------------------------------------------------------------------------
    def row_menu(self, gesture, item, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)   # not the folder's menu as well
        pos = item.get_position()
        if not self.sel.is_selected(pos):
            self.sel.select_item(pos, True)
        _ok, px, py = gesture.get_widget().translate_coordinates(self.table, x, y)
        self.menu(self.selected(), self.table, px, py)

    def menu(self, sel, parent, x, y):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("files-menu")
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        items = []
        if sel:
            items += [("Open", self.open_selected, "Enter"), None,
                      ("Cut", lambda: self.copy(cut=True), "Ctrl+X"), ("Copy", self.copy, "Ctrl+C"),
                      ("Copy path", self.copy_path, "Ctrl+Shift+C"), None]
            if len(sel) == 1:
                items.append(("Rename…", self.rename, "F2"))
            items += [("Move to trash", self.trash, "Del"), ("Delete permanently…", lambda: self.trash(True), "Shift+Del")]
        else:
            items += [("New folder…", self.new_folder, "Ctrl+Shift+N"), ("New file…", lambda: self.new_folder(file=True), ""),
                      None, ("Paste", self.paste, "Ctrl+V"), ("Copy path", self.copy_path, "Ctrl+Shift+C"), None,
                      ("Open terminal here", self.terminal, "F4"),
                      ("Hide hidden files" if self.show_hidden else "Show hidden files", self.toggle_hidden, "Ctrl+H")]
        col = box(True, 0)
        for it in items:
            if it is None:
                sep = Gtk.Separator()
                sep.set_margin_top(3)
                sep.set_margin_bottom(3)
                col.append(sep)
                continue
            text, fn, key = it
            row = box(False, 24, label(text, *(("red",) if "Delete" in text else ())), label(key, "dim"))
            row.get_first_child().set_hexpand(True)
            b = Gtk.Button(child=row)
            b.add_css_class("flat")
            b.add_css_class("menu-btn")
            b.connect("clicked", lambda _b, fn=fn: (pop.popdown(), fn()))
            col.append(b)
        pop.set_child(col)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    # ---- keys -----------------------------------------------------------------------------------------
    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if keyval == Gdk.KEY_Escape:
            if self.prompt.get_visible():
                self.prompt_close()
            elif self.path_entry.get_visible():
                self.paint_crumbs()
                self.table.grab_focus()
            elif self.filter.get_text():
                self.filter.set_text("")
                self.table.grab_focus()
            else:
                self.sel.unselect_all()
            return True
        if ctrl and k == Gdk.KEY_l:
            self.edit_path()
            return True
        if ctrl and k == Gdk.KEY_f:
            self.filter.grab_focus()
            return True
        if self.typing():
            return False
        if alt and keyval == Gdk.KEY_Left or keyval == Gdk.KEY_BackSpace:
            self.go_back()
        elif alt and keyval == Gdk.KEY_Right:
            self.go_forward()
        elif alt and keyval == Gdk.KEY_Up:
            self.go_up()
        elif keyval == Gdk.KEY_slash:
            self.filter.grab_focus()
        elif ctrl and k == Gdk.KEY_h:
            self.toggle_hidden()
        elif ctrl and shift and k == Gdk.KEY_n:
            self.new_folder()
        elif ctrl and shift and k == Gdk.KEY_c:
            self.copy_path()
        elif ctrl and k == Gdk.KEY_c:
            self.copy()
        elif ctrl and k == Gdk.KEY_x:
            self.copy(cut=True)
        elif ctrl and k == Gdk.KEY_v:
            self.paste()
        elif keyval == Gdk.KEY_F2:
            self.rename()
        elif keyval == Gdk.KEY_Delete:
            self.trash(permanent=shift)
        elif keyval == Gdk.KEY_F4:
            self.terminal()
        elif keyval == Gdk.KEY_F5:
            self.reload()
        else:
            return False
        return True


class FilesApp(PanelApp):
    """The panels' window, also taking a folder to open: `filesgui.py <folder>` (xdg-open of a folder)
    sends it to the running one, which goes there."""
    def __init__(self, view):
        super().__init__(view, "sami.files", (1200, 720), toggle=False)
        self.set_flags(Gio.ApplicationFlags.HANDLES_OPEN)

    def do_open(self, files, _n, _hint):
        self.activate()
        p = files[0].get_path() if files else None
        if p:
            GLib.idle_add(lambda: (self.view.go(p) if os.path.isdir(p) else
                                   self.view.go(os.path.dirname(p), keep={os.path.basename(p)})) and False)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, lambda: gc.collect() is not None)
    FilesApp(Files()).run(sys.argv)


if __name__ == "__main__":
    main()
