#!/usr/bin/env python3
# updatesgui.py — Updates (Super + Ctrl + U): what pacman and paru would update, what to watch out for, and
# the snapshots to go back to. Running it again closes it.
#
#   Updates    the repo packages (checkupdates: a copy of the package lists, nothing locked) and the AUR
#              ones (paru -Qua); the ones worth a look first, in amber with why (the kernel, the graphics
#              stack, Hyprland, the core, Python's version…); Arch's news since the last full upgrade above
#              (a manual intervention is announced there). Update all runs `paru -Syu` in the terminal
#              popup (termgui.py), where sudo asks for the password.
#   Snapshots  snap-pac takes a snapshot of / before and after every pacman run, and grub-btrfs lists them
#              in the boot menu: boot one from GRUB's “Arch Linux snapshots” submenu, then make it stay with
#              btrfs-assistant (Restore). Listing them needs system/root-setup.sh's snapper step once.
#
# Nothing runs in the background: the lists are made when the panel opens (F5 again).
import datetime, json, os, re, subprocess, sys, threading, time
import xml.etree.ElementTree as ET
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, run, scrolled

TERM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "termgui.py")
NEWS = "https://archlinux.org/feeds/news/"
PACMAN_LOG = "/var/log/pacman.log"
# packages worth a look before updating: (name pattern, why)
WATCH = [(r"linux(-cachyos)?(-lts)?(-headers)?", "the kernel: in use after a reboot (the LTS one stays bootable)"),
         (r"nvidia.*", "the graphics driver: reboot after"),
         (r"(lib32-)?(mesa|vulkan-.+)", "the graphics stack: reboot after if the screen misbehaves"),
         (r"hyprland|aquamarine|hypr(utils|lang|graphics|cursor|wayland-scanner)|xdg-desktop-portal-hyprland",
          "Hyprland: a new version can change the config's syntax (hyprctl configerrors after, then log out and in)"),
         (r"systemd|glibc|gcc-libs|openssl", "the core system: reboot after"),
         (r"grub|mkinitcpio|limine|dracut", "the boot loader / initramfs"),
         (r"gtk4|glib2|python-gobject|webkitgtk.*|vte4", "the panels run on it: reopen them after"),
         (r"tmux", "agentmux runs on it: agentmux reload after"),
         (r"waybar", "the bar: systemctl --user restart waybar after"),
         (r"docker|containerd", "Docker: the containers restart")]

CSS = """
.upd-list > row { padding: 4px 12px; }
.upd-name { min-width: 240px; }
.upd-ver { color: #565f89; }
.upd-new { color: #c0caf5; }
.upd-why { color: #e0af68; }
.upd-aur { color: #bb9af7; }
.news { padding: 8px 12px; border: 1px solid alpha(#3b4261, .7); }
.news-title { color: #e0af68; font-weight: 700; }
.section-head { color: #6b8fe0; font-weight: 700; margin: 12px 0 4px 0; }
.snap-list > row { padding: 5px 12px; }
.snap-num { color: #565f89; min-width: 50px; }
"""


def last_upgrade():
    """When the last full upgrade (pacman -Syu) started, as a datetime, or None."""
    try:
        with open(PACMAN_LOG, errors="replace") as f:
            stamps = [l[1:25] for l in f if "starting full system upgrade" in l]
        return datetime.datetime.fromisoformat(stamps[-1]) if stamps else None
    except (OSError, ValueError):
        return None


def pending():
    """[(name, old, new, aur)] from checkupdates (repos) and paru -Qua (AUR)."""
    out = []
    for cmd, aur in ((["checkupdates"], False), (["paru", "-Qua"], True)):
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in text.splitlines():
            m = re.match(r"(\S+) (\S+) -> (\S+)", line)
            if m:
                out.append((m.group(1), m.group(2), m.group(3), aur))
    return out


def why(name, old, new):
    """Why a package is worth a look, or ""."""
    if name == "python":
        mm = lambda v: ".".join(v.split(".")[:2])
        return f"Python {mm(old)} → {mm(new)}: what pip installed for {mm(old)} won't load" if mm(old) != mm(new) else ""
    return next((reason for pat, reason in WATCH if re.fullmatch(pat, name)), "")


def news():
    """[(title, link, datetime)] of Arch's news feed, newest first ([] offline)."""
    try:
        root = ET.fromstring(urlopen(NEWS, timeout=10).read())
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        try:
            when = datetime.datetime.strptime(it.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %z")
        except (TypeError, ValueError):
            continue
        items.append((it.findtext("title") or "", it.findtext("link") or "", when))
    return items


def snapshots():
    """(rows, problem): snapper's root snapshots newest first, a pre / post pair as one row: {number, pre,
    date, what}; problem when the user can't list them (snapper's ALLOW_USERS)."""
    try:
        r = subprocess.run(["snapper", "--jsonout", "-c", "root", "list"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return [], "snapper isn't installed"
    if r.returncode:
        return [], "Run system/root-setup.sh once (pkexec) to let you list them: " + (r.stderr or r.stdout).strip()
    snaps = json.loads(r.stdout or "{}").get("root", [])
    pre = {s["number"]: s for s in snaps if s.get("type") == "pre"}
    rows, paired = [], set()
    for s in snaps:
        if s["number"] == 0:
            continue
        if s.get("type") == "post" and s.get("pre-number") in pre:
            p = pre[s["pre-number"]]
            paired.add(p["number"])
            rows.append({"number": f"{p['number']}–{s['number']}", "date": s.get("date", ""),
                         "what": p.get("description") or s.get("description") or ""})
        elif s.get("type") != "pre":
            rows.append({"number": str(s["number"]), "date": s.get("date", ""), "what": s.get("description", "")})
    rows += [{"number": str(n), "date": p.get("date", ""), "what": (p.get("description") or "") + " (no after)"}
             for n, p in pre.items() if n not in paired]
    return sorted(rows, key=lambda r: r["date"], reverse=True), ""


class Updates(View):
    title, subtitle = "Updates", "pacman, paru, snapshots"
    icon = "\U000f06b0"   # nf-md-update
    interval = 0
    css = CSS
    hints = [("Tab", "updates / snapshots"), ("u", "update all"), ("F5", "check again"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.tab = "updates"
        self.updates, self.items, self.problem = None, [], ""

    def build(self):
        self.tabs = {}
        bar = box(False, 0, classes=("tabs",))
        for key, text in (("updates", "Updates"), ("snapshots", "Snapshots")):
            b = button(text, lambda key=key: self.switch(key), "tab")
            b.set_focusable(False)
            self.tabs[key] = b
            bar.append(b)
        self.stack = Gtk.Stack()
        self.upd_page = box(True, 8)
        self.snap_page = box(True, 8)
        self.stack.add_named(scrolled(self.upd_page), "updates")
        self.stack.add_named(scrolled(self.snap_page), "snapshots")
        self.actions = box(False, 8)
        self.actions.set_margin_top(10)
        page = box(True, 10, bar, self.stack, self.actions)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(16)
        self.switch("updates")
        self.check()
        return page

    def switch(self, tab):
        self.tab = tab
        self.stack.set_visible_child_name(tab)
        for k, b in self.tabs.items():
            (b.add_css_class if k == tab else b.remove_css_class)("on")
        self.paint_actions()

    # ---- checking ----------------------------------------------------------------------------------
    def check(self):
        self.updates = None
        clear(self.upd_page)
        self.upd_page.append(label("checking pacman and the AUR…", "dim"))
        clear(self.snap_page)
        self.snap_page.append(label("reading the snapshots…", "dim"))
        self.set_subtitle("checking…")
        self.paint_actions()

        def work():
            got = (pending(), news(), last_upgrade(), snapshots())
            GLib.idle_add(self.checked, *got)
        threading.Thread(target=work, daemon=True).start()

    def checked(self, updates, items, last, snaps):
        self.updates = updates
        self.paint_updates(updates, items, last)
        self.paint_snapshots(*snaps)
        self.paint_actions()
        return False

    # ---- drawing -----------------------------------------------------------------------------------
    def paint_updates(self, updates, items, last):
        clear(self.upd_page)
        since = (datetime.datetime.now(datetime.timezone.utc) - last).days if last else None
        watch = [(u, why(*u[:3])) for u in updates]
        flagged = [(u, w) for u, w in watch if w]
        self.set_subtitle(f"{len(updates)} updates · {len(flagged)} to watch" +
                          (f" · last full upgrade {since} days ago" if since is not None else ""))
        fresh = [n for n in items if last and n[2] > last] if last else items[:3]
        for title, link, when in fresh[:4]:
            t = label(title, "news-title", wrap=True)
            more = button("Read", lambda link=link: self.open(link), "flat")
            self.upd_page.append(box(True, 2, box(False, 8, t, more), label(f"Arch news · {when:%d %b %Y}", "dim"),
                                     classes=("news",)))
            t.set_hexpand(True)
        if not updates:
            self.upd_page.append(label("Everything is up to date." if updates == [] else "", "dim"))
            return
        if flagged:
            self.upd_page.append(label("Worth a look", "section-head"))
            self.upd_page.append(self.package_list(flagged))
        rest = [(u, w) for u, w in watch if not w]
        if rest:
            self.upd_page.append(label(f"The rest ({len(rest)})", "section-head"))
            self.upd_page.append(self.package_list(rest))

    def package_list(self, rows):
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("upd-list")
        for (name, old, new, aur), reason in rows:
            line = box(False, 12, label(name, "upd-name"), label(old, "upd-ver"), label("→", "upd-ver"),
                       label(new, "upd-new"), *([label("AUR", "upd-aur")] if aur else []))
            r = box(True, 1, line, *([label(reason, "upd-why", wrap=True)] if reason else []))
            lst.append(Gtk.ListBoxRow(child=r, activatable=False))
        return lst

    def paint_snapshots(self, rows, problem):
        clear(self.snap_page)
        self.snap_page.append(label(
            "pacman takes a snapshot of the system before and after every run (snap-pac), and the boot menu lists "
            "them (grub-btrfs). To go back after a bad update: reboot, pick “Arch Linux snapshots” in GRUB and boot the "
            "one from before it (read-only, to check it works), then open btrfs-assistant → Snapper → Restore it "
            "and reboot. Your home folder isn't in them: only the system goes back.", "dim", wrap=True))
        if problem:
            self.snap_page.append(label(problem, "upd-why", wrap=True))
            return
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("snap-list")
        for r in rows[:60]:
            lst.append(Gtk.ListBoxRow(child=box(False, 12, label(r["number"], "snap-num"), label(r["date"][:16], "dim"),
                                                label(r["what"], ellipsize=True)), activatable=False))
        self.snap_page.append(lst)

    def paint_actions(self):
        clear(self.actions)
        if self.tab == "updates":
            up = button("Update all", self.update_all, "primary", tooltip="u: paru -Syu in a terminal popup")
            up.set_sensitive(bool(self.updates))
            self.actions.append(up)
        else:
            self.actions.append(button("Open btrfs-assistant", self.open_assistant, "primary",
                                       tooltip="browse and restore snapshots (asks for your password)"))
        self.actions.append(button("Check again", self.check, "flat", tooltip="F5"))

    # ---- actions -----------------------------------------------------------------------------------
    def open(self, link):
        subprocess.Popen(["xdg-open", link], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def update_all(self):
        """paru -Syu in the terminal popup (sudo asks there); this panel closes so it's the one on screen."""
        if not self.updates:
            return
        script = ('printf "\\033[38;2;107;143;224mupdate\\033[0m \\033[2m%s\\033[0m\\n\\n" '
                  '"snapshots before and after: Super + Ctrl + U → Snapshots"; paru -Syu; s=$?; '
                  'printf "\\n\\033[2m── done (%s) · then: hyprctl configerrors, and reboot if the kernel or drivers '
                  'changed · Enter to close ──\\033[0m" $s; read _')
        subprocess.Popen(["uwsm", "app", "--", sys.executable, TERM, "update", "--", "sh", "-c", script],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def open_assistant(self):
        subprocess.Popen(["uwsm", "app", "--", "btrfs-assistant-launcher"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Tab:
            self.switch("snapshots" if self.tab == "updates" else "updates")
        elif keyval == Gdk.KEY_F5:
            self.check()
        elif keyval == Gdk.KEY_u and self.tab == "updates":
            self.update_all()
        else:
            return False
        return True


def make():
    return Updates()


def main():
    run(make(), "panels.updates", (900, 640))


if __name__ == "__main__":
    main()
