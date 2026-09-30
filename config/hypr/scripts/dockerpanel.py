#!/usr/bin/env python3
# dockerpanel.py — Docker panel (Tokyo Night). The app on workspace 9 (config/hypr/workspaces.conf).
# Four tabs, each a list on the left and the selected item's details on the right:
#   Containers  grouped by compose project; live CPU, memory, ports, logs
#   Images      in use / unused; size, age, which containers use it, layers
#   Volumes     grouped by compose project; size, mountpoint, which containers use it
#   Networks    driver, subnet, gateway, the containers on it and their IPs
# lazydocker is one key away (L) for anything this doesn't do.
#
#   1–4 / Tab / click  switch tab     ↑↓ / click  select     d d  remove (press twice)
#   containers: s start/stop  r restart  p project start/stop  l logs  e shell  o open port
#   images: u pull (update)            L lazydocker            q close
import json, os, re, signal, subprocess, threading, time
from itertools import zip_longest

from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, TRACK, GREEN, YELLOW, RED, CYAN,
                      BOLD, RESET, clip, fit, frame, card, header, section, hints, highlight, visible_len)

HOME = os.path.expanduser("~")
FORMAT = ('{{json .}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
          '\t{{.Label "com.docker.compose.project.working_dir"}}')
PORT = re.compile(r"(?:[\d.]+|\[[^\]]*\]):(\d+(?:-\d+)?)->(\d+(?:-\d+)?)/(\w+)")
ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|.)")
TABS = ["containers", "images", "volumes", "networks"]
BUILTIN_NETWORKS = {"bridge", "host", "none"}


def docker(*args, timeout=15):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def level(p): return GREEN if p < 60 else YELLOW if p < 85 else RED


def bar(p, width):
    n = round(width * min(p, 100) / 100)
    return level(p) + "━" * n + TRACK + "━" * (width - n) + RESET


def short_size(s):
    """'768.4MiB / 15.51GiB' -> '768M', '1.527MiB / …' -> '1.5M', '51.5MB' -> '52M'"""
    m = re.match(r"([\d.]+)\s*([kKMGT]?)i?B", s.strip())
    if not m:
        return s.split("/")[0].strip()
    n, unit = float(m.group(1)), m.group(2).upper() or "B"
    return f"{n:.1f}{unit}" if n < 10 else f"{n:.0f}{unit}"


def wrap(line, w):
    """A long line as several rows, continuations indented."""
    rows, line = [line[:w]], line[w:]
    while line and w > 2:
        rows.append("  " + line[:w - 2])
        line = line[w - 2:]
    return rows


def percent(s):
    try:
        return float(s.rstrip("%"))
    except ValueError:
        return 0.0


def ports(text):
    """Published ports as [(host, container, proto)], IPv4/IPv6 duplicates merged."""
    out = []
    for p in PORT.findall(text or ""):
        if p not in out:
            out.append(p)
    return out


def label(labels, key):
    """One value out of docker's 'k=v,k=v' label string."""
    m = re.search(r"(?:^|,)" + re.escape(key) + r"=([^,]*)", labels or "")
    return m.group(1) if m else ""


def status(c):
    """(colour, dot, words) for a container."""
    state, health = c["State"], c.get("HealthStatus", "none")
    if state == "running":
        if health == "unhealthy": return RED, "●", "unhealthy"
        if health == "starting": return YELLOW, "◐", "starting"
        return GREEN, "●", "healthy" if health == "healthy" else "running"
    if state == "restarting": return YELLOW, "↻", "restarting"
    if state == "paused": return DIM, "‖", "paused"
    code = re.search(r"Exited \((\d+)\)", c["Status"])
    if code and code.group(1) != "0": return RED, "○", f"exited ({code.group(1)})"
    return DIM, "○", state


def kv_rows(pairs, w, key_w=11):
    return [f"{DIM}{k:<{key_w}}{RESET}{FG}{clip(v, w - key_w)}{RESET}" for k, v in pairs]


class DockerPanel(Panel):
    interval = 1.0

    def __init__(self):
        self.tab = "containers"
        self.items, self.error = [], ""                 # containers
        self.images, self.volumes, self.networks = [], [], []
        self.sel = {t: None for t in TABS}              # selected ID per tab (survives refreshes)
        self.scroll = {t: 0 for t in TABS}
        self.stats = {}
        self.logs, self.logs_for = [], None
        self.history = {}                               # image ID -> [(size, command)]
        self.busy = {}                                  # ID -> "removing…" while an action runs
        self.armed = None                               # (tab, ID, time): d was pressed once
        self.flash, self.flash_at = "", 0
        self.rows, self.left_w, self.tab_spans = {}, 0, []  # for clicks
        self.refresh = threading.Event()
        self.tick()
        for watcher in (self.watch_stats, self.watch_resources, self.watch_detail):
            threading.Thread(target=watcher, daemon=True).start()

    # ---- data ------------------------------------------------------------------------------------
    def tick(self):
        try:
            r = docker("ps", "-a", "--format", FORMAT, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.items, self.error = [], str(e)
            return
        if r.returncode:
            self.items, self.error = [], r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "docker failed"
            return
        items = []
        for line in r.stdout.splitlines():
            raw, project, service, workdir = (line.split("\t") + ["", "", ""])[:4]
            c = json.loads(raw)
            c.update(project=project, service=service, workdir=workdir, short=self.short(c["Names"], project))
            items.append(c)
        # compose projects A–Z, containers not in a project last
        items.sort(key=lambda c: (not c["project"], c["project"], c["short"]))
        self.items, self.error = items, ""
        self.keep_selection("containers", [c["ID"] for c in items])

    def keep_selection(self, tab, ids):
        if self.sel[tab] not in ids:
            self.sel[tab] = ids[0] if ids else None

    def watch_stats(self):
        while True:
            try:
                r = docker("stats", "--no-stream", "--format", "{{json .}}")
                self.stats = {s["ID"]: s for s in map(json.loads, r.stdout.splitlines())}
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            time.sleep(2)

    def watch_resources(self):
        """Images, volumes and networks, with what uses them. Every 3 s, or sooner after an action."""
        while True:
            try:
                self.load_resources()
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
                pass
            self.refresh.wait(3)
            self.refresh.clear()

    def load_resources(self):
        df = json.loads(docker("system", "df", "-v", "--format", "{{json .}}").stdout)
        users = []  # (container name, image, [volume names])
        for line in docker("ps", "-a", "--no-trunc", "--format", "{{.Names}}\t{{.Image}}\t{{.Mounts}}").stdout.splitlines():
            name, image, mounts = (line.split("\t") + ["", ""])[:3]
            users.append((name, image, [m for m in mounts.split(",") if m]))

        images = []
        for i in df.get("Images") or []:
            iid = i["ID"].removeprefix("sha256:")[:12]
            repo, tag = i["Repository"], i["Tag"]
            ref = f"{repo}:{tag}"
            used = [n for n, img, _ in users
                    if img in (ref, iid, i["ID"]) or (tag == "latest" and img == repo)]
            images.append(dict(ID=iid, repo=repo, tag=tag, ref=ref, size=i["Size"], created=i["CreatedSince"],
                               used=used, dangling=repo == "<none>"))
        images.sort(key=lambda i: (not i["used"], i["dangling"], i["ref"]))

        volumes = []
        for v in df.get("Volumes") or []:
            project = label(v.get("Labels"), "com.docker.compose.project")
            name = v["Name"]
            short = name[len(project) + 1:] if project and name.startswith(project + "_") else name
            if re.fullmatch(r"[0-9a-f]{64}", name):
                short = f"anonymous {name[:12]}"
            volumes.append(dict(ID=name, short=short, project=project, driver=v["Driver"], size=v["Size"],
                                mount=v["Mountpoint"], used=[n for n, _, m in users if name in m]))
        volumes.sort(key=lambda v: (not v["project"], v["project"], v["short"]))

        networks = []
        ids = docker("network", "ls", "-q").stdout.split()
        for n in json.loads(docker("network", "inspect", *ids).stdout) if ids else []:
            cfg = (n.get("IPAM", {}).get("Config") or [{}])[0]
            members = sorted((c.get("Name", "?"), c.get("IPv4Address", "").split("/")[0])
                             for c in (n.get("Containers") or {}).values())
            networks.append(dict(ID=n["Id"][:12], name=n["Name"], driver=n["Driver"], scope=n["Scope"],
                                 subnet=cfg.get("Subnet", ""), gateway=cfg.get("Gateway", ""),
                                 internal=n.get("Internal", False),
                                 project=(n.get("Labels") or {}).get("com.docker.compose.project", ""),
                                 members=members, created=n.get("Created", "")[:19].replace("T", " ")))
        networks.sort(key=lambda n: (n["name"] not in BUILTIN_NETWORKS, n["name"]))

        self.images, self.volumes, self.networks = images, volumes, networks
        self.keep_selection("images", [i["ID"] for i in images])
        self.keep_selection("volumes", [v["ID"] for v in volumes])
        self.keep_selection("networks", [n["ID"] for n in networks])

    def watch_detail(self):
        """The slow part of the right-hand side: the selected container's logs, an image's layers."""
        while True:
            tab, sid = self.tab, self.sel[self.tab]
            try:
                if tab == "containers" and sid:
                    r = subprocess.run(["docker", "logs", "--tail", "200", sid], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, timeout=10)
                    text = ANSI.sub("", r.stdout.decode(errors="replace"))
                    lines = [l.split("\r")[-1].expandtabs(4) for l in text.splitlines()]
                    self.logs = [l for l in lines if l.strip()] or ["(no output yet)"]
                    self.logs_for = sid
                elif tab == "images" and sid and sid not in self.history:
                    r = docker("history", "--no-trunc", "--format", "{{.Size}}\t{{.CreatedBy}}", sid)
                    layers = []
                    for line in r.stdout.splitlines():
                        size, cmd = (line.split("\t", 1) + [""])[:2]
                        cmd = re.sub(r"^/bin/sh -c (#\(nop\)\s*)?", "", cmd).replace("|", " ")
                        layers.append((size, " ".join(cmd.split())))
                    self.history[sid] = layers
            except (OSError, subprocess.TimeoutExpired):
                pass
            # wake early when the tab or selection changes
            for _ in range(20):
                time.sleep(0.1)
                if (self.tab, self.sel[self.tab]) != (tab, sid):
                    break

    def entries(self, tab=None):
        return {"containers": self.items, "images": self.images, "volumes": self.volumes,
                "networks": self.networks}[tab or self.tab]

    def current(self):
        return next((e for e in self.entries() if e["ID"] == self.sel[self.tab]), None)

    # ---- drawing: the list -----------------------------------------------------------------------
    def short(self, name, project):
        """A compose container's name without the project prefix and the -1 suffix."""
        if project and name.startswith(project + "-"):
            name = name[len(project) + 1:]
            name = name[:-2] if name.endswith("-1") else name
        return name

    def row_name(self, color, dot, name, w, bright=True):
        return f"{color}{dot}{RESET} {FG if bright else DIM}{fit(name, w):<{w}}{RESET}"

    def container_row(self, c, w):
        color, dot, words = status(c)
        name = self.row_name(color, dot, c["short"], max(w - 36, 4), c["State"] == "running")
        if c["ID"] in self.busy:
            return f"{name}  {CYAN}{self.busy[c['ID']]}{RESET}"
        if c["State"] != "running":
            when = re.sub(r"^Exited \(\d+\) ", "", c["Status"])
            return f"{name}  {DIM}{fit(words + ' · ' + when, 33)}{RESET}"
        s = self.stats.get(c["ID"])
        cpu = percent(s["CPUPerc"]) if s else 0.0
        mem = short_size(s["MemUsage"]) if s else "…"
        pp = ports(c["Ports"])
        port = (pp[0][0] + ("+" if len(pp) > 1 else "")) if pp else ""
        return (f"{name}  {bar(cpu, 8)} {FG}{cpu:>5.1f}%{RESET}  {FG}{mem:>6}{RESET}  "
                f"{ACCENT}{port:>7}{RESET}")

    def image_row(self, i, w):
        color, dot = (GREEN, "●") if i["used"] else (YELLOW, "◌") if i["dangling"] else (DIM, "○")
        name = i["ref"] if not i["dangling"] else f"<none> {i['ID']}"
        tail = f"{CYAN}{self.busy[i['ID']]}{RESET}" if i["ID"] in self.busy else \
            f"{FG}{short_size(i['size']):>6}{RESET}  {DIM}{fit(i['created'], 13):>13}{RESET}"
        return f"{self.row_name(color, dot, name, max(w - 27, 4), bool(i['used']))}  {tail}"

    def volume_row(self, v, w):
        color, dot = (GREEN, "●") if v["used"] else (DIM, "○")
        tail = f"{CYAN}{self.busy[v['ID']]}{RESET}" if v["ID"] in self.busy else \
            f"{FG}{short_size(v['size']):>6}{RESET}  {DIM}{fit(', '.join(self.short(n, v['project']) for n in v['used']) or 'unused', 16):<16}{RESET}"
        return f"{self.row_name(color, dot, v['short'], max(w - 27, 4), bool(v['used']))}  {tail}"

    def network_row(self, n, w):
        color, dot = (GREEN, "●") if n["members"] else (DIM, "○")
        tail = f"{CYAN}{self.busy[n['ID']]}{RESET}" if n["ID"] in self.busy else \
            f"{DIM}{n['driver']:<8}{RESET} {FG}{fit(n['subnet'], 16):<16}{RESET} {ACCENT}{len(n['members']):>3}{RESET}"
        return f"{self.row_name(color, dot, n['name'], max(w - 32, 4), bool(n['members']))}  {tail}"

    def groups(self):
        """[(section title, right-hand note, [entries])] for the current tab."""
        e = self.entries()
        if self.tab == "containers":
            out = []
            for p in dict.fromkeys(c["project"] for c in e):
                g = [c for c in e if c["project"] == p]
                out.append((p or "standalone", f"{sum(c['State'] == 'running' for c in g)}/{len(g)} up", g))
            return out
        if self.tab == "images":
            used = [i for i in e if i["used"]]
            unused = [i for i in e if not i["used"]]
            return [g for g in [("in use", f"{len(used)}", used), ("unused", f"{len(unused)}", unused)] if g[2]]
        if self.tab == "volumes":
            out = []
            for p in dict.fromkeys(v["project"] for v in e):
                g = [v for v in e if v["project"] == p]
                out.append((p or "other", f"{sum(bool(v['used']) for v in g)}/{len(g)} in use", g))
            return out
        builtin = [n for n in e if n["name"] in BUILTIN_NETWORKS]
        custom = [n for n in e if n["name"] not in BUILTIN_NETWORKS]
        return [g for g in [("created", f"{len(custom)}", custom), ("built in", f"{len(builtin)}", builtin)] if g[2]]

    def left(self, w, top_row, room):
        draw_row = {"containers": self.container_row, "images": self.image_row,
                    "volumes": self.volume_row, "networks": self.network_row}[self.tab]
        sel = self.sel[self.tab]
        lines, ids = [], []   # ids[i]: the entry on lines[i], or None
        for title, note, group in self.groups():
            if lines:
                lines.append(""); ids.append(None)
            lines.append(section(title, w, note)); ids.append(None)
            for e in group:
                row = draw_row(e, w)
                lines.append(highlight(row, w) if e["ID"] == sel else row)
                ids.append(e["ID"])
        if not lines:
            lines = [f"{RED}{self.error}{RESET}" if self.error else f"{DIM}nothing here{RESET}"]
            ids = [None]
        # keep the selection on screen
        at = ids.index(sel) if sel in ids else 0
        top = self.scroll[self.tab]
        if at < top + 1:
            top = max(at - 1, 0)
        elif at >= top + room:
            top = at - room + 1
        top = self.scroll[self.tab] = max(min(top, len(lines) - room), 0)
        lines, ids = lines[top:top + room], ids[top:top + room]
        self.rows = {top_row + i: eid for i, eid in enumerate(ids) if eid}
        return lines

    # ---- drawing: the details --------------------------------------------------------------------
    def right(self, w, room):
        e = self.current()
        if not e:
            return []
        return {"containers": self.container_detail, "images": self.image_detail,
                "volumes": self.volume_detail, "networks": self.network_detail}[self.tab](e, w, room)

    def fill(self, rows, lines, w, room, empty):
        """Append wrapped lines, keeping the last ones that fit."""
        n = max(room - len(rows), 0)
        tail = [r for l in lines[-n:] for r in wrap(l, w)][-n:] if n else []
        return rows + ([f"{FG}{clip(r, w)}{RESET}" for r in tail] or [f"{DIM}{empty}{RESET}"])

    def container_detail(self, c, w, room):
        color, dot, words = status(c)
        s = self.stats.get(c["ID"]) if c["State"] == "running" else None
        rows = [section(c["Names"], w, f"{color}{dot} {words}{RESET}")]
        kv = [("Image", c["Image"])]
        if c["State"] == "running":
            kv.append(("Up", re.sub(r"^Up |\s*\(.*\)$", "", c["Status"])))
        else:
            kv.append(("Status", c["Status"]))
        pp = ports(c["Ports"])
        if pp:
            kv.append(("Ports", "  ".join(f"{h}→{p}" + ("" if t == "tcp" else f"/{t}") for h, p, t in pp)))
        if s:
            cpu, mem = percent(s["CPUPerc"]), percent(s["MemPerc"])
            kv.append(("CPU", f"{bar(cpu, 12)} {FG}{cpu:.1f}%{RESET}"))
            kv.append(("Memory", f"{bar(mem, 12)} {FG}{s['MemUsage'].replace(' / ', ' of ')}{RESET}"))
            kv.append(("Net I/O", s["NetIO"].replace(" / ", " in · ") + " out"))
        if c["workdir"]:
            kv.append(("Project", c["workdir"].replace(HOME, "~")))
        rows += kv_rows(kv, w) + ["", section("Logs", w, "l opens all")]
        logs = self.logs if self.logs_for == c["ID"] else ["loading…"]
        return self.fill(rows, logs, w, room, "no output")

    def image_detail(self, i, w, room):
        state = f"{GREEN}● in use{RESET}" if i["used"] else f"{YELLOW}◌ dangling{RESET}" if i["dangling"] \
            else f"{DIM}○ unused{RESET}"
        rows = [section(i["ref"], w, state)]
        rows += kv_rows([("ID", i["ID"]), ("Size", i["size"]), ("Created", i["created"]),
                         ("Used by", ", ".join(i["used"]) or "no containers")], w)
        rows += ["", section("Layers", w, "size  command")]
        layers = self.history.get(i["ID"])
        if layers is None:
            return rows + [f"{DIM}loading…{RESET}"]
        n = max(room - len(rows), 0)
        for size, cmd in layers[:n]:
            rows.append(f"{FG}{short_size(size) if size != '0B' else '·':>6}{RESET}  {DIM}{fit(cmd, w - 8)}{RESET}")
        return rows

    def volume_detail(self, v, w, room):
        state = f"{GREEN}● in use{RESET}" if v["used"] else f"{DIM}○ unused{RESET}"
        rows = [section(v["short"], w, state)]
        rows += kv_rows([("Name", v["ID"]), ("Size", v["size"]), ("Driver", v["driver"]),
                         ("Project", v["project"] or "—"), ("Mountpoint", v["mount"])], w)
        rows += ["", section("Used by", w, f"{len(v['used'])}")]
        return rows + ([f"{GREEN}●{RESET} {FG}{fit(n, w - 2)}{RESET}" for n in v["used"]]
                       or [f"{DIM}no containers — d d removes it{RESET}"])

    def network_detail(self, n, w, room):
        rows = [section(n["name"], w, f"{n['driver']} · {n['scope']}")]
        rows += kv_rows([("ID", n["ID"]), ("Subnet", n["subnet"] or "—"), ("Gateway", n["gateway"] or "—"),
                         ("Internal", "yes" if n["internal"] else "no"), ("Project", n["project"] or "—"),
                         ("Created", n["created"])], w)
        rows += ["", section("Containers", w, f"{len(n['members'])}")]
        return rows + ([f"{GREEN}●{RESET} {FG}{fit(name, w - 20):<{w - 20}}{RESET}{ACCENT}{ip:>18}{RESET}"
                        for name, ip in n["members"]] or [f"{DIM}none attached{RESET}"])

    # ---- drawing: the card -----------------------------------------------------------------------
    def tab_bar(self, w, pad):
        parts, spans, x = [], [], len(pad)
        for i, t in enumerate(TABS):
            text = f" {i + 1} {t.capitalize()} {len(self.entries(t))} "
            style = f"{ACCENT}{BOLD}" if t == self.tab else DIM
            parts.append(f"{style}{text}{RESET}")
            spans.append((x, x + len(text), t))
            x += len(text) + 2
        self.tab_spans = spans
        underline = "".join((f"{ACCENT}{'━' * (e - s)}{RESET}" if t == self.tab else " " * (e - s)) + "  "
                            for s, e, t in spans)
        return ["  ".join(parts), underline]

    def draw(self, w, h):
        cols, rows, width, pad = frame(200)
        room = max(rows - 5 - 3, 0)   # card chrome, then the tab bar (2 rows) and a blank row
        gap = 4
        lw = min((width - gap) * 9 // 20, 58)
        rw = width - gap - lw
        self.left_w = len(pad) + lw
        body_top = 3 + 3              # header (2) + blank, tab bar (2) + blank
        L, R = self.left(lw, body_top, room), self.right(rw, room)
        body = self.tab_bar(width, pad) + [""]
        body += [clip(l or "", lw) + " " * gap + (r or "") for l, r in zip_longest(L, R)]
        up = sum(c["State"] == "running" for c in self.items)
        sub = f"{up} running · {len(self.items) - up} stopped"
        head = header("", "Docker", sub, width)
        if self.flash and time.time() - self.flash_at < 4:
            foot = self.flash
        else:
            keys = {"containers": [("s", "start/stop"), ("r", "restart"), ("p", "project"), ("l", "logs"),
                                   ("e", "shell"), ("o", "open port")],
                    "images": [("u", "pull"), ("d d", "remove")],
                    "volumes": [("d d", "remove")],
                    "networks": [("d d", "remove")]}[self.tab]
            foot = hints([("1-4", "tabs"), ("↑↓", "select")] + keys + [("L", "lazydocker"), ("q", "close")], width)
        return card(head, body, foot, rows, pad)

    # ---- actions ---------------------------------------------------------------------------------
    def say(self, msg, color=ACCENT):
        self.flash, self.flash_at = f"{color}{msg}{RESET}", time.time()

    def act(self, ids, doing, args, done):
        """Run a docker command in the background; the rows show `doing` until it finishes."""
        def work():
            for i in ids:
                self.busy[i] = doing
            try:
                r = docker(*args, timeout=300)
                if r.returncode:
                    self.say(fit(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed", 150), RED)
                else:
                    self.say(done)
            except (OSError, subprocess.TimeoutExpired) as e:
                self.say(str(e), RED)
            finally:
                for i in ids:
                    self.busy.pop(i, None)
                self.refresh.set()
        threading.Thread(target=work, daemon=True).start()

    def external(self, argv):
        """A full-screen program in place of the panel. Ctrl+C belongs to it, not to us."""
        signal.signal(signal.SIGINT, lambda *_: None)  # reset to default in the child by exec
        try:
            suspend(argv)
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)

    def move(self, step):
        ids = [e["ID"] for _, _, g in self.groups() for e in g]  # in on-screen order
        if ids:
            i = ids.index(self.sel[self.tab]) if self.sel[self.tab] in ids else 0
            self.sel[self.tab] = ids[max(0, min(len(ids) - 1, i + step))]

    def remove(self, e):
        """d twice: remove the selected image, volume or network (docker refuses if it's in use)."""
        if self.tab == "containers":
            return
        name = {"images": e.get("ref"), "volumes": e.get("short"), "networks": e.get("name")}[self.tab]
        if self.tab == "networks" and e["name"] in BUILTIN_NETWORKS:
            self.say(f"{name} is built into docker and can't be removed", YELLOW)
            return
        if self.armed and self.armed[:2] == (self.tab, e["ID"]) and time.time() - self.armed[2] < 3:
            self.armed = None
            cmd = {"images": ["rmi", e["ID"]], "volumes": ["volume", "rm", e["ID"]],
                   "networks": ["network", "rm", e["ID"]]}[self.tab]
            self.act([e["ID"]], "removing…", cmd, f"removed {name}")
        else:
            self.armed = (self.tab, e["ID"], time.time())
            self.say(f"press d again to remove {name}", YELLOW)

    def key(self, k):
        if k == "q":
            return "quit"
        if k in ("1", "2", "3", "4"):
            self.tab = TABS[int(k) - 1]
        elif k in ("TAB", "RIGHT"):
            self.tab = TABS[(TABS.index(self.tab) + 1) % len(TABS)]
        elif k in ("BTAB", "LEFT"):
            self.tab = TABS[(TABS.index(self.tab) - 1) % len(TABS)]
        elif k in ("UP", "k", "WHEELUP"): self.move(-1)
        elif k in ("DOWN", "j", "WHEELDOWN"): self.move(+1)
        elif k in ("HOME", "g"): self.move(-10**6)
        elif k in ("END", "G"): self.move(10**6)
        elif isinstance(k, tuple):
            _, x, y = k
            if y in (3, 4):  # the tab bar
                self.tab = next((t for s, e, t in self.tab_spans if s <= x < e), self.tab)
            elif x < self.left_w and y in self.rows:
                self.sel[self.tab] = self.rows[y]
        elif k == "L":
            self.external(["lazydocker"])
        else:
            e = self.current()
            if e and e["ID"] not in self.busy:
                if k == "d":
                    self.remove(e)
                elif self.tab == "containers":
                    self.container_key(k, e)
                elif self.tab == "images" and k == "u":
                    if e["dangling"]:
                        self.say("an untagged image can't be pulled", YELLOW)
                    else:
                        self.act([e["ID"]], "pulling…", ["pull", e["ref"]], f"pulled {e['ref']}")

    def container_key(self, k, c):
        if k == "s":
            if c["State"] == "running":
                self.act([c["ID"]], "stopping…", ["stop", c["ID"]], f"stopped {c['short']}")
            else:
                self.act([c["ID"]], "starting…", ["start", c["ID"]], f"started {c['short']}")
        elif k == "r":
            self.act([c["ID"]], "restarting…", ["restart", c["ID"]], f"restarted {c['short']}")
        elif k == "p":
            if not c["project"]:
                self.say("this container isn't part of a compose project", YELLOW)
                return
            group = [x for x in self.items if x["project"] == c["project"]]
            running = any(x["State"] == "running" for x in group)
            verb = "stop" if running else "start"
            self.act([x["ID"] for x in group], "stopping…" if running else "starting…",
                     ["compose", "-p", c["project"], verb], f"{c['project']}: {verb}{'ped' if running else 'ed'}")
        elif k == "l":
            self.external(["sh", "-c", 'docker logs --tail 5000 "$1" 2>&1 | less -R +G', "sh", c["ID"]])
        elif k == "e":
            if c["State"] != "running":
                self.say("start the container first (s)", YELLOW)
                return
            self.external(["docker", "exec", "-it", c["ID"], "sh", "-c",
                           "command -v bash >/dev/null && exec bash || exec sh"])
        elif k == "o":
            pp = ports(c["Ports"])
            if not pp:
                self.say("no published ports", YELLOW)
                return
            url = f"http://localhost:{pp[0][0].split('-')[0]}"
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"opened {url}")


if __name__ == "__main__":
    show(DockerPanel())
