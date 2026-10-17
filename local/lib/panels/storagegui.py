#!/usr/bin/env python3
# storagegui.py — Storage (Super + Ctrl + F): what fills the disks and what can go. Running it again closes it.
#
#   top      the SSD (/) and the HDD (/mnt/data): used / free
#   rows     what can be freed, each with its size, measured in threads when the panel opens:
#            Docker build cache, images no container uses, stopped containers; node_modules of projects in
#            ~/code untouched for STALE_DAYS (codesync never backs them up: a reinstall brings them back);
#            the pacman cache (old versions, uninstalled packages); ~/.cache's folders; the Trash
#   Clean    twice within 3 s. A row with a list (node_modules, ~/.cache) unfolds to clean one at a time.
#            The pacman cache needs root: paccache runs in the terminal popup (termgui.py), asking for the password.
#
# Never touches /mnt/data (codesync's .code-trash is codesync's) or Docker volumes (they hold databases).
import json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, level, run, scrolled

HOME = os.path.expanduser("~")
CODE = f"{HOME}/code"
CACHE = f"{HOME}/.cache"
TRASH = f"{HOME}/.local/share/Trash"
TERM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "termgui.py")
FILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filesgui.py")
STALE_DAYS = 30
DISKS = (("/", "SSD (system, ~/code)"), ("/mnt/data", "HDD (data, code backup)"))
UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3,
         "TIB": 1024 ** 4}

CSS = """
.disk { padding: 4px 0 10px 0; }
.disk levelbar trough { background: #292e42; border: none; padding: 0; min-height: 8px; }
.disk levelbar block { min-height: 8px; border: none; }
.disk levelbar block.empty { background: transparent; }
.disk levelbar.green block.filled { background: #9ece6a; }
.disk levelbar.yellow block.filled { background: #e0af68; }
.disk levelbar.red block.filled { background: #f7768e; }
.item-where { color: #565f89; font-size: 9pt; }
.item-where button, .sub-row .open { padding: 0 6px; min-height: 0; color: #6b8fe0; background: transparent; }
.item-where button:hover, .sub-row .open:hover { color: #c0caf5; }
.item { padding: 10px 0; border-bottom: 1px solid alpha(#3b4261, .7); }
.item-title { font-weight: 700; }
.item-size { font-weight: 700; color: #e0af68; min-width: 80px; }
.item-size.small { color: #565f89; }
.sub-row { padding: 3px 0 3px 26px; }
.sub-row button { padding: 0 8px; min-height: 0; }
.fold { min-width: 16px; min-height: 0; padding: 0 2px; border: none; background: transparent; color: #565f89; }
.fold:hover { color: #c0caf5; background: transparent; }
"""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000


def parse_size(text):
    """docker's / paccache's "48.62GB", "1.9 GiB" -> bytes."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)", text or "", re.I)
    return int(float(m.group(1)) * UNITS[m.group(2).upper()]) if m else 0


def du(path):
    """Bytes a folder takes on disk (its blocks: a sparse or compressed file counts what it uses)."""
    try:
        out = subprocess.run(["du", "-sb", "--", path], capture_output=True, text=True, timeout=300).stdout
        return int(out.split()[0]) if out else 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def last_touched(path):
    """When a project was last worked on: its last commit, else its newest top-level entry."""
    try:
        r = subprocess.run(["git", "-C", path, "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        return max(e.stat(follow_symlinks=False).st_mtime for e in os.scandir(path) if e.name != "node_modules")
    except (OSError, ValueError):
        return time.time()


def stale_node_modules():
    """[(project path, its node_modules folders, bytes, days since touched)] for the ~/code projects not
    touched for STALE_DAYS: every node_modules in the project (a monorepo's apps' too), not inside another."""
    projects = []
    for d, dirs, files in os.walk(CODE):
        if ".git" in dirs or "package.json" in files:
            projects.append(d)
            dirs[:] = []   # a project's insides are its own
        else:
            dirs[:] = [x for x in dirs if not x.startswith(".") and x != "node_modules"]
    out = []
    for p in projects:
        days = (time.time() - last_touched(p)) / 86400
        if days < STALE_DAYS:
            continue
        mods = []
        for d, dirs, _files in os.walk(p):
            if "node_modules" in dirs:
                mods.append(os.path.join(d, "node_modules"))
            dirs[:] = [x for x in dirs if x not in ("node_modules", ".git")]
        if mods:
            out.append((p, mods, sum(du(m) for m in mods), int(days)))
    return sorted(out, key=lambda x: -x[2])


class Storage(View):
    title, subtitle = "Storage", "what fills the disk, and what can go"
    icon = "\U000f02ca"   # nf-md-harddisk
    interval = 0
    css = CSS
    hints = [("Clean", "twice"), ("F5", "measure again"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.sizes = {}          # item key -> bytes (None: measuring)
        self.lists = {}          # item key -> [(label, path(s), bytes, note)] for the rows that unfold
        self.open = set()        # items unfolded
        self.armed = None        # (key, time): Clean pressed once
        self.pool = ThreadPoolExecutor(4)

    def items(self):
        """(key, title, what it is, how it's cleaned, where it is on disk) in the order shown."""
        docker = "/var/lib/docker"
        return [("docker-cache", "Docker build cache", "layers kept from earlier builds; a build makes them again",
                 "docker builder prune -af", [f"{docker}/buildkit"]),
                ("docker-images", "Docker images no container uses", "pulled or built images nothing runs from "
                 "(a stopped container's image stays)", "docker image prune -af", [f"{docker}/image", f"{docker}/overlay2"]),
                ("docker-stopped", "Stopped Docker containers", "their volumes and images stay", "docker container prune -f",
                 [f"{docker}/containers"]),
                ("node-modules", f"node_modules of projects untouched for {STALE_DAYS}+ days",
                 "never in the code backup; pnpm / npm install brings them back", "deleted", [CODE]),
                ("pacman", "Package cache (pacman, paru)", "older versions of installed packages (the newest kept) and "
                 "uninstalled ones; needs your password", "paccache -rk1, paccache -ruk0",
                 ["/var/cache/pacman/pkg"] + ([f"{CACHE}/paru/clone"] if os.path.isdir(f"{CACHE}/paru/clone") else [])),
                ("cache", "~/.cache", "apps' caches, made again as needed (browser caches, downloaded test browsers…)",
                 "deleted", [CACHE]),
                ("trash", "Trash", "what Files deleted", "emptied", [f"{TRASH}/files"])]

    def where(self, paths):
        """The item's folders, each with Open (in Files) when you may read it: Docker's is root's."""
        row = box(False, 6, label("\uf07b", "item-where"), classes=("item-where",))
        for p in paths:
            row.append(label(p.replace(HOME, "~"), "item-where"))
            if os.access(p, os.R_OK | os.X_OK):
                b = button("Open", lambda p=p: self.open_folder(p), tooltip="in Files")
                b.set_focusable(False)
                row.append(b)
        return row

    def open_folder(self, path):
        subprocess.Popen(["uwsm", "app", "--", sys.executable, FILES, path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def build(self):
        self.disks = box(True, 6)
        self.body = box(True, 0)
        page = box(True, 12, self.disks, scrolled(self.body))
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        self.paint_disks()
        self.measure_all()
        return page

    # ---- measuring ---------------------------------------------------------------------------------
    def measure_all(self):
        for key, *_ in self.items():
            self.measure(key)

    def measure(self, key):
        self.sizes[key] = None
        self.paint()

        def work():
            try:
                size, items = getattr(self, "size_" + key.replace("-", "_"))()
            except Exception as e:   # a tool missing (docker off): the row says so instead of a size
                size, items = 0, [(f"couldn't measure: {e}", None, 0, "")]
            GLib.idle_add(self.measured, key, size, items)
        self.pool.submit(work)

    def measured(self, key, size, items):
        self.sizes[key] = size
        self.lists[key] = items
        self.paint()
        self.paint_disks()
        return False

    def docker_df(self):
        out = subprocess.run(["docker", "system", "df", "--format", "json"], capture_output=True, text=True,
                             timeout=60).stdout
        return {d["Type"]: d for d in (json.loads(l) for l in out.splitlines() if l.strip())}

    def size_docker_cache(self):
        return parse_size(self.docker_df().get("Build Cache", {}).get("Reclaimable")), []

    def size_docker_images(self):
        return parse_size(self.docker_df().get("Images", {}).get("Reclaimable")), []

    def size_docker_stopped(self):
        return parse_size(self.docker_df().get("Containers", {}).get("Reclaimable")), []

    def size_node_modules(self):
        rows = [(os.path.relpath(p, CODE), mods, size, f"{days} days") for p, mods, size, days in stale_node_modules()]
        return sum(r[2] for r in rows), rows

    def size_pacman(self):
        total = 0
        for args in (["-dk1"], ["-duk0"]):
            out = subprocess.run(["paccache", *args], capture_output=True, text=True, timeout=60).stdout
            m = re.search(r"disk space saved: ([\d.]+ \w+)", out)
            total += parse_size(m.group(1)) if m else 0
        return total, []

    def size_cache(self):
        rows = []
        with os.scandir(CACHE) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    rows.append((e.name, [e.path], du(e.path), ""))
        rows = [r for r in rows if r[2] > 1_000_000]
        return sum(r[2] for r in rows), sorted(rows, key=lambda r: -r[2])

    def size_trash(self):
        return (du(TRASH) if os.path.isdir(TRASH) else 0), []

    # ---- drawing -----------------------------------------------------------------------------------
    def paint_disks(self):
        clear(self.disks)
        for mount, name in DISKS:
            if not os.path.ismount(mount):
                continue
            st = os.statvfs(mount)
            total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
            used = total - free
            pct = used / total * 100 if total else 0
            bar = Gtk.LevelBar(min_value=0, max_value=100, value=pct, hexpand=True)
            bar.add_css_class(level(pct))
            head = box(False, 10, label(name, "bold"), label(mount, "dim"), label(""),
                       label(f"{human(used)} used · {human(free)} free of {human(total)}", "dim"))
            head.get_first_child().get_next_sibling().get_next_sibling().set_hexpand(True)
            self.disks.append(box(True, 6, head, bar, classes=("disk",)))

    def paint(self):
        clear(self.body)
        total = sum(s for s in self.sizes.values() if s)
        busy = any(s is None for s in self.sizes.values())
        self.set_subtitle(("measuring… " if busy else "") + f"{human(total)} can be freed")
        for key, title, what, how, paths in self.items():
            size = self.sizes.get(key)
            sub = self.lists.get(key) or []
            unfolds = key in ("node-modules", "cache") and sub
            fold = button("" if key in self.open else "", lambda key=key: self.toggle(key), "fold")
            if not unfolds:   # kept, unseen: every row's title starts at the same place
                fold.set_opacity(0)
                fold.set_sensitive(False)
            fold.set_focusable(False)
            text = box(True, 2, label(title, "item-title"), label(f"{what} · {how}", "dim", wrap=True), self.where(paths))
            text.set_hexpand(True)
            size_l = label("measuring…" if size is None else human(size), "item-size", xalign=1.0)
            if size is not None and size < 50_000_000:
                size_l.add_css_class("small")
            sure = self.armed and self.armed[0] == key and time.time() - self.armed[1] < 3
            clean = button("Click again" if sure else "Clean", lambda key=key: self.clean(key),
                           *(("armed",) if sure else ("flat", "danger")))
            clean.set_sensitive(bool(size))
            clean.set_focusable(False)
            row = box(False, 12, fold, text, size_l, clean, classes=("item",))
            row.set_valign(Gtk.Align.START)
            self.body.append(row)
            if unfolds and key in self.open:
                for name, paths, bytes_, note in sub:
                    k = (key, name)
                    sure = self.armed and self.armed[0] == k and time.time() - self.armed[1] < 3
                    b = button("Click again" if sure else "Delete", lambda k=k, paths=paths: self.clean_one(k, paths),
                               *(("armed",) if sure else ("flat", "danger")))
                    b.set_focusable(False)
                    b.set_sensitive(bool(paths))
                    n = label(name, ellipsize=True)
                    folder = os.path.dirname(paths[0]) if key == "node-modules" and paths else (paths or [""])[0]
                    o = button("Open", lambda f=folder: self.open_folder(f), "open", tooltip=folder.replace(HOME, "~"))
                    o.set_focusable(False)
                    o.set_sensitive(bool(folder))
                    self.body.append(box(False, 12, n, label(note, "dim"), label(human(bytes_), "dim", xalign=1.0), o, b,
                                         classes=("sub-row",)))

    def toggle(self, key):
        self.open ^= {key}
        self.paint()

    # ---- cleaning ----------------------------------------------------------------------------------
    def confirm(self, key, what):
        """True on the second press within 3 s; the first arms the button."""
        if self.armed and self.armed[0] == key and time.time() - self.armed[1] < 3:
            self.armed = None
            return True
        self.armed = (key, time.time())
        self.say(f"press again to clean {what}")
        GLib.timeout_add(3100, lambda: self.paint() or False)
        self.paint()
        return False

    def clean(self, key):
        title = next(t for k, t, *_ in self.items() if k == key)
        if not self.confirm(key, title):
            return
        if key == "pacman":   # root: in the terminal popup, where sudo asks for the password
            script = ('printf "\\033[38;2;107;143;224mpackage cache\\033[0m old versions and uninstalled packages\\n\\n"; '
                      'sudo sh -c "paccache -rk1 && paccache -ruk0"; s=$?; '
                      'printf "\\n\\033[2m── done (%s) · Enter to close ──\\033[0m" $s; read _')
            subprocess.Popen(["uwsm", "app", "--", sys.executable, TERM, "package cache", "--", "sh", "-c", script],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            GLib.timeout_add_seconds(20, lambda: self.measure("pacman") or False)
            return
        cmds = {"docker-cache": ["docker", "builder", "prune", "-af"], "docker-images": ["docker", "image", "prune", "-af"],
                "docker-stopped": ["docker", "container", "prune", "-f"], "trash": ["gio", "trash", "--empty"]}
        paths = [p for _n, ps, _b, _note in self.lists.get(key, []) for p in (ps or [])]
        self.say(f"cleaning {title}…", seconds=120)

        def work():
            if key in cmds:
                r = subprocess.run(cmds[key], capture_output=True, text=True, timeout=1800)
                ok, msg = r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
            else:
                ok, msg = self.remove(paths), [""]
            GLib.idle_add(lambda: (self.say(f"{title}: cleaned" if ok else f"{title}: {msg[0]}", "ok" if ok else "bad"),
                                   self.measure(key)) and False)
        self.pool.submit(work)

    def clean_one(self, k, paths):
        if not self.confirm(k, k[1]):
            return

        def work():
            ok = self.remove(paths)
            GLib.idle_add(lambda: (self.say(f"deleted {k[1]}" if ok else f"couldn't delete all of {k[1]}",
                                            "ok" if ok else "bad"), self.measure(k[0])) and False)
        self.pool.submit(work)

    @staticmethod
    def remove(paths):
        """Delete folders, only ever inside ~/code (node_modules) or ~/.cache."""
        ok = True
        for p in paths:
            real = os.path.realpath(p)
            if not (real.startswith(CACHE + "/") or (real.startswith(CODE + "/") and os.path.basename(real) == "node_modules")):
                ok = False
                continue
            shutil.rmtree(real, ignore_errors=True)
            ok = ok and not os.path.exists(real)
        return ok

    def key(self, keyval, state):
        if keyval == Gdk.KEY_F5:
            self.measure_all()
            return True
        return False


def make():
    return Storage()


def main():
    run(make(), "panels.storage", (900, 640))


if __name__ == "__main__":
    main()
