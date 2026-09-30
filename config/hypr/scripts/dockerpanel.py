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
#   containers: s start/stop  r restart  p project start/stop  o open port
#               l full-screen logs (formatted by dockerlogs.py, following)
#               e exec: run a command inside (default: its shell; Tab = as root; ↑↓ history)
#   images: u pull (update)            L lazydocker            q close
import json, os, re, shlex, shutil, signal, subprocess, sys, threading, time
from itertools import zip_longest

import dockerlogs
from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, TRACK, GREEN, YELLOW, RED, CYAN,
                      BOLD, RESET, clip, fit, frame, card, header, section, hints, highlight, visible_len)

HOME = os.path.expanduser("~")
FORMAT = ('{{json .}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
          '\t{{.Label "com.docker.compose.project.working_dir"}}')
PORT = re.compile(r"(?:[\d.]+|\[[^\]]*\]):(\d+(?:-\d+)?)->(\d+(?:-\d+)?)/(\w+)")
DOCKERLOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dockerlogs.py")
SHELLS = {"bash", "sh", "ash", "zsh", "fish", "dash"}
EXEC_PRESETS = ["env", "ps aux", "df -h", "cat /etc/os-release", "ls -la"]
TABS = ["containers", "images", "volumes", "networks"]
BUILTIN_NETWORKS = {"bridge", "host", "none"}
PROJECT = "project:"  # a compose project's row in the containers list has the ID "project:<name>"


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


def is_project(e):
    return bool(e) and e["ID"].startswith(PROJECT)


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
        self.logs, self.logs_for = [], None           # dockerlogs.Record list of the selected container
        self.prompt = None                              # the exec prompt while it's open
        self.exec_history = []
        self.history = {}                               # image ID -> [(size, command)]
        self.busy = {}                                  # ID -> "removing…" while an action runs
        self.armed = None                               # (tab, ID, time): d was pressed once
        self.folded = set()                             # compose projects shown as just their row
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
        projects = [PROJECT + p for p in dict.fromkeys(c["project"] for c in items) if p]
        self.keep_selection("containers", projects + [c["ID"] for c in items])

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
                if tab == "containers" and sid and not sid.startswith(PROJECT):
                    r = subprocess.run(["docker", "logs", "--timestamps", "--tail", "300", sid],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
                    records = map(dockerlogs.parse, r.stdout.decode(errors="replace").splitlines())
                    self.logs = dockerlogs.collapse([x for x in records if not dockerlogs.blank(x)])
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
        sid = self.sel[self.tab]
        if self.tab == "containers" and sid and sid.startswith(PROJECT):
            return self.project(sid[len(PROJECT):])
        return next((e for e in self.entries() if e["ID"] == sid), None)

    def project(self, name):
        """A compose project as an entry: its row ID, name, folder and containers."""
        group = [c for c in self.items if c["project"] == name]
        if not group:
            return None
        return dict(ID=PROJECT + name, project=name, workdir=group[0]["workdir"], containers=group)

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

    def project_row(self, name, w):
        """A compose project's heading, selectable like Docker Desktop's project row."""
        group = [c for c in self.items if c["project"] == name]
        up = sum(c["State"] == "running" for c in group)
        pid = PROJECT + name
        if pid in self.busy:
            note = f"{CYAN}{self.busy[pid]}"
        elif up:
            note = f"{GREEN if up == len(group) else YELLOW}●{RESET}{DIM} running {up}/{len(group)}"
        else:
            note = f"○ exited 0/{len(group)}"
        return section(f"{'▸' if name in self.folded else '▾'} {name}", w, note)

    def listing(self):
        """[(ID or None, draw(w))]: the list's lines in on-screen order. On the containers tab a
        compose project's heading is an entry too, and a folded project hides its containers."""
        draw_row = {"containers": self.container_row, "images": self.image_row,
                    "volumes": self.volume_row, "networks": self.network_row}[self.tab]
        out = []
        for title, note, group in self.groups():
            if out:
                out.append((None, lambda w: ""))
            project = self.tab == "containers" and group[0]["project"]
            if project:
                out.append((PROJECT + project, lambda w, p=project: self.project_row(p, w)))
                if project in self.folded:
                    continue
            else:
                out.append((None, lambda w, t=title, n=note: section(t, w, n)))
            out += [(e["ID"], lambda w, e=e: draw_row(e, w)) for e in group]
        return out

    def left(self, w, top_row, room):
        sel = self.sel[self.tab]
        listing = self.listing()
        lines = [highlight(draw(w), w) if eid and eid == sel else draw(w) for eid, draw in listing]
        ids = [eid for eid, _ in listing]   # ids[i]: the entry on lines[i], or None
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
        if is_project(e):
            return self.project_detail(e, w, room)
        return {"containers": self.container_detail, "images": self.image_detail,
                "volumes": self.volume_detail, "networks": self.network_detail}[self.tab](e, w, room)

    def project_detail(self, p, w, room):
        group = p["containers"]
        up = [c for c in group if c["State"] == "running"]
        state = (f"{GREEN if len(up) == len(group) else YELLOW}● running {len(up)}/{len(group)}{RESET}"
                 if up else f"{DIM}○ exited{RESET}")
        rows = [section(p["project"], w, state)]
        kv = [("Folder", p["workdir"].replace(HOME, "~") or "—"),
              ("Containers", f"{len(up)} running · {len(group) - len(up)} stopped")]
        stats = [self.stats[c["ID"]] for c in up if c["ID"] in self.stats]
        if stats:
            cpu, mem = sum(percent(s["CPUPerc"]) for s in stats), sum(percent(s["MemPerc"]) for s in stats)
            kv.append(("CPU", f"{bar(cpu, 12)} {FG}{cpu:.1f}%{RESET}"))
            kv.append(("Memory", f"{bar(mem, 12)} {FG}{mem:.1f}% of RAM{RESET}"))
        pp = [f"{h} {c['short']}" for c in up for h, _, _ in ports(c["Ports"])]
        if pp:
            kv.append(("Ports", "  ".join(pp)))
        rows += kv_rows(kv, w) + ["", section("Containers", w, f"{len(up)}/{len(group)} up")]
        for c in group:
            color, dot, words = status(c)
            words = f"{CYAN}{self.busy[c['ID']]}" if c["ID"] in self.busy else f"{DIM}{words}"
            rows.append(f"{color}{dot}{RESET} {FG if c['State'] == 'running' else DIM}{fit(c['short'], w - 20):<{w - 20}}"
                        f"{RESET}{' ' * max(18 - visible_len(words), 0)}{words}{RESET}")
        return rows[:room]

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
        rows += kv_rows(kv, w) + ["", section("Logs", w, "l full screen")]
        if self.logs_for != c["ID"]:
            return rows + [f"{DIM}loading…{RESET}"]
        if not self.logs:
            return rows + [f"{DIM}no output yet{RESET}"]
        n, tail = max(room - len(rows), 0), []
        for rec in reversed(self.logs):  # newest at the bottom; stop once the pane is full
            tail = dockerlogs.rows(rec, w) + tail
            if len(tail) >= n:
                break
        return rows + [clip(r, w) for r in tail[len(tail) - n:]] if n else rows

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
        head = header("\uf308", "Docker", sub, width)
        if self.prompt:
            foot = self.prompt_line(width)
        elif self.flash and time.time() - self.flash_at < 4:
            foot = self.flash
        else:
            on_project = is_project(self.current())
            keys = {"containers": [("s", "start/stop all"), ("r", "restart all"), ("l", "logs"),
                                   ("enter", "fold")] if on_project else
                                  [("s", "start/stop"), ("r", "restart"), ("l", "logs"),
                                   ("e", "exec"), ("o", "open port")],
                    "images": [("u", "pull"), ("d d", "remove")],
                    "volumes": [("d d", "remove")],
                    "networks": [("d d", "remove")]}[self.tab]
            foot = hints([("1-4", "tabs"), ("↑↓", "select")] + keys + [("L", "lazydocker"), ("q", "close")], width)
        return card(head, body, foot, rows, pad)

    # ---- actions ---------------------------------------------------------------------------------
    def say(self, msg, color=ACCENT):
        self.flash, self.flash_at = f"{color}{msg}{RESET}", time.time()

    def act(self, ids, doing, args, done):
        """Run a docker command in the background; the rows show `doing` until it finishes.
        `ids` can be a dict of ID -> label when rows should say different things."""
        labels = ids if isinstance(ids, dict) else dict.fromkeys(ids, doing)
        self.busy.update(labels)  # now, so a second key press can't start it twice

        def work():
            try:
                r = docker(*args, timeout=300)
                if r.returncode:
                    self.say(fit(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed", 150), RED)
                else:
                    self.say(done)
            except (OSError, subprocess.TimeoutExpired) as e:
                self.say(str(e), RED)
            finally:
                for i in labels:
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
        ids = [eid for eid, _ in self.listing() if eid]  # in on-screen order
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
        if self.prompt:
            return self.prompt_key(k)
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
        elif k in ("ENTER", " ") and is_project(self.current()):
            self.folded ^= {self.current()["project"]}
        else:
            e = self.current()
            if e and e["ID"] not in self.busy:
                if is_project(e):
                    self.project_key(k, e)
                elif k == "d":
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
        elif k == "l":
            self.full_logs(c)
        elif k in ("e", "x"):
            if c["State"] != "running":
                self.say("start the container first (s)", YELLOW)
                return
            self.open_prompt(c)
        elif k == "o":
            pp = ports(c["Ports"])
            if not pp:
                self.say("no published ports", YELLOW)
                return
            url = f"http://localhost:{pp[0][0].split('-')[0]}"
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"opened {url}")


    def project_key(self, k, p):
        """The project row, like Docker Desktop's: stop if anything runs, else start everything."""
        name, group = p["project"], p["containers"]
        running = [c for c in group if c["State"] == "running"]
        if k == "s":
            doing, verb, done, rows = ("stopping…", "stop", "stopped", running) if running else \
                ("starting…", "start", "started", group)
        elif k == "r":
            doing, verb, done, rows = "restarting…", "restart", "restarted", group
        elif k == "l":
            return self.project_logs(name)
        elif k in ("e", "x", "o"):
            return self.say("select a container first", YELLOW)
        else:
            return
        # only the rows that will change say so; the project row says what's happening
        labels = {c["ID"]: doing for c in rows} | {p["ID"]: doing}
        self.act(labels, doing, ["compose", "-p", name, verb], f"{name}: {done}")

    # ---- full-screen logs ------------------------------------------------------------------------
    def full_logs(self, c):
        """Formatted logs in less, following new lines (F / Ctrl+C toggle). dockerlogs.py writes
        to a file that less follows; it runs in its own session so Ctrl+C in less doesn't stop it."""
        cols = shutil.get_terminal_size().columns
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"dockerpanel-{c['ID']}.log")
        with open(path, "w") as out:
            p = subprocess.Popen([sys.executable, DOCKERLOGS, "--follow", c["ID"], "--width", str(cols)],
                                 stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.4)  # let the backlog arrive so less opens at the end of it
        try:
            self.external(["less", "-R", "--mouse", "+F",
                           f"-Ps{c['Names']} logs · F follow · Ctrl+C stop following · / search · q back$", path])
        finally:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            p.wait()
            os.remove(path)

    def project_logs(self, name):
        """Every container's logs in one stream (compose colours each name), in less."""
        script = (f"docker compose --ansi always -p {shlex.quote(name)} logs --follow --tail 200 2>&1 | "
                  f"less -R --mouse +F {shlex.quote(f'-Ps{name} logs · F follow · Ctrl+C stop following · / search · q back$')}")
        self.external(["sh", "-c", script])

    # ---- exec ------------------------------------------------------------------------------------
    def open_prompt(self, c):
        """The exec prompt in the footer, pre-filled with the container's best shell."""
        self.prompt = dict(c=c, text="sh", shell="sh", touched=False, root=False, pick=-1)
        prompt = self.prompt

        def detect():
            try:
                r = docker("exec", c["ID"], "sh", "-c", "command -v bash || command -v ash || command -v sh",
                           timeout=5)
                shell = os.path.basename(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.split() else ""
            except (OSError, subprocess.TimeoutExpired):
                shell = ""
            if shell:
                prompt["shell"] = shell
                if not prompt["touched"]:
                    prompt["text"] = shell
        threading.Thread(target=detect, daemon=True).start()

    def prompt_line(self, w):
        p = self.prompt
        who = f"{RED}root{RESET}" if p["root"] else f"{DIM}default user{RESET}"
        left = (f"{ACCENT}{BOLD}exec{RESET} {FG}{p['c']['short']}{RESET} {DIM}as{RESET} {who} "
                f"{ACCENT}❯{RESET} {FG}{p['text']}{RESET}{ACCENT}▏{RESET}")
        right = hints([("Enter", "run"), ("Tab", "root"), ("↑↓", "history"), ("Esc", "cancel")],
                      max(w - visible_len(left) - 2, 0))
        return left + " " * max(w - visible_len(left) - visible_len(right), 2) + right

    def prompt_key(self, k):
        p = self.prompt
        choices = self.exec_history + [x for x in EXEC_PRESETS if x not in self.exec_history]
        if k == "ESC":
            self.prompt = None
        elif k == "ENTER":
            self.prompt = None
            self.run_exec(p["c"], p["text"].strip() or "sh", p["root"])
        elif k in ("TAB", "BTAB"):
            p["root"] = not p["root"]
        elif k in ("UP", "DOWN") and choices:
            p["pick"] = max(-1, min(len(choices) - 1, p["pick"] + (1 if k == "UP" else -1)))
            p["text"] = choices[p["pick"]] if p["pick"] >= 0 else p["shell"]
            p["touched"] = p["pick"] >= 0  # back at the suggestion: typing replaces it again
        elif k == "BACKSPACE":
            p["text"], p["touched"] = (p["text"][:-1] if p["touched"] else ""), True
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            p["text"] = (p["text"] if p["touched"] else "") + k  # typing replaces the suggestion
            p["touched"] = True

    def run_exec(self, c, command, root):
        try:
            words = shlex.split(command)
        except ValueError as e:
            self.say(f"can't parse the command: {e}", RED)
            return
        if command in self.exec_history:
            self.exec_history.remove(command)
        self.exec_history.insert(0, command)
        argv = ["docker", "exec", "-it"] + (["-u", "0"] if root else []) + [c["ID"]] + words
        shell = words[0] in SHELLS and len(words) == 1
        # a title line first; afterwards wait for Enter unless it was an interactive shell that went fine
        script = (f'printf "\\033[2J\\033[H\\033[38;2;107;143;224mexec\\033[0m %s \\033[2m%s\\033[0m\\n\\n" '
                  f'{shlex.quote(c["Names"])} {shlex.quote(("as root · " if root else "") + command)}; '
                  f'{shlex.join(argv)}; s=$?; '
                  f'if [ $s -ne 0 ] || [ {0 if shell else 1} = 1 ]; then '
                  f'printf "\\n\\033[2m── exit %s · Enter to go back ──\\033[0m" $s; read _; fi')
        self.external(["sh", "-c", script])


if __name__ == "__main__":
    show(DockerPanel())
