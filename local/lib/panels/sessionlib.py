#!/usr/bin/env python3
# sessionlib.py — what was open when the session ended, and opening it again after the next login.
#   sessionlib.py ask    at login (autostart.lua): if the last Log out / Restart / Shutdown left a list,
#                        the restore panel (restoregui.py) asks whether to open it all again
#   sessionlib.py show   print the saved list
# The session-end panel (sessionendgui.py) calls save() before it closes the apps. Each app is kept once
# (Chrome's and VS Code's windows are one process: started again, they reopen all of them), with the
# command that starts it and the workspace it was on. Opening them again is restoregui.py's: one at a
# time, the next once the last one's window is up and the CPU has calmed down (open_gently).
import json, os, shlex, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
import wsgroups
from hyprsock import query

STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "session")
SAVED = os.path.join(STATE, "saved.json")   # waiting to be offered at the next login
ASKED = os.path.join(STATE, "asked.json")   # offered (restored or not): kept for `show`, never offered again
# Windows that aren't apps of their own: popups and panels, the kitty beside a VS Code window (codepair
# opens it again with its VS Code), the Control Center (autostart.lua opens it at every login)
SKIP = ("TUI.float", "code-term-", "panels.", "sami.controlcenter")
# Apps that reopen their own windows when started plainly. VS Code doesn't if it's given a folder (the
# Code workspace's `code ~/dotfiles`); Chrome only when asked to (its startup page is a new tab)
REOPENS = {"com.microsoft.VSCode": "code", "code": "code",
           "google-chrome": "google-chrome-stable --restore-last-session"}


NAMES = {"com.microsoft.VSCode": "VS Code", "code": "VS Code", "google-chrome": "Chrome"}


def name_of(win):
    """What to call the app: this repo's windows by their title (sami.docker → Docker), a few by name,
    else the Name of its .desktop entry, else its class."""
    cls = win["class"]
    if cls.startswith("sami.") and win["title"]:
        return win["title"]
    if cls in NAMES:
        return NAMES[cls]
    for d in (os.path.expanduser("~/.local/share/applications"), "/usr/share/applications"):
        for n in (f"{cls}.desktop", f"{cls.lower()}.desktop"):
            try:
                for line in open(os.path.join(d, n), errors="ignore"):
                    if line.startswith("Name="):
                        return line[5:].strip()
            except OSError:
                pass
    return cls


def command_of(win):
    """(argv, cwd) that starts the app of this window: its own way of reopening everything, its
    workspace group's launch command, else exactly how its process was started."""
    cls = win["class"]
    if cls in REOPENS:
        return [os.path.expanduser(a) for a in shlex.split(REOPENS[cls])], None
    for g in wsgroups.groups().values():
        alts = g["cls"].split("|")
        if g["launch"] and wsgroups.matches(win, g["cls"]):
            # a group of several apps has a command each, in the same order (Docker ; Dokploy)
            i = next((n for n, a in enumerate(alts) if wsgroups.matches(win, a)), 0)
            cmd = g["launch"][i] if i < len(g["launch"]) else g["launch"][0]
            return [os.path.expanduser(a) for a in shlex.split(cmd)], None
    try:
        argv = open(f"/proc/{win['pid']}/cmdline").read().split("\0")[:-1]
        return argv, os.readlink(f"/proc/{win['pid']}/cwd")
    except OSError:
        return None, None


def snapshot(clients):
    """The apps behind these windows: one entry per process, in workspace order."""
    apps, seen = [], set()
    for w in sorted(clients, key=lambda c: (c["workspace"]["id"] <= 0, c["workspace"]["id"])):
        if w["pid"] in seen or not w["class"] or w["class"].startswith(SKIP):
            continue
        seen.add(w["pid"])
        argv, cwd = command_of(w)
        if argv:
            same = [c for c in clients if c["pid"] == w["pid"]]
            apps.append({"class": w["class"], "name": name_of(w),
                         "workspace": w["workspace"]["name"], "windows": len(same), "argv": argv, "cwd": cwd})
    return apps


def save(clients, threads, action):
    os.makedirs(STATE, exist_ok=True)
    with open(SAVED, "w") as f:
        json.dump({"at": time.time(), "action": action, "apps": snapshot(clients), "threads": threads}, f, indent=1)


def pending():
    try:
        return json.load(open(SAVED))
    except (OSError, ValueError):
        return None


def answered():
    """The list was offered: it isn't again (restored or not)."""
    try:
        os.replace(SAVED, ASKED)
    except OSError:
        pass


# ---- opening again ---------------------------------------------------------------------------------
def cpu_busy(seconds=0.5):
    """How busy the CPU is (0–1), over the next `seconds`."""
    def sample():
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(f), f[3] + f[4]   # total, idle + iowait
    t1, i1 = sample()
    time.sleep(seconds)
    t2, i2 = sample()
    return 1 - (i2 - i1) / max(1, t2 - t1)


def open_gently(app, stop, timeout=30, calm=0.5, settle=15):
    """Start one app and wait until its window is up, then until the CPU is under `calm` (at most
    `settle` s): the next one starts on a quiet system, not on top of this one's start-up. Its window
    goes to the workspace it was on. Returns the seconds its window took, or None (no window)."""
    before = {c["address"] for c in query("clients") or []}
    home = (query("activeworkspace") or {}).get("name")
    subprocess.Popen(["uwsm", "app", "--", *app["argv"]], cwd=app.get("cwd") or os.path.expanduser("~"),
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start = time.monotonic()
    new = None
    while not new and time.monotonic() - start < timeout and not stop():
        time.sleep(0.25)
        new = next((c for c in query("clients") or [] if c["address"] not in before
                    and c["class"] == app["class"]), None)
    if not new:
        return None
    took = time.monotonic() - start
    if new["workspace"]["name"] != app["workspace"]:
        wsgroups.dispatch(f'hl.dsp.window.move({{ workspace = "{app["workspace"]}", follow = false, '
                          f'window = "address:{new["address"]}" }})')
    # opening it switched the screen to its workspace: back to where you were
    now = (query("activeworkspace") or {}).get("name")
    if home and now != home and now in (new["workspace"]["name"], app["workspace"]):
        wsgroups.dispatch(f'hl.dsp.focus({{ workspace = "{home}" }})')
    time.sleep(1.5)   # the app's own start-up continues after its first window
    end = time.monotonic() + settle
    while time.monotonic() < end and not stop() and cpu_busy() > calm:
        pass
    return took


def already_open(apps):
    """How many of each class are open now (the Control Center autostart started, an app opened
    by hand meanwhile): that many of the list's are left out."""
    counts = {}
    for c in query("clients") or []:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    skip = []
    for i, a in enumerate(apps):
        if counts.get(a["class"], 0) > 0:
            skip.append(i)
            counts[a["class"]] -= 1
    return skip


def main(argv):
    if argv[:1] == ["ask"]:
        if pending():
            panel = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restoregui.py")
            os.execvp(sys.executable, [sys.executable, panel])
    elif argv[:1] == ["show"]:
        for path in (SAVED, ASKED):
            try:
                print(path, json.dumps(json.load(open(path)), indent=1))
            except (OSError, ValueError):
                pass
    else:
        print("usage: sessionlib.py ask|show")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
