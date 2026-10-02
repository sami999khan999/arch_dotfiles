#!/usr/bin/env python3
# agentpickgui.py — agentmux's two pickers as GTK popups (Tokyo Night, the blurred backdrop of every
# panel). agentmux runs them (`agentmux open`, Ctrl+Alt+N with more than one agent); running one
# again closes it. The choice goes back to agentmux: `agentmux project <folder>` / `new-thread <agent>`.
#   agentpickgui.py harness   New thread: which agent (the installed ones; the others shown greyed)
#   agentpickgui.py project   Open project: a folder browser. The first time it asks for the projects
#                             folder (the root, remembered in agentmux's state.json); after that it opens there.
# Type to filter (the shortcut list's fuzzy matching; matched letters in blue), ↑↓ to move, ↵ or a
# click to choose, Esc to clear the search, then close.
# Browser: a click or → goes into a folder, ← / Backspace goes up (above the root too, to open a
# project anywhere), ↵ opens the selected folder, Ctrl+R makes the shown folder the root. A query
# starting with / or ~ is a path: it lists that folder's matching subfolders.
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agentmux"))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, run, scrolled
from keys import fuzzy
from keysgui import marked
import lib

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
HIT = "#7aa2f7"   # matched letters, as in the shortcut list
SKIP = {"node_modules", "dist", "build", ".next", "target", ".venv", "venv", "vendor", "__pycache__"}
SEARCH_DEPTH = 3       # how deep a query searches below the shown folder
SEARCH_LIMIT = 4000    # folders indexed for a query, at most
CSS = """
.pick-list > row { padding: 7px 12px; }
.pick-list > row.off { opacity: .45; }
.pick-glyph { min-width: 18px; }
.crumbs button { padding: 2px 6px; min-height: 0; }
"""


def tilde(path):
    return "~" + path[len(lib.HOME):] if path == lib.HOME or path.startswith(lib.HOME + "/") else path


def subdirs(path):
    """The visible subfolders of path, sorted (case-insensitive)."""
    try:
        with os.scandir(path) as it:
            names = [e.name for e in it if not e.name.startswith(".") and e.name not in SKIP
                     and e.is_dir(follow_symlinks=True)]
    except OSError:
        return []
    return [os.path.join(path, n) for n in sorted(names, key=str.lower)]


def is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def branch(path):
    """The checked-out branch of a git repo ("" if not one), read from .git/HEAD."""
    try:
        head = open(os.path.join(path, ".git", "HEAD")).read().strip()
    except OSError:   # not a repo, a worktree (.git is a file), or unreadable
        return ""
    return head[16:] if head.startswith("ref: refs/heads/") else head[:7]


class Picker(View):
    """A search line over a list; subclasses fill rows() and say what a choice does."""
    css = CSS
    interval = 0
    placeholder = "type to filter"

    def __init__(self):
        super().__init__()
        self.items = []   # what each list row stands for (None: not choosable)

    def build(self):
        self.search = Gtk.Entry(placeholder_text=self.placeholder, hexpand=True)
        self.search.connect("changed", lambda *_: self.paint())
        self.search.connect("activate", lambda *_: self.choose_selected())
        early = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        early.connect("key-pressed", lambda _c, keyval, _code, state: self.key_early(keyval, state))
        self.search.add_controller(early)   # before the text field turns ← → Backspace into editing
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("pick-list")
        self.list.connect("row-activated", lambda _l, row: self.clicked(row.get_index()))
        self.scroll = scrolled(self.list)
        self.scroll.set_margin_top(10)
        page = box(True, 0, *self.above(), self.search, self.scroll)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        self.paint()
        GLib.idle_add(lambda: self.search.grab_focus() and False)   # once the window is up
        return page

    def above(self):
        """Widgets over the search line."""
        return []

    def key_early(self, keyval, state):
        """A key in the search line, before it edits with it. True: used here."""
        return False

    def paint(self, select=0):
        clear(self.list)
        self.items = []
        for item, child, on in self.rows(self.search.get_text().strip()):
            row = Gtk.ListBoxRow(child=child, activatable=on, selectable=on)
            if not on:
                row.add_css_class("off")
            self.list.append(row)
            self.items.append(item if on else None)
        self.select(select)

    def select(self, i):
        on = [n for n, it in enumerate(self.items) if it is not None]
        if not on:
            return
        n = min(on, key=lambda n: abs(n - i))
        row = self.list.get_row_at_index(n)
        self.list.select_row(row)
        GLib.idle_add(lambda: self.reveal(row) and False)

    def reveal(self, row):
        """Scroll the list so the selected row is in view."""
        ok, rect = row.compute_bounds(self.list)
        if not ok:
            return
        adj = self.scroll.get_vadjustment()
        top, bottom = rect.get_y(), rect.get_y() + rect.get_height()
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())

    def selected(self):
        row = self.list.get_selected_row()
        return row.get_index() if row else -1

    def move(self, d):
        """d rows that way (PgUp / PgDn: 10), to a choosable one; it stops at the ends."""
        here = self.selected()
        if here < 0 or not self.items:
            return
        i = max(0, min(here + d, len(self.items) - 1))
        while self.items[i] is None and i != here:   # back towards where it was
            i += -1 if d > 0 else 1
        self.select(i)

    def choose_selected(self):
        i = self.selected()
        if 0 <= i < len(self.items) and self.items[i] is not None:
            self.choose(self.items[i])

    def clicked(self, i):
        self.choose(self.items[i])

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Down:
            self.move(1)
        elif keyval == Gdk.KEY_Up:
            self.move(-1)
        elif keyval == Gdk.KEY_Page_Down:
            self.move(10)
        elif keyval == Gdk.KEY_Page_Up:
            self.move(-10)
        elif keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
        else:
            return False
        return True

    def done(self, *args):
        """Hand the choice to agentmux and close."""
        subprocess.Popen([AGENTMUX, *args], start_new_session=True)
        self.close()


# ---- New thread: which agent --------------------------------------------------------------------
class Harness(Picker):
    title, subtitle = "New thread", "which agent?"
    icon = "\U000f06a9"
    hints = [("Enter", "start"), ("↑↓", "move"), ("Esc", "cancel")]

    def rows(self, q):
        installed = {k for k, _ in lib.installed_harnesses()}
        last = lib.load_state().get("harness")
        found = []
        for k, (name, cmd) in lib.HARNESSES.items():
            m = fuzzy(q, name, cmd) if q else (0, set(), set())
            if m:
                found.append((-m[0], k, name, cmd, m[1], m[2]))
        found.sort(key=lambda f: (f[0], f[1] not in installed))
        for _, k, name, cmd, nh, ch in found:
            on = k in installed
            text = box(True, 2, label(marked(name, nh, HIT), "bold", markup=True),
                       label(marked(cmd, ch, HIT) if on else "not installed", "setting-sub", markup=on))
            text.set_hexpand(True)
            line = box(False, 12, label("✳", "accent" if on else "dim", "pick-glyph"), text)
            if on and k == last:
                line.append(label("last used", "green"))
            yield k, line, on

    def choose(self, harness):
        self.done("new-thread", harness)


# ---- Open project: a folder browser -------------------------------------------------------------
class Project(Picker):
    title = "Open project"
    icon = ""
    placeholder = "search, or type a path"

    def __init__(self):
        super().__init__()
        self.root = lib.projects_root()
        self.setup = self.root is None            # first run: choosing the projects folder
        self.cwd = self.root or lib.HOME
        self.children = subdirs(self.cwd)
        self.opened = set(lib.projects())
        self.index = {}                           # folder -> every folder below it (for search), built lazily
        if self.setup:
            self.title = "Choose your projects folder"
        self.subtitle = "" if self.setup else "root " + tilde(self.root)
        self.set_hints()

    def set_hints(self):
        self.hints = [("Enter", "use as root" if self.setup else "open"), ("→ / click", "into"),
                      ("←", "back"), *([] if self.setup else [("Ctrl+R", "make root")]), ("Esc", "close")]

    def above(self):
        self.crumbs = box(False, 2, classes=("crumbs",))
        self.crumbs.set_margin_bottom(12)
        return [self.crumbs]

    def build(self):
        page = super().build()
        self.go(self.cwd)
        return page

    # ---- moving around ----
    def go(self, path, select=None):
        self.cwd = os.path.abspath(os.path.expanduser(path))
        self.children = subdirs(self.cwd)
        self.draw_crumbs()
        if self.search.get_text():
            self.search.set_text("")   # repaints
        else:
            self.paint()
        i = next((n for n, it in enumerate(self.items) if it and it[1] == select), None)
        if i is None:   # the first folder, not "open this folder"
            i = 1 if self.children else 0
        self.select(i)

    def up(self):
        parent = os.path.dirname(self.cwd)
        if parent != self.cwd:
            self.go(parent, select=self.cwd)

    def draw_crumbs(self):
        """~ › code › starter-kits, each part a button that goes there."""
        clear(self.crumbs)
        parts, path = [], self.cwd
        while True:
            parts.append(path)
            parent = os.path.dirname(path)
            if path == lib.HOME or parent == path:
                break
            path = parent
        for k, p in enumerate(reversed(parts)):
            if k:
                self.crumbs.append(label("›", "dim"))
            name = tilde(p) if p == lib.HOME or os.path.dirname(p) == p else os.path.basename(p)
            b = button(name, lambda p=p: self.go(p), "flat")
            if p == self.cwd:
                b.add_css_class("bold")
            self.crumbs.append(b)

    # ---- the list ----
    def rows(self, q):
        if not q:
            here = "Use this folder as the projects root" if self.setup else "Open this folder"
            line = box(False, 12, label("", "green", "pick-glyph"), label(here, "green"),
                       label(tilde(self.cwd), "dim", xalign=1.0, ellipsize=True))
            yield ("here", self.cwd), line, True
            for p in self.children:
                yield ("dir", p), self.folder(p, os.path.basename(p), ""), True
            return
        if q[0] in "/~":   # a path: the typed folder's subfolders that start with what follows the last /
            base, _, prefix = os.path.expanduser(q).rpartition("/")
            base = base or "/"
            for p in subdirs(base):
                name = os.path.basename(p)
                if name.lower().startswith(prefix.lower()):
                    yield ("dir", p), self.folder(p, name, tilde(base), set(range(len(prefix)))), True
            return
        found = []
        for p in self.search_index():
            rel = os.path.relpath(p, self.cwd)
            m = fuzzy(q, os.path.basename(p), rel)
            if m:
                found.append((m[0] - rel.count("/") * 5, p, m[1], m[2], rel))
        found.sort(key=lambda f: -f[0])
        for _, p, nh, dh, rel in found[:300]:
            yield ("dir", p), self.folder(p, os.path.basename(p), rel, nh, dh), True

    def folder(self, path, name, where, nh=(), dh=()):
        """A folder's row: ● if it's an open project, a branch glyph for a git repo; where it is
        (search results) under the name; the branch at the right."""
        b = branch(path)
        if path in self.opened:
            glyph = label("●", "accent", "pick-glyph")
        else:
            glyph = label("" if b else "", "amber" if b else "dim", "pick-glyph")
        text = box(True, 2, label(marked(name, nh, HIT), *(("bold",) if b else ()), markup=True))
        if where:
            text.append(label(marked(where, dh, HIT), "setting-sub", markup=True, ellipsize=True))
        text.set_hexpand(True)
        return box(False, 12, glyph, text, label(b, "dim"))

    def search_index(self):
        """Every folder up to SEARCH_DEPTH below the shown one (cached per folder)."""
        if self.cwd not in self.index:
            out, todo = [], [(self.cwd, 0)]
            while todo and len(out) < SEARCH_LIMIT:
                path, depth = todo.pop(0)
                for p in subdirs(path):
                    out.append(p)
                    if depth + 1 < SEARCH_DEPTH and not is_repo(p):   # a repo's insides aren't projects
                        todo.append((p, depth + 1))
            self.index[self.cwd] = out
        return self.index[self.cwd]

    # ---- choosing ----
    def choose(self, item, into=False):
        kind, path = item
        typed_path = self.search.get_text().strip()[:1] in ("/", "~")
        if kind == "dir" and (into or (typed_path and not self.setup)):
            return self.go(path)   # a typed path: ↵ goes into the folder, so the path can be extended
        if self.setup:   # the root is set; now browse it and pick a project
            lib.set_projects_root(path)
            self.root, self.setup = path, False
            self.window.set_title(Project.title)
            self.set_subtitle("root " + tilde(path))
            self.set_hints()
            self.host.show_hints()
            self.say(f"Projects folder: {tilde(path)}")
            return self.go(path)
        self.done("project", path)

    def clicked(self, i):
        self.choose(self.items[i], into=True)

    def key_early(self, keyval, state):
        text = self.search.get_text()
        if keyval == Gdk.KEY_Right and self.search.get_position() == len(text):   # the caret at the end
            i = self.selected()
            if i >= 0 and self.items[i]:
                self.choose(self.items[i], into=True)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_BackSpace) and not text:
            self.up()
            return True
        return False

    def key(self, keyval, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and keyval in (Gdk.KEY_r, Gdk.KEY_R) and not self.setup:
            lib.set_projects_root(self.cwd)
            self.root = self.cwd
            self.set_subtitle("root " + tilde(self.cwd))
            self.say(f"Projects folder is now {tilde(self.cwd)}")
            return True
        return super().key(keyval, state)


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "project"
    if kind == "harness":
        run(Harness(), "panels.newthread", (620, 440))
    else:
        run(Project(), "panels.openproject", (820, 600))


if __name__ == "__main__":
    main()
