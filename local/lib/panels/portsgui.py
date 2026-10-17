#!/usr/bin/env python3
# portsgui.py — Ports (Super + Ctrl + O): everything listening on this PC, by what it is. Running it again
# closes it.
#
#   a row    the port, who can reach it (this PC only / the network), what it is — a dev server with its
#            project in ~/code (from its folder), an app or service (usage.py: its cgroup), a Docker
#            container with its compose project — and how long it's been up
#   filter   All / Dev (your processes in ~/code, agentmux terminals) / Docker / System, and a search
#   keys     Enter / o open http://localhost:<port>   y copy the URL   d (twice) stop it   / search
#
# Stop: a dev server is sent SIGTERM (its whole job when it's one a shell started, so `pnpm dev` and its
# node go together); a container is `docker stop`ped. A system service (root's: no process to see) can't be
# stopped from here. `ss` is asked every 2 s while the panel shows; docker at most every 10 s.
import os, re, signal, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, run, scrolled

CODE = os.path.expanduser("~/code")
HOME = os.path.expanduser("~")
DOCKER_EVERY = 10
FILTERS = (("all", "All"), ("dev", "Dev"), ("docker", "Docker"), ("system", "System"))
# root's services, by port (no process to name them by: ss shows only your own processes)
KNOWN = {22: "SSH", 53: "DNS (systemd-resolved)", 631: "Printing (CUPS)", 5353: "mDNS (Avahi / resolved)",
         5355: "LLMNR (systemd-resolved)", 53317: "LocalSend (lsend)", 323: "Time (chrony)"}
DOCKER_PORT = re.compile(r"(?:[\d.]+|\[::\]):(\d+)(?:-(\d+))?->(\d+)(?:-(\d+))?/(tcp|udp)")
G_DEV, G_DOCKER, G_APP, G_SYSTEM = "", "", "", ""   # code, docker, window, cog

CSS = """
.port-list > row { padding: 7px 12px; }
.port-num { font-weight: 700; min-width: 64px; }
.port-glyph { min-width: 20px; color: #565f89; }
.port-name { color: #c0caf5; }
.port-where { color: #565f89; }
.port-wide { color: #e0af68; }
.chip { padding: 3px 10px; color: #565f89; background: transparent; }
.chip:hover { color: #a9b1d6; background: alpha(#c0caf5, .06); }
.chip.on { color: #c0caf5; background: #292e42; }
.port-tools { padding: 0 0 8px 0; }
"""


def ago(seconds):
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def docker_up(status):
    """docker ps's "Up 49 minutes (healthy)" as ago() says it: 49m (unhealthy kept: it matters)."""
    m = re.match(r"(?:About )?(an?|\d+) (second|minute|hour|day|week|month)", status)
    if not m:
        return status
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    secs = n * {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000}[m.group(2)]
    return ago(secs) + (" unhealthy" if "unhealthy" in status else "")


def started(pid):
    """Seconds since the process started (its starttime in /proc, after boot), or None."""
    try:
        fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        boot = float(open("/proc/uptime").read().split()[0])
        return boot - int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return None


def project_of(path):
    """The ~/code project a folder is in, as its path under ~/code (the folder itself when it's a repo's
    subfolder: the nearest one with a .git or a package.json), or ""."""
    path = os.path.realpath(path or "")
    if not path.startswith(CODE + "/"):
        return ""
    top, d = path, path
    while d.startswith(CODE + "/"):   # the outermost one: a monorepo's apps/web is the monorepo
        if os.path.exists(f"{d}/.git") or os.path.exists(f"{d}/package.json"):
            top = d
        d = os.path.dirname(d)
    return os.path.relpath(top, CODE)


def listening():
    """[(proto, address, port, pid or None)]: ss's listening TCP and bound UDP sockets, without multicast
    and IPv4 / IPv6 twins of the same thing."""
    try:
        out = subprocess.run(["ss", "-Hltunp"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    seen, rows = set(), []
    for line in out.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        addr, _, port = f[4].rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        if not port.isdigit() or addr.startswith(("224.", "ff0", "239.")):
            continue
        m = re.search(r"pid=(\d+)", line)
        pid = int(m.group(1)) if m else None
        key = (f[0], int(port), pid)
        if key in seen:
            continue
        seen.add(key)
        rows.append((f[0], addr, int(port), pid))
    return rows


class Ports(View):
    title, subtitle = "Ports", "what listens on this PC"
    icon = "\U000f0318"   # nf-md-lan
    interval = 2.0
    css = CSS
    hints = [("Enter", "open"), ("y", "copy URL"), ("d", "stop (twice)"), ("f", "filter"), ("/", "search"),
             ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.only, self.query = "all", ""
        self.rows, self.shown_sig = [], None
        self.docker, self.docker_at = {}, 0   # (proto, host port) -> container info
        self.armed = None                     # (key, time): Stop pressed once

    def build(self):
        self.search = Gtk.SearchEntry(placeholder_text="search  (/)", hexpand=True)
        self.search.connect("search-changed", lambda e: self.set_query(e.get_text()))
        self.search.connect("stop-search", lambda e: (e.set_text(""), self.list.grab_focus()))
        self.search.connect("activate", lambda *_: self.list.grab_focus())
        self.chips = {}
        chips = box(False, 2)
        for key, text in FILTERS:
            b = button(text, lambda key=key: self.set_only(key), "chip")
            b.set_focusable(False)
            self.chips[key] = b
            chips.append(b)
        tools = box(False, 10, self.search, chips, classes=("port-tools",))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("port-list")
        self.list.set_activate_on_single_click(False)
        self.list.connect("row-activated", lambda _l, row: self.open_url(row.item))
        self.actions = box(False, 8)
        self.actions.set_margin_top(10)
        page = box(True, 0, tools, scrolled(self.list), self.actions)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(16)
        self.list.connect("row-selected", lambda *_: self.paint_actions())
        self.refresh()
        GLib.idle_add(lambda: self.list.grab_focus() and False)
        return page

    # ---- what listens ----------------------------------------------------------------------------
    def containers(self):
        """Docker's published ports: (proto, host port) -> {name, project, folder, status} (cached)."""
        if time.time() - self.docker_at < DOCKER_EVERY:
            return self.docker
        self.docker_at, found = time.time(), {}
        fmt = '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}\t' \
              '{{.Label "com.docker.compose.project.working_dir"}}\t{{.Status}}'
        try:
            out = subprocess.run(["docker", "ps", "--format", fmt], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        for line in out.splitlines():
            name, ports, project, folder, status = (line.split("\t") + [""] * 5)[:5]
            for lo, hi, _in_lo, _in_hi, proto in DOCKER_PORT.findall(ports):
                for port in range(int(lo), int(hi or lo) + 1):
                    found[(proto, port)] = {"name": name, "project": project, "folder": folder,
                                            "status": status.removeprefix("Up ")}
        self.docker = found
        return found

    def gather(self):
        docker = self.containers()
        items = []
        for proto, addr, port, pid in listening():
            wide = addr in ("0.0.0.0", "::", "*")
            item = {"key": (proto, port, pid), "proto": proto, "port": port, "wide": wide, "pid": pid}
            c = docker.get((proto, port))
            if c:
                rel = project_of(c["folder"])
                item.update(kind="docker", name=c["name"], where=(rel or c["project"] or "container"),
                            up=docker_up(c["status"]), container=c["name"])
            elif pid:
                cwd = os.path.realpath(f"/proc/{pid}/cwd") if os.path.exists(f"/proc/{pid}/cwd") else ""
                rel = project_of(cwd)
                cg = usage.cgroup_of(pid)
                group, what = usage.describe(cg, [str(pid)]) if cg else ("", "app")
                prog = usage.label_of(pid)
                if re.match(r"v?\d", prog):   # a process titled by its version (Next.js: "next-server (v16)")
                    prog = os.path.basename(usage.read(f"/proc/{pid}/cmdline").split("\0")[0].split()[0] or prog)
                dev = bool(rel) or what == "agent" or group.startswith("agentmux")
                t = started(pid)
                item.update(kind="dev" if dev else "app", name=prog,
                            where=rel or (group if group and group != prog else
                                          {"service": "user service", "system": "system"}.get(what, cwd.replace(HOME, "~"))),
                            up=ago(t) if t is not None else "")
            else:
                item.update(kind="system", name=KNOWN.get(port, "system service"), where="root", up="")
            items.append(item)
        order = {"dev": 0, "docker": 1, "app": 2, "system": 3}
        items.sort(key=lambda i: (order[i["kind"]], i["port"]))
        return items

    def refresh(self):
        self.rows = self.gather()
        self.paint()

    def passes(self, i):
        if self.only == "dev" and i["kind"] != "dev":
            return False
        if self.only == "docker" and i["kind"] != "docker":
            return False
        if self.only == "system" and i["kind"] not in ("system", "app"):
            return False
        hay = f"{i['port']} {i['name']} {i['where']} {i['proto']}".casefold()
        return all(w in hay for w in self.query.casefold().split())

    def paint(self):
        shown = [i for i in self.rows if self.passes(i)]
        sig = (self.only, self.query, tuple((i["key"], i["name"], i["where"], i["up"]) for i in shown))
        for key, b in self.chips.items():
            (b.add_css_class if key == self.only else b.remove_css_class)("on")
        n = {k: sum(i["kind"] == k for i in self.rows) for k in ("dev", "docker")}
        self.set_subtitle(f"{len(self.rows)} listening · {n['dev']} dev · {n['docker']} docker")
        if sig == self.shown_sig:
            return
        self.shown_sig = sig
        keep = self.list.get_selected_row().item["key"] if self.list.get_selected_row() else None
        clear(self.list)
        for i in shown:
            row = Gtk.ListBoxRow()
            row.item = i
            glyph = {"dev": G_DEV, "docker": G_DOCKER, "app": G_APP, "system": G_SYSTEM}[i["kind"]]
            port = label(f"{i['port']}" + ("" if i["proto"] == "tcp" else "/udp"), "port-num")
            reach = label("network" if i["wide"] else "this PC", "port-wide" if i["wide"] else "port-where")
            reach.set_tooltip_text("reachable from other machines on the network" if i["wide"]
                                   else "only from this PC (localhost)")
            name = label(i["name"], "port-name", ellipsize=True)
            name.set_width_chars(20)   # a column: the projects line up under each other
            name.set_max_width_chars(20)
            name.set_hexpand(False)
            where = label(i["where"], "port-where", ellipsize=True)
            where.set_hexpand(True)
            up = label(i["up"], "dim", xalign=1.0)
            up.set_width_chars(4)
            reach.set_width_chars(8)
            reach.set_xalign(1.0)
            row.set_child(box(False, 12, label(glyph, "port-glyph"), port, name, where, reach, up))
            self.list.append(row)
            if i["key"] == keep:
                self.list.select_row(row)
        if not shown:
            empty = Gtk.ListBoxRow(selectable=False, activatable=False)
            empty.item = None
            empty.set_child(label("Nothing listening" + (" matches" if self.query or self.only != "all" else ""), "dim"))
            self.list.append(empty)
        if self.list.get_selected_row() is None and shown:
            self.list.select_row(self.list.get_row_at_index(0))
        self.paint_actions()

    def selected(self):
        row = self.list.get_selected_row()
        return row.item if row else None

    def paint_actions(self):
        clear(self.actions)
        i = self.selected()
        if not i:
            return
        sure = self.armed and self.armed[0] == i["key"] and time.time() - self.armed[1] < 3
        stop = button("Click again to stop" if sure else "Stop", lambda: self.stop(i), "danger",
                      "armed" if sure else "flat", tooltip="d, twice")
        stop.set_sensitive(i["kind"] != "system")
        for b in (button("Open", lambda: self.open_url(i), "primary", tooltip="Enter"),
                  button("Copy URL", lambda: self.copy(i), tooltip="y"), label(""), stop):
            if isinstance(b, Gtk.Button):
                b.set_focusable(False)
            self.actions.append(b)
        self.actions.get_last_child().get_prev_sibling().set_hexpand(True)

    # ---- actions ---------------------------------------------------------------------------------
    def url(self, i):
        return f"http://localhost:{i['port']}"

    def open_url(self, i):
        if i:
            subprocess.Popen(["xdg-open", self.url(i)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"opened {self.url(i)}")

    def copy(self, i):
        self.window.get_clipboard().set(self.url(i))
        self.say(f"copied {self.url(i)}")

    def stop(self, i):
        """Twice within 3 s: a container is docker stopped, a process gets SIGTERM — its whole job when a
        shell started it as one (its own process group, not the shell's session)."""
        if i["kind"] == "system":
            return
        if not (self.armed and self.armed[0] == i["key"] and time.time() - self.armed[1] < 3):
            self.armed = (i["key"], time.time())
            self.say(f"press again (d) to stop {i['name']} on {i['port']}")
            GLib.timeout_add(3100, lambda: self.paint_actions() or False)
            return self.paint_actions()
        self.armed = None
        if i["kind"] == "docker":
            subprocess.Popen(["docker", "stop", i["container"]], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.docker_at = 0
            self.say(f"stopping {i['container']}…")
            return
        try:
            pgid, sid = os.getpgid(i["pid"]), os.getsid(i["pid"])
            if pgid != sid and pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(i["pid"], signal.SIGTERM)
            self.say(f"stopped {i['name']} (port {i['port']})")
        except OSError as e:
            self.say(f"couldn't stop it: {e.strerror}", "bad")
        GLib.timeout_add(600, lambda: self.refresh() or False)

    def set_only(self, key):
        self.only = key
        self.paint()

    def set_query(self, text):
        self.query = text.strip()
        self.paint()

    def leave_text(self):
        self.list.grab_focus()

    def key(self, keyval, state):
        if self.typing():
            return False
        i = self.selected()
        if keyval in (Gdk.KEY_slash, Gdk.KEY_KP_Divide):
            self.search.grab_focus()
        elif keyval == Gdk.KEY_f:
            keys = [k for k, _ in FILTERS]
            self.set_only(keys[(keys.index(self.only) + 1) % len(keys)])
        elif keyval == Gdk.KEY_o and i:
            self.open_url(i)
        elif keyval == Gdk.KEY_y and i:
            self.copy(i)
        elif keyval in (Gdk.KEY_d, Gdk.KEY_Delete) and i:
            self.stop(i)
        else:
            return False
        return True


def make():
    return Ports()


def main():
    run(make(), "panels.ports", (900, 560))


if __name__ == "__main__":
    main()
