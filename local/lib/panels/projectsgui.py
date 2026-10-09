#!/usr/bin/env python3
# projectsgui.py — the Projects panel (Super + Ctrl + P): every project in ~/code, from the `projects`
# list (~/code/.projects/projects.json, private), with its git details. Running it again closes it.
#
#   left   the ~/code tree, all of it or one GitHub account's / org's (the dropdown by the search): groups fold (←/→, Enter or a click on the group); a repo shows its branch and
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
import importlib.machinery, importlib.util, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gtkkit import (Gdk, GLib, Gtk, Pango, View, backdrop_setting, window_blur, box, button, clear, dropdown, label, recolor, rgbf, rule_heading, wrap_box,
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
FOLDED = {"templates"}   # groups that start folded
MAX_BRANCH_ROWS = 6       # local branches in a project's branch table (the Branches tab has them all)
STALE_ALL = 3600          # seconds: when the panel opens, repos fetched longer ago than this are fetched
# the list's tabs (1-4): every project, or only those that need something: work not on GitHub yet
# (uncommitted or unpushed), GitHub ahead of you (Sync brings it), what needs merging by hand
FILTERS = [("all", "All"), ("changes", "Changes"), ("behind", "Behind"), ("conflicts", "Conflicts")]
EMPTY = {"changes": "Everything is committed and pushed.", "behind": "Everything is up to date with GitHub.",
         "conflicts": "Nothing to merge by hand."}
G_PIN = "\U000f0403"             # a pinned project (nf-md-pin)
# a project's status (projects mark; the dropdown on its page): its name and colour, in pj.STATUSES' order
STATUS = {"in-progress": ("In progress", "#7aa2f7"), "active": ("Active", "#9ece6a"), "testing": ("Testing", "#73daca"),
          "blocked": ("Blocked", "#f7768e"), "planned": ("Planned", "#7dcfff"), "idea": ("Idea", "#bb9af7"),
          "paused": ("Paused", "#e0af68"), "done": ("Done", "#737aa2")}
BY_STATUS = "\u00a7status"       # the sort dropdown's "by status" (its other values: a folder tree, one status)
FOLDED.add(f"{BY_STATUS}/done")   # by status, Done starts folded
INDENT = 18 + 8                  # a tree level: .pj-glyph's width + the rows' spacing
G_OPEN, G_SHUT = "", ""         # folder open / closed
G_REPO = "\uf401"                # a git repo (nf-oct-repo), in the folders' icon column
G_MISSING, G_LOCAL = "", ""   # cloud download, warning
# graph: lane colours (Tokyo Night; the theme recolours them), lane width and row height in px
LANES = ["#7aa2f7", "#bb9af7", "#9ece6a", "#ff9e64", "#7dcfff", "#e0af68", "#73daca", "#f7768e"]
LANE_W, ROW_H, MAX_LANES = 14, 24, 10
NEW_FOLDER = "New folder…"
CSS = """
.tree > row { padding: 3px 10px; }
.tree > row.group-row { padding-top: 6px; }
.tree > row.hint-row { padding: 14px 10px; }
/* the tabs over the list: tighter than a page's, so four and Sync fit the column */
.pj-filters { padding: 0; }
.pj-filters .tab { padding: 6px 8px; }
.pj-filters .tab:disabled { color: #3b4261; background: transparent; }
button.pj-sync { padding: 4px 10px; }
.pj-status { font-size: 9pt; }
.pj-glyph { min-width: 18px; }
.graph > row { padding: 0 10px 0 0; min-height: 24px; }
.graph > row:hover, .branch-list > row:hover { background: alpha(#292e42, .45); }
.branch-list > row { padding: 5px 4px; }
.hash { color: #e0af68; }
.chip { padding: 0 5px; font-size: 9pt; }
button.branch-chip { padding: 1px 6px; min-height: 0; margin-left: -6px; }
.branch-head { font-size: 9pt; }
/* the header's two cards, Status and Branches: side by side, the same width and height */
/* not ".card": GTK themes give that class a background of their own */
.pj-card { border: 1px solid alpha(#3b4261, .7); padding: 10px 14px; }
.card-title { color: #a9b1d6; font-weight: 700; }
/* main heads the branch card, the other branches under it, set off by a rule */
separator.branch-rule { background: alpha(#3b4261, .5); min-height: 1px; margin: 3px 0; }
.dot-col { min-width: 10px; }
button.branch-chip:hover { background: alpha(#c0caf5, .1); }
.chip-local { background: alpha(#6b8fe0, .2); color: #7aa2f7; }
.chip-remote { background: alpha(#565f89, .2); color: #565f89; }
.chip-remote.same { color: #a9b1d6; }
.chip-head { color: #9ece6a; border: 1px solid alpha(#9ece6a, .6); }
.chip-tag { background: alpha(#e0af68, .14); color: #e0af68; }
.state { padding: 1px 7px; background: alpha(#c0caf5, .06); }
.form-label { color: #a9b1d6; min-width: 120px; }
.tabs.inner { padding: 0; margin-top: 14px; }
/* the panel's own popups (Move, Delete…): a dimmed panel, a bordered card in the middle */
.dialog { background: #1a1b26; border: 1px solid alpha(#3b4261, .7); padding: 20px 22px; }
.dialog-title { font-weight: 700; font-size: 12pt; }
.folders { background: #16161e; border: 1px solid alpha(#3b4261, .7); }
.folders > row { padding: 4px 10px; }
.folders > row:selected { background: #292e42; }
button.danger-fill { background: alpha(#f7768e, .16); color: #f7768e; }
button.danger-fill:hover { background: alpha(#f7768e, .26); }
button.danger-fill:disabled { background: alpha(#c0caf5, .04); color: #565f89; }
.changes > row { padding: 2px 4px; }
.st { min-width: 22px; font-weight: 700; }
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


def para(text, *classes):
    """A popup's paragraph: wraps at the card's width instead of widening it."""
    l = label(text, *classes, wrap=True)
    l.set_max_width_chars(62)
    l.set_wrap_mode(Pango.WrapMode.WORD_CHAR)   # a long path breaks too
    return l


def sync_summary(reps):
    """One line for what pj.sync() did across one or more repos."""
    n = lambda key: sum(len(r[key]) for r in reps)
    bits = [f"{n('updated')} branch{'es' * (n('updated') != 1)} updated"]
    if n("created"):
        bits.append(f"{n('created')} new")
    if n("deleted"):
        bits.append(f"{n('deleted')} deleted (gone on GitHub, all in main)")
    skipped = [f"{b} ({why})" for r in reps for b, why in r["skipped"]]
    if skipped:
        bits.append("skipped " + ", ".join(skipped[:3]) + ("…" if len(skipped) > 3 else ""))
    if n("diverged"):
        bits.append(f"{n('diverged')} diverged: see Conflicts")
    errors = [r["error"] for r in reps if r["error"]]
    if errors:
        bits.append(f"{len(errors)} couldn't fetch ({errors[0]})")
    return ", ".join(bits)


def set_health(p, h):
    """A Project's numbers from pj.health(). Set in one dict update: it's called from git threads, and
    field by field the tree, painted meanwhile on the main thread, could show half old, half new."""
    p.__dict__.update(dirty=h["dirty"], unpushed=h["unpushed"], main=h["main"], behind=h["behind"],
                      out_of_date=h["out_of_date"], conflicts=h["diverged"] + h["unmerged"],
                      to_sync=h["to_sync"], behind_branches=h["behind_branches"], new_branches=h["new_branches"])


def status_lines(path):
    """`git status --porcelain` lines as git writes them: XY, a space, the path. Not git_lines(): its
    strip() takes the first line's leading space (" M a.txt", modified but not staged) with it."""
    try:
        r = subprocess.run(["git", "-C", path, "status", "--porcelain=v1", "-uall"], capture_output=True,
                           text=True, timeout=20, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l for l in r.stdout.splitlines() if len(l) > 3]


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
        self.dirty = 0      # changed files (count_changes, live)
        self.unpushed = 0   # commits on no remote
        self.main, self.behind = "", 0   # the main branch, and how far it's behind origin's (last fetch)
        self.out_of_date = 0   # what Sync would bring (branches behind GitHub, branches new there)
        self.to_sync, self.behind_branches, self.new_branches = 0, {}, 0   # its commits, by branch; new branches
        self.conflicts = 0     # diverged branches + files left with conflicts: merging by hand
        self.branch = pj.current_branch(self.path) if kind == "repo" else ""


def owner_of(p):
    """The owner filter's key for a project: its GitHub account or org (lower case: GitHub ignores case),
    §other for a remote elsewhere, §local for a folder with no remote."""
    if p.kind == "local":
        return "§local"
    repo = pj.github_repo(p.entry.get("url", ""))
    return repo.split("/")[0].lower() if repo else "§other"


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
    icon = "\uea62"
    interval = 2   # refresh(): the shown project's git status, live
    css = CSS
    hints = [("Enter", "VS Code"), ("t", "terminal"), ("a", "agent"), ("c", "commit"), ("s", "sync"), ("p", "pin"), ("m", "move"), ("n", "new"),
             ("Tab", "changes / conflicts / graph / branches"), ("←→", "fold"), ("/", "search"), ("?", "ask")]

    def __init__(self):
        super().__init__()
        self.projects = load_projects()
        self.list_seen = self.list_stamp()
        self.folded = set(FOLDED)
        self.selected = None          # rel of the project shown on the right
        self.ref = ""                 # the graph's branch ("" = every branch)
        self.gen = 0                  # detail loads: only the newest one draws
        self.rows = {}                # rel -> (row, dot label) for in-place updates
        self.fetched = set()          # projects fetched since the panel opened
        self.fetching = set()
        self.fetched_all = False      # the stale repos were fetched (once, when the panel opened)
        self.shown_rel = None         # the project on the page: the same one again isn't blanked first
        self.tree_seen = None         # what the tree shows (paint_tree skips a repaint that changes nothing)
        self.branch_names = []        # for the graph's branch filter
        self.org = ""                 # the tree's owner filter: an owner_of() key ("" = every owner)
        self.syncing = False          # Sync all is running
        self.filter = "all"           # the list's tab (FILTERS)
        self.busy = False             # a clone / create is running
        self.stop = None              # the clone run's stop event
        self.start = None             # the New form's "Start from" (None until it's built)
        self.gh_admin = {}            # owner/name -> may this account delete it on GitHub
        self.pinned = pj.load()["pinned"]
        self.marks = pj.load()["status"]   # rel -> its status (STATUS)
        self.sort = ""                # the sort dropdown: "" the folders, BY_STATUS, or one status's projects only

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        self.new_btn = button("+ New", self.show_new, "flat", tooltip="n: a new project")
        ask = button("Ask", self.ask, "flat", tooltip="?: a read-only chat about your projects")
        self.missing_btn = button("", self.clone_missing, "flat", tooltip="clone the listed projects that aren't here")
        self.update_counts()
        return [self.missing_btn, ask, self.new_btn]

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
        self.org_box = box(False, 0)   # the owner filter, above the search (paint_orgs)
        self.filter_btns = {}
        bar = box(False, 0, classes=("tabs", "pj-filters"))
        for i, (key, _name) in enumerate(FILTERS, 1):
            self.filter_btns[key] = button("", lambda k=key: self.set_filter(k), "tab", tooltip=str(i))
            bar.append(self.filter_btns[key])
        bar.append(Gtk.Box(hexpand=True))
        self.sync_btn = button("\uf021 Sync", self.sync_all, "flat", "pj-sync",
                               tooltip="Sync every project: GitHub's changes down, every branch (never merges, "
                                       "pushes or touches uncommitted work)")
        bar.append(self.sync_btn)
        self.sort_box = box(False, 0)   # the sort dropdown, under the tabs (paint_sort)
        top = box(True, 6, self.org_box, self.search, bar, self.sort_box)
        top.set_margin_bottom(8)
        left = box(True, 0, top, scrolled(self.tree))
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

        self.paint_orgs()
        self.paint_sort()
        self.paint_tree()
        if os.path.exists(pj.LIST):
            self.pages.set_visible_child_name("empty")
            threading.Thread(target=self.background, daemon=True).start()
        else:
            self.show_setup()
        GLib.idle_add(lambda: (self.update_counts(), self.focus_list()) and False)
        # the panel's popups (Move, Delete…) are a card over the dimmed panel, not windows of their own:
        # a window of this class would be tiled on the workspace like the panel itself
        self.card = box(True, 10, classes=("dialog",))
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)
        self.card.set_vexpand(True)   # the dimmed layer's height is the card's to centre in
        self.card.set_size_request(560, -1)
        self.dim = box(True, 0, self.card, classes=("dialog-dim",))
        self.dim.set_visible(False)
        self.content = box(False, 0, left, self.pages)
        overlay = Gtk.Overlay(child=self.content)
        overlay.add_overlay(self.dim)
        # behind a dialog the panel is blurred (the windows' blur, Appearance → Blur) and darkened like the
        # screen behind a popup (Appearance → Panels)
        on, dark = backdrop_setting()
        blur = window_blur()
        css = Gtk.CssProvider()
        css.load_from_string(recolor(f".blurred {{ filter: blur({max(blur // 2, 1) if on and blur else 0}px); }}\n"
                                     f".dialog-dim {{ background: alpha(#16161e, {min(dark + 0.3, 0.9) if on else 0.72}); }}"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 2)
        return overlay

    def build_detail(self):
        self.heading = label("", "heading", ellipsize=True)
        self.where = label("", "dim", ellipsize=True)
        # the name row: Pin, Move… and the deletes at its right end, away from the everyday buttons
        self.manage = box(False, 4)
        self.manage.set_valign(Gtk.Align.START)
        self.title_col = box(True, 0, self.heading, self.where)
        self.title_col.set_hexpand(True)
        # two cards across the page's width: Status (the checked-out branch's state, the remotes) and
        # Branches (every local branch against main; a click on one shows its history, Graph)
        # the state chips wrap onto a second line rather than widen the card (and the window, on a small screen)
        self.chips = wrap_box(8, 6)
        self.fetched_lbl = label("", "dim")
        self.urls = box(True, 2)
        self.note = label("", "amber", wrap=True)
        self.status = box(True, 8, self.card_head("Status", self.fetched_lbl), self.chips, self.urls, self.note,
                          classes=("pj-card",))
        self.others = box(True, 8, classes=("pj-card",))
        self.cards = box(False, 12, self.status, self.others)
        self.cards.set_homogeneous(True)
        self.cards.set_margin_top(14)
        self.actions = box(False, 8)
        self.actions.set_margin_top(14)

        self.tab_btns = {}
        bar = box(False, 0, classes=("tabs", "inner"))
        for name, title in (("changes", "Changes"), ("conflicts", "Conflicts"), ("graph", "Graph"), ("branches", "Branches")):
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
        self.change_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.change_list.add_css_class("changes")
        self.tab_pages.add_named(scrolled(self.change_list), "changes")
        self.conflict_list = box(True, 4)
        self.tab_pages.add_named(scrolled(self.conflict_list), "conflicts")
        self.tab_pages.add_named(scrolled(self.graph), "graph")
        self.tab_pages.add_named(scrolled(self.branch_list), "branches")
        self.tab_pages.set_margin_top(6)
        self.live = None   # the shown project's (status, HEAD, refs) at the last look: refresh() redraws on a change
        self.live_busy = False
        self.switch_tab("changes")
        return box(True, 0, box(False, 12, self.title_col, self.manage), self.cards, self.actions, self.tabs_bar,
                   self.tab_pages)

    def card_head(self, title, right):
        """A card's title line: its name, dim text at the right end."""
        right.set_hexpand(True)
        right.set_xalign(1.0)
        return box(False, 8, label(title, "card-title"), right)

    def switch_tab(self, name):
        self.tab = name
        self.tab_pages.set_visible_child_name(name)
        for n, b in self.tab_btns.items():
            (b.add_css_class if n == name else b.remove_css_class)("on")
        self.filter_box.set_visible(name == "graph")

    def leave_text(self):
        """A click outside a text field: out of the search, to the list (the panel's keys t, a, c… work there)."""
        w = self.window.get_focus()
        if w is not None and (w is self.search or w.is_ancestor(self.search)):
            self.focus_list()
        else:
            super().leave_text()

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

    def list_stamp(self):
        try:
            return os.stat(pj.LIST).st_mtime_ns
        except OSError:
            return None

    def disk_changed(self):
        """A listed project cloned or gone since the panel looked (a stat per project: cheap enough for
        refresh's every 2 s)."""
        return any((p.kind == "repo") != pj.is_repo(p.path) for p in self.projects if p.kind in ("repo", "missing"))

    def reload(self, select=None):
        self.list_seen = self.list_stamp()
        self.projects = load_projects()
        data = pj.load()
        self.pinned, self.marks = data["pinned"], data["status"]
        self.paint_orgs()
        self.paint_sort()
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
        """Every repo's health (changed files, unpushed commits, main behind), then the tree again (the
        Changes section is made of it). Then the repos not fetched for a while are fetched, quietly, so
        "main behind" is current, and those are looked at again."""
        repos = [p for p in self.projects if p.kind == "repo"]

        def one(p):
            set_health(p, pj.health(p.rel))
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(one, repos))
        GLib.idle_add(lambda: self.paint_tree(keep_scroll=True))
        stale = [p for p in repos if (pj.fetched_ago(p.path) or STALE_ALL + 1) > STALE_ALL
                 and p.rel not in self.fetching]
        if not stale or self.fetched_all:
            return
        self.fetched_all = True   # once per panel run

        def fetch(p):
            self.fetching.add(p.rel)
            pj.git(p.path, "fetch", "--all", "--prune", "-q", timeout=120)
            self.fetching.discard(p.rel)
            self.fetched.add(p.rel)
            one(p)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(fetch, stale))
        GLib.idle_add(lambda: self.paint_tree(keep_scroll=True))

    def paint_orgs(self):
        """The owner filter: every GitHub account / org in the list with its project count, then other
        remotes and local-only folders. A filter whose owner is gone from the list goes back to all."""
        counts, names = {}, {}
        for p in self.projects:
            k = owner_of(p)
            counts[k] = counts.get(k, 0) + 1
            if k[0] != "§":
                names.setdefault(k, pj.github_repo(p.entry["url"]).split("/")[0])
        names.update({"§other": "other remotes", "§local": "local only"})
        keys = sorted(k for k in counts if k[0] != "§") + [k for k in ("§other", "§local") if k in counts]
        if self.org not in counts:
            self.org = ""
        clear(self.org_box)
        dd = dropdown([("", "All owners")] + [(k, f"{names[k]}  {counts[k]}") for k in keys], self.org,
                      self.pick_org)
        dd.set_tooltip_text("only one GitHub account's or org's projects")
        dd.set_hexpand(True)   # the column's width, like the search under it
        self.org_box.append(dd)

    def pick_org(self, key):
        self.org = key
        self.paint_tree()
        self.focus_list()

    def paint_sort(self):
        """The sort dropdown: the folder tree, sections by status, or one status's projects only (with
        how many have it). A status no project has any more goes back to the folders."""
        counts = {}
        for p in self.projects:
            if p.rel in self.marks:
                counts[self.marks[p.rel]] = counts.get(self.marks[p.rel], 0) + 1
        if self.sort in STATUS and self.sort not in counts:
            self.sort = ""
        clear(self.sort_box)
        dd = dropdown([("", "Sort: by folder"), (BY_STATUS, "Sort: by status")]
                      + [(k, f"Only {name.lower()}  {counts[k]}") for k, (name, _c) in STATUS.items() if k in counts],
                      self.sort, self.pick_sort)
        dd.set_tooltip_text("the list by folder or by status (set on a project's page), or one status only")
        dd.set_hexpand(True)
        self.sort_box.append(dd)

    def pick_sort(self, key):
        self.sort = key
        self.paint_tree()
        self.focus_list()

    def status_rank(self, rel):
        st = self.marks.get(rel)
        return pj.STATUSES.index(st) if st in STATUS else len(pj.STATUSES)

    def shown(self):
        """The projects the owner filter and the sort dropdown's one status let through."""
        return [p for p in self.projects if (not self.org or owner_of(p) == self.org)
                and (self.sort not in STATUS or self.marks.get(p.rel) == self.sort)]

    # ---- the tree --------------------------------------------------------------------------------
    def needing(self, projects):
        """{tab: [rel]}: the projects each filter tab shows."""
        repos = [p for p in projects if p.kind == "repo"]
        return {"all": [p.rel for p in projects],
                "changes": [p.rel for p in repos if p.dirty or p.unpushed],
                "behind": [p.rel for p in repos if p.out_of_date],
                "conflicts": [p.rel for p in repos if p.conflicts]}

    def layout(self):
        """[(kind, key, depth, extra)] in on-screen order: kind is hint, group or project. A search is one
        flat list of matches, best first. All: the pinned projects, then the folders (each project once);
        another tab: just its projects, flat, by top folder."""
        q = self.search.get_text().strip()
        projects = self.shown()
        if q:
            found = []
            for p in projects:
                m = fuzzy(q, p.name, p.rel)
                if m:
                    found.append((-m[0], p.rel, m))
            return [("project", rel, 0, m) for _, rel, m in sorted(found)]
        if self.filter != "all":
            items = self.needing(projects)[self.filter]
            if not items:
                return [("hint", EMPTY[self.filter], 0, None)]
            rank = (lambda r: (self.status_rank(r), r)) if self.sort == BY_STATUS else None
            return [("project", r, 0, "where") for r in sorted(items, key=rank)]
        if self.sort == BY_STATUS:
            return self.status_layout(projects)
        by_rel = {p.rel: p for p in projects}
        pinned = [r for r in self.pinned if r in by_rel]
        out = [("project", r, 0, "pinned") for r in pinned]
        groups = set()
        for p in projects:
            if p.rel in pinned:   # it's at the top already
                continue
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

    def status_layout(self, projects):
        """Sort by status: a section per status, under way first, then those with none; each project once
        (pinned or not), with its folder."""
        out = []
        for key in pj.STATUSES + [""]:
            rels = sorted(p.rel for p in projects if self.marks.get(p.rel, "") == key)
            if not rels:
                continue
            group = f"{BY_STATUS}/{key or 'none'}"
            name, colour = STATUS.get(key, ("No status", "#565f89"))
            out.append(("group", group, 0, (name, colour, len(rels))))
            if group not in self.folded:
                out += [("project", r, 0, "where") for r in rels]
        return out

    def set_filter(self, key):
        self.filter = key
        self.paint_tree()
        self.focus_list()

    def paint_filters(self):
        """The tabs: each with how many it would show (an empty one greyed out), the current one marked."""
        counts = {k: len(v) for k, v in self.needing(self.shown()).items()}
        for key, name in FILTERS:
            b = self.filter_btns[key]
            n = counts[key]
            b.set_label(f"{name} {n}" if n or key == "all" else name)
            b.set_sensitive(bool(n) or key in ("all", self.filter))
            (b.add_css_class if key == self.filter else b.remove_css_class)("on")
        self.sync_btn.set_label("Syncing\u2026" if self.syncing else "\uf021 Sync")
        self.sync_btn.set_sensitive(not self.syncing)

    def paint_tree(self, keep_scroll=False):
        # nothing it shows changed (a sync or a live check that found the same): no rebuild, no flicker
        self.paint_filters()
        seen = (self.layout(), self.selected, self.syncing, tuple(self.pinned), tuple(sorted(self.marks.items())), self.filter,
                tuple((p.rel, p.kind, p.branch, p.dirty, p.unpushed, p.out_of_date, p.to_sync, p.conflicts) for p in self.projects))
        if keep_scroll and seen == self.tree_seen:
            return False
        self.tree_seen = seen
        adj = self.tree.get_parent().get_vadjustment() if keep_scroll and self.tree.get_parent() else None
        top = adj.get_value() if adj else 0
        clear(self.tree)
        self.rows = {}
        by_rel = {p.rel: p for p in self.projects}
        first, picked = None, False
        for kind, key, depth, extra in self.layout():
            if kind == "hint":
                row = Gtk.ListBoxRow(child=label(key, "dim", wrap=True), selectable=False, activatable=False)
                row.add_css_class("hint-row")
            elif kind == "group":
                shut = key in self.folded
                if extra:   # a status's section: its name in its colour
                    name, colour, n = extra
                    title = label(f"<span foreground='{recolor(colour)}'>{GLib.markup_escape_text(name)}</span>",
                                  "bold", markup=True)
                else:
                    n = sum(p.rel.startswith(key + "/") and p.rel not in self.pinned for p in self.shown())
                    title = label(os.path.basename(key), "bold")
                line = box(False, 8, label(G_SHUT if shut else G_OPEN, "accent", "pj-glyph"), title, label(str(n), "dim"))
                row = Gtk.ListBoxRow(child=line)
                row.add_css_class("group-row")
            else:
                row = Gtk.ListBoxRow(child=self.project_line(by_rel[key], extra))
                self.rows.setdefault(key, []).append(row)
                first = first or row
            row.kind, row.key = kind, key
            # a level is a glyph and its gap: a folder's rows start right under the folder's name
            row.get_child().set_margin_start(depth * INDENT)
            self.tree.append(row)
            if kind == "project" and key == self.selected and not picked:
                self.tree.select_row(row)
                picked = True
        if self.search.get_text().strip() and first:
            self.tree.select_row(first)
        if adj:   # put the view back where it was as the new rows are laid out, in that same frame
            def back(a):
                a.set_value(min(top, max(a.get_upper() - a.get_page_size(), 0)))
            hid = adj.connect("changed", back)
            GLib.timeout_add(300, lambda: adj.handler_is_connected(hid) and adj.disconnect(hid) and False)
            back(adj)
        return False

    def project_line(self, p, match):
        """A project's row: its icon (a pin when pinned), its name (with its folder in a filtered list or a
        search), the branch at the right when it isn't the main one; under it, in words, what needs doing:
        17 changed · 3 to push · 5 to pull · 2 new branches · ⚠ 1 to merge. Nothing to do: one line."""
        if p.kind == "repo":
            # the branch only when it isn't main: that's the case worth seeing (main on every row was noise)
            # (main isn't known until its health is in: main / master stand for it till then)
            off_main = p.branch not in ((p.main,) if p.main else ("", "main", "master"))
            glyph, gclass, right, rclass = G_REPO, "dim", p.branch if off_main else "", "dim"
        elif p.kind == "missing":
            glyph, gclass, right, rclass = G_MISSING, "dim", "not cloned", "dim"
        else:
            glyph, gclass, right, rclass = G_LOCAL, "amber", "local only", "amber"
        if match == "pinned":
            glyph, gclass = G_PIN, "accent"
        if match in ("where", "pinned"):   # out of its folder: the folder (dim) before the name
            parent = os.path.dirname(p.rel)
            if parent and os.path.basename(parent).replace("-", "_") in p.name.replace("-", "_"):   # housefind/housefind_1
                parent = os.path.dirname(parent)
            name = text = label((f"<span foreground='{recolor('#565f89')}'>{GLib.markup_escape_text(parent)}/</span>"
                                 if parent else "") + GLib.markup_escape_text(p.name), markup=True, ellipsize=True)
            name.set_ellipsize(Pango.EllipsizeMode.START)   # a long path loses its start, never the name
        elif match:
            name = label(marked(p.name, match[1], HIT), markup=True, ellipsize=True)
            where = label(marked(os.path.dirname(p.rel), match[2], HIT), "setting-sub", markup=True, ellipsize=True)
            where.set_max_width_chars(10)
            text = box(True, 1, name, where)
        else:
            name = text = label(p.name, *(("dim",) if p.kind == "missing" else ()), ellipsize=True)
        name.set_max_width_chars(10)   # the column keeps its width: long names ellipsize instead
        text.set_hexpand(True)
        line = box(False, 8, label(glyph, gclass, "pj-glyph"), text)
        if right:
            branch = label(right, rclass, ellipsize=True)
            branch.set_max_width_chars(14)   # a long branch name doesn't squeeze the project's
            branch.set_xalign(1.0)
            if p.kind == "repo":
                branch.set_tooltip_text(f"checked out: {right} (not the main branch)")
            line.append(branch)
        # its status at the right end, lined up down the list (by status, its section says it)
        if p.rel in self.marks and self.marks[p.rel] in STATUS and self.sort != BY_STATUS:
            name, colour = STATUS[self.marks[p.rel]]
            line.append(label(f"<span foreground='{recolor(colour)}'>{name.lower()}</span>", "pj-status", markup=True))
        s = lambda n, one, many: f"{n} {one if n == 1 else many}"
        parts, tips = [], []
        for show, words, colour, tip in (
                (p.dirty, s(p.dirty, "changed", "changed"), "#e0af68", s(p.dirty, "uncommitted change", "uncommitted changes")),
                (p.unpushed, s(p.unpushed, "to push", "to push"), "#e0af68",
                 s(p.unpushed, "commit on no remote yet", "commits on no remote yet")),
                (p.to_sync, s(p.to_sync, "to pull", "to pull"), "#7dcfff", "behind GitHub: " + ", ".join(
                    f"{b} ↓{n}" for b, n in sorted(p.behind_branches.items())) + " (Sync brings them down)"),
                (p.new_branches, s(p.new_branches, "new branch", "new branches"), "#7dcfff",
                 "on GitHub, not here yet (Sync makes them)"),
                (p.conflicts, "\u26a0 " + s(p.conflicts, "to merge", "to merge"), "#f7768e",
                 "needs merging by hand: see its Conflicts tab")):
            if show:
                parts.append(f"<span foreground='{recolor(colour)}'>{GLib.markup_escape_text(words)}</span>")
                tips.append(f"{words}: {tip}")
        if not parts:
            return line
        status = label(f"<span foreground='{recolor('#3b4261')}'> \u00b7 </span>".join(parts), "pj-status",
                       markup=True, ellipsize=True)
        status.set_tooltip_text("\n".join(tips))
        status.set_margin_start(INDENT)   # under the name, past the icon
        return box(True, 2, line, status)

    def tree_clicked(self, gesture, _n, x, y):
        hit = self.tree.pick(x, y, Gtk.PickFlags.DEFAULT)
        while hit is not None and not isinstance(hit, (Gtk.Button, Gtk.ListBoxRow)):
            hit = hit.get_parent()
        if isinstance(hit, Gtk.Button):
            return   # a button in a section's header (Sync all): it does its own thing, no folding
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
        elif row.kind == "project":
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
        self.live = None
        # the same project again (a sync, a commit, a live change): what's on the page stays until each part
        # is replaced by its new content, so nothing blanks in between; another project starts empty
        same = p.rel == self.shown_rel
        self.shown_rel = p.rel
        self.heading.set_text(p.name)
        self.where.set_text(tilde(p.path))
        if not same:
            clear(self.chips)
            self.fetched_lbl.set_text("")
            clear(self.others)
            self.others.set_visible(False)
            clear(self.change_list)
            clear(self.conflict_list)
        clear(self.urls)
        e = p.entry
        if p.kind != "local":
            def bare(url):   # the card is half the page: no scheme or .git (the tooltip has it all)
                return url.removeprefix("https://").removeprefix("http://").removesuffix(".git")
            self.urls.append(label(bare(e["url"]), "sub", ellipsize=True))
            for name, url in e.get("remotes", {}).items():
                self.urls.append(label(f"{name}  {bare(url)}", "dim", ellipsize=True))
            for w in children(self.urls):
                w.set_tooltip_text(e["url"] if w is self.urls.get_first_child() else None)
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
        if not same or not has_git:
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
        """The project's buttons: what you do with it on the left; Move… and the deletes at the right."""
        clear(self.actions)
        clear(self.manage)
        self.push_btn = self.commit_btn = None
        if p.kind == "missing":
            self.actions.append(button("Clone", self.clone, "primary", tooltip="every branch, into its folder"))
        else:
            self.actions.append(button("VS Code", self.open_code, "primary", tooltip="Enter"))
            self.actions.append(button("Terminal", self.open_term, tooltip="t"))
            self.actions.append(button("Agent", self.open_agent, tooltip="a: an agentmux thread here"))
            self.commit_btn = button("Commit…", self.ask_commit, tooltip="c: your changes, with a message")
            self.commit_btn.set_visible(p.kind == "repo" and p.dirty > 0)
            self.actions.append(self.commit_btn)
            if p.kind == "repo":
                self.actions.append(button("Sync", self.sync_one, tooltip="s: bring GitHub's changes down "
                                           "(every branch; never merges, pushes or touches your edits)"))
                self.push_btn = button("Push", self.push, tooltip="push the checked-out branch")
                self.push_btn.set_sensitive(False)
                self.actions.append(self.push_btn)
        if p.kind != "local" and web_url(p.entry["url"]):
            self.actions.append(button("GitHub", self.open_web, "flat", tooltip="g"))
        if p.kind == "missing":   # nothing here: only the list entry (a repo that's gone, say)
            self.manage.append(button("Forget", self.ask_forget, "flat", "danger", tooltip="drop it from the list"))
        else:
            mark = dropdown([("", "No status")] + [(k, name) for k, (name, _c) in STATUS.items()],
                            self.marks.get(p.rel, ""), lambda key, rel=p.rel: self.set_mark(rel, key))
            mark.set_tooltip_text("its status: the list shows it, Sort: by status groups by it (on every PC)")
            self.manage.append(mark)
            pinned = p.rel in self.pinned
            self.manage.append(button("Unpin" if pinned else "Pin", self.toggle_pin, "flat",
                                      tooltip="p: " + ("off the Pinned section" if pinned else "first in the list, on every PC")))
            self.manage.append(button("Move…", self.ask_move, "flat", tooltip="m: to another folder in ~/code"))
            self.manage.append(button("Delete", self.ask_delete, "flat", "danger", tooltip="Delete: the folder"))
        # Delete on GitHub: only once GitHub says this account may (admin on the repo); asked in a thread
        self.gh_btn = button("Delete on GitHub", self.ask_delete_github, "flat", "danger")
        self.gh_btn.set_visible(False)
        self.manage.append(self.gh_btn)
        repo = pj.github_repo(p.entry.get("url", "")) if p.kind != "local" else None
        if repo:
            if repo in self.gh_admin:
                self.gh_btn.set_visible(self.gh_admin[repo])
            else:
                def ask(rel=p.rel):
                    self.gh_admin[repo] = pj.can_delete_on_github(p.entry["url"])
                    GLib.idle_add(lambda: (self.selected == rel and self.gh_btn.set_visible(self.gh_admin[repo]))
                                  and False)
                threading.Thread(target=ask, daemon=True).start()

    def load_git(self, p, gen):
        """What the detail page shows from git (in this thread), then the graph."""
        branches = pj.branch_states(p.path)
        main = pj.default_branch(p.path)
        # each local branch against main: how many of its commits main lacks, how many of main's it lacks
        base = (f"refs/heads/{main}" if any(b["local"] and b["name"] == main for b in branches)
                else f"refs/remotes/origin/{main}") if main else ""
        for b in branches:
            b["main"], b["vs_main"] = main, None
            if base and b["local"] and b["name"] != main:
                out = git_lines(p.path, "rev-list", "--left-right", "--count", f"{base}...refs/heads/{b['name']}")
                if out and len(out[0].split()) == 2:
                    behind, ahead = (int(x) for x in out[0].split())
                    if behind and not ahead and pj.git(p.path, "diff", "--quiet", base, f"refs/heads/{b['name']}")[0]:
                        behind = 0   # main is ahead only by merge commits of this branch: the same files
                    b["vs_main"] = (ahead, behind)
        dirty, ahead, behind = pj.state(p.rel)
        has_origin = "origin" in pj.remotes(p.path)
        changes = status_lines(p.path)
        old = (p.dirty, p.unpushed, p.behind, p.out_of_date, p.conflicts)   # its badges may be stale (a push since the panel opened)
        set_health(p, pj.health(p.rel))
        if (p.dirty, p.unpushed, p.behind, p.out_of_date, p.conflicts) != old:
            GLib.idle_add(lambda: self.paint_tree(keep_scroll=True))
        seen = self.look(p)   # the baseline the live checks compare against
        GLib.idle_add(lambda: gen == self.gen and setattr(self, "live", seen) and False)
        GLib.idle_add(self.paint_git, p, gen, branches, dirty, ahead, behind, has_origin)
        GLib.idle_add(self.paint_changes, p, gen, changes)
        clashes = pj.conflicts(p.rel)
        GLib.idle_add(self.paint_conflicts, p, gen, clashes)
        self.load_graph(p, gen, self.ref)

    # ---- live: the shown project's git status, every `interval` s while the panel shows --------------
    def look(self, p):
        """What changes when you work in it: the working tree, HEAD, the branches (a commit, a checkout,
        a pull, an edit)."""
        return ("\n".join(status_lines(p.path)), "\n".join(git_lines(p.path, "rev-parse", "HEAD")),
                "\n".join(git_lines(p.path, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes")))

    def refresh(self):
        # the list or ~/code changed outside the panel (projects delete / move / clone on the command line,
        # the other PC's changes pulled): show it, not what was there when the panel opened
        if (self.list_stamp() != self.list_seen or self.disk_changed()) and not self.busy:
            self.reload()
        p = self.current()
        if (not p or p.kind != "repo" or self.live_busy or self.dim.get_visible()
                or self.pages.get_visible_child_name() != "detail"):
            return
        self.live_busy = True
        gen = self.gen

        def go():
            seen = self.look(p)
            GLib.idle_add(self.live_seen, p, gen, seen)
        threading.Thread(target=go, daemon=True).start()

    def live_seen(self, p, gen, seen):
        self.live_busy = False
        if gen != self.gen or p.rel != self.selected:
            return False
        before, self.live = self.live, seen
        if before is not None and before != seen:   # something changed: the whole page again (no fetch)
            def again():
                old = (p.dirty, p.unpushed, p.behind, p.out_of_date, p.conflicts)
                set_health(p, pj.health(p.rel))
                p.branch = pj.current_branch(p.path)
                if (p.dirty, p.unpushed, p.behind, p.out_of_date, p.conflicts) != old or seen[1] != (before[1] if before else None):
                    GLib.idle_add(lambda: self.paint_tree(keep_scroll=True))   # its badges, the Changes section
                self.load_git(p, gen)
            threading.Thread(target=again, daemon=True).start()
        return False

    def paint_conflicts(self, p, gen, c):
        """The Conflicts tab: files left with conflicts in the folder (from a merge or pull you started),
        then each branch where you and GitHub both have new commits, with the files that would clash.
        Nothing here is merged automatically: VS Code is where you merge and resolve."""
        if gen != self.gen:
            return False
        clear(self.conflict_list)
        n = len(c["unmerged"]) + len(c["diverged"])
        self.tab_btns["conflicts"].set_label(f"Conflicts {n}" if n else "Conflicts")
        if not n:
            self.conflict_list.append(label("No conflicts: every branch is in step with GitHub, or only ahead.", "dim"))
            return False
        if c["unmerged"]:
            self.conflict_list.append(label("Unresolved in your folder: resolve them in VS Code, then commit", "bold"))
            for f in c["unmerged"]:
                self.conflict_list.append(box(False, 10, label("U", "st", "red"), label(f, ellipsize=True)))
        for name, ahead, behind, files in c["diverged"]:
            head = box(False, 10, label(name, "bold"),
                       label(f"you have {ahead} commit{'s' * (ahead > 1)}, GitHub has {behind}", "dim"))
            head.set_margin_top(10)
            self.conflict_list.append(head)
            if files:
                self.conflict_list.append(label(f"would clash in {len(files)} file{'s' * (len(files) > 1)}: "
                                                "merge it in VS Code (pull, then resolve)", "red"))
                for f in files:
                    self.conflict_list.append(box(False, 10, label("!", "st", "red"), label(f, ellipsize=True)))
            else:
                self.conflict_list.append(label("no clashing files: it would merge cleanly; pull it in VS Code "
                                                "(or a terminal: git pull) when you're ready", "amber"))
        return False

    def paint_changes(self, p, gen, lines):
        """The Changes tab: each changed file, what happened to it (staged or not), live."""
        if gen != self.gen:
            return False
        clear(self.change_list)
        what = {"M": ("modified", "amber"), "A": ("added", "green"), "D": ("deleted", "red"),
                "R": ("renamed", "cyan"), "C": ("copied", "cyan"), "U": ("conflict", "red"), "?": ("new", "green"),
                "T": ("type changed", "amber")}
        for l in lines:
            if len(l) < 4:
                continue
            x, y, path = l[0], l[1], l[3:]
            code = "U" if "U" in (x, y) or (x, y) in (("A", "A"), ("D", "D")) else (x if x not in " ?" else y)
            word, colour = what.get(code, ("changed", "amber"))
            staged = x not in " ?" and code != "U"
            row = box(False, 10, label(code if code != "?" else "+", "st", colour),
                      label(path.replace(" -> ", "  →  "), ellipsize=True),
                      label(word + (" · staged" if staged else ""), "dim"))
            row.get_first_child().get_next_sibling().set_hexpand(True)
            self.change_list.append(Gtk.ListBoxRow(child=row, activatable=False))
        if not any(len(l) >= 4 for l in lines):
            self.change_list.append(Gtk.ListBoxRow(child=label("Nothing changed: the working tree is clean.", "dim"),
                                                   activatable=False))
        n = sum(len(l) >= 4 for l in lines)
        self.tab_btns["changes"].set_label(f"Changes {n}" if n else "Changes")
        if getattr(self, "commit_btn", None) is not None:
            self.commit_btn.set_visible(n > 0)
        return False

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

    def paint_others(self, branches):
        """The branches against main. Main is a heading of its own (the reference, with its GitHub state);
        under it, every other local branch a row: its state on GitHub, then how it stands against main
        (in main, the same, or how many of its commits main doesn't have yet). The checked-out one has
        the dot; a branch's name shows its history (Graph)."""
        clear(self.others)
        local = [b for b in branches if b["local"]]
        remote_only = sum(not b["local"] for b in branches)
        main = next((b["main"] for b in branches if b.get("main")), "")
        # shown even for a lone main, so the Status card beside it keeps half the width, not all of it
        self.others.set_visible(bool(local))
        if not local:
            return
        self.others.append(self.card_head("Branches", label(f"compared to {main}" if len(local) > 1 else "", "dim",
                                                            ellipsize=True)))

        def on_github(b):
            if b["gone"]:
                return "✗ deleted on GitHub", "red"
            if not b["upstream"]:
                return "• never pushed", "amber"
            if b["ahead"] and b["behind"]:
                return f"↑{b['ahead']} ↓{b['behind']} diverged", "red"
            if b["ahead"]:
                return f"↑{b['ahead']} to push", "amber"
            if b["behind"]:
                return f"↓{b['behind']} to sync", "cyan"
            return "✓ pushed", "green"

        def name_button(b):
            text = label(b["name"], *(("bold",) if b["head"] else ()))
            text.set_ellipsize(Pango.EllipsizeMode.MIDDLE)   # a long name gives way on a small screen, not the card
            text.set_width_chars(min(len(b["name"]), 10))       # but keeps enough to tell it apart
            name = Gtk.Button(child=text, tooltip_text=f"{b['name']}: its history (Graph)")
            name.add_css_class("flat")
            name.add_css_class("branch-chip")
            name.connect("clicked", lambda _b, n=b["name"]: self.show_graph_of(n, tab=True))
            return name

        base = next((b for b in local if b["name"] == main), None)
        others = sorted((b for b in local if b["name"] != main), key=lambda b: (not b["head"], -b["unix"]))
        block = box(True, 4)
        grid = Gtk.Grid(column_spacing=18, row_spacing=1)   # one grid: main's row lines up with the others
        first = 0
        if base:   # the heading: main, its GitHub state, what the rows below are compared to; a rule under it
            text, cls = on_github(base)
            hint = label("the only branch" if not others else "", "dim")
            for c, w in enumerate((label("●" if base["head"] else "", "accent", "dot-col"), name_button(base),
                                   label(text, cls), hint)):
                w.set_valign(Gtk.Align.CENTER)
                grid.attach(w, c, 0, 1, 1)
            if others:
                rule = Gtk.Separator()
                rule.add_css_class("branch-rule")
                grid.attach(rule, 0, 1, 4, 1)
            first = 2
        for r, b in enumerate(others[:MAX_BRANCH_ROWS], first):
            gh, gcls = on_github(b)
            if b["vs_main"] is None:
                mn, mcls, tip = "", "dim", ""
            elif b["vs_main"][0]:
                n = b["vs_main"][0]
                mn, mcls = f"{n} not in {main}", "amber"
                tip = f"{n} commit{'s' * (n > 1)} on {b['name']} that {main} doesn't have yet"
            elif b["vs_main"][1]:
                mn, mcls = f"in {main}", "green"
                tip = f"all of {b['name']} is in {main}; {main} has {b['vs_main'][1]} newer commit{'s' * (b['vs_main'][1] > 1)}"
            else:
                mn, mcls, tip = f"same as {main}", "green", f"{b['name']} and {main} are the same"
            vs = label(mn, mcls)
            vs.set_tooltip_text(tip or None)
            for c, w in enumerate((label("●" if b["head"] else "", "accent", "dot-col"), name_button(b), label(gh, gcls), vs)):
                w.set_valign(Gtk.Align.CENTER)
                grid.attach(w, c, r, 1, 1)
        block.append(grid)
        more = []
        if len(others) > MAX_BRANCH_ROWS:
            more.append(f"{len(others) - MAX_BRANCH_ROWS} more")
        if remote_only:
            more.append(f"{remote_only} only on GitHub")
        if more:
            block.append(label(" · ".join(more) + " (Branches tab)", "dim", wrap=True))
        self.others.append(block)

    def paint_git(self, p, gen, branches, dirty, ahead, behind, has_origin):
        if gen != self.gen:
            return False
        self.paint_others(branches)
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
        if p.behind and (not cur or cur["name"] != p.main or not behind):   # main behind, the branch shown or not
            chip(f"{p.main} ↓{p.behind} behind GitHub", "cyan")
        self.fetched_lbl.set_text("fetching…" if p.rel in self.fetching else ago(pj.fetched_ago(p.path)))
        if self.push_btn:
            self.push_btn.set_sensitive(bool(ahead) or (ahead is None and has_origin))
        self.paint_branches(branches)
        self.branch_names = [b["name"] for b in branches]
        self.paint_filter()
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
        if tab:   # a branch's name (the table, the Branches tab): the Graph tab on it; only the graph reloads
            self.switch_tab("graph")
        if ref != self.ref or tab:
            self.ref = ref
            if tab:
                self.paint_filter()   # the filter shows that branch (from the dropdown itself it already does)
            threading.Thread(target=self.load_graph, args=(p, self.gen, ref), daemon=True).start()

    def paint_filter(self):
        """The graph's branch filter, showing self.ref."""
        clear(self.filter_box)
        self.filter_box.append(dropdown([("", "All branches")] + [(n, n) for n in self.branch_names],
                                        self.ref, self.show_graph_of))

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

    def toggle_pin(self):
        p = self.current()
        if not p or p.kind == "missing":
            return
        on = p.rel not in self.pinned
        self.pinned = [r for r in self.pinned if r != p.rel] + ([p.rel] if on else [])   # shown at once
        self.paint_tree(keep_scroll=True)
        self.paint_actions(p)

        def save():
            problem = pj.pin(p.rel, on)
            if problem:
                GLib.idle_add(lambda: self.say(f"{p.name}: {problem}", "bad") and False)
        threading.Thread(target=save, daemon=True).start()

    def set_mark(self, rel, key):
        """A project's status from its page's dropdown: shown at once, saved to the list in a thread."""
        if key:
            self.marks[rel] = key
        else:
            self.marks.pop(rel, None)
        self.paint_sort()
        self.paint_tree(keep_scroll=True)

        def save():
            problem = pj.mark(rel, key)
            if problem:
                GLib.idle_add(lambda: self.say(f"{os.path.basename(rel)}: {problem}", "bad") and False)
        threading.Thread(target=save, daemon=True).start()

    # ---- the popups: move, delete, delete on GitHub, forget ------------------------------------------
    def open_dialog(self, title, *widgets, focus=None, handlers=(), width=560):
        """handlers: (widget, handler id) pairs, disconnected when the popup goes: a list being
        destroyed emits row-selected for its rows, and a handler still connected then reached into the
        half-destroyed list (SIGSEGV in gtk_list_box_remove_all)."""
        self.drop_handlers()
        self.handlers = list(handlers)
        win_w = self.window.get_width() if self.host and self.window else 1180
        self.card.set_size_request(min(width, max(win_w - 80, 360)), -1)   # never wider than the panel
        self.content.add_css_class("blurred")
        clear(self.card)
        self.card.append(para(title, "dialog-title"))
        for w in widgets:
            self.card.append(w)
        self.dim.set_visible(True)
        GLib.idle_add(lambda: (focus or self.card).grab_focus() and False)

    def close_dialog(self):
        self.drop_handlers()
        self.dim.set_visible(False)
        self.content.remove_css_class("blurred")
        self.focus_list()

    def drop_handlers(self):
        for widget, hid in getattr(self, "handlers", []):
            widget.disconnect(hid)
        self.handlers = []

    def dialog_buttons(self, *buttons):
        row = box(False, 8, label(""), button("Cancel", self.close_dialog, "flat", tooltip="Esc"), *buttons)
        row.get_first_child().set_hexpand(True)
        row.set_margin_top(10)
        return row

    def run_job(self, start, work, after=None):
        """Close the popup, say(start), run work() -> (ok, message) in a thread, say how it went, then
        after(ok) (default: reload the list)."""
        self.close_dialog()
        self.say(start, seconds=300)

        def go():
            ok, msg = work()
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad", seconds=8),
                                   (after or (lambda ok: self.reload()))(ok)) and False)
        threading.Thread(target=go, daemon=True).start()

    def busy_note(self, p):
        """A red line naming what runs in the project (moving or deleting it would break it), or None."""
        busy = pj.busy_in(p.rel)
        return para(f"{', '.join(busy)} {'are' if len(busy) > 1 else 'is'} running in it: close "
                     f"{'them' if len(busy) > 1 else 'it'} first.", "red") if busy else None

    def ask_move(self):
        """Move: the folder tree of ~/code (pick where it goes), a new folder inside the picked one if
        you want one, its own name; the line under them shows where it ends up."""
        p = self.current()
        if not p or p.kind == "missing":
            return
        own = lambda g: g == p.rel or g.startswith(p.rel + "/")
        folders = [""] + sorted(g for g in pj.groups() if not own(g))
        tree = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        tree.add_css_class("folders")
        for g in folders:
            depth = g.count("/") + 1 if g else 0
            line = box(False, 8, label("\uf07b", "accent"), label(os.path.basename(g) or "~/code", *(() if g else ("bold",))))
            line.set_margin_start(depth * 18)
            row = Gtk.ListBoxRow(child=line)
            row.rel = g
            tree.append(row)
        scroll = scrolled(tree, vexpand=False)   # the folder tree: up to 260 px, less on a small window
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(max(min(260, (self.window.get_height() if self.host and self.window else 680) - 460), 120))
        new_dir = Gtk.Entry(placeholder_text="new folder inside it (optional)", hexpand=True)
        name = Gtk.Entry(text=p.name, hexpand=True)
        where = para("", "sub")
        move_btn = button("Move", lambda: go(), "primary")

        def target():
            row = tree.get_selected_row()
            parts = [row.rel if row else "", new_dir.get_text().strip().strip("/"), name.get_text().strip().strip("/")]
            return "/".join(x for x in parts if x)

        def update(*_):
            t = target()
            taken = t != p.rel and os.path.exists(f"{pj.ROOT}/{t}")
            where.set_text("stays where it is" if t == p.rel else f"{tilde(pj.ROOT)}/{t}" +
                           ("   (taken)" if taken else ""))
            move_btn.set_sensitive(bool(name.get_text().strip()) and t != p.rel and not taken and not busy)

        def go():
            if move_btn.get_sensitive():
                t = target()
                self.run_job(f"moving {p.name}…", lambda: pj.move(p.rel, t),
                             lambda ok: self.reload(select=t if ok else p.rel))
        handlers = [(tree, tree.connect("row-selected", update))]
        for e in (new_dir, name):
            handlers += [(e, e.connect("changed", update)), (e, e.connect("activate", lambda *_: go()))]
        busy = self.busy_note(p)
        here = folders.index(os.path.dirname(p.rel)) if os.path.dirname(p.rel) in folders else 0
        tree.select_row(tree.get_row_at_index(here))
        update()
        self.open_dialog(f"Move {p.name}", para(f"from {tilde(p.path)}", "dim"), scroll,
                         box(False, 10, label("New folder", "form-label"), new_dir),
                         box(False, 10, label("Name", "form-label"), name),
                         box(False, 10, label("Goes to", "form-label"), where),
                         *([busy] if busy else []),
                         para("Its HDD backup copy moves with it (codesync), and the list records it.", "dim"),
                         self.dialog_buttons(move_btn), focus=tree, handlers=handlers)

    def ask_commit(self):
        """Commit: its own popup (commitgui.py), like every other panel; the live look sees the result."""
        p = self.current()
        if p and p.kind == "repo":
            self.launch("python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "commitgui.py"), p.rel)

    def ask(self):
        """Ask: a read-only chat about the projects, its own popup (askgui.py), about the selected one first."""
        p = self.current()
        self.launch("python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "askgui.py"),
                    *([p.rel] if p else []))

    def ask_delete(self):
        p = self.current()
        if not p or p.kind == "missing":
            return
        risk, busy = pj.at_risk(p.rel), self.busy_note(p)
        lines = [para(f"{tilde(p.path)} goes, and it's dropped from the list.", "sub")]
        if risk:
            lines.append(para("Only this copy has: " + ", ".join(risk) + ".", "red"))
        if busy:
            lines.append(busy)
        lines.append(para("If codesync backs it up, the HDD copy goes to its trash (kept 30 days).", "dim"))
        go = button("Delete folder", lambda: self.run_job(f"deleting {p.name}…", lambda: pj.delete(p.rel),
                                                          lambda ok: (self.reload(), ok and self.pages.set_visible_child_name("empty"))),
                    "danger-fill")
        go.set_sensitive(not busy)
        self.open_dialog(f"Delete {p.name}?", *lines, self.dialog_buttons(go), focus=go)

    def ask_delete_github(self):
        p = self.current()
        repo = pj.github_repo(p.entry.get("url", "")) if p else None
        if not repo:
            return
        short = repo.split("/")[1]
        confirm = Gtk.Entry(placeholder_text=f"type {short} to confirm", hexpand=True)
        go = button("Delete on GitHub", lambda: start(), "danger-fill")
        go.set_sensitive(False)
        handlers = [(confirm, confirm.connect("changed", lambda e: go.set_sensitive(e.get_text().strip() == short))),
                    (confirm, confirm.connect("activate", lambda *_: go.get_sensitive() and start()))]

        def start():
            self.close_dialog()
            self.say(f"deleting {repo} on GitHub…", seconds=120)

            def work():
                ok, msg, scope = pj.delete_on_github(p.rel) if p.kind != "missing" else self.delete_missing_on_github(repo)
                GLib.idle_add(lambda: self.after_github(p, repo, ok, msg, scope) and False)
            threading.Thread(target=work, daemon=True).start()
        here = ("The folder here stays: its GitHub remote is removed and it becomes local-only."
                if p.kind != "missing" else "It isn't cloned here: it's dropped from the list too.")
        self.open_dialog(f"Delete {repo} on GitHub?",
                         para("The repository goes with its issues, pull requests, releases and wiki. GitHub can "
                               "restore it for 90 days (Settings → Repositories → Deleted repositories).", "sub"),
                         para(here, "dim"), confirm, self.dialog_buttons(go), focus=confirm, handlers=handlers)

    def delete_missing_on_github(self, repo):
        r = subprocess.run(["gh", "repo", "delete", repo, "--yes"], capture_output=True, text=True, timeout=60)
        if r.returncode:
            return False, pj.last_line(r.stderr, "gh failed"), "delete_repo" in r.stderr
        data = pj.load()
        data["projects"] = [e for e in data["projects"] if pj.github_repo(e["url"]) != repo]
        pj.save(data)
        pj.commit(f"deleted {repo} on GitHub")
        return True, f"{repo} deleted on GitHub (GitHub can restore it for 90 days)", False

    def after_github(self, p, repo, ok, msg, scope):
        if scope:   # gh's token can't delete repos: grant it once, in a terminal (a browser login)
            self.say("")
            self.open_dialog("GitHub needs your permission first",
                             para("gh can't delete repositories yet. Grant it once (gh auth refresh adds the "
                                   "delete_repo scope; a browser page asks you), then delete again.", "sub"),
                             self.dialog_buttons(button("Grant permission", lambda: (
                                 self.terminal(["gh", "auth", "refresh", "-h", "github.com", "-s", "delete_repo"],
                                               "gh auth refresh"), self.close_dialog()), "primary")))
            return
        self.say(msg, "ok" if ok else "bad", seconds=10)
        if ok:
            self.gh_admin.pop(repo, None)
            self.reload(select=p.rel if p.kind != "missing" else None)

    def ask_forget(self):
        p = self.current()
        if not p:
            return

        def work():
            data = pj.load()
            data["projects"] = [e for e in data["projects"] if e["path"] != p.rel]
            pj.save(data)
            problem = pj.commit(f"forget {p.rel}")
            return True, f"{p.name}: dropped from the list" + (f" ({problem})" if problem else "")
        self.open_dialog(f"Forget {p.name}?",
                         para("It's dropped from the list. Nothing else changes: on GitHub, or on another PC.",
                               "sub"),
                         self.dialog_buttons(button("Forget", lambda: self.run_job("forgetting…", work), "danger-fill")))

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

    # ---- Sync: GitHub's side down, never merging or pushing (pj.sync) -------------------------------
    def sync_one(self):
        p = self.current()
        if not p or p.kind != "repo":
            return
        self.say(f"syncing {p.name}…", seconds=300)

        def go():
            rep = pj.sync(p.rel)
            set_health(p, pj.health(p.rel))
            p.branch = pj.current_branch(p.path)
            GLib.idle_add(lambda: (self.say(f"{p.name}: {sync_summary([rep])}", "bad" if rep["error"] else "ok",
                                            seconds=12),
                                   self.paint_tree(keep_scroll=True), self.show_detail(fetch=False)) and False)
        threading.Thread(target=go, daemon=True).start()

    def sync_all(self):
        """Every repo, 8 at a time, the progress in the status line; then a summary."""
        if self.syncing:
            return
        repos = [p for p in self.projects if p.kind == "repo"]
        self.syncing, done, reps = True, [0], []

        def one(p):
            rep = pj.sync(p.rel)
            set_health(p, pj.health(p.rel))
            p.branch = pj.current_branch(p.path)
            reps.append(rep)
            done[0] += 1
            GLib.idle_add(lambda n=done[0]: self.say(f"syncing {n} / {len(repos)}…", seconds=300) and False)

        def go():
            with ThreadPoolExecutor(8) as pool:
                list(pool.map(one, repos))
            self.syncing = False
            GLib.idle_add(lambda: (self.say(sync_summary(reps), "ok", seconds=15), self.paint_tree(keep_scroll=True),
                                   self.current() and self.show_detail(fetch=False)) and False)
        self.say(f"syncing {len(repos)} projects…", seconds=300)
        threading.Thread(target=go, daemon=True).start()

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
        # ~/code itself, or the selected project's folder when it's one of several repos (frontend + backend)
        group = os.path.dirname(here.rel) if here else ""
        folders = pj.groups()
        self.folder = group if group in folders else ""
        clear(self.folder_box)
        self.folder_box.append(dropdown([("", "~/code")] + [(f, f) for f in folders] + [(NEW_FOLDER, NEW_FOLDER)], self.folder,
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
        rel = f"{folder}/{name}" if folder else name
        problem = ("a name" if not name else "a folder" if self.folder == NEW_FOLDER and not folder else
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
        rel = f"{folder}/{name}" if folder else name
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
        """The list's URL and Restore (filled in when gh finds a code-projects repo); a new list is the
        small button under it. GitHub only shows up when gh isn't logged in (private repos need it)."""
        self.gh_state = label("checking GitHub…", "dim", wrap=True)
        self.gh_login_btn = button("Log in to GitHub", lambda: self.terminal(["gh", "auth", "login"], "gh auth login"))
        self.gh_row = box(False, 8, self.gh_login_btn, button("Check again", self.check_gh, "flat"))
        self.list_url = Gtk.Entry(placeholder_text="https://github.com/<you>/code-projects", hexpand=True)
        self.list_url.connect("activate", lambda *_: self.restore())
        restore = box(False, 8, self.list_url, button("Restore", self.restore, "primary",
                                                      tooltip="clone the list, then every project in it"))
        restore.set_margin_top(16)
        fresh = button("Start a new list instead", self.create_list, "flat",
                       tooltip="a private GitHub repo listing what's in ~/code now")
        fresh.set_halign(Gtk.Align.START)
        fresh.set_margin_top(8)
        return box(True, 6, label("Set up Projects on this PC", "heading"),
                   label("Paste your project list's URL: every project in it is cloned back.", "sub", wrap=True),
                   self.gh_state, self.gh_row, restore, fresh)

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
            self.gh_state.set_text("" if url else f"No code-projects repo under {user} on GitHub.")
        else:
            self.gh_state.set_text("Log in to GitHub first: your list is a private repo.")
        self.gh_state.set_visible(bool(self.gh_state.get_text()))
        self.gh_row.set_visible(not user)
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
        if problem:
            self.busy = False
            self.say(problem, "bad", seconds=10)
            return False
        self.say("putting what's already in ~/code where the list says…", seconds=300)

        def work():
            # the list's folder structure: what's here moves to its listed folder, the rest to
            # unsorted/ and into the list (the scan records it and pushes)
            moves = pj.arrange(report=lambda line: None)
            pj.scan(quiet=True)
            GLib.idle_add(self.arranged, moves)
        threading.Thread(target=work, daemon=True).start()
        return False

    def arranged(self, moves):
        self.busy = False
        self.reload()
        self.clone_missing()
        if moves:
            unsorted = sum(dst.startswith(pj.UNSORTED + "/") for _, dst in moves)
            self.say(f"moved {len(moves) - unsorted} project(s) to their listed folders, "
                     f"{unsorted} not in the list to {pj.UNSORTED}/ (now listed)", seconds=12)
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
            self.reload()   # the panel thought some weren't: show them
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
        if self.dim.get_visible():   # a popup is open: Esc closes it, everything else is its own
            if keyval == Gdk.KEY_Escape:
                self.close_dialog()
                return True
            return False
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
        elif keyval == Gdk.KEY_question:
            self.ask()
        elif keyval == Gdk.KEY_Tab and page == "detail" and self.tabs_bar.get_visible():
            order = ["changes", "conflicts", "graph", "branches"]
            self.switch_tab(order[(order.index(self.tab) + 1) % 4])
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and row and row.kind in ("group", "project"):
            group = row.key if row.kind == "group" else os.path.dirname(row.key)
            if self.sort == BY_STATUS and row.kind == "project":
                group = f"{BY_STATUS}/{self.marks.get(row.key) or 'none'}"
            if group and not self.search.get_text() and self.filter == "all":
                self.fold(group, shut=keyval == Gdk.KEY_Left)
        elif Gdk.KEY_1 <= keyval <= Gdk.KEY_4:
            self.set_filter(FILTERS[keyval - Gdk.KEY_1][0])
        elif keyval == Gdk.KEY_n:
            self.show_new()
        elif keyval == Gdk.KEY_t:
            self.open_term()
        elif keyval == Gdk.KEY_a:
            self.open_agent()
        elif keyval == Gdk.KEY_s and page == "detail":
            self.sync_one()
        elif keyval == Gdk.KEY_f:
            self.fetch()
        elif keyval == Gdk.KEY_g:
            self.open_web()
        elif keyval == Gdk.KEY_c and page == "detail":
            self.ask_commit()
        elif keyval == Gdk.KEY_p and page == "detail":
            self.toggle_pin()
        elif keyval == Gdk.KEY_m and page == "detail":
            self.ask_move()
        elif keyval == Gdk.KEY_Delete and page == "detail":
            self.ask_delete()
        else:
            return False
        return True


def make():
    return Projects()


def main():
    # a workspace app (Projects, 5 by default; workspaces.conf), maximized there like the Docker panel
    run(make(), "sami.projects", (1180, 680), toggle=False)


if __name__ == "__main__":
    main()
