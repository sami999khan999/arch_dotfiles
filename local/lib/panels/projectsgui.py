#!/usr/bin/env python3
# projectsgui.py — the Projects panel (Super + Ctrl + P): every project in ~/code, from the `projects`
# list (~/code/.projects/projects.json, private), with its git details. Running it again closes it.
#
#   left   the ~/code tree: groups fold (←/→, Enter or a click on the group); a repo shows its branch and
#          a dot when it has uncommitted changes; a cloud for a listed project that isn't cloned on this
#          PC; ⚠ a local-only folder (no git remote: only the SSD and the HDD backup have it)
#   right  the selected project: branch, to push / to pull, changed files, last fetch, remotes; open it in
#          VS Code, a terminal or agentmux; Fetch, Pull (fast-forward only), Push, GitHub. Two tabs:
#          Graph (the history of every branch as lanes, like VS Code's Git Graph; or one branch) and
#          Branches (each local branch next to its remote: in sync / to push / to pull / diverged…).
#   + New  a new project (empty, from a template, or cloned from a URL; optionally a GitHub repo), recorded
#          in the list. Clone missing: the listed projects that aren't on this PC, with progress.
#   setup  on a PC without the list: restore it from its repo URL and clone everything, or start a new one.
#
# Opening runs `projects scan` in the background (the list follows ~/code); selecting a project fetches
# it if its last fetch is over 10 minutes old, so "to pull" is current. Every git call runs in a thread.
# The data, clone, new and pull are the `projects` command's.
import importlib.machinery, importlib.util, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gtkkit import (Gdk, GLib, Gtk, View, box, button, clear, dropdown, label, recolor, rgbf, rule_heading,
                    run, scrolled, switch)
from keys import fuzzy
from keysgui import marked

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
HIT = recolor("#7aa2f7")   # matched letters, as in the shortcut list
HISTORY = 300              # commits in the graph
STALE = 600                # seconds: an older fetch is redone when the project is selected
FOLDED = {"archive", "templates", "forks"}   # groups that start folded
G_OPEN, G_SHUT = "", ""         # folder open / closed
G_REPO, G_MISSING, G_LOCAL = "", "", ""   # git branch, cloud download, warning
# graph: lane colours (Tokyo Night; the theme recolours them), lane width and row height in px
LANES = ["#7aa2f7", "#bb9af7", "#9ece6a", "#ff9e64", "#7dcfff", "#e0af68", "#73daca", "#f7768e"]
LANE_W, ROW_H, MAX_LANES = 14, 24, 10
NEW_FOLDER = "New folder…"
CSS = """
.tree > row { padding: 3px 10px; }
.tree > row.group-row { padding-top: 6px; }
.pj-glyph { min-width: 18px; }
.graph > row { padding: 0 10px 0 0; min-height: 24px; }
.graph > row:hover, .branch-list > row:hover { background: alpha(#292e42, .45); }
.branch-list > row { padding: 5px 4px; }
.hash { color: #e0af68; }
.chip { padding: 0 5px; font-size: 9pt; }
.chip-local { background: alpha(#6b8fe0, .2); color: #7aa2f7; }
.chip-remote { background: alpha(#565f89, .2); color: #565f89; }
.chip-remote.same { color: #a9b1d6; }
.chip-head { color: #9ece6a; border: 1px solid alpha(#9ece6a, .6); }
.chip-tag { background: alpha(#e0af68, .14); color: #e0af68; }
.state { padding: 1px 7px; background: alpha(#c0caf5, .06); }
.form-label { color: #a9b1d6; min-width: 120px; }
.tabs.inner { padding: 0; margin-top: 14px; }
"""


# ---- the graph ----------------------------------------------------------------------------------
def graph_rows(commits):
    """Lanes for a topo-ordered history ([{"hash", "parents"}], newest first), one dict per commit:
    col (its lane), colour (an index), top / bottom: the lines in the row's upper / lower half as
    (from lane, to lane, colour index), width (lanes used). A lane is the commit it waits for: a commit
    takes the lane waiting for it (the others waiting join it), its first parent continues that lane
    and each other parent joins the lane already waiting for it, or opens one."""
    lanes, colours, rows, nxt = [], [], [], 0

    def free_lane():
        if None in lanes:
            return lanes.index(None)
        lanes.append(None)
        colours.append(0)
        return len(lanes) - 1

    for c in commits:
        h, parents = c["hash"], c["parents"]
        waiting = [i for i, x in enumerate(lanes) if x == h]
        if waiting:
            col = waiting[0]
        else:                       # a branch tip: a new lane
            col = free_lane()
            colours[col], nxt = nxt, nxt + 1
        top = [(i, col if x == h else i, colours[i]) for i, x in enumerate(lanes) if x is not None]
        colour, width = colours[col], len(lanes)
        for i in waiting:
            lanes[i] = None
        bottom = [(i, i, colours[i]) for i, x in enumerate(lanes) if x is not None]
        if parents:
            lanes[col] = parents[0]
            bottom.append((col, col, colour))
            for p in parents[1:]:
                if p in lanes:
                    j = lanes.index(p)
                else:
                    j = free_lane()
                    lanes[j], colours[j], nxt = p, nxt, nxt + 1
                bottom.append((col, j, colours[j]))
        width = max(width, len(lanes))
        while lanes and lanes[-1] is None:
            lanes.pop()
            colours.pop()
        rows.append({"col": col, "colour": colour, "top": top, "bottom": bottom, "width": width})
    return rows


def lane_x(i):
    return 9 + i * LANE_W


def draw_graph_row(cr, h, r, is_head):
    mid = h / 2
    cr.set_line_width(1.6)

    def edge(a, b, colour, y0, y1):
        if a >= MAX_LANES or b >= MAX_LANES:
            return
        cr.set_source_rgb(*rgbf(LANES[colour % len(LANES)]))
        cr.move_to(lane_x(a), y0)
        if a == b:
            cr.line_to(lane_x(b), y1)
        else:
            ym = (y0 + y1) / 2
            cr.curve_to(lane_x(a), ym, lane_x(b), ym, lane_x(b), y1)
        cr.stroke()
    for a, b, c in r["top"]:
        edge(a, b, c, 0, mid)
    for a, b, c in r["bottom"]:
        edge(a, b, c, mid, h)
    if r["col"] < MAX_LANES:
        x = lane_x(r["col"])
        cr.arc(x, mid, 4.5, 0, 6.3)
        if is_head:   # HEAD: a ring
            cr.set_source_rgb(*rgbf("#1a1b26"))
            cr.fill_preserve()
            cr.set_line_width(2.2)
        cr.set_source_rgb(*rgbf(LANES[r["colour"] % len(LANES)]))
        cr.stroke() if is_head else cr.fill()


def ref_chips(decor, remote_names, local_names, most=3):
    """%D ("HEAD -> dev, origin/dev, tag: v1") as chip labels: the first few, then "+N" (a commit many
    branches point at would otherwise push the subject out)."""
    chips = []
    for ref in filter(None, (r.strip() for r in decor.split(","))):
        if ref.startswith("HEAD -> "):
            chips.append(label("HEAD", "chip", "chip-head"))
            chips.append(label(ref[8:], "chip", "chip-local"))
        elif ref == "HEAD":
            chips.append(label("HEAD", "chip", "chip-head"))
        elif ref.startswith("tag: "):
            chips.append(label(ref[5:], "chip", "chip-tag"))
        elif ref.partition("/")[0] in remote_names:
            if ref.endswith("/HEAD"):
                continue
            same = ref.partition("/")[2] in local_names   # the local branch is here too: in step
            chips.append(label(ref, "chip", "chip-remote", *(("same",) if same else ())))
        else:
            chips.append(label(ref, "chip", "chip-local"))
    if len(chips) > most:
        names = ", ".join(c.get_text() for c in chips[most:])
        chips = chips[:most] + [label(f"+{len(chips) - most}", "chip", "chip-remote")]
        chips[-1].set_tooltip_text(names)
    for c in chips:   # cut here: an ellipsizing label would be squeezed to "…" by the subject next to it
        c.set_text(short(c.get_text(), 20))
    return chips


# ---- small helpers ------------------------------------------------------------------------------
def short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def web_url(url):
    """The repo's web page for an https or git@ remote ("" if it isn't a known host)."""
    u = url.removesuffix(".git")
    if u.startswith("git@"):
        host, _, path = u[4:].partition(":")
        u = f"https://{host}/{path}"
    return u if u.startswith("https://") else ""


def git_lines(path, *args):
    ok, out = pj.git(path, *args)
    return out.splitlines() if ok and out else []


def ago(secs):
    if secs is None:
        return "never fetched"
    for n, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if secs >= n:
            return f"fetched {int(secs // n)} {unit} ago"
    return "fetched just now"


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def branch_state(b):
    """(text, colour class) for a branch_states() row."""
    if not b["local"]:
        return "remote only", "dim"
    if b["gone"]:
        return "remote deleted", "red"
    if not b["upstream"]:
        return "local only", "amber"
    if b["ahead"] and b["behind"]:
        return f"↑{b['ahead']} ↓{b['behind']} diverged", "red"
    if b["ahead"]:
        return f"↑{b['ahead']} to push", "amber"
    if b["behind"]:
        return f"↓{b['behind']} to pull", "cyan"
    return "in sync", "green"


def children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


class Project:
    """One project row: rel (path under ~/code), kind repo / missing / local, the list's entry."""
    def __init__(self, rel, kind, entry):
        self.rel, self.kind, self.entry = rel, kind, entry
        self.path = f"{pj.ROOT}/{rel}"
        self.name = os.path.basename(rel)
        self.dirty = 0
        self.branch = pj.current_branch(self.path) if kind == "repo" else ""


def load_projects():
    data = pj.load()
    out = {}
    for e in data["projects"]:
        out[e["path"]] = Project(e["path"], "repo" if pj.is_repo(f"{pj.ROOT}/{e['path']}") else "missing", e)
    for e in data["local_only"]:
        out[e["path"]] = Project(e["path"], "local", e)
    return [out[k] for k in sorted(out)]


class Projects(View):
    title, subtitle = "Projects", ""
    icon = ""
    interval = 0
    css = CSS
    hints = [("Enter", "VS Code"), ("t", "terminal"), ("a", "agent"), ("f", "fetch"), ("n", "new"),
             ("Tab", "graph / branches"), ("←→", "fold"), ("/", "search"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.projects = load_projects()
        self.folded = set(FOLDED)
        self.selected = None          # rel of the project shown on the right
        self.ref = ""                 # the graph's branch ("" = every branch)
        self.gen = 0                  # detail loads: only the newest one draws
        self.rows = {}                # rel -> (row, dot label) for in-place updates
        self.fetched = set()          # projects fetched since the panel opened
        self.fetching = set()
        self.busy = False             # a clone / create is running
        self.stop = None              # the clone run's stop event
        self.start = None             # the New form's "Start from" (None until it's built)

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        self.new_btn = button("+ New", self.show_new, "flat", tooltip="n: a new project")
        self.missing_btn = button("", self.clone_missing, "flat", tooltip="clone the listed projects that aren't here")
        self.update_counts()
        return [self.missing_btn, self.new_btn]

    def build(self):
        self.search = Gtk.Entry(placeholder_text="/ search", hexpand=True)
        self.search.connect("changed", lambda *_: self.paint_tree())
        self.search.connect("activate", lambda *_: self.focus_list())
        down = Gtk.EventControllerKey()
        down.connect("key-pressed", lambda _c, k, _code, _s: k == Gdk.KEY_Down and self.focus_list())
        self.search.add_controller(down)
        self.tree = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.tree.add_css_class("tree")
        self.tree.set_activate_on_single_click(False)
        self.tree.connect("row-selected", lambda _l, row: row and self.picked(row))
        self.tree.connect("row-activated", lambda _l, row: self.activated(row))
        click = Gtk.GestureClick()   # a single click on a group folds it
        click.connect("released", self.tree_clicked)
        self.tree.add_controller(click)
        self.search.set_margin_bottom(8)
        left = box(True, 0, self.search, scrolled(self.tree))
        left.add_css_class("side")
        left.set_size_request(400, -1)
        left.set_hexpand(False)   # its rows expand; without this the column takes a share of the spare width
        for edge in ("start", "end", "top"):
            getattr(left, f"set_margin_{edge}")(14)

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True,
                               hhomogeneous=False)
        self.pages.add_named(self.build_detail(), "detail")
        self.pages.add_named(self.build_new(), "new")
        self.pages.add_named(self.build_setup(), "setup")
        self.pages.add_named(self.build_progress(), "progress")
        self.pages.add_named(box(True, 0, label("Pick a project on the left.", "dim")), "empty")
        for name in ("detail", "new", "setup", "progress", "empty"):
            page = self.pages.get_child_by_name(name)
            for edge in ("start", "end", "top"):
                getattr(page, f"set_margin_{edge}")(20)

        self.paint_tree()
        if os.path.exists(pj.LIST):
            self.pages.set_visible_child_name("empty")
            threading.Thread(target=self.background, daemon=True).start()
        else:
            self.show_setup()
        GLib.idle_add(lambda: (self.update_counts(), self.focus_list()) and False)
        return box(False, 0, left, self.pages)

    def build_detail(self):
        self.heading = label("", "heading", ellipsize=True)
        self.where = label("", "dim", ellipsize=True)
        self.chips = box(False, 8)
        self.chips.set_margin_top(8)
        self.urls = box(True, 2)
        self.urls.set_margin_top(8)
        self.actions = box(False, 8)
        self.actions.set_margin_top(14)
        self.note = label("", "amber", wrap=True)
        self.note.set_margin_top(12)

        self.tab_btns = {}
        bar = box(False, 0, classes=("tabs", "inner"))
        for name, title in (("graph", "Graph"), ("branches", "Branches")):
            b = button(title, lambda n=name: self.switch_tab(n), "tab")
            self.tab_btns[name] = b
            bar.append(b)
        self.filter_box = box(False, 8)
        self.filter_box.set_margin_start(16)
        self.filter_box.set_valign(Gtk.Align.CENTER)
        bar.append(self.filter_box)
        self.tabs_bar = bar

        self.graph = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.graph.add_css_class("graph")
        self.branch_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.branch_list.add_css_class("branch-list")
        self.branch_list.connect("row-activated", lambda _l, row: self.show_graph_of(row.ref, tab=True))
        self.tab_pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True,
                                   hhomogeneous=False)   # sized by the shown tab, not the widest one
        self.tab_pages.add_named(scrolled(self.graph), "graph")
        self.tab_pages.add_named(scrolled(self.branch_list), "branches")
        self.tab_pages.set_margin_top(6)
        self.switch_tab("graph")
        return box(True, 0, self.heading, self.where, self.chips, self.urls, self.actions, self.note,
                   self.tabs_bar, self.tab_pages)

    def switch_tab(self, name):
        self.tab = name
        self.tab_pages.set_visible_child_name(name)
        for n, b in self.tab_btns.items():
            (b.add_css_class if n == name else b.remove_css_class)("on")
        self.filter_box.set_visible(name == "graph")

    def focus_list(self):
        row = self.tree.get_selected_row() or self.first_project() or self.tree.get_row_at_index(0)
        if row:
            self.tree.select_row(row)
            row.grab_focus()
        return True

    def first_project(self):
        """The first project row (the panel opens on it rather than on the first group)."""
        i = 0
        while (row := self.tree.get_row_at_index(i)) is not None:
            if row.kind == "project":
                return row
            i += 1
        return None

    def update_counts(self):
        n = {k: sum(p.kind == k for p in self.projects) for k in ("repo", "missing", "local")}
        bits = [f"{n['repo']} repos"]
        if n["local"]:
            bits.append(f"{n['local']} local only")
        if self.host:
            self.set_subtitle(" · ".join(bits) if self.projects else "no list yet")
        self.missing_btn.set_label(f"Clone missing ({n['missing']})")
        self.missing_btn.set_visible(n["missing"] > 0)
        self.new_btn.set_visible(os.path.exists(pj.LIST))

    # ---- background: scan the folder, then each repo's changed files ------------------------------
    def background(self):
        lines = pj.scan(quiet=True)
        if lines:
            GLib.idle_add(self.reload)
        self.count_changes()

    def reload(self, select=None):
        self.projects = load_projects()
        if select:
            self.selected = None
            parts = select.split("/")
            for i in range(1, len(parts)):
                self.folded.discard("/".join(parts[:i]))
        self.paint_tree()
        self.update_counts()
        if select:
            self.select_rel(select)
        threading.Thread(target=self.count_changes, daemon=True).start()
        return False

    def count_changes(self):
        repos = [p for p in self.projects if p.kind == "repo"]

        def one(p):
            p.dirty = len(git_lines(p.path, "status", "--porcelain"))
            return p
        with ThreadPoolExecutor(6) as pool:
            for p in pool.map(one, repos):
                if p.dirty:
                    GLib.idle_add(self.mark_dirty, p.rel)

    def mark_dirty(self, rel):
        if rel in self.rows:
            self.rows[rel][1].set_text("●")
        return False

    # ---- the tree --------------------------------------------------------------------------------
    def layout(self):
        """[(kind, key, depth, match)] in on-screen order: kind is group or project."""
        q = self.search.get_text().strip()
        if q:   # a flat list of matches, best first
            found = []
            for p in self.projects:
                m = fuzzy(q, p.name, p.rel)
                if m:
                    found.append((-m[0], p.rel, m))
            return [("project", rel, 0, m) for _, rel, m in sorted(found)]
        out, groups = [], set()
        for p in self.projects:
            parts = p.rel.split("/")
            hidden = False
            for i in range(1, len(parts)):
                g = "/".join(parts[:i])
                if g not in groups and not hidden:
                    groups.add(g)
                    out.append(("group", g, i - 1, None))
                if g in self.folded:
                    hidden = True
            if not hidden:
                out.append(("project", p.rel, len(parts) - 1, None))
        return out

    def paint_tree(self):
        clear(self.tree)
        self.rows = {}
        by_rel = {p.rel: p for p in self.projects}
        first = None
        for kind, key, depth, match in self.layout():
            if kind == "group":
                shut = key in self.folded
                n = sum(p.rel.startswith(key + "/") for p in self.projects)
                line = box(False, 8, label(G_SHUT if shut else G_OPEN, "accent", "pj-glyph"),
                           label(os.path.basename(key), "bold"), label(str(n), "dim"))
                row = Gtk.ListBoxRow(child=line)
                row.add_css_class("group-row")
                row.kind, row.key = "group", key
            else:
                line, dot = self.project_line(by_rel[key], match)
                row = Gtk.ListBoxRow(child=line)
                row.kind, row.key = "project", key
                self.rows[key] = (row, dot)
                first = first or row
            row.get_child().set_margin_start(depth * 16)
            self.tree.append(row)
            if key == self.selected:
                self.tree.select_row(row)
        if self.search.get_text().strip() and first:
            self.tree.select_row(first)

    def project_line(self, p, match):
        if p.kind == "repo":
            glyph, gclass, right, rclass = G_REPO, "amber", p.branch, "dim"
        elif p.kind == "missing":
            glyph, gclass, right, rclass = G_MISSING, "dim", "not cloned", "dim"
        else:
            glyph, gclass, right, rclass = G_LOCAL, "amber", "local only", "amber"
        if match:
            name = label(marked(p.name, match[1], HIT), markup=True, ellipsize=True)
            where = label(marked(os.path.dirname(p.rel), match[2], HIT), "setting-sub", markup=True, ellipsize=True)
            where.set_max_width_chars(10)
            text = box(True, 1, name, where)
        else:
            name = text = label(p.name, *(("dim",) if p.kind == "missing" else ()), ellipsize=True)
        name.set_max_width_chars(10)   # the column keeps its width: long names ellipsize instead
        text.set_hexpand(True)
        dot = label("●" if p.dirty else "", "amber")
        return box(False, 8, label(glyph, gclass, "pj-glyph"), text, dot, label(right, rclass)), dot

    def tree_clicked(self, gesture, _n, _x, y):
        row = self.tree.get_row_at_y(int(y))
        if row and row.kind == "group":
            self.fold(row.key)

    def fold(self, key, shut=None):
        shut = key not in self.folded if shut is None else shut
        (self.folded.add if shut else self.folded.discard)(key)
        self.paint_tree()
        self.select_rel(key)

    def select_rel(self, key):
        i = 0
        while (row := self.tree.get_row_at_index(i)) is not None:
            if row.key == key:
                self.tree.select_row(row)
                row.grab_focus()
                return
            i += 1

    def activated(self, row):
        if row.kind == "group":
            self.fold(row.key)
        else:
            self.open_code()

    def picked(self, row):
        page = self.pages.get_visible_child_name()
        if row.kind != "project" or (self.busy and page in ("progress", "new", "setup")):
            return   # keep a running clone / create in view
        if row.key != self.selected or page != "detail":
            self.selected, self.ref = row.key, ""
            self.show_detail()

    def current(self):
        return next((p for p in self.projects if p.rel == self.selected), None)

    # ---- the detail page -------------------------------------------------------------------------
    def show_detail(self, fetch=True):
        p = self.current()
        if not p:
            return
        self.pages.set_visible_child_name("detail")
        self.gen += 1
        gen = self.gen
        self.heading.set_text(p.name)
        self.where.set_text(tilde(p.path))
        clear(self.chips)
        clear(self.urls)
        e = p.entry
        if p.kind != "local":
            self.urls.append(label(e["url"], "sub", ellipsize=True))
            for name, url in e.get("remotes", {}).items():
                self.urls.append(label(f"{name}  {url}", "dim", ellipsize=True))
        self.paint_actions(p)
        notes = {"missing": "Listed, but not cloned on this PC. Clone gets it with every branch.",
                 "local": "No git remote: only this SSD and the HDD backup have it. "
                          + ("Add a remote and push it to keep it safe." if e.get("git")
                             else "Put it in git and push it to keep it safe.")}
        self.note.set_text(notes.get(p.kind, ""))
        self.note.set_visible(p.kind in notes)
        has_git = p.kind == "repo" or (p.kind == "local" and e.get("git"))
        self.tabs_bar.set_visible(has_git)
        self.tab_pages.set_visible(has_git)
        clear(self.graph)
        clear(self.branch_list)
        clear(self.filter_box)
        if not has_git:
            return
        stale = pj.fetched_ago(p.path)
        if (fetch and p.kind == "repo" and p.rel not in self.fetched and p.rel not in self.fetching
                and (stale is None or stale > STALE)):
            self.fetching.add(p.rel)
            threading.Thread(target=self.auto_fetch, args=(p,), daemon=True).start()
        threading.Thread(target=self.load_git, args=(p, gen), daemon=True).start()

    def auto_fetch(self, p):
        ok, _ = pj.git(p.path, "fetch", "--all", "--prune", "-q", timeout=60)
        self.fetching.discard(p.rel)
        self.fetched.add(p.rel)
        GLib.idle_add(self.after_fetch, p, ok)

    def after_fetch(self, p, ok):
        if p.rel == self.selected and self.pages.get_visible_child_name() == "detail":
            if not ok:
                self.say(f"{p.name}: couldn't fetch (offline?), so this is the last known state", "bad")
            self.show_detail(fetch=False)
        return False

    def paint_actions(self, p):
        clear(self.actions)
        self.pull_btn = self.push_btn = None
        if p.kind == "missing":
            self.actions.append(button("Clone", self.clone, "primary", tooltip="every branch, into its folder"))
        else:
            self.actions.append(button("VS Code", self.open_code, "primary", tooltip="Enter"))
            self.actions.append(button("Terminal", self.open_term, tooltip="t"))
            self.actions.append(button("Agent", self.open_agent, tooltip="a: an agentmux thread here"))
            if p.kind == "repo":
                self.actions.append(button("Fetch", self.fetch, tooltip="f: every remote, prune"))
                self.pull_btn = button("Pull", self.pull, tooltip="fast-forward the checked-out branch")
                self.push_btn = button("Push", self.push, tooltip="push the checked-out branch")
                for b in (self.pull_btn, self.push_btn):
                    b.set_sensitive(False)
                    self.actions.append(b)
        if p.kind != "local" and web_url(p.entry["url"]):
            self.actions.append(button("GitHub", self.open_web, "flat", tooltip="g"))

    def load_git(self, p, gen):
        """What the detail page shows from git (in this thread), then the graph."""
        branches = pj.branch_states(p.path)
        dirty, ahead, behind = pj.state(p.rel)
        has_origin = "origin" in pj.remotes(p.path)
        GLib.idle_add(self.paint_git, p, gen, branches, dirty, ahead, behind, has_origin)
        self.load_graph(p, gen, self.ref)

    def load_graph(self, p, gen, ref):
        sep = "\x1f"
        fmt = sep.join(["%H", "%P", "%h", "%D", "%s", "%an", "%cr"])
        which = [ref] if ref else ["--branches", "--remotes", "--tags", "HEAD"]
        lines = git_lines(p.path, "log", "--topo-order", f"-n{HISTORY}", f"--format={fmt}", *which, "--")
        commits = []
        for l in lines:
            f = (l.split(sep) + [""] * 7)[:7]
            commits.append({"hash": f[0], "parents": f[1].split(), "short": f[2], "decor": f[3],
                            "subject": f[4], "author": f[5], "when": f[6]})
        head = git_lines(p.path, "rev-parse", "HEAD")
        remotes = set(pj.remotes(p.path))
        local = set(git_lines(p.path, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
        GLib.idle_add(self.paint_graph, gen, ref, commits, graph_rows(commits), head[0] if head else "",
                      remotes, local)

    def paint_git(self, p, gen, branches, dirty, ahead, behind, has_origin):
        if gen != self.gen:
            return False
        clear(self.chips)
        cur = next((b for b in branches if b["head"]), None)

        def chip(text, cls):
            self.chips.append(label(text, "state", cls))
        chip(cur["name"] if cur else "detached", "bold")
        if cur and cur["gone"]:
            chip("remote deleted", "red")
        elif ahead is None:
            chip("no upstream", "amber")
        elif ahead and behind:
            chip(f"↑{ahead} ↓{behind} diverged", "red")
        elif ahead:
            chip(f"↑{ahead} to push", "amber")
        elif behind:
            chip(f"↓{behind} to pull", "cyan")
        else:
            chip("in sync", "green")
        chip(f"{dirty} changed" if dirty else "clean", "amber" if dirty else "dim")
        chip("fetching…" if p.rel in self.fetching else ago(pj.fetched_ago(p.path)), "dim")
        if self.pull_btn:
            can_pull = bool(behind) and not ahead
            self.pull_btn.set_sensitive(can_pull)
            self.pull_btn.set_tooltip_text("fast-forward the checked-out branch" if can_pull else
                                           "diverged: merge or rebase it yourself" if ahead and behind else
                                           "nothing to pull")
            self.push_btn.set_sensitive(bool(ahead) or (ahead is None and has_origin))
        self.paint_branches(branches)
        clear(self.filter_box)
        self.filter_box.append(dropdown([("", "All branches")] + [(b["name"], b["name"]) for b in branches],
                                        self.ref, self.show_graph_of))
        return False

    def paint_branches(self, branches):
        clear(self.branch_list)
        def column(widget, width):
            """A fixed-width cell, so the rows line up as a table (and long names don't widen the window)."""
            cell = box(False, 0, widget)
            cell.set_size_request(width, -1)
            cell.set_hexpand(False)   # a fixed column: the spare width goes to the gap before the buttons
            if not widget.get_hexpand():   # a chip keeps its size; text fills the cell and ellipsizes
                widget.set_halign(Gtk.Align.START)
            return cell
        for b in branches:
            text, cls = branch_state(b)
            name = label(b["name"], *(("bold",) if b["head"] else ("dim",) if not b["local"] else ()),
                         ellipsize=True)
            up = label(b["upstream"] if b["local"] else "", "dim", ellipsize=True)
            for w in (name, up):
                w.set_max_width_chars(1)   # natural width ~0: the cell's size decides, the text ellipsizes
                w.set_hexpand(True)
            when = label(b["date"], "dim", ellipsize=True)
            when.set_max_width_chars(1)
            when.set_hexpand(True)
            line = box(False, 8, label("●" if b["head"] else "", "accent", "pj-glyph"), column(name, 140),
                       column(label(text, "state", cls), 130), column(up, 96), column(when, 96))
            line.append(box(False, 0))
            line.get_last_child().set_hexpand(True)   # the buttons go to the right end
            if not b["head"]:
                line.append(button("Check out", lambda b=b: self.checkout(b), "flat"))
            if b["local"] and b["behind"] and not b["ahead"] and not b["gone"]:
                line.append(button("Pull", lambda b=b: self.pull(b["name"]), "flat"))
            for w in children(line):
                w.set_valign(Gtk.Align.CENTER)
            row = Gtk.ListBoxRow(child=line, tooltip_text="click: its history in the graph")
            row.ref = b["name"]
            self.branch_list.append(row)

    def paint_graph(self, gen, ref, commits, rows, head, remotes, local):
        if gen != self.gen or ref != self.ref:
            return False
        clear(self.graph)
        lanes = min(MAX_LANES, max((r["width"] for r in rows), default=1))
        width = lane_x(lanes - 1) + 12
        for c, r in zip(commits, rows):
            area = Gtk.DrawingArea(content_width=width, content_height=ROW_H)
            area.set_draw_func(lambda _a, cr, _w, h, r=r, is_head=c["hash"] == head: draw_graph_row(cr, h, r, is_head))
            line = box(False, 6, area, label(c["short"], "hash"), *ref_chips(c["decor"], remotes, local),
                       label(c["subject"], ellipsize=True), label(short(c["author"], 14), "dim"),
                       label(c["when"], "dim"))
            for w in list(children(line))[1:]:
                w.set_valign(Gtk.Align.CENTER)
            self.graph.append(Gtk.ListBoxRow(child=line, activatable=False))
        if not commits:
            self.graph.append(Gtk.ListBoxRow(child=label("no commits", "dim")))
        return False

    def show_graph_of(self, ref, tab=False):
        p = self.current()
        if not p:
            return
        if tab:   # from the Branches tab: the graph tab, its filter set to ref
            self.ref = ref
            self.switch_tab("graph")
            self.show_detail(fetch=False)
            return
        if ref != self.ref:
            self.ref = ref
            threading.Thread(target=self.load_graph, args=(p, self.gen, ref), daemon=True).start()

    # ---- actions ---------------------------------------------------------------------------------
    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def terminal(self, argv, title):
        """A program in a floating terminal (TUI.float: windowrules float and centre it)."""
        self.launch("kitty", "--class", "TUI.float", "--title", title, "-e", *argv)

    def openable(self):
        p = self.current()
        return p if p and p.kind != "missing" and self.pages.get_visible_child_name() == "detail" else None

    def open_code(self):
        if p := self.openable():
            self.launch("code", p.path)
            self.close()

    def open_term(self):
        if p := self.openable():
            self.launch("kitty", "--directory", p.path)
            self.close()

    def open_agent(self):
        if p := self.openable():
            subprocess.Popen([AGENTMUX, "project", p.path], start_new_session=True)
            self.close()

    def open_web(self):
        p = self.current()
        if p and p.kind != "local" and web_url(p.entry["url"]):
            subprocess.Popen(["xdg-open", web_url(p.entry["url"])], start_new_session=True)

    def git_job(self, start, work):
        """say(start), run work(p) -> (ok, message) in a thread, report it and redraw the project."""
        p = self.current()
        if not p:
            return
        self.say(start, seconds=300)

        def run_it():
            ok, msg = work(p)
            p.branch = pj.current_branch(p.path)
            GLib.idle_add(lambda: (self.say(f"{p.name}: {msg}", "ok" if ok else "bad"), self.paint_tree(),
                                   self.show_detail(fetch=False)) and False)
        threading.Thread(target=run_it, daemon=True).start()

    def fetch(self):
        p = self.openable()
        if p and p.kind == "repo":
            def work(p):
                ok, out = pj.git(p.path, "fetch", "--all", "--prune", "-q", timeout=300)
                return ok, "fetched" if ok else pj.last_line(out, "fetch failed")
            self.git_job(f"fetching {p.name}…", work)

    def pull(self, branch=None):
        p = self.openable()
        if p and p.kind == "repo":
            b = branch or p.branch
            self.git_job(f"pulling {b}…", lambda p: pj.pull_ff(p.path, b))

    def push(self):
        p = self.openable()
        if p and p.kind == "repo":
            self.git_job(f"pushing {p.branch}…", lambda p: pj.push(p.path))

    def checkout(self, b):
        p = self.openable()
        if not p:
            return
        if pj.state(p.rel)[0]:
            self.say(f"{p.name} has uncommitted changes: commit or stash them before switching", "bad")
            return
        name = b["name"] if b["local"] else b["name"].partition("/")[2]   # a remote one: a local tracking branch

        def work(p):
            ok, out = pj.git(p.path, "switch", "-q", name)
            return ok, f"on {name}" if ok else pj.last_line(out, "switch failed")
        self.git_job(f"switching to {name}…", work)

    def clone(self):
        p = self.current()
        if not p or p.kind != "missing" or self.busy:
            return
        self.say(f"cloning {p.name}…", seconds=600)

        def work():
            ok, detail = pj.clone_one(p.entry)
            msg = f"cloned {p.name}, {detail}" if ok else f"{p.name}: {detail}"
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad"), self.reload(select=p.rel)) and False)
        threading.Thread(target=work, daemon=True).start()

    # ---- + New -----------------------------------------------------------------------------------
    def build_new(self):
        self.new_name = Gtk.Entry(placeholder_text="my-app", hexpand=True)
        self.new_name.connect("changed", lambda *_: self.new_preview())
        self.new_name.connect("activate", lambda *_: self.create())
        self.folder_box = box(False, 0)
        self.new_folder = Gtk.Entry(placeholder_text="clients/acme", hexpand=True)
        self.new_folder.connect("changed", lambda *_: self.new_preview())
        self.start_box = box(False, 0)
        self.new_url = Gtk.Entry(placeholder_text="https://github.com/owner/repo.git", hexpand=True)
        self.new_url.connect("changed", lambda *_: self.new_preview())
        self.owner_box = box(False, 0)
        self.gh_switch = switch(True, lambda on: self.new_preview())
        self.private_switch = switch(True, lambda on: self.new_preview())
        self.preview = label("", "dim", wrap=True)
        self.create_btn = button("Create", self.create, "primary")

        def row(title, *widgets):
            r = box(False, 12, label(title, "form-label"), *widgets)
            r.set_margin_top(10)
            for w in children(r):
                w.set_valign(Gtk.Align.CENTER)
            return r
        self.folder_row2 = row("", self.new_folder)
        self.url_row = row("URL", self.new_url)
        self.gh_rows = box(True, 0, row("GitHub repo", self.gh_switch, label("create it and push", "dim")),
                           row("Owner", self.owner_box), row("Private", self.private_switch))
        buttons = box(False, 8, self.create_btn, button("Cancel", self.leave_page))
        buttons.set_margin_top(18)
        self.preview.set_margin_top(14)
        return box(True, 0, label("New project", "heading"),
                   label("A folder in ~/code with git, recorded in your list (and pushed).", "dim"),
                   rule_heading("Project", spaced=True), row("Name", self.new_name), row("Folder", self.folder_box),
                   self.folder_row2, row("Start from", self.start_box), self.url_row, self.gh_rows,
                   self.preview, buttons)

    def show_new(self):
        if self.busy or not os.path.exists(pj.LIST):
            return
        for e in (self.new_name, self.new_url, self.new_folder):
            e.set_text("")
        here = self.current()
        group = os.path.dirname(here.rel) if here else "active"
        folders = pj.groups()
        self.folder = group if group in folders else "active" if "active" in folders else \
            (folders[0] if folders else NEW_FOLDER)
        clear(self.folder_box)
        self.folder_box.append(dropdown([(f, f) for f in folders] + [(NEW_FOLDER, NEW_FOLDER)], self.folder,
                                        lambda f: (setattr(self, "folder", f), self.new_preview())))
        tdir = f"{pj.ROOT}/templates"
        templates = sorted(n for n in os.listdir(tdir) if not n.startswith(".")) if os.path.isdir(tdir) else []
        self.start = "empty"
        clear(self.start_box)
        self.start_box.append(dropdown([("empty", "Empty (README + git)")]
                                       + [(f"t:{t}", f"Template: {t}") for t in templates]
                                       + [("clone", "Clone a URL")], "empty",
                                       lambda s: (setattr(self, "start", s), self.new_preview())))
        self.owner = ""
        clear(self.owner_box)
        self.owner_box.append(label("loading…", "dim"))
        threading.Thread(target=lambda: GLib.idle_add(self.paint_owners, pj.gh_owners()), daemon=True).start()
        self.pages.set_visible_child_name("new")
        self.new_preview()
        GLib.idle_add(lambda: self.new_name.grab_focus() and False)

    def paint_owners(self, owners):
        clear(self.owner_box)
        if owners:
            self.owner = owners[0]
            self.owner_box.append(dropdown([(o, o) for o in owners], self.owner,
                                           lambda o: (setattr(self, "owner", o), self.new_preview())))
        else:
            self.owner_box.append(label("gh isn't logged in (gh auth login): no GitHub repo", "amber"))
            self.gh_switch.set_active(False)
        self.new_preview()
        return False

    def new_rel(self):
        """(folder, name) the form would create; the name comes from the URL when cloning without one."""
        folder = self.new_folder.get_text().strip().strip("/") if self.folder == NEW_FOLDER else self.folder
        name = self.new_name.get_text()
        if not name.strip() and self.start == "clone" and self.new_url.get_text().strip():
            name = pj.repo_name(self.new_url.get_text().strip())
        return folder, pj.kebab(name)

    def new_preview(self):
        if self.start is None:
            return
        cloning = self.start == "clone"
        self.folder_row2.set_visible(self.folder == NEW_FOLDER)
        self.url_row.set_visible(cloning)
        self.gh_rows.set_visible(not cloning)
        folder, name = self.new_rel()
        rel = f"{folder}/{name}"
        problem = ("a name" if not name else "a folder" if not folder else
                   "a URL" if cloning and not self.new_url.get_text().strip() else "")
        exists = not problem and os.path.exists(f"{pj.ROOT}/{rel}")
        if problem:
            self.preview.set_text(f"Needs {problem}.")
        elif exists:
            self.preview.set_text(f"~/code/{rel} already exists.")
        else:
            gh = self.gh_switch.get_active() and self.owner
            what = ("cloned with every branch" if cloning else
                    f"GitHub: {self.owner}/{name}, {'private' if self.private_switch.get_active() else 'public'}"
                    if gh else "no GitHub repo: local only until you add one")
            self.preview.set_text(f"→ ~/code/{rel}    {what}")
        self.create_btn.set_sensitive(not problem and not exists and not self.busy)

    def create(self):
        if self.busy or not self.create_btn.get_sensitive():
            return
        folder, name = self.new_rel()
        rel = f"{folder}/{name}"
        start, url = self.start, self.new_url.get_text().strip()
        owner = self.owner if self.gh_switch.get_active() else ""
        private = self.private_switch.get_active()
        self.busy = True
        self.create_btn.set_sensitive(False)

        def say(msg):
            GLib.idle_add(lambda: self.say(msg, seconds=300) and False)

        def work():
            if start == "clone":
                say(f"cloning into {rel}…")
                ok, detail = pj.clone_one({"path": rel, "url": url})
                if ok:
                    pj.scan(quiet=True)
                msg = f"cloned {rel}, {detail}" if ok else detail
            else:
                ok, msg = pj.new_project(rel, start[2:] if start.startswith("t:") else "", owner, private, say=say)
            GLib.idle_add(self.created, ok, msg, rel)
        threading.Thread(target=work, daemon=True).start()

    def created(self, ok, msg, rel):
        self.busy = False
        self.say(msg, "ok" if ok else "bad", seconds=8)
        if ok or os.path.exists(f"{pj.ROOT}/{rel}"):
            self.reload(select=rel)
        else:
            self.new_preview()
        return False

    def leave_page(self):
        if self.busy:
            return
        self.pages.set_visible_child_name("empty")
        if self.current():
            self.show_detail(fetch=False)
        self.focus_list()

    # ---- a new PC: the setup page ----------------------------------------------------------------
    def build_setup(self):
        self.gh_state = label("checking GitHub…", "dim", wrap=True)
        self.gh_login_btn = button("Log in to GitHub", lambda: self.terminal(["gh", "auth", "login"], "gh auth login"))
        gh = box(False, 8, self.gh_login_btn, button("Check again", self.check_gh, "flat"))
        gh.set_margin_top(8)
        self.list_url = Gtk.Entry(placeholder_text="https://github.com/<you>/code-projects", hexpand=True)
        self.list_url.connect("activate", lambda *_: self.restore())
        restore = box(False, 8, self.list_url, button("Get the list and clone everything", self.restore, "primary"))
        restore.set_margin_top(10)
        fresh = button("Create the list", self.create_list)
        fresh.set_halign(Gtk.Align.START)
        fresh.set_margin_top(10)
        return box(True, 0, label("Set up Projects on this PC", "heading"),
                   label(f"There's no project list in {tilde(pj.DIR)} yet.", "dim"),
                   rule_heading("GitHub", spaced=True), self.gh_state, gh,
                   rule_heading("Restore your list", spaced=True),
                   label("Your list is a private repo (code-projects). Paste its URL: it's cloned, then every "
                         "project in it, into the same folders, with all their branches.", "sub", wrap=True),
                   restore,
                   rule_heading("Start a new list", spaced=True),
                   label("No list yet? Create one (a private GitHub repo) from what's in ~/code now. Then build "
                         "your structure with + New: pick a folder or New folder…, choose Clone a URL, paste "
                         "the repo's URL. Each project is recorded in the list.", "sub", wrap=True),
                   fresh)

    def show_setup(self):
        for w in children(self.pages.get_child_by_name("setup")):
            if isinstance(w, Gtk.Label) and w.get_wrap():
                w.set_max_width_chars(80)   # a paragraph's natural width, not one long line
        self.pages.set_visible_child_name("setup")
        self.check_gh()

    def check_gh(self):
        self.gh_state.set_text("checking GitHub…")

        def work():
            user = pj.gh_login()
            GLib.idle_add(self.paint_gh, user, pj.list_url() if user else "")
        threading.Thread(target=work, daemon=True).start()

    def paint_gh(self, user, url):
        if user:
            self.gh_state.set_text(f"Logged in as {user}." + ("" if url else " No code-projects repo under it."))
        else:
            self.gh_state.set_text("gh isn't logged in, so private repos can't be cloned. Log in, then Check again.")
        self.gh_login_btn.set_visible(not user)
        if url and not self.list_url.get_text():
            self.list_url.set_text(url)
        return False

    def restore(self):
        if self.busy:
            return
        url = self.list_url.get_text().strip()
        self.busy = True
        self.say("getting the list…", seconds=300)
        threading.Thread(target=lambda: GLib.idle_add(self.restored, pj.get_list(url)), daemon=True).start()

    def restored(self, problem):
        self.busy = False
        if problem:
            self.say(problem, "bad", seconds=10)
            return False
        self.reload()
        self.clone_missing()
        return False

    def create_list(self):
        if self.busy:
            return
        self.busy = True
        self.say("creating the list…", seconds=300)
        threading.Thread(target=lambda: GLib.idle_add(self.list_created, *pj.init_list()), daemon=True).start()

    def list_created(self, ok, msg):
        self.busy = False
        self.say("List created. Add projects with + New (Clone a URL)." if ok else msg, "ok" if ok else "bad", 10)
        if ok:
            self.reload()
            self.pages.set_visible_child_name("empty")
        return False

    # ---- clone progress --------------------------------------------------------------------------
    def build_progress(self):
        self.prog_title = label("", "heading")
        self.prog_counts = label("", "sub")
        self.prog_counts.set_hexpand(True)
        self.prog_stop = button("Stop", self.stop_clones, "danger", tooltip="finish the running ones, skip the rest")
        self.prog_done = button("Done", self.leave_page, "primary")
        self.prog_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.prog_list.add_css_class("branch-list")
        top = box(False, 12, self.prog_counts, self.prog_stop, self.prog_done)
        top.set_margin_top(6)
        lst = scrolled(self.prog_list)
        lst.set_margin_top(12)
        return box(True, 0, self.prog_title, top, lst)

    def clone_missing(self):
        if self.busy:
            return
        todo = pj.missing()
        if not todo:
            self.say("every listed project is already here")
            return
        self.busy = True
        self.stop = threading.Event()
        self.prog_title.set_text(f"Cloning {len(todo)} project{'s' * (len(todo) > 1)}")
        self.prog_state = {}
        self.prog_tally = {"ok": 0, "failed": 0, "skipped": 0}
        clear(self.prog_list)
        for e in todo:
            st = label("waiting", "dim", xalign=1.0, ellipsize=True)   # an error is what gets cut, not the name
            st.set_max_width_chars(1)
            spin = Gtk.Spinner()
            self.prog_list.append(Gtk.ListBoxRow(child=box(False, 10, spin, label(e["path"]), st),
                                                 activatable=False))
            self.prog_state[e["path"]] = (spin, st)
        self.prog_stop.set_visible(True)
        self.prog_stop.set_sensitive(True)
        self.prog_done.set_visible(False)
        self.paint_counts(len(todo))
        self.pages.set_visible_child_name("progress")

        def report(e, state, detail):
            GLib.idle_add(self.progress, e["path"], state, detail, len(todo))

        def work():
            pj.clone_all(todo, report, self.stop)
            GLib.idle_add(self.clones_done)
        threading.Thread(target=work, daemon=True).start()

    def progress(self, rel, state, detail, total):
        spin, st = self.prog_state[rel]
        spin.set_spinning(state == "started")
        for c in ("dim", "green", "red"):
            st.remove_css_class(c)
        text, cls = {"started": ("cloning…", "dim"), "ok": (f"✓ {detail}", "green"),
                     "failed": (f"✗ {detail}", "red"), "skipped": ("skipped", "dim")}[state]
        st.set_text(text)
        st.set_tooltip_text(detail or None)
        st.add_css_class(cls)
        if state in self.prog_tally:
            self.prog_tally[state] += 1
        self.paint_counts(total)
        return False

    def paint_counts(self, total):
        t = self.prog_tally
        bits = [f"{sum(t.values())} of {total}", f"{t['ok']} cloned"]
        if t["failed"]:
            bits.append(f"{t['failed']} failed")
        if t["skipped"]:
            bits.append(f"{t['skipped']} skipped")
        self.prog_counts.set_text(" · ".join(bits))

    def stop_clones(self):
        if self.stop:
            self.stop.set()
            self.prog_stop.set_sensitive(False)
            self.say("stopping after the running clones…")

    def clones_done(self):
        self.busy = False
        self.prog_stop.set_visible(False)
        self.prog_done.set_visible(True)
        t = self.prog_tally
        self.say(f"{t['ok']} cloned" + (f", {t['failed']} failed" if t["failed"] else ""),
                 "bad" if t["failed"] else "ok", seconds=10)
        self.projects = load_projects()
        self.paint_tree()
        self.update_counts()
        threading.Thread(target=self.count_changes, daemon=True).start()
        return False

    # ---- keys ------------------------------------------------------------------------------------
    def key(self, keyval, state):
        page = self.pages.get_visible_child_name()
        row = self.tree.get_selected_row()
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
                self.focus_list()
            elif page in ("new", "progress") and not self.busy:
                self.leave_page()
            else:
                self.close()
            return True
        if self.typing():
            return False
        if keyval == Gdk.KEY_slash:
            self.search.grab_focus()
        elif keyval == Gdk.KEY_Tab and page == "detail" and self.tabs_bar.get_visible():
            self.switch_tab("branches" if self.tab == "graph" else "graph")
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and row:
            group = row.key if row.kind == "group" else os.path.dirname(row.key)
            if group and not self.search.get_text():
                self.fold(group, shut=keyval == Gdk.KEY_Left)
        elif keyval == Gdk.KEY_n:
            self.show_new()
        elif keyval == Gdk.KEY_t:
            self.open_term()
        elif keyval == Gdk.KEY_a:
            self.open_agent()
        elif keyval == Gdk.KEY_f:
            self.fetch()
        elif keyval == Gdk.KEY_g:
            self.open_web()
        else:
            return False
        return True


def make():
    return Projects()


def main():
    run(make(), "panels.projects", (1180, 680))


if __name__ == "__main__":
    main()
