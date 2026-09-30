#!/usr/bin/env python3
# syncpanel.py — Code Sync panel (Tokyo Night): everything about the ~/code -> HDD backup.
# Opened by the sync icon in waybar (left). Reads codesync's state; edits its settings.json and,
# through codesync, this PC's two folders (machine.json).
#
#   ↑↓ / click  select a folder or setting    ⏎ / click  change a folder (opens a folder dialog)
#   ←→ or - +   change a setting (applies immediately)
#   s  sync now     p  pause / resume     r  restore the backup into an empty code folder
#   i  ignore list     l  log     t  old versions     q  close
import datetime, importlib.machinery, importlib.util, json, os, signal, subprocess, textwrap, threading, time
from itertools import zip_longest
from fnmatch import fnmatch

from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, KEY, TRACK, GREEN, YELLOW, RED, CYAN,
                      MAGENTA, BOLD, RESET, clip, fit, frame, card, spread, header, section, hints, highlight,
                      visible_len)

CODESYNC = os.path.expanduser("~/.local/bin/codesync")
FOLDERPICK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "folderpick.py")


def load_codesync():
    """codesync as a module. Loaded again after the folders change: SRC, DST… are read at import."""
    loader = importlib.machinery.SourceFileLoader("codesync", CODESYNC)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("codesync", loader))
    loader.exec_module(mod)
    return mod


cs = load_codesync()
human, ago = cs.human, cs.ago


def tilde(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def fit_path(p, n):
    """A path cut to n columns from the left, so the folder's own name stays visible."""
    p = tilde(p)
    return p if len(p) <= n else "…" + p[len(p) - max(n - 1, 0):]


# the two folders, shown above the settings: key, label, what it's for
FOLDERS = [
    ("src", "Code folder", "where you work"),
    ("dst", "Backup", "where copies go"),
]

# the settings the panel can change: key, label, choices (seconds or days), unit formatter
def secs(n):
    return f"{n} s" if n < 60 else f"{n // 60} min" if n < 3600 else f"{n // 3600} h"


SETTINGS = [
    ("quiet", "Sync when changes stop for", [1, 2, 3, 5, 10, 15, 30, 60], secs),
    ("max_wait", "…but wait at most", [10, 15, 30, 60, 120, 300, 600], secs),
    ("full_every", "Full check every", [300, 600, 900, 1800, 3600, 7200, 21600], secs),
    ("trash_days", "Keep old versions", [3, 7, 14, 30, 60, 90, 180, 365], lambda n: f"{n} days"),
]
STATUS = {  # colour, dot, words
    "ok": (GREEN, "●", "Up to date"),
    "pending": (YELLOW, "●", "Changes waiting"),
    "syncing": (CYAN, "◌", "Syncing…"),
    "paused": (DIM, "‖", "Paused"),
    "error": (RED, "●", "Problem"),
    "restore": (YELLOW, "!", "Restore needed"),
    "restoring": (CYAN, "◌", "Restoring…"),
    "off": (RED, "○", "Service not running"),
}


def tree_size(top, skip_dirs, skip_files):
    """(files, dirs, bytes backed up, bytes skipped) under top, using the ignore list."""
    files = dirs = size = skipped = 0
    stack = [(top, False)]
    while stack:
        d, ignored = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        ign = ignored or any(fnmatch(e.name, p) for p in skip_dirs)
                        if not ign:
                            dirs += 1
                        stack.append((e.path, ign))
                    elif e.is_file(follow_symlinks=False):
                        n = e.stat(follow_symlinks=False).st_size
                        if ignored or any(fnmatch(e.name, p) for p in skip_files):
                            skipped += n
                        else:
                            files, size = files + 1, size + n
                except OSError:
                    pass
    return files, dirs, size, skipped


class SyncPanel(Panel):
    interval = 1.0

    def __init__(self):
        self.sel = 0  # index into FOLDERS + SETTINGS
        self.flash, self.flash_at, self.flash_color = "", 0, ACCENT
        self.folders, self.trash, self.measured = {}, (0, 0), 0
        self.rows = {}  # screen row -> selectable index, for clicks
        self.right_x = 0  # first screen column of the right column: clicks left of it select nothing
        self.busy = None       # what a background step is doing ("waiting for the folder dialog…")
        self.proposal = None   # new folders waiting for y / n: dict(src, dst, which, warning, preview, restore)
        self.remeasure = threading.Event()
        self.tick()
        threading.Thread(target=self.measure, daemon=True).start()

    # slow numbers (walks the disks) in the background, refreshed every 2 minutes
    def measure(self):
        while True:
            dirs, files = cs.ignore_patterns()
            out = {}
            try:
                for name in sorted(os.listdir(cs.SRC)):
                    p = os.path.join(cs.SRC, name)
                    if os.path.isdir(p) and not any(fnmatch(name, x) for x in dirs):
                        out[name] = tree_size(p, dirs, files)
            except OSError:
                pass
            self.folders = out
            size = days = 0
            try:
                for day in os.listdir(cs.TRASH):
                    days += 1
                    size += tree_size(os.path.join(cs.TRASH, day), [], [])[2]
            except OSError:
                pass
            self.trash, self.measured = (size, days), time.time()
            self.remeasure.wait(120)
            self.remeasure.clear()

    def tick(self):
        self.state = cs.load_state()
        self.cfg = cs.settings()
        self.paused = os.path.exists(cs.PAUSED)
        self.pid = cs.service_pid()

    def status_key(self):
        st = self.state.get("status", "off")
        if st == "restoring" and cs.restoring():
            return st  # runs outside the service, so it shows even when the service is off
        return st if self.pid else "off"

    # ---- drawing ---------------------------------------------------------------------------------
    def left(self, w):
        s, rows = self.state, []
        st = self.status_key()
        color, dot, words = STATUS.get(st, STATUS["ok"])
        when = f"last sync {ago(s['last_sync'])}" if s.get("last_sync") else "never synced"
        rows.append(spread(f"{color}{dot}{RESET}  {FG}{BOLD}{words}{RESET}", f"{DIM}{when}{RESET}", w))
        if st in ("error", "restore", "restoring") and s.get("detail"):
            rows.append(f"   {color}{fit(s['detail'], w - 3)}{RESET}")
        if st == "restore":
            rows.append(f"   {FG}press {KEY}r{RESET}{FG} to copy the backup into the code folder{RESET}")
            rows.append(f"   {DIM}{fit('nothing is deleted; syncing resumes when it’s done', w - 3)}{RESET}")
        last = s.get("last")
        if last and st not in ("restore", "restoring"):
            rows.append(f"   {DIM}{last['reason']} · {last['changes']} changes · {human(last.get('bytes', 0))}"
                        f" · {last['took']}s{RESET}")
        rows.append("")

        rows.append(section("Backup", w))
        tot = s.get("totals") or {}
        skipped = sum(v[3] for v in self.folders.values())
        try:
            ssd_free = human(os.statvfs(cs.SRC).f_bavail * os.statvfs(cs.SRC).f_frsize)
        except OSError:
            ssd_free = "?"
        try:
            hdd_free = human(os.statvfs(cs.MOUNT).f_bavail * os.statvfs(cs.MOUNT).f_frsize) \
                if os.path.ismount(cs.MOUNT) else "not mounted"
        except OSError:
            hdd_free = "?"
        kv = [
            ("Backed up", f"{tot.get('files', 0):,} files · {tot.get('dirs', 0):,} folders · "
                          f"{human(tot.get('size', 0))}"),
            ("Skipped", f"{human(skipped)} (ignore list)" if self.measured else "measuring…"),
            ("Old versions", f"{human(self.trash[0])} over {self.trash[1]} day(s) · kept "
                             f"{self.cfg['trash_days']} days" if self.measured else "measuring…"),
            ("Free space", f"code {ssd_free} · backup {hdd_free}"),
            ("Watching", f"{s.get('watched', 0):,} folders"),
        ]
        rows += [f"{DIM}{k:<14}{RESET}{FG}{fit(v, w - 14)}{RESET}" for k, v in kv]
        rows.append("")

        rows.append(section("Folders", w, "files      size   skipped"))
        for name, (f, d, size, skip) in self.folders.items():
            rows.append(f"{FG}{fit(name, w - 30):<{w - 30}}{RESET}{f:>8,}{human(size):>11}{DIM}{human(skip):>11}{RESET}")
        if not self.folders:
            rows.append(f"{DIM}{'measuring…' if not self.measured else 'the code folder is empty'}{RESET}")
        rows.append("")

        t = s.get("today", {})
        if t.get("date") != datetime.date.today().isoformat():
            t = {}
        rows.append(section("Today", w))
        rows.append(f"{FG}{t.get('syncs', 0)}{RESET} {DIM}syncs ·{RESET} {GREEN}+{t.get('added', 0)}{RESET} "
                    f"{DIM}new ·{RESET} {YELLOW}~{t.get('updated', 0)}{RESET} {DIM}changed ·{RESET} "
                    f"{RED}-{t.get('deleted', 0)}{RESET} {DIM}deleted ·{RESET} "
                    f"{FG}{human(t.get('bytes', 0))}{RESET} {DIM}copied{RESET}")
        return rows

    def confirm_rows(self, w):
        """The y / n question after a folder was picked."""
        p = self.proposal
        new = p["src"] if p["which"] == "src" else p["dst"]
        what = "code folder" if p["which"] == "src" else "backup"
        rows = [section("Change folder", w, "y apply · n cancel"),
                f"{FG}Use {BOLD}{fit_path(new, w - 24)}{RESET}{FG} as the {what}?{RESET}"]
        pv = p["preview"]
        if p["restore"]:
            rows.append(f"{YELLOW}{fit('That code folder is empty and the backup is not.', w)}{RESET}")
            rows.append(f"{DIM}{fit('y saves, then copies the backup into it.', w)}{RESET}")
        elif pv is None:
            rows.append(f"{DIM}comparing the folders…{RESET}")
        elif pv.get("error"):
            rows.append(f"{RED}{fit('can’t compare: ' + pv['error'], w)}{RESET}")
        else:
            rows.append(f"{DIM}backup:{RESET} {GREEN}+{pv['new']:,}{RESET} {DIM}new ·{RESET} "
                        f"{YELLOW}~{pv['updated']:,}{RESET} {DIM}changed ·{RESET} "
                        f"{RED}−{pv['removed']:,}{RESET} {DIM}to old versions{RESET}")
            if pv["removed"]:
                gone = f"{pv['removed']:,} backed-up files are not in that code folder;"
                kept = f"they move to old versions, kept {self.cfg['trash_days']} days."
                rows.append(f"{YELLOW}{fit(gone, w)}{RESET}")
                rows.append(f"{DIM}{fit(kept, w)}{RESET}")
        if p["warning"]:
            rows += [f"{YELLOW}{line}{RESET}" for line in textwrap.wrap("! " + p["warning"], w)[:3]]
        rows.append("")
        return rows

    def right(self, w, top_row, room):
        rows = self.confirm_rows(w) if self.proposal else []
        self.rows = {}

        rows.append(section("Folders", w, "⏎ change"))
        for i, (key, label, role) in enumerate(FOLDERS):
            path = cs.SRC if key == "src" else cs.DST
            left = f"{FG if i == self.sel else DIM}{label}{RESET} {DIM}· {role}{RESET}"
            row = spread(left, f"{FG}{BOLD}{fit_path(path, max(w - visible_len(left) - 2, 8))}{RESET}", w)
            self.rows[top_row + len(rows)] = i
            rows.append(highlight(row, w) if i == self.sel else row)
        if self.busy:
            rows.append(f"{CYAN}{self.busy}{RESET}")
        rows.append("")

        rows.append(section("Settings", w, "←→ change"))
        for j, (key, label, _, fmt) in enumerate(SETTINGS):
            i = len(FOLDERS) + j
            val = f"{ACCENT}‹{RESET} {FG}{BOLD}{fmt(self.cfg[key])}{RESET} {ACCENT}›{RESET}"
            row = spread(f"{FG if i == self.sel else DIM}{label}{RESET}", val, w)
            self.rows[top_row + len(rows)] = i
            rows.append(highlight(row, w) if i == self.sel else row)
        full = self.state.get("last_full")
        if full and not self.paused:
            rows.append(f"{DIM}next full check in {secs(max(int(full + self.cfg['full_every'] - time.time()), 0))}{RESET}")
        rows.append("")

        recent = self.state.get("recent", [])
        rows.append(section("Recent changes", w, f"{len(recent)} kept"))
        colors = {"+": GREEN, "~": YELLOW, "-": RED}
        for t, op, path in recent[:max(room - len(rows), 0)]:
            stamp = time.strftime("%H:%M:%S", time.localtime(t))
            rows.append(f"{DIM}{stamp}{RESET}  {colors.get(op, FG)}{op}{RESET} {FG}{fit(path, w - 12)}{RESET}")
        if not recent:
            rows.append(f"{DIM}nothing yet{RESET}")
        return rows

    def draw(self, w, h):
        cols, rows, width, pad = frame(124)
        room = max(rows - 5, 0)
        gap = 4
        lw = (width - gap) * 11 // 20
        rw = width - gap - lw
        body_top = 3  # header (2) + blank row; folder and settings rows are clickable from here
        self.right_x = len(pad) + lw + gap
        L, R = self.left(lw), self.right(rw, body_top, room)
        body = [clip(l or "", lw) + " " * gap + (r or "") for l, r in zip_longest(L, R)]
        room_sub = max(width - len("Code Sync") - 8, 20) // 2
        sub = f"{fit_path(cs.SRC, room_sub)} → {fit_path(cs.DST, room_sub)}"
        head = header("\U000f04e6", "Code Sync", sub, width)
        if self.flash and time.time() - self.flash_at < 4:
            foot = f"{self.flash_color}{fit(self.flash, width)}{RESET}"
        elif self.proposal:
            foot = hints([("y", "apply"), ("n", "cancel")], width)
        else:
            pairs = [("s", "sync now"), ("p", "resume" if self.paused else "pause"), ("↑↓", "select"),
                     ("⏎", "folder"), ("←→", "setting")]
            if self.status_key() == "restore":
                pairs.insert(0, ("r", "restore"))
            pairs += [("i", "ignore list"), ("l", "log"), ("t", "old versions"), ("q", "close")]
            foot = hints(pairs, width)
        return card(head, body, foot, rows, pad)

    # ---- actions ---------------------------------------------------------------------------------
    def say(self, msg, color=ACCENT):
        self.flash, self.flash_at, self.flash_color = msg, time.time(), color

    def change(self, step):
        if self.sel < len(FOLDERS):
            return
        key, label, choices, fmt = SETTINGS[self.sel - len(FOLDERS)]
        cur = self.cfg[key]
        idx = min(range(len(choices)), key=lambda i: abs(choices[i] - cur))
        new = choices[max(0, min(len(choices) - 1, idx + step))]
        cfg = dict(self.cfg, **{key: new})
        with open(cs.SETTINGS, "w") as f:  # in place: the file is a symlink into ~/dotfiles
            json.dump(cfg, f, indent=2)
            f.write("\n")
        if self.pid:
            os.kill(self.pid, signal.SIGUSR2)
        self.tick()
        self.say(f"{label.rstrip('…').strip()}: {fmt(new)}")

    def choose(self, which):
        """Folder dialog, checks and a dry-run preview, in the background; ends in self.proposal."""
        if self.busy or self.proposal:
            return
        cur = cs.SRC if which == "src" else cs.DST
        title = "Code folder: where you work" if which == "src" else "Backup folder: where copies go"
        self.busy = "waiting for the folder dialog…"

        def work():
            try:
                r = subprocess.run([FOLDERPICK, cur, title], capture_output=True, text=True)
                picked = r.stdout.strip()
                if r.returncode != 0 or not picked:
                    return  # cancelled
                src, dst = (picked, cs.DST) if which == "src" else (cs.SRC, picked)
                if os.path.realpath(src) == os.path.realpath(cs.SRC) and \
                        os.path.realpath(dst) == os.path.realpath(cs.DST):
                    self.say("that’s the folder already in use")
                    return
                error, warning = cs.check_folders(src, dst)
                if error:
                    self.say(error, RED)
                    return
                restore = cs.needs_restore(src, dst)
                self.proposal = dict(which=which, src=src, dst=dst, warning=warning, preview=None,
                                     restore=restore)
                if not restore:
                    self.busy = "comparing the folders…"
                    self.proposal["preview"] = cs.preview(src, dst)
            except Exception as e:  # a background failure must not leave the panel stuck
                self.say(f"couldn’t change the folder: {e}", RED)
            finally:
                self.busy = None

        threading.Thread(target=work, daemon=True).start()

    def apply(self):
        global cs
        p, self.proposal = self.proposal, None
        cs.save_machine(p["src"], p["dst"])  # also restarts the service on the new folders
        cs = load_codesync()
        self.folders, self.measured = {}, 0
        self.remeasure.set()
        self.tick()
        if p["restore"]:
            self.restore()
        elif not cs.service_pid():
            self.say("folders saved — start the backup service with: codesync enable")
        else:
            self.say(f"now backing up {tilde(cs.SRC)} → {tilde(cs.DST)}", GREEN)

    def restore(self):
        if cs.restoring():
            self.say("a restore is already running")
            return
        if not cs.needs_restore():
            self.say("nothing to restore: the code folder isn’t empty")
            return
        subprocess.Popen([CODESYNC, "restore"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.say("restoring the backup into the code folder — syncing resumes when it’s done")

    def key(self, k):
        if self.proposal:
            if k in ("y", "Y") and not (self.proposal["preview"] or {}).get("error") and \
                    (self.proposal["restore"] or self.proposal["preview"] is not None):
                self.apply()
            elif k in ("n", "N", "ESC", "q"):
                self.proposal = None
                self.say("not changed")
            return
        n = len(FOLDERS) + len(SETTINGS)
        if k in ("q", "ESC"):
            return "quit"
        if k in ("UP", "k", "WHEELUP"):
            self.sel = (self.sel - 1) % n
        elif k in ("DOWN", "j", "WHEELDOWN"):
            self.sel = (self.sel + 1) % n
        elif k in ("LEFT", "-"):
            self.change(-1)
        elif k in ("RIGHT", "+", "="):
            self.change(+1)
        elif k == "ENTER" and self.sel < len(FOLDERS):
            self.choose(FOLDERS[self.sel][0])
        elif isinstance(k, tuple) and k[2] in self.rows and k[1] >= self.right_x:
            self.sel = self.rows[k[2]]
            if self.sel < len(FOLDERS):
                self.choose(FOLDERS[self.sel][0])  # a folder row works like a button
        elif k == "r":
            self.restore()
        elif k == "s":
            if self.pid:
                os.kill(self.pid, signal.SIGUSR1)
                self.say("syncing now…")
            else:
                self.say("the service isn't running — start it with: codesync enable")
        elif k == "p":
            if self.paused:
                if os.path.exists(cs.PAUSED):
                    os.remove(cs.PAUSED)
            else:
                open(cs.PAUSED, "w").close()
            if self.pid:
                os.kill(self.pid, signal.SIGUSR2)
                if self.paused:
                    os.kill(self.pid, signal.SIGUSR1)  # resuming: catch up now
            self.say("resumed" if self.paused else "paused — changes will sync when resumed")
        elif k == "i":
            subprocess.Popen(["gnome-text-editor", cs.IGNORE], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say("ignore list opened — changes apply on the next sync")
        elif k == "l":
            suspend(["sh", "-c", "journalctl --user -u codesync -o cat -n 500 --no-pager | less -R +G"])
        elif k == "t":
            if not os.path.ismount(cs.MOUNT):
                self.say("the backup drive isn't mounted")
                return
            os.makedirs(cs.TRASH, exist_ok=True)
            subprocess.Popen(["dolphin", cs.TRASH], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.tick()


if __name__ == "__main__":
    show(SyncPanel())
