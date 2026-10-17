#!/usr/bin/env python3
# docgui.py — Documents, the PDF viewer (the default for PDFs: config/mimeapps.list).
#   docgui.py <file.pdf>   shows it; the running viewer takes what's opened next
# The pages in a column (scroll, or a page at a time: Space / PgDn), zoom (fit the width, fit the page,
# +/−, Ctrl+wheel), fullscreen (f: a page fills the screen and ←/→ turn them, as a presentation), the
# outline (t), find (Ctrl+F), links (a page link jumps, a web link opens in the browser), a password
# asked for a locked file, and the file watched (a LaTeX build shows up). Poppler draws the pages, in a
# thread with its own copy of the document; only the pages near the view are drawn and kept.
# Remembered (~/.local/state/documents): each file's page and zoom, the outline pane.
import gc, json, os, subprocess, sys, threading
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, Gio, GLib, Graphene, Gtk, PanelApp, View, box, button, clear, collect, label, rgbf,
                    scrolled)
import gi
gi.require_version("Gsk", "4.0")
gi.require_version("Poppler", "0.18")
gi.require_foreign("cairo")
from gi.repository import Gsk, Poppler
import cairo

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or f"{HOME}/.local/state", "documents", "state.json")
FILES = f"{HOME}/.local/lib/panels/filesgui.py"
FILES_KEPT = 300             # files whose page and zoom are remembered
GAP = 12                     # px between pages, and around them
CACHE_BYTES = 300 << 20      # drawn pages kept (the ones nearest the view stay)
MAX_PIXELS = 40_000_000      # a page drawn bigger than this is drawn smaller and stretched
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.1, 8.0, 1.2
G_DOC, G_FULL = "", "\U000f0293"
CSS = """
.doc-view { background: transparent; }
.outline-pane { border-right: 1px solid alpha(#3b4261, .7); }
.outline-pane listbox { background: transparent; }
.outline-pane row { padding: 4px 14px; }
.outline-pane row:selected { background: #292e42; }
.prompt { border-top: 1px solid alpha(#3b4261, .7); padding: 8px 20px; }
.canvas-msg { color: #565f89; }
.menu-btn { padding: 4px 14px; }
popover.docs-menu > contents { padding: 4px; }
window.panel.full { background: #16161e; }
"""


def path_short(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def open_doc(path, password=None):
    return Poppler.Document.new_from_file(Gio.File.new_for_path(path).get_uri(), password)


def dest_page(doc, dest):
    """A link's or an outline entry's page, 0-based (or None)."""
    if dest is None:
        return None
    if dest.type == Poppler.DestType.NAMED:
        dest = doc.find_dest(dest.named_dest)
        if dest is None:
            return None
    return dest.page_num - 1 if dest.page_num > 0 else None


class Page(Gtk.Widget):
    """One page: white until its picture is drawn (then that, stretched while a sharper one is drawn),
    the find matches over it."""

    def __init__(self, viewer, index):
        super().__init__()
        self.viewer, self.index = viewer, index
        self.w = self.h = 1
        self.tex = None
        self.matches = []          # find: rectangles in the page's points, top-left origin
        self.set_halign(Gtk.Align.CENTER)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.hover)
        self.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect("released", self.clicked)
        self.add_controller(click)

    def set_size(self, w, h):
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self.queue_resize()

    def do_measure(self, orientation, _for_size):
        n = self.w if orientation == Gtk.Orientation.HORIZONTAL else self.h
        return n, n, -1, -1

    def do_snapshot(self, snap):
        r = Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
        white = Gdk.RGBA()
        white.parse("#ffffff")   # the paper's own colour, whatever the theme
        snap.append_color(white, r)
        if self.tex:
            snap.append_texture(self.tex, r)
        if self.matches:
            s = self.viewer.scale
            cur = self.viewer.match_at
            for n, (x1, y1, x2, y2) in enumerate(self.matches):
                c = Gdk.RGBA()
                c.red, c.green, c.blue = rgbf("#6b8fe0" if cur == (self.index, n) else "#e0af68")
                c.alpha = .45
                snap.append_color(c, Graphene.Rect().init(x1 * s, y1 * s, (x2 - x1) * s, (y2 - y1) * s))
        line = Gdk.RGBA()
        line.red, line.green, line.blue = rgbf("#3b4261")
        line.alpha = .7
        edge = Gsk.RoundedRect()
        edge.init_from_rect(r, 0)
        snap.append_border(edge, [1, 1, 1, 1], [line] * 4)

    def link_at(self, x, y):
        s = self.viewer.scale
        px, py = x / s, y / s
        ph = self.viewer.sizes[self.index][1]
        for m in self.viewer.links(self.index):
            a = m.area   # PDF points, bottom-left origin
            if a.x1 <= px <= a.x2 and ph - a.y2 <= py <= ph - a.y1:
                return m.action
        return None

    def hover(self, _ctl, x, y):
        self.set_cursor_from_name("pointer" if self.link_at(x, y) else None)

    def clicked(self, gesture, n, x, y):
        b = gesture.get_current_button()
        if b == 3:
            self.viewer.menu(self, x, y)
        elif b == 8:
            self.viewer.step(-1)
        elif b == 9:
            self.viewer.step(1)
        elif b == 1 and n == 1:
            action = self.link_at(x, y)
            if action:
                self.viewer.follow(action)
        elif b == 1 and n == 2 and self.viewer.full:
            self.viewer.fullscreen()


class Documents(View):
    title, icon = "Documents", G_DOC
    interval = 0
    backdrop = False
    css = CSS
    hints = [("Space/PgDn", "next page"), ("+/−", "zoom"), ("w", "fit width"), ("z", "fit page"),
             ("f", "fullscreen"), ("t", "outline"), ("Ctrl+F", "find"), ("g", "go to page"), ("Right-click", "more")]

    def __init__(self):
        super().__init__()
        self.path, self.doc, self.password = None, None, None
        self.gen = 0               # bumped by every (re)load: a picture drawn for an older one is dropped
        self.sizes, self.pages = [], []
        self.scale, self.fit = 1.0, "width"   # fit: "width", "page" or None (a zoom of its own)
        self.full, self.fit_before_full = False, None
        self.cache, self.cache_size = OrderedDict(), 0   # index → (scale, texture, bytes)
        self.want, self.want_cond = [], threading.Condition()
        self.link_cache = {}
        self.restore = None        # (page, fraction down it) to scroll to once the column has its size
        self.find_text, self.found, self.match_at, self.find_job = "", {}, None, 0
        self.monitor, self.reload_timer = None, 0
        self.prompt_mode = None    # "find", "page" or "password": what the bottom bar's entry is for
        self.state = {"outline": False, "files": {}}
        try:
            with open(STATE) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        threading.Thread(target=self.worker, daemon=True).start()

    def save_state(self):
        if self.path and self.doc:
            files = self.state["files"]
            files.pop(self.path, None)
            files[self.path] = {"page": self.current_page(), "fit": self.fit, "scale": self.scale}
            while len(files) > FILES_KEPT:
                files.pop(next(iter(files)))
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, "w") as f:
                json.dump(self.state, f)
        except OSError:
            pass

    # ---- the window -----------------------------------------------------------------------------------
    def header_extra(self):
        return [button(f"{G_FULL} Fullscreen", self.fullscreen, "flat", tooltip="f / F11")]

    def build(self):
        self.column = box(True, GAP, classes=("doc-view",))
        for side in ("top", "bottom", "start", "end"):
            getattr(self.column, f"set_margin_{side}")(GAP)
        self.msg = label("No document open  ·  Ctrl+O to open one, or drop it here", "canvas-msg", xalign=0.5)
        self.msg.set_vexpand(True)
        self.column.append(self.msg)
        self.scroll = Gtk.ScrolledWindow(child=self.column, hexpand=True, vexpand=True)
        self.vadj, self.hadj = self.scroll.get_vadjustment(), self.scroll.get_hadjustment()
        self.vadj.connect("value-changed", lambda *_: self.view_moved())
        self.vadj.connect("changed", lambda *_: self.apply_restore())
        self.hadj.connect("notify::page-size", lambda *_: self.refit())
        self.vadj.connect("notify::page-size", lambda *_: self.fit == "page" and self.refit())
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.wheel)
        self.scroll.add_controller(wheel)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "pinch_from", self.scale))
        pinch.connect("scale-changed", lambda _g, k: self.zoom_to(self.pinch_from * k))
        self.scroll.add_controller(pinch)
        empty_click = Gtk.GestureClick(button=3)   # the menu from the gaps around the pages too
        empty_click.connect("pressed", lambda _g, _n, x, y: self.menu(self.scroll, x, y))
        self.scroll.add_controller(empty_click)

        self.outline_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.outline_list.connect("row-activated", lambda _l, row: self.go(row.page))
        self.outline = scrolled(self.outline_list)
        self.outline.set_size_request(260, -1)
        self.outline.set_hexpand(False)
        self.outline.add_css_class("outline-pane")

        self.prompt_label = label("", "dim")
        self.prompt_entry = Gtk.Entry(hexpand=True)
        self.prompt_entry.connect("activate", lambda *_: self.prompt_done())
        self.prompt_entry.connect("changed", lambda *_: self.prompt_mode == "find" and self.prompt_entry.get_text() == ""
                                  and self.clear_find())
        self.prompt = box(False, 10, self.prompt_label, self.prompt_entry,
                          button("Close", self.prompt_close, "flat"), classes=("prompt",))
        self.prompt.set_visible(False)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, files, _x, _y: self.open([f.get_path() for f in files.get_files()
                                                                  if f.get_path()]) or True)
        self.scroll.add_controller(drop)
        self.body = box(True, 0, box(False, 0, self.outline, self.scroll), self.prompt)
        self.panes()
        return self.body

    def panes(self):
        self.outline.set_visible(self.state["outline"] and not self.full and self.outline_list.get_first_child() is not None)

    def chrome(self, on):
        """The header and the hint line (hidden in fullscreen)."""
        outer = self.window.get_child()
        outer.get_first_child().set_visible(on)
        outer.get_last_child().set_visible(on)

    def show_msg(self, text):
        clear(self.column)
        self.pages, self.sizes = [], []
        self.msg.set_text(text)
        self.column.append(self.msg)

    # ---- the document ---------------------------------------------------------------------------------
    def open(self, paths):
        paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
        if paths:
            self.save_state()   # where the one open was left
            self.password = None
            self.load(paths[0])

    def load(self, path, keep_place=False):
        place = (self.current_page(), self.page_fraction()) if keep_place and self.doc else None
        self.path = path
        try:
            doc = open_doc(path, self.password)
        except GLib.Error as e:
            self.doc = None
            self.gen += 1
            self.forget_pages()
            if "encrypted" in e.message.lower() or "password" in e.message.lower():
                self.show_msg("\U000f033e  This document is locked")
                self.ask("password", "Password", visibility=False)
            else:
                self.show_msg(f"Can't open {os.path.basename(path)}: {e.message}")
            self.set_subtitle(path_short(path))
            self.watch(path)
            return
        self.doc = doc
        self.gen += 1
        self.forget_pages()
        self.link_cache = {}
        self.clear_find(redraw=False)
        self.sizes = [doc.get_page(i).get_size() for i in range(doc.get_n_pages())]
        clear(self.column)
        self.pages = [Page(self, i) for i in range(len(self.sizes))]
        for p in self.pages:
            self.column.append(p)
        if not self.pages:
            self.show_msg("This document has no pages")
        saved = self.state["files"].get(path, {})
        if not keep_place:
            self.fit = saved.get("fit", "width") if not self.full else "page"
            self.scale = saved.get("scale", 1.0)
            place = (min(saved.get("page", 0), max(len(self.sizes) - 1, 0)), 0.0)
        self.fill_outline()
        self.panes()
        self.watch(path)
        self.layout(place)
        if self.prompt_mode == "password":
            self.prompt_close()

    def forget_pages(self):
        self.cache.clear()
        self.cache_size = 0
        with self.want_cond:
            self.want = []

    def watch(self, path):
        """Follow the file: rewritten (a LaTeX build, a download finishing) it's read again, same place."""
        if self.monitor:
            self.monitor.cancel()
        try:
            self.monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.connect("changed", self.file_changed)
        except GLib.Error:
            self.monitor = None

    def file_changed(self, _m, _f, _other, event):
        if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.MOVED_IN):
            return
        if self.reload_timer:
            GLib.source_remove(self.reload_timer)

        def later():   # once the writer is done
            self.reload_timer = 0
            if self.path and os.path.isfile(self.path):
                self.load(self.path, keep_place=True)
            return False
        self.reload_timer = GLib.timeout_add(400, later)

    def links(self, i):
        if i not in self.link_cache:
            self.link_cache[i] = self.doc.get_page(i).get_link_mapping() if self.doc else []
        return self.link_cache[i]

    def fill_outline(self):
        clear(self.outline_list)
        if not self.doc:
            return

        def add(it, depth):
            while it:
                action = it.get_action()
                if action and action.type == Poppler.ActionType.GOTO_DEST:
                    page = dest_page(self.doc, action.goto_dest.dest)
                    if page is not None:
                        text = label(action.goto_dest.title or "", ellipsize=True)
                        text.set_margin_start(14 * depth)
                        row = Gtk.ListBoxRow(child=text)
                        row.page = page
                        self.outline_list.append(row)
                child = it.get_child()
                if child:
                    add(child, depth + 1)
                if not it.next():
                    break
        try:
            top = Poppler.IndexIter.new(self.doc)
        except TypeError:   # no outline: the constructor returns NULL
            return
        add(top, 0)

    # ---- layout: the scale, where each page is ---------------------------------------------------------
    def fit_scale(self):
        vw, vh = self.hadj.get_page_size() - 2 * GAP, self.vadj.get_page_size() - 2 * GAP
        if not self.sizes or vw <= 0:
            return self.scale
        widest = max(w for w, _h in self.sizes)
        if self.fit == "width":
            return max(vw / widest, ZOOM_MIN)
        tallest = max(h for _w, h in self.sizes)
        return max(min(vw / widest, vh / tallest), ZOOM_MIN)

    def layout(self, place=None):
        """Every page at the current scale, then the view where `place` (page, fraction) says."""
        if self.fit:
            self.scale = self.fit_scale()
        for p, (w, h) in zip(self.pages, self.sizes):
            p.set_size(max(round(w * self.scale), 1), max(round(h * self.scale), 1))
            p.queue_draw()
        self.restore = place
        self.apply_restore()
        self.update_subtitle()
        GLib.idle_add(lambda: self.view_moved() and False)

    def refit(self):
        if self.fit and self.pages and abs(self.fit_scale() - self.scale) > 1e-3:
            self.layout((self.current_page(), self.page_fraction()))

    def page_top(self, i):
        return GAP + sum(round(h * self.scale) + GAP for _w, h in self.sizes[:i])

    def apply_restore(self):
        """Scroll to the saved place once the column is as tall as the new scale makes it."""
        if self.restore is None or not self.pages:
            return
        i, frac = self.restore
        h = round(self.sizes[i][1] * self.scale)
        y = self.page_top(i) + frac * h
        if self.fit == "page" or (self.full and frac == 0):   # a whole page in view: centred
            y = self.page_top(i) - max((self.vadj.get_page_size() - h) / 2, GAP)
        y = max(y, 0)
        if self.vadj.get_upper() - self.vadj.get_page_size() + 1 >= y:
            self.restore = None
        self.vadj.set_value(y)

    def current_page(self):
        """The page at the middle of the view."""
        if not self.pages:
            return 0
        mid = self.vadj.get_value() + self.vadj.get_page_size() / 2
        y = GAP
        for i, (_w, h) in enumerate(self.sizes):
            y += round(h * self.scale) + GAP
            if y > mid:
                return i
        return len(self.sizes) - 1

    def page_fraction(self):
        """How far down the current page the view's top is (0 at the top), to keep the place on a zoom."""
        if not self.pages:
            return 0.0
        i = self.current_page()
        h = max(round(self.sizes[i][1] * self.scale), 1)
        return min(max((self.vadj.get_value() - self.page_top(i)) / h, 0.0), 1.0)

    def update_subtitle(self):
        if not self.doc:
            return
        fit = {"width": "fit width", "page": "fit page"}.get(self.fit, f"{self.scale * 100:.0f} %")
        self.set_subtitle(f"{os.path.basename(self.path)}  ·  page {self.current_page() + 1} of "
                          f"{len(self.sizes)}  ·  {fit}")

    # ---- drawing the pages (a worker thread, nearest the view first) ----------------------------------
    def view_moved(self):
        if not self.pages:
            return
        top, bottom = self.vadj.get_value(), self.vadj.get_value() + self.vadj.get_page_size()
        reach = self.vadj.get_page_size()   # a screen above and below drawn ahead
        cur, near, y = self.current_page(), [], GAP
        for i, (_w, h) in enumerate(self.sizes):
            ph = round(h * self.scale)
            if y + ph >= top - reach and y <= bottom + reach:
                near.append(i)
            y += ph + GAP
        near.sort(key=lambda i: abs(i - cur))
        ds = self.scale * self.window.get_scale_factor()
        todo = [i for i in near if i not in self.cache or abs(self.cache[i][0] - ds) > 1e-3]
        with self.want_cond:
            self.want = [(self.gen, i, ds) for i in todo]
            self.want_cond.notify()
        for i in near:   # kept: the ones in view are the last to go
            if i in self.cache:
                self.cache.move_to_end(i)
        self.update_subtitle()

    def worker(self):
        doc, doc_gen = None, -1
        while True:
            with self.want_cond:
                while not self.want:
                    self.want_cond.wait()
                gen, i, ds = self.want.pop(0)
                path, password = self.path, self.password
            if gen != self.gen:
                continue
            try:
                if doc_gen != gen:   # Poppler's documents aren't shared between threads: this one's own
                    doc, doc_gen = open_doc(path, password), gen
                page = doc.get_page(i)
                w, h = page.get_size()
                k = min(ds, (MAX_PIXELS / max(w * h, 1)) ** 0.5)
                pw, ph = max(round(w * k), 1), max(round(h * k), 1)
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, pw, ph)
                cr = cairo.Context(surf)
                cr.set_source_rgb(1, 1, 1)
                cr.paint()
                cr.scale(pw / w, ph / h)
                page.render(cr)
                surf.flush()
                tex = Gdk.MemoryTexture.new(pw, ph, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                            GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
            except (GLib.Error, cairo.Error, MemoryError):
                continue
            GLib.idle_add(self.drawn, gen, i, ds, tex, pw * ph * 4)

    def drawn(self, gen, i, ds, tex, size):
        if gen != self.gen or i >= len(self.pages):
            return False
        old = self.cache.pop(i, None)
        if old:
            self.cache_size -= old[2]
        self.cache[i] = (ds, tex, size)
        self.cache_size += size
        self.pages[i].tex = tex
        self.pages[i].queue_draw()
        while self.cache_size > CACHE_BYTES and len(self.cache) > 1:
            j, (_s, _t, n) = self.cache.popitem(last=False)
            self.cache_size -= n
            self.pages[j].tex = None
            self.pages[j].queue_draw()
        return False

    # ---- moving and zooming ---------------------------------------------------------------------------
    def go(self, i):
        if self.pages:
            self.restore = (min(max(i, 0), len(self.pages) - 1), 0.0)
            self.apply_restore()

    def step(self, d):
        """A page on / back: the next page's top (in fit-page and fullscreen, the next page centred)."""
        if not self.pages:
            return
        i = self.current_page()
        if d > 0 and self.fit != "page" and not self.full:
            nxt = self.page_top(i + 1) if i + 1 < len(self.pages) else self.vadj.get_upper()
            if nxt - self.vadj.get_value() > self.vadj.get_page_size():   # the rest of this page first
                self.vadj.set_value(self.vadj.get_value() + self.vadj.get_page_size() * 0.9)
                return
        if d < 0 and self.fit != "page" and not self.full and self.vadj.get_value() > self.page_top(i) + 1:
            self.go(i)   # back to this page's top first
            return
        self.go(i + d)

    def set_fit(self, how):
        self.fit = how
        self.layout((self.current_page(), self.page_fraction() if how == "width" else 0.0))
        self.save_state()

    def zoom_to(self, s):
        s = min(max(s, ZOOM_MIN), ZOOM_MAX)
        if abs(s - self.scale) < 1e-3:
            return
        place = (self.current_page(), self.page_fraction())
        self.fit, self.scale = None, s
        self.layout(place)

    def zoom_by(self, k):
        self.zoom_to(self.scale * k)

    def wheel(self, ctl, _dx, dy):
        if ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_by(ZOOM_STEP if dy < 0 else 1 / ZOOM_STEP)
            return True
        if self.full and self.fit == "page":   # a presentation: a page per notch
            self.step(1 if dy > 0 else -1)
            return True
        return False

    def fullscreen(self):
        self.full = not self.full
        win = self.window
        self.host.set_fullscreen(self.full)
        (win.add_css_class if self.full else win.remove_css_class)("full")
        self.chrome(not self.full)
        self.panes()
        place = (self.current_page(), 0.0 if self.full else self.page_fraction())
        if self.full:   # a page fills the screen
            self.fit_before_full, self.fit = (self.fit, self.scale), "page"
        elif self.fit_before_full:
            self.fit, self.scale = self.fit_before_full
        self.layout(place)

    def follow(self, action):
        if action.type == Poppler.ActionType.GOTO_DEST:
            page = dest_page(self.doc, action.goto_dest.dest)
            if page is not None:
                self.go(page)
        elif action.type == Poppler.ActionType.NAMED and action.named.named_dest:
            name = action.named.named_dest
            moves = {"NextPage": 1, "PrevPage": -1}
            if name in moves:
                self.step(moves[name])
            elif name in ("FirstPage", "LastPage"):
                self.go(0 if name == "FirstPage" else len(self.pages) - 1)
        elif action.type == Poppler.ActionType.URI and action.uri.uri:
            self.launch("xdg-open", action.uri.uri)
            self.say(f"opened {action.uri.uri}")

    # ---- find -----------------------------------------------------------------------------------------
    def find(self, text):
        """Every match in every page (a thread with its own copy of the document), then the first one
        from the current page on."""
        self.clear_find(redraw=True)
        self.find_text = text
        self.find_job += 1
        job, gen, path, password, start = self.find_job, self.gen, self.path, self.password, self.current_page()
        self.say("finding…", seconds=30)

        def work():
            found = {}
            try:
                doc = open_doc(path, password)
                for i in range(doc.get_n_pages()):
                    if job != self.find_job:
                        return
                    page = doc.get_page(i)
                    h = page.get_size()[1]
                    rects = page.find_text_with_options(text, Poppler.FindFlags.DEFAULT)
                    if rects:
                        found[i] = [(r.x1, h - r.y2, r.x2, h - r.y1) for r in rects]
            except GLib.Error:
                pass
            GLib.idle_add(lambda: self.found_all(job, gen, found, start) and False)
        threading.Thread(target=work, daemon=True).start()

    def found_all(self, job, gen, found, start):
        if job != self.find_job or gen != self.gen:
            return
        self.found = found
        for i, rects in found.items():
            self.pages[i].matches = rects
            self.pages[i].queue_draw()
        n = sum(len(r) for r in found.values())
        if not n:
            self.say(f"“{self.find_text}” isn't in this document", "bad")
            return
        self.say(f"{n} match{'es' if n > 1 else ''}  ·  Enter next, Shift+Enter previous")
        first = next((i for i in sorted(found) if i >= start), min(found))
        self.show_match((first, 0))

    def all_matches(self):
        return [(i, n) for i in sorted(self.found) for n in range(len(self.found[i]))]

    def next_match(self, d):
        every = self.all_matches()
        if not every:
            return
        k = every.index(self.match_at) + d if self.match_at in every else 0
        self.show_match(every[k % len(every)])

    def show_match(self, at):
        old, self.match_at = self.match_at, at
        for i in {old[0] if old else None, at[0]} - {None}:
            self.pages[i].queue_draw()
        i, n = at
        _x1, y1, _x2, y2 = self.found[i][n]
        y = self.page_top(i) + (y1 + y2) / 2 * self.scale - self.vadj.get_page_size() / 2
        self.vadj.set_value(max(y, 0))
        every = self.all_matches()
        self.say(f"match {every.index(at) + 1} of {len(every)}  ·  page {i + 1}", seconds=8)

    def clear_find(self, redraw=True):
        self.find_job += 1
        for i in self.found:
            if i < len(self.pages):
                self.pages[i].matches = []
                if redraw:
                    self.pages[i].queue_draw()
        self.found, self.match_at, self.find_text = {}, None, ""

    # ---- the bottom bar: find, go to page, password ---------------------------------------------------
    def ask(self, mode, text, value="", visibility=True):
        self.prompt_mode = mode
        self.prompt_label.set_text(text)
        self.prompt_entry.set_visibility(visibility)
        self.prompt_entry.set_text(value)
        self.prompt.set_visible(True)
        self.prompt_entry.grab_focus()
        self.prompt_entry.select_region(0, -1)

    def prompt_done(self):
        text, mode = self.prompt_entry.get_text(), self.prompt_mode
        if mode == "find":
            if not text:
                return
            if text == self.find_text and self.found:
                self.next_match(1)
            else:
                self.find(text)
        elif mode == "page":
            try:
                self.go(int(text) - 1)
            except ValueError:
                self.say("a page number", "bad")
                return
            self.prompt_close()
        elif mode == "password":
            self.password = text
            self.load(self.path)
            if not self.doc:
                self.say("wrong password", "bad")

    def prompt_close(self):
        self.prompt.set_visible(False)
        self.prompt_mode = None
        self.window.set_focus(None)

    # ---- doing things ---------------------------------------------------------------------------------
    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def copy_path(self):
        if self.path:
            self.window.get_clipboard().set(self.path)
            self.say(f"path copied: {self.path}")

    def copy_page_text(self):
        if self.doc:
            self.window.get_clipboard().set(self.doc.get_page(self.current_page()).get_text() or "")
            self.say(f"page {self.current_page() + 1}'s text copied")

    def show_in_files(self):
        if self.path:
            self.launch("python3", FILES, self.path)

    def open_with(self):
        if self.path:
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(self.path))
            launcher.set_always_ask(True)
            launcher.launch(self.window, None, None)

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open a document", modal=True)
        filt = Gtk.FileFilter(name="PDF documents")
        filt.add_mime_type("application/pdf")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                self.open([d.open_finish(res).get_path()])
            except (GLib.Error, TypeError):
                pass   # cancelled
        dialog.open(self.window, None, done)

    def toggle_outline(self):
        if self.outline_list.get_first_child() is None:
            self.say("this document has no outline")
            return
        self.state["outline"] = not self.state["outline"]
        self.save_state()
        self.panes()

    def info(self):
        if not self.doc:
            return
        d = self.doc
        bits = [d.get_title(), d.get_author(), f"{len(self.sizes)} pages",
                f"{self.sizes[0][0] / 72 * 25.4:.0f} × {self.sizes[0][1] / 72 * 25.4:.0f} mm" if self.sizes else "",
                f"PDF {d.get_pdf_version_string() or ''}".strip()]
        self.say("  ·  ".join(b for b in bits if b), seconds=10)

    # ---- the right-click menu -------------------------------------------------------------------------
    def menu(self, parent, x, y):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("docs-menu")
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)

        def tick(on):
            return "✓ " if on else "   "
        items = [("Open…", self.open_dialog, "Ctrl+O")]
        if self.doc:
            items += [None, ("Zoom in", lambda: self.zoom_by(ZOOM_STEP), "+"),
                      ("Zoom out", lambda: self.zoom_by(1 / ZOOM_STEP), "−"),
                      (tick(self.fit == "width") + "Fit width", lambda: self.set_fit("width"), "w"),
                      (tick(self.fit == "page") + "Fit page", lambda: self.set_fit("page"), "z"),
                      ("Actual size", lambda: self.zoom_to(96 / 72), "1"),
                      None, ("Exit fullscreen" if self.full else "Fullscreen", self.fullscreen, "f"),
                      (tick(self.state["outline"]) + "Outline", self.toggle_outline, "t"),
                      ("Find…", lambda: self.ask("find", "Find", self.find_text), "Ctrl+F"),
                      ("Go to page…", lambda: self.ask("page", "Page", str(self.current_page() + 1)), "g"),
                      ("Document info", self.info, "i"),
                      None, ("Copy this page's text", self.copy_page_text, "Ctrl+C"),
                      ("Copy path", self.copy_path, "Ctrl+Shift+C"), ("Show in Files", self.show_in_files, "e"),
                      ("Open with…", self.open_with, "o"), ("Reload", lambda: self.load(self.path, True), "F5")]
        col = box(True, 0)
        for it in items:
            if it is None:
                sep = Gtk.Separator()
                sep.set_margin_top(3)
                sep.set_margin_bottom(3)
                col.append(sep)
                continue
            text, fn, key = it
            row = box(False, 24, label(text), label(key, "dim"))
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
        if keyval == Gdk.KEY_Escape:
            if self.prompt.get_visible():
                self.prompt_close()
            elif self.found:
                self.clear_find()
                self.say("")
            elif self.full:
                self.fullscreen()
            else:
                self.window.close()
            return True
        if self.prompt_mode == "find" and self.typing() and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and shift:
            self.next_match(-1)
            return True
        if self.typing():
            return False
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and k == Gdk.KEY_o:
            self.open_dialog()
        elif ctrl and k == Gdk.KEY_f:
            self.ask("find", "Find", self.find_text)
        elif ctrl and shift and k == Gdk.KEY_c:
            self.copy_path()
        elif ctrl and k == Gdk.KEY_c:
            self.copy_page_text()
        elif ctrl and keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
            self.zoom_by(ZOOM_STEP)
        elif ctrl and keyval == Gdk.KEY_minus:
            self.zoom_by(1 / ZOOM_STEP)
        elif ctrl and k == Gdk.KEY_0:
            self.set_fit("width")
        elif k == Gdk.KEY_q:
            self.window.close()
        elif ctrl or not self.doc:
            return False
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_Right) and not shift or \
                (self.full and keyval == Gdk.KEY_Down) or k == Gdk.KEY_n:
            self.step(1)
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_BackSpace, Gdk.KEY_Left) or (keyval == Gdk.KEY_space and shift) \
                or (self.full and keyval == Gdk.KEY_Up) or k == Gdk.KEY_p:
            self.step(-1)
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_j):
            self.vadj.set_value(self.vadj.get_value() + 60)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_k):
            self.vadj.set_value(self.vadj.get_value() - 60)
        elif keyval == Gdk.KEY_Home:
            self.go(0)
        elif keyval == Gdk.KEY_End:
            self.go(len(self.pages) - 1)
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.zoom_by(ZOOM_STEP)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract, Gdk.KEY_underscore):
            self.zoom_by(1 / ZOOM_STEP)
        elif k in (Gdk.KEY_w, Gdk.KEY_0):
            self.set_fit("width")
        elif k == Gdk.KEY_z:
            self.set_fit("page")
        elif keyval == Gdk.KEY_1:
            self.zoom_to(96 / 72)   # a point is 1/72 in, the screen 96 px/in: the page at its printed size
        elif k == Gdk.KEY_f or keyval == Gdk.KEY_F11:
            self.fullscreen()
        elif k == Gdk.KEY_t:
            self.toggle_outline()
        elif k == Gdk.KEY_g:
            self.ask("page", "Page", str(self.current_page() + 1))
        elif k == Gdk.KEY_i:
            self.info()
        elif k == Gdk.KEY_e:
            self.show_in_files()
        elif k == Gdk.KEY_o:
            self.open_with()
        elif keyval == Gdk.KEY_F5:
            self.load(self.path, keep_place=True)
        elif keyval == Gdk.KEY_Menu:
            self.menu(self.scroll, self.scroll.get_width() / 2, self.scroll.get_height() / 2)
        else:
            return False
        return True

    def quit(self):
        self.save_state()


class DocumentsApp(PanelApp):
    """One viewer: what's opened while it runs goes to it (xdg-open, Files). Esc leaves fullscreen first."""

    def __init__(self, view):
        super().__init__(view, "panels.documents", (1100, 680), toggle=True)
        self.set_flags(Gio.ApplicationFlags.HANDLES_OPEN)

    def do_activate(self):
        if self.win:   # run again with nothing to open: just bring it up
            self.win.present()
            return
        super().do_activate()

    def do_open(self, files, _n, _hint):
        self.activate()
        paths = [f.get_path() for f in files if f.get_path()]
        GLib.idle_add(lambda: self.view.open(paths) and False)

    def do_shutdown(self):
        self.view.quit()
        PanelApp.do_shutdown(self)

    def _on_key(self, ctrl, keyval, code, state):
        if keyval == Gdk.KEY_Escape:   # the find bar's Esc too: it closes the bar, not the window
            return self.view.key(keyval, state)
        return super()._on_key(ctrl, keyval, code, state)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    DocumentsApp(Documents()).run(sys.argv)


if __name__ == "__main__":
    main()
