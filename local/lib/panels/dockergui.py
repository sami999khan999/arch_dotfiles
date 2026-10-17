#!/usr/bin/env python3
# dockergui.py — the Docker panel as a GTK window (Tokyo Night, plain). Workspace 9's app
# (config/hypr/workspaces.conf); tiled, so running it again just brings it up.
#
#   tabs   Containers · Images · Volumes · Networks (1-4)
#   left   a search box and a filter (all / running / stopped, in use / unused), then the tab's list:
#          containers grouped by compose project, each project a row of its own (its − / + button,
#          Enter or double-click collapses it), like Docker Desktop; a dot per row says its state
#   right  the selected item: details, what you can do with it, then a pane below — a container's
#          Logs / Env / Mounts / Inspect, an image's layers, a volume's or network's users — with a
#          filter of its own (and Errors, on the logs)
# The divider between the two sides can be dragged, and the one between the details and the pane below.
#
# The data and the docker commands are dockerpanel.py's (its DockerPanel is the model here:
# it polls docker in background threads); this only draws them. Full-screen things (logs in
# less, exec, compose logs, lazydocker) open in a terminal popup over the blurred backdrop (termgui.py).
import json, os, re, shlex, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dockerlogs
import dockerpanel as dp
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, clear, label, recolor, run, scrolled, wrap_box

TAB_NAMES = {"containers": "Containers", "images": "Images", "volumes": "Volumes", "networks": "Networks"}
# the list's filter, per tab: (key, label); the first is everything
FILTERS = {"containers": (("all", "All"), ("running", "Running"), ("stopped", "Stopped")),
           "images": (("all", "All"), ("used", "In use"), ("unused", "Unused")),
           "volumes": (("all", "All"), ("used", "In use"), ("unused", "Unused")),
           "networks": (("all", "All"), ("used", "In use"), ("unused", "Unused"))}
# the list's columns, per tab (the name first): (title, width), as cols() draws the rows
COLUMNS = {"containers": (("CPU", -6), ("MEM", -6), ("PORT", -7), ("", -9)),
           "images": (("SIZE", -6), ("CREATED", -14)),
           "volumes": (("SIZE", -6), ("USED BY", 18)),
           "networks": (("DRIVER", 8), ("SUBNET", 16), ("#", -3))}
PANES = (("logs", "Logs"), ("env", "Env"), ("mounts", "Mounts"), ("inspect", "Inspect"))
FILES = os.path.expanduser("~/.local/lib/panels/filesgui.py")
TERM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "termgui.py")
DOT = "●"

CSS = """
/* each project (or group) starts with space and a thin line, so the groups read as blocks */
.group-row, .project-row { margin-top: 10px; padding-top: 10px; border-top: 1px solid alpha(#3b4261, .7); }
.group-row.first, .project-row.first { margin-top: 0; border-top: none; }
.fold { min-width: 18px; min-height: 0; padding: 0 2px; border: none; color: #565f89; }
.fold:hover { color: #c0caf5; background: transparent; }
.red { color: #f7768e; }
.kv-key { color: #565f89; }
textview, textview text { background: transparent; color: #c0caf5; }
.list-tools { padding: 10px 12px 8px 12px; }
.col-head { padding: 2px 12px 6px 12px; border-bottom: 1px solid alpha(#3b4261, .7); }
/* the rows' size: the columns are counted in characters, a smaller font made them narrower */
.col-head label { color: #565f89; font-weight: 700; }
.chip { padding: 3px 10px; color: #565f89; background: transparent; }
.chip:hover { color: #a9b1d6; background: alpha(#c0caf5, .06); }
.chip.on { color: #c0caf5; background: #292e42; }
.pane-bar { padding: 0 20px 4px 20px; border-bottom: 1px solid alpha(#3b4261, .7); }
.pane-bar .tab { padding: 6px 10px; }
.pane-bar entry { min-height: 20px; padding: 2px 8px; }
paned > separator { background: alpha(#3b4261, .7); min-width: 1px; min-height: 1px; }
"""


def fit(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


def size_bytes(s):
    """'1.2GB' / '768.4MiB' → bytes (docker's sizes, to add up); 0 when it's not a size."""
    m = re.match(r"([\d.]+)\s*([kKMGT]?)i?B", (s or "").strip())
    if not m:
        return 0
    return float(m.group(1)) * 1024 ** "BKMGT".index(m.group(2).upper() or "B")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def dot_class(c):
    """A container's dot: green up, amber on its way (starting, restarting, paused), red failed, dim stopped."""
    _, _, words = dp.status(c)
    if words == "unhealthy" or words.startswith("exited ("):
        return "red"
    if words in ("starting", "restarting", "paused"):
        return "amber"
    return "green" if c["State"] == "running" else "dim"


def in_lines(segments):
    """[(text, tag)] split into lines, each a list of segments (the newline kept on its last one)."""
    lines, cur = [], []
    for text, tag in segments:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if part or not last:
                cur.append((part + ("" if last else "\n"), tag))
            if not last:
                lines.append(cur)
                cur = []
    if cur:
        lines.append(cur)
    return lines


class Docker(View):
    title, subtitle = "Docker", "containers, images, volumes, networks"
    icon = ""   # nf-linux-docker, as the terminal panel
    interval = 1.0
    css = CSS
    hints = [("1-4", "tabs"), ("/", "search"), ("f", "filter"), ("s", "start/stop"), ("r", "restart"), ("p", "pause"),
             ("l", "logs"), ("e", "shell"), ("x", "run…"), ("w", "pane"), ("y", "copy ID"), ("d", "delete"),
             ("Enter", "collapse"), ("L", "lazydocker")]

    def __init__(self):
        super().__init__()
        self.model = dp.DockerPanel()            # polls docker in its own threads
        self.model.say = self.model_say          # its messages go to our status line
        self.model.external = self.terminal      # its "full screen" programs open in a terminal
        self.ticking = False
        self.shown_rows = None                   # [(kind, id)] the list was last built from
        self.detail_for = None                   # what the right side shows: (tab, id)
        self.actions_sig = None
        self.text_key = None
        self.folded = set()                      # compose projects shown as just their row
        self.armed = None                        # (ID, time): Remove was pressed once
        self.query = ""                          # the list's search
        self.only = {t: "all" for t in dp.TABS}  # the list's filter, per tab
        self.pane = "logs"                       # a container's lower pane
        self.pane_query, self.errors_only = "", False
        self.inspected = {}                      # (ID, State) → docker inspect, for Env / Mounts / Inspect
        self.inspecting = set()

    # ---- plumbing --------------------------------------------------------------------------------
    def model_say(self, msg, color=dp.ACCENT):
        """dockerpanel's say() (act() reports with it), from any thread."""
        GLib.idle_add(lambda: self.say(msg, "bad" if color == dp.RED else "ok") and False)

    def terminal(self, argv, title="docker"):
        """A program in a terminal popup over the blurred backdrop, like every panel popup."""
        self.launch(sys.executable, TERM, title, "--", *argv)

    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def header_extra(self):
        self.prune_btn = button("", self.prune, "flat", "danger")
        self.prune_btn.set_visible(False)
        return [self.prune_btn, button("lazydocker", lambda: self.terminal(["lazydocker"], "lazydocker"), "flat",
                       tooltip="Everything else (L)")]

    # ---- layout ----------------------------------------------------------------------------------
    def build(self):
        self.tabs = {}
        bar = box(False, 0, classes=("tabs",))
        for i, t in enumerate(dp.TABS, 1):
            b = button("", lambda t=t: self.switch(t), "tab", tooltip=f"{i}")
            self.tabs[t] = b
            bar.append(b)

        self.search = Gtk.SearchEntry(placeholder_text="search  (/)", hexpand=True)
        self.search.connect("search-changed", lambda e: self.set_query(e.get_text()))
        self.search.connect("stop-search", lambda *_: self.leave_search())
        self.search.connect("activate", lambda *_: self.list.grab_focus())
        keys = Gtk.EventControllerKey()   # ↓ from the search goes into the list
        keys.connect("key-pressed", lambda _c, k, _code, _s: k == Gdk.KEY_Down and self.list.grab_focus())
        self.search.add_controller(keys)
        self.chips = box(False, 2)
        tools = box(False, 10, self.search, self.chips, classes=("list-tools",))
        self.col_head = box(False, 0, classes=("col-head",))

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(False)
        self.list.connect("row-selected", self.on_select)
        self.list.connect("row-activated", lambda _l, row: self.fold(row.eid))
        left = box(True, 0, tools, self.col_head, scrolled(self.list))
        left.set_size_request(400, -1)

        self.heading = label("", "heading", ellipsize=True)
        self.state = label("", "dim", ellipsize=True)
        self.kv = Gtk.Grid(column_spacing=16, row_spacing=4)
        self.kv.set_margin_top(12)
        self.actions = wrap_box(8, 8)   # wraps onto a second line in a narrow window
        self.actions.set_margin_top(14)
        top = box(True, 0, self.heading, self.state, self.kv, self.actions)
        for edge in ("start", "end", "top"):
            getattr(top, f"set_margin_{edge}")(20)
        top.set_margin_bottom(14)

        # the lower pane's bar: its title (or a container's Logs / Env / Mounts / Inspect), its filter
        self.lower_title = label("", "section", "flush")
        self.pane_tabs = {}
        pane_tabs = box(False, 0)
        for key, text in PANES:
            b = button(text, lambda key=key: self.set_pane(key), "tab")
            b.set_focusable(False)
            self.pane_tabs[key] = b
            pane_tabs.append(b)
        self.pane_tabs_box = pane_tabs
        self.pane_filter = Gtk.SearchEntry(placeholder_text="filter  (Ctrl+F)")
        self.pane_filter.set_size_request(220, -1)
        self.pane_filter.connect("search-changed", lambda e: self.set_pane_query(e.get_text()))
        self.pane_filter.connect("stop-search", lambda e: (e.set_text(""), self.list.grab_focus()))
        self.errors_btn = Gtk.ToggleButton(label="Errors", tooltip_text="Only errors and warnings")
        self.errors_btn.add_css_class("flat")
        self.errors_btn.set_focusable(False)
        self.errors_btn.connect("toggled", lambda b: self.set_errors(b.get_active()))
        spacer = Gtk.Box(hexpand=True)
        pane_bar = box(False, 8, self.lower_title, pane_tabs, spacer, self.errors_btn, self.pane_filter,
                       classes=("pane-bar",))
        pane_bar.set_valign(Gtk.Align.CENTER)

        self.text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=20, right_margin=20,
                                 top_margin=6, bottom_margin=10)
        buf = self.text.get_buffer()
        for name, props in {"dim": {"foreground": recolor("#565f89")}, "red": {"foreground": recolor("#f7768e")},
                            "accent": {"foreground": recolor("#6b8fe0")}, "sub": {"foreground": recolor("#a9b1d6")},
                            "green": {"foreground": recolor("#9ece6a")}, "amber": {"foreground": recolor("#e0af68")},
                            "bold": {"weight": Pango.Weight.BOLD}}.items():
            buf.create_tag(name, **props)
        self.text_scroll = scrolled(self.text)

        # the details above, the pane below, the divider between them draggable: until it's dragged the
        # details take what they need (fit_split); scrolled, so dragged up they still show, scrolling
        self.top = top
        right = self.split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=False,
                                       start_child=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=top),
                                       end_child=box(True, 0, pane_bar, self.text_scroll), resize_start_child=False,
                                       shrink_start_child=True, shrink_end_child=False)
        right.set_hexpand(True)
        self.split_dragged = False
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)   # a press on the divider
        drag.connect("drag-begin", lambda _g, _x, y: abs(y - right.get_position()) < 8
                     and setattr(self, "split_dragged", True))
        right.add_controller(drag)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=left, end_child=right,
                          resize_start_child=False, shrink_start_child=False, shrink_end_child=False,
                          wide_handle=False)
        paned.set_position(560)
        self.update()
        GLib.idle_add(lambda: self.list.grab_focus() and False)
        return box(True, 0, bar, paned)

    def leave_text(self):
        self.list.grab_focus()

    # ---- refresh ---------------------------------------------------------------------------------
    def refresh(self):
        """docker ps runs in a thread (it can take a while when docker is busy); the stats,
        resources and logs threads are dockerpanel's own."""
        if self.ticking:
            return
        if not self.model.due():   # docker ps only after a docker event, or every 10 s: else just
            self.update()          # draw what the stats / logs threads brought (no thread a second)
            return
        self.ticking = True

        def work():
            self.model.tick()
            GLib.idle_add(self.after_tick)
        threading.Thread(target=work, daemon=True).start()

    def shown_changed(self, shown):
        """Workspace 9 hidden: the model's watchers wait (stats, system df, logs); shown: all at once."""
        m = self.model
        if shown:
            m.changed = True
            m.awake.set()
            m.refresh.set()
            self.refresh()
        else:
            m.awake.clear()

    def after_tick(self):
        self.ticking = False
        self.update()
        return False

    def update(self):
        m = self.model
        for t, b in self.tabs.items():
            b.set_label(f"{TAB_NAMES[t]} {len(m.entries(t))}")
            (b.add_css_class if t == m.tab else b.remove_css_class)("on")
        self.paint_prune()
        self.paint_summary()
        self.paint_tools()
        self.paint_list()
        self.paint_detail()

    def paint_summary(self):
        """The header's line: what's running, and what the images and volumes take on disk."""
        m = self.model
        if m.error:
            self.set_subtitle("docker isn't answering")
            return
        up = sum(c["State"] == "running" for c in m.items)
        bits = [f"{up} running · {len(m.items) - up} stopped"]
        if m.images:
            bits.append(f"images {human(sum(size_bytes(i['size']) for i in m.images))}")
        if m.volumes and any(v["size"] for v in m.volumes):
            bits.append(f"volumes {human(sum(size_bytes(v['size']) for v in m.volumes))}")
        self.set_subtitle("   ·   ".join(bits))

    def paint_prune(self):
        """The header's red button: Delete stopped (containers) or Delete dangling (images), twice."""
        m = self.model
        what = self.prunable()
        key = f"prune-{m.tab}"
        sure = self.armed and self.armed[0] == key and time.time() - self.armed[1] < 3
        noun = "stopped" if m.tab == "containers" else "dangling"
        self.prune_btn.set_label(f"Click again: delete {len(what)}" if sure else f"Delete {noun} ({len(what)})")
        self.prune_btn.set_tooltip_text(
            "Delete every stopped container, twice to confirm (D); their volumes and images stay"
            if m.tab == "containers" else "Delete every untagged image no container uses, twice to confirm (D)")
        self.prune_btn.set_visible(bool(what))

    def paint_tools(self):
        """The filter chips and the column titles, for this tab (made again only when it changes)."""
        m = self.model
        sig = (m.tab, self.only[m.tab])
        if getattr(self, "tools_sig", None) == sig:
            return
        self.tools_sig = sig
        clear(self.chips)
        for key, text in FILTERS[m.tab]:
            b = button(text, lambda key=key: self.set_only(key), "chip", tooltip="f cycles")
            b.set_focusable(False)
            if key == self.only[m.tab]:
                b.add_css_class("on")
            self.chips.append(b)
        clear(self.col_head)
        self.col_head.append(self.cols("NAME", *[(t, w, ()) for t, w in COLUMNS[m.tab]], dot=" "))

    # ---- the list --------------------------------------------------------------------------------
    def passes(self, e, words=None):
        """The tab's filter, then the search: every word somewhere in the entry's name, image, project,
        ports (containers), ref (images), subnet and members (networks)."""
        m, only = self.model, self.only[self.model.tab]
        if m.tab == "containers":
            on = e["State"] == "running"
            hay = (e["short"], e["Names"], e["Image"], e["project"], e["Ports"], e["Status"])
        elif m.tab == "images":
            on, hay = bool(e["used"]), (e["ref"], e["ID"])
        elif m.tab == "volumes":
            on, hay = bool(e["used"]), (e["short"], e["ID"], e["project"])
        else:
            on, hay = bool(e["members"]), (e["name"], e["subnet"], e["project"], *(n for n, _ in e["members"]))
        if only != "all" and on != (only in ("running", "used")):
            return False
        words = self.query.casefold().split() if words is None else words
        text = " ".join(hay).casefold()
        return all(w in text for w in words)

    def layout(self):
        """[(kind, id, payload)] in on-screen order: kind is header, project or entry. Searching or
        filtering shows a collapsed project's matches too."""
        m, out = self.model, []
        words = self.query.casefold().split()
        narrowed = bool(words) or self.only[m.tab] != "all"
        for title, note, group in m.groups():
            project = m.tab == "containers" and group[0]["project"]
            named = project and words and all(w in project.casefold() for w in words)
            shown = [e for e in group if self.passes(e, [] if named else words)]
            if not shown:
                continue
            if project:
                out.append(("project", dp.PROJECT + project, project))
                if project in self.folded and not narrowed:
                    continue
            else:
                out.append(("header", None, (title, note)))
            out += [("entry", e["ID"], e) for e in shown]
        return out

    def paint_list(self):
        m, rows = self.model, self.layout()
        shape = [(k, i) for k, i, _ in rows]
        if shape != self.shown_rows:          # new / removed / folded rows: build the list again
            self.shown_rows = shape
            self.building = True
            clear(self.list)
            for kind, eid, _ in rows:
                row = Gtk.ListBoxRow(selectable=kind != "header", activatable=kind == "project")
                row.eid = eid
                if kind in ("header", "project"):
                    row.add_css_class("group-row" if kind == "header" else "project-row")
                    if not self.list.get_first_child():
                        row.add_css_class("first")
                self.list.append(row)
            self.building = False
        if not rows:
            clear(self.list)
            self.shown_rows = None
            text = m.error or (f"nothing matches “{self.query}”" if self.query else
                               "nothing here" if self.only[m.tab] == "all" else "none like that")
            self.list.append(Gtk.ListBoxRow(child=label(text, "red" if m.error else "dim"), selectable=False))
            return
        ids = [i for _, i, _ in rows if i]
        if m.sel[m.tab] not in ids:   # the selection was filtered out: the first that shows
            m.sel[m.tab] = ids[0]
        sel_row = None
        row = self.list.get_first_child()
        for kind, eid, payload in rows:
            sig = self.row_sig(kind, eid, payload)
            if getattr(row, "sig", None) != sig:   # a row's widgets are made again only when it changed
                row.sig = sig
                row.set_child(self.row_content(kind, eid, payload))
            if eid and eid == m.sel[m.tab]:
                sel_row = row
            row = row.get_next_sibling()
        if sel_row is not None and self.list.get_selected_row() is not sel_row:
            self.building = True
            self.list.select_row(sel_row)
            self.building = False

    def cols(self, name, *tail, dim=False, name_cls=(), dot=None):
        """A list line: a dot (glyph, colour class), the name, then fixed-width columns (the font is
        monospace). dot=" " keeps its room (the column titles)."""
        n = label(name, *name_cls, ellipsize=True)
        if dim:
            n.add_css_class("dim")
        line = box(False, 14, n)
        if dot is not None:
            glyph, cls = (dot, ()) if isinstance(dot, str) else dot
            d = label(glyph, *cls)
            d.set_width_chars(1)
            line.prepend(d)
            line.set_spacing(10)
        for text, width, cls in tail:
            lab = label(text, *cls, xalign=1.0 if width < 0 else 0.0)
            lab.set_width_chars(abs(width))
            lab.set_max_width_chars(abs(width))
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            line.append(lab)
        return line

    def row_sig(self, kind, eid, p):
        """What a row shows, to compare: its payload and the model's live parts it draws (busy, the
        stats as shown, the project's states, folded)."""
        m = self.model
        if kind == "project":
            return (p, m.busy.get(eid), p in self.folded, tuple(c["State"] for c in m.items if c["project"] == p))
        if kind == "entry" and m.tab == "containers":
            st = m.stats.get(eid)
            shown = (f"{dp.percent(st['CPUPerc']):.1f}", dp.short_size(st["MemUsage"])) if st else None
            return (repr(p), m.busy.get(eid), shown)
        return (repr(p), m.busy.get(eid))

    def row_content(self, kind, eid, p):
        m = self.model
        if kind == "header":
            title, note = p
            return self.cols(title, (note, -14, ("dim",)), name_cls=("dim",))
        if kind == "project":
            group = [c for c in m.items if c["project"] == p]
            up = sum(c["State"] == "running" for c in group)
            if eid in m.busy:
                note, cls = m.busy[eid], ("accent",)
            else:
                note = f"running {up}/{len(group)}" if up else f"exited 0/{len(group)}"
                cls = ("green",) if up == len(group) else ("yellow",) if up else ("dim",)
            folded = p in self.folded
            if folded:
                note += " · collapsed"
            line = self.cols(p, (note, -30, cls), name_cls=("bold",))
            toggle = button("+" if folded else "−", lambda: self.fold(eid), "flat", "fold",
                            tooltip="Expand (Enter)" if folded else "Collapse (Enter)")
            line.prepend(toggle)
            line.set_spacing(8)
            return line
        e = p
        if m.tab == "containers":
            return self.container_line(e)
        busy = (m.busy[e["ID"]], -30, ("accent",)) if e["ID"] in m.busy else None
        if m.tab == "images":
            name = e["ref"] if not e["dangling"] else f"<none> {e['ID']}"
            dot = (DOT, ("green",) if e["used"] else ("amber",) if e["dangling"] else ("dim",))
            return self.cols(name, *([busy] if busy else [(dp.short_size(e["size"]), -6, ()),
                                                          (e["created"], -14, ("dim",))]), dim=not e["used"], dot=dot)
        if m.tab == "volumes":
            users = ", ".join(m.short(n, e["project"]) for n in e["used"]) or "unused"
            return self.cols(e["short"], *([busy] if busy else [(dp.short_size(e["size"]), -6, ()),
                                                                (users, 18, ("dim",))]), dim=not e["used"],
                             dot=(DOT, ("green",) if e["used"] else ("dim",)))
        return self.cols(e["name"], *([busy] if busy else [(e["driver"], 8, ("dim",)), (e["subnet"], 16, ()),
                                                           (str(len(e["members"])), -3, ("dim",))]),
                         dim=not e["members"], dot=(DOT, ("green",) if e["members"] else ("dim",)))

    def container_line(self, c):
        m = self.model
        _, _, words = dp.status(c)
        running = c["State"] == "running"
        bad = words == "unhealthy" or words.startswith("exited (")
        dot = (DOT, (dot_class(c),))
        if c["ID"] in m.busy:
            return self.cols(c["short"], (m.busy[c["ID"]], -32, ("accent",)), dim=not running, dot=dot)
        if not running:
            when = re.sub(r"^Exited \(\d+\) ", "", c["Status"])
            return self.cols(c["short"], (f"{words} · {when}", -32, ("red",) if bad else ("dim",)), dim=True, dot=dot)
        s = m.stats.get(c["ID"])
        cpu = f"{dp.percent(s['CPUPerc']):.1f}%" if s else "…"
        mem = dp.short_size(s["MemUsage"]) if s else "…"
        pp = dp.ports(c["Ports"])
        port = (pp[0][0] + ("+" if len(pp) > 1 else "")) if pp else ""
        return self.cols(c["short"], (cpu, -6, ()), (mem, -6, ()), (port, -7, ("dim",)),
                         ("unhealthy" if bad else words if words != "running" else "", -9,
                          ("red",) if bad else ("dim",)), dot=dot)

    def on_select(self, _list, row):
        if getattr(self, "building", False) or row is None or not row.eid:
            return
        self.model.sel[self.model.tab] = row.eid
        self.armed = None
        self.paint_detail()

    def switch(self, tab):
        if tab != self.model.tab:
            self.model.tab = tab
            self.shown_rows = None
            self.model.refresh.set()   # Volumes: their sizes now, not in up to 30 s
            self.update()

    def set_query(self, text):
        self.query = text.strip()
        self.update()

    def leave_search(self):
        self.search.set_text("")
        self.list.grab_focus()

    def set_only(self, key):
        self.only[self.model.tab] = key
        self.update()

    def cycle_only(self):
        keys = [k for k, _ in FILTERS[self.model.tab]]
        self.set_only(keys[(keys.index(self.only[self.model.tab]) + 1) % len(keys)])
        self.say(f"showing {dict(FILTERS[self.model.tab])[self.only[self.model.tab]].lower()}")

    def fold(self, eid):
        if eid and dp.is_project({"ID": eid}):
            name = eid[len(dp.PROJECT):]
            self.folded ^= {name}
            self.update()

    def move(self, step):
        ids = [i for k, i, _ in self.layout() if i]
        if ids:
            cur = self.model.sel[self.model.tab]
            i = ids.index(cur) if cur in ids else 0
            self.model.sel[self.model.tab] = ids[max(0, min(len(ids) - 1, i + step))]
            self.update()

    # ---- the details -----------------------------------------------------------------------------
    def paint_detail(self):
        m = self.model
        e = m.current()
        key = (m.tab, e["ID"] if e else None)
        if key != self.detail_for:
            self.detail_for, self.actions_sig, self.text_key = key, None, None
        container = bool(e) and m.tab == "containers" and not dp.is_project(e)
        self.pane_tabs_box.set_visible(container)
        self.lower_title.set_visible(not container)
        self.errors_btn.set_visible(container and self.pane == "logs")
        for k, b in self.pane_tabs.items():
            (b.add_css_class if k == self.pane else b.remove_css_class)("on")
        if not e:
            self.kv_rows([])
            self.heading.set_text("Docker isn't answering" if m.error else "Nothing selected")
            self.state.set_text(m.error)
            self.set_state_class("dim")
            self.set_actions("none", lambda: [])
            self.lower_title.set_text("")
            self.set_text([])
            self.fit_split()
            return
        if dp.is_project(e):
            self.project_detail(e)
        else:
            {"containers": self.container_detail, "images": self.image_detail,
             "volumes": self.volume_detail, "networks": self.network_detail}[m.tab](e)
        self.fit_split()

    def fit_split(self):
        """The divider under the details, as tall as they are, until it's dragged: then it stays put."""
        if self.split_dragged:
            return
        width = self.top.get_width() or 600
        need = self.top.measure(Gtk.Orientation.VERTICAL, width)[1]
        if need != self.split.get_position():
            self.split.set_position(need)

    def set_state_class(self, cls):
        for c in ("dim", "green", "amber", "red", "accent"):
            (self.state.add_css_class if c == cls else self.state.remove_css_class)(c)

    def kv_rows(self, pairs):
        """The key / value grid: built again only when its keys change; every second it's just the
        values' text."""
        keys = [k for k, _ in pairs]
        if keys != getattr(self, "kv_keys", None):
            self.kv_keys = keys
            clear(self.kv)
            self.kv_vals = []
            for i, k in enumerate(keys):
                self.kv.attach(label(k, "kv-key"), 0, i, 1, 1)
                self.kv_vals.append(label("", ellipsize=True, wrap=False))
                self.kv_vals[-1].set_selectable(True)   # an ID, an IP, a path: copied from here
                self.kv_vals[-1].set_can_focus(False)
                self.kv.attach(self.kv_vals[-1], 1, i, 1, 1)
        for lab, (_, v) in zip(self.kv_vals, pairs):
            if lab.get_text() != v:
                lab.set_text(v)

    def set_actions(self, sig, make):
        """The buttons, from make(): built only when what you can do changes, so an open Exec popover
        survives refreshes. Built every refresh and thrown away, the Exec popovers piled up (about
        7 MB a minute, 1 GB after a couple of hours) and each one ran a docker exec to find the shell."""
        if sig == self.actions_sig:
            return
        self.actions_sig = sig
        clear(self.actions)
        for b in make():
            b.set_focusable(False)   # Enter belongs to the list
            self.actions.append(b)

    def set_text(self, segments, key=None, follow=False):
        """The lower pane: [(text, tag or None)], only the lines with the pane's filter in them. Only
        rewritten when its content changed; follow: kept at the end while it was there (logs)."""
        q = self.pane_query.casefold()
        key = (key if key is not None else "".join(t for t, _ in segments), q)
        if key == self.text_key:
            return
        self.text_key = key
        if q:
            lines = [ln for ln in in_lines(segments) if q in "".join(t for t, _ in ln).casefold()]
            segments = [s for ln in lines for s in ln] or [(f"no line has “{self.pane_query}”", "dim")]
        adj = self.text_scroll.get_vadjustment()
        at_end = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40
        buf = self.text.get_buffer()
        buf.set_text("")
        for text, tag in segments:
            if tag:
                buf.insert_with_tags_by_name(buf.get_end_iter(), text, tag)
            else:
                buf.insert(buf.get_end_iter(), text)
        if follow and at_end:
            GLib.idle_add(lambda: self.text.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 1) and False)

    def act_button(self, text, fn, *cls, tooltip=None, busy=False):
        b = button(text, fn, *cls, tooltip=tooltip)
        b.set_sensitive(not busy)
        return b

    def container_detail(self, c):
        m = self.model
        _, _, words = dp.status(c)
        running = c["State"] == "running"
        paused = c["State"] == "paused"
        busy = c["ID"] in m.busy
        self.heading.set_text(c["Names"])
        self.state.set_text(m.busy[c["ID"]] if busy else words)
        self.set_state_class("accent" if busy else dot_class(c))
        pairs = [("Image", c["Image"]), ("ID", c["ID"][:12]),
                 ("Up", re.sub(r"^Up |\s*\(.*\)$", "", c["Status"])) if running else ("Status", c["Status"])]
        pp = dp.ports(c["Ports"])
        if pp:
            pairs.append(("Ports", "  ".join(f"{h}→{p}" + ("" if t == "tcp" else f"/{t}") for h, p, t in pp)))
        s = m.stats.get(c["ID"]) if running else None
        if s:
            pairs += [("CPU", f"{dp.percent(s['CPUPerc']):.1f}%"),
                      ("Memory", s["MemUsage"].replace(" / ", " of ")),
                      ("Net I/O", s["NetIO"].replace(" / ", " in · ") + " out")]
        info = self.inspect_of(c)
        if info:
            nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
            ips = "  ".join(f"{n} {v.get('IPAddress')}" for n, v in nets.items() if v.get("IPAddress"))
            if ips:
                pairs.append(("Networks", ips))
            policy = ((info.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no"
            pairs.append(("Restart", f"{policy} · restarted {info.get('RestartCount', 0)}×"))
        if c["workdir"]:
            pairs.append(("Project", c["workdir"].replace(dp.HOME, "~")))
        self.kv_rows(pairs)

        def make():
            buttons = [self.act_button("Stop" if running or paused else "Start", lambda: self.container_action("s", c),
                                       "primary", tooltip="s", busy=busy),
                       self.act_button("Restart", lambda: self.container_action("r", c), tooltip="r", busy=busy)]
            if running or paused:
                buttons.append(self.act_button("Resume" if paused else "Pause", lambda: self.container_action("p", c),
                                               tooltip="Freeze its processes, or let them go on (p)", busy=busy))
            buttons.append(button("Logs", lambda: self.full_logs(c), tooltip="Full screen, following (l)"))
            if running:
                buttons += [button("Shell", lambda: self.model.run_shell(c), tooltip="Its shell, in a terminal (e)"),
                            self.exec_button(c)]
            if pp:
                buttons.append(button("Open port", lambda: self.container_action("o", c), tooltip="o"))
            buttons += [button("Copy ID", lambda: self.copy(c["ID"], "ID"), "flat", tooltip="y"),
                        self.remove_button(c, busy)]
            return buttons
        self.set_actions((c["ID"], c["State"], busy, bool(pp), self.armed_on(c)), make)
        self.container_pane(c, info)

    def container_pane(self, c, info):
        """A container's lower pane: its logs, or what docker inspect says (Env / Mounts / all of it)."""
        m = self.model
        if self.pane == "logs":
            if m.logs_for != c["ID"]:
                self.set_text([("loading…", "dim")], key=("loading", c["ID"]))
            elif not m.logs:
                self.set_text([("no output yet", "dim")], key=("empty", c["ID"]))
            else:
                self.set_text(self.log_segments(m.logs), key=(c["ID"], id(m.logs), self.errors_only), follow=True)
            return
        if info is None:
            self.set_text([("loading…", "dim")], key=("inspecting", c["ID"]))
            return
        seg = []
        if self.pane == "env":
            for var in (info.get("Config") or {}).get("Env") or []:
                k, _, v = var.partition("=")
                seg += [(k, "accent"), ("=", "dim"), (v + "\n", None)]
        elif self.pane == "mounts":
            for mnt in info.get("Mounts") or []:
                src = mnt.get("Name") or mnt.get("Source", "")
                seg += [(f"{mnt.get('Type', ''):<7}", "dim"), (src.replace(dp.HOME, "~"), None), ("  →  ", "dim"),
                        (mnt.get("Destination", ""), "accent"), ("" if mnt.get("RW", True) else "  read-only", "amber"),
                        ("\n", None)]
        else:
            seg = [(json.dumps(info, indent=2) + "\n", None)]
        self.set_text(seg or [("none", "dim")], key=(self.pane, c["ID"], id(info)))

    def inspect_of(self, c):
        """docker inspect for the container, cached while its state stays (fetched in a thread)."""
        key = (c["ID"], c["State"])
        if key in self.inspected:
            return self.inspected[key]
        if key not in self.inspecting:
            self.inspecting.add(key)

            def work():
                try:
                    info = json.loads(dp.docker("inspect", c["ID"]).stdout or "[{}]")[0]
                except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                    info = {}

                def done():
                    self.inspecting.discard(key)
                    for k in [k for k in self.inspected if k[0] == c["ID"]]:
                        del self.inspected[k]   # its older state's
                    if len(self.inspected) > 30:
                        self.inspected.pop(next(iter(self.inspected)))
                    self.inspected[key] = info
                    self.paint_detail()
                    return False
                GLib.idle_add(done)
            threading.Thread(target=work, daemon=True).start()
        return None

    def set_pane(self, key):
        self.pane = key
        self.text_key = None
        self.paint_detail()

    def cycle_pane(self):
        keys = [k for k, _ in PANES]
        self.set_pane(keys[(keys.index(self.pane) + 1) % len(keys)])

    def set_pane_query(self, text):
        self.pane_query = text.strip()
        self.paint_detail()

    def set_errors(self, on):
        self.errors_only = on
        self.paint_detail()

    def log_segments(self, records):
        out = []
        for r in records[-300:]:
            if self.errors_only and r.level not in ("error", "warn"):
                continue
            out.append((f"{r.time or '':<8}  ", "dim"))
            badge = {"error": "ERR", "warn": "WRN", "info": "INF", "debug": "DBG", "trace": "TRC"}.get(r.level, "   ")
            out.append((f"{badge}  ", "red" if r.level == "error" else "amber" if r.level == "warn" else "dim"))
            out.append((r.msg, "red" if r.level == "error" else None))
            for k, v in r.fields:
                out.append((f"  {k}=", "dim"))
                out.append((v if " " not in v else f'"{v}"', "red" if k in ("err", "error", "exception") else "sub"))
            if r.count > 1:
                out.append((f"  ×{r.count}", "dim"))
            out.append(("\n", None))
        return out or [("no errors or warnings", "dim")]

    def project_detail(self, p):
        m = self.model
        group = p["containers"]
        up = [c for c in group if c["State"] == "running"]
        busy = p["ID"] in m.busy
        workdir = group[0]["workdir"]
        self.heading.set_text(p["project"])
        self.state.set_text(m.busy[p["ID"]] if busy else
                            f"running {len(up)}/{len(group)}" if up else "exited")
        self.set_state_class("accent" if busy else "green" if len(up) == len(group) else "amber" if up else "dim")
        pairs = [("Folder", workdir.replace(dp.HOME, "~") or "—"),
                 ("Containers", f"{len(up)} running · {len(group) - len(up)} stopped")]
        stats = [m.stats[c["ID"]] for c in up if c["ID"] in m.stats]
        if stats:
            pairs += [("CPU", f"{sum(dp.percent(s['CPUPerc']) for s in stats):.1f}%"),
                      ("Memory", f"{sum(dp.percent(s['MemPerc']) for s in stats):.1f}% of RAM")]
        pp = [f"{h} {c['short']}" for c in up for h, _, _ in dp.ports(c["Ports"])]
        if pp:
            pairs.append(("Ports", "  ".join(pp)))
        self.kv_rows(pairs)
        folded = p["project"] in self.folded
        has_files = bool(self.compose_files(p))
        here = bool(workdir) and os.path.isdir(workdir)
        self.set_actions((p["ID"], bool(up), busy, folded, self.armed_on(p), has_files, here), lambda: [
            self.act_button("Stop all" if up else "Start all", lambda: self.project_action("s", p), "primary",
                            tooltip="s", busy=busy),
            self.act_button("Restart all", lambda: self.project_action("r", p), tooltip="r", busy=busy),
            self.act_button("Pull & up", lambda: self.project_action("U", p), busy=busy or not has_files,
                            tooltip="Pull newer images, then recreate what changed (U)"),
            self.act_button("Rebuild", lambda: self.project_action("B", p), busy=busy or not has_files,
                            tooltip="Build its images again, then recreate (B)"),
            button("Logs", lambda: self.project_logs(p["project"]), tooltip="Every container's logs, following (l)"),
            self.act_button("Folder", lambda: self.launch("python3", FILES, workdir), "flat", busy=not here,
                            tooltip="Open its folder in Files (F)"),
            self.act_button("VS Code", lambda: self.launch("code", workdir), "flat", busy=not here,
                            tooltip="Open its folder in VS Code (C)"),
            button("Expand" if folded else "Collapse", lambda: self.fold(p["ID"]), "flat", tooltip="Enter"),
            self.remove_button(p, busy, "Delete all", "Stop and delete its containers and network, twice to "
                               "confirm (d); volumes stay (start it again from the project: docker compose up)")])
        self.lower_title.set_text("Containers")
        seg = []
        for c in group:
            _, _, words = dp.status(c)
            s = m.stats.get(c["ID"]) if c["State"] == "running" else None
            pp = dp.ports(c["Ports"])
            seg.append((f"{DOT} ", {"red": "red", "amber": "amber", "green": "green"}.get(dot_class(c), "dim")))
            seg.append((f"{fit(c['short'], 22):<24}", None if c["State"] == "running" else "dim"))
            seg.append((f"{fit(m.busy.get(c['ID'], words), 14):<16}",
                        "accent" if c["ID"] in m.busy else "dim"))
            if s:
                seg.append((f"{dp.percent(s['CPUPerc']):>5.1f}%  {dp.short_size(s['MemUsage']):>6}  ", "sub"))
            if pp:
                seg.append((" ".join(h for h, _, _ in pp), "accent"))
            seg.append(("\n", None))
        self.set_text(seg)

    def compose_files(self, p):
        """The project's compose files that are still there (compose up needs them), or []."""
        files = p["containers"][0].get("configs") or []
        return [f for f in files if os.path.isfile(f)]

    def image_detail(self, i):
        m = self.model
        busy = i["ID"] in m.busy
        self.heading.set_text(i["ref"] if not i["dangling"] else f"<none> {i['ID']}")
        self.state.set_text(m.busy[i["ID"]] if busy else "in use" if i["used"] else
                            "dangling" if i["dangling"] else "unused")
        self.set_state_class("accent" if busy else "green" if i["used"] else "amber" if i["dangling"] else "dim")
        self.kv_rows([("ID", i["ID"]), ("Size", i["size"]), ("Created", i["created"]),
                      ("Used by", ", ".join(i["used"]) or "no containers")])
        self.set_actions((i["ID"], busy, self.armed_on(i)), lambda: [
            self.act_button("Pull", lambda: self.key_action("u"), "primary", tooltip="Pull a newer version (u)",
                            busy=busy or i["dangling"]),
            self.act_button("Run", lambda: self.run_image(i), busy=busy,
                            tooltip="Run it once in a terminal, removed when it ends (n)"),
            button("Copy ID", lambda: self.copy(i["ref"] if not i["dangling"] else i["ID"], "name"), "flat",
                   tooltip="y"),
            self.remove_button(i, busy)])
        self.lower_title.set_text("Layers")
        layers = m.history.get(i["ID"])
        if layers is None:
            self.set_text([("loading…", "dim")], key=("loading", i["ID"]))
            return
        seg = []
        for size, cmd in layers:
            seg.append((f"{dp.short_size(size) if size != '0B' else '·':>6}  ", None))
            seg.append((cmd + "\n", "dim"))
        self.set_text(seg)

    def volume_detail(self, v):
        m = self.model
        busy = v["ID"] in m.busy
        self.heading.set_text(v["short"])
        self.state.set_text(m.busy[v["ID"]] if busy else "in use" if v["used"] else "unused")
        self.set_state_class("accent" if busy else "green" if v["used"] else "dim")
        self.kv_rows([("Name", v["ID"]), ("Size", v["size"] or "…"), ("Driver", v["driver"]),
                      ("Project", v["project"] or "—"), ("Mountpoint", v["mount"])])
        self.set_actions((v["ID"], busy, self.armed_on(v)), lambda: [
            button("Copy name", lambda: self.copy(v["ID"], "name"), "flat", tooltip="y"), self.remove_button(v, busy)])
        self.lower_title.set_text(f"Used by ({len(v['used'])})")
        self.set_text([(n + "\n", None) for n in v["used"]] or [("no containers", "dim")])

    def network_detail(self, n):
        m = self.model
        busy = n["ID"] in m.busy
        self.heading.set_text(n["name"])
        self.state.set_text(m.busy[n["ID"]] if busy else f"{n['driver']} · {n['scope']}")
        self.set_state_class("accent" if busy else "green" if n["members"] else "dim")
        self.kv_rows([("ID", n["ID"]), ("Subnet", n["subnet"] or "—"), ("Gateway", n["gateway"] or "—"),
                      ("Internal", "yes" if n["internal"] else "no"), ("Project", n["project"] or "—"),
                      ("Created", n["created"])])
        builtin = n["name"] in dp.BUILTIN_NETWORKS
        self.set_actions((n["ID"], busy, self.armed_on(n)), lambda: [
            button("Copy ID", lambda: self.copy(n["ID"], "ID"), "flat", tooltip="y"),
            *([] if builtin else [self.remove_button(n, busy)])])
        self.lower_title.set_text(f"Containers ({len(n['members'])})")
        seg = []
        for name, ip in n["members"]:
            seg += [(f"{name:<36}", None), (ip + "\n", "dim")]
        self.set_text(seg or [("none attached", "dim")])

    def remove_button(self, e, busy, text="Delete", tooltip="Press twice to delete (d)"):
        """Delete (an image, volume, network, container; a project's Delete all): the first press arms
        it, red, for 3 s; the second deletes."""
        sure = self.armed_on(e)
        return self.act_button("Click again to delete" if sure else text, lambda: self.remove(e),
                               "danger" if sure else "flat", tooltip=tooltip, busy=busy)

    def armed_on(self, e):
        return bool(self.armed and self.armed[0] == e["ID"] and time.time() - self.armed[1] < 3)

    def disarm(self, key):
        """The armed Delete goes back to normal after its 3 s."""
        if self.armed and self.armed[0] == key:
            self.armed = None
            self.update()
        return False

    def arm(self, key, msg):
        self.armed = (key, time.time())
        self.say(msg)
        GLib.timeout_add(3100, self.disarm, key)
        self.update()

    def prunable(self):
        """What the header's Delete button would sweep: stopped containers, or dangling images no
        container uses."""
        m = self.model
        if m.tab == "containers":
            return [c for c in m.items if c["State"] in ("exited", "created", "dead")]
        if m.tab == "images":
            return [i for i in m.images if i["dangling"] and not i["used"]]
        return []

    # ---- actions ---------------------------------------------------------------------------------
    def copy(self, text, what):
        self.window.get_clipboard().set(text)
        self.say(f"{what} copied: {text}")

    def container_action(self, k, c):
        m = self.model
        if k == "s":
            if c["State"] in ("running", "paused"):
                m.act([c["ID"]], "stopping…", ["stop", c["ID"]], f"stopped {c['short']}")
            else:
                m.act([c["ID"]], "starting…", ["start", c["ID"]], f"started {c['short']}")
        elif k == "r":
            m.act([c["ID"]], "restarting…", ["restart", c["ID"]], f"restarted {c['short']}")
        elif k == "p":
            if c["State"] == "paused":
                m.act([c["ID"]], "resuming…", ["unpause", c["ID"]], f"resumed {c['short']}")
            elif c["State"] == "running":
                m.act([c["ID"]], "pausing…", ["pause", c["ID"]], f"paused {c['short']}")
            else:
                self.say("only a running container can be paused")
        elif k == "o":
            pp = dp.ports(c["Ports"])
            if not pp:
                self.say("no published ports")
                return
            url = f"http://localhost:{pp[0][0].split('-')[0]}"
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"opened {url}")
        self.update()

    def project_action(self, k, p):
        """Like Docker Desktop's project row: stop if anything runs, else start everything.
        Only the rows that will change say so; the project row says what's happening. U / B: compose up
        from its files, pulling newer images or building them again."""
        name, group = p["project"], p["containers"]
        running = [c for c in group if c["State"] == "running"]
        if k in ("U", "B"):
            files = self.compose_files(p)
            if not files:
                self.say(f"{name}'s compose file isn't there any more", "bad")
                return
            cmd = ["compose", "-p", name, "--project-directory", group[0]["workdir"] or os.path.dirname(files[0]),
                   *[a for f in files for a in ("-f", f)], "up", "-d"]
            doing, done = ("pulling…", "pulled and up") if k == "U" else ("building…", "rebuilt and up")
            cmd += ["--pull", "always"] if k == "U" else ["--build"]
            self.model.act({c["ID"]: doing for c in group} | {p["ID"]: doing}, doing, cmd, f"{name}: {done}")
            self.update()
            return
        if k == "s":
            doing, verb, done, rows = ("stopping…", "stop", "stopped", running) if running else \
                ("starting…", "start", "started", group)
        else:
            doing, verb, done, rows = "restarting…", "restart", "restarted", group
        labels = {c["ID"]: doing for c in rows} | {p["ID"]: doing}
        self.model.act(labels, doing, ["compose", "-p", name, verb], f"{name}: {done}")
        self.update()

    def run_image(self, i):
        """The image's own command, once, in a terminal (docker run --rm -it): gone when it ends."""
        ref = i["ref"] if not i["dangling"] else i["ID"]
        script = (f'printf "\\033[2J\\033[H\\033[38;2;107;143;224mrun\\033[0m %s \\033[2m%s\\033[0m\\n\\n" '
                  f'{shlex.quote(ref)} {shlex.quote("removed when it ends · Ctrl+C stops it")}; '
                  f'docker run --rm -it {shlex.quote(ref)}; s=$?; '
                  f'printf "\\n\\033[2m── exit %s · Enter to close ──\\033[0m" $s; read _')
        self.terminal(["sh", "-c", script], f"run {ref}")

    def remove(self, e):
        """Twice (the button or d) within 3 s: delete the container (stopped first if it runs), the
        project's containers (compose down: volumes stay), the image, volume or network."""
        m = self.model
        project = dp.is_project(e)
        name = e["project"] if project else {"containers": e.get("short"), "images": e.get("ref"),
                                             "volumes": e.get("short"), "networks": e.get("name")}.get(m.tab)
        if not name:
            return
        if m.tab == "networks" and e["name"] in dp.BUILTIN_NETWORKS:
            self.say(f"{name} is built into docker and can't be removed")
            return
        if not self.armed_on(e):
            what = f"all of {name}'s containers (volumes stay)" if project else name
            return self.arm(e["ID"], f"press again (or d) to delete {what}")
        self.armed = None
        if project:
            labels = {c["ID"]: "deleting…" for c in e["containers"]} | {e["ID"]: "deleting…"}
            m.act(labels, "deleting…", ["compose", "-p", name, "down", "--remove-orphans"], f"{name}: deleted")
        else:
            cmd = {"containers": ["rm", "-f", e["ID"]], "images": ["rmi", e["ID"]],
                   "volumes": ["volume", "rm", e["ID"]], "networks": ["network", "rm", e["ID"]]}[m.tab]
            m.act([e["ID"]], "deleting…", cmd, f"deleted {name}")
        self.update()

    def prune(self):
        """The header's Delete (D), twice: every stopped container, or every dangling image no container
        uses, at once, each named (not docker … prune: one that changes meanwhile isn't swept up unseen)."""
        m = self.model
        what, key = self.prunable(), f"prune-{m.tab}"
        if not what:
            return
        noun = "stopped containers (volumes stay)" if m.tab == "containers" else "dangling images"
        if not (self.armed and self.armed[0] == key and time.time() - self.armed[1] < 3):
            return self.arm(key, f"press again (or D) to delete {len(what)} {noun}")
        self.armed = None
        ids = [e["ID"] for e in what]
        cmd = ["rm", *ids] if m.tab == "containers" else ["rmi", *ids]
        m.act(dict.fromkeys(ids, "deleting…"), "deleting…", cmd,
              f"deleted {len(ids)} {'stopped container' if m.tab == 'containers' else 'dangling image'}"
              f"{'s' * (len(ids) != 1)}")
        self.update()

    # ---- exec ------------------------------------------------------------------------------------
    def exec_button(self, c):
        """Run…: a command in it, a small form in a popover (Shell is the button next to it)."""
        m = self.model
        entry = Gtk.Entry(placeholder_text="a command, e.g. env", hexpand=True)
        root = Gtk.CheckButton(label="as root")
        pop = Gtk.Popover()

        def go(*_):
            if not entry.get_text().strip():
                return
            pop.popdown()
            m.run_exec(c, entry.get_text().strip(), root.get_active())

        entry.connect("activate", go)
        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
        for cmd in m.exec_history[:4] + [x for x in dp.EXEC_PRESETS if x not in m.exec_history]:
            presets.append(button(cmd, lambda cmd=cmd: entry.set_text(cmd), "flat"))
        form = box(True, 8, label(f"Run in {c['short']}", "dim"), entry, presets,
                   box(False, 8, root, label(""), button("Run", go, "primary")))
        form.set_size_request(420, -1)
        pop.set_child(form)
        pop.connect("show", lambda *_: entry.grab_focus())

        mb = Gtk.MenuButton(label="Run…", tooltip_text="Run a command in it (x)")
        mb.set_popover(pop)
        self.exec_menu = mb
        return mb

    # ---- full-screen views -----------------------------------------------------------------------
    def full_logs(self, c):
        """Formatted logs in less, following (F / Ctrl+C toggle). dockerlogs.py writes to a file
        that less follows, in its own session so Ctrl+C in less doesn't stop it; stopped, and the file
        gone, however less ends (q, or the popup closed: a hang-up)."""
        script = (f'f=$(mktemp); trap \'kill -- -$p 2>/dev/null; rm -f "$f"\' EXIT; trap exit HUP TERM; '
                  f'setsid {shlex.quote(sys.executable)} {shlex.quote(dp.DOCKERLOGS)} --follow '
                  f'{shlex.quote(c["ID"])} --width "$(tput cols)" >"$f" 2>/dev/null & p=$!; sleep 0.4; '
                  f'less -R --mouse +F {shlex.quote("-Ps" + c["Names"] + " logs · F follow · Ctrl+C stop following · / search · q close$")} "$f"')
        self.terminal(["sh", "-c", script], f"{c['short']} logs")

    def project_logs(self, name):
        script = (f"docker compose --ansi always -p {shlex.quote(name)} logs --follow --tail 200 2>&1 | "
                  f"less -R --mouse +F {shlex.quote(f'-Ps{name} logs · F follow · Ctrl+C stop following · / search · q close$')}")
        self.terminal(["sh", "-c", script], f"{name} logs")

    # ---- keys ------------------------------------------------------------------------------------
    def key_action(self, k):
        """The TUI's letter keys, for the selected item."""
        m = self.model
        e = m.current()
        if not e:
            return
        if k == "y":
            if dp.is_project(e):
                self.copy(e["project"], "name")
            else:
                self.copy({"images": e.get("ref") if not e.get("dangling") else e["ID"]}.get(m.tab, e["ID"]),
                          "name" if m.tab in ("images", "volumes") else "ID")
            return
        if e["ID"] in m.busy:
            return
        if dp.is_project(e):
            if k in ("s", "r", "U", "B"):
                self.project_action(k, e)
            elif k == "l":
                self.project_logs(e["project"])
            elif k == "d":
                self.remove(e)
            elif k in ("F", "C") and e["containers"][0]["workdir"]:
                self.launch(*(["python3", FILES] if k == "F" else ["code"]), e["containers"][0]["workdir"])
        elif k == "d":
            self.remove(e)
        elif m.tab == "containers":
            if k in ("s", "r", "o", "p"):
                self.container_action(k, e)
            elif k == "l":
                self.full_logs(e)
            elif k in ("e", "x"):
                if e["State"] != "running":
                    self.say("start the container first (s)")
                elif k == "e":
                    m.run_shell(e)
                elif getattr(self, "exec_menu", None):
                    self.exec_menu.popup()
        elif m.tab == "images" and k == "u":
            if e["dangling"]:
                self.say("an untagged image can't be pulled")
            else:
                m.act([e["ID"]], "pulling…", ["pull", e["ref"]], f"pulled {e['ref']}")
        elif m.tab == "images" and k == "n":
            self.run_image(e)
        self.update()

    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.pane_filter.grab_focus()
            return True
        if self.typing():
            return False
        name = Gdk.keyval_name(keyval) or ""
        m = self.model
        if name in ("1", "2", "3", "4"):
            self.switch(dp.TABS[int(name) - 1])
        elif name in ("Tab", "Right"):
            self.switch(dp.TABS[(dp.TABS.index(m.tab) + 1) % 4])
        elif name in ("ISO_Left_Tab", "Left"):
            self.switch(dp.TABS[(dp.TABS.index(m.tab) - 1) % 4])
        elif name in ("slash", "KP_Divide"):
            self.search.grab_focus()
        elif name == "Escape" and self.query:
            self.leave_search()
        elif name == "f":
            self.cycle_only()
        elif name == "w" and m.tab == "containers":
            self.cycle_pane()
        elif name in ("j", "k"):
            self.move(1 if name == "j" else -1)
        elif name in ("g", "G"):
            self.move(-10**6 if name == "g" else 10**6)
        elif name == "L":
            self.terminal(["lazydocker"], "lazydocker")
        elif name in ("s", "r", "l", "e", "x", "o", "u", "d", "p", "y", "n", "U", "B", "F", "C"):
            self.key_action(name)
        elif name == "D" and m.tab in ("containers", "images"):
            self.prune()
        else:
            return False
        return True


def make():
    return Docker()


def main():
    run(make(), "sami.docker", (1280, 720), toggle=False)


if __name__ == "__main__":
    main()
