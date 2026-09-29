#!/usr/bin/env python3
# syncpanel.py — Code Sync panel (Tokyo Night): everything about the ~/code -> HDD backup.
# Opened by the sync icon in waybar (left). Reads codesync's state; edits its settings.json.
#
#   ↑↓ / click  select a setting       ←→ or - +  change it (applies immediately)
#   s  sync now     p  pause / resume     i  ignore list     l  log     t  old versions     q  close
import datetime, importlib.machinery, importlib.util, json, os, signal, subprocess, threading, time
from itertools import zip_longest
from fnmatch import fnmatch

from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, KEY, TRACK, GREEN, YELLOW, RED, CYAN,
                      MAGENTA, BOLD, RESET, clip, fit, frame, card, spread, header, section, hints, highlight,
                      visible_len)


def load_codesync():
    path = os.path.expanduser("~/.local/bin/codesync")
    loader = importlib.machinery.SourceFileLoader("codesync", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("codesync", loader))
    loader.exec_module(mod)
    return mod


cs = load_codesync()
human, ago = cs.human, cs.ago

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
        self.sel = 0
        self.flash, self.flash_at = "", 0
        self.folders, self.trash, self.measured = {}, (0, 0), 0
        self.rows = {}  # screen row -> setting index, for clicks
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
            time.sleep(120)

    def tick(self):
        self.state = cs.load_state()
        self.cfg = cs.settings()
        self.paused = os.path.exists(cs.PAUSED)
        self.pid = cs.service_pid()

    # ---- drawing ---------------------------------------------------------------------------------
    def left(self, w):
        s, rows = self.state, []
        st = s.get("status", "off") if self.pid else "off"
        color, dot, words = STATUS.get(st, STATUS["ok"])
        when = f"last sync {ago(s['last_sync'])}" if s.get("last_sync") else "never synced"
        rows.append(spread(f"{color}{dot}{RESET}  {FG}{BOLD}{words}{RESET}", f"{DIM}{when}{RESET}", w))
        if st == "error" and s.get("detail"):
            rows.append(f"   {RED}{fit(s['detail'], w - 3)}{RESET}")
        last = s.get("last")
        if last:
            rows.append(f"   {DIM}{last['reason']} · {last['changes']} changes · {human(last.get('bytes', 0))}"
                        f" · {last['took']}s{RESET}")
        rows.append("")

        rows.append(section("Backup", w))
        tot = s.get("totals", {})
        skipped = sum(v[3] for v in self.folders.values())
        try:
            ssd_free = human(os.statvfs(cs.SRC).f_bavail * os.statvfs(cs.SRC).f_frsize)
            hdd_free = human(os.statvfs(cs.MOUNT).f_bavail * os.statvfs(cs.MOUNT).f_frsize) \
                if os.path.ismount(cs.MOUNT) else "not mounted"
        except OSError:
            ssd_free = hdd_free = "?"
        kv = [
            ("Backed up", f"{tot.get('files', 0):,} files · {tot.get('dirs', 0):,} folders · "
                          f"{human(tot.get('size', 0))}"),
            ("Skipped", f"{human(skipped)} (ignore list)" if self.measured else "measuring…"),
            ("Old versions", f"{human(self.trash[0])} over {self.trash[1]} day(s) · kept "
                             f"{self.cfg['trash_days']} days" if self.measured else "measuring…"),
            ("Free space", f"SSD {ssd_free} · HDD {hdd_free}"),
            ("Watching", f"{s.get('watched', 0):,} folders"),
        ]
        rows += [f"{DIM}{k:<14}{RESET}{FG}{fit(v, w - 14)}{RESET}" for k, v in kv]
        rows.append("")

        rows.append(section("Folders", w, "files      size   skipped"))
        for name, (f, d, size, skip) in self.folders.items():
            rows.append(f"{FG}{fit(name, w - 30):<{w - 30}}{RESET}{f:>8,}{human(size):>11}{DIM}{human(skip):>11}{RESET}")
        if not self.folders:
            rows.append(f"{DIM}measuring…{RESET}")
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

    def right(self, w, top_row, room):
        rows = [section("Settings", w, "←→ change")]
        self.rows = {}
        for i, (key, label, _, fmt) in enumerate(SETTINGS):
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
        body_top = 3  # header (2) + blank row; settings rows are clickable from here
        L, R = self.left(lw), self.right(rw, body_top, room)
        body = [clip(l or "", lw) + " " * gap + (r or "") for l, r in zip_longest(L, R)]
        sub = f"{cs.SRC.replace(os.path.expanduser('~'), '~')} → {cs.DST}"
        head = header("\U000f04e6", "Code Sync", sub, width)
        if self.flash and time.time() - self.flash_at < 3:
            foot = f"{ACCENT}{self.flash}{RESET}"
        else:
            foot = hints([("s", "sync now"), ("p", "resume" if self.paused else "pause"), ("↑↓", "select"),
                          ("←→", "change"), ("i", "ignore list"), ("l", "log"), ("t", "old versions"),
                          ("q", "close")], width)
        return card(head, body, foot, rows, pad)

    # ---- actions ---------------------------------------------------------------------------------
    def say(self, msg):
        self.flash, self.flash_at = msg, time.time()

    def change(self, step):
        key, label, choices, fmt = SETTINGS[self.sel]
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

    def key(self, k):
        if k in ("q", "ESC"):
            return "quit"
        if k in ("UP", "k", "WHEELUP"):
            self.sel = (self.sel - 1) % len(SETTINGS)
        elif k in ("DOWN", "j", "WHEELDOWN"):
            self.sel = (self.sel + 1) % len(SETTINGS)
        elif k in ("LEFT", "-"):
            self.change(-1)
        elif k in ("RIGHT", "+", "="):
            self.change(+1)
        elif isinstance(k, tuple) and k[2] in self.rows:
            self.sel = self.rows[k[2]]
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
                self.say("the HDD isn't mounted")
                return
            os.makedirs(cs.TRASH, exist_ok=True)
            subprocess.Popen(["dolphin", cs.TRASH], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.tick()


if __name__ == "__main__":
    show(SyncPanel())
