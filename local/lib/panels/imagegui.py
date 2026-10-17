#!/usr/bin/env python3
# imagegui.py — Images, the image viewer (the default app for pictures: config/mimeapps.list).
#   imagegui.py <image>…   shows it, and the pictures next to it (←/→); several files: just those
#   imagegui.py <folder>   the pictures in that folder
# The running viewer takes what's opened next. Zoom (keys, wheel at the pointer, pinch, double-click for
# 100 %), pan (drag, arrows), rotate / flip (Ctrl+S writes them to the file), fullscreen, a slideshow,
# a thumbnail strip, an info pane (file, image, camera from EXIF), animated GIF / WebP, rename, trash,
# copy, set as wallpaper, open in Files or another app; everything is also on the right-click menu.
# The folder is watched (a new screenshot shows up). Remembered (~/.local/state/images): the panes,
# the background, the sort order, the slideshow's pace.
import gc, hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, Gio, GLib, Graphene, Gtk, Pango, PanelApp, View, box, button, clear, collect, label,
                    recolor, rule_heading, scrolled)
import gi
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
from gi.repository import GdkPixbuf, Gsk

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or f"{HOME}/.local/state", "images", "state.json")
WALLPAPER = f"{HOME}/.config/hypr/scripts/wallpaper.sh"
FILES = f"{HOME}/.local/lib/panels/filesgui.py"
EXTS = {"png", "jpg", "jpeg", "jpe", "jfif", "gif", "webp", "svg", "svgz", "bmp", "ico", "avif", "heic", "heif",
        "jxl", "tif", "tiff", "tga", "qoi", "pnm", "ppm", "pgm", "pbm", "xpm", "icns", "dds", "exr"}
ANIMATED = {"gif", "webp", "png"}   # may have frames (APNG too): tried as an animation first
VECTOR = {"svg", "svgz"}
CACHE_BYTES = 400 << 20      # decoded pictures kept for going back and forth (and the next one, read ahead)
THUMBS_KEPT = 400
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.02, 32.0, 1.25
BACKGROUNDS = ("panel", "checker", "light")
SORTS = (("name", "Name"), ("date", "Date modified"), ("size", "Size"))
G_IMAGE, G_FULL = "\uf1c5", "\U000f0293"
CSS = """
.canvas-msg { color: #565f89; }
.strip { border-top: 1px solid alpha(#3b4261, .7); }
.strip listview { background: transparent; }
.strip listview > row { padding: 6px 3px; background: transparent; }
.strip listview > row picture { border: 2px solid transparent; opacity: .55; }
.strip listview > row:hover picture { opacity: .85; }
.strip listview > row:selected picture { border-color: #6b8fe0; opacity: 1; }
.info-pane { border-left: 1px solid alpha(#3b4261, .7); padding: 14px 18px; }
.info-key { color: #565f89; min-width: 92px; }
.info-val { color: #c0caf5; }
.prompt { border-top: 1px solid alpha(#3b4261, .7); padding: 8px 20px; }
.menu-btn { padding: 4px 14px; }
popover.images-menu > contents { padding: 4px; }
window.panel.full { background: #16161e; }
"""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ext(path):
    return path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""


def natural(name):
    """Sort key: numbers by value (shot-2 before shot-10), case ignored."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name.lower())]


def rgba(colour, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(recolor(colour))
    c.alpha = alpha
    return c


def texture(pb):
    """A pixbuf as a texture (any thread: textures are immutable)."""
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())


def decode(path):
    """("still", texture) or ("anim", PixbufAnimation); in a thread. EXIF orientation applied; a vector
    drawing rendered big enough to zoom into."""
    e = ext(path)
    if e in ANIMATED:
        anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
        if not anim.is_static_image():
            return "anim", anim
    if e in VECTOR:
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        k = 2048 / max(w, h, 1)   # at least 2048 px on the long side: sharp when zoomed in
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, round(w * k), round(h * k), True) if k > 1 and w and h \
            else GdkPixbuf.Pixbuf.new_from_file(path)
    else:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
    pb = pb.apply_embedded_orientation() or pb
    return "still", texture(pb)


def cached_thumb(path, mtime):
    """The freedesktop thumbnail another app already made (~/.cache/thumbnails), if it's not stale."""
    digest = hashlib.md5(Gio.File.new_for_path(path).get_uri().encode()).hexdigest()
    base = os.environ.get("XDG_CACHE_HOME") or f"{HOME}/.cache"
    for size in ("large", "normal"):
        t = f"{base}/thumbnails/{size}/{digest}.png"
        try:
            if os.path.getmtime(t) >= mtime:
                return GdkPixbuf.Pixbuf.new_from_file(t)
        except (OSError, GLib.Error):
            pass
    return None


def exif(path):
    """{tag: value} from the picture's EXIF (ImageMagick reads it), or {}."""
    try:
        out = subprocess.run(["magick", "identify", "-format", "%[EXIF:*]", path + "[0]"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    tags = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if v.strip():
            tags[k.removeprefix("exif:")] = v.strip()
    return tags


def ratio(v):
    """An EXIF rational ("1/250", "28/10") as a number."""
    try:
        a, _, b = v.partition("/")
        return float(a) / float(b) if b else float(a)
    except (ValueError, ZeroDivisionError):
        return None


class Canvas(Gtk.Widget):
    """The picture: fitted to the window or at a zoom, panned, rotated and flipped. Positions are in the
    shown picture's pixels (after rotate / flip): (cx, cy) is the point at the middle of the widget."""

    def __init__(self, viewer):
        super().__init__(hexpand=True, vexpand=True, focusable=False, overflow=Gtk.Overflow.HIDDEN)
        self.viewer = viewer
        self.paintable, self.iw, self.ih = None, 0, 0
        self.rot, self.flip_h, self.flip_v = 0, False, False
        self.fit, self.s, self.cx, self.cy = True, 1.0, 0.0, 0.0
        self.bg = "panel"
        self.msg = label("", "canvas-msg", xalign=0.5)
        self.msg.set_parent(self)
        self.pointer = None

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        drag.connect("drag-end", lambda *_: self.set_cursor_from_name("grab" if self.pannable() else None))
        self.add_controller(drag)
        clicks = Gtk.GestureClick(button=0)
        clicks.connect("pressed", self.pressed)
        self.add_controller(clicks)
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self.scrolled)
        self.add_controller(wheel)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "pointer", (x, y)))
        motion.connect("leave", lambda *_: setattr(self, "pointer", None))
        self.add_controller(motion)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "pinch_from", self.s))
        pinch.connect("scale-changed", self.pinched)
        self.add_controller(pinch)

    # -- the picture --
    def show(self, paintable, w, h, keep_view=False):
        self.paintable, self.iw, self.ih = paintable, w, h
        if not keep_view:
            self.rot, self.flip_h, self.flip_v, self.fit = 0, False, False, True
        self.msg.set_visible(False)
        self.update()

    def say(self, text):
        self.paintable, self.iw, self.ih = None, 0, 0
        self.msg.set_text(text)
        self.msg.set_visible(True)
        self.update()

    def shown_size(self):
        return (self.ih, self.iw) if self.rot % 180 else (self.iw, self.ih)

    def fit_scale(self):
        dw, dh = self.shown_size()
        W, H = self.get_width(), self.get_height()
        if not dw or not dh or W <= 1 or H <= 1:
            return 1.0
        return min(W / dw, H / dh, 1.0)   # fitted, never blown up past 100 %

    def update(self):
        if self.fit:
            self.s = self.fit_scale()
            dw, dh = self.shown_size()
            self.cx, self.cy = dw / 2, dh / 2
        self.clamp()
        self.set_cursor_from_name("grab" if self.pannable() else None)
        self.queue_draw()
        self.viewer.view_changed()

    def clamp(self):
        """Keep the picture on screen: centred on an axis where it's smaller than the window."""
        dw, dh = self.shown_size()
        for axis, (size, room) in enumerate(((dw, self.get_width()), (dh, self.get_height()))):
            half = room / 2 / self.s
            c = (self.cx, self.cy)[axis]
            c = size / 2 if size * self.s <= room else min(max(c, half), size - half)
            if axis:
                self.cy = c
            else:
                self.cx = c

    def pannable(self):
        dw, dh = self.shown_size()
        return self.paintable is not None and (dw * self.s > self.get_width() + 1 or dh * self.s > self.get_height() + 1)

    def zoom_to(self, s, at=None):
        """Zoom to s with the picture's point under `at` (widget px; default: the pointer, else the middle) kept there."""
        if not self.paintable:
            return
        W, H = self.get_width(), self.get_height()
        ax, ay = at or self.pointer or (W / 2, H / 2)
        px, py = self.cx + (ax - W / 2) / self.s, self.cy + (ay - H / 2) / self.s
        self.s = min(max(s, ZOOM_MIN), ZOOM_MAX)
        self.cx, self.cy = px - (ax - W / 2) / self.s, py - (ay - H / 2) / self.s
        self.fit = False
        self.update()

    def zoom_by(self, factor, at=None):
        self.zoom_to(self.s * factor, at)

    def set_fit(self):
        self.fit = True
        self.update()

    def rotate(self, deg):
        self.rot = (self.rot + deg) % 360
        self.fit = True
        self.update()

    def flip(self, horizontal):
        # mirrored on the screen's axis: with the picture turned a quarter, that's the picture's other axis
        if horizontal == (self.rot % 180 == 0):
            self.flip_h = not self.flip_h
        else:
            self.flip_v = not self.flip_v
        self.update()

    def pan(self, dx, dy):
        """Move the view by a fraction of the window."""
        self.cx += dx * self.get_width() / self.s
        self.cy += dy * self.get_height() / self.s
        self.update()

    def edited(self):
        return bool(self.rot or self.flip_h or self.flip_v)

    # -- input --
    def drag_begin(self, _g, _x, _y):
        self.drag_from = (self.cx, self.cy)
        if self.pannable():
            self.set_cursor_from_name("grabbing")

    def drag_update(self, _g, dx, dy):
        self.cx, self.cy = self.drag_from[0] - dx / self.s, self.drag_from[1] - dy / self.s
        self.clamp()
        self.queue_draw()

    def pressed(self, gesture, n, x, y):
        b = gesture.get_current_button()
        if b == 3:
            self.viewer.menu(self, x, y)
        elif b == 8:
            self.viewer.step(-1)
        elif b == 9:
            self.viewer.step(1)
        elif b == 1 and n == 2:   # double-click: 100 % at that point, or back to fitted
            if self.fit and self.fit_scale() < 1:
                self.zoom_to(1.0, (x, y))
            else:
                self.set_fit()

    def scrolled(self, ctl, _dx, dy):
        # a wheel notch is one zoom step; a touchpad's small deltas zoom smoothly
        step = ZOOM_STEP if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 1.01
        self.zoom_by(step ** -dy)
        return True

    def pinched(self, gesture, scale):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self.pinch_from * scale, (x, y) if ok else None)

    # -- drawing --
    def do_size_allocate(self, w, h, baseline):
        self.msg.allocate(w, h, baseline, None)
        child = self.get_first_child()
        while child is not None:   # the right-click menu: a popover's parent has to place it
            if isinstance(child, Gtk.Popover):
                child.present()
            child = child.get_next_sibling()
        if self.fit:
            self.s = self.fit_scale()
        self.clamp()
        self.viewer.view_changed()

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snap):
        W, H = self.get_width(), self.get_height()
        if self.paintable is not None and self.iw:
            s = self.s
            dw, dh = self.shown_size()
            x0, y0 = W / 2 - self.cx * s, H / 2 - self.cy * s
            area = Graphene.Rect().init(x0, y0, dw * s, dh * s)
            if self.bg == "checker":   # transparent parts as a grey checkerboard
                tile = Graphene.Rect().init(0, 0, 16, 16)
                snap.push_repeat(area, tile)
                snap.append_color(rgba("#3b4261"), tile)
                snap.append_color(rgba("#565f89"), Graphene.Rect().init(0, 0, 8, 8))
                snap.append_color(rgba("#565f89"), Graphene.Rect().init(8, 8, 8, 8))
                snap.pop()
            elif self.bg == "light":
                snap.append_color(rgba("#c0caf5"), area)
            snap.save()
            snap.translate(Graphene.Point().init(x0 + dw * s / 2, y0 + dh * s / 2))
            snap.rotate(self.rot)
            snap.scale(-1 if self.flip_h else 1, -1 if self.flip_v else 1)
            rect = Graphene.Rect().init(-self.iw * s / 2, -self.ih * s / 2, self.iw * s, self.ih * s)
            # mipmapped when shrunk (no shimmer), sharp pixels from 200 % (pixel art, peeping)
            filt = (Gsk.ScalingFilter.TRILINEAR if s < 1 else Gsk.ScalingFilter.NEAREST if s >= 2
                    else Gsk.ScalingFilter.LINEAR)
            snap.append_scaled_texture(self.paintable, filt, rect)
            snap.restore()
        self.snapshot_child(self.msg, snap)


class Images(View):
    title, icon = "Images", G_IMAGE
    interval = 0
    backdrop = False
    css = CSS
    hints = [("\u2190/\u2192", "previous / next"), ("+/\u2212", "zoom"), ("0", "fit"), ("r", "rotate"),
             ("f", "fullscreen"), ("i", "info"), ("t", "thumbnails"), ("s", "slideshow"), ("Del", "trash"),
             ("Right-click", "more")]

    def __init__(self):
        super().__init__()
        self.files, self.index = [], -1
        self.cache, self.cache_size = OrderedDict(), 0
        self.anim_timer = self.slide_timer = self.rescan_timer = 0
        self.anim_iter, self.anim_paused = None, False
        self.monitor, self.watched = None, None
        self.only = None          # opened as a set of files (not a folder): just those
        self.full = False
        self.thumbs, self.thumb_pics = OrderedDict(), {}
        self.thumb_want, self.thumb_cond = [], threading.Condition()
        self.exif_cache = {}
        self.state = {"info": False, "thumbs": True, "bg": "panel", "sort": "name", "slide": 4}
        try:
            with open(STATE) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        threading.Thread(target=self.thumb_worker, daemon=True).start()

    def save_state(self):
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
        self.canvas = Canvas(self)
        self.canvas.bg = self.state["bg"] if self.state["bg"] in BACKGROUNDS else "panel"

        self.store = Gio.ListStore(item_type=Gtk.StringObject)
        self.strip_sel = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.thumb_setup)
        factory.connect("bind", self.thumb_bind)
        factory.connect("unbind", self.thumb_unbind)
        # a click selects (single_click_activate would select on hover, moving the blue frame off the current one)
        self.strip_list = Gtk.ListView(model=self.strip_sel, factory=factory, orientation=Gtk.Orientation.HORIZONTAL,
                                       can_focus=False)
        self.strip_sel.connect("notify::selected", lambda sel, _p: sel.get_selected() != Gtk.INVALID_LIST_POSITION
                               and self.go(sel.get_selected()))
        self.strip = Gtk.ScrolledWindow(child=self.strip_list, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, min_content_height=84)
        self.strip.add_css_class("strip")
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.strip_wheel)
        self.strip.add_controller(wheel)

        self.info_box = box(True, 0)
        self.info = scrolled(self.info_box)
        self.info.set_size_request(280, -1)
        self.info.set_hexpand(False)
        self.info.add_css_class("info-pane")

        self.prompt_entry = Gtk.Entry(hexpand=True)
        self.prompt_entry.connect("activate", lambda *_: self.rename_done())
        self.prompt = box(False, 10, label("Rename to", "dim"), self.prompt_entry,
                          button("Rename", self.rename_done, "primary"), button("Cancel", self.prompt_close, "flat"),
                          classes=("prompt",))
        self.prompt.set_visible(False)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, files, _x, _y: self.open([f.get_path() for f in files.get_files()
                                                                  if f.get_path()]) or True)
        self.canvas.add_controller(drop)

        self.body = box(True, 0, box(False, 0, self.canvas, self.info), self.strip, self.prompt)
        self.panes()
        self.canvas.say("No picture open  \u00b7  Ctrl+O to open one, or drop it here")
        return self.body

    def panes(self):
        self.info.set_visible(self.state["info"] and not self.full)
        self.strip.set_visible(self.state["thumbs"] and not self.full and len(self.files) > 1)
        if self.state["info"]:
            self.fill_info()

    def chrome(self, on):
        """The header and the hint line (hidden in fullscreen)."""
        outer = self.window.get_child()
        head, foot = outer.get_first_child(), outer.get_last_child()
        head.set_visible(on)
        foot.set_visible(on)

    # ---- which pictures -------------------------------------------------------------------------------
    def open(self, paths):
        """Show these: one picture (and its folder's others), a folder, or exactly the files given."""
        paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
        if not paths:
            return
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.only, folder, first = None, paths[0], None
        elif len(paths) == 1:
            self.only, folder, first = None, os.path.dirname(paths[0]), paths[0]
        else:
            self.only, folder, first = [p for p in paths if os.path.isfile(p)], None, paths[0]
        self.watch(folder)
        self.rescan(first)

    def listing(self):
        if self.only is not None:
            files = [p for p in self.only if os.path.exists(p)]
        else:
            try:
                names = os.listdir(self.watched)
            except OSError:
                names = []
            files = [os.path.join(self.watched, n) for n in names if ext(n) in EXTS and not n.startswith(".")
                     and os.path.isfile(os.path.join(self.watched, n))]
        how = self.state["sort"]
        try:
            if how == "date":
                files.sort(key=lambda p: -os.path.getmtime(p))
            elif how == "size":
                files.sort(key=lambda p: -os.path.getsize(p))
            else:
                files.sort(key=lambda p: natural(os.path.basename(p)))
        except OSError:   # one vanished while sorting: the next rescan has it right
            pass
        return files

    def rescan(self, show=None):
        """Read the list again, staying on `show` (default: the current picture, else the same place)."""
        cur = show or (self.files[self.index] if 0 <= self.index < len(self.files) else None)
        files = self.listing()
        if show and show not in files and os.path.isfile(show):   # a hidden file, an odd extension: shown anyway
            files.insert(0, show)
        old_index = self.index
        self.files = files
        self.store.splice(0, self.store.get_n_items(), [Gtk.StringObject.new(p) for p in files])
        if not files:
            self.index = -1
            self.stop_anim()
            self.canvas.say("No pictures here")
            self.view_changed()
            self.panes()
            return
        i = files.index(cur) if cur in files else min(max(old_index, 0), len(files) - 1)
        self.panes()
        self.go(i, force=cur not in files or show is not None)

    def watch(self, folder):
        """Follow the folder (pictures added, removed, renamed by other apps): inotify, no polling."""
        if folder == self.watched and self.monitor:
            return
        if self.monitor:
            self.monitor.cancel()
        self.watched, self.monitor = folder, None
        if folder:
            try:
                self.monitor = Gio.File.new_for_path(folder).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
                self.monitor.connect("changed", self.folder_changed)
            except GLib.Error:
                pass

    def folder_changed(self, _m, f, _other, event):
        if event == Gio.FileMonitorEvent.CHANGES_DONE_HINT and f.get_path() in self.files:
            self.forget(f.get_path())   # edited by another app: decode it again
            if self.current() == f.get_path():
                self.go(self.index, force=True)
        if event in (Gio.FileMonitorEvent.ATTRIBUTE_CHANGED, Gio.FileMonitorEvent.CHANGED):
            return
        if self.rescan_timer:   # one rescan for a burst (a copy of many pictures)
            GLib.source_remove(self.rescan_timer)

        def later():
            self.rescan_timer = 0
            self.rescan()
            return False
        self.rescan_timer = GLib.timeout_add(300, later)

    def current(self):
        return self.files[self.index] if 0 <= self.index < len(self.files) else None

    # ---- showing one ----------------------------------------------------------------------------------
    def step(self, d):
        if self.files:
            self.go((self.index + d) % len(self.files))

    def go(self, i, force=False):
        if not self.files or not 0 <= i < len(self.files):
            return
        if i == self.index and not force and self.canvas.paintable is not None:
            return
        self.index = i
        path = self.files[i]
        self.strip_sel.set_selected(i)
        if self.strip.get_visible():
            self.strip_list.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
        self.stop_anim()
        hit = self.cache.get(self.cache_key(path))
        if hit:
            self.cache.move_to_end(self.cache_key(path))
            self.present(path, hit)
        else:
            self.canvas.say("")   # blank while it decodes (a big picture takes a moment)
            self.view_changed()
            self.load(path, show=True)
        self.read_ahead()
        if self.state["info"]:
            self.fill_info()

    def cache_key(self, path):
        try:
            st = os.stat(path)
            return path, st.st_mtime_ns, st.st_size
        except OSError:
            return path, 0, 0

    def load(self, path, show):
        key = self.cache_key(path)

        def work():
            try:
                got = decode(path)
            except GLib.Error as e:
                got = ("error", e.message)
            GLib.idle_add(lambda: self.loaded(path, key, got, show) and False)
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, path, key, got, show):
        if got[0] != "error":
            self.remember(key, got)
        if show and self.current() == path:
            self.present(path, got)

    def remember(self, key, got):
        if key in self.cache:
            return
        self.cache[key] = got
        self.cache_size += self.weight(got)
        while self.cache_size > CACHE_BYTES and len(self.cache) > 1:
            _k, old = self.cache.popitem(last=False)
            self.cache_size -= self.weight(old)

    def forget(self, path):
        for k in [k for k in self.cache if k[0] == path]:
            self.cache_size -= self.weight(self.cache.pop(k))

    @staticmethod
    def weight(got):
        kind, thing = got
        if kind == "anim":
            return thing.get_width() * thing.get_height() * 4 * 4   # a few frames' worth
        return thing.get_width() * thing.get_height() * 4

    def read_ahead(self):
        """Decode the next (and previous) picture now, so the arrow keys show them at once."""
        for d in (1, -1):
            if len(self.files) > 1:
                p = self.files[(self.index + d) % len(self.files)]
                if self.cache_key(p) not in self.cache:
                    self.load(p, show=False)

    def present(self, path, got):
        kind, thing = got
        if kind == "error":
            self.canvas.say(f"Can't show {os.path.basename(path)}: {thing}")
        elif kind == "anim":
            self.anim_iter = thing.get_iter(None)
            self.canvas.show(texture(self.anim_iter.get_pixbuf()), thing.get_width(), thing.get_height())
            self.anim_paused = False
            self.next_frame()
        else:
            self.canvas.show(thing, thing.get_width(), thing.get_height())
        self.window.set_title(f"{os.path.basename(path)} \u2014 Images")
        if self.state["info"]:
            self.fill_info()

    def next_frame(self):
        delay = self.anim_iter.get_delay_time()
        if delay < 0 or self.anim_paused:   # the last frame of an animation that doesn't loop
            self.anim_timer = 0
            return False

        def tick():
            self.anim_iter.advance(None)
            c = self.canvas
            c.show(texture(self.anim_iter.get_pixbuf()), c.iw, c.ih, keep_view=True)
            self.next_frame()
            return False
        self.anim_timer = GLib.timeout_add(max(delay, 20), tick)
        return False

    def stop_anim(self):
        if self.anim_timer:
            GLib.source_remove(self.anim_timer)
        self.anim_timer, self.anim_iter = 0, None

    def toggle_anim(self):
        if not self.anim_iter:
            return
        self.anim_paused = not self.anim_paused
        if self.anim_paused and self.anim_timer:
            GLib.source_remove(self.anim_timer)
            self.anim_timer = 0
        elif not self.anim_paused:
            self.next_frame()
        self.say("animation paused" if self.anim_paused else "animation playing")

    def view_changed(self):
        """The header's line: name · n / total · size · zoom (· slideshow)."""
        if not getattr(self, "host", None) or not self.host.win:
            return
        path, c = self.current(), self.canvas
        if not path:
            self.set_subtitle("")
            return
        parts = [os.path.basename(path), f"{self.index + 1} / {len(self.files)}"]
        if c.paintable is not None:
            parts += [f"{c.iw} \u00d7 {c.ih}", f"{c.s * 100:.0f} %" + (" fit" if c.fit else "")]
        if c.edited():
            parts.append("rotated (Ctrl+S saves)")
        if self.slide_timer:
            parts.append(f"slideshow {self.state['slide']} s")
        self.set_subtitle("  \u00b7  ".join(parts))

    # ---- the info pane --------------------------------------------------------------------------------
    def fill_info(self):
        clear(self.info_box)
        path = self.current()
        if not path:
            return
        try:
            st = os.stat(path)
        except OSError:
            return
        c = self.canvas
        rows = [("File", None), ("Name", os.path.basename(path)), ("Folder", path_short(os.path.dirname(path))),
                ("Size", human(st.st_size)), ("Modified", time.strftime("%d %b %Y  %H:%M", time.localtime(st.st_mtime))),
                ("Image", None), ("Type", ext(path).upper() + (" (animated)" if self.anim_iter else ""))]
        if c.iw:
            rows += [("Pixels", f"{c.iw} \u00d7 {c.ih}"), ("Megapixels", f"{c.iw * c.ih / 1e6:.1f}")]
        for title, value in rows:
            if value is None:
                self.info_box.append(rule_heading(title, spaced=title != "File"))
            else:
                self.info_row(title, value)
        tags = self.exif_cache.get(path)
        if tags is None:   # read once, in a thread; the pane fills in when it's there
            def work():
                got = exif(path)

                def done():
                    self.exif_cache[path] = got
                    if self.current() == path and self.state["info"]:
                        self.fill_info()
                    return False
                GLib.idle_add(done)
            self.exif_cache[path] = {}   # not asked twice while it's being read
            threading.Thread(target=work, daemon=True).start()
            return
        cam = camera_rows(tags)
        if cam:
            self.info_box.append(rule_heading("Camera", spaced=True))
            for title, value in cam:
                self.info_row(title, value)

    def info_row(self, title, value):
        v = label(value, "info-val", wrap=True)
        v.set_wrap_mode(Pango.WrapMode.WORD_CHAR)   # a long path breaks anywhere, not one word per line
        v.set_selectable(True)
        v.set_can_focus(False)
        v.set_hexpand(True)
        self.info_box.append(box(False, 8, label(title, "info-key"), v, classes=("setting",)))

    # ---- the thumbnail strip --------------------------------------------------------------------------
    def thumb_setup(self, _f, item):
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        pic.set_size_request(96, 66)
        item.set_child(pic)

    def thumb_bind(self, _f, item):
        pic, path = item.get_child(), item.get_item().get_string()
        pic.path = path
        self.thumb_pics.setdefault(path, set()).add(pic)
        tex = self.thumbs.get(path)
        pic.set_paintable(tex)
        if tex is None:
            with self.thumb_cond:
                self.thumb_want.append(path)
                self.thumb_cond.notify()

    def thumb_unbind(self, _f, item):
        pic = item.get_child()
        path = getattr(pic, "path", None)
        self.thumb_pics.get(path, set()).discard(pic)
        pic.set_paintable(None)
        with self.thumb_cond:
            if path in self.thumb_want:
                self.thumb_want.remove(path)

    def thumb_worker(self):
        """One thread making thumbnails, newest request first (what's in view now)."""
        while True:
            with self.thumb_cond:
                while not self.thumb_want:
                    self.thumb_cond.wait()
                path = self.thumb_want.pop()
            if path in self.thumbs:
                continue
            try:
                pb = cached_thumb(path, os.path.getmtime(path)) or \
                    GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 192, 132, True)
                tex = texture(pb.apply_embedded_orientation() or pb)
            except (GLib.Error, OSError):
                continue
            GLib.idle_add(lambda p=path, t=tex: self.thumb_done(p, t) and False)

    def thumb_done(self, path, tex):
        self.thumbs[path] = tex
        while len(self.thumbs) > THUMBS_KEPT:
            self.thumbs.popitem(last=False)
        for pic in self.thumb_pics.get(path, ()):
            pic.set_paintable(tex)

    def strip_wheel(self, ctl, _dx, dy):
        adj = self.strip.get_hadjustment()
        step = 108 if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 1
        adj.set_value(adj.get_value() + dy * step)
        return True

    # ---- doing things ---------------------------------------------------------------------------------
    def toggle(self, what):
        self.state[what] = not self.state[what]
        self.save_state()
        self.panes()
        if what == "thumbs" and self.strip.get_visible() and self.index >= 0:
            GLib.idle_add(lambda: self.strip_list.scroll_to(self.index, Gtk.ListScrollFlags.NONE, None) and False)

    def cycle_bg(self):
        c = self.canvas
        c.bg = BACKGROUNDS[(BACKGROUNDS.index(c.bg) + 1) % len(BACKGROUNDS)]
        self.state["bg"] = c.bg
        self.save_state()
        c.queue_draw()
        self.say(f"background: {c.bg}")

    def set_sort(self, how):
        self.state["sort"] = how
        self.save_state()
        self.rescan()
        self.say(f"sorted by {dict(SORTS)[how].lower()}")

    def fullscreen(self):
        self.full = not self.full
        win = self.window
        self.host.set_fullscreen(self.full)
        (win.add_css_class if self.full else win.remove_css_class)("full")
        self.chrome(not self.full)
        self.panes()

    def slideshow(self, pace=None):
        if pace:
            self.state["slide"] = pace
            self.save_state()
        if self.slide_timer:
            GLib.source_remove(self.slide_timer)
            self.slide_timer = 0
            if not pace:
                self.say("slideshow stopped")
                self.view_changed()
                return
        if len(self.files) < 2:
            return
        self.slide_timer = GLib.timeout_add_seconds(self.state["slide"], lambda: self.step(1) or True)
        self.say(f"slideshow: every {self.state['slide']} s  \u00b7  s stops it")
        self.view_changed()

    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def copy_image(self):
        c = self.canvas
        if c.paintable is not None:
            self.window.get_clipboard().set_texture(c.paintable)
            self.say("picture copied" + (" (as saved, not rotated)" if c.edited() else ""))

    def copy_path(self):
        if self.current():
            self.window.get_clipboard().set(self.current())
            self.say(f"path copied: {self.current()}")

    def wallpaper(self):
        if self.current():
            self.launch(WALLPAPER, self.current())
            self.say("set as the wallpaper")

    def show_in_files(self):
        if self.current():
            self.launch("python3", FILES, self.current())

    def open_with(self):
        if self.current():
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(self.current()))
            launcher.set_always_ask(True)
            launcher.launch(self.window, None, None)

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open a picture", modal=True)
        filt = Gtk.FileFilter(name="Pictures")
        filt.add_pixbuf_formats()
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        if self.watched:
            dialog.set_initial_folder(Gio.File.new_for_path(self.watched))

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return   # cancelled
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.open([p for p in paths if p])
        dialog.open_multiple(self.window, None, done)

    def trash(self):
        path = self.current()
        if not path:
            return
        try:
            Gio.File.new_for_path(path).trash(None)
        except GLib.Error as e:
            self.say(f"can't move it to the trash: {e.message}", "bad")
            return
        self.forget(path)
        if self.only is not None:
            self.only.remove(path)
        self.say(f"{os.path.basename(path)} moved to the trash")
        self.rescan()

    def rename(self):
        path = self.current()
        if not path:
            return
        name = os.path.basename(path)
        self.prompt.set_visible(True)
        self.prompt_entry.set_text(name)
        self.prompt_entry.grab_focus()
        self.prompt_entry.select_region(0, name.rfind(".") if "." in name[1:] else len(name))

    def rename_done(self):
        path, new = self.current(), self.prompt_entry.get_text().strip()
        if not path or not new or new == os.path.basename(path):
            self.prompt_close()
            return
        if "/" in new:
            self.say("a name can't have a /", "bad")
            return
        target = os.path.join(os.path.dirname(path), new)
        if os.path.exists(target):
            self.say(f"{new} already exists", "bad")
            return
        try:
            os.rename(path, target)
        except OSError as e:
            self.say(f"couldn't rename: {e.strerror}", "bad")
            return
        self.forget(path)
        if self.only is not None:
            self.only[self.only.index(path)] = target
        self.prompt_close()
        self.rescan(target)
        self.say(f"renamed to {new}")

    def prompt_close(self):
        self.prompt.set_visible(False)
        self.window.set_focus(None)

    def save_edit(self):
        """Write the rotation / flip into the file (ImageMagick; JPEG at its own quality)."""
        path, c = self.current(), self.canvas
        if not path or not c.edited():
            self.say("nothing to save: rotate (r) or flip (h / v) first")
            return
        if self.anim_iter:
            self.say("animations are shown turned but not saved", "bad")
            return
        ops = ["-auto-orient"] + (["-flop"] if c.flip_h else []) + (["-flip"] if c.flip_v else []) + \
              (["-rotate", str(c.rot)] if c.rot else [])
        self.say("saving…")

        def work():
            fd, tmp = tempfile.mkstemp(prefix=".images-", suffix="." + ext(path), dir=os.path.dirname(path))
            os.close(fd)
            try:
                r = subprocess.run(["magick", path, *ops, tmp], capture_output=True, text=True, timeout=120)
                if r.returncode:
                    raise OSError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "magick failed")
                shutil.copyfile(tmp, path)   # into the file itself: a link to it stays a link
                err = None
            except (OSError, subprocess.TimeoutExpired) as e:
                err = str(e)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            GLib.idle_add(lambda: self.saved(path, err) and False)
        threading.Thread(target=work, daemon=True).start()

    def saved(self, path, err):
        if err:
            self.say(f"couldn't save: {err}", "bad")
            return
        self.forget(path)
        self.thumbs.pop(path, None)
        if self.current() == path:
            self.go(self.index, force=True)
        self.say("saved")

    # ---- the right-click menu -------------------------------------------------------------------------
    def menu(self, parent, x, y):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("images-menu")
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        c, have = self.canvas, self.current() is not None
        def tick(on):
            return "\u2713 " if on else "   "
        items = [("Open…", self.open_dialog, "Ctrl+O")]
        if have:
            items += [None, ("Zoom in", lambda: c.zoom_by(ZOOM_STEP), "+"), ("Zoom out", lambda: c.zoom_by(1 / ZOOM_STEP), "\u2212"),
                      ("Fit to window", c.set_fit, "0"), ("Actual size", lambda: c.zoom_to(1.0), "1"),
                      None, ("Rotate right", lambda: c.rotate(90), "r"), ("Rotate left", lambda: c.rotate(-90), "Shift+R"),
                      ("Flip horizontally", lambda: c.flip(True), "h"), ("Flip vertically", lambda: c.flip(False), "v")]
            if c.edited():
                items.append(("Save rotation", self.save_edit, "Ctrl+S"))
            if self.anim_iter:
                items.append(("Pause animation" if not self.anim_paused else "Play animation", self.toggle_anim, "p"))
            items += [None, ("Exit fullscreen" if self.full else "Fullscreen", self.fullscreen, "f"),
                      ("Stop slideshow" if self.slide_timer else "Slideshow", self.slideshow, "s"),
                      (tick(self.state["info"]) + "Info", lambda: self.toggle("info"), "i"),
                      (tick(self.state["thumbs"]) + "Thumbnails", lambda: self.toggle("thumbs"), "t"),
                      (f"Background: {c.bg}", self.cycle_bg, "b"), None]
            items += [(tick(self.state["sort"] == how) + f"Sort by {text.lower()}", lambda how=how: self.set_sort(how), "")
                      for how, text in SORTS]
            items += [None, ("Copy picture", self.copy_image, "Ctrl+C"), ("Copy path", self.copy_path, "Ctrl+Shift+C"),
                      ("Set as wallpaper", self.wallpaper, "w"), ("Show in Files", self.show_in_files, "e"),
                      ("Open with…", self.open_with, "o"), None,
                      ("Rename…", self.rename, "F2"), ("Move to trash", self.trash, "Del")]
        col = box(True, 0)
        for it in items:
            if it is None:
                sep = Gtk.Separator()
                sep.set_margin_top(3)
                sep.set_margin_bottom(3)
                col.append(sep)
                continue
            text, fn, key = it
            row = box(False, 24, label(text, *(("red",) if "trash" in text else ())), label(key, "dim"))
            row.get_first_child().set_hexpand(True)
            b = Gtk.Button(child=row)
            b.add_css_class("flat")
            b.add_css_class("menu-btn")
            b.connect("clicked", lambda _b, fn=fn: (pop.popdown(), fn()))
            col.append(b)
        pop.set_child(scrolled(col) if len(items) > 24 else col)
        if len(items) > 24:   # taller than a small window: it scrolls
            pop.get_child().set_size_request(260, min(self.window.get_height() - 40, 640))
            pop.get_child().set_propagate_natural_height(True)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    # ---- keys -----------------------------------------------------------------------------------------
    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        c = self.canvas
        if keyval == Gdk.KEY_Escape:
            if self.prompt.get_visible():
                self.prompt_close()
            elif self.full:
                self.fullscreen()
            else:
                self.window.close()
            return True
        if self.typing():
            return False
        k = Gdk.keyval_to_lower(keyval)
        W, H = c.get_width(), c.get_height()
        dw, dh = c.shown_size()
        wide, tall = dw * c.s > W + 1, dh * c.s > H + 1   # zoomed past the window: the arrows pan
        if ctrl and k == Gdk.KEY_o:
            self.open_dialog()
        elif ctrl and k == Gdk.KEY_s:
            self.save_edit()
        elif ctrl and shift and k == Gdk.KEY_c:
            self.copy_path()
        elif ctrl and k == Gdk.KEY_c:
            self.copy_image()
        elif k == Gdk.KEY_q:
            self.window.close()
        elif ctrl:
            return False
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Left) and wide and not shift:
            c.pan(0.15 if keyval == Gdk.KEY_Right else -0.15, 0)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and tall:
            c.pan(0, 0.15 if keyval == Gdk.KEY_Down else -0.15)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_Down):
            self.step(1)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up, Gdk.KEY_BackSpace, Gdk.KEY_Up):
            self.step(-1)
        elif keyval == Gdk.KEY_Home:
            self.go(0)
        elif keyval == Gdk.KEY_End:
            self.go(len(self.files) - 1)
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            c.zoom_by(ZOOM_STEP, (W / 2, H / 2))
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract, Gdk.KEY_underscore):
            c.zoom_by(1 / ZOOM_STEP, (W / 2, H / 2))
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            c.set_fit()
        elif keyval in (Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3, Gdk.KEY_4):
            c.zoom_to(float(keyval - Gdk.KEY_0), (W / 2, H / 2))
        elif k == Gdk.KEY_r:
            c.rotate(-90 if shift else 90)
        elif k == Gdk.KEY_h:
            c.flip(True)
        elif k == Gdk.KEY_v:
            c.flip(False)
        elif k == Gdk.KEY_f or keyval == Gdk.KEY_F11:
            self.fullscreen()
        elif k == Gdk.KEY_s:
            self.slideshow()
        elif keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):   # the slideshow's pace
            self.slideshow(min(max(self.state["slide"] + (1 if keyval == Gdk.KEY_bracketright else -1), 1), 60))
        elif k == Gdk.KEY_i:
            self.toggle("info")
        elif k == Gdk.KEY_t:
            self.toggle("thumbs")
        elif k == Gdk.KEY_b:
            self.cycle_bg()
        elif k == Gdk.KEY_p:
            self.toggle_anim()
        elif k == Gdk.KEY_w:
            self.wallpaper()
        elif k == Gdk.KEY_e:
            self.show_in_files()
        elif k == Gdk.KEY_o:
            self.open_with()
        elif keyval == Gdk.KEY_F2:
            self.rename()
        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.trash()
        elif keyval == Gdk.KEY_F5:
            self.rescan()
        elif keyval == Gdk.KEY_Menu:
            self.menu(c, W / 2, H / 2)
        else:
            return False
        return True


def path_short(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def camera_rows(t):
    """The EXIF that's worth reading: camera, lens, settings, when."""
    rows = []
    cam = " ".join(dict.fromkeys(v for v in (t.get("Make", ""), t.get("Model", "")) if v))
    if cam:
        rows.append(("Camera", cam))
    if t.get("LensModel"):
        rows.append(("Lens", t["LensModel"]))
    exp = ratio(t.get("ExposureTime", ""))
    if exp:
        rows.append(("Exposure", f"1/{round(1 / exp)} s" if exp < 1 else f"{exp:g} s"))
    f = ratio(t.get("FNumber", ""))
    if f:
        rows.append(("Aperture", f"f/{f:.1f}"))
    iso = t.get("PhotographicSensitivity") or t.get("ISOSpeedRatings")
    if iso:
        rows.append(("ISO", iso))
    fl = ratio(t.get("FocalLength", ""))
    if fl:
        rows.append(("Focal length", f"{fl:g} mm"))
    when = t.get("DateTimeOriginal") or t.get("DateTime")
    if when:
        rows.append(("Taken", when.replace(":", "-", 2)))
    if t.get("Software"):
        rows.append(("Software", t["Software"]))
    return rows


class ImagesApp(PanelApp):
    """One viewer: what's opened while it runs goes to it (xdg-open, Files). Esc leaves fullscreen first."""

    def __init__(self, view):
        super().__init__(view, "panels.images", (1100, 680), toggle=True)
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

    def _on_key(self, ctrl, keyval, code, state):
        if keyval == Gdk.KEY_Escape and not self.typing():
            return self.view.key(keyval, state)
        return super()._on_key(ctrl, keyval, code, state)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    ImagesApp(Images()).run(sys.argv)


if __name__ == "__main__":
    main()
