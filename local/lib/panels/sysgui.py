#!/usr/bin/env python3
# sysgui.py — the system monitor as a GTK window (Tokyo Night). Super + Ctrl + T, or the CPU /
# memory modules in waybar; running it again closes it. Also a tile of the Control Center
# (compact: the Overview only).
#
#   Overview    CPU, memory and the NVIDIA GPU: a summary, a graph of the last 90 s, the details
#   Apps        CPU by what caused it: each app, service, Docker container and agentmux thread, its
#               finished processes counted too (usage.py: cgroups), what in it is busy; Enter: its
#               processes (a container: the Docker panel). The bar's busy warning opens this tab
#   Processes   every running process: sort by a column, search, End process / Kill (click twice)
#   Storage     each drive: model, SSD / HDD, read / write now, how busy it is, each partition's usage
#   Ports       what's listening: port, this PC only / the network, the program, its project in
#               ~/code, how long it's been up; open it, copy its URL, close it (Stop / Kill, click
#               twice; a Docker container is stopped). "Show all" adds UDP, the system's and the
#               random high ports apps (VS Code, Chrome, the agents) open for themselves
#
#   1-5  tabs     /  search processes     Delete  end the selected process / close the selected port
#
# Everything is read in a background thread (nvidia-smi alone takes a moment), so the window
# never stutters; the CPU / temperature / memory helpers are sysmon.py's (the terminal version).
import json, os, pwd, re, signal, subprocess, sys, threading, time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gi.repository import GObject
from datetime import datetime
from gtkkit import (Gdk, Gio, GLib, Gtk, View, box, button, clear, hint_markup, label, level, recolor, rgbf, rule_heading,
                    run, scrolled)
import sysmon, usage

HISTORY = 60          # samples kept for the graphs (× INTERVAL = 90 s)
HOT = 85              # percent: from here a number turns red
TABS = ["Overview", "Apps", "Processes", "Storage", "Ports"]
CODE = os.path.expanduser("~/code")
ACCENT, TRACK = rgbf("#6b8fe0"), rgbf("#292e42")
RED = rgbf("#f7768e")
LEVEL_RGB = {"green": rgbf("#9ece6a"), "yellow": rgbf("#e0af68"),
             "red": RED}  # gtkkit.level() classes, for bars drawn with cairo
GPU_FIELDS = ["name", "driver_version", "utilization.gpu", "memory.used", "memory.total", "temperature.gpu",
              "power.draw", "power.limit", "clocks.gr", "clocks.max.gr", "clocks.mem", "fan.speed", "pstate"]

CSS = """
levelbar trough { background: #292e42; border: none; padding: 0; min-height: 6px; }
levelbar block { min-height: 6px; border: none; }
levelbar block.filled { background: #6b8fe0; }
levelbar block.empty { background: transparent; }
levelbar.hot block.filled { background: #f7768e; }
/* the Control Center card: thin bars in the level colours (green, amber, red) */
levelbar.thin trough, levelbar.thin block { min-height: 3px; }
levelbar.green block.filled { background: #9ece6a; }
levelbar.yellow block.filled { background: #e0af68; }
levelbar.red block.filled { background: #f7768e; }
.sys-page { margin: 16px 20px; }
.sys-page.compact { margin: 12px 16px; }   /* a Control Center tile: the same inset as its neighbours */
.sys-big { font-weight: bold; }
columnview, columnview listview { background: transparent; color: #c0caf5; }
columnview > header > button { background: transparent; border: none; box-shadow: none; color: #565f89; padding: 4px 8px; }
columnview > header > button:hover { color: #c0caf5; }
columnview > header > button sort-indicator { -gtk-icon-source: none; min-width: 0; min-height: 0; }
columnview listview > row { padding: 0; }
columnview listview > row:hover { background: alpha(#292e42, .35); }
columnview listview > row:selected { background: alpha(#292e42, .75); color: #c0caf5; }
columnview listview > row > cell { padding: 3px 8px; }
"""


# ---- reading the system ---------------------------------------------------------------------------
def meminfo():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0]) * 1024
    return info


def core_ticks():
    """[(total, idle)] per CPU thread, from /proc/stat."""
    out = []
    for line in open("/proc/stat"):
        if line.startswith("cpu") and line[3].isdigit():
            v = list(map(int, line.split()[1:]))
            out.append((sum(v), v[3] + v[4]))
    return out


def cpu_mhz():
    mhz = [float(l.split(":")[1]) for l in open("/proc/cpuinfo") if l.startswith("cpu MHz")]
    return sum(mhz) / len(mhz) if mhz else 0


def gpu():
    """The NVIDIA GPU's numbers as {field: text}, or None (no nvidia-smi, or it failed)."""
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=2).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    vals = [v.strip() for v in out[0].split(",")]
    return dict(zip(GPU_FIELDS, vals)) if len(vals) == len(GPU_FIELDS) else None


def num(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default   # "[N/A]" — the card doesn't report it


def processes():
    """{pid: dict(name, user, ticks, rss, threads, cmd)} for every userspace process."""
    out, users = {}, {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            raw = open(f"/proc/{pid}/stat").read()
            uid = os.stat(f"/proc/{pid}").st_uid
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        comm = raw[raw.index("(") + 1:raw.rindex(")")]
        f = raw[raw.rindex(")") + 2:].split()
        rss = int(f[21]) * sysmon.PAGE
        if rss == 0 and not cmd:   # kernel threads
            continue
        if uid not in users:
            try:
                users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                users[uid] = str(uid)
        out[int(pid)] = dict(name=sysmon.app_name(pid, comm), user=users[uid], ticks=int(f[11]) + int(f[12]),
                             rss=rss, threads=int(f[17]), cmd=cmd or f"[{comm}]")
    return out


def drives():
    """Physical disks with their partitions, from lsblk (sizes in bytes)."""
    try:
        data = json.loads(subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,KNAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,ROTA,TYPE"],
            capture_output=True, text=True, timeout=3).stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    return [d for d in data.get("blockdevices", []) if d.get("type") == "disk" and not d["name"].startswith("zram")]


def diskstats():
    """{kernel name: (bytes read, bytes written, ms spent doing I/O)} since boot."""
    out = {}
    for line in open("/proc/diskstats"):
        f = line.split()
        out[f[2]] = (int(f[5]) * 512, int(f[9]) * 512, int(f[12]))
    return out


# ---- listening ports ------------------------------------------------------------------------------
# the range the kernel hands out random ports from: apps (VS Code, agents, Chrome) listen on a few
# of those for themselves, which isn't what this tab is for, so they're under "Show all"
try:
    EPHEMERAL = int(open("/proc/sys/net/ipv4/ip_local_port_range").read().split()[0])
except (OSError, ValueError, IndexError):
    EPHEMERAL = 32768
BOOT = next((int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime")), 0)
TICK = os.sysconf("SC_CLK_TCK")


def loopback(addr):
    """True for an address only this PC can reach (127.x, ::1, a v4-mapped 127.x)."""
    a = addr.strip("[]").split("%")[0]
    return a.startswith("127.") or a == "::1" or a.startswith("::ffff:127.")


def docker_ports():
    """{host port: container name} for running containers ({} without docker)."""
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for line in out.splitlines():
        name, _, ports = line.partition("\t")
        for m in re.finditer(r":(\d+)(?:-(\d+))?->", ports):   # 0.0.0.0:9000-9001->9000-9001/tcp
            for port in range(int(m[1]), int(m[2] or m[1]) + 1):
                found[port] = name
    return found


def project_of(pid):
    """The project folder (relative to ~/code: the repo the process runs in) of pid, or ""."""
    try:
        d = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""
    if not (d + "/").startswith(CODE + "/"):
        return ""
    cwd = d
    while d.startswith(CODE + "/"):   # a dev server runs in apps/web: its repo is further up
        if os.path.exists(f"{d}/.git"):
            return os.path.relpath(d, CODE)
        d = os.path.dirname(d)
    return os.path.relpath(cwd, CODE)


def started(pid):
    """When pid started (epoch seconds), or 0."""
    try:
        raw = open(f"/proc/{pid}/stat").read()
        return BOOT + int(raw[raw.rindex(")") + 2:].split()[19]) / TICK
    except (OSError, ValueError, IndexError):
        return 0


def listening(docker):
    """Every listening socket, one entry per (protocol, port): its addresses, the processes holding it
    (pids: only this user's show; root's and the system's come without), the program, project and start
    time, and the Docker container publishing it."""
    try:
        out = subprocess.run(["ss", "-Htulnp"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = {}
    for line in out.splitlines():
        f = line.split(None, 6)
        if len(f) < 6:
            continue
        proto, local = f[0], f[4]
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        r = rows.setdefault((proto, int(port)), {"proto": proto, "port": int(port), "addrs": set(), "pids": set()})
        r["addrs"].add(addr)
        r["pids"].update(int(p) for p in re.findall(r"pid=(\d+)", f[6] if len(f) > 6 else ""))
    for r in rows.values():
        pids = sorted(r["pids"])
        r["pid"] = pids[0] if pids else None
        r["local"] = all(loopback(a) for a in r["addrs"])
        r["container"] = docker.get(r["port"]) if r["proto"] == "tcp" else None
        r["name"], r["cmd"], r["project"], r["since"] = "", "", "", 0
        if r["pid"]:
            try:
                raw = open(f"/proc/{r['pid']}/stat").read()
                r["name"] = sysmon.app_name(r["pid"], raw[raw.index("(") + 1:raw.rindex(")")])
                r["cmd"] = open(f"/proc/{r['pid']}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                pass
            r["project"], r["since"] = project_of(r["pid"]), started(r["pid"])
        elif r["container"]:
            r["name"], r["cmd"] = f"docker · {r['container']}", f"docker container {r['container']}"
        r["system"] = not r["pid"] and not r["container"]
    return sorted(rows.values(), key=lambda r: (r["proto"] != "tcp", r["port"]))


def ago(t):
    s = max(time.time() - t, 0)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return f"{int(s)}s"


class Sampler:
    """Takes one reading of everything every INTERVAL seconds, in its own thread."""

    def __init__(self):
        self.cpu, self.mem, self.gpu = (deque(maxlen=HISTORY) for _ in range(3))
        self.want_procs = False   # only read every process while the Processes tab is shown
        self.want_ports = False   # the same for the listening sockets (Ports, and Processes' port tags)
        self.want_apps = False    # and the cgroups, while Apps is shown
        self.usage = None
        self.docker, self.docker_at = {}, 0
        self.t0, self.i0 = sysmon.cpu_ticks()
        self.c0 = core_ticks()
        self.p0, self.d0, self.at = {}, diskstats(), time.time()
        self.drives, self.drives_at = [], 0
        self.snap = None
        self.lock = threading.Lock()

    def sample(self):
        now = time.time()
        t1, i1 = sysmon.cpu_ticks()
        dt = t1 - self.t0
        cpu = (1 - (i1 - self.i0) / dt) * 100 if dt else 0
        mi = meminfo()
        used = mi["MemTotal"] - mi["MemAvailable"]
        g = gpu()
        procs = []
        if self.want_procs:
            p1 = processes()
            for pid, p in p1.items():
                prev = self.p0.get(pid)
                p["pid"] = pid
                p["cpu"] = (p["ticks"] - prev["ticks"]) / dt * 100 if prev and dt else 0.0
                procs.append(p)
            self.p0 = p1
        d1, span = diskstats(), max(now - self.at, 0.1)
        rates = {k: ((v[0] - self.d0.get(k, v)[0]) / span, (v[1] - self.d0.get(k, v)[1]) / span) for k, v in d1.items()}
        # busy: share of the last interval the disk had I/O in flight (Task Manager's "active time")
        busy = {k: min((v[2] - self.d0.get(k, v)[2]) / (span * 1000) * 100, 100) for k, v in d1.items()}
        if now - self.drives_at > 5:
            self.drives, self.drives_at = drives(), now
        ports = []
        if self.want_ports or self.want_procs:
            if now - self.docker_at > 5:   # docker ps is slow-ish: its containers every 5 s
                self.docker, self.docker_at = docker_ports(), now
            ports = listening(self.docker)
            by_pid = {}
            for r in ports:   # the tags show what Ports shows by default (TCP below the random range)
                if r["proto"] != "tcp" or r["port"] >= EPHEMERAL:
                    continue
                for pid in r["pids"]:
                    by_pid.setdefault(pid, []).append(r["port"])
            for p in procs:
                p["ports"] = sorted(set(by_pid.get(p["pid"], [])))
        self.t0, self.i0, self.d0, self.at = t1, i1, d1, now
        self.cpu.append(cpu)
        c1 = core_ticks()
        cores = [(1 - (i1_ - i0_) / (t1_ - t0_)) * 100 if t1_ - t0_ else 0
                 for (t0_, i0_), (t1_, i1_) in zip(self.c0, c1)]
        self.c0 = c1
        self.mem.append(used / mi["MemTotal"] * 100)
        self.gpu.append(num(g["utilization.gpu"]) if g else 0.0)
        one, five, threads = sysmon.load()
        nprocs = sum(1 for p in os.listdir("/proc") if p.isdigit())
        apps = None   # not shown, or the first reading of a run (no CPU use yet: two readings needed)
        if self.want_apps:
            first, self.usage = self.usage is None, self.usage or usage.Sampler()
            rows = self.usage.sample()
            apps = None if first else rows
        else:
            self.usage = None
        snap = dict(cpu=cpu, cores=cores, temp=sysmon.cpu_temp(), mhz=cpu_mhz(), mem=mi, used=used, gpu=g, procs=procs,
                    drives=self.drives, rates=rates, busy=busy, load=(one, five), nprocs=nprocs, threads=threads,
                    ports=ports, apps=apps, new_per_min=self.usage.new_per_min if apps else 0)
        with self.lock:
            self.snap = snap


def graph(values, height=0):
    """An area graph of 0–100 % values, oldest on the left. height 0: fill the space it's given."""
    area = Gtk.DrawingArea(hexpand=True, vexpand=not height, content_height=height or 40)

    def draw(_a, cr, w, h):
        cr.set_source_rgb(*TRACK)
        for frac in (0, 0.5):                    # the bottom line and a faint 50 % line
            cr.rectangle(0, round(h - 1 - frac * (h - 2)), w, 1)
        cr.fill()
        vals = list(values)
        if len(vals) < 2:
            return
        step = w / (len(vals) - 1)              # what's been sampled so far spans the width; it
        x0 = 0                                    # gets finer until it holds the last 90 s
        pts = [(x0 + i * step, h - 1 - min(v, 100) / 100 * (h - 2)) for i, v in enumerate(vals)]
        cr.move_to(x0, h - 1)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(w, h - 1)
        cr.close_path()
        cr.set_source_rgba(*ACCENT, 0.18)
        cr.fill()
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(1.5)
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.stroke()
    area.set_draw_func(draw)
    return area


def usage_bar(pct):
    b = Gtk.LevelBar(min_value=0, max_value=100, hexpand=True, valign=Gtk.Align.CENTER)
    for name in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH, Gtk.LEVEL_BAR_OFFSET_FULL):
        b.remove_offset_value(name)   # one colour, not the theme's low/high/full steps
    b.set_value(min(pct, 100))
    if pct >= HOT:
        b.add_css_class("hot")
    return b


def align_titles(table, rights):
    """Column titles over right-aligned numbers sit at the right too (GTK puts every title at the
    left): the title button's box goes to its end. Its widgets are GTK's own (header > title > box)."""
    title = table.get_first_child().get_first_child()
    for right in rights:
        if title is None:
            break
        inner = title.get_first_child()
        if inner.get_last_child() is not inner.get_first_child():
            inner.get_last_child().set_visible(False)   # the sort icon: hidden by the CSS, it still kept its slot
        if right:
            inner.set_halign(Gtk.Align.END)
        title = title.get_next_sibling()


def inset(table):
    """The table's first and last columns line up with the toolbar above it (20px in: 12 + the cells'
    8px padding)."""
    table.set_margin_start(12)
    table.set_margin_end(12)
    return table


def rate(bps):
    return f"{sysmon.size(bps)}/s" if bps >= 1024 else "0"


class Proc(GObject.Object):
    """One row of the process table (ColumnView needs GObjects). Kept from one refresh to the next:
    `d` is updated in place and "changed" tells the cells on screen to redraw, which is far cheaper
    than replacing every row (that rebuilt and re-sorted ~300 rows per refresh: ~30 % of a core)."""
    __gtype_name__ = "SysguiProc"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, d):
        super().__init__()
        self.d = d


# (title, key, text of a row, sort key, width in chars or 0 = expand, right-aligned, dim)
COLUMNS = [("Name", "name", lambda d: d["name"] + (" · " + " ".join(f":{p}" for p in d["ports"]) if d.get("ports") else ""),
            lambda d: d["name"].lower(), 0, False, False),
           ("PID", "pid", lambda d: str(d["pid"]), lambda d: d["pid"], 7, True, True),
           ("User", "user", lambda d: d["user"], lambda d: d["user"], 9, False, True),
           ("CPU", "cpu", lambda d: f"{d['cpu']:.1f}%", lambda d: d["cpu"], 7, True, False),
           ("Memory", "rss", lambda d: sysmon.size(d["rss"]), lambda d: d["rss"], 9, True, False),
           ("Threads", "threads", lambda d: str(d["threads"]), lambda d: d["threads"], 7, True, True),
           ("Command", "cmd", lambda d: d["cmd"], lambda d: d["cmd"], 0, False, True)]


# the Ports table: (title, text of a row, css class of a row or None, sort key, width in chars or 0, right-aligned)
PORT_COLUMNS = [
    ("Port", lambda d: str(d["port"]) + ("/udp" if d["proto"] == "udp" else ""), lambda d: "bold", lambda d: d["port"], 8, True),
    ("Reachable", lambda d: "this PC" if d["local"] else "network", lambda d: "dim" if d["local"] else "amber",
     lambda d: d["local"], 9, False),
    ("Program", lambda d: d["name"] or "system", lambda d: None if d["name"] else "dim", lambda d: (d["name"] or "~").lower(), 18, False),
    ("PID", lambda d: str(d["pid"] or ""), lambda d: "dim", lambda d: d["pid"] or 0, 7, True),
    ("Project", lambda d: os.path.basename(d["project"]) or "—", lambda d: "accent" if d["project"] else "dim",
     lambda d: d["project"] or "~", 18, False),
    ("Up", lambda d: ago(d["since"]) if d["since"] else "", lambda d: "dim", lambda d: -(d["since"] or 0), 5, True),
    ("Command", lambda d: d["cmd"], lambda d: "dim", lambda d: d["cmd"], 0, False)]


# the Apps table, as PORT_COLUMNS: (title, text of a row, css class of a row or None, sort key, width, right-aligned)
KINDS = {"app": "app", "agent": "agent thread", "service": "service", "container": "container", "system": "system"}
APP_COLUMNS = [
    ("Name", lambda d: d["name"], lambda d: "bold" if d["cpu"] >= 100 else None, lambda d: d["name"].lower(), 0, False),
    ("Kind", lambda d: KINDS[d["kind"]], lambda d: "accent" if d["kind"] == "agent" else "dim", lambda d: d["kind"], 12, False),
    ("CPU", lambda d: f"{d['cpu']:.1f}%", lambda d: "amber" if d["cpu"] >= 100 else None, lambda d: d["cpu"], 8, True),
    ("Memory", lambda d: sysmon.size(d["mem"]), lambda d: None, lambda d: d["mem"], 9, True),
    ("Processes", lambda d: str(d["procs"]), lambda d: "dim", lambda d: d["procs"], 9, True),
    ("Busy with", lambda d: "  ".join(f"{n} {p:.0f}%" for n, p in d["top"]), lambda d: "dim",
     lambda d: d["top"][0][1] if d["top"] else 0, 0, False)]


class System(View):
    title = "System"
    popup = "~/.local/lib/panels/sysgui.py"
    icon = "\U000f035b"   # as the terminal card
    interval = sysmon.INTERVAL
    css = CSS
    start_tab = "Overview"   # main(): --tab Apps (the bar's busy warning)

    def __init__(self):
        super().__init__()
        self.sampler = Sampler()
        self.tab = "Overview"
        self.armed = None   # (action, pid, time) while End / Kill waits for its second click
        self.awake = threading.Event()   # cleared while the Control Center's workspace is hidden
        self.awake.set()
        threading.Thread(target=self.sample_loop, daemon=True).start()

    @property
    def subtitle(self):
        return f"{sysmon.MODEL} · up {sysmon.uptime()}"

    def counts(self, s):
        return f"{self.subtitle} · {s['nprocs']} processes · {s['threads']} threads"

    @property
    def hints(self):
        if self.compact:
            return []
        h = [("1-5", "tabs")]
        if self.tab == "Processes":
            h += [("/", "search"), ("click a column", "sort"), ("Delete", "end process"), ("Enter", "its ports")]
        elif self.tab == "Ports":
            h += [("Enter", "open"), ("Delete", "close the port")]
        elif self.tab == "Apps":
            h += [("click a column", "sort"), ("Enter", "its processes")]
        return h + [("Esc", "close")]

    def sample_loop(self):
        time.sleep(0.4)       # CPU use needs two readings a moment apart
        while not hasattr(self, "blocks"):   # compact or not is only known once build() ran
            time.sleep(0.1)
        while True:
            if self.compact:  # the card reads what the terminal card read (sysmon), in this thread
                if not self.awake.is_set():   # hidden: no process walk, no nvidia-smi
                    self.awake.wait()
                    self.card_stats.tick()    # shown again: two fresh readings a moment apart, not
                    time.sleep(0.4)           # CPU use averaged over the whole time it was hidden
                self.card_stats.tick()
            else:
                self.sampler.sample()
            GLib.idle_add(self.show)
            time.sleep(self.interval)

    def shown_changed(self, shown):
        (self.awake.set if shown else self.awake.clear)()

    @property
    def tile_info(self):
        return f"{sysmon.MODEL} · up {sysmon.uptime()} · {datetime.now():%H:%M}"

    @property
    def tile_footer(self):
        one, five, procs = sysmon.load()
        return recolor(f"<span foreground='#565f89'>load </span><span foreground='#c0caf5'>{one}</span>"
                       f"<span foreground='#565f89'> · {five}   {procs} processes</span>")

    # ---- layout ----------------------------------------------------------------------------------
    def build(self):
        if self.compact:
            return self.build_card()
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self.pages.add_named(self.build_overview(), "Overview")
        self.pages.add_named(self.build_apps(), "Apps")
        self.pages.add_named(self.build_processes(), "Processes")
        self.pages.add_named(scrolled(self.build_storage()), "Storage")
        self.pages.add_named(self.build_ports(), "Ports")
        self.tab_buttons = {}
        bar = box(False, 0, classes=("tabs",))
        for i, t in enumerate(TABS, 1):
            b = button(t, lambda t=t: self.switch(t), "tab", tooltip=str(i))
            self.tab_buttons[t] = b
            bar.append(b)
        self.switch(self.start_tab if self.start_tab in TABS else "Overview")
        return box(True, 0, bar, self.pages)

    def switch(self, tab):
        self.tab = tab
        self.sampler.want_procs = tab == "Processes"
        self.sampler.want_ports = tab == "Ports"
        self.sampler.want_apps = tab == "Apps"
        self.pages.set_visible_child_name(tab)
        for t, b in self.tab_buttons.items():
            (b.add_css_class if t == tab else b.remove_css_class)("on")
        if tab == "Processes":   # the list has the keyboard, so 1-4 and Esc keep working; / searches
            GLib.idle_add(lambda: self.table.grab_focus() and False)
        elif tab == "Ports":
            GLib.idle_add(lambda: self.port_table.grab_focus() and False)
        elif tab == "Apps":
            GLib.idle_add(lambda: self.app_table.grab_focus() and False)
        if self.host:
            self.host.show_hints()

    def build_card(self):
        """The Control Center card, as the terminal one: Resources (CPU, memory, disk, GPU: a bar
        in the level colour, the percentage, details) and the biggest Apps (cpu, a memory bar)."""
        self.card_stats = sysmon.SystemPanel()
        self.blocks = {}   # show() waits for this
        res = Gtk.Grid(column_spacing=12, row_spacing=3)
        self.res_rows = {}
        for i, name in enumerate(("CPU", "Memory", "Disk", "GPU")):
            bar = usage_bar(0)
            bar.add_css_class("thin")
            pct, detail = label("", "bold", xalign=1.0), label("", "dim", xalign=1.0)
            pct.set_width_chars(4)
            detail.set_width_chars(19)
            for c, w in enumerate((label(sysmon.ICONS[name], "accent"), label(name), bar, pct, detail)):
                res.attach(w, c, i, 1, 1)
            self.res_rows[name] = (bar, pct, detail, [res.get_child_at(c, i) for c in range(5)])
        apps = Gtk.Grid(column_spacing=12, row_spacing=3)
        head = rule_heading("Apps")
        head.set_hexpand(True)
        apps.attach(head, 0, 0, 1, 1)
        apps.attach(label("cpu", "dim", xalign=1.0), 1, 0, 1, 1)
        apps.attach(label("memory", "dim", xalign=1.0), 2, 0, 2, 1)
        self.app_rows = []
        for i in range(1, sysmon.TOP_APPS - 1):
            name, cpu, size = label("", ellipsize=True), label("", xalign=1.0), label("", "dim", xalign=1.0)
            cpu.set_width_chars(6)
            size.set_width_chars(8)
            bar = usage_bar(0)
            bar.add_css_class("thin")
            bar.set_hexpand(False)
            bar.set_size_request(96, -1)
            for c, w in enumerate((name, cpu, bar, size)):
                apps.attach(w, c, i, 1, 1)
            self.app_rows.append((name, cpu, bar, size))
        apps.set_margin_top(10)
        return box(True, 6, rule_heading("Resources"), res, apps)

    def paint_card(self):
        cpu, temp, mem, dsk, g, apps = self.card_stats.stats
        t = f"{temp:.0f}°C" if temp else ""
        rows = {"CPU": (cpu, t), "Memory": (mem[0] / mem[1] * 100, f"{sysmon.size(mem[0])} / {sysmon.size(mem[1])}"),
                "Disk": (dsk[0] / dsk[1] * 100, f"{sysmon.size(dsk[0])} / {sysmon.size(dsk[1])}")}
        if g:
            rows["GPU"] = (g[0], f"{g[1]:.0f}°C")
        for name, (bar, pct, detail, widgets) in self.res_rows.items():
            for w in widgets:
                w.set_visible(name in rows)
            if name not in rows:
                continue
            value, text = rows[name]
            bar.set_value(min(value, 100))
            for c in ("green", "yellow", "red"):
                bar.remove_css_class(c)
                pct.remove_css_class(c)
            bar.add_css_class(level(value))
            pct.add_css_class(level(value))
            pct.set_text(f"{value:.0f}%")
            detail.set_text(text)
        biggest = max((m for _, (_, m) in apps[:1]), default=1)
        for (name_l, cpu_l, bar, size_l), (name, (c, m)) in zip(self.app_rows, apps):
            name_l.set_text(name[:1].upper() + name[1:])
            cpu_l.set_text(f"{c:.1f}%")
            for k in ("green", "yellow", "red"):
                cpu_l.remove_css_class(k)
            cpu_l.add_css_class(level(c))
            bar.set_value(max(m / biggest * 100, 2))
            size_l.set_text(sysmon.size(m))

    def build_overview(self):
        """Three equal bands (CPU, memory, GPU) filling the height. Each: a heading with what it
        is; on the left the reading, small bars (each CPU thread; memory and swap; GPU load and
        video memory) and details; on the right the graph, as big as the band."""
        page = box(True, 0, classes=("sys-page",))
        page.set_homogeneous(True)
        self.blocks = {}
        lefts = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # every graph starts at the same x
        for name, values in (("CPU", self.sampler.cpu), ("Memory", self.sampler.mem), ("GPU", self.sampler.gpu)):
            head = rule_heading(name)
            info_label = label("", "dim")   # what it is (model, size), after the rule
            head.append(info_label)
            value = label("", "readout")
            note = label("", "sub", ellipsize=True)
            bars = Gtk.Grid(column_spacing=8, row_spacing=3)
            bars.set_margin_top(6)
            lines = label("", "dim", ellipsize=True)
            lines.set_margin_top(6)
            for w in (note, lines):
                w.set_hexpand(False)   # the graph takes the spare width, not the text
            left = box(True, 0, box(False, 12, value, note), bars, lines)
            left.set_size_request(320, -1)
            left.set_valign(Gtk.Align.CENTER)
            lefts.add_widget(left)
            g = graph(values)
            g.set_margin_top(4)
            band = box(True, 6, head, box(False, 28, left, g))
            band.set_margin_top(4)
            band.set_margin_bottom(10)
            self.blocks[name] = (band, value, note, lines, g, info_label, bars, {})
            page.append(band)
        return page

    def set_bars(self, name, rows):
        """The small bars under a reading: [(label, pct, text)], made once, then updated."""
        *_, bars, made = self.blocks[name]
        for i, (title, pct, text) in enumerate(rows):
            if i not in made:
                bar = usage_bar(0)
                bar.add_css_class("thin")
                bar.set_hexpand(False)
                t, v = label("", "dim"), label("", "dim", xalign=1.0)
                two = len(rows) > 3                     # many bars (CPU threads): two columns
                v.set_width_chars(4 if two else 9)
                bar.set_size_request(100 if two else 140, -1)
                col, row = ((i % 2) * 3, i // 2) if two else (0, i)
                if two and i % 2:
                    t.set_margin_start(20)              # space between the two columns
                for c, w in enumerate((t, bar, v)):
                    bars.attach(w, col + c, row, 1, 1)
                made[i] = (t, bar, v)
            t, bar, v = made[i]
            t.set_text(title)
            bar.set_value(min(pct, 100))
            for c in ("green", "yellow", "red"):
                bar.remove_css_class(c)
            bar.add_css_class(level(pct))
            v.set_text(text)

    def build_processes(self):
        self.store = Gio.ListStore(item_type=Proc)
        self.filter = Gtk.CustomFilter.new(self.matches)
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.table = Gtk.ColumnView(reorderable=False, show_row_separators=False)
        self.columns = {}
        for title, key, text, sort_key, width, right, dim in COLUMNS:
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", lambda _f, item, right=right, dim=dim, width=width:
                            item.set_child(label("", *(["dim"] if dim else []), xalign=1.0 if right else 0.0,
                                                 ellipsize=not width)))
            factory.connect("bind", self.bind_cell, text)
            factory.connect("unbind", self.unbind_cell)
            col = Gtk.ColumnViewColumn(title=title, factory=factory, expand=not width)
            if width:
                col.set_fixed_width(width * 9 + 16)
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _u, k=sort_key: (k(a.d) > k(b.d)) - (k(a.d) < k(b.d))))
            self.table.append_column(col)
            self.columns[key] = col
        self.table.get_sorter().connect("changed", lambda *_: self.mark_sort())
        self.sorted = Gtk.SortListModel(model=filtered, sorter=self.table.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.table.set_model(self.selection)
        self.table.sort_by_column(self.columns["cpu"], Gtk.SortType.DESCENDING)
        align_titles(self.table, [c[5] for c in COLUMNS])
        self.table.connect("activate", lambda *_: self.show_ports_of(self.selected_pid()))   # Enter / double click

        self.search = Gtk.Entry(placeholder_text="Search name, user, command…", hexpand=True)
        self.search.connect("changed", lambda *_: self.filter.changed(Gtk.FilterChange.DIFFERENT))
        esc = Gtk.EventControllerKey()   # Esc in the search: clear it, then give the keyboard back to the list
        esc.connect("key-pressed", self.search_key)
        self.search.add_controller(esc)
        self.count = label("", "dim")
        self.end_btn = button("End process", lambda: self.signal("end"), tooltip="Ask it to quit (Delete); click twice")
        self.kill_btn = button("Kill", lambda: self.signal("kill"), tooltip="Force it to stop; click twice")
        top = box(False, 10, self.search, self.count, self.end_btn, self.kill_btn)
        top.set_margin_top(16)
        top.set_margin_start(20)
        top.set_margin_end(20)
        top.set_margin_bottom(8)
        return box(True, 0, top, scrolled(inset(self.table)))

    def build_ports(self):
        """The listening ports: a table (rows kept from one refresh to the next, as Processes), the
        "UDP and system" switch and the actions on the selected port."""
        self.port_store = Gio.ListStore(item_type=Proc)
        self.port_filter = Gtk.CustomFilter.new(lambda item: self.port_all.get_active() or (
            item.d["proto"] == "tcp" and not item.d["system"] and (item.d["port"] < EPHEMERAL or item.d["container"])))
        filtered = Gtk.FilterListModel(model=self.port_store, filter=self.port_filter)
        self.port_table = Gtk.ColumnView(reorderable=False, show_row_separators=False)
        first = None
        for title, text, cls, sort_key, width, right in PORT_COLUMNS:
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", lambda _f, item, right=right:
                            item.set_child(label("", xalign=1.0 if right else 0.0, ellipsize=not right)))
            factory.connect("bind", self.bind_port_cell, text, cls)
            factory.connect("unbind", self.unbind_cell)
            col = Gtk.ColumnViewColumn(title=title, factory=factory, expand=not width)
            if width:
                col.set_fixed_width(width * 9 + 16)
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _u, k=sort_key: (k(a.d) > k(b.d)) - (k(a.d) < k(b.d))))
            self.port_table.append_column(col)
            first = first or col
        self.port_sorted = Gtk.SortListModel(model=filtered, sorter=self.port_table.get_sorter())
        self.port_sel = Gtk.SingleSelection(model=self.port_sorted, autoselect=True, can_unselect=False)
        self.port_table.set_model(self.port_sel)
        self.port_table.sort_by_column(first, Gtk.SortType.ASCENDING)
        align_titles(self.port_table, [c[5] for c in PORT_COLUMNS])
        self.port_table.connect("activate", lambda *_: self.open_port())
        self.port_sel.connect("notify::selected", lambda *_: self.port_count_text())   # the buttons follow the row

        self.port_all = Gtk.CheckButton(label="Show all", tooltip_text=f"also UDP, the system's, and the random ports "
                                        f"({EPHEMERAL} and up) apps open for themselves")
        self.port_all.connect("toggled", lambda *_: (self.port_filter.changed(Gtk.FilterChange.DIFFERENT),
                                                     self.port_count_text()))
        self.port_count = label("", "dim")
        self.port_count.set_hexpand(True)
        self.open_btn = button("Open", self.open_port, tooltip="http://localhost:<port> in the browser (Enter)")
        self.copy_btn = button("Copy URL", self.copy_port, "flat")
        self.goto_btn = button("Process", self.port_process, "flat", tooltip="the process in Processes")
        self.stop_btn = button("Close port", lambda: self.close_port("end"),
                               tooltip="stop what holds it (Delete; a Docker container: docker stop); click twice")
        self.pkill_btn = button("Kill", lambda: self.close_port("kill"), tooltip="force it to stop; click twice")
        top = box(False, 10, self.port_count, self.port_all, self.open_btn, self.copy_btn, self.goto_btn,
                  self.stop_btn, self.pkill_btn)
        for edge in ("top", "start", "end"):
            getattr(top, f"set_margin_{edge}")(16 if edge == "top" else 20)
        top.set_margin_bottom(8)
        self.port_empty = label("Nothing is listening", "dim", xalign=0.5)
        self.port_empty.set_vexpand(True)
        self.port_pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self.port_pages.add_named(scrolled(inset(self.port_table)), "table")
        self.port_pages.add_named(self.port_empty, "empty")
        return box(True, 0, top, self.port_pages)

    def build_apps(self):
        """CPU by what caused it (usage.py): one row per app, service, container or agentmux thread, kept
        from one refresh to the next as in Processes."""
        self.app_store = Gio.ListStore(item_type=Proc)
        self.app_table = Gtk.ColumnView(reorderable=False, show_row_separators=False)
        cpu_col = None
        for title, text, cls, sort_key, width, right in APP_COLUMNS:
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", lambda _f, item, right=right:
                            item.set_child(label("", xalign=1.0 if right else 0.0, ellipsize=not right)))
            factory.connect("bind", self.bind_port_cell, text, cls)
            factory.connect("unbind", self.unbind_cell)
            col = Gtk.ColumnViewColumn(title=title, factory=factory, expand=not width)
            if width:
                col.set_fixed_width(width * 9 + 16)
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _u, k=sort_key: (k(a.d) > k(b.d)) - (k(a.d) < k(b.d))))
            self.app_table.append_column(col)
            cpu_col = col if title == "CPU" else cpu_col
        self.app_sorted = Gtk.SortListModel(model=self.app_store, sorter=self.app_table.get_sorter())
        self.app_sel = Gtk.SingleSelection(model=self.app_sorted, autoselect=False, can_unselect=True)
        self.app_table.set_model(self.app_sel)
        self.app_table.sort_by_column(cpu_col, Gtk.SortType.DESCENDING)
        align_titles(self.app_table, [c[5] for c in APP_COLUMNS])
        self.app_table.connect("activate", lambda *_: self.app_processes())   # Enter / double click
        self.app_summary = label("Measuring…", "dim")
        self.app_summary.set_hexpand(True)
        top = box(False, 10, self.app_summary)
        for edge in ("top", "start", "end"):
            getattr(top, f"set_margin_{edge}")(16 if edge == "top" else 20)
        top.set_margin_bottom(8)
        return box(True, 0, top, scrolled(inset(self.app_table)))

    def paint_apps(self, s):
        if s["apps"] is None:
            return
        fresh = {r["path"]: r for r in s["apps"] if r["cpu"] >= 0.05 or r["kind"] in ("app", "agent", "container")}
        for i in range(self.app_store.get_n_items() - 1, -1, -1):
            if self.app_store.get_item(i).d["path"] not in fresh:
                self.app_store.remove(i)
        known = set()
        for i in range(self.app_store.get_n_items()):
            row = self.app_store.get_item(i)
            row.d = fresh[row.d["path"]]
            known.add(row.d["path"])
            row.emit("changed")
        new = [Proc(r) for k, r in fresh.items() if k not in known]
        if new:
            self.app_store.splice(self.app_store.get_n_items(), 0, new)
        vadj = self.app_table.get_vadjustment()
        at_top = not vadj or vadj.get_value() < 1
        self.app_table.get_sorter().changed(Gtk.SorterChange.DIFFERENT)
        if at_top and self.app_sorted.get_n_items():   # GTK keeps the top row in view as rows move: the busiest first
            self.app_table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        total = sum(r["cpu"] for r in s["apps"])
        self.app_summary.set_text(f"CPU {total / os.cpu_count():.0f}% · {s['new_per_min']:.0f} new processes a minute · "
                                  f"load {s['load'][0]}   (a row's CPU: 100% is one whole core)")

    def app_processes(self):
        """Enter on a row: its processes (a container: the Docker panel, which knows it better)."""
        pos = self.app_sel.get_selected()
        item = self.app_sorted.get_item(pos) if pos != Gtk.INVALID_LIST_POSITION else None
        if not item:
            return
        d = item.d
        if d["kind"] == "container":
            subprocess.Popen([os.path.expanduser("~/.local/bin/wsgroups"), "go", "9"], start_new_session=True)
            return
        self.switch("Processes")
        self.search.set_text(d["top"][0][0] if d["top"] else d["name"].split(" ")[0])

    def build_storage(self):
        self.storage = box(True, 0, classes=("sys-page",))
        return self.storage

    # ---- painting --------------------------------------------------------------------------------
    def show(self):
        if self.compact:
            if hasattr(self, "app_rows") and self.card_stats.stats:
                self.paint_card()
            return False
        with self.sampler.lock:
            s = self.sampler.snap
        if s is None or not hasattr(self, "blocks"):   # a reading that came before build()
            return False
        self.paint_overview(s)
        if not self.compact:
            if self.tab == "Processes":
                self.paint_processes(s)
            elif self.tab == "Apps":
                self.paint_apps(s)
            elif self.tab == "Ports":
                self.paint_ports(s)
            elif self.tab == "Storage":
                self.paint_storage(s)
        return False

    def paint_overview(self, s):
        mi, used = s["mem"], s["used"]
        temp = f"{s['temp']:.0f}°C · " if s["temp"] else ""
        self.set_block("CPU", s["cpu"], f"{s['cpu']:.0f}%", f"{temp}{s['mhz'] / 1000:.2f} GHz",
                       [f"load {s['load'][0]} · {s['load'][1]}"], f"{temp}{s['mhz'] / 1000:.2f} GHz",
                       f"{sysmon.MODEL} · {os.cpu_count()} threads")
        self.set_bars("CPU", [(f"{i + 1}", c, f"{c:.0f}%") for i, c in enumerate(s["cores"])])

        swap_used = mi["SwapTotal"] - mi["SwapFree"]
        mem_pct = used / mi["MemTotal"] * 100
        swap_pct = swap_used / mi["SwapTotal"] * 100 if mi["SwapTotal"] else 0
        self.set_block("Memory", mem_pct, sysmon.size(used), f"of {sysmon.size(mi['MemTotal'])}",
                       [f"{sysmon.size(mi['MemAvailable'])} available · {sysmon.size(mi['Cached'])} cached"],
                       f"of {sysmon.size(mi['MemTotal'])}", f"{sysmon.size(mi['MemTotal'])} RAM")
        self.set_bars("Memory", [("used", mem_pct, f"{mem_pct:.0f}%"),
                                 ("swap", swap_pct, sysmon.size(swap_used))])

        g = s["gpu"]
        block = self.blocks["GPU"][0]
        block.set_visible(bool(g))
        if g:
            vram_used, vram_total = num(g["memory.used"]), num(g["memory.total"], 1)
            power = (f"{num(g['power.draw']):.0f} of {num(g['power.limit']):.0f} W"
                     if num(g["power.draw"], None) is not None else f"limit {num(g['power.limit']):.0f} W")
            fan = f" · fan {num(g['fan.speed']):.0f}%" if num(g["fan.speed"], None) is not None else ""
            util = num(g["utilization.gpu"])
            self.set_block("GPU", util, f"{util:.0f}%", f"{num(g['temperature.gpu']):.0f}°C · {g['pstate']}", [
                f"core {num(g['clocks.gr']):.0f} MHz · memory {num(g['clocks.mem']):.0f} MHz",
                f"power {power}{fan}"],
                f"{vram_used:.0f} of {vram_total:.0f} MB video memory",
                f"{g['name'].replace('NVIDIA ', '')} · driver {g['driver_version']}")
            self.set_bars("GPU", [("load", util, f"{util:.0f}%"),
                                  ("vram", vram_used / vram_total * 100, f"{vram_used:.0f} MB")])
        self.set_subtitle(self.counts(s))

    def set_block(self, name, pct, reading, note, lines, short, info):
        _, value, note_label, lines_label, g, info_label, *_ = self.blocks[name]
        info_label.set_text(info)
        value.set_text(reading)
        for c in ("green", "yellow", "red"):
            value.remove_css_class(c)
        value.add_css_class(level(pct))
        note_label.set_text(short if self.compact else note)
        lines_label.set_visible(not self.compact)
        lines_label.set_text("\n".join(lines))
        g.queue_draw()

    def matches(self, item):
        words = self.search.get_text().lower().split()
        hay = f"{item.d['name']} {item.d['user']} {item.d['pid']} {item.d['cmd']}".lower()
        return all(w in hay for w in words)

    def mark_sort(self):
        """The sort column's title gets ↓ / ↑ (the theme's arrow icon is hidden)."""
        sorter = self.table.get_sorter()
        primary, order = sorter.get_primary_sort_column(), sorter.get_primary_sort_order()
        for title, key, _t, _k, _w, right, _d in COLUMNS:
            col = self.columns[key]
            arrow = ("↓" if order == Gtk.SortType.DESCENDING else "↑") if col is primary else ""
            # before a right-aligned title, so its last letter stays over the numbers' last digit
            col.set_title((f"{arrow} {title}" if right else f"{title} {arrow}") if arrow else title)

    def selected_pid(self):
        item = self.selection.get_selected_item()
        return item.d["pid"] if item else None

    def bind_cell(self, _factory, item, text):
        """A cell on screen shows its row, and redraws when that row's numbers change."""
        cell, proc = item.get_child(), item.get_item()
        cell.set_text(text(proc.d))
        cell._proc = proc
        cell._changed = proc.connect("changed", lambda p: cell.set_text(text(p.d)))

    def unbind_cell(self, _factory, item):
        cell = item.get_child()
        if getattr(cell, "_proc", None) is not None:
            cell._proc.disconnect(cell._changed)
            cell._proc = None

    def paint_processes(self, s):
        if not s["procs"]:
            return
        fresh = {d["pid"]: d for d in s["procs"]}
        # processes that ended: drop their rows (from the end, so indices stay valid)
        for i in range(self.store.get_n_items() - 1, -1, -1):
            if self.store.get_item(i).d["pid"] not in fresh:
                self.store.remove(i)
        # the rest: new numbers in place, then one redraw signal each (only cells on screen listen)
        known = set()
        for i in range(self.store.get_n_items()):
            proc = self.store.get_item(i)
            proc.d = fresh[proc.d["pid"]]
            known.add(proc.d["pid"])
            proc.emit("changed")
        new = [Proc(d) for pid, d in fresh.items() if pid not in known]
        if new:
            self.store.splice(self.store.get_n_items(), 0, new)
        # CPU / memory moved: re-sort. GTK keeps the row at the top of the view in view while rows
        # move, so the list scrolled along with whatever sank (all start at 0 %). Reading from the
        # top (the usual case): stay at the top, on the busiest processes
        vadj = self.table.get_vadjustment()
        at_top = not vadj or vadj.get_value() < 1
        self.table.get_sorter().changed(Gtk.SorterChange.DIFFERENT)
        if at_top and self.sorted.get_n_items():
            self.table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        shown = self.sorted.get_n_items()
        self.count.set_text(f"{shown} of {len(s['procs'])}" if shown != len(s["procs"]) else f"{shown} processes")

    def bind_port_cell(self, _factory, item, text, cls):
        cell, row = item.get_child(), item.get_item()

        def paint(r):
            cell.set_text(text(r.d))
            for c in ("bold", "dim", "amber", "accent"):
                cell.remove_css_class(c)
            if cls(r.d):
                cell.add_css_class(cls(r.d))
            cell.set_tooltip_text(r.d["project"] or None if text is PORT_COLUMNS[4][1] else None)
        paint(row)
        cell._proc = row
        cell._changed = row.connect("changed", paint)

    def paint_ports(self, s):
        fresh = {(r["proto"], r["port"]): r for r in s["ports"]}
        key = lambda d: (d["proto"], d["port"])
        for i in range(self.port_store.get_n_items() - 1, -1, -1):
            if key(self.port_store.get_item(i).d) not in fresh:
                self.port_store.remove(i)
        known = set()
        for i in range(self.port_store.get_n_items()):
            row = self.port_store.get_item(i)
            row.d = fresh[key(row.d)]
            known.add(key(row.d))
            row.emit("changed")
        new = [Proc(r) for k, r in fresh.items() if k not in known]
        if new:
            self.port_store.splice(self.port_store.get_n_items(), 0, new)
            self.port_table.get_sorter().changed(Gtk.SorterChange.DIFFERENT)
        self.port_count_text()

    def port_count_text(self):
        shown = self.port_sorted.get_n_items()
        self.port_count.set_text(f"{shown} port{'s' * (shown != 1)} listening")
        self.port_pages.set_visible_child_name("table" if shown else "empty")
        d = self.selected_port()
        web = bool(d) and d["proto"] == "tcp"
        for b in (self.open_btn, self.copy_btn):
            b.set_sensitive(web)
        self.goto_btn.set_sensitive(bool(d and d["pid"]))
        for b in (self.stop_btn, self.pkill_btn):
            b.set_sensitive(bool(d and not d["system"]))
        self.pkill_btn.set_visible(not (d and d["container"] and not d["pid"]))

    def paint_storage(self, s):
        """Each drive as a block: its name and type, read / write now, a map of its partitions
        (one bar, each partition's share of the drive, filled as far as it's used), then a line
        per partition. The partition lines share their columns across drives."""
        clear(self.storage)
        cols = [Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL) for _ in range(5)]
        names = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # "Space" / "Activity" labels
        pcts = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)    # the % at the end of those rows
        for i, d in enumerate(s["drives"]):
            parts = []
            for p in d.get("children") or [d]:
                mounts = sorted((m for m in (p.get("mountpoints") or []) if m), key=len)
                used = free = None
                if mounts:
                    st = os.statvfs(mounts[0])
                    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
                    used = total - free
                parts.append((p, mounts, used, free))

            # heading: the drive, and its read / write rates as two small readouts
            name = label((d.get("model") or d["name"]).strip(), "heading", ellipsize=True)
            kind = "HDD" if d.get("rota") else "SSD"
            title = box(True, 2, name, label(f"{kind} · {sysmon.size(d['size'])} · {d['name']}", "dim"))
            title.set_hexpand(True)
            rd, wr = s["rates"].get(d["kname"], (0, 0))
            rates = box(False, 28)
            for what, value in (("Read", rd), ("Write", wr)):
                r = box(True, 0, label(what, "section", "flush", xalign=1.0),
                        label(rate(value) if value >= 1024 else "idle", "readout-small" if value >= 1024 else "dim",
                              xalign=1.0))
                r.set_size_request(110, -1)
                rates.append(r)
            block = box(True, 0, box(False, 16, title, rates))
            if i:
                block.add_css_class("hairline")
                block.set_margin_top(18)
                title.set_margin_top(18)
                rates.set_margin_top(18)

            # two bars, coloured by level (green, amber from 60 %, red from 85 %): how full the drive
            # is (a segment per partition), and how busy it is right now
            mounted = [(used, free) for _, _, used, free in parts if used is not None]
            full = sum(u for u, _ in mounted) / sum(u + f for u, f in mounted) * 100 if mounted else 0
            busy = s["busy"].get(d["kname"], 0)
            activity = usage_bar(0)
            activity.set_value(busy)
            activity.add_css_class(level(busy))
            for i_row, (what, bar, pct, note) in enumerate((
                    ("Space", self.drive_map(d["size"], [(p["size"], used) for p, _, used, _ in parts]),
                     full, "full"),
                    ("Activity", activity, busy, "busy"))):
                name_l = label(what, "dim", xalign=0.0)
                if note == "full" and not mounted:
                    pct_l = label("not mounted", "dim", xalign=1.0)   # no usage to show (e.g. Windows)
                elif note == "busy" and pct < 1:
                    pct_l = label("idle", "dim", xalign=1.0)
                else:
                    pct_l = label(f"{pct:.0f}% {note}", level(pct), xalign=1.0)
                names.add_widget(name_l)
                pcts.add_widget(pct_l)
                row = box(False, 14, name_l, bar, pct_l)
                row.set_margin_top(12 if i_row == 0 else 6)
                block.append(row)
            block.get_last_child().set_margin_bottom(8)

            # one line per partition
            for p, mounts, used, free in parts:
                part = label(p["name"] + (f"  {p['label']}" if p.get("label") else ""))
                fs = label(p.get("fstype") or "—", "dim")
                if mounts:
                    pct = used / (used + free) * 100 if used + free else 0
                    where = label(", ".join(mounts[:3]) + (f"  +{len(mounts) - 3}" if len(mounts) > 3 else ""),
                                  "sub", ellipsize=True)
                    amounts = [label(f"{sysmon.size(used)} used", xalign=1.0),
                               label(f"{sysmon.size(free)} free", "sub", xalign=1.0),
                               label(f"{pct:.0f}%", level(pct), xalign=1.0)]
                else:
                    where = label("not mounted", "dim")
                    amounts = [label("", xalign=1.0), label(sysmon.size(p["size"]), "dim", xalign=1.0),
                               label("", xalign=1.0)]
                for c, w in enumerate((part, fs)):
                    cols[c].add_widget(w)
                for c, w in enumerate(amounts):
                    cols[c + 2].add_widget(w)
                where.set_hexpand(True)
                if not mounts:
                    part.add_css_class("dim")
                line = box(False, 18, part, fs, where, *amounts)
                line.set_margin_top(4)
                block.append(line)
            self.storage.append(block)

    def drive_map(self, size, parts):
        """One bar for the drive: a segment per partition, as wide as its share of the drive,
        filled as far as it's used. Unmounted partitions are faint, empty segments."""
        area = Gtk.DrawingArea(hexpand=True, content_height=6, valign=Gtk.Align.CENTER)

        def draw(_a, cr, w, h):
            x, gap = 0.0, 3
            for psize, used in parts:
                seg = max(w * psize / size - gap, 2)
                if used is None:
                    cr.set_source_rgba(*TRACK, 0.5)      # not mounted: a dim, empty segment
                    cr.rectangle(x, 0, seg, h)
                    cr.fill()
                else:
                    cr.set_source_rgb(*TRACK)
                    cr.rectangle(x, 0, seg, h)
                    cr.fill()
                    frac = used / psize if psize else 0
                    cr.set_source_rgb(*LEVEL_RGB[level(frac * 100)])   # fuller = amber, then red
                    cr.rectangle(x, 0, max(seg * frac, 1), h)
                    cr.fill()
                x += seg + gap
        area.set_draw_func(draw)
        return area

    # ---- actions ---------------------------------------------------------------------------------
    def signal(self, how):
        pid = self.selected_pid()
        if pid is None:
            self.say("Select a process first", "bad")
            return
        btn = self.end_btn if how == "end" else self.kill_btn
        if not (self.armed and self.armed[:2] == (how, pid) and time.time() - self.armed[2] < 3):
            self.disarm()
            self.armed = (how, pid, time.time())
            btn.add_css_class("armed")
            btn.set_label("Click again")
            GLib.timeout_add(3000, self.disarm)
            return
        self.disarm()
        item = self.selection.get_selected_item()
        try:
            os.kill(pid, signal.SIGTERM if how == "end" else signal.SIGKILL)
            self.say(f"{'Asked' if how == 'end' else 'Forced'} {item.d['name']} ({pid}) to stop")
        except ProcessLookupError:
            self.say("That process has already stopped")
        except PermissionError:
            self.say(f"{item.d['name']} belongs to {item.d['user']}: not allowed", "bad")

    def disarm(self):
        self.armed = None
        if not self.compact:
            for b, text in ((self.end_btn, "End process"), (self.kill_btn, "Kill"),
                            (self.stop_btn, "Close port"), (self.pkill_btn, "Kill")):
                b.set_label(text)
                b.remove_css_class("armed")
        return False

    # ---- the Ports tab's actions -----------------------------------------------------------------
    def selected_port(self):
        item = self.port_sel.get_selected_item() if hasattr(self, "port_sel") else None
        return item.d if item else None

    def open_port(self):
        d = self.selected_port()
        if d and d["proto"] == "tcp":
            subprocess.Popen(["xdg-open", f"http://localhost:{d['port']}"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def copy_port(self):
        d = self.selected_port()
        if d:
            Gdk.Display.get_default().get_clipboard().set(f"http://localhost:{d['port']}")
            self.say(f"Copied http://localhost:{d['port']}")

    def port_process(self):
        """The process holding the port, in Processes (searched by its PID)."""
        d = self.selected_port()
        if d and d["pid"]:
            self.switch("Processes")
            self.search.set_text(str(d["pid"]))

    def show_ports_of(self, pid):
        """Enter on a process: its ports, if it has any."""
        item = self.selection.get_selected_item()
        if pid is None or not item or not item.d.get("ports"):
            return
        self.switch("Ports")
        for i in range(self.port_sorted.get_n_items()):
            if self.port_sorted.get_item(i).d["pid"] == pid:
                self.port_sel.set_selected(i)
                self.port_table.scroll_to(i, None, Gtk.ListScrollFlags.FOCUS, None)
                break

    def close_port(self, how):
        """Close the selected port: stop what holds it (every process listed on it; a Docker
        container with docker stop). Click twice, as End process."""
        d = self.selected_port()
        if not d or d["system"]:
            self.say("That port belongs to the system: not closed here", "bad")
            return
        btn = self.stop_btn if how == "end" else self.pkill_btn
        what = ("port", how, d["proto"], d["port"])
        if not (self.armed and self.armed[:4] == what and time.time() - self.armed[4] < 3):
            self.disarm()
            self.armed = (*what, time.time())
            btn.add_css_class("armed")
            btn.set_label("Click again")
            GLib.timeout_add(3000, self.disarm)
            return
        self.disarm()
        if d["container"] and not d["pid"]:
            subprocess.Popen(["docker", "stop", d["container"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"Stopping the {d['container']} container (port {d['port']})")
            return
        done, denied = 0, 0
        for pid in d["pids"]:
            try:
                os.kill(pid, signal.SIGTERM if how == "end" else signal.SIGKILL)
                done += 1
            except ProcessLookupError:
                pass
            except PermissionError:
                denied += 1
        if denied and not done:
            self.say(f"{d['name']} isn't yours: not allowed", "bad")
        else:
            self.say(f"{'Asked' if how == 'end' else 'Forced'} {d['name']} to stop: port {d['port']} closes with it")

    def search_key(self, _ctrl, keyval, _code, _state):
        if keyval != Gdk.KEY_Escape:
            return False
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.table.grab_focus()
        return True

    def key(self, keyval, state):
        if self.compact or self.typing():
            return False
        name = Gdk.keyval_name(keyval) or ""
        if name in ("1", "2", "3", "4", "5"):
            self.switch(TABS[int(name) - 1])
            return True
        if name == "slash":
            self.switch("Processes")
            GLib.idle_add(lambda: self.search.grab_focus() and False)
            return True
        if name == "Delete" and self.tab == "Processes":
            self.signal("end")
            return True
        if name == "Delete" and self.tab == "Ports":
            self.close_port("end")
            return True
        return False


def make():
    """The view, for run() here and for the Control Center."""
    return System()


def main():
    view = make()
    if "--tab" in sys.argv[1:-1]:
        view.start_tab = sys.argv[sys.argv.index("--tab") + 1]
    run(view, "panels.system", (980, 680))


if __name__ == "__main__":
    main()
