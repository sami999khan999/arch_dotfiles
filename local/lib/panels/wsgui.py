#!/usr/bin/env python3
# wsgui.py — the workspace groups manager as a GTK window (Tokyo Night). `wsgroups` opens it
# (Super + Ctrl + G, or the waybar groups icon); running it again closes it.
#
#   left   the ten workspaces: number, name, how many windows are open
#   right  the selected one: its settings (editable), actions, its open windows
#
# All the logic (workspaces.conf, launching, tidying) is wsgroups' own; this only draws it.
# The Control Center still embeds the terminal version (wsgroups.UI).
import importlib.machinery, importlib.util, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, rule_heading, run, scrolled


def load_wsgroups():
    path = os.path.expanduser("~/.local/bin/wsgroups")
    loader = importlib.machinery.SourceFileLoader("wsgroups", path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("wsgroups", loader))
    loader.exec_module(module)
    return module


class Workspaces(View):
    title, subtitle = "Workspaces", "Alt + 1…0 switches windows"
    hints = [("1-0", "select"), ("Enter", "save"), ("l", "launch"), ("g", "go"), ("t", "tidy"), ("Esc", "close")]
    popup = "~/.local/bin/wsgroups"
    icon = "\U000f0570"   # nf-md-view_carousel, as in waybar

    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.groups, self.clients, self.active = {}, [], 1
        self.sel = None
        self.armed = 0          # GLib source id while "Remove" waits for its second click

    # ---- data ------------------------------------------------------------------------------------
    def load(self):
        self.groups = self.ws.groups()
        self.clients = self.ws.query("clients") or []
        self.active = (self.ws.query("activeworkspace") or {}).get("id", 1)

    def windows(self, n):
        return self.ws.windows(n, self.clients)

    def apps(self):
        """Open apps, one per class, for the "Use an open app" menu."""
        seen = {}
        for c in sorted(self.clients, key=lambda c: c["class"].lower()):
            if c["class"] and c["workspace"]["id"] > 0 and not c["class"].startswith(("panels.", "TUI.float")):
                seen.setdefault(c["class"], c)
        return list(seen.values())

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        return [button("Tidy", self.tidy, "flat", tooltip="Move every open window to its app's workspace (t)")]

    def build(self):
        self.load()
        if self.compact:
            return self.build_tile()
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", lambda _l, row: row and self.select(row.ws))
        self.rows = {}
        for n in range(1, 11):
            row = Gtk.ListBoxRow()
            row.ws = n
            self.rows[n] = row
            self.list.append(row)
        side = box(True, 0, scrolled(self.list) if self.compact else self.list, classes=("side",))
        side.set_size_request(360, -1)
        self.list.set_margin_top(8)

        self.detail = box(True, 0)
        for edge in ("start", "end", "top", "bottom"):
            getattr(self.detail, f"set_margin_{edge}")(20)
        self.paint_rows()
        start = self.active if 1 <= self.active <= 10 else 1
        self.list.select_row(self.rows[start])
        GLib.idle_add(lambda: self.rows[start].grab_focus() and False)
        return box(False, 0, side, scrolled(self.detail))

    def build_tile(self):
        """The Control Center card: the ten workspaces — dot on the current one, name, window
        class, a dot per open window — and the selected one's launch command below. Adding,
        editing and removing apps happen in the full window (a / e / d open it)."""
        self.hints = [("↑↓", "select"), ("a", "add app"), ("e", "edit"), ("d", "delete"), ("l", "launch"),
                      ("g", "go"), ("t", "tidy")]
        self.tile_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.tile_list.connect("row-selected", lambda _l, row: row and self.tile_select(row.ws))
        self.tile_list.connect("row-activated", lambda _l, row: self.ws.go(row.ws))
        self.tile_rows = {}
        for n in range(1, 11):
            row = Gtk.ListBoxRow()
            row.ws = n
            self.tile_rows[n] = row
            self.tile_list.append(row)
        self.tile_detail = box(True, 2)
        self.tile_detail.set_margin_top(10)
        self.paint_tile()
        self.sel = self.active if 1 <= self.active <= 10 else 1
        self.tile_list.select_row(self.tile_rows[self.sel])
        GLib.idle_add(lambda: self.tile_rows[self.sel].grab_focus() and False)
        return box(True, 0, self.tile_list, self.tile_detail)

    def paint_tile(self):
        names = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # the class column lines up
        for n, row in self.tile_rows.items():
            g, count = self.groups.get(n), len(self.windows(n))
            here = label("\u25cf" if n == self.active else " ", "accent")
            num = label(str(n % 10), "accent" if g else "dim")
            name = label(g["name"] if g else "—", "bold" if g else "dim")
            names.add_widget(name)
            cls = label(", ".join(g["cls"].split("|")) if g else "unassigned", "amber" if g else "dim", ellipsize=True)
            dots = (label("\u25cf" * min(count, 4), "green") if count else label("\u25cb", "dim"))
            line = box(False, 10, here, num, name, cls, dots)
            if count:
                line.append(label(str(count), "dim"))
            row.set_child(line)
        self.paint_tile_detail()

    def tile_select(self, n):
        self.sel = n
        self.paint_tile_detail()

    def paint_tile_detail(self):
        clear(self.tile_detail)
        n, g = self.sel or 1, self.groups.get(self.sel or 1)
        wins = len(self.windows(n))
        title = f"Workspace {n % 10}" + (f" · {g['name']}" if g else "")
        self.tile_detail.append(rule_heading(title, f"{wins} open" if wins else ""))
        if g:
            self.tile_detail.append(box(False, 12, label("launch", "dim"),
                                        label(" ; ".join(g["launch"]) or "nothing", ellipsize=True)))
        else:
            self.tile_detail.append(label("No app yet. Press a to put an open one here.", "dim"))

    def paint_rows(self):
        """The list, as the Control Center card draws it: dot on the current workspace, number,
        name, the window class in amber, a dot per open window."""
        names = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        for n, row in self.rows.items():
            g, count = self.groups.get(n), len(self.windows(n))
            name = label(g["name"] if g else "—", "bold" if g else "dim")
            names.add_widget(name)
            line = box(False, 10, label("\u25cf" if n == self.active else " ", "accent"),
                       label(str(n % 10), "accent" if g else "dim"), name,
                       label(", ".join(g["cls"].split("|")) if g else "unassigned", "amber" if g else "dim",
                             ellipsize=True),
                       label("\u25cf" * min(count, 4), "green") if count else label("\u25cb", "dim"))
            if count:
                line.append(label(str(count), "dim"))
            row.set_child(line)

    def select(self, n):
        self.sel = n
        self.disarm()
        self.paint_detail()

    def paint_detail(self):
        clear(self.detail)
        n, g = self.sel, self.groups.get(self.sel)
        wins = self.windows(n)

        eyebrow = label(f"Workspace {n % 10}" + ("  ·  current" if n == self.active else ""), "section", "flush")
        eyebrow.set_margin_bottom(2)
        self.detail.append(eyebrow)
        self.detail.append(label(g["name"] if g else "Empty", "heading", *([] if g else ["dim"])))

        grid = Gtk.Grid(column_spacing=14, row_spacing=8)
        grid.set_margin_top(16)
        self.fields = {}
        rows = [("name", "Name", g["name"] if g else "", ""),
                ("cls", "Window class", g["cls"] if g else "", "several apps: a|b"),
                ("launch", "Launch", " ; ".join(g["launch"]) if g else "", "commands, separated by ;"),
                ("new", "Super + N", (g.get("new") or "") if g else "", "empty: first launch command")]
        for i, (key, title, value, hint) in enumerate(rows):
            grid.attach(label(title, "dim"), 0, i, 1, 1)
            entry = Gtk.Entry(text=value, placeholder_text=hint, hexpand=True)
            entry.connect("activate", lambda *_: self.save())
            self.fields[key] = entry
            grid.attach(entry, 1, i, 1, 1)
        self.detail.append(grid)

        pick = Gtk.MenuButton(label="Use an open app", tooltip_text="Put a running app on this workspace")
        pick.set_popover(self.app_menu())
        launch = button("Launch", self.launch, tooltip="Start its launch commands (l)")
        launch.set_sensitive(bool(g and g["launch"]))
        self.remove_btn = button("Remove", self.remove, tooltip="Unassign this workspace (Delete)")
        self.remove_btn.set_sensitive(bool(g))
        spacer = label("")
        spacer.set_hexpand(True)
        save = button("Save", self.save, "primary", tooltip="Enter in a field, or Ctrl + S")
        go = button("Go", self.go, tooltip="Switch to this workspace (g)")
        if self.compact:   # a Control Center tile: two shorter rows
            acts = box(True, 8, box(False, 8, save, launch, go), box(False, 8, pick, spacer, self.remove_btn))
        else:
            acts = box(False, 8, save, launch, go, pick, spacer, self.remove_btn)
        acts.set_margin_top(14)
        self.detail.append(acts)

        self.detail.append(rule_heading("Open windows", str(len(wins)), spaced=True))
        if not wins:
            self.detail.append(label("none", "dim"))
        win_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        win_list.set_activate_on_single_click(True)
        win_list.connect("row-activated", lambda _l, row: self.focus(row.address))
        for i, w in enumerate(wins, 1):
            line = box(False, 12, label(f"Alt+{i % 10}", "dim"), label(w["title"] or w["class"], ellipsize=True))
            row = Gtk.ListBoxRow(child=line, tooltip_text="Click to focus this window")
            row.address = w["address"]
            win_list.append(row)
        self.detail.append(win_list)

    def app_menu(self):
        pop = Gtk.Popover()
        menu = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        menu.set_activate_on_single_click(True)
        for c in self.apps():
            owner = next((ws for ws, g in self.groups.items() if self.ws.matches(c, g["cls"])), None)
            line = box(False, 12, label(c["class"]),
                       label(f"workspace {owner % 10}" if owner else "", "dim"))
            row = Gtk.ListBoxRow(child=line)
            row.app = c
            menu.append(row)

        def picked(_l, row):
            pop.popdown()
            self.use_app(row.app)
        menu.connect("row-activated", picked)
        pop.set_child(menu if menu.get_first_child() else label("no apps open", "dim"))
        return pop

    # ---- actions ---------------------------------------------------------------------------------
    def refresh(self):
        """Every 2 s: counts and window list follow what's open. The form is left alone while
        it's being edited, so a refresh never eats what you typed."""
        old = (self.groups, [(c["address"], c["workspace"]["id"], c["title"]) for c in self.clients], self.active)
        self.load()
        new = (self.groups, [(c["address"], c["workspace"]["id"], c["title"]) for c in self.clients], self.active)
        if new != old and self.compact:
            self.paint_tile()
            return
        elif new != old:
            self.paint_rows()
            if not self.typing() and not self.edited():
                self.paint_detail()

    def edited(self):
        g = self.groups.get(self.sel) or {}
        now = self.form()
        return (now["name"], now["cls"], now["launch"], now["new"]) != \
            (g.get("name", ""), g.get("cls", ""), g.get("launch", []), g.get("new", "") or "")

    def form(self):
        f = {k: e.get_text().strip() for k, e in self.fields.items()}
        f["launch"] = [c.strip() for c in f["launch"].split(";") if c.strip()]
        return f

    def reload(self, msg, kind="ok"):
        self.load()
        if self.compact:
            self.paint_tile()
        else:
            self.paint_rows()
            self.paint_detail()
        self.say(msg, kind)

    def save(self):
        f = self.form()
        if not f["name"] or not f["cls"]:
            self.say("Name and window class are both needed", "bad")
            return
        try:
            re.compile(f["cls"])
        except re.error:
            self.say(f"Not a valid class pattern: {f['cls']}", "bad")
            return
        self.groups = self.ws.groups()
        self.groups[self.sel] = {"name": f["name"], "cls": f["cls"], "launch": f["launch"], "new": f["new"]}
        self.ws.write_conf(self.groups)
        self.reload(f"Saved workspace {self.sel % 10}")

    def use_app(self, c):
        groups = self.ws.groups()
        err = self.ws.assign(groups, self.sel, c["class"], c.get("pid"))
        if err:
            self.say(err, "bad")
            return
        self.ws.write_conf(groups)
        self.reload(f"{c['class']} now opens on workspace {self.sel % 10}")

    def launch(self):
        started = self.ws.launch(self.sel, force=True)
        self.say("Launched" if started else "Nothing to launch: set a launch command first",
                 "ok" if started else "bad")

    def go(self):
        self.ws.go(self.sel)
        self.close()

    def focus(self, address):
        self.ws.dispatch(f'hl.dsp.focus({{ window = "address:{address}" }})')
        self.close()

    def tidy(self):
        moved = self.ws.tidy()
        GLib.timeout_add(300, lambda: self.reload(f"Moved {moved} window(s) to their workspaces") and False)

    def remove(self):
        if not self.armed:      # first click arms it, a second one within 3 s removes
            self.remove_btn.set_label("Click again to remove")
            self.remove_btn.add_css_class("armed")
            self.armed = GLib.timeout_add(3000, self.disarm)
            return
        self.disarm()
        name = self.groups[self.sel]["name"]
        groups = self.ws.groups()
        groups.pop(self.sel, None)
        self.ws.write_conf(groups)
        self.reload(f"Removed {name} from workspace {self.sel % 10}")

    def disarm(self):
        if self.armed:
            GLib.source_remove(self.armed)
            self.armed = 0
        if getattr(self, "remove_btn", None):
            self.remove_btn.set_label("Remove")
            self.remove_btn.remove_css_class("armed")
        return False

    def key(self, keyval, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.save()
            return True
        if self.typing():
            return False
        name = Gdk.keyval_name(keyval) or ""
        if self.compact:   # the card: go / launch / tidy here, editing in the full window
            if name.isdigit():
                self.tile_list.select_row(self.tile_rows[int(name) or 10])
            elif name in ("g", "Return"):
                self.ws.go(self.sel)
            elif name == "l":
                self.say("Launched" if self.ws.launch(self.sel, force=True) else "Nothing to launch")
            elif name == "t":
                self.tidy()
            elif name in ("a", "e", "d"):
                subprocess.Popen([os.path.expanduser(self.popup)], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                return False
            return True
        if name.isdigit():
            self.list.select_row(self.rows[int(name) or 10])
            return True
        actions = {"l": self.launch, "g": self.go, "t": self.tidy, "Delete": self.remove}
        if name in actions:
            actions[name]()
            return True
        return False


def make(ws=None):
    """The view, for run() here and for the Control Center."""
    return Workspaces(ws or load_wsgroups())


def main(ws=None):
    run(make(ws), "panels.workspaces", (900, 560))


if __name__ == "__main__":
    main()
