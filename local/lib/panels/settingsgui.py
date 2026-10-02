#!/usr/bin/env python3
# settingsgui.py — System Settings (GTK 4, Tokyo Night). Super + I opens it; running it again closes it.
# One window for every setting: a sidebar of sections (type to fuzzy-search sections and the settings
# in them), a page per section. Every control applies at once and says where it saved the value;
# settingslib.py does the reading, writing and applying, this file is only the layout.
# A page is built the first time it's shown, so the window opens fast.
#
#   type  search     ↑↓  sections     Esc  clear, then close
import os, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, GLib, Gtk, Pango, View, backdrop_setting, box, button, clear, dropdown, label,
                    rule_heading, run, scrolled, setting_row, slider, spin, switch)
import keys
import settingslib as S

HERE = os.path.dirname(os.path.abspath(__file__))
THUMBS = os.path.expanduser("~/.cache/settings-thumbs")


class Section:
    def __init__(self, group, key, icon, title, words, build):
        self.group, self.key, self.icon, self.title, self.words, self.build = group, key, icon, title, words, build


class Settings(View):
    title, subtitle = "Settings", "every setting in one place"
    icon = ""
    interval = 0
    hints = [("type", "search"), ("↑↓", "sections"), ("Esc", "clear / close")]
    css = """
    .settings-side { min-width: 230px; border-right: 1px solid alpha(#c0caf5, .07); }
    .settings-side list > row { padding: 4px 14px; }
    .settings-side .side-icon { color: #6b8fe0; min-width: 18px; }
    .settings-group { color: #565f89; font-size: 9pt; font-weight: 700; padding: 8px 14px 2px 14px; }
    .settings-page { padding: 6px 26px 20px 22px; }
    .page-title { font-size: 14pt; font-weight: 700; margin: 8px 0 2px 0; }
    .wall { padding: 3px; border: 2px solid transparent; background: transparent; }
    .wall.current { border-color: #6b8fe0; }
    .wall:hover { background: alpha(#c0caf5, .06); }
    .banner { background: alpha(#e0af68, .14); padding: 10px 14px; margin: 8px 0; }
    .power button.on { background: #6b8fe0; color: #16161e; font-weight: 700; }
    .about-key { color: #565f89; min-width: 110px; }
    """

    def __init__(self):
        super().__init__()
        self.pages = {}
        self.sections = [
            Section("Look & feel", "appearance", "", "Appearance",
                    "gaps border width rounded corners blur shadow opacity transparency dim panels backdrop behind", self.page_appearance),
            Section("Look & feel", "animations", "", "Animations",
                    "animation speed slide workspace motion", self.page_animations),
            Section("Look & feel", "wallpaper", "", "Wallpaper",
                    "background image picture fill fit", self.page_wallpaper),
            Section("Look & feel", "theme", "", "Theme & fonts",
                    "dark light mode icons cursor size font monospace gtk qt", self.page_theme),
            Section("Input & display", "keyboard", "", "Keyboard",
                    "layout language switch repeat rate delay numlock typing", self.page_keyboard),
            Section("Input & display", "mouse", "", "Mouse",
                    "pointer sensitivity speed acceleration focus follows natural scroll zoom", self.page_mouse),
            Section("Input & display", "display", "", "Display",
                    "monitor screen resolution refresh rate hz scale vrr", self.page_display),
            Section("Input & display", "night", "", "Night light",
                    "blue light temperature warm schedule evening hyprsunset", self.page_night),
            Section("Power & bar", "idle", "", "Idle & lock",
                    "lock screen timeout screen off sleep stay awake hypridle", self.page_idle),
            Section("Power & bar", "power", "", "Power profile",
                    "performance balanced power saver energy", self.page_power),
            Section("Power & bar", "notifications", "", "Notifications",
                    "mako position corner timeout do not disturb dnd hide apps", self.page_notifications),
            Section("Power & bar", "bar", "", "Top bar",
                    "waybar position bottom height cpu memory temperature modules", self.page_bar),
            Section("System", "apps", "", "Default apps",
                    "terminal browser file manager editor calculator open with web links pdf images video",
                    self.page_apps),
            Section("System", "time", "", "Date & time",
                    "timezone time zone clock 24 hour ntp automatic", self.page_time),
            Section("System", "more", "", "More settings",
                    "sound audio volume network wifi code backup workspace groups shortcuts system monitor agentmux agents",
                    self.page_more),
            Section("System", "about", "", "About this PC",
                    "system info cpu gpu memory disk kernel version uptime", self.page_about),
        ]

    # ---- frame -------------------------------------------------------------------------------
    def build(self):
        self.search = Gtk.Entry(placeholder_text="Search settings…")
        self.search.set_margin_start(12)
        self.search.set_margin_end(12)
        self.search.set_margin_top(10)
        self.search.connect("changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.first_match())

        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.set_header_func(self.group_header)
        self.side.connect("row-selected", self.selected)
        self.rows = {}
        for s in self.sections:
            row = Gtk.ListBoxRow(child=box(False, 10, label(s.icon, "side-icon"), label(s.title)))
            row.section = s
            self.rows[s.key] = row
            self.side.append(row)
        self.side.set_filter_func(self.matches)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True)
        left = box(True, 6, self.search, scrolled(self.side), classes=("settings-side",))
        self.side.select_row(self.rows["appearance"])
        GLib.idle_add(lambda: self.search.grab_focus() and False)
        return box(False, 0, left, self.stack)

    def group_header(self, row, before):
        if before is None or before.section.group != row.section.group:
            row.set_header(label(row.section.group.upper(), "settings-group"))
        else:
            row.set_header(None)

    def matches(self, row):
        q = self.search.get_text().strip()
        return not q or keys.fuzzy(q, row.section.title, row.section.words) is not None

    def filter(self):
        self.side.invalidate_filter()
        sel = self.side.get_selected_row()
        if sel is None or not self.matches(sel):
            self.first_match()

    def first_match(self):
        for s in self.sections:
            if self.matches(self.rows[s.key]):
                self.side.select_row(self.rows[s.key])
                return

    def selected(self, _list, row):
        if row is None:
            return
        s = row.section
        if s.key not in self.pages:
            content = box(True, 0, label(s.title, "page-title"), classes=("settings-page",))
            try:
                s.build(content)
            except Exception as e:   # one broken page mustn't take the window down
                content.append(label(f"Couldn't load this page: {e}", "red", wrap=True))
            self.pages[s.key] = scrolled(content)
            self.stack.add_named(self.pages[s.key], s.key)
        self.stack.set_visible_child_name(s.key)

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and self.search.has_focus():
            visible = [self.rows[s.key] for s in self.sections if self.matches(self.rows[s.key])]
            cur = self.side.get_selected_row()
            i = visible.index(cur) if cur in visible else -1
            i = min(i + 1, len(visible) - 1) if keyval == Gdk.KEY_Down else max(i - 1, 0)
            if visible:
                self.side.select_row(visible[i])
            return True
        if not self.typing():
            ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if ch.isprintable() and ch != "\0" and not state & Gdk.ModifierType.CONTROL_MASK:
                self.search.grab_focus()
                self.search.set_text(self.search.get_text() + ch)
                self.search.set_position(-1)
                return True
        return False

    # ---- helpers -----------------------------------------------------------------------------
    def done(self, where, err=None):
        """Status line after a change: where it was saved, or the error in red."""
        if err:
            self.say(f"Couldn't apply: {str(err).splitlines()[0][:120]}", "bad")
        else:
            self.say(f"Applied · saved to {where}")

    def hypr(self, opt, where="settings.json"):
        """on_change for a control bound to a Hyprland option."""
        return lambda v: self.done(where, S.hypr_set(opt, v))

    def bg(self, work, then=None):
        """Run slow work (pkexec-less system calls) off the UI thread; then(result) back on it."""
        def go():
            result = work()
            if then:
                GLib.idle_add(lambda: then(result) and False)
        threading.Thread(target=go, daemon=True).start()

    @staticmethod
    def heading(page, text, spaced=True):
        page.append(rule_heading(text, spaced=spaced))

    def launch(self, *cmd):
        S.spawn(*cmd)

    # ---- Look & feel -------------------------------------------------------------------------
    def page_appearance(self, p):
        g = S.hypr_get
        self.heading(p, "Windows")
        p.append(setting_row("Gap between windows", "Space between tiled windows, in pixels",
                             spin(0, 30, 1, g("general:gaps_in"), self.hypr("general:gaps_in"))))
        p.append(setting_row("Gap to screen edge", "Space around the tiled windows",
                             spin(0, 40, 1, g("general:gaps_out"), self.hypr("general:gaps_out"))))
        p.append(setting_row("Border width", "0 hides window borders",
                             spin(0, 6, 1, g("general:border_size"), self.hypr("general:border_size"))))
        p.append(setting_row("Rounded corners", "Corner radius; 0 is square",
                             spin(0, 20, 1, g("decoration:rounding"), self.hypr("decoration:rounding"))))
        self.heading(p, "Transparency")
        p.append(setting_row("Focused window opacity", "1 is fully opaque",
                             slider(0.5, 1, 0.01, g("decoration:active_opacity"), self.hypr("decoration:active_opacity"))))
        p.append(setting_row("Other windows opacity", None,
                             slider(0.5, 1, 0.01, g("decoration:inactive_opacity"), self.hypr("decoration:inactive_opacity"))))
        p.append(setting_row("Dim behind the scratchpad", "How dark the screen gets behind Super + S",
                             slider(0, 1, 0.05, g("decoration:dim_special"), self.hypr("decoration:dim_special"))))
        self.heading(p, "Blur")
        p.append(setting_row("Blur", "Blurs what's behind translucent windows and the bar",
                             switch(g("decoration:blur:enabled"), self.hypr("decoration:blur:enabled"))))
        p.append(setting_row("Blur size", None,
                             spin(1, 20, 1, g("decoration:blur:size"), self.hypr("decoration:blur:size"))))
        p.append(setting_row("Blur passes", "More is smoother and costs more GPU",
                             spin(1, 6, 1, g("decoration:blur:passes"), self.hypr("decoration:blur:passes"))))
        self.heading(p, "Panels")
        on, blur, darken = backdrop_setting()
        p.append(setting_row("Blur behind panels", "While a panel (Settings, shortcuts, sound…) is open, the screen "
                             "behind it is blurred and darkened; a click there closes the panel",
                             switch(on, lambda v: self.panels("backdrop", v))))
        p.append(setting_row("Panel blur", "How blurry, in pixels; 0 only darkens. Separate from the window "
                             "blur above",
                             slider(0, 40, 1, blur, lambda v: self.panels("blur", int(v)), fmt="{:.0f} px")))
        p.append(setting_row("Panel darken", "How much darker the screen gets behind a panel",
                             slider(0, 0.8, 0.05, darken, lambda v: self.panels("darken", v), fmt="{:.0%}")))
        p.append(label("Changes show from the next panel you open.", "setting-sub", wrap=True))
        self.heading(p, "Shadow")
        p.append(setting_row("Window shadow", None,
                             switch(g("decoration:shadow:enabled"), self.hypr("decoration:shadow:enabled"))))
        p.append(setting_row("Shadow size", None,
                             spin(0, 40, 1, g("decoration:shadow:range"), self.hypr("decoration:shadow:range"))))
        p.append(box(False, 0, button("Back to the config's values", lambda: self.reset_hypr(
            ["general:gaps_in", "general:gaps_out", "general:border_size", "decoration:rounding",
             "decoration:active_opacity", "decoration:inactive_opacity", "decoration:dim_special",
             "decoration:blur:enabled", "decoration:blur:size", "decoration:blur:passes",
             "decoration:shadow:enabled", "decoration:shadow:range"], "appearance"), "flat"),
            classes=("setting",)))

    def panels(self, key, value):
        S.remember("panels", key, value)
        self.done("settings.json · from the next panel")

    def reset_hypr(self, opts, page):
        for o in opts:
            S.remember("hypr", o, None)
        S.generate()
        S.sh("hyprctl", "reload")
        self.rebuild(page)
        self.say("Back to the values in the hand-written config")

    def rebuild(self, key):
        """Throw a page away and show it again freshly read."""
        old = self.pages.pop(key, None)
        if old:
            self.stack.remove(old)
        self.selected(self.side, self.rows[key])

    def page_animations(self, p):
        speed, ws = S.anim_get()
        self.heading(p, "Motion")
        p.append(setting_row("Animations", "Off makes every change instant",
                             switch(S.hypr_get("animations:enabled"), self.hypr("animations:enabled"))))
        p.append(setting_row("Speed", "Scales every animation in animations.lua",
                             dropdown([("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast")], speed,
                                      lambda v: self.done("settings.json", S.anim_set(speed=v)))))
        p.append(setting_row("Slide between workspaces", "Off: workspaces switch instantly",
                             switch(ws, lambda v: self.done("settings.json", S.anim_set(workspaces=v)))))

    def page_wallpaper(self, p):
        current = S.wallpaper_get()
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                           min_children_per_line=2, row_spacing=8, column_spacing=8, homogeneous=True)
        self.heading(p, "Pick one")
        p.append(flow)
        buttons = []
        for path in S.wallpapers():
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
            pic.set_size_request(170, 96)
            b = Gtk.Button(child=pic, tooltip_text=os.path.basename(path))
            b.add_css_class("wall")
            if path == current:
                b.add_css_class("current")
            b.path = path
            buttons.append(b)

            def chosen(_b, path=path):
                S.wallpaper_set(path)
                for o in buttons:
                    (o.add_css_class if o.path == path else o.remove_css_class)("current")
                self.say(f"Wallpaper: {os.path.basename(path)} (lock screen uses it too)")
            b.connect("clicked", chosen)
            flow.append(b)
            GLib.idle_add(self.load_thumb, path, pic)
        self.heading(p, "Options")
        p.append(setting_row("Fit", "Fill crops the picture to the screen; Fit shows all of it",
                             dropdown([("fill", "Fill"), ("fit", "Fit"), ("center", "Centre"), ("tile", "Tile")],
                                      S.wallpaper_mode(),
                                      lambda v: (S.wallpaper_set(mode=v), self.done("settings.json")))))
        p.append(setting_row("Wallpaper folder", S.WALLS.replace(S.HOME, "~"),
                             button("Open folder", lambda: self.launch("xdg-open", S.WALLS))))

    def load_thumb(self, path, pic):
        """A small copy of the picture, made once and kept in ~/.cache."""
        from gi.repository import GdkPixbuf
        os.makedirs(THUMBS, exist_ok=True)
        thumb = f"{THUMBS}/{os.path.basename(path)}.png"
        try:
            if not os.path.exists(thumb) or os.path.getmtime(thumb) < os.path.getmtime(path):
                GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 340, 192, True).savev(thumb, "png", [], [])
            pic.set_filename(thumb)
        except GLib.Error:
            pass
        return False

    def page_theme(self, p):
        t = S.theme_get()
        self.heading(p, "Style")
        p.append(setting_row("Dark mode", "GTK and libadwaita apps; Qt and KDE apps follow their own theme",
                             switch(t["dark"], lambda v: (S.theme_set_dark(v), self.done("gsettings + gtk-3.0")))))
        icons = S.themes("icons")
        p.append(setting_row("Icon theme", None,
                             dropdown([(i, i) for i in icons], t["icons"],
                                      lambda v: (S.theme_set_icons(v), self.done("gsettings, gtk-3.0, qt6ct")))))
        self.heading(p, "Pointer")
        cur = {"name": t["cursor"], "size": t["cursor_size"]}

        def cursor(name=None, size=None):
            cur["name"], cur["size"] = name or cur["name"], size or cur["size"]
            S.theme_set_cursor(cur["name"], cur["size"])
            self.done("gsettings, gtk-3.0, uwsm/env (apps started from now on)")
        p.append(setting_row("Cursor theme", None,
                             dropdown([(c, c) for c in S.themes("cursors")], t["cursor"], lambda v: cursor(name=v))))
        p.append(setting_row("Cursor size", None,
                             dropdown([(s, str(s)) for s in (16, 20, 24, 32, 40, 48, 64)], t["cursor_size"],
                                      lambda v: cursor(size=v))))
        self.heading(p, "Fonts")
        for mono, title, sub in ((False, "Interface font", "Menus, buttons and window text of GTK apps"),
                                 (True, "Monospace font", "Code and terminals that follow the system font")):
            fb = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
            fb.set_font_desc(Pango.FontDescription.from_string(t["mono" if mono else "font"]))
            fb.connect("notify::font-desc", lambda w, _p, m=mono: (
                S.theme_set_font(w.get_font_desc().to_string(), m), self.done("gsettings")))
            p.append(setting_row(title, sub, fb))
        self.heading(p, "More")
        p.append(setting_row("GTK theme tool", "nwg-look: every GTK option",
                             button("Open", lambda: self.launch("nwg-look"))))
        p.append(setting_row("Qt theme tool", "qt6ct: style, colours and fonts of Qt apps",
                             button("Open", lambda: self.launch("qt6ct"))))

    # ---- Input & display ---------------------------------------------------------------------
    def page_keyboard(self, p):
        layouts = [l for l in (S.hypr_get("input:kb_layout") or "us").split(",") if l]
        names = dict(S.xkb_layouts())
        self.heading(p, "Layouts")
        holder = box(True, 0)
        p.append(holder)

        def save(new):
            layouts[:] = new
            self.done("settings.json", S.hypr_set("input:kb_layout", ",".join(new)))
            show()

        def show():
            clear(holder)
            for i, code in enumerate(layouts):
                rm = button("Remove", lambda c=code: save([l for l in layouts if l != c]), "flat")
                rm.set_sensitive(len(layouts) > 1)
                up = button("↑", lambda i=i: save(layouts[:i - 1] + [layouts[i], layouts[i - 1]] + layouts[i + 1:]), "flat",
                            tooltip="Move up (the first one is used at login)")
                up.set_sensitive(i > 0)
                holder.append(setting_row(names.get(code, code), "default" if i == 0 else code, up, rm))
        show()
        add = dropdown([("", "Add a layout…")] + [(c, f"{d} ({c})") for c, d in S.xkb_layouts()], "",
                       lambda v: v and v not in layouts and save(layouts + [v]), search=True)
        p.append(setting_row("Add layout", "Type to search", add))
        opts = S.hypr_get("input:kb_options") or ""
        grp = next((o for o in opts.split(",") if o.startswith("grp:")), "")

        def switch_key(v):
            rest = [o for o in opts.split(",") if o and not o.startswith("grp:")]
            self.done("settings.json", S.hypr_set("input:kb_options", ",".join(rest + ([v] if v else []))))
        p.append(setting_row("Switch layouts with", "Only matters with two or more layouts",
                             dropdown(S.SWITCH_KEYS, grp, switch_key)))
        self.heading(p, "Typing")
        p.append(setting_row("Repeat rate", "Characters per second while a key is held",
                             spin(10, 80, 1, S.hypr_get("input:repeat_rate"), self.hypr("input:repeat_rate"))))
        p.append(setting_row("Repeat delay", "Milliseconds before a held key starts repeating",
                             spin(150, 1000, 25, S.hypr_get("input:repeat_delay"), self.hypr("input:repeat_delay"))))
        p.append(setting_row("Num Lock on at login", None,
                             switch(S.hypr_get("input:numlock_by_default"), self.hypr("input:numlock_by_default"))))
        p.append(setting_row("Try it", None, Gtk.Entry(placeholder_text="type here to test", width_chars=24)))

    def page_mouse(self, p):
        g = S.hypr_get
        self.heading(p, "Pointer")
        p.append(setting_row("Speed", "-1 slowest, 0 default, 1 fastest",
                             slider(-1, 1, 0.05, g("input:sensitivity"), self.hypr("input:sensitivity"))))
        p.append(setting_row("Acceleration", "Flat: the same distance however fast you move",
                             dropdown([("flat", "Flat"), ("adaptive", "Adaptive")], g("input:accel_profile") or "adaptive",
                                      self.hypr("input:accel_profile"))))
        p.append(setting_row("Natural scrolling", "Content follows the wheel, like a phone",
                             switch(g("input:natural_scroll"), self.hypr("input:natural_scroll"))))
        self.heading(p, "Focus")
        p.append(setting_row("Focus follows the mouse", "Which window gets the keyboard",
                             dropdown([(1, "Window under the pointer"), (2, "On click (pointer moves freely)"),
                                       (0, "Only on click")], g("input:follow_mouse"), self.hypr("input:follow_mouse"))))
        self.heading(p, "Zoom")
        p.append(setting_row("Screen zoom", "Magnifies around the pointer (also Super + Ctrl + Z)",
                             slider(1, 4, 0.1, g("cursor:zoom_factor"), self.hypr("cursor:zoom_factor"), fmt="{:.1f}×")))

    def page_display(self, p):
        for m in S.monitors():
            if m.get("disabled"):
                continue
            name = m["name"]
            now = f"{m['width']}x{m['height']}@{m['refreshRate']:.2f}"
            modes = []
            for mode in m.get("availableModes", []):
                mode = mode.removesuffix("Hz")
                if mode not in modes:
                    modes.append(mode)
            if now not in modes:
                modes.insert(0, now)
            self.heading(p, f"{name} · {m.get('make', '')} {m.get('model', '')}".strip())
            pick = {"mode": now, "scale": float(m["scale"])}
            p.append(setting_row("Resolution and refresh rate", None,
                                 dropdown([(x, x.replace("@", "  @ ") + " Hz") for x in modes], now,
                                          lambda v: pick.update(mode=v))))
            p.append(setting_row("Scale", "Bigger text and windows on high-resolution screens",
                                 dropdown([(s, f"{int(s * 100)} %") for s in (1.0, 1.25, 1.5, 1.75, 2.0)],
                                          float(m["scale"]), lambda v: pick.update(scale=v))))
            banner = box(False, 10, classes=("banner",))
            banner.set_visible(False)
            p.append(box(False, 0, button("Apply", lambda n=name, k=pick, old=(now, float(m["scale"])), b=banner:
                                          self.try_mode(n, k, old, b), "primary"), classes=("setting",)))
            p.append(banner)
        self.heading(p, "All screens")
        p.append(setting_row("Variable refresh rate", "Adaptive sync (FreeSync / G-Sync) where the screen supports it",
                             dropdown([(0, "Off"), (1, "Always"), (2, "Fullscreen only"), (3, "Fullscreen games and video")],
                                      S.hypr_get("misc:vrr"), self.hypr("misc:vrr"))))

    def try_mode(self, name, pick, old, banner):
        """Apply now; keep it only if confirmed within 15 s (a mode the screen can't show reverts)."""
        err = S.monitor_set(name, pick["mode"], pick["scale"], keep=False)
        if err:
            return self.done("", err)
        clear(banner)
        left = {"s": 15, "timer": 0}
        text = label("", "amber")
        banner.append(text)
        banner.append(Gtk.Box(hexpand=True))

        def finish(keep):
            if left["timer"]:
                GLib.source_remove(left["timer"])
                left["timer"] = 0
            banner.set_visible(False)
            if keep:
                self.done("settings.local.json (this PC)", S.monitor_set(name, pick["mode"], pick["scale"]))
            else:
                S.monitor_set(name, old[0], old[1], keep=False)
                self.say("Display settings reverted")

        def tick():
            left["s"] -= 1
            if left["s"] <= 0:
                finish(False)
                return False
            text.set_text(f"Keep these display settings? Reverting in {left['s']} s")
            return True
        text.set_text("Keep these display settings? Reverting in 15 s")
        banner.append(button("Keep", lambda: finish(True), "primary"))
        banner.append(button("Revert", lambda: finish(False)))
        banner.set_visible(True)
        left["timer"] = GLib.timeout_add(1000, tick)

    def page_night(self, p):
        n = S.night_get()
        self.heading(p, "Night light")
        p.append(setting_row("Night light", "Warmer colours, less blue light (Super + Ctrl + N)",
                             switch(n["on"], lambda v: (S.night_set_on(v), self.say("Night light " + ("on" if v else "off"))))))
        p.append(setting_row("Warmth", "Lower is warmer; changes live while it's on",
                             slider(2500, 6500, 100, n["temp"], lambda v: (S.night_set_temp(v), self.done("settings.json")),
                                    fmt="{:.0f} K")))
        self.heading(p, "Schedule")
        start = Gtk.Entry(text=n["start"], width_chars=6, max_length=5)
        end = Gtk.Entry(text=n["end"], width_chars=6, max_length=5)
        sched = switch(n["schedule"], lambda v: apply())

        def apply():
            import re
            a, b = start.get_text().strip(), end.get_text().strip()
            if not (re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", a) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", b)):
                return self.say("Times are HH:MM, e.g. 20:00", "bad")
            S.night_set_schedule(sched.get_active(), a, b)
            self.done("settings.json + systemd user timers" if sched.get_active() else "settings.json (schedule off)")
        for e in (start, end):
            e.connect("activate", lambda *_: apply())
        p.append(setting_row("On a schedule", "Turns on and off by itself; two systemd timers, nothing runs in between",
                             sched))
        p.append(setting_row("Turn on at", "24-hour time; Enter to save", start))
        p.append(setting_row("Turn off at", None, end))

    # ---- Power & bar -------------------------------------------------------------------------
    def page_idle(self, p):
        i = S.idle_get()
        mins = [60, 120, 180, 300, 600, 900, 1200, 1800, 2700, 3600]
        opts = [(s, f"{s // 60} min") for s in mins] + [(S.NEVER, "Never")]

        for v in (i["lock"], i["off"]):   # a value set by hand that isn't a choice: shown as it is
            if v and v not in dict(opts):
                opts.insert(-1, (v, f"{v / 60:g} min"))
        opts[:-1] = sorted(opts[:-1])

        def near(v):
            return v if v in dict(opts) else S.NEVER
        self.heading(p, "When idle")
        p.append(setting_row("Lock the screen after", None,
                             dropdown(opts, near(i["lock"]), lambda v: (S.idle_set(lock=v), self.done("hypridle.conf")))))
        p.append(setting_row("Turn the screen off after", "Counted from the last input, like the lock",
                             dropdown(opts, near(i["off"]), lambda v: (S.idle_set(off=v), self.done("hypridle.conf")))))
        p.append(setting_row("Lock before sleep", "Locks the screen when the PC suspends",
                             switch(i["before_sleep"], lambda v: (S.idle_set(before_sleep=v), self.done("hypridle.conf")))))
        self.heading(p, "Right now")
        p.append(setting_row("Stay awake", "Never lock or turn the screen off until switched back (Super + Ctrl + I)",
                             switch(i["awake"], lambda v: (S.idle_stay_awake(v), self.say("Stay awake " + ("on" if v else "off"))))))

    def page_power(self, p):
        profiles = S.power_profiles()
        if not profiles:
            p.append(label("power-profiles-daemon isn't running.", "dim"))
            return
        names = {"power-saver": ("Power saver", "Slower, cooler, quieter"),
                 "balanced": ("Balanced", "The default"),
                 "performance": ("Performance", "Fastest, more power and heat")}
        self.heading(p, "Profile")
        row = box(False, 6, classes=("power",))
        btns = {}

        def pick(prof):
            err = S.power_set(prof)
            for k, b in btns.items():
                (b.add_css_class if k == prof else b.remove_css_class)("on")
            self.done("power-profiles-daemon", err)
        for prof in profiles:
            b = button(names.get(prof, (prof, ""))[0], lambda pr=prof: pick(pr), tooltip=names.get(prof, ("", ""))[1])
            if prof == S.power_get():
                b.add_css_class("on")
            btns[prof] = b
            row.append(b)
        p.append(box(False, 0, row, classes=("setting",)))
        if "performance" not in profiles:
            p.append(label("Performance isn't offered by this processor's driver.", "setting-sub"))

    def page_notifications(self, p):
        m = S.mako_get()
        self.heading(p, "Where and how long")
        p.append(setting_row("Position", None,
                             dropdown(S.CORNERS, m["anchor"], lambda v: (S.mako_set("anchor", v), self.done("mako/config")))))
        p.append(setting_row("Stay on screen for", None,
                             dropdown([(3000, "3 s"), (5000, "5 s"), (8000, "8 s"), (10000, "10 s"), (15000, "15 s"),
                                       (0, "Until dismissed")], m["timeout"],
                                      lambda v: (S.mako_set("default-timeout", v), self.done("mako/config")))))
        p.append(setting_row("Do not disturb", "Hide notifications until switched off (Super + Ctrl + ,)",
                             switch(m["dnd"], lambda v: (S.mako_dnd(v), self.say("Do not disturb " + ("on" if v else "off"))))))
        p.append(setting_row("Try it", None, button("Send a test notification", S.mako_test)))
        self.heading(p, "Hidden apps")
        hidden = list(m["hidden"])
        holder = box(True, 0)
        p.append(holder)

        def save(apps):
            hidden[:] = apps
            S.mako_hidden_set(apps)
            self.done("mako/config")
            show()

        def show():
            clear(holder)
            if not hidden:
                holder.append(label("None: every app's notifications show.", "setting-sub"))
            for a in hidden:
                holder.append(setting_row(a, None, button("Show again", lambda a=a: save([x for x in hidden if x != a]), "flat")))
        show()
        entry = Gtk.Entry(placeholder_text="app name, e.g. discord", width_chars=22)
        add = lambda: entry.get_text().strip() and entry.get_text().strip() not in hidden and (
            save(hidden + [entry.get_text().strip()]), entry.set_text(""))
        entry.connect("activate", lambda *_: add())
        p.append(setting_row("Hide an app", "Its app name as notifications report it", entry, button("Hide", add)))

    def page_bar(self, p):
        b = S.bar_get()
        self.heading(p, "Bar")
        p.append(setting_row("Position", None,
                             dropdown([("top", "Top"), ("bottom", "Bottom")], b["position"],
                                      lambda v: (S.bar_set_position(v), self.done("waybar/config.jsonc")))))
        p.append(setting_row("Height", "In pixels",
                             spin(20, 44, 1, b["height"], lambda v: (S.bar_set_height(v), self.done("waybar/config.jsonc")))))
        self.heading(p, "System stats on the bar")
        shown = list(b["stats"])
        has_battery = any(os.path.exists(f"/sys/class/power_supply/{d}/capacity")
                          for d in os.listdir("/sys/class/power_supply")) if os.path.isdir("/sys/class/power_supply") else False
        names = {"cpu": "Processor", "memory": "Memory", "temperature": "Temperature", "battery": "Battery"}

        def flip(mod, on):
            if on and mod not in shown:
                shown.append(mod)
            if not on and mod in shown:
                shown.remove(mod)
            S.bar_set_stats(shown)
            self.done("waybar/config.jsonc")
        for mod in S.STATS:
            if mod == "battery" and not has_battery:
                continue
            p.append(setting_row(names[mod], None, switch(mod in shown, lambda v, m=mod: flip(m, v))))

    # ---- System ------------------------------------------------------------------------------
    def page_apps(self, p):
        self.heading(p, "Apps the shortcuts open")
        for var, title, _ in S.ROLES:
            opts = S.role_options(var)
            if not opts:
                continue
            p.append(setting_row(title, None, dropdown([(o, o.split()[0]) for o in opts], S.role_get(var),
                                                       lambda v, var=var: (S.role_set(var, v), self.done("variables.lua")))))
        self.heading(p, "Open files and links with")
        for title, mimes in S.MIME_ROLES:
            opts = S.mime_options(mimes[0])
            if not opts:
                continue
            p.append(setting_row(title, None, dropdown(opts, S.mime_get(mimes[0]),
                                                       lambda v, m=mimes: (S.mime_set(m, v), self.done("mimeapps.list")))))

    def page_time(self, p):
        t = S.time_get()
        clock = label("", "readout")
        p.append(box(False, 0, clock, classes=("setting",)))

        def tick():
            import datetime
            clock.set_text(datetime.datetime.now().strftime("%H:%M:%S" if t["h24"] else "%I:%M:%S %p"))
            return clock.get_root() is not None
        tick()
        GLib.timeout_add(1000, tick)
        self.heading(p, "Time")
        zones = S.timezones()
        p.append(setting_row("Time zone", "Type to search",
                             dropdown([(z, z.replace("_", " ")) for z in zones], t["zone"],
                                      lambda v: self.bg(lambda: S.time_set_zone(v), lambda e: self.done("system (timedatectl)", e)),
                                      search=True)))
        p.append(setting_row("Set the time automatically", "From the internet (NTP)",
                             switch(t["ntp"], lambda v: self.bg(lambda: S.time_set_ntp(v), lambda e: self.done("system (timedatectl)", e)))))
        p.append(setting_row("24-hour clock", "The bar's clock and GTK apps",
                             switch(t["h24"], lambda v: (t.update(h24=v), S.time_set_h24(v), tick(),
                                                         self.done("settings.json + gsettings")))))

    def page_more(self, p):
        self.heading(p, "Other panels")
        for title, sub, script in (("Sound", "Outputs, inputs, volume, app streams", "audiogui.py"),
                                   ("Network", "Connection, traffic, Wi-Fi", "netgui.py"),
                                   ("Code backup", "codesync: folders, timing, ignore list, restore", "syncgui.py"),
                                   ("Workspace groups", "Which app lives on which workspace", "wsgui.py"),
                                   ("Shortcuts", "Every keybind, searchable (Super + K)", "keysgui.py"),
                                   ("agentmux", "Its shortcuts and notifications (Ctrl + Alt + S in agentmux)",
                                    "agentmuxgui.py"),
                                   ("System monitor", "Processes, memory, storage (Super + Ctrl + T)", "sysgui.py")):
            p.append(setting_row(title, sub, button("Open", lambda s=script: self.launch("python3", f"{HERE}/{s}"))))

    def page_about(self, p):
        rows = S.about()
        self.heading(p, "This PC")
        grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        for i, (k, v) in enumerate(rows):
            grid.attach(label(k, "about-key"), 0, i, 1, 1)
            val = label(v, wrap=True)
            val.set_selectable(True)
            grid.attach(val, 1, i, 1, 1)
        p.append(box(False, 0, grid, classes=("setting",)))

        def copy():
            Gdk.Display.get_default().get_clipboard().set("\n".join(f"{k}: {v}" for k, v in rows))
            self.say("Copied")
        p.append(box(False, 0, button("Copy", copy), classes=("setting",)))


def make():
    return Settings()


def main():
    run(make(), "panels.settings", (1000, 680))


if __name__ == "__main__":
    main()
