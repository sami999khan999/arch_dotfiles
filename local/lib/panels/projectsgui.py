#!/usr/bin/env python3
# projectsgui.py — the Projects panel (Super + Ctrl + P): every project in ~/code, from the `projects`
# list (~/code/.projects/projects.json, private), with its git details. Running it again closes it.
#
#   left   the ~/code tree: groups fold (←/→, Enter or a click on the group); a repo shows its branch and
#          a dot when it has uncommitted changes; ○ a listed project that isn't cloned on this PC;
#          ⚠ a local-only folder (no git remote: only the SSD and the HDD backup have it)
#   right  the selected project: branch, ahead/behind, changed files, remotes; open it in VS Code, a
#          terminal or agentmux; Fetch, GitHub, Clone (when it isn't here). Its branches (newest first,
#          ● the checked-out one; click one for its history) and the history of that branch.
#
# Opening runs `projects scan` in the background (the list follows ~/code). Every git call runs in a
# thread, so a big repo never freezes the window. The data and clone are the `projects` command's.
import importlib.machinery, importlib.util, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, recolor, rule_heading, run, scrolled
from keys import fuzzy
from keysgui import marked

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
HIT = recolor("#7aa2f7")   # matched letters, as in the shortcut list
HISTORY = 200              # commits shown
FOLDED = {"archive", "templates", "forks"}   # groups that start folded
G_OPEN, G_SHUT = "", ""         # folder open / closed
G_REPO, G_MISSING, G_LOCAL = "", "", ""   # git branch, cloud download, warning
CSS = """
.tree > row { padding: 3px 10px; }
.tree > row.group-row { padding-top: 6px; }
.pj-glyph { min-width: 18px; }
.branches > row, .history > row { padding: 3px 0; }
.branches > row:hover, .history > row:hover { background: alpha(#292e42, .45); }
.hash { color: #e0af68; }
.detail-url { color: #a9b1d6; }
"""


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
    hints = [("Enter", "VS Code"), ("t", "terminal"), ("a", "agent"), ("f", "fetch"), ("g", "GitHub"),
             ("←→", "fold"), ("/", "search"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.projects = load_projects()
        self.folded = set(FOLDED)
        self.selected = None          # rel of the project shown on the right
        self.ref = None               # the branch whose history is shown
        self.gen = 0                  # detail loads: only the newest one draws
        self.rows = {}                # rel -> (row, dot label) for in-place updates

    # ---- layout ----------------------------------------------------------------------------------
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

        self.heading = label("", "heading", ellipsize=True)
        self.where = label("", "dim", ellipsize=True)
        self.state = label("", "sub", ellipsize=True)
        self.state.set_margin_top(4)
        self.urls = box(True, 2)
        self.urls.set_margin_top(6)
        self.actions = box(False, 8)
        self.actions.set_margin_top(14)
        self.note = label("", "amber", wrap=True)
        self.note.set_margin_top(12)
        self.branch_head = rule_heading("Branches", spaced=True)
        self.branches = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.branches.add_css_class("branches")
        self.branches.connect("row-activated", lambda _l, row: self.show_history(row.ref))
        self.branch_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                propagate_natural_height=True, max_content_height=170)
        self.branch_scroll.set_child(self.branches)
        self.history_head = rule_heading("History", spaced=True)
        self.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.history.add_css_class("history")
        right = box(True, 0, self.heading, self.where, self.state, self.urls, self.actions, self.note,
                    self.branch_head, self.branch_scroll, self.history_head, scrolled(self.history))
        right.set_hexpand(True)
        for edge in ("start", "end", "top"):
            getattr(right, f"set_margin_{edge}")(20)

        self.paint_tree()
        self.update_subtitle()
        GLib.idle_add(lambda: self.focus_list() and False)
        threading.Thread(target=self.background, daemon=True).start()
        return box(False, 0, left, right)

    def focus_list(self):
        row = self.tree.get_selected_row() or self.tree.get_row_at_index(0)
        if row:
            self.tree.select_row(row)
            row.grab_focus()
        return True

    def update_subtitle(self):
        n = {k: sum(p.kind == k for p in self.projects) for k in ("repo", "missing", "local")}
        bits = [f"{n['repo']} repos"]
        if n["missing"]:
            bits.append(f"{n['missing']} not cloned")
        if n["local"]:
            bits.append(f"{n['local']} local only")
        self.set_subtitle(" · ".join(bits))

    # ---- background: scan the folder, then each repo's changed files ------------------------------
    def background(self):
        lines = pj.scan(quiet=True)
        if lines:
            GLib.idle_add(self.reload)
        self.count_changes()

    def reload(self):
        self.projects = load_projects()
        self.paint_tree()
        self.update_subtitle()
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
        """[(kind, key, depth)] in on-screen order: kind is group or project."""
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
                row.kind, row.key, row.dot = "project", key, dot
                first = first or row
            row.get_child().set_margin_start(depth * 16)
            self.tree.append(row)
            if kind == "project":
                self.rows[key] = (row, row.dot)
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
        for i in range(10000):
            row = self.tree.get_row_at_index(i)
            if row is None:
                break
            if row.key == key:
                self.tree.select_row(row)
                row.grab_focus()
                break

    def activated(self, row):
        if row.kind == "group":
            self.fold(row.key)
        else:
            self.open_code()

    def picked(self, row):
        if row.kind == "project" and row.key != self.selected:
            self.selected, self.ref = row.key, None
            self.show_detail()

    def current(self):
        return next((p for p in self.projects if p.rel == self.selected), None)

    # ---- the right side --------------------------------------------------------------------------
    def show_detail(self):
        p = self.current()
        if not p:
            return
        self.gen += 1
        gen = self.gen
        self.heading.set_text(p.name)
        self.where.set_text(pj.ROOT.replace(os.path.expanduser("~"), "~") + "/" + p.rel)
        self.state.set_text("")
        self.state.set_visible(False)
        clear(self.urls)
        e = p.entry
        if p.kind != "local":
            self.urls.append(label(e["url"], "detail-url", ellipsize=True))
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
        for w in (self.branch_head, self.branch_scroll, self.history_head):
            w.set_visible(has_git)
        clear(self.branches)
        clear(self.history)
        if has_git:
            threading.Thread(target=self.load_git, args=(p, gen), daemon=True).start()

    def paint_actions(self, p):
        clear(self.actions)
        if p.kind == "missing":
            self.actions.append(button("Clone", self.clone, "primary", tooltip="every branch, into its folder"))
        else:
            self.actions.append(button("VS Code", self.open_code, "primary", tooltip="Enter"))
            self.actions.append(button("Terminal", self.open_term, tooltip="t"))
            self.actions.append(button("Agent", self.open_agent, tooltip="a: an agentmux thread here"))
            if p.kind == "repo":
                self.actions.append(button("Fetch", self.fetch, tooltip="f: every remote, prune"))
        if p.kind != "local" and web_url(p.entry["url"]):
            self.actions.append(button("GitHub", self.open_web, "flat", tooltip="g"))

    def load_git(self, p, gen):
        """Branches and the head's state, then the history (in this thread)."""
        fmt = "%(refname)%09%(refname:short)%09%(committerdate:relative)%09%(upstream:track,nobracket)"
        # git's output is stripped, so the last line can lose its empty last field: pad to 4
        refs = [(l.split("\t") + [""] * 4)[:4] for l in git_lines(p.path, "for-each-ref", "--sort=-committerdate",
                                                                   f"--format={fmt}", "refs/heads", "refs/remotes")]
        local = {short for full, short, *_ in refs if full.startswith("refs/heads/")}
        branches = []
        for full, short, when, track in refs:
            if full.startswith("refs/remotes/"):
                name = short.partition("/")[2]
                if short.endswith("/HEAD") or name in local or not name:
                    continue        # a remote branch with a local one shows as the local one
            branches.append((short, when, track, full.startswith("refs/remotes/")))
        head = pj.current_branch(p.path)
        dirty, ahead, behind = pj.state(p.rel)
        GLib.idle_add(self.paint_git, p, gen, branches, head, dirty, ahead, behind)
        self.load_history(p, gen, self.ref or head or "HEAD")

    def load_history(self, p, gen, ref):
        log = [l.split("\t") for l in git_lines(p.path, "log", f"-n{HISTORY}", "--format=%h%x09%s%x09%an%x09%cr", ref, "--")]
        GLib.idle_add(self.paint_history, gen, ref, log)

    def paint_git(self, p, gen, branches, head, dirty, ahead, behind):
        if gen != self.gen:
            return False
        bits = [head or "detached"]
        if ahead:
            bits.append(f"↑{ahead}")
        if behind:
            bits.append(f"↓{behind}")
        if ahead is None and head:
            bits.append("no upstream")
        bits.append(f"{dirty} changed" if dirty else "clean")
        self.state.set_text(" · ".join(bits))
        self.state.set_visible(True)
        clear(self.branches)
        for name, when, track, remote_only in branches:
            mark = label("●" if name == head else "", "accent", "pj-glyph")
            line = box(False, 8, mark, label(name, *(("bold",) if name == head else ("dim",) if remote_only else ()),
                                              ellipsize=True),
                       label(track, "amber"), label("remote only" if remote_only else "", "dim"),
                       label(when, "dim"))
            row = Gtk.ListBoxRow(child=line)
            row.ref = name
            self.branches.append(row)
        return False

    def paint_history(self, gen, ref, log):
        if gen != self.gen:
            return False
        self.ref = ref
        clear(self.history)
        for fields in log:
            if len(fields) < 4:
                continue
            h, subject, author, when = fields[:4]
            self.history.append(Gtk.ListBoxRow(child=box(
                False, 10, label(h, "hash"), label(subject, ellipsize=True), label(author, "dim"), label(when, "dim"))))
        self.history_head.get_first_child().set_text(f"History · {ref}")
        return False

    def show_history(self, ref):
        p = self.current()
        if p:
            self.ref = ref
            threading.Thread(target=self.load_history, args=(p, self.gen, ref), daemon=True).start()

    # ---- actions ---------------------------------------------------------------------------------
    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def openable(self):
        p = self.current()
        return p if p and p.kind != "missing" else None

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

    def fetch(self):
        p = self.current()
        if not p or p.kind != "repo":
            return
        self.say(f"fetching {p.name}…")

        def work():
            ok, out = pj.git(p.path, "fetch", "--all", "--prune", "-q", timeout=300)
            GLib.idle_add(lambda: (self.say(f"fetched {p.name}" if ok else out.splitlines()[-1] if out else "fetch failed",
                                            "ok" if ok else "bad"), self.show_detail()) and False)
        threading.Thread(target=work, daemon=True).start()

    def clone(self):
        p = self.current()
        if not p or p.kind != "missing":
            return
        self.say(f"cloning {p.name}…", seconds=600)

        def work():
            ok, line = pj.clone_one(p.entry)
            msg = f"cloned {p.name}" if ok else line.split("  ", 1)[-1] or "clone failed"
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad"), self.reload(), self.show_detail()) and False)
        threading.Thread(target=work, daemon=True).start()

    def key(self, keyval, state):
        row = self.tree.get_selected_row()
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
                self.focus_list()
            else:
                self.close()
            return True
        if self.typing():
            return False
        if keyval == Gdk.KEY_slash:
            self.search.grab_focus()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and row:
            group = row.key if row.kind == "group" else os.path.dirname(row.key)
            if group and not self.search.get_text():
                self.fold(group, shut=keyval == Gdk.KEY_Left)
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
