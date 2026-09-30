#!/usr/bin/env python3
# dockerpanel.py — Docker panel (Tokyo Night): containers grouped by compose project, live CPU and
# memory, ports, and the selected container's details and logs. The app on workspace 9
# (config/hypr/workspaces.conf); lazydocker is one key away for anything this doesn't do.
#
#   ↑↓ / click  select        s  start / stop       r  restart      p  start / stop the whole project
#   l  full logs (less)       e  shell inside       o  open port    L  lazydocker        q  close
import json, os, re, signal, subprocess, threading, time
from itertools import zip_longest

from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, TRACK, GREEN, YELLOW, RED, CYAN,
                      BOLD, RESET, clip, fit, frame, card, spread, header, section, hints, highlight)

HOME = os.path.expanduser("~")
FORMAT = ('{{json .}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
          '\t{{.Label "com.docker.compose.project.working_dir"}}')
PORT = re.compile(r"(?:[\d.]+|\[[^\]]*\]):(\d+(?:-\d+)?)->(\d+(?:-\d+)?)/(\w+)")
ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|.)")


def docker(*args, timeout=15):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def level(p): return GREEN if p < 60 else YELLOW if p < 85 else RED


def bar(p, width):
    n = round(width * min(p, 100) / 100)
    return level(p) + "━" * n + TRACK + "━" * (width - n) + RESET


def short_size(s):
    """'768.4MiB / 15.51GiB' -> '768M', '1.527MiB / …' -> '1.5M'"""
    m = re.match(r"([\d.]+)\s*([kKMGT]?)i?B", s.strip())
    if not m:
        return s.split("/")[0].strip()
    n, unit = float(m.group(1)), m.group(2).upper() or "B"
    return f"{n:.1f}{unit}" if n < 10 else f"{n:.0f}{unit}"


def wrap(line, w):
    """A long log line as several rows, continuations indented."""
    rows, line = [line[:w]], line[w:]
    while line:
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


class DockerPanel(Panel):
    interval = 1.0

    def __init__(self):
        self.items, self.error = [], ""
        self.sel = None                   # selected container ID (survives refreshes and reordering)
        self.stats, self.counts = {}, (0, 0)
        self.logs, self.logs_for = [], None
        self.busy = {}                    # container ID -> "restarting…" while an action runs
        self.flash, self.flash_at = "", 0
        self.rows, self.left_w = {}, 0    # screen row -> container ID, for clicks
        self.scroll = 0
        self.tick()
        threading.Thread(target=self.watch_stats, daemon=True).start()
        threading.Thread(target=self.watch_logs, daemon=True).start()

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
            name = c["Names"]
            if project and name.startswith(project + "-"):
                name = name[len(project) + 1:]
                name = name[:-2] if name.endswith("-1") else name
            c.update(project=project, service=service, workdir=workdir, short=name)
            items.append(c)
        # compose projects A–Z, containers not in a project last
        items.sort(key=lambda c: (not c["project"], c["project"], c["short"]))
        self.items, self.error = items, ""
        ids = [c["ID"] for c in items]
        if self.sel not in ids:
            self.sel = ids[0] if ids else None

    def watch_stats(self):
        while True:
            try:
                r = docker("stats", "--no-stream", "--format", "{{json .}}")
                self.stats = {s["ID"]: s for s in map(json.loads, r.stdout.splitlines())}
                images = docker("images", "-q").stdout.split()
                volumes = docker("volume", "ls", "-q").stdout.split()
                self.counts = (len(set(images)), len(volumes))
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            time.sleep(2)

    def watch_logs(self):
        while True:
            cid = self.sel
            if cid:
                try:
                    r = subprocess.run(["docker", "logs", "--tail", "200", cid], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, timeout=10)
                    text = ANSI.sub("", r.stdout.decode(errors="replace"))
                    lines = [l.split("\r")[-1].expandtabs(4) for l in text.splitlines()]
                    self.logs = [l for l in lines if l.strip()] or ["(no output yet)"]
                    self.logs_for = cid
                except (OSError, subprocess.TimeoutExpired):
                    pass
            # wake early when the selection changes
            for _ in range(20):
                time.sleep(0.1)
                if self.sel != cid:
                    break

    def current(self):
        return next((c for c in self.items if c["ID"] == self.sel), None)

    # ---- drawing ---------------------------------------------------------------------------------
    def container_row(self, c, w):
        color, dot, words = status(c)
        name_w = max(w - 36, 4)
        name = f"{color}{dot}{RESET} {FG if c['State'] == 'running' else DIM}{fit(c['short'], name_w):<{name_w}}{RESET}"
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

    def left(self, w, top_row, room):
        lines, ids = [], []   # ids[i]: the container on lines[i], or None
        project = object()
        for c in self.items:
            if c["project"] != project:
                project = c["project"]
                group = [x for x in self.items if x["project"] == project]
                up = sum(x["State"] == "running" for x in group)
                if lines:
                    lines.append(""); ids.append(None)
                lines.append(section(project or "standalone", w, f"{up}/{len(group)} up"))
                ids.append(None)
            row = self.container_row(c, w)
            lines.append(highlight(row, w) if c["ID"] == self.sel else row)
            ids.append(c["ID"])
        if not lines:
            lines = [f"{RED}{self.error}{RESET}" if self.error else f"{DIM}no containers{RESET}"]
            ids = [None]
        # keep the selection on screen
        at = ids.index(self.sel) if self.sel in ids else 0
        if at < self.scroll + 1:
            self.scroll = max(at - 1, 0)
        elif at >= self.scroll + room:
            self.scroll = at - room + 1
        self.scroll = max(min(self.scroll, len(lines) - room), 0)
        lines, ids = lines[self.scroll:self.scroll + room], ids[self.scroll:self.scroll + room]
        self.rows = {top_row + i: cid for i, cid in enumerate(ids) if cid}
        return lines

    def right(self, w, room):
        c = self.current()
        if not c:
            return []
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
        rows += [f"{DIM}{k:<10}{RESET}{FG}{clip(v, w - 10)}{RESET}" for k, v in kv]
        rows.append("")
        rows.append(section("Logs", w, "l opens all"))
        logs = self.logs if self.logs_for == c["ID"] else [f"{DIM}loading…{RESET}"]
        n = max(room - len(rows), 0)
        tail = [r for l in logs[-n:] for r in wrap(l, w)][-n:] if n else []
        rows += [f"{FG}{clip(r, w)}{RESET}" for r in tail]
        return rows

    def draw(self, w, h):
        cols, rows, width, pad = frame(200)
        room = max(rows - 5, 0)
        gap = 4
        lw = min((width - gap) * 9 // 20, 58)
        rw = width - gap - lw
        self.left_w = len(pad) + lw
        body_top = 3  # header (2) + blank row
        L, R = self.left(lw, body_top, room), self.right(rw, room)
        body = [clip(l or "", lw) + " " * gap + (r or "") for l, r in zip_longest(L, R)]
        up = sum(c["State"] == "running" for c in self.items)
        images, volumes = self.counts
        sub = f"{up} running · {len(self.items) - up} stopped · {images} images · {volumes} volumes"
        head = header("", "Docker", sub, width)
        if self.flash and time.time() - self.flash_at < 4:
            foot = self.flash
        else:
            foot = hints([("↑↓", "select"), ("s", "start/stop"), ("r", "restart"), ("p", "project"),
                          ("l", "logs"), ("e", "shell"), ("o", "open port"), ("L", "lazydocker"),
                          ("q", "close")], width)
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
                r = docker(*args, timeout=120)
                if r.returncode:
                    self.say(fit(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed", 150), RED)
                else:
                    self.say(done)
            except (OSError, subprocess.TimeoutExpired) as e:
                self.say(str(e), RED)
            finally:
                for i in ids:
                    self.busy.pop(i, None)
        threading.Thread(target=work, daemon=True).start()

    def external(self, argv):
        """A full-screen program in place of the panel. Ctrl+C belongs to it, not to us."""
        signal.signal(signal.SIGINT, lambda *_: None)  # reset to default in the child by exec
        try:
            suspend(argv)
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)

    def move(self, step):
        ids = [c["ID"] for c in self.items]
        if ids:
            i = ids.index(self.sel) if self.sel in ids else 0
            self.sel = ids[max(0, min(len(ids) - 1, i + step))]

    def key(self, k):
        c = self.current()
        if k == "q":
            return "quit"
        if k in ("UP", "k", "WHEELUP"): self.move(-1)
        elif k in ("DOWN", "j", "WHEELDOWN"): self.move(+1)
        elif k in ("HOME", "g"): self.move(-len(self.items))
        elif k in ("END", "G"): self.move(len(self.items))
        elif isinstance(k, tuple):
            if k[1] < self.left_w and k[2] in self.rows:
                self.sel = self.rows[k[2]]
        elif k == "L":
            self.external(["lazydocker"])
        elif not c or c["ID"] in self.busy:
            return
        elif k == "s":
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
            group = [x["ID"] for x in self.items if x["project"] == c["project"]]
            running = any(x["State"] == "running" for x in self.items if x["project"] == c["project"])
            verb = "stop" if running else "start"
            self.act(group, "stopping…" if running else "starting…",
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
