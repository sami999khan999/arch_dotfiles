#!/usr/bin/env python3
# pickergui.py — the file picker: the Open / Save dialogs every app gets through the desktop portal (a browser's
# upload, VS Code's Open Folder, Save as…) are Files windows (filesgui.py) in the panels' look and the colour theme.
# A D-Bus service (org.freedesktop.impl.portal.desktop.sami, started by D-Bus on the first dialog:
# local/share/dbus-1/services/), the FileChooser backend of xdg-desktop-portal: config/xdg-desktop-portal/
# picks it for Hyprland, GTK's own picker after it if this one isn't there (system/portal/ installs sami.portal).
# Each request is a window: Open (a file, files, a folder) or Save (a name, a folder; Save files: a folder),
# the app's file types as a filter. Enter or a double click chooses, Esc or Close cancels. Rename, trash and
# paste are off here: it picks, it doesn't change files. It remembers its own last folder (picker.json).
import fnmatch, gc, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gio, GLib, Gtk, ViewHost, box, button, dropdown, install_css, label
import filesgui as fg

BUS = "org.freedesktop.impl.portal.desktop.sami"
PATH = "/org/freedesktop/portal/desktop"
XML = """<node>
  <interface name="org.freedesktop.impl.portal.FileChooser">""" + "".join(f"""
    <method name="{m}">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{{sv}}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{{sv}}" name="results" direction="out"/>
    </method>""" for m in ("OpenFile", "SaveFile", "SaveFiles")) + """
  </interface>
  <interface name="org.freedesktop.impl.portal.Request"><method name="Close"/></interface>
</node>"""
CHOOSER, REQUEST = Gio.DBusNodeInfo.new_for_xml(XML).interfaces
CSS = fg.CSS + """
.pick-bar { border-top: 1px solid alpha(#3b4261, .7); padding-top: 10px; }
dropdown.filter > button { min-width: 0; padding: 3px 10px; }
"""


def path_of(value):
    """A portal path (ay, NUL-terminated) as a str, or None."""
    if not value:
        return None
    raw = bytes(value).rstrip(b"\0")
    return os.fsdecode(raw) if raw else None


class Picker(fg.Files):
    state_file = os.path.join(os.path.dirname(fg.STATE), "picker.json")
    css = CSS

    def __init__(self, mode, title, opts, done):
        """mode: "open", "save" or "saves" (SaveFiles: a folder for the files named in opts["files"]).
        done(response, uris, current filter) answers the request: 0 chosen, 1 cancelled."""
        self.mode, self.opts, self.done = mode, opts, done
        self.multiple = bool(opts.get("multiple")) and mode == "open"
        self.directory = bool(opts.get("directory")) or mode == "saves"
        self.filters = [f for f in opts.get("filters", []) if f and f[1]]
        cur = opts.get("current_filter")
        self.filter_i = next((i for i, f in enumerate(self.filters) if cur and f[0] == cur[0]), 0)
        self.answered = False
        current_file = path_of(opts.get("current_file"))
        start = path_of(opts.get("current_folder")) or (os.path.dirname(current_file) if current_file else None)
        super().__init__(start)
        self.title = title or ("Save" if mode != "open" else "Choose a folder" if self.directory else "Open")
        self.name = opts.get("current_name") or (os.path.basename(current_file) if current_file else "")
        self.accept_label = opts.get("accept_label") or ("Save" if mode != "open" else "Select" if self.directory
                                                         else "Open")
        self.hints = [("Enter", "open folder / choose"), ("Bksp", "back"), ("Ctrl+H", "hidden"), ("/", "filter"),
                      ("Ctrl+L", "type a path"), ("Esc", "cancel")]

    def build(self):
        page = super().build()
        bits = []
        if self.mode == "save":
            self.name_entry = Gtk.Entry(text=self.name, hexpand=True)
            self.name_entry.connect("activate", lambda *_: self.choose())
            bits += [label("Name", "dim"), self.name_entry]
            GLib.idle_add(self.focus_name)
        else:
            self.name_entry = None
            what = "the folder you're in, or the one selected" if self.directory else \
                "files" if self.multiple else "a file"
            bits.append(label(f"Choose {what}", "dim", ellipsize=True))
            bits[-1].set_hexpand(True)
        if len(self.filters) > 1 or (self.filters and self.mode == "open"):
            dd = dropdown([(i, f[0]) for i, f in enumerate(self.filters)], self.filter_i, self.pick_filter)
            dd.add_css_class("filter")
            dd.set_tooltip_text("which files show")
            bits.append(dd)
        self.go_btn = button(self.accept_label, self.choose, "primary")
        bits += [button("Cancel", self.cancel, "flat"), self.go_btn]
        self.main.append(box(False, 8, *bits, classes=("pick-bar",)))
        self.sel.connect("selection-changed", lambda *_: self.picked_row())
        return page

    def focus_name(self):
        self.name_entry.grab_focus()
        stem = self.name.rfind(".") if "." in self.name[1:] else len(self.name)
        self.name_entry.select_region(0, stem)
        return False

    def pick_filter(self, i):
        self.filter_i = i
        self.refilter()

    def visible(self, e):
        if not super().visible(e):
            return False
        if e.is_dir:
            return True
        if self.directory:
            return False   # a folder is what's chosen: files aren't shown
        if not self.filters:
            return True
        for kind, pattern in self.filters[self.filter_i][1]:
            if kind == 0 and fnmatch.fnmatch(e.name.casefold(), pattern.casefold()):
                return True
            if kind == 1:
                ctype = Gio.content_type_guess(e.name, None)[0]
                if ctype and Gio.content_type_is_a(ctype, Gio.content_type_from_mime_type(pattern) or pattern):
                    return True
        return False

    def picked_row(self):
        """Save: a file clicked puts its name in the Name field (to save over it)."""
        sel = self.selected()
        if self.name_entry and len(sel) == 1 and not sel[0].is_dir:
            self.name_entry.set_text(sel[0].name)

    def open_entry(self, e):
        if e is None:
            return
        if e.is_dir:
            self.go(e.path)
        elif self.name_entry:
            self.name_entry.set_text(e.name)
            self.choose()
        else:
            self.finish([e.path])

    def choose(self):
        if self.mode == "save":
            name = self.name_entry.get_text().strip()
            if not name:
                self.say("type a name", "bad")
                return
            target = os.path.join(self.cwd, os.path.expanduser(name))
            if os.path.isdir(target):   # a folder's name (or a path) typed: go there
                self.go(target)
                self.name_entry.set_text("")
                return
            if not os.path.isdir(os.path.dirname(target)):
                self.say(f"no folder {os.path.dirname(target)}", "bad")
                return
            if os.path.exists(target):
                self.ask(f"“{os.path.basename(target)}” is already here. Replace it?",
                         lambda _v: self.finish([target]), ok="Replace", danger=True)
                return
            self.finish([target])
        elif self.directory:
            sel = [e for e in self.selected() if e.is_dir]
            folder = sel[0].path if len(sel) == 1 else self.cwd
            if self.mode == "saves":
                names = [path_of(f) for f in self.opts.get("files", [])]
                targets = [os.path.join(folder, os.path.basename(n)) for n in names if n]
                taken = [t for t in targets if os.path.exists(t)]
                if taken:
                    self.ask(f"{len(taken)} of the files are already in {os.path.basename(folder)}. Replace them?",
                             lambda _v: self.finish(targets), ok="Replace", danger=True)
                    return
                self.finish(targets)
            else:
                self.finish([folder])
        else:
            sel = self.selected()
            files = [e.path for e in sel if not e.is_dir]
            if not files:
                if len(sel) == 1:   # a folder selected: open it
                    self.go(sel[0].path)
                else:
                    self.say("select a file", "bad")
                return
            self.finish(files if self.multiple else files[:1])

    def finish(self, paths):
        self.answered = True
        f = self.filters[self.filter_i] if self.filters else None
        self.done(0, [Gio.File.new_for_path(p).get_uri() for p in paths], f)
        self.host.win.close()

    def cancel(self):
        if not self.answered:
            self.answered = True
            self.done(1, [], None)
        self.host.win.close()

    # picking only: nothing here changes files except a new folder
    def rename(self):
        pass

    def trash(self, permanent=False):
        pass

    def paste(self):
        pass

    def copy(self, cut=False):
        if not cut:
            super().copy()

    def menu(self, sel, parent, x, y):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("files-menu")
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        col = box(True, 0)
        for text, fn in (("New folder…", self.new_folder), ("Copy path", self.copy_path),
                         ("Hide hidden files" if self.show_hidden else "Show hidden files", self.toggle_hidden)):
            b = button(text, lambda fn=fn: (pop.popdown(), fn()), "flat", "menu-btn")
            col.append(b)
        pop.set_child(col)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape and not (self.prompt.get_visible() or self.path_entry.get_visible()
                                             or self.filter.get_text()):
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self.typing():
            sel = self.selected()
            if len(sel) == 1 and sel[0].is_dir and not self.directory:
                self.go(sel[0].path)
            else:
                self.choose()
            return True
        return super().key(keyval, state)


class Dialog(ViewHost):
    """A picker's window: the panels' header and status line around it (gtkkit.ViewHost)."""
    def __init__(self, view, app):
        self.view, view.host = view, self
        self.build_window(app, (1100, 700))
        self.win.connect("close-request", lambda *_: (view.cancel() if not view.answered else None) and False)
        self.win.present()


class Service(Gtk.Application):
    """The portal backend: owns BUS, answers each FileChooser call with a Dialog."""
    def __init__(self):
        super().__init__(application_id="panels.picker", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()   # a service: it stays for the next dialog
        install_css(CSS)
        Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.REPLACE, self.on_bus, None,
                         lambda *_: self.quit())   # the name taken by another one: this one goes

    def do_activate(self):
        pass

    def on_bus(self, conn, _name):
        conn.register_object_with_closures2(PATH, CHOOSER, self.call, None, None)

    def call(self, conn, _sender, _path, _iface, method, params, invocation):
        handle, _app, _parent, title, options = params.unpack()
        state = {"replied": False, "view": None}

        def done(code, uris, flt):
            if state["replied"]:
                return
            state["replied"] = True
            conn.unregister_object(request)
            results = {}
            if code == 0:
                results["uris"] = GLib.Variant("as", uris)
                if flt:
                    results["current_filter"] = GLib.Variant("(sa(us))", flt)
            invocation.return_value(GLib.Variant("(ua{sv})", (code, results)))

        def close(_c, _s, _p, _i, _m, _params, inv):   # the app gave up on the dialog
            if state["view"] and not state["view"].answered:
                state["view"].answered = True
                done(2, [], None)
                state["view"].host.win.close()
            inv.return_value(None)
        request = conn.register_object_with_closures2(handle, REQUEST, close, None, None)
        mode = {"OpenFile": "open", "SaveFile": "save", "SaveFiles": "saves"}[method]
        view = Picker(mode, title, options, done)
        state["view"] = view
        Dialog(view, self)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(5, lambda: gc.collect() is not None)
    Service().run([sys.argv[0]])


if __name__ == "__main__":
    main()
