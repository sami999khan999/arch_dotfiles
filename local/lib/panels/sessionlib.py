#!/usr/bin/env python3
# sessionlib.py — a desktop as a list of items (capture), opening such a list again gently (open_items), the
# saved setups (setups.json, synced) and the last session (what the last Log out / Restart / Shutdown left).
#   sessionlib.py ask    at login (autostart.lua): if the last session left a list, the Setups panel
#                        (setupsgui.py --login) asks whether to open it again
#   sessionlib.py show   print the last session's list and the tabs Chrome last reported
#
# An item is one window, with what reopens it as it was: a Chrome window and its tabs (the download manager's
# extension reports them: TABS), a VS Code window and its folder (from its title), a terminal and its folder,
# agentmux and its project, any other app by its workspace group's command or exactly how it was started.
# Opening is one item at a time: the next once the last one's window is up and the CPU has calmed down
# (open_gently), each window on the workspace it was on, while you stay on yours.
import json, os, shlex, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
import wsgroups
from hyprsock import query

HOME = os.path.expanduser("~")
XDG_STATE = os.environ.get("XDG_STATE_HOME", os.path.join(HOME, ".local/state"))
STATE = os.path.join(XDG_STATE, "session")
SAVED = os.path.join(STATE, "saved.json")   # the last session, waiting to be offered at the next login
ASKED = os.path.join(STATE, "asked.json")   # offered (restored or not): still listed, never asked about again
TABS = os.path.join(XDG_STATE, "setups", "chrome-tabs.json")    # written by local/lib/downloads/native-host.py
AGENTMUX_STATE = os.path.join(XDG_STATE, "agentmux", "state.json")
PROJECTS_CMD = os.path.join(HOME, ".local/bin/projects")
AGENTMUX = os.path.join(HOME, ".local/bin/agentmux")
CODE_ROOT = os.path.join(HOME, "code")
# Windows that aren't apps of their own: popups and panels, the kitty beside a VS Code window (codepair
# opens it again with its VS Code), the Control Center (autostart.lua opens it at every login)
SKIP = ("TUI.float", "code-term-", "panels.", "sami.controlcenter")
CHROME, CODE = "google-chrome", ("com.microsoft.VSCode", "code")
NAMES = {"com.microsoft.VSCode": "VS Code", "code": "VS Code", "google-chrome": "Chrome"}


def tilde(path):
    return "~" + path[len(HOME):] if path and path.startswith(HOME) else path or ""


def name_of(win):
    """What to call the app: this repo's windows by their title (sami.docker → Docker), a few by name,
    else the Name of its .desktop entry, else its class."""
    cls = win["class"]
    if cls.startswith("sami.") and win.get("title"):
        return win["title"]
    if cls in NAMES:
        return NAMES[cls]
    for d in (os.path.join(HOME, ".local/share/applications"), "/usr/share/applications"):
        for n in (f"{cls}.desktop", f"{cls.lower()}.desktop"):
            try:
                for line in open(os.path.join(d, n), errors="ignore"):
                    if line.startswith("Name="):
                        return line[5:].strip()
            except OSError:
                pass
    return cls


def group_command(cls):
    """The launch command of the workspace group this class belongs to (argv), or None."""
    for g in wsgroups.groups().values():
        if g["launch"] and wsgroups.matches({"class": cls}, g["cls"]):
            # a group of several apps has a command each, in the same order (Docker ; Dokploy)
            alts = g["cls"].split("|")
            i = next((n for n, a in enumerate(alts) if wsgroups.matches({"class": cls}, a)), 0)
            cmd = g["launch"][i] if i < len(g["launch"]) else g["launch"][0]
            return [os.path.expanduser(a) for a in shlex.split(cmd)]
    return None


# ---- what a window is ------------------------------------------------------------------------------------
def code_folder_name(title):
    """VS Code's title is "[●] file - folder - Visual Studio Code" (or "folder - …"): the folder's name."""
    parts = [p.strip() for p in title.removesuffix("Visual Studio Code").rstrip(" -").split(" - ")]
    return parts[-1] if parts and parts[-1] else ""


def resolve_folder(name):
    """A folder's name (all a title shows) as a path: a project in the project list, ~/dotfiles, or a
    folder of that name a few levels down ~/code."""
    if not name:
        return None
    try:
        for p in json.load(open(os.path.join(CODE_ROOT, ".projects", "projects.json"))).get("projects", []):
            if os.path.basename(p["path"].rstrip("/")) == name:
                return os.path.join(CODE_ROOT, p["path"])
    except (OSError, ValueError, KeyError):
        pass
    for base in (HOME, os.path.join(HOME, "dotfiles")):
        if os.path.basename(base) == name:
            return base
    for root, dirs, _files in os.walk(CODE_ROOT):
        if root[len(CODE_ROOT):].count(os.sep) >= 3:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        if name in dirs:
            return os.path.join(root, name)
    return None


def shell_cwd(pid):
    """The folder of the shell a terminal (its process) runs."""
    try:
        kids = open(f"/proc/{pid}/task/{pid}/children").read().split()
        return os.readlink(f"/proc/{kids[0]}/cwd") if kids else os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def chrome_windows():
    """The tabs Chrome last reported: [{title, tabs: [{url, title, …}]}], or None."""
    try:
        return json.load(open(TABS)).get("windows")
    except (OSError, ValueError):
        return None


def agentmux_project():
    try:
        return json.load(open(AGENTMUX_STATE)).get("project") or None
    except (OSError, ValueError):
        return None


def detail_of(item):
    """The dim line beside its name: the tabs, the folder, the project."""
    if item["kind"] == "chrome":
        urls = item.get("urls") or []
        sites = []
        for u in urls:
            host = u.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
            if host and host not in sites:
                sites.append(host)
        return f"{len(urls)} tab{'s' * (len(urls) != 1)}" + (" · " + ", ".join(sites[:4]) if sites else "")
    if item["kind"] == "code":
        return tilde(item.get("folder")) or "no folder"
    if item["kind"] == "kitty":
        return tilde(item.get("cwd"))
    if item["kind"] == "agentmux":
        return tilde(item.get("project"))
    return ""


def make_item(kind, cls, workspace, **fields):
    item = {"kind": kind, "class": cls, "name": NAMES.get(cls) or fields.pop("name", None) or cls,
            "workspace": str(workspace), **fields}
    item["detail"] = detail_of(item)
    return item


def capture(clients=None):
    """The desktop now as items, one per window (Chrome: per window with its tabs), in workspace order."""
    clients = [c for c in (query("clients") if clients is None else clients) or []
               if c.get("class") and not c["class"].startswith(SKIP) and c.get("mapped", True)]
    clients.sort(key=lambda c: (c["workspace"]["id"] <= 0, c["workspace"]["id"]))
    items = []
    chrome = [c for c in clients if c["class"] == CHROME]
    tabs = chrome_windows() if chrome else None
    if tabs:
        # each reported window to its Hyprland window (whose title is the active tab's + " - Google Chrome"),
        # for its workspace; one that doesn't match takes a Chrome window left over
        free = list(chrome)
        for w in tabs:
            win = next((c for c in free if c["title"] == f"{w.get('title', '')} - Google Chrome"), None) \
                or (free[0] if free else None)
            if win:
                free.remove(win)
            urls = [t["url"] for t in w.get("tabs", []) if t.get("url") and not t["url"].startswith("chrome://newtab")]
            items.append(make_item("chrome", CHROME, (win or chrome[0])["workspace"]["name"], urls=urls))
    elif chrome:   # no tabs reported (the extension not updated yet): Chrome reopens its own last session
        items.append(make_item("app", CHROME, chrome[0]["workspace"]["name"],
                               argv=["google-chrome-stable", "--restore-last-session"]))
    for c in clients:
        cls, ws = c["class"], c["workspace"]["name"]
        if cls == CHROME:
            continue
        if cls in CODE:
            items.append(make_item("code", cls, ws, folder=resolve_folder(code_folder_name(c["title"]))))
        elif cls == "kitty":
            items.append(make_item("kitty", cls, ws, cwd=shell_cwd(c["pid"])))
        elif cls == "agentmux":
            items.append(make_item("agentmux", cls, ws, project=agentmux_project()))
        else:
            argv, cwd = group_command(cls), None
            if not argv:
                try:
                    argv = open(f"/proc/{c['pid']}/cmdline").read().split("\0")[:-1]
                    cwd = os.readlink(f"/proc/{c['pid']}/cwd")
                except OSError:
                    continue
            if argv:
                items.append(make_item("app", cls, ws, name=name_of(c), argv=argv, cwd=cwd))
    # several windows of one process with the same command (an app's own windows): it reopens them itself
    seen, out = set(), []
    for it in items:
        key = (it["kind"], it["class"], json.dumps(it.get("argv")), it.get("cwd")) if it["kind"] == "app" else None
        if key and key in seen:
            continue
        seen.add(key)
        out.append(it)
    return sorted(out, key=ws_order)


def ws_order(item):
    """Workspaces 1…10 in order, special ones (the scratchpad) after."""
    ws = item["workspace"]
    return (0, int(ws)) if ws.lstrip("-").isdigit() else (1, 0)


def app_item(cls, name, argv):
    """An app added by hand (Setups → Add): its workspace group's workspace, else the current one."""
    ws = next((str(n) for n, g in wsgroups.groups().items() if wsgroups.matches({"class": cls}, g["cls"])),
              str((query("activeworkspace") or {}).get("name", "1")))
    kind = "code" if cls in CODE else "kitty" if cls == "kitty" else "agentmux" if cls == "agentmux" else "app"
    return make_item(kind, cls, ws, name=name, argv=argv)


# ---- opening ---------------------------------------------------------------------------------------------
def argv_of(item):
    """(argv, cwd) that opens this item's window."""
    kind = item["kind"]
    if kind == "chrome":
        return ["google-chrome-stable", "--new-window", *(item.get("urls") or [])], None
    if kind == "code":
        return ["code", "--new-window", *([item["folder"]] if item.get("folder") else [])], None
    if kind == "kitty":
        return (["kitty", "--directory", item["cwd"]] if item.get("cwd") else ["kitty"]), None
    if kind == "agentmux":
        return group_command("agentmux") or ["kitty", "--class", "agentmux", "-e", AGENTMUX], None
    return item.get("argv") or [], item.get("cwd")


def is_open(item, clients, counts):
    """True if what this item opens was open already before the opening started: the same VS Code folder;
    an app's window (the Control Center autostart opened, a panel), each one standing for one item of its
    class (counts: open windows per class, used up as they're matched). A Chrome window or a terminal is
    never 'the same'."""
    kind = item["kind"]
    if kind in ("chrome", "kitty"):
        return False
    if kind == "code":
        name = os.path.basename(item.get("folder") or "")
        return any(c["class"] in CODE and code_folder_name(c["title"]) == name for c in clients) if name else False
    if counts.get(item["class"], 0) > 0:
        counts[item["class"]] -= 1
        return True
    return False


def after_open(item):
    """What follows its window: agentmux shows the item's project."""
    if item["kind"] == "agentmux" and item.get("project"):
        subprocess.Popen([AGENTMUX, "project", item["project"]], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cpu_busy(seconds=0.5):
    """How busy the CPU is (0–1), over the next `seconds`."""
    def sample():
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(f), f[3] + f[4]   # total, idle + iowait
    t1, i1 = sample()
    time.sleep(seconds)
    t2, i2 = sample()
    return 1 - (i2 - i1) / max(1, t2 - t1)


def open_gently(item, stop, timeout=30, calm=0.5, settle=15):
    """Open one item and wait until its window is up, then until the CPU is under `calm` (at most `settle`
    s): the next one starts on a quiet system, not on top of this one's start-up. Its window goes to the
    workspace it was on. Returns the seconds its window took, or None (no window)."""
    argv, cwd = argv_of(item)
    if not argv:
        return None
    before = {c["address"] for c in query("clients") or []}
    home = (query("activeworkspace") or {}).get("name")
    subprocess.Popen(["uwsm", "app", "--", *argv], cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start = time.monotonic()
    new = None
    while not new and time.monotonic() - start < timeout and not stop():
        time.sleep(0.25)
        new = next((c for c in query("clients") or [] if c["address"] not in before
                    and c["class"] == item["class"]), None)
    if not new:
        return None
    took = time.monotonic() - start
    if new["workspace"]["name"] != item["workspace"]:
        wsgroups.dispatch(f'hl.dsp.window.move({{ workspace = "{item["workspace"]}", follow = false, '
                          f'window = "address:{new["address"]}" }})')
    # opening it switched the screen to its workspace: back to where you were
    now = (query("activeworkspace") or {}).get("name")
    if home and now != home and now in (new["workspace"]["name"], item["workspace"]):
        wsgroups.dispatch(f'hl.dsp.focus({{ workspace = "{home}" }})')
    after_open(item)
    time.sleep(1.5)   # the app's own start-up continues after its first window
    end = time.monotonic() + settle
    while time.monotonic() < end and not stop() and cpu_busy() > calm:
        pass
    return took


def open_items(items, stop, progress):
    """Open the items in order, gently; progress(i, state, text) after each step (state: busy, done,
    stuck). What's open already is left alone."""
    clients = query("clients") or []   # what's open before: the windows opened here don't count
    counts = {}
    for c in clients:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    for i, item in enumerate(items):
        if stop():
            return
        if is_open(item, clients, counts):
            after_open(item)
            progress(i, "done", "already open")
            continue
        progress(i, "busy", "opening…")
        took = open_gently(item, stop)
        progress(i, "stuck", "no window") if took is None else progress(i, "done", f"open · {took:.1f} s")


# ---- the last session --------------------------------------------------------------------------------------
def save(clients, threads, action):
    """The session-end panel, before it closes anything: what's open, for the next login."""
    os.makedirs(STATE, exist_ok=True)
    with open(SAVED, "w") as f:
        json.dump({"at": time.time(), "action": action, "items": capture(clients), "threads": threads}, f, indent=1)


def last_session():
    """(the last session, still to be asked about?) or (None, False)."""
    for path, asking in ((SAVED, True), (ASKED, False)):
        try:
            return json.load(open(path)), asking
        except (OSError, ValueError):
            pass
    return None, False


def answered():
    """The last session was offered: it isn't asked about again (restored or not)."""
    try:
        os.replace(SAVED, ASKED)
    except OSError:
        pass


# ---- setups (in the private project-list repo, synced between PCs) --------------------------------------------
def projects_module():
    """local/bin/projects as a module: its repo helpers (list_pull, commit) sync setups.json too."""
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("projects", PROJECTS_CMD)
    spec = importlib.util.spec_from_loader("projects", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def setups_path():
    return os.path.join(CODE_ROOT, ".projects", "setups.json")


def load_setups():
    try:
        return json.load(open(setups_path())).get("setups", [])
    except (OSError, ValueError):
        return []


def save_setups(setups):
    os.makedirs(os.path.dirname(setups_path()), exist_ok=True)
    with open(setups_path(), "w") as f:   # in place: the file may be a link
        json.dump({"version": 1, "setups": setups}, f, indent=1)
        f.write("\n")


def pull_setups():
    """What the other PC changed. Returns a problem, or ""."""
    try:
        return projects_module().list_pull()
    except Exception as e:   # no projects repo here: setups stay local
        return str(e)


def push_setups(message):
    """Commit setups.json and push it. Returns a problem, or ""."""
    try:
        return projects_module().commit(f"setups: {message}", ("setups.json",))
    except Exception as e:
        return str(e)


def main(argv):
    if argv[:1] == ["ask"]:
        if last_session()[1]:
            panel = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setupsgui.py")
            os.execvp(sys.executable, [sys.executable, panel, "--login"])
    elif argv[:1] == ["show"]:
        session, asking = last_session()
        print(f"last session ({'to ask about' if asking else 'asked'}):", json.dumps(session, indent=1))
        print("chrome tabs:", json.dumps(chrome_windows(), indent=1))
    else:
        print("usage: sessionlib.py ask|show")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
