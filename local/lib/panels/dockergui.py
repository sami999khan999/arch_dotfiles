#!/usr/bin/env python3
# dockergui.py — the Docker panel as a GTK window (Tokyo Night, plain). Workspace 9's app
# (config/hypr/workspaces.conf); tiled, so running it again just brings it up.
#
#   tabs   Containers · Images · Volumes · Networks (1-4)
#   left   the tab's list; containers grouped by compose project, each project a row of its own
#          (its − / + button, Enter or double-click collapses it), like Docker Desktop
#   right  the selected item: details, what you can do with it, then its logs / layers / members
#
# The data and the docker commands are dockerpanel.py's (its DockerPanel is the model here:
# it polls docker in background threads); this only draws them. Full-screen things (logs in
# less, exec, compose logs, lazydocker) open in a floating terminal.
import os, re, shlex, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dockerlogs
import dockerpanel as dp
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, clear, label, run, scrolled

TAB_NAMES = {"containers": "Containers", "images": "Images", "volumes": "Volumes", "networks": "Networks"}

CSS = """
/* each project (or group) starts with space and a thin line, so the groups read as blocks */
.group-row, .project-row { margin-top: 10px; padding-top: 10px; border-top: 1px solid alpha(#3b4261, .7); }
.group-row.first, .project-row.first { margin-top: 0; border-top: none; }
.fold { min-width: 18px; min-height: 0; padding: 0 2px; border: none; color: #565f89; }
.fold:hover { color: #c0caf5; background: transparent; }
.red { color: #f7768e; }
.kv-key { color: #565f89; }
textview, textview text { background: transparent; color: #c0caf5; }
.logs { border-top: 1px solid alpha(#3b4261, .7); }
"""


def fit(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


class Docker(View):
    title, subtitle = "Docker", "containers, images, volumes, networks"
    icon = "\uf308"   # nf-linux-docker, as the terminal panel
    interval = 1.0
    css = CSS
    hints = [("1-4", "tabs"), ("s", "start/stop"), ("r", "restart"), ("l", "logs"), ("e", "exec"),
             ("o", "open port"), ("u", "pull"), ("d", "remove"), ("Enter", "collapse"), ("L", "lazydocker")]

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

    # ---- plumbing --------------------------------------------------------------------------------
    def model_say(self, msg, color=dp.ACCENT):
        """dockerpanel's say() (act() reports with it), from any thread."""
        GLib.idle_add(lambda: self.say(msg, "bad" if color == dp.RED else "ok") and False)

    def terminal(self, argv, title="docker"):
        """A program in a floating terminal (TUI.float: windowrules float and centre it)."""
        subprocess.Popen(["uwsm", "app", "--", "kitty", "--class", "TUI.float", "--title", title, "-e", *argv],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def header_extra(self):
        return [button("lazydocker", lambda: self.terminal(["lazydocker"], "lazydocker"), "flat",
                       tooltip="Everything else (L)")]

    # ---- layout ----------------------------------------------------------------------------------
    def build(self):
        self.tabs = {}
        bar = box(False, 0, classes=("tabs",))
        for i, t in enumerate(dp.TABS, 1):
            b = button("", lambda t=t: self.switch(t), "tab", tooltip=f"{i}")
            self.tabs[t] = b
            bar.append(b)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(False)
        self.list.connect("row-selected", self.on_select)
        self.list.connect("row-activated", lambda _l, row: self.fold(row.eid))
        left = scrolled(self.list)
        left.add_css_class("side")
        left.set_size_request(560, -1)

        self.heading = label("", "heading", ellipsize=True)
        self.state = label("", "dim", ellipsize=True)
        self.kv = Gtk.Grid(column_spacing=16, row_spacing=4)
        self.kv.set_margin_top(12)
        self.actions = box(False, 8)
        self.actions.set_margin_top(14)
        self.lower_title = label("", "section")
        top = box(True, 0, self.heading, self.state, self.kv, self.actions, self.lower_title)
        for edge in ("start", "end", "top"):
            getattr(top, f"set_margin_{edge}")(20)

        self.text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=20, right_margin=20,
                                 top_margin=6, bottom_margin=10)
        buf = self.text.get_buffer()
        for name, props in {"dim": {"foreground": "#565f89"}, "red": {"foreground": "#f7768e"},
                            "accent": {"foreground": "#6b8fe0"}, "sub": {"foreground": "#a9b1d6"},
                            "green": {"foreground": "#9ece6a"},
                            "bold": {"weight": Pango.Weight.BOLD}}.items():
            buf.create_tag(name, **props)
        self.text_scroll = scrolled(self.text)

        right = box(True, 0, top, self.text_scroll)
        right.set_hexpand(True)
        self.update()
        GLib.idle_add(lambda: self.list.grab_focus() and False)
        return box(True, 0, bar, box(False, 0, left, right))

    # ---- refresh ---------------------------------------------------------------------------------
    def refresh(self):
        """docker ps runs in a thread (it can take a while when docker is busy); the stats,
        resources and logs threads are dockerpanel's own."""
        if self.ticking:
            return
        self.ticking = True

        def work():
            self.model.tick()
            GLib.idle_add(self.after_tick)
        threading.Thread(target=work, daemon=True).start()

    def after_tick(self):
        self.ticking = False
        self.update()
        return False

    def update(self):
        m = self.model
        for t, b in self.tabs.items():
            b.set_label(f"{TAB_NAMES[t]} {len(m.entries(t))}")
            (b.add_css_class if t == m.tab else b.remove_css_class)("on")
        self.paint_list()
        self.paint_detail()

    # ---- the list --------------------------------------------------------------------------------
    def layout(self):
        """[(kind, id, payload)] in on-screen order: kind is header, project or entry."""
        m, out = self.model, []
        for title, note, group in m.groups():
            project = m.tab == "containers" and group[0]["project"]
            if project:
                out.append(("project", dp.PROJECT + project, project))
                if project in self.folded:
                    continue
            else:
                out.append(("header", None, (title, note)))
            out += [("entry", e["ID"], e) for e in group]
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
            self.list.append(Gtk.ListBoxRow(child=label(m.error or "nothing here", "red" if m.error else "dim"),
                                            selectable=False))
            return
        sel_row = None
        row = self.list.get_first_child()
        for kind, eid, payload in rows:
            row.set_child(self.row_content(kind, eid, payload))
            if eid and eid == m.sel[m.tab]:
                sel_row = row
            row = row.get_next_sibling()
        if sel_row is not None and self.list.get_selected_row() is not sel_row:
            self.building = True
            self.list.select_row(sel_row)
            self.building = False

    def cols(self, name, *tail, dim=False, name_cls=()):
        """A list line: the name, then fixed-width columns (the font is monospace)."""
        n = label(name, *name_cls, ellipsize=True)
        if dim:
            n.add_css_class("dim")
        line = box(False, 14, n)
        for text, width, cls in tail:
            lab = label(text, *cls, xalign=1.0 if width < 0 else 0.0)
            lab.set_width_chars(abs(width))
            lab.set_max_width_chars(abs(width))
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            line.append(lab)
        return line

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
            toggle = button("+" if folded else "\u2212", lambda: self.fold(eid), "flat", "fold",
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
            return self.cols(name, *([busy] if busy else [(dp.short_size(e["size"]), -6, ()),
                                                          (e["created"], -14, ("dim",))]), dim=not e["used"])
        if m.tab == "volumes":
            users = ", ".join(m.short(n, e["project"]) for n in e["used"]) or "unused"
            return self.cols(e["short"], *([busy] if busy else [(dp.short_size(e["size"]), -6, ()),
                                                                (users, 18, ("dim",))]), dim=not e["used"])
        return self.cols(e["name"], *([busy] if busy else [(e["driver"], 8, ("dim",)), (e["subnet"], 16, ()),
                                                           (str(len(e["members"])), -3, ("dim",))]),
                         dim=not e["members"])

    def container_line(self, c):
        m = self.model
        _, _, words = dp.status(c)
        running = c["State"] == "running"
        bad = words == "unhealthy" or words.startswith("exited (")
        if c["ID"] in m.busy:
            return self.cols(c["short"], (m.busy[c["ID"]], -32, ("accent",)), dim=not running)
        if not running:
            when = re.sub(r"^Exited \(\d+\) ", "", c["Status"])
            return self.cols(c["short"], (f"{words} · {when}", -32, ("red",) if bad else ("dim",)), dim=True)
        s = m.stats.get(c["ID"])
        cpu = f"{dp.percent(s['CPUPerc']):.1f}%" if s else "…"
        mem = dp.short_size(s["MemUsage"]) if s else "…"
        pp = dp.ports(c["Ports"])
        port = (pp[0][0] + ("+" if len(pp) > 1 else "")) if pp else ""
        return self.cols(c["short"], (cpu, -6, ()), (mem, -6, ()), (port, -7, ("dim",)),
                         ("unhealthy" if bad else "", -9, ("red",)))

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
            self.update()

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
        clear(self.kv)
        if not e:
            self.heading.set_text("Docker isn't answering" if m.error else "Nothing selected")
            self.state.set_text(m.error)
            clear(self.actions)
            self.lower_title.set_text("")
            self.set_text([])
            return
        if dp.is_project(e):
            self.project_detail(e)
        else:
            {"containers": self.container_detail, "images": self.image_detail,
             "volumes": self.volume_detail, "networks": self.network_detail}[m.tab](e)

    def kv_rows(self, pairs):
        for i, (k, v) in enumerate(pairs):
            self.kv.attach(label(k, "kv-key"), 0, i, 1, 1)
            self.kv.attach(label(v, ellipsize=True, wrap=False), 1, i, 1, 1)

    def set_actions(self, sig, buttons):
        """Rebuilt only when what you can do changes, so an open Exec popover survives refreshes."""
        if sig == self.actions_sig:
            return
        self.actions_sig = sig
        clear(self.actions)
        for b in buttons:
            self.actions.append(b)

    def set_text(self, segments, key=None, follow=False):
        """The lower pane: [(text, tag or None)]. Only rewritten when its content changed."""
        key = key if key is not None else "".join(t for t, _ in segments)
        if key == self.text_key:
            return
        self.text_key = key
        buf = self.text.get_buffer()
        buf.set_text("")
        for text, tag in segments:
            if tag:
                buf.insert_with_tags_by_name(buf.get_end_iter(), text, tag)
            else:
                buf.insert(buf.get_end_iter(), text)
        if follow:
            GLib.idle_add(lambda: self.text.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 1) and False)

    def act_button(self, text, fn, *cls, tooltip=None, busy=False):
        b = button(text, fn, *cls, tooltip=tooltip)
        b.set_sensitive(not busy)
        return b

    def container_detail(self, c):
        m = self.model
        _, _, words = dp.status(c)
        running = c["State"] == "running"
        busy = c["ID"] in m.busy
        self.heading.set_text(c["Names"])
        self.state.set_text(m.busy[c["ID"]] if busy else words)
        pairs = [("Image", c["Image"]),
                 ("Up", re.sub(r"^Up |\s*\(.*\)$", "", c["Status"])) if running else ("Status", c["Status"])]
        pp = dp.ports(c["Ports"])
        if pp:
            pairs.append(("Ports", "  ".join(f"{h}→{p}" + ("" if t == "tcp" else f"/{t}") for h, p, t in pp)))
        s = m.stats.get(c["ID"]) if running else None
        if s:
            pairs += [("CPU", f"{dp.percent(s['CPUPerc']):.1f}%"),
                      ("Memory", s["MemUsage"].replace(" / ", " of ")),
                      ("Net I/O", s["NetIO"].replace(" / ", " in · ") + " out")]
        if c["workdir"]:
            pairs.append(("Project", c["workdir"].replace(dp.HOME, "~")))
        self.kv_rows(pairs)

        buttons = [self.act_button("Stop" if running else "Start", lambda: self.container_action("s", c),
                                   "primary", tooltip="s", busy=busy),
                   self.act_button("Restart", lambda: self.container_action("r", c), tooltip="r", busy=busy),
                   button("Logs", lambda: self.full_logs(c), tooltip="Full screen, following (l)")]
        if running:
            buttons.append(self.exec_button(c))
        if pp:
            buttons.append(button("Open port", lambda: self.container_action("o", c), tooltip="o"))
        self.set_actions((c["ID"], running, busy, bool(pp)), buttons)

        self.lower_title.set_text("Logs")
        if m.logs_for != c["ID"]:
            self.set_text([("loading…", "dim")], key=("loading", c["ID"]))
        elif not m.logs:
            self.set_text([("no output yet", "dim")], key=("empty", c["ID"]))
        else:
            self.set_text(self.log_segments(m.logs), key=(c["ID"], id(m.logs)), follow=True)

    def log_segments(self, records):
        out = []
        for r in records[-300:]:
            out.append((f"{r.time or '':<8}  ", "dim"))
            badge = {"error": "ERR", "warn": "WRN", "info": "INF", "debug": "DBG", "trace": "TRC"}.get(r.level, "   ")
            out.append((f"{badge}  ", "red" if r.level == "error" else "dim"))
            out.append((r.msg, "red" if r.level == "error" else None))
            for k, v in r.fields:
                out.append((f"  {k}=", "dim"))
                out.append((v if " " not in v else f'"{v}"', "red" if k in ("err", "error", "exception") else "sub"))
            if r.count > 1:
                out.append((f"  ×{r.count}", "dim"))
            out.append(("\n", None))
        return out

    def project_detail(self, p):
        m = self.model
        group = p["containers"]
        up = [c for c in group if c["State"] == "running"]
        busy = p["ID"] in m.busy
        self.heading.set_text(p["project"])
        self.state.set_text(m.busy[p["ID"]] if busy else
                            f"running {len(up)}/{len(group)}" if up else "exited")
        pairs = [("Folder", group[0]["workdir"].replace(dp.HOME, "~") or "—"),
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
        self.set_actions((p["ID"], bool(up), busy, folded), [
            self.act_button("Stop all" if up else "Start all", lambda: self.project_action("s", p), "primary",
                            tooltip="s", busy=busy),
            self.act_button("Restart all", lambda: self.project_action("r", p), tooltip="r", busy=busy),
            button("Logs", lambda: self.project_logs(p["project"]), tooltip="Every container's logs, following (l)"),
            button("Expand" if folded else "Collapse", lambda: self.fold(p["ID"]), tooltip="Enter")])
        self.lower_title.set_text("Containers")
        seg = []
        for c in group:
            _, _, words = dp.status(c)
            bad = words == "unhealthy" or words.startswith("exited (")
            seg.append((f"{c['short']:<24}", None if c["State"] == "running" else "dim"))
            seg.append((m.busy.get(c["ID"], words) + "\n",
                        "accent" if c["ID"] in m.busy else "red" if bad
                        else "green" if c["State"] == "running" else "dim"))
        self.set_text(seg)

    def image_detail(self, i):
        m = self.model
        busy = i["ID"] in m.busy
        self.heading.set_text(i["ref"] if not i["dangling"] else f"<none> {i['ID']}")
        self.state.set_text(m.busy[i["ID"]] if busy else "in use" if i["used"] else
                            "dangling" if i["dangling"] else "unused")
        self.kv_rows([("ID", i["ID"]), ("Size", i["size"]), ("Created", i["created"]),
                      ("Used by", ", ".join(i["used"]) or "no containers")])
        pull = self.act_button("Pull", lambda: self.key_action("u"), "primary", tooltip="Pull a newer version (u)",
                               busy=busy or i["dangling"])
        self.set_actions((i["ID"], busy), [pull, self.remove_button(i, busy)])
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
        self.kv_rows([("Name", v["ID"]), ("Size", v["size"]), ("Driver", v["driver"]),
                      ("Project", v["project"] or "—"), ("Mountpoint", v["mount"])])
        self.set_actions((v["ID"], busy), [self.remove_button(v, busy)])
        self.lower_title.set_text(f"Used by ({len(v['used'])})")
        self.set_text([(n + "\n", None) for n in v["used"]] or [("no containers", "dim")])

    def network_detail(self, n):
        m = self.model
        busy = n["ID"] in m.busy
        self.heading.set_text(n["name"])
        self.state.set_text(m.busy[n["ID"]] if busy else f"{n['driver']} · {n['scope']}")
        self.kv_rows([("ID", n["ID"]), ("Subnet", n["subnet"] or "—"), ("Gateway", n["gateway"] or "—"),
                      ("Internal", "yes" if n["internal"] else "no"), ("Project", n["project"] or "—"),
                      ("Created", n["created"])])
        builtin = n["name"] in dp.BUILTIN_NETWORKS
        self.set_actions((n["ID"], busy), [] if builtin else [self.remove_button(n, busy)])
        self.lower_title.set_text(f"Containers ({len(n['members'])})")
        seg = []
        for name, ip in n["members"]:
            seg += [(f"{name:<36}", None), (ip + "\n", "dim")]
        self.set_text(seg or [("none attached", "dim")])

    def remove_button(self, e, busy):
        return self.act_button("Remove", lambda: self.remove(e), tooltip="Press twice to remove (d)",
                               busy=busy)

    # ---- actions ---------------------------------------------------------------------------------
    def container_action(self, k, c):
        m = self.model
        if k == "s":
            if c["State"] == "running":
                m.act([c["ID"]], "stopping…", ["stop", c["ID"]], f"stopped {c['short']}")
            else:
                m.act([c["ID"]], "starting…", ["start", c["ID"]], f"started {c['short']}")
        elif k == "r":
            m.act([c["ID"]], "restarting…", ["restart", c["ID"]], f"restarted {c['short']}")
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
        Only the rows that will change say so; the project row says what's happening."""
        name, group = p["project"], p["containers"]
        running = [c for c in group if c["State"] == "running"]
        if k == "s":
            doing, verb, done, rows = ("stopping…", "stop", "stopped", running) if running else \
                ("starting…", "start", "started", group)
        else:
            doing, verb, done, rows = "restarting…", "restart", "restarted", group
        labels = {c["ID"]: doing for c in rows} | {p["ID"]: doing}
        self.model.act(labels, doing, ["compose", "-p", name, verb], f"{name}: {done}")
        self.update()

    def remove(self, e):
        """Twice (the button or d) within 3 s: remove the image, volume or network."""
        m = self.model
        name = {"images": e.get("ref"), "volumes": e.get("short"), "networks": e.get("name")}.get(m.tab)
        if not name:
            return
        if m.tab == "networks" and e["name"] in dp.BUILTIN_NETWORKS:
            self.say(f"{name} is built into docker and can't be removed")
            return
        if self.armed and self.armed[0] == e["ID"] and time.time() - self.armed[1] < 3:
            self.armed = None
            cmd = {"images": ["rmi", e["ID"]], "volumes": ["volume", "rm", e["ID"]],
                   "networks": ["network", "rm", e["ID"]]}[m.tab]
            m.act([e["ID"]], "removing…", cmd, f"removed {name}")
            self.update()
        else:
            self.armed = (e["ID"], time.time())
            self.say(f"press Remove (or d) again to remove {name}")

    # ---- exec ------------------------------------------------------------------------------------
    def exec_button(self, c):
        """Exec: a small form in a popover, pre-filled with the container's best shell."""
        m = self.model
        entry = Gtk.Entry(text="sh", hexpand=True)
        entry.touched = False
        entry.connect("changed", lambda *_: setattr(entry, "touched", entry.has_focus()))
        root = Gtk.CheckButton(label="as root")
        pop = Gtk.Popover()

        def go(*_):
            pop.popdown()
            m.run_exec(c, entry.get_text().strip() or "sh", root.get_active())

        entry.connect("activate", go)
        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
        for cmd in m.exec_history[:4] + [x for x in dp.EXEC_PRESETS if x not in m.exec_history]:
            presets.append(button(cmd, lambda cmd=cmd: entry.set_text(cmd), "flat"))
        form = box(True, 8, label(f"Run in {c['short']}", "dim"), entry, presets,
                   box(False, 8, root, label(""), button("Run", go, "primary")))
        form.set_size_request(420, -1)
        pop.set_child(form)

        def detect():
            try:
                r = dp.docker("exec", c["ID"], "sh", "-c", "command -v bash || command -v ash || command -v sh",
                              timeout=5)
                shell = os.path.basename(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.split() else ""
            except (OSError, subprocess.TimeoutExpired):
                shell = ""
            if shell:
                GLib.idle_add(lambda: (not entry.touched and entry.set_text(shell)) and False)
        pop.connect("show", lambda *_: threading.Thread(target=detect, daemon=True).start())

        mb = Gtk.MenuButton(label="Exec", tooltip_text="Run a command or a shell in it (e)")
        mb.set_popover(pop)
        self.exec_menu = mb
        return mb

    # ---- full-screen views -----------------------------------------------------------------------
    def full_logs(self, c):
        """Formatted logs in less, following (F / Ctrl+C toggle). dockerlogs.py writes to a file
        that less follows, in its own session so Ctrl+C in less doesn't stop it."""
        script = (f'f=$(mktemp); setsid {shlex.quote(sys.executable)} {shlex.quote(dp.DOCKERLOGS)} --follow '
                  f'{shlex.quote(c["ID"])} --width "$(tput cols)" >"$f" 2>/dev/null & p=$!; sleep 0.4; '
                  f'less -R --mouse +F {shlex.quote("-Ps" + c["Names"] + " logs · F follow · Ctrl+C stop following · / search · q close$")} "$f"; '
                  f'kill -- -$p 2>/dev/null; rm -f "$f"')
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
        if not e or e["ID"] in m.busy:
            return
        if dp.is_project(e):
            if k in ("s", "r"):
                self.project_action(k, e)
            elif k == "l":
                self.project_logs(e["project"])
        elif k == "d":
            self.remove(e)
        elif m.tab == "containers":
            if k in ("s", "r", "o"):
                self.container_action(k, e)
            elif k == "l":
                self.full_logs(e)
            elif k in ("e", "x"):
                if e["State"] != "running":
                    self.say("start the container first (s)")
                elif getattr(self, "exec_menu", None):
                    self.exec_menu.popup()
        elif m.tab == "images" and k == "u":
            if e["dangling"]:
                self.say("an untagged image can't be pulled")
            else:
                m.act([e["ID"]], "pulling…", ["pull", e["ref"]], f"pulled {e['ref']}")
        self.update()

    def key(self, keyval, state):
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
        elif name in ("j", "k"):
            self.move(1 if name == "j" else -1)
        elif name in ("g", "G"):
            self.move(-10**6 if name == "g" else 10**6)
        elif name == "L":
            self.terminal(["lazydocker"], "lazydocker")
        elif name in ("s", "r", "l", "e", "x", "o", "u", "d"):
            self.key_action(name)
        else:
            return False
        return True


def make():
    return Docker()


def main():
    run(make(), "sami.docker", (1280, 720), toggle=False)


if __name__ == "__main__":
    main()
