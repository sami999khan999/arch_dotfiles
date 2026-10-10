# usage.py — what's using the CPU, by what started it: every app, service, container and tmux pane
# (an agentmux thread) is a cgroup of its own (systemd), and a cgroup's cpu.stat counts its processes
# that have already exited too — the short ones a process list never catches (a test runner's
# `cat /proc/cpuinfo | grep` pipelines, Docker's health checks). Only file reads: no process is
# started, except a cached docker / tmux lookup of names. Used by the System panel's Apps tab
# (sysgui.py), the bar's busy warning (scripts/loadwatch.py), agentmux's sidebar and dotperf.
import os, re, subprocess, time

ROOT = "/sys/fs/cgroup"
# panels and helpers shown by what they are rather than by their script
TITLES = {"ccgui.py": "Control Center", "projectsgui.py": "Projects", "dockergui.py": "Docker panel",
          "filesgui.py": "Files", "dbgui.py": "Databases", "pickergui.py": "File dialogs",
          "sysgui.py": "System panel", "elephant": "Launcher (elephant)", "waybar": "Bar (waybar)",
          "wayland-wm": "Hyprland", "codesync": "Code backup (codesync)", "google-chrome-stable": "Chrome",
          "code": "VS Code"}
INTERPRETERS = {"python3", "python", "node", "bun", "deno", "sh", "bash", "zsh", "fish", "env", "uv"}


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def usec(path):
    """CPU time this cgroup has used, in µs (its exited processes included)."""
    m = re.search(r"usage_usec (\d+)", read(f"{ROOT}/{path}/cpu.stat"))
    return int(m.group(1)) if m else 0


def leaves():
    """The cgroups that hold processes: those without cgroups under them (cgroup v2 keeps processes
    only in leaves), relative to ROOT."""
    out = []
    for d, dirs, files in os.walk(ROOT):
        if not dirs and "cpu.stat" in files:
            out.append(os.path.relpath(d, ROOT))
    return out


def cgroup_of(pid):
    """The cgroup a process is in (relative to ROOT), or None."""
    line = read(f"/proc/{pid}/cgroup").strip()
    return line.split("::", 1)[1].lstrip("/") if "::" in line else None


def forks():
    """Processes started since boot (the `processes` line of /proc/stat)."""
    m = re.search(r"^processes (\d+)", read("/proc/stat"), re.M)
    return int(m.group(1)) if m else 0


def label_of(pid):
    """What a process is, in a word or two: "vitest", "tsc", "pactl" — an interpreter's script, not
    "node" or "python3"; a process that renamed itself ("node (vitest)") by that name."""
    args = [a for a in read(f"/proc/{pid}/cmdline").split("\0") if a]
    if not args:
        return read(f"/proc/{pid}/comm").strip()
    if len(args) == 1 and " " in args[0]:   # a process title ("node (vitest 2)") or a rewritten command line (Chrome)
        m = re.search(r"\(([^)\s]+)", args[0])
        if m:
            return m.group(1)
        args = args[0].split()
    name = os.path.basename(args[0])
    if name in INTERPRETERS:
        rest = iter(args[1:])
        for a in rest:
            if a == "-m":                            # python -m module
                return next(rest, name)
            if a in ("-X", "-W", "-c", "-e", "--require", "-r"):   # options that take a value
                next(rest, None)
            elif not a.startswith("-"):
                base = os.path.basename(a)
                return base if base not in ("cli.js", "index.js", "main.js") else os.path.basename(os.path.dirname(a)) or base
    return name


# ---- names -----------------------------------------------------------------------------------------
_docker = {"at": 0, "names": {}}
_panes = {"at": 0, "names": {}}


def docker_name(cid):
    if cid not in _docker["names"] and time.time() - _docker["at"] > 60:   # an unknown container: look again
        _docker["at"] = time.time()
        try:
            out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
                                 capture_output=True, text=True, timeout=5).stdout
            _docker["names"] = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
        except (OSError, subprocess.SubprocessError):
            pass
    return _docker["names"].get(cid, cid[:12])


def pane_name(path):
    """A tmux pane's scope: the agentmux thread (or sidebar) it runs, from the panes' pids."""
    if path not in _panes["names"] and time.time() - _panes["at"] > 10:
        _panes["at"] = time.time()
        names = {}
        for server, what in (("agents", "#{session_name}"), ("agentmux", "agentmux #{pane_current_command}")):
            try:
                out = subprocess.run(["tmux", "-L", server, "list-panes", "-a", "-F", "#{pane_pid} " + what],
                                     capture_output=True, text=True, timeout=2).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            for line in out.splitlines():
                pid, _, name = line.partition(" ")
                cg = cgroup_of(pid)
                if cg and not name.startswith("_"):
                    names[cg] = name
        _panes["names"] = names
    return _panes["names"].get(path)


def describe(path, pids=()):
    """(name, kind) of a cgroup: kind app / agent / service / container / system. pids: its processes,
    to name an app started through an interpreter (python3 x.py) by what it runs."""
    leaf = path.rsplit("/", 1)[-1]
    if m := re.fullmatch(r"docker-([0-9a-f]{64})\.scope", leaf):
        return docker_name(m.group(1)), "container"
    if leaf.startswith("tmux-spawn-"):
        name = pane_name(path)
        if name is None:
            return (label_of(pids[0]) if pids else "tmux"), "app"
        return name, "app" if name.startswith("agentmux ") else "agent"
    if m := re.fullmatch(r"app-(?:Hyprland|hyprland)-(.+?)(?:@[^-]*)?-[0-9a-f]+\.scope", leaf):
        prog = m.group(1).replace("\\x2d", "-")
        if prog in INTERPRETERS and pids:
            prog = label_of(pids[0])
        return TITLES.get(prog, prog), "app"
    if re.fullmatch(r"session-\d+\.scope", leaf):
        return "Login session (Hyprland)", "system"
    # an app's own scopes: app-com.google.Chrome-3129674, app-com.microsoft.VSCode-164068, kitty-1353-0
    if m := re.fullmatch(r"(?:app-)?(?:[a-z]+\.)*([A-Za-z][\w-]*?)(?:-\d+)+\.scope", leaf):
        return {"Chrome": "Chrome", "VSCode": "VS Code"}.get(m.group(1), m.group(1)), "app"
    if m := re.fullmatch(r"(?:app-)?(.+?)(?:@.*)?\.service", leaf):
        name = m.group(1).replace("\\x2d", "-")
        system = path.startswith("system.slice") or "user@" not in path
        return TITLES.get(name, name), "system" if system else "service"
    return leaf.removesuffix(".scope").removesuffix(".slice"), "system"


# ---- sampling --------------------------------------------------------------------------------------
class Sampler:
    """Two readings a moment apart: each cgroup's CPU in % of one core (100 = a whole core, so a busy
    4-thread machine reaches 400), its memory and process count, and what in it is busiest."""

    def __init__(self):
        self.prev, self.ticks, self.at = {}, {}, None
        self.forks_at = (forks(), time.time())
        self.new_per_min = 0.0

    def sample(self, busiest=10.0):
        """[{path, name, kind, cpu, mem, procs, top}] sorted by CPU, busiest first; top (the busiest
        commands) only for groups at `busiest` % or more."""
        now, cur = time.time(), {p: usec(p) for p in leaves()}
        dt = (now - self.at) if self.at else 0
        n, t0 = forks(), self.forks_at
        if now > t0[1]:
            self.new_per_min = (n - t0[0]) / (now - t0[1]) * 60
        self.forks_at = (n, now)
        rows = []
        for path, used in cur.items():
            before = self.prev.get(path)
            cpu = (used - before) / dt / 10_000 if dt and before is not None else 0.0
            pids = [p for p in read(f"{ROOT}/{path}/cgroup.procs").split()]
            if not pids and cpu < 0.05:
                continue   # an empty scope left behind
            name, kind = describe(path, pids)
            mem = int(read(f"{ROOT}/{path}/memory.current", "0").strip() or 0)
            # its processes' ticks are kept from 1 % on, so a group that turns busy names them at once
            top = self.top(pids, dt) if cpu >= 1 else []
            rows.append({"path": path, "name": name, "kind": kind, "cpu": max(cpu, 0.0), "mem": mem,
                         "procs": len(pids), "top": top if cpu >= busiest else []})
        self.prev, self.at = cur, now
        rows.sort(key=lambda r: (-r["cpu"], -r["mem"]))
        return rows

    def top(self, pids, dt, n=2):
        """The busiest commands among pids since the last reading: [(label, cpu %)]."""
        busy, hz = {}, os.sysconf("SC_CLK_TCK")
        for pid in pids:
            f = read(f"/proc/{pid}/stat")
            if not f:
                continue
            fields = f[f.rindex(")") + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            before = self.ticks.get(pid)
            self.ticks[pid] = ticks
            if before is None or not dt:
                continue
            pct = (ticks - before) / hz / dt * 100
            if pct > 0:
                label = label_of(pid)
                busy[label] = busy.get(label, 0) + pct
        return sorted(busy.items(), key=lambda kv: -kv[1])[:n]


def group_cpu(path, last):
    """CPU % of one cgroup since `last` ((usec, time) from a previous call, or None): (pct, new last)."""
    now, used = time.time(), usec(path)
    pct = (used - last[0]) / (now - last[1]) / 10_000 if last and now > last[1] else 0.0
    return max(pct, 0.0), (used, now)
