#!/usr/bin/env python3
# videogui.py — Videos, the video player (the default app for video and audio files: config/mimeapps.list).
#   videogui.py <file>…   plays it, then the other videos in its folder; several files: just those
#   videogui.py <folder>  the videos in that folder
# mpv plays (libmpv.py: its OpenGL renderer draws into the window), in the panels' look: a control bar
# (play, seek, volume, speed, subtitles, playlist, fullscreen), a playlist pane, an info overlay and a
# right-click menu with all of it. The running player takes what's opened next. Each file resumes where
# it was left (mpv's watch-later files, ~/.local/state/videos/watch_later); a finished one starts over.
# The folder is watched (a new download joins the list). Remembered: the volume, mute, the playlist pane.
import gc, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gio, GLib, Gtk, PanelApp, View, box, button, clear, collect, label, recolor, scrolled
import libmpv

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or f"{HOME}/.local/state", "videos")
STATE = os.path.join(STATE_DIR, "state.json")
SHOTS = f"{HOME}/Pictures/Screenshots"   # where screenshot.sh puts the screen's
FILES = f"{HOME}/.local/lib/panels/filesgui.py"
VIDEO = {"mp4", "m4v", "mkv", "webm", "mov", "avi", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts", "mts", "3gp",
         "ogv", "vob"}
AUDIO = {"mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "aac", "wma", "alac", "ape"}
EXTS = VIDEO | AUDIO
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
SEEK_SMALL, SEEK_MID, SEEK_BIG = 5, 10, 30
TICK_MS = 250                # the time and the seek bar while playing (paused: nothing runs)
HIDE_MS = 2500               # fullscreen: the controls and the pointer go after this long without a move
G_MOVIE, G_MUSIC = "\U000f0381", "\U000f0387"
G_PLAY, G_PAUSE, G_NEXT, G_PREV = "\U000f040a", "\U000f03e4", "\U000f04ad", "\U000f04ae"
G_VOL_HIGH, G_VOL_MID, G_VOL_LOW, G_VOL_OFF = "\U000f057e", "\U000f0580", "\U000f057f", "\U000f0581"
G_FULL, G_FULL_EXIT, G_SUBS, G_LIST, G_LOOP = "\U000f0293", "\U000f0294", "\U000f0a16", "\U000f0411", "\U000f0456"
CSS = """
.video-msg { color: #565f89; font-size: 13pt; }
.osd { background: alpha(#16161e, .85); color: #c0caf5; padding: 6px 12px; margin: 14px;
       border: 1px solid alpha(#3b4261, .7); }
.video-info { background: alpha(#16161e, .85); color: #a9b1d6; padding: 10px 14px; margin: 14px;
              border: 1px solid alpha(#3b4261, .7); }
.controls { border-top: 1px solid alpha(#3b4261, .7); padding: 4px 12px; background: #16161e; }
.controls .ctl { background: transparent; color: #a9b1d6; padding: 2px 8px; font-size: 13pt; min-height: 0; }
.controls .ctl:hover { background: alpha(#c0caf5, .08); color: #c0caf5; }
.controls .ctl.on { color: #6b8fe0; }
.controls .play { color: #c0caf5; font-size: 15pt; }
.controls .time { color: #a9b1d6; }
.controls .speed { color: #e0af68; }
.controls scale { padding: 8px 0; }
.list-pane { border-left: 1px solid alpha(#3b4261, .7); }
.list-pane list > row { padding: 6px 14px; }
.list-pane row.playing label { color: #6b8fe0; font-weight: 700; }
.menu-btn { padding: 4px 14px 4px 6px; }
.menu-tick { min-width: 22px; }
.menu-btn label { font-family: "JetBrainsMono Nerd Font"; }
popover.videos-menu > contents { padding: 4px; }
window.panel.full { background: #16161e; }
"""


def ext(path):
    return path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""


def natural(name):
    """Sort key: numbers by value (part 2 before part 10), case ignored."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name.lower())]


def clock(s):
    s = max(int(s or 0), 0)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def track_name(t):
    """A track as the menu and the OSD say it: its title, language, codec."""
    parts = [t.get("title"), t.get("lang"), t.get("codec")]
    name = "  ·  ".join(str(p) for p in parts if p) or f"track {t.get('id')}"
    return name + ("  (external)" if t.get("external") else "")


def path_short(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


class Videos(View):
    title, icon = "Videos", G_MOVIE
    interval = 0
    backdrop = False
    css = CSS
    hints = [("Space", "play / pause"), ("←/→", f"{SEEK_SMALL} s"), ("↑/↓", "volume"),
             ("n/p", "next / previous"), ("f", "fullscreen"), ("c", "subtitles"), ("t", "playlist"),
             ("i", "info"), ("Right-click", "more")]

    def __init__(self):
        super().__init__()
        self.files, self.index, self.only = [], -1, None
        self.monitor, self.watched, self.rescan_timer = None, None, 0
        self.full = False
        self.props = {}
        self.tracks = []
        self.loaded = None          # the file mpv has open (set once it's loaded)
        self.tick_timer = self.click_timer = self.hide_timer = self.osd_timer = 0
        self.dragging = False
        self.state = {"volume": 100, "mute": False, "list": False}
        try:
            with open(STATE) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save_state(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(STATE, "w") as f:
                json.dump(self.state, f)
        except OSError:
            pass

    # ---- the window -----------------------------------------------------------------------------------
    def build(self):
        self.player = libmpv.Player({
            "vo": "libmpv", "hwdec": "auto-safe", "idle": "yes", "keep-open": "yes", "terminal": "no",
            "input-default-bindings": "no", "input-vo-keyboard": "no", "osc": "no", "osd-level": "0",
            "ytdl": "no", "sub-auto": "fuzzy", "audio-file-auto": "fuzzy",
            "background-color": recolor("#16161e"),   # around the picture, as the panels' background
            "watch-later-dir": os.path.join(STATE_DIR, "watch_later"),
            "watch-later-options": "start,aid,sid,sub-delay,audio-delay",
            "screenshot-directory": SHOTS, "screenshot-template": "%F-%wH.%wM.%wS",
            "volume": self.state["volume"], "volume-max": 150, "mute": "yes" if self.state["mute"] else "no",
        }, self.on_property, self.on_event)
        for name, fmt in (("pause", libmpv.FMT_FLAG), ("duration", libmpv.FMT_DOUBLE),
                          ("volume", libmpv.FMT_DOUBLE), ("mute", libmpv.FMT_FLAG), ("speed", libmpv.FMT_DOUBLE),
                          ("eof-reached", libmpv.FMT_FLAG), ("loop-file", libmpv.FMT_STRING),
                          ("width", libmpv.FMT_INT64), ("height", libmpv.FMT_INT64), ("sid", libmpv.FMT_STRING)):
            self.player.observe(name, fmt)

        self.area = Gtk.GLArea(hexpand=True, vexpand=True)
        self.area.connect("realize", self.gl_realize)
        self.area.connect("unrealize", self.gl_unrealize)
        self.area.connect("render", self.gl_render)
        self.msg = label("", "video-msg", xalign=0.5)
        self.msg.set_halign(Gtk.Align.CENTER)
        self.msg.set_valign(Gtk.Align.CENTER)
        self.msg.set_can_target(False)
        self.osd_label = label("", "osd")
        self.osd_label.set_halign(Gtk.Align.START)
        self.osd_label.set_valign(Gtk.Align.START)
        self.osd_label.set_can_target(False)
        self.osd_label.set_visible(False)
        self.info_label = label("", "video-info")
        self.info_label.set_halign(Gtk.Align.END)
        self.info_label.set_valign(Gtk.Align.START)
        self.info_label.set_can_target(False)
        self.info_label.set_visible(False)
        self.overlay = Gtk.Overlay(child=self.area)
        for w in (self.msg, self.osd_label, self.info_label):
            self.overlay.add_overlay(w)

        clicks = Gtk.GestureClick(button=0)
        clicks.connect("pressed", self.pressed)
        self.overlay.add_controller(clicks)
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self.wheel)
        self.area.add_controller(wheel)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.moved)
        self.overlay.add_controller(motion)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, files, _x, _y: self.open([f.get_path() for f in files.get_files()
                                                                  if f.get_path()]) or True)
        self.overlay.add_controller(drop)

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.connect("row-activated", lambda _l, row: self.play(row.get_index()))
        self.list_pane = scrolled(self.list_box)
        self.list_pane.set_size_request(280, -1)
        self.list_pane.set_hexpand(False)
        self.list_pane.add_css_class("list-pane")

        self.controls = self.build_controls()
        self.video_row = box(False, 0, self.overlay, self.list_pane)
        self.body = box(True, 0, self.video_row, self.controls)
        self.panes()
        self.say_center("No video open  ·  Ctrl+O to open one, or drop it here")
        return self.body

    def build_controls(self):
        def ctl(glyph, fn, tip, *classes):
            b = button(glyph, lambda *_: fn(), "ctl", *classes, tooltip=tip)
            b.set_focusable(False)   # Space and the arrows stay the player's
            return b
        self.b_prev = ctl(G_PREV, lambda: self.step(-1), "Previous (p)")
        self.b_play = ctl(G_PLAY, self.toggle_pause, "Play / pause (Space)", "play")
        self.b_next = ctl(G_NEXT, lambda: self.step(1), "Next (n)")
        self.pos_label = label("0:00", "time")
        self.dur_label = label("0:00", "time")
        self.seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.seek.set_draw_value(False)
        self.seek.set_hexpand(True)
        self.seek.set_focusable(False)
        self.seek.connect("change-value", self.seek_dragged)
        held = Gtk.GestureClick()   # while the bar is held, the playing position doesn't pull it back
        held.connect("pressed", lambda *_: setattr(self, "dragging", True))
        held.connect("stopped", lambda *_: setattr(self, "dragging", False))
        held.connect("released", lambda *_: setattr(self, "dragging", False))
        held.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.seek.add_controller(held)
        self.b_vol = ctl(G_VOL_HIGH, self.toggle_mute, "Mute (m)")
        self.vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 150, 1)
        self.vol.set_draw_value(False)
        self.vol.set_size_request(90, -1)
        self.vol.set_focusable(False)
        self.vol.add_mark(100, Gtk.PositionType.BOTTOM, None)   # past it mpv amplifies
        self.vol.connect("change-value", lambda _s, _t, v: self.set_volume(v) or False)
        self.speed_label = label("", "speed")
        self.b_loop = ctl(G_LOOP, self.toggle_loop, "Loop this file (r)")
        self.b_subs = ctl(G_SUBS, lambda: self.cycle_track("sub"), "Subtitles (c)")
        self.b_list = ctl(G_LIST, self.toggle_list, "Playlist (t)")
        self.b_full = ctl(G_FULL, self.fullscreen, "Fullscreen (f)")
        bar = box(False, 6, self.b_prev, self.b_play, self.b_next, self.pos_label, self.seek, self.dur_label,
                  self.b_vol, self.vol, self.speed_label, self.b_loop, self.b_subs, self.b_list, self.b_full,
                  classes=("controls",))
        bar.set_valign(Gtk.Align.END)
        return bar

    def panes(self):
        self.list_pane.set_visible(self.state["list"] and not self.full and len(self.files) > 1)
        (self.b_list.add_css_class if self.state["list"] else self.b_list.remove_css_class)("on")
        many = len(self.files) > 1
        self.b_prev.set_visible(many)
        self.b_next.set_visible(many)

    def chrome(self, on):
        """The header and the hint line (hidden in fullscreen)."""
        outer = self.window.get_child()
        outer.get_first_child().set_visible(on)
        outer.get_last_child().set_visible(on)

    # ---- mpv's picture --------------------------------------------------------------------------------
    def gl_realize(self, area):
        area.make_current()
        if area.get_error():
            self.say_center(f"No OpenGL: {area.get_error().message}")
            return
        try:
            self.player.render_init(area.queue_render)
        except libmpv.MpvError as e:
            self.say_center(f"mpv can't draw here: {e}")

    def gl_unrealize(self, area):
        area.make_current()
        self.player.render_free()

    def gl_render(self, area, _ctx):
        s = area.get_scale_factor()
        self.player.render(libmpv.bound_framebuffer(), area.get_width() * s, area.get_height() * s)
        return True

    # ---- which files ----------------------------------------------------------------------------------
    def open(self, paths):
        """Play these: one file (then its folder's others), a folder, or exactly the files given."""
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
        self.files = self.listing()
        if first and first not in self.files:   # an odd extension: played anyway
            self.files.insert(0, first)
        self.fill_list()
        if self.files:
            self.play(self.files.index(first) if first else 0)
        else:
            self.say_center("No videos here")

    def listing(self):
        if self.only is not None:
            return [p for p in self.only if os.path.exists(p)]
        try:
            names = os.listdir(self.watched)
        except OSError:
            names = []
        files = [os.path.join(self.watched, n) for n in names if ext(n) in EXTS and not n.startswith(".")
                 and os.path.isfile(os.path.join(self.watched, n))]
        return sorted(files, key=lambda p: natural(os.path.basename(p)))

    def watch(self, folder):
        """Follow the folder (a download finishing, a file renamed): inotify, no polling."""
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

    def folder_changed(self, _m, _f, _other, event):
        if event in (Gio.FileMonitorEvent.ATTRIBUTE_CHANGED, Gio.FileMonitorEvent.CHANGED):
            return   # a file growing (a download): one rescan when it's done is enough
        if self.rescan_timer:
            GLib.source_remove(self.rescan_timer)

        def later():
            self.rescan_timer = 0
            self.rescan()
            return False
        self.rescan_timer = GLib.timeout_add(500, later)

    def rescan(self):
        """Read the list again, staying on the file that's playing (it keeps playing even if it's gone)."""
        cur = self.current()
        files = self.listing()
        if cur and cur not in files:
            files.insert(min(self.index, len(files)), cur)
        self.files = files
        self.index = files.index(cur) if cur in files else -1
        self.fill_list()

    def current(self):
        return self.files[self.index] if 0 <= self.index < len(self.files) else None

    def fill_list(self):
        clear(self.list_box)
        for i, p in enumerate(self.files):
            row = Gtk.ListBoxRow(child=label(os.path.basename(p), ellipsize=True))
            row.set_tooltip_text(os.path.basename(p))
            if i == self.index:
                row.add_css_class("playing")
            self.list_box.append(row)
        self.panes()

    def mark_playing(self):
        row, i = self.list_box.get_first_child(), 0
        while row is not None:
            (row.add_css_class if i == self.index else row.remove_css_class)("playing")
            row, i = row.get_next_sibling(), i + 1

    # ---- playing --------------------------------------------------------------------------------------
    def play(self, i):
        if not 0 <= i < len(self.files):
            return
        self.leave_file()
        self.index = i
        self.mark_playing()
        self.player.command_async("loadfile", self.files[i])
        self.player.set("pause", False)
        self.view_changed()

    def leave_file(self):
        """Before another file, or closing: remember where this one was (or forget it, if it was watched
        to the end), so opening it again resumes there."""
        if not self.loaded:
            return
        try:
            pos, dur = float(self.player.get("time-pos") or 0), self.props.get("duration") or 0
            if self.props.get("eof-reached") or (dur and pos > dur - 10):
                self.player.command("delete-watch-later-config", self.loaded)
            elif pos > 5:
                self.player.command("write-watch-later-config")
        except (libmpv.MpvError, ValueError):
            pass

    def step(self, d):
        if len(self.files) > 1:
            self.play((self.index + d) % len(self.files))

    def toggle_pause(self):
        if self.loaded:
            if self.props.get("eof-reached"):   # at the end: again from the start
                self.player.command("seek", 0, "absolute")
            self.player.command("cycle", "pause")

    def seek_by(self, secs):
        if not self.loaded:
            return
        try:
            self.player.command("seek", secs, "relative+exact")
        except libmpv.MpvError:
            return
        self.osd(("+" if secs > 0 else "−") + f"{abs(secs)} s")

    def seek_to(self, frac):
        if self.loaded and self.props.get("duration"):
            self.player.command("seek", frac * 100, "absolute-percent+exact")

    def seek_dragged(self, _scale, _scroll, value):
        if self.loaded:
            dur = self.props.get("duration") or 0
            value = min(max(value, 0), dur)
            # while held, keyframes only (instant); the release lands exactly
            self.player.command("seek", value, "absolute+keyframes" if self.dragging else "absolute+exact")
            self.pos_label.set_text(clock(value))
        return False

    def set_volume(self, v):
        self.player.set("volume", round(min(max(v, 0), 150)))
        if self.props.get("mute"):
            self.player.set("mute", False)

    def volume_by(self, d):
        self.set_volume((self.props.get("volume") or 0) + d)
        self.osd(f"volume {round(min(max((self.props.get('volume') or 0) + d, 0), 150))} %")

    def toggle_mute(self):
        self.player.command("cycle", "mute")

    def set_speed(self, s):
        self.player.set("speed", round(min(max(s, 0.25), 4.0), 2))
        self.osd(f"speed {self.player.get('speed', '1')[:4].rstrip('0').rstrip('.')}×")

    def toggle_loop(self):
        on = self.props.get("loop-file") not in ("inf", "yes")
        self.player.set("loop-file", "inf" if on else "no")
        self.osd("loop this file" if on else "loop off")

    def cycle_track(self, kind):
        """Subtitles (c) / audio (a): the next track, then off (subtitles only)."""
        if not self.loaded:
            return
        prop = "sid" if kind == "sub" else "aid"
        try:
            self.player.command("cycle", prop)
        except libmpv.MpvError:
            return
        self.tracks = self.player.get_json("track-list", [])
        on = next((t for t in self.tracks if t.get("type") == kind and t.get("selected")), None)
        what = "subtitles" if kind == "sub" else "audio"
        if not any(t.get("type") == kind for t in self.tracks):
            self.osd(f"no {what} in this file")
        else:
            self.osd(f"{what}: {track_name(on)}" if on else f"{what} off")

    def pick_track(self, kind, tid):
        self.player.set("sid" if kind == "sub" else "aid", tid)

    def screenshot(self):
        if not self.loaded:
            return
        os.makedirs(SHOTS, exist_ok=True)
        self.player.command_async("screenshot", "subtitles")
        self.osd(f"screenshot saved to {path_short(SHOTS)}")

    # ---- what mpv says --------------------------------------------------------------------------------
    def on_property(self, name, value):
        self.props[name] = value
        if name == "pause":
            self.b_play.set_label(G_PLAY if value else G_PAUSE)
            self.arm_tick()
        elif name == "duration":
            self.seek.set_range(0, max(value or 0, 1))
            self.dur_label.set_text(clock(value))
        elif name in ("volume", "mute"):
            vol, mute = self.props.get("volume") or 0, self.props.get("mute")
            self.b_vol.set_label(G_VOL_OFF if mute or vol == 0 else G_VOL_LOW if vol < 34
                                 else G_VOL_MID if vol < 67 else G_VOL_HIGH)
            self.vol.set_value(vol)
            self.state["volume"], self.state["mute"] = round(vol), bool(mute)
        elif name == "speed":
            on = value is not None and abs(value - 1) > 1e-3
            self.speed_label.set_text(f"{value:g}×" if on else "")
            self.speed_label.set_visible(on)
        elif name == "loop-file":
            (self.b_loop.add_css_class if value in ("inf", "yes") else self.b_loop.remove_css_class)("on")
        elif name == "sid":
            (self.b_subs.add_css_class if value not in (None, "no") else self.b_subs.remove_css_class)("on")
        elif name == "eof-reached" and value:
            self.finished()
        if name in ("width", "height", "speed", "loop-file"):
            self.view_changed()

    def on_event(self, kind, data):
        if kind == libmpv.EV_FILE_LOADED:
            self.loaded = self.player.get("path")
            self.tracks = self.player.get_json("track-list", [])
            if any(t.get("type") == "video" for t in self.tracks):
                self.msg.set_visible(False)
            else:   # music: its name where the picture would be
                self.say_center(f"{G_MUSIC}  {self.player.get('media-title') or os.path.basename(self.loaded)}")
            self.update_time()
            self.view_changed()
            if self.info_label.get_visible():
                self.fill_info()
        elif kind == libmpv.EV_END_FILE and data[0] == libmpv.END_ERROR:
            self.loaded = None
            name = os.path.basename(self.current() or "")
            self.say_center(f"Can't play {name}")
            self.say(f"can't play {name}: {data[1]}", "bad")
        elif kind in (libmpv.EV_PLAYBACK_RESTART, libmpv.EV_VIDEO_RECONFIG):   # after a seek; the picture changed
            self.update_time()
            if kind == libmpv.EV_VIDEO_RECONFIG and self.info_label.get_visible():
                self.fill_info()

    def finished(self):
        """The end of a file (mpv stays on its last frame: keep-open): the next one, if there is one."""
        if self.loaded:
            try:
                self.player.command("delete-watch-later-config", self.loaded)
            except libmpv.MpvError:
                pass
        if self.index < len(self.files) - 1:
            self.play(self.index + 1)

    def arm_tick(self):
        playing = self.loaded is not None and self.props.get("pause") is False
        if playing and not self.tick_timer:
            self.tick_timer = GLib.timeout_add(TICK_MS, self.tick)
        elif not playing and self.tick_timer:
            GLib.source_remove(self.tick_timer)
            self.tick_timer = 0
        self.update_time()

    def tick(self):
        if self.props.get("pause") is not False:
            self.tick_timer = 0
            return False
        self.update_time()
        return True

    def update_time(self):
        try:
            pos = float(self.player.get("time-pos") or 0)
        except ValueError:
            pos = 0
        self.pos_label.set_text(clock(pos))
        if not self.dragging:
            self.seek.set_value(pos)

    def view_changed(self):
        """The header's line: name · n / total · size (· speed · loop)."""
        if not getattr(self, "host", None) or not self.host.win:
            return
        path = self.current()
        if not path:
            self.set_subtitle("")
            return
        parts = [os.path.basename(path)]
        if len(self.files) > 1:
            parts.append(f"{self.index + 1} / {len(self.files)}")
        w, h = self.props.get("width"), self.props.get("height")
        if w and h:
            parts.append(f"{w} × {h}")
        speed = self.props.get("speed")
        if speed and abs(speed - 1) > 1e-3:
            parts.append(f"{speed:g}×")
        if self.props.get("loop-file") in ("inf", "yes"):
            parts.append("looping")
        self.set_subtitle("  ·  ".join(parts))
        self.window.set_title(f"{os.path.basename(path)} — Videos")

    # ---- messages over the picture --------------------------------------------------------------------
    def say_center(self, text):
        self.msg.set_text(text)
        self.msg.set_visible(True)

    def osd(self, text):
        self.osd_label.set_text(text)
        self.osd_label.set_visible(True)
        if self.osd_timer:
            GLib.source_remove(self.osd_timer)

        def hide():
            self.osd_timer = 0
            self.osd_label.set_visible(False)
            return False
        self.osd_timer = GLib.timeout_add(1200, hide)

    def toggle_info(self):
        on = not self.info_label.get_visible()
        self.info_label.set_visible(on)
        if on:
            self.fill_info()

    def fill_info(self):
        """What's playing and how: the file, the picture, the sound, the decoder."""
        g = self.player.get
        path = self.current()
        if not path or not self.loaded:
            self.info_label.set_text("nothing playing")
            return
        try:
            size = os.path.getsize(path)
            size = f"{size / 1e9:.2f} GB" if size >= 1e9 else f"{size / 1e6:.1f} MB"
        except OSError:
            size = "?"
        rows = [("File", os.path.basename(path)), ("Folder", path_short(os.path.dirname(path))), ("Size", size),
                ("Length", clock(self.props.get("duration")))]
        if self.props.get("width"):
            fps = g("container-fps")
            rows += [("Video", f"{g('video-codec-name') or g('video-format') or '?'}  ·  "
                               f"{self.props['width']} × {self.props['height']}"
                               + (f"  ·  {float(fps):.3g} fps" if fps else "")),
                     ("Decoder", "hardware (" + g("hwdec-current") + ")" if g("hwdec-current", "no") != "no"
                      else "software")]
        if g("audio-codec-name"):
            rows.append(("Audio", f"{g('audio-codec-name')}  ·  {g('audio-params/channel-count', '?')} ch  "
                                  f"·  {int(g('audio-params/samplerate', '0')) / 1000:g} kHz"))
        subs = [t for t in self.tracks if t.get("type") == "sub"]
        if subs:
            rows.append(("Subtitles", f"{len(subs)} track" + ("s" if len(subs) > 1 else "")))
        dim = recolor("#565f89")
        self.info_label.set_markup("\n".join(
            f"<span foreground='{dim}'>{k:<10}</span>{GLib.markup_escape_text(v)}" for k, v in rows))

    # ---- input ----------------------------------------------------------------------------------------
    def pressed(self, gesture, n, x, y):
        b = gesture.get_current_button()
        if b == 3:
            self.menu(self.overlay, x, y)
        elif b == 8:
            self.step(-1)
        elif b == 9:
            self.step(1)
        elif b == 1 and n == 1:   # a click pauses, unless it's the first of a double-click
            if self.click_timer:
                GLib.source_remove(self.click_timer)

            def single():
                self.click_timer = 0
                self.toggle_pause()
                return False
            self.click_timer = GLib.timeout_add(Gtk.Settings.get_default().get_property("gtk-double-click-time"),
                                                single)
        elif b == 1 and n == 2:
            if self.click_timer:
                GLib.source_remove(self.click_timer)
                self.click_timer = 0
            self.fullscreen()

    def wheel(self, ctl, _dx, dy):
        # a notch is 5 %; a touchpad's small deltas move it smoothly
        self.volume_by(-dy * (5 if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 1))
        return True

    def moved(self, _ctl, x, y):
        if not self.full or getattr(self, "last_pointer", None) == (x, y):
            return   # GTK repeats a motion when the picture under the pointer changes
        self.last_pointer = (x, y)
        self.controls.set_visible(True)
        self.overlay.set_cursor(None)
        self.arm_hide()

    def arm_hide(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        def hide():
            self.hide_timer = 0
            if self.full:
                self.controls.set_visible(False)
                self.overlay.set_cursor_from_name("none")
            return False
        self.hide_timer = GLib.timeout_add(HIDE_MS, hide)

    # ---- doing things ---------------------------------------------------------------------------------
    def fullscreen(self):
        self.full = not self.full
        win = self.window
        self.host.set_fullscreen(self.full)
        (win.add_css_class if self.full else win.remove_css_class)("full")
        self.chrome(not self.full)
        self.panes()
        self.b_full.set_label(G_FULL_EXIT if self.full else G_FULL)
        # fullscreen, the bar floats over the picture's bottom edge and hides when the pointer rests
        if self.full:
            self.body.remove(self.controls)
            self.overlay.add_overlay(self.controls)
            self.arm_hide()
        else:
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
                self.hide_timer = 0
            self.overlay.remove_overlay(self.controls)
            self.body.append(self.controls)
            self.controls.set_visible(True)
            self.overlay.set_cursor(None)

    def toggle_list(self):
        self.state["list"] = not self.state["list"]
        self.save_state()
        self.panes()

    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def copy_path(self):
        if self.current():
            self.window.get_clipboard().set(self.current())
            self.say(f"path copied: {self.current()}")

    def show_in_files(self):
        if self.current():
            self.launch("python3", FILES, self.current())

    def open_with(self):
        if self.current():
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(self.current()))
            launcher.set_always_ask(True)
            launcher.launch(self.window, None, None)

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open a video", modal=True)
        filt = Gtk.FileFilter(name="Videos and music")
        for e in sorted(EXTS):
            filt.add_suffix(e)
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
        self.say(f"{os.path.basename(path)} moved to the trash")
        self.loaded = None   # nothing to remember about it
        if self.only is not None:
            self.only.remove(path)
        i = self.index
        self.files.remove(path)
        self.index = -1
        self.fill_list()
        if self.files:
            self.play(min(i, len(self.files) - 1))
        else:
            self.player.command("stop")
            self.say_center("No videos here")
            self.view_changed()

    # ---- the right-click menu -------------------------------------------------------------------------
    def menu(self, parent, x, y):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("videos-menu")
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        have = self.loaded is not None
        # (text, do, key[, checked]): the check goes in a column of its own, so every label lines up
        items = [("Open…", self.open_dialog, "Ctrl+O")]
        if have:
            items += [None, ("Play" if self.props.get("pause") else "Pause", self.toggle_pause, "Space")]
            if len(self.files) > 1:
                items += [("Next", lambda: self.step(1), "n"), ("Previous", lambda: self.step(-1), "p")]
            self.tracks = self.player.get_json("track-list", [])
            subs = [t for t in self.tracks if t.get("type") == "sub"]
            if subs:
                off = not any(t.get("selected") for t in subs)
                items += [None, ("Subtitles off", lambda: self.pick_track("sub", "no"), "", off)]
                items += [(track_name(t), lambda t=t: self.pick_track("sub", t["id"]), "", bool(t.get("selected")))
                          for t in subs]
            audio = [t for t in self.tracks if t.get("type") == "audio"]
            if len(audio) > 1:
                items.append(None)
                items += [(track_name(t), lambda t=t: self.pick_track("audio", t["id"]), "", bool(t.get("selected")))
                          for t in audio]
            speed = self.props.get("speed") or 1
            items.append(None)
            items += [(f"Speed {s:g}×", lambda s=s: self.set_speed(s), "Backspace" if s == 1 else "",
                       abs(speed - s) < 1e-3) for s in SPEEDS]
            items += [None, ("Loop this file", self.toggle_loop, "r", self.props.get("loop-file") in ("inf", "yes")),
                      ("Exit fullscreen" if self.full else "Fullscreen", self.fullscreen, "f"),
                      ("Playlist", self.toggle_list, "t", bool(self.state["list"])),
                      ("Info", self.toggle_info, "i", bool(self.info_label.get_visible())),
                      None, ("Screenshot", self.screenshot, "s"), ("Copy path", self.copy_path, "Ctrl+Shift+C"),
                      ("Show in Files", self.show_in_files, "e"), ("Open with…", self.open_with, "o"), None,
                      ("Move to trash", self.trash, "Del")]
        col = box(True, 0)
        for it in items:
            if it is None:
                sep = Gtk.Separator()
                sep.set_margin_top(3)
                sep.set_margin_bottom(3)
                col.append(sep)
                continue
            text, fn, key, on = (*it, False)[:4]
            text_label = label(text, *(("red",) if "trash" in text else ()))
            text_label.set_hexpand(True)
            key_label = label(key, "dim")
            key_label.set_margin_start(24)
            row = box(False, 0, label("\u2713" if on else "", "menu-tick", "accent"), text_label, key_label)
            b = Gtk.Button(child=row)
            b.add_css_class("flat")
            b.add_css_class("menu-btn")
            b.connect("clicked", lambda _b, fn=fn: (pop.popdown(), fn()))
            col.append(b)
        # as tall as it needs, but never past the window (a popover is cut at its edge): then it scrolls
        sc = scrolled(col)
        sc.set_propagate_natural_height(True)
        sc.set_propagate_natural_width(True)
        sc.set_max_content_height(max(self.window.get_height() - 40, 120))
        pop.set_child(sc)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    # ---- keys -----------------------------------------------------------------------------------------
    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            if self.full:
                self.fullscreen()
            else:
                self.window.close()
            return True
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and k == Gdk.KEY_o:
            self.open_dialog()
        elif ctrl and shift and k == Gdk.KEY_c:
            self.copy_path()
        elif k == Gdk.KEY_q and not ctrl:
            self.window.close()
        elif ctrl:
            return False
        elif keyval in (Gdk.KEY_space, Gdk.KEY_k, Gdk.KEY_K, Gdk.KEY_AudioPlay, Gdk.KEY_AudioPause):
            self.toggle_pause()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            n = SEEK_BIG if shift else SEEK_SMALL
            self.seek_by(n if keyval == Gdk.KEY_Right else -n)
        elif k in (Gdk.KEY_j, Gdk.KEY_l):
            self.seek_by(SEEK_MID if k == Gdk.KEY_l else -SEEK_MID)
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.volume_by(5 if keyval == Gdk.KEY_Up else -5)
        elif k == Gdk.KEY_m:
            self.toggle_mute()
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_AudioNext) or k == Gdk.KEY_n:
            self.step(1)
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_AudioPrev) or k == Gdk.KEY_p:
            self.step(-1)
        elif keyval == Gdk.KEY_Home:
            self.seek_to(0)
        elif Gdk.KEY_0 <= keyval <= Gdk.KEY_9:   # 3 = 30 % in, as on YouTube
            self.seek_to((keyval - Gdk.KEY_0) / 10)
        elif keyval in (Gdk.KEY_comma, Gdk.KEY_period):   # a frame back / on (pauses)
            if self.loaded:
                self.player.command("frame-back-step" if keyval == Gdk.KEY_comma else "frame-step")
        elif keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            self.set_speed((self.props.get("speed") or 1) * (1.1 if keyval == Gdk.KEY_bracketright else 1 / 1.1))
        elif keyval == Gdk.KEY_BackSpace:
            self.set_speed(1.0)
        elif k == Gdk.KEY_f or keyval == Gdk.KEY_F11:
            self.fullscreen()
        elif k == Gdk.KEY_c:
            self.cycle_track("sub")
        elif k == Gdk.KEY_a:
            self.cycle_track("audio")
        elif k == Gdk.KEY_r:
            self.toggle_loop()
        elif k == Gdk.KEY_t:
            self.toggle_list()
        elif k == Gdk.KEY_i:
            self.toggle_info()
        elif k == Gdk.KEY_s:
            self.screenshot()
        elif k == Gdk.KEY_e:
            self.show_in_files()
        elif k == Gdk.KEY_o:
            self.open_with()
        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.trash()
        elif keyval == Gdk.KEY_Menu:
            self.menu(self.overlay, self.overlay.get_width() / 2, self.overlay.get_height() / 2)
        else:
            return False
        return True

    def quit(self):
        """The app is closing: remember the place, the volume; then mpv goes."""
        self.leave_file()
        self.save_state()
        if self.player.render_ctx:   # quit with the window still up: mpv waits for its renderer to go first
            self.area.make_current()
            self.player.render_free()
        self.player.destroy()


class VideosApp(PanelApp):
    """One player: what's opened while it runs goes to it (xdg-open, Files). Esc leaves fullscreen first."""

    def __init__(self, view):
        super().__init__(view, "panels.videos", (1100, 680), toggle=True)
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
        if keyval == Gdk.KEY_Escape and not self.typing():
            return self.view.key(keyval, state)
        return super()._on_key(ctrl, keyval, code, state)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    VideosApp(Videos()).run(sys.argv)


if __name__ == "__main__":
    main()
