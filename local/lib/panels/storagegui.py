#!/usr/bin/env python3
# storagegui.py — Storage (Super + Ctrl + F): what fills the disks and what can go. Running it again closes it.
#
#   top      the SSD (/) and the HDD (/mnt/data) side by side: used / free; then the total that can be freed
#   rows     what can be freed, each with its size, measured in threads when the panel opens:
#            Docker build cache, images no container uses, stopped containers; node_modules of projects in
#            ~/code untouched for STALE_DAYS (codesync never backs them up: a reinstall brings them back);
#            the pacman cache (old versions, uninstalled packages); ~/.cache's folders; the Trash
#            Rows with nothing in them fold into "Already clean" at the end.
#   Clean    twice within 3 s. A row with a list (node_modules, ~/.cache) unfolds to clean one at a time.
#            Clean all (twice) cleans every row in turn; the package cache's terminal opens first.
#            The pacman cache needs root: paccache runs in the terminal popup (termgui.py), asking for the password.
#
# Never touches /mnt/data (codesync's .code-trash is codesync's) or Docker volumes (they hold databases).
import json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, level, rule_heading, run, scrolled

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
.disk { padding: 12px 14px; border: 1px solid alpha(#3b4261, .7); background: alpha(#292e42, .35); }
.disk levelbar trough { background: #292e42; border: none; padding: 0; min-height: 8px; }
.disk levelbar block { min-height: 8px; border: none; }
.disk levelbar block.empty { background: transparent; }
.disk levelbar.green block.filled { background: #9ece6a; }
.disk levelbar.yellow block.filled { background: #e0af68; }
.disk levelbar.red block.filled { background: #f7768e; }
.summary { padding: 2px 0 4px 0; border-bottom: 1px solid alpha(#3b4261, .7); }
.total { font-size: 17pt; font-weight: 700; color: #e0af68; }
.total.none { color: #9ece6a; }
.share trough { background: #292e42; border: none; padding: 0; min-height: 3px; }
.share block { min-height: 3px; border: none; }
.share block.empty { background: transparent; }
.share block.filled { background: #e0af68; }
.item-where { color: #565f89; font-size: 9pt; }
.item-disk { color: #a9b1d6; font-size: 9pt; font-weight: 700; }
.item-where button, .sub-row .open { padding: 0 6px; min-height: 0; color: #6b8fe0; background: transparent; }
.item-where button:hover, .sub-row .open:hover { color: #c0caf5; }
.item { padding: 12px 0; border-bottom: 1px solid alpha(#3b4261, .7); }
.item-title { font-weight: 700; }
.item-size { font-weight: 700; color: #e0af68; min-width: 90px; }
.item-size.small { color: #565f89; }
.sub-row { padding: 3px 0 3px 30px; }
.sub-row button { padding: 0 8px; min-height: 0; }
.done-row { padding: 3px 0 3px 4px; }
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


def drive_of(mount):
    """What a mount is on, in words: "TEAM T253256GB · sdb1 · btrfs · SATA SSD" (lsblk; "" if it can't say)."""
    try:
        src = subprocess.run(["findmnt", "-no", "SOURCE", mount], capture_output=True, text=True,
                             timeout=5).stdout.strip().split("[")[0]
        tree = json.loads(subprocess.run(["lsblk", "-J", "-o", "NAME,MODEL,FSTYPE,ROTA,TRAN"], capture_output=True,
                                         text=True, timeout=5).stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    for disk in tree.get("blockdevices", []):
        for part in disk.get("children") or [disk]:
            if f"/dev/{part['name']}" == src:
                kind = "hard disk" if disk.get("rota") else "SSD"
                bus = (disk.get("tran") or "").upper()
                return " · ".join(x for x in ((disk.get("model") or "").strip(), part["name"], part.get("fstype") or "",
                                             f"{bus} {kind}".strip()) if x)
    return ""


def device_of(path):
    """The partition a path is on (/dev/sdb1), whichever btrfs subvolume holds it; a folder you can't look
    into (Docker's, root's) by its nearest parent you can."""
    while path:
        try:
            out = subprocess.run(["findmnt", "-no", "SOURCE", "-T", path], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        if out or path == "/":
            return out.split("[")[0]
        path = os.path.dirname(path)
    return ""


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
        self.all_timer = 0       # Clean all pressed once: the timer that disarms it
        self.cleaning = False    # Clean all at work
        self.pool = ThreadPoolExecutor(4)
        self.drives = {m: drive_of(m) for m, _ in DISKS}   # once: the disks don't change while it's open
        devs = {device_of(m): name.split(" (")[0] for m, name in DISKS if os.path.ismount(m)}
        self.disk_of = lambda p, cache={}: cache.setdefault(p, devs.get(device_of(p), ""))   # "SSD" / "HDD"

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
        disks = sorted({self.disk_of(p) for p in paths} - {""})
        row = box(False, 6, *([label(f"on the {' and '.join(disks)}", "item-disk")] if disks else []),
                  label("\uf07b", "item-where"), classes=("item-where",))
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
        self.disks = box(False, 12)
        self.disks.set_homogeneous(True)   # the SSD and the HDD side by side, as wide as each other
        self.total_l = label("", "total")
        self.total_note = label("", "dim")
        self.all_b = button("Clean all", lambda: self.clean_all(), "flat", "danger",
                            tooltip="every row at once (twice within 3 s); the package cache asks for your password")
        self.all_b.set_focusable(False)
        self.all_b.set_valign(Gtk.Align.CENTER)
        note = box(True, 2, self.total_l, self.total_note)
        note.set_hexpand(True)
        self.body = box(True, 0)
        page = box(True, 14, self.disks, box(False, 12, note, self.all_b, classes=("summary",)), scrolled(self.body))
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
            short, _, role = name.partition(" (")
            head = box(False, 8, label(short, "bold"), label(f"{role.rstrip(')')} · {mount}", "dim", ellipsize=True),
                       label(f"{pct:.0f}%", "bold", {"green": "green", "yellow": "amber", "red": "red"}[level(pct)]))
            card = box(True, 6, head, bar, label(f"{human(used)} used · {human(free)} free of {human(total)}", "sub"),
                       classes=("disk",))
            if self.drives.get(mount):
                card.append(label(self.drives[mount], "item-where", ellipsize=True))
            self.disks.append(card)

    def paint(self):
        clear(self.body)
        busy = any(s is None for s in self.sizes.values())
        total = sum(s for s in self.sizes.values() if s)
        failed = {k for k, rows in self.lists.items() if any(r[1] is None for r in rows)}   # couldn't measure
        todo = [it for it in self.items() if self.sizes.get(it[0]) != 0 or it[0] in failed]
        done = [it for it in self.items() if it not in todo]
        self.set_subtitle("measuring…" if busy else self.subtitle)
        self.total_l.set_text(f"{human(total)} can be freed" if total or busy else "Nothing to clean")
        self.total_l.remove_css_class("none")
        if not total and not busy:
            self.total_l.add_css_class("none")
        places = sum(1 for k, *_ in self.items() if self.sizes.get(k))
        self.total_note.set_text("cleaning…" if self.cleaning else "measuring the rest…" if busy else
                                 f"in {places} place{'s' * (places != 1)}: Clean each below, or Clean all"
                                 if places else "every place below is already empty")
        self.all_b.set_visible(bool(total) or busy)
        self.all_b.set_sensitive(bool(total) and not busy and not self.cleaning)
        biggest = max([s for s in self.sizes.values() if s] or [1])
        for key, title, what, how, paths in todo:
            self.body.append(self.item_row(key, title, what, how, paths, biggest))
            if key in self.open and key in ("node-modules", "cache"):
                for name, sub_paths, bytes_, note in self.lists.get(key) or []:
                    self.body.append(self.sub_row(key, name, sub_paths, bytes_, note))
        if done:
            self.body.append(rule_heading("Already clean", str(len(done)), spaced=True))
            for _key, title, *_ in done:
                self.body.append(box(False, 10, label("\uf00c", "green"), label(title, "dim", ellipsize=True),
                                     label("0 B", "dim"), classes=("done-row",)))

    def item_row(self, key, title, what, how, paths, biggest):
        size = self.sizes.get(key)
        unfolds = key in ("node-modules", "cache") and self.lists.get(key)
        fold = button("\uf078" if key in self.open else "\uf054", lambda key=key: self.toggle(key), "fold")
        if not unfolds:   # kept, unseen: every row's title starts at the same place
            fold.set_opacity(0)
            fold.set_sensitive(False)
        fold.set_focusable(False)
        where = self.where(paths)
        where.append(label(f"· {how}", "item-where"))
        text = box(True, 3, label(title, "item-title"), label(what, "dim", wrap=True), where)
        text.set_hexpand(True)
        size_l = label("measuring…" if size is None else human(size), "item-size", xalign=1.0)
        if size is not None and size < 50_000_000:
            size_l.add_css_class("small")
        share = Gtk.LevelBar(min_value=0, max_value=1, value=(size or 0) / biggest, css_classes=["share"])
        share.set_size_request(90, -1)
        sized = box(True, 6, size_l, share)
        sized.set_valign(Gtk.Align.CENTER)
        sure = self.armed and self.armed[0] == key and time.time() - self.armed[1] < 3
        clean = button("Click again" if sure else "Clean", lambda key=key: self.clean(key),
                       *(("armed",) if sure else ("flat", "danger")))
        clean.set_sensitive(bool(size) and not self.cleaning)
        clean.set_focusable(False)
        clean.set_valign(Gtk.Align.CENTER)
        return box(False, 14, fold, text, sized, clean, classes=("item",))

    def sub_row(self, key, name, paths, bytes_, note):
        k = (key, name)
        sure = self.armed and self.armed[0] == k and time.time() - self.armed[1] < 3
        b = button("Click again" if sure else "Delete", lambda k=k, paths=paths: self.clean_one(k, paths),
                   *(("armed",) if sure else ("flat", "danger")))
        b.set_focusable(False)
        b.set_sensitive(bool(paths) and not self.cleaning)
        folder = os.path.dirname(paths[0]) if key == "node-modules" and paths else (paths or [""])[0]
        o = button("Open", lambda f=folder: self.open_folder(f), "open", tooltip=folder.replace(HOME, "~"))
        o.set_focusable(False)
        o.set_sensitive(bool(folder))
        return box(False, 12, label(name, ellipsize=True), label(note, "dim"), label(human(bytes_), "dim", xalign=1.0),
                   o, b, classes=("sub-row",))

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
        if key == "pacman":
            self.clean_pacman()
            return
        self.say(f"cleaning {title}…", seconds=120)

        def work():
            ok, msg = self.run_clean(key)
            GLib.idle_add(lambda: (self.say(f"{title}: cleaned" if ok else f"{title}: {msg}", "ok" if ok else "bad"),
                                   self.measure(key)) and False)
        self.pool.submit(work)

    def clean_pacman(self):
        """Root: in the terminal popup, where sudo asks for the password."""
        script = ('printf "\\033[38;2;107;143;224mpackage cache\\033[0m old versions and uninstalled packages\\n\\n"; '
                  'sudo sh -c "paccache -rk1 && paccache -ruk0"; s=$?; '
                  'printf "\\n\\033[2m── done (%s) · Enter to close ──\\033[0m" $s; read _')
        subprocess.Popen(["uwsm", "app", "--", sys.executable, TERM, "package cache", "--", "sh", "-c", script],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        GLib.timeout_add_seconds(20, lambda: self.measure("pacman") or False)

    def run_clean(self, key):
        """Cleans one row (not the package cache), in a worker thread: (ok, why not)."""
        cmds = {"docker-cache": ["docker", "builder", "prune", "-af"], "docker-images": ["docker", "image", "prune", "-af"],
                "docker-stopped": ["docker", "container", "prune", "-f"]}
        if key == "trash":
            return self.empty_trash(), "couldn't delete all of it"
        if key in cmds:
            try:
                r = subprocess.run(cmds[key], capture_output=True, text=True, timeout=1800)
            except (OSError, subprocess.TimeoutExpired) as e:
                return False, str(e)
            return r.returncode == 0, ((r.stderr or r.stdout).strip().splitlines()[-1:] or [""])[0]
        paths = [p for _n, ps, _b, _note in self.lists.get(key, []) for p in (ps or [])]
        return self.remove(paths), "couldn't delete all of it"

    def clean_all(self):
        """Every row with something in it; the first press arms the button, the second within 3 s cleans."""
        if "armed" not in self.all_b.get_css_classes():
            self.all_b.add_css_class("armed")
            self.all_b.set_label("Click again")
            self.say("press again to clean everything listed")
            self.all_timer = GLib.timeout_add(3000, self.disarm_all)
            return
        GLib.source_remove(self.all_timer)
        self.disarm_all()
        titles = {k: t for k, t, *_ in self.items()}
        keys = [k for k in titles if self.sizes.get(k)]
        if "pacman" in keys:   # first: its password is asked while the rest is cleaned
            keys.remove("pacman")
            self.clean_pacman()
        self.cleaning = True
        self.paint()

        def work():
            failed = []
            for i, key in enumerate(keys, 1):
                GLib.idle_add(lambda t=titles[key], i=i: self.say(f"cleaning {t} ({i} of {len(keys)})…", seconds=1800)
                              and False)
                if not self.run_clean(key)[0]:
                    failed.append(titles[key])
            GLib.idle_add(self.cleaned_all, failed)
        self.pool.submit(work)

    def disarm_all(self):
        self.all_b.remove_css_class("armed")
        self.all_b.set_label("Clean all")
        self.all_timer = 0
        return False

    def cleaned_all(self, failed):
        self.cleaning = False
        self.say(f"couldn't clean: {', '.join(failed)}" if failed else "everything cleaned", "bad" if failed else "ok")
        self.measure_all()
        self.paint_disks()
        return False

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

    @staticmethod
    def empty_trash():
        """Empties the Trash itself: `gio trash --empty` needs gvfs, which this desktop doesn't have."""
        for sub in ("files", "info", "expunged"):
            folder = os.path.join(TRASH, sub)
            for name in os.listdir(folder) if os.path.isdir(folder) else []:
                p = os.path.join(folder, name)
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        return not os.listdir(os.path.join(TRASH, "files")) if os.path.isdir(os.path.join(TRASH, "files")) else True

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
