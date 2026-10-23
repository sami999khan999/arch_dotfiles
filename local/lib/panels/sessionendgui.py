#!/usr/bin/env python3
# sessionendgui.py poweroff|reboot|logout — Log out, Restart and Shutdown, shown as they happen: every app
# is closed the way you would close it, then the agent threads end, then the system goes down. The power
# menu (powergui.py) opens it.
#
# Going straight to `systemctl poweroff` leaves systemd to SIGTERM / SIGKILL whatever is open: Chrome comes
# back with "didn't shut down correctly", an editor's unsaved work is at the mercy of a signal. Here every
# window is closed first, as Super + W would; an app with several windows (Chrome, VS Code) is told to quit
# instead (SIGTERM, which Chromium and Electron treat as File → Quit), so it keeps all of them for next time.
# Each app is a row: closing…, closed (how long it took), or still open after WAIT s (likely asking to save:
# it's focused, the panel beside it; Show / Force quit per app, F for all). Nothing goes down while one is
# open; once the last one closes it carries on by itself. Then agentmux quits (its threads come back next
# time), and the rest is systemd's: listed (your services, Docker's containers), stopped gracefully by it.
# What was open is saved first (sessionlib.save): after the next login, restoregui.py offers to open it again.
# Esc (or closing the panel) cancels: what's closed stays closed. No backdrop: the apps closing, and a save dialog, stay in view.
import os, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, View, box, button, hypr_events, hypr_socket, label, rule_heading, run, scrolled
from hyprsock import query
import sessionlib

ACTIONS = {"poweroff": ("Shutdown", "shutting down", "\U000f0425", ["systemctl", "poweroff"]),
           "reboot": ("Restart", "restarting", "\U000f0709", ["systemctl", "reboot"]),
           "logout": ("Log out", "logging out", "\U000f0343", ["uwsm", "stop"])}
WAIT = 15   # seconds the apps get to close (Chrome and VS Code take 1–3)
AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
USER_UNITS = os.path.expanduser("~/.config/systemd/user/")
CSS = """
.end-row { padding: 5px 2px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.end-glyph { min-width: 18px; }
.end-row button { padding: 0 8px; min-height: 0; }
"""
# a row's state: glyph, its colour class, the text at the right
STATES = {"wait": ("\U000f0150", "dim", "waiting"),         # nf-md-clock_outline
          "busy": ("\U000f0453", "amber", "closing…"),       # nf-md-refresh
          "done": ("\U000f012c", "green", "closed"),          # nf-md-check
          "stuck": ("\U000f0026", "red", "still open"),       # nf-md-alert
          "next": ("\U000f0142", "dim", "systemd stops it")}  # nf-md-chevron_right


def windows():
    """Every app window but this panel's (and its backdrop's): Hyprland's clients."""
    return [c for c in query("clients") or [] if c.get("mapped", True) and c.get("pid", -1) > 0
            and c["pid"] != os.getpid()]


def name_of(w):
    """The app's name: its class, or the title of this repo's own windows (sami.docker → Docker)."""
    if w["class"].startswith("sami.") and w["title"]:
        return w["title"]
    return w["class"] or w["title"] or f"pid {w['pid']}"


class Row:
    """One line: a glyph, a name, dim details, a state at the right, buttons when stuck."""

    def __init__(self, name, detail="", state="wait"):
        self.glyph = label("", "end-glyph")
        self.status = label("", "dim", xalign=1.0)
        self.buttons = box(False, 6)
        self.widget = box(False, 10, self.glyph, label(name, "sub"), label(detail, "dim", ellipsize=True),
                          self.status, self.buttons, classes=("end-row",))
        self.set(state)

    def set(self, state, text=None):
        glyph, colour, said = STATES[state]
        self.glyph.set_text(glyph)
        for w in (self.glyph, self.status):
            for c in ("dim", "amber", "green", "red"):
                w.remove_css_class(c)
            w.add_css_class(colour)
        self.status.set_text(text or said)


class App(Row):
    """An app: its windows (Hyprland addresses) and processes."""

    def __init__(self, name, wins):
        titles = sorted({w["title"] for w in wins if w["title"] and w["title"] != name})
        detail = f"{len(wins)} windows" if len(wins) > 1 else titles[0] if titles else ""
        super().__init__(name, detail)
        self.name, self.wins = name, {w["address"]: w for w in wins}
        self.pids = {w["pid"] for w in wins}
        self.closed_at = None


class SessionEnd(View):
    interval = 0
    css = CSS
    backdrop = False

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.title, self.going_text, self.icon, self.cmd = ACTIONS[action]
        self.subtitle = "closing apps"
        self.hints = [("Esc", "cancel")]
        self.started, self.going = time.monotonic(), False

    def build(self):
        self.apps = {}
        by_name = {}
        for w in windows():
            by_name.setdefault(name_of(w), []).append(w)
        self.list = box(True, 0)
        self.list.append(rule_heading("Apps", f"{len(by_name)} open" if by_name else "none open"))
        for name in sorted(by_name, key=str.lower):
            app = self.apps[name] = App(name, by_name[name])
            self.list.append(app.widget)
        self.threads = [s for s in self.tmux_sessions() if not s.startswith("_")]
        self.threads_row = None
        if self.threads:
            self.list.append(rule_heading("Agent threads", str(len(self.threads)), spaced=True))
            self.threads_row = Row("agentmux", ", ".join(self.threads))
            self.list.append(self.threads_row.widget)
        self.list.append(rule_heading("Then systemd", spaced=True))
        self.later = box(True, 0)   # filled from a thread: systemctl and docker take a moment
        self.list.append(self.later)
        page = scrolled(self.list)
        for edge in ("start", "end", "top", "bottom"):
            getattr(self.list, f"set_margin_{edge}")(18)
        threading.Thread(target=self.find_later, daemon=True).start()
        GLib.idle_add(self.start)
        return page

    # -- what's running --
    def tmux_sessions(self):
        try:
            out = subprocess.run(["tmux", "-L", "agents", "list-sessions", "-F", "#{session_name}"],
                                 capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return []
        return out.stdout.split() if out.returncode == 0 else []

    def find_later(self):
        """What systemd stops after the apps: your own user services (those in ~/.config/systemd/user),
        and, on Restart / Shutdown, Docker's running containers. Docker is asked only if it's running
        (`docker ps` would start it through its socket)."""
        rows = []
        try:
            units = subprocess.run(["systemctl", "--user", "list-units", "--type=service", "--state=running",
                                    "--no-legend", "--plain"], capture_output=True, text=True, timeout=3).stdout
            names = [line.split()[0] for line in units.splitlines() if line.strip()]
            paths = subprocess.run(["systemctl", "--user", "show", "-p", "Id", "-p", "FragmentPath", *names],
                                   capture_output=True, text=True, timeout=3).stdout if names else ""
            mine = [block.split("Id=", 1)[1].split("\n", 1)[0].removesuffix(".service")
                    for block in paths.split("\n\n") if "FragmentPath=" + USER_UNITS in block]
            if mine:
                rows.append(("Services", ", ".join(sorted(mine))))
        except (OSError, subprocess.TimeoutExpired, IndexError):
            pass
        if self.action != "logout":
            try:
                if subprocess.run(["systemctl", "is-active", "--quiet", "docker.service"], timeout=3).returncode == 0:
                    ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
                                        text=True, timeout=5).stdout.split()
                    if ps:
                        rows.append(("Docker", f"{len(ps)} container{'s' * (len(ps) != 1)}: {', '.join(ps)}"))
            except (OSError, subprocess.TimeoutExpired):
                pass
        rows.append(("Session", "Hyprland, the bar, audio" if self.action == "logout" else
                     "Hyprland, the bar, audio, then the system"))
        GLib.idle_add(lambda: self.show_later(rows) and False)

    def show_later(self, rows):
        for name, detail in rows:
            self.later.append(Row(name, detail, "next").widget)

    # -- closing --
    def start(self):
        # what's open, for the next login to offer again (restoregui.py), before anything closes
        sessionlib.save(windows(), self.threads, self.action)
        events = False
        if self.apps:
            events = hypr_events(self.window, (b"closewindow>>",), self.window_closed)
            self.close_all()
        # the clock (the subtitle's seconds, the WAIT deadline) and, without the event socket, the check
        GLib.timeout_add(1000 if events else 250, self.tick)
        self.tick()
        return False

    def close_all(self):
        by_pid = {}
        for app in self.apps.values():
            for w in app.wins.values():
                by_pid.setdefault(w["pid"], []).append(w)
            app.set("busy")
        for pid, own in by_pid.items():
            if len(own) > 1:
                try:
                    os.kill(pid, signal.SIGTERM)
                    continue
                except OSError:
                    pass
            for w in own:
                hypr_socket(f'dispatch hl.dsp.window.close({{ window = "address:{w["address"]}" }})')

    def window_closed(self, event):
        addr = "0x" + event[len(b"closewindow>>"):].decode(errors="replace")
        for app in self.apps.values():
            if app.wins.pop(addr, None) is not None and not app.wins:
                self.app_closed(app)
        self.check()

    def app_closed(self, app):
        if app.closed_at is None:
            app.closed_at = time.monotonic()
            app.set("done", f"closed · {app.closed_at - self.started:.1f} s")
            for b in list(self.buttons_of(app)):
                app.buttons.remove(b)

    def buttons_of(self, app):
        child = app.buttons.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def tick(self):
        if self.going:
            return False
        # the event socket can miss a window (an app gone before it was listed); the list is the truth
        alive = {w["address"] for w in windows()}
        for app in self.apps.values():
            for addr in [a for a in app.wins if a not in alive]:
                del app.wins[addr]
            if not app.wins:
                self.app_closed(app)
        self.check()
        return not self.going

    def open_apps(self):
        return [a for a in self.apps.values() if a.closed_at is None]

    def check(self):
        if self.going:
            return
        left = self.open_apps()
        took = time.monotonic() - self.started
        if not left:
            self.going = True
            self.set_subtitle("apps closed")
            GLib.idle_add(self.end_threads)
            return
        if took < WAIT:
            self.set_subtitle(f"closing {len(left)} app{'s' * (len(left) != 1)} · {int(took)} s")
            return
        self.set_subtitle(f"waiting on {len(left)} app{'s' * (len(left) != 1)}")
        stuck = [a for a in left if not a.buttons.get_first_child()]
        for app in stuck:
            app.set("stuck")
            app.buttons.append(button("Show", lambda a=app: self.show(a), "flat"))
            app.buttons.append(button("Force quit", lambda a=app: self.force(a), "flat", "danger"))
        if stuck:
            self.hints = [("F", "force quit all"), ("Esc", "cancel")]
            self.host.show_hints()
            self.say(f"{left[0].name} is still open: close it (it may be asking to save) or force quit it",
                     "bad", 10)
            if len(stuck) == len(left):   # the first time: show the first one, as you'd look for it
                self.show(left[0])

    def show(self, app):
        """Focus the app's window, this panel moved beside it on its workspace (a save dialog comes up
        over it: Hyprland raises the focused window)."""
        w = next(iter(app.wins.values()), None)
        if not w:
            return
        me = next((c for c in query("clients") or [] if c["pid"] == os.getpid()), None)
        if me and me["workspace"]["id"] != w["workspace"]["id"]:
            hypr_socket(f'dispatch hl.dsp.window.move({{ workspace = "{w["workspace"]["name"]}", follow = false, '
                        f'window = "address:{me["address"]}" }})')
        hypr_socket(f'dispatch hl.dsp.focus({{ window = "address:{w["address"]}" }})')

    def force(self, app):
        for pid in app.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        app.set("busy", "force quitting…")

    # -- after the apps --
    def end_threads(self):
        """agentmux quits (the sidebars stop watching first, so its threads come back next time), then
        the system goes down."""
        if not self.threads_row:
            return self.go()
        self.threads_row.set("busy", "ending…")
        self.set_subtitle("ending agent threads")

        def quit():
            try:
                subprocess.run([AGENTMUX, "quit", "--now"], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
            GLib.idle_add(self.threads_ended)
        threading.Thread(target=quit, daemon=True).start()
        return False

    def threads_ended(self):
        self.threads_row.set("done", "ended · back next time")
        return self.go()

    def go(self):
        self.set_subtitle(f"{self.going_text}…")
        self.hints = []
        self.host.show_hints()
        # a moment to read the last line, then systemd takes over (and ends this panel with the session)
        GLib.timeout_add(600, lambda: subprocess.Popen(self.cmd, start_new_session=True, stdout=subprocess.DEVNULL,
                                                       stderr=subprocess.DEVNULL) and False)
        return False

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and any(a.buttons.get_first_child() for a in self.open_apps()):
            for app in self.open_apps():
                self.force(app)
            return True
        return False


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ACTIONS:
        sys.exit(f"usage: {os.path.basename(sys.argv[0])} {'|'.join(ACTIONS)}")
    view = SessionEnd(sys.argv[1])
    run(view, "panels.sessionend", (620, 520))


if __name__ == "__main__":
    main()
