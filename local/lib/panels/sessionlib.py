#!/usr/bin/env python3
# sessionlib.py — a desktop as a list of items (capture), opening such a list again gently (open_items), the
# saved setups (setups.json, synced) and the last session (what the last Log out / Restart / Shutdown left).
#   sessionlib.py ask    at login (autostart.lua): if the last session left a list, the Setups panel
#                        (setupsgui.py --login) asks whether to open it again
#   sessionlib.py show   print the last session's list and the tabs Chrome last reported
#   sessionlib.py restore  open the list start_restore wrote (as the transient unit setups-restore, so it
#                        carries on when the panel closes; the panel follows RESTORE and stops the unit)
#
# An item is one window, with what reopens it as it was: a Chrome window and its tabs (from Chrome's own session
# file, or the download manager's extension when its report is newer: TABS), a VS Code window and its folder
# (from its title), a terminal and its folder,
# agentmux and its project, any other app by its workspace group's command or exactly how it was started.
# Opening is one item at a time: the next once the last one's window is up and the CPU has calmed down
# (open_gently), each window on the workspace it was on, while you stay on yours.
import glob, json, os, re, shlex, shutil, signal, struct, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
import wsgroups
from hyprsock import query

HOME = os.path.expanduser("~")
XDG_STATE = os.environ.get("XDG_STATE_HOME", os.path.join(HOME, ".local/state"))
STATE = os.path.join(XDG_STATE, "session")
SAVED = os.path.join(STATE, "saved.json")   # the last session, waiting to be offered at the next login
ASKED = os.path.join(STATE, "asked.json")   # offered (restored or not): still listed, never asked about again
RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "setups")
RESTORE = os.path.join(RUNTIME, "restore.json")   # the restore under way: its items and how far each got
# Chrome windows being opened whose pinned tabs are still to be put back: the extension's next
# tab report (local/lib/downloads/native-host.py) finds each one's window and has the extension do it
REGROUP = os.path.join(RUNTIME, "regroup.json")
GROUP_COLOURS = ("grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange")   # Chrome's order
UNIT = "setups-restore"
TABS = os.path.join(XDG_STATE, "setups", "chrome-tabs.json")    # written by local/lib/downloads/native-host.py
CHROME_DATA = os.path.join(HOME, ".config/google-chrome")
# Chrome's last session, moved here as a restore starts Chrome (set_chrome_session_aside); the one before is dropped
CHROME_ASIDE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")), "setups", "chrome-session")
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


def chrome_sessions():
    """The folder of Chrome's own Session_* files: in the profile it last used (Default, or Profile 1… when
    profiles were added and removed)."""
    try:
        profile = json.load(open(os.path.join(CHROME_DATA, "Local State")))["profile"]["last_used"]
    except (OSError, ValueError, KeyError, TypeError):
        profile = "Default"
    return os.path.join(CHROME_DATA, profile or "Default", "Sessions")


def chrome_running():
    """True if Chrome is running: its SingletonLock is a link to "host-pid" while it is."""
    try:
        pid = int(os.readlink(os.path.join(CHROME_DATA, "SingletonLock")).rsplit("-", 1)[1])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def set_chrome_session_aside():
    """Before a restore's first Chrome window starts Chrome: its last session moved out of its way. Set to
    "Continue where you left off", Chrome brings that session back with the first window it opens, whatever its
    command line says (--no-startup-window, --incognito, --app first don't stop it), and the restore's own
    windows came on top: 17 windows for 9."""
    if chrome_running():
        return
    files = glob.glob(os.path.join(chrome_sessions(), "Session_*"))
    if not files:
        return
    shutil.rmtree(CHROME_ASIDE, ignore_errors=True)
    os.makedirs(CHROME_ASIDE)
    for f in files:
        shutil.move(f, CHROME_ASIDE)


def chrome_windows():
    """Chrome's open windows and their tabs: [{title, tabs: [{url, title, pinned, active, group}], groups: [{title,
    color, collapsed}]}] (a tab's group: its index in groups, -1 for none), or None. From the extension's report if
    it's newer than Chrome's own session file, else from that file."""
    try:
        reported = os.path.getmtime(TABS)
    except OSError:
        reported = 0
    files = glob.glob(os.path.join(chrome_sessions(), "Session_*"))
    session = max(files, key=os.path.getmtime) if files else None
    if reported and (not session or reported >= os.path.getmtime(session)):
        try:
            return json.load(open(TABS)).get("windows")
        except (OSError, ValueError):
            pass
    return chrome_session(session) if session else None


def chrome_session(path):
    """Chrome's session file (SNSS: a log of commands Chrome appends as you browse) replayed into its open
    windows and their tabs, in the extension's shape. Incognito windows are never in it. The commands used
    (Chrome's session_service_commands.cc): 0 tab → window, 2 tab's place in its window, 6 a tab's
    navigation entry (a pickle: tab, index, url, title), 7 a tab's current entry, 8 a window's selected tab,
    9 window type (0 = normal), 12 pinned, 16 tab closed, 17 window closed, 25 a tab's group (tab, its token,
    in one or not), 27 a group's look (a pickle: token, title, colour, collapsed)."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    if data[:4] != b"SNSS":
        return None
    window_of, place, entries, current, pinned, selected, kind = {}, {}, {}, {}, {}, {}, {}
    group_of, looks = {}, {}   # tab -> its group's token; token -> {title, color, collapsed}
    closed_tabs, closed_windows = set(), set()
    pos = 8   # "SNSS", version
    while pos + 2 <= len(data):
        size = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        if not size or pos + size > len(data):
            break
        cmd, body = data[pos], data[pos + 1:pos + size]
        pos += size
        try:
            if cmd in (0, 2, 7, 8, 9) and len(body) >= 8:
                a, b = struct.unpack_from("<ii", body)
                {0: window_of, 2: place, 7: current, 8: selected, 9: kind}[cmd][b if cmd == 0 else a] = a if cmd == 0 else b
            elif cmd == 6:
                tab, index, n = struct.unpack_from("<iii", body, 4)   # after the pickle's own size
                p = 16
                url = body[p:p + n].decode("utf-8", "replace")
                p += (n + 3) & ~3
                n = struct.unpack_from("<i", body, p)[0]
                title = body[p + 4:p + 4 + 2 * n].decode("utf-16-le", "replace")
                entries.setdefault(tab, {})[index] = (url, title)
            elif cmd == 12 and len(body) >= 5:
                pinned[struct.unpack_from("<i", body)[0]] = bool(body[4])
            elif cmd == 16 and len(body) >= 4:
                closed_tabs.add(struct.unpack_from("<i", body)[0])
            elif cmd == 17 and len(body) >= 4:
                closed_windows.add(struct.unpack_from("<i", body)[0])
            elif cmd == 25 and len(body) >= 28:
                tab, token, has = struct.unpack_from("<i", body)[0], body[8:24], struct.unpack_from("<i", body, 24)[0]
                group_of[tab] = token if has else None
            elif cmd == 27 and len(body) >= 28:
                token, n = body[4:20], struct.unpack_from("<i", body, 20)[0]
                p = 24 + ((2 * n + 3) & ~3)
                colour, collapsed = struct.unpack_from("<Ii", body, p)
                looks[token] = {"title": body[24:24 + 2 * n].decode("utf-16-le", "replace"),
                                "color": GROUP_COLOURS[colour] if colour < len(GROUP_COLOURS) else "grey",
                                "collapsed": bool(collapsed)}
        except struct.error:   # a command cut short (Chrome writing as we read): skip it
            continue
    windows = {}
    for tab, win in window_of.items():
        if tab in closed_tabs or win in closed_windows or kind.get(win, 0) != 0 or not entries.get(tab):
            continue
        nav = entries[tab]
        url, title = nav.get(current.get(tab), nav[max(nav)])
        windows.setdefault(win, []).append((place.get(tab, 0), {"url": url, "title": title,
                                                                 "pinned": pinned.get(tab, False),
                                                                 "token": group_of.get(tab)}))
    out = []
    for win, tabs in windows.items():
        tabs = [t for _, t in sorted(tabs, key=lambda x: x[0])]
        active = selected.get(win, 0)
        tokens = []   # this window's groups, in tab order
        for i, t in enumerate(tabs):
            t["active"] = i == active
            token = t.pop("token")
            if token and token not in tokens:
                tokens.append(token)
            t["group"] = tokens.index(token) if token else -1
        out.append({"title": tabs[min(active, len(tabs) - 1)]["title"], "tabs": tabs,
                    "groups": [looks.get(t, {"title": "", "color": "grey", "collapsed": False}) for t in tokens]})
    return out


def words(text):
    """The words of a title or address (3 letters or more, lower case), to compare two of them."""
    return {w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(w) > 2}


def agentmux_project():
    try:
        return json.load(open(AGENTMUX_STATE)).get("project") or None
    except (OSError, ValueError):
        return None


def detail_of(item):
    """The dim line beside its name: the tabs, the folder, the project."""
    if item["kind"] == "chrome" and item.get("incognito"):
        return "incognito window · opens empty (its tabs are never kept)"
    if item["kind"] == "chrome":
        urls = item.get("urls") or []
        sites = []
        for u in urls:
            host = u.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
            if host and host not in sites:
                sites.append(host)
        groups = len(item.get("groups") or [])
        tabs = f"{len(urls)} tab{'s' * (len(urls) != 1)}" + (f" · {groups} group{'s' * (groups != 1)} (Chrome's own)" if groups else "") \
            + (" · " + ", ".join(sites[:4]) if sites else "")
        return f"{item['title']} · {tabs}" if item.get("title") else tabs   # its current tab: which window it is
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
        # each of Chrome's windows to its Hyprland window (whose title is the current tab's + " - Google Chrome"),
        # for its workspace: by its current tab's title, then by any of its tabs', then by the words they share
        suffix = " - Google Chrome"
        free = list(chrome)
        found = [None] * len(tabs)
        for n, w in enumerate(tabs):
            found[n] = next((c for c in free if w.get("title") and c["title"] == w["title"] + suffix), None)
            if found[n]:
                free.remove(found[n])
        for n, w in enumerate(tabs):   # the session file's selected tab can lag: any of its tabs' titles
            if not found[n]:
                titles = {t.get("title", "") + suffix for t in w.get("tabs", [])}
                found[n] = next((c for c in free if c["title"] in titles), None)
                if found[n]:
                    free.remove(found[n])
        for n, w in enumerate(tabs):   # its current tab's title not written yet: the most words in common
            if not found[n] and free:
                mine = words(" ".join(t.get("title", "") + " " + t.get("url", "") for t in w.get("tabs", [])))
                found[n] = max(free, key=lambda c: len(words(c["title"].removesuffix(suffix)) & mine))
                free.remove(found[n])
            win = found[n] or chrome[0]
            kept = [t for t in w.get("tabs", []) if t.get("url") and not t["url"].startswith("chrome://newtab")]
            # its tab groups and pinned tabs, by their place among the tabs kept
            looks = w.get("groups") or []
            groups = [dict(g, tabs=[i for i, t in enumerate(kept) if t.get("group", -1) == n]) for n, g in enumerate(looks)]
            items.append(make_item("chrome", CHROME, win["workspace"]["name"], urls=[t["url"] for t in kept],
                                   pinned=[i for i, t in enumerate(kept) if t.get("pinned")],
                                   groups=[g for g in groups if g["tabs"]],
                                   title=w.get("title") or win["title"].removesuffix(suffix)))
        for c in free:   # a window Chrome didn't record is incognito: kept as one, its tabs never are
            items.append(make_item("chrome", CHROME, c["workspace"]["name"], urls=[], incognito=True))
    elif chrome:   # Chrome's tabs unknown (no session file yet): it reopens its own last session
        item = make_item("app", CHROME, chrome[0]["workspace"]["name"],
                         argv=["google-chrome-stable", "--restore-last-session"])
        item["detail"] = f"{len(chrome)} window{'s' * (len(chrome) != 1)} · its last session (tabs not known)"
        items.append(item)
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
def loose_tabs(item):
    """A Chrome window's tabs that weren't in a tab group, and which of them are pinned (their places among
    those): what opening it brings back. Chrome keeps every tab group in its saved groups strip (bookmarks bar),
    where a click reopens it; a group made again here was saved once more, a new dot per group at every restore."""
    grouped = {i for g in item.get("groups") or [] for i in g.get("tabs", [])}
    keep = [i for i in range(len(item.get("urls") or [])) if i not in grouped]
    pinned = set(item.get("pinned") or [])
    return [item["urls"][i] for i in keep], [n for n, i in enumerate(keep) if i in pinned]


def argv_of(item):
    """(argv, cwd) that opens this item's window."""
    kind = item["kind"]
    if kind == "chrome":
        if item.get("incognito"):
            return ["google-chrome-stable", "--incognito"], None
        return ["google-chrome-stable", "--new-window", *loose_tabs(item)[0]], None
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
    class (counts: open windows per class, used up as they're matched); a terminal in the same folder, each
    standing for one (counts[("kitty", folder)]: else every Continue added the session's terminals again,
    and the next session kept them all). A Chrome window is never 'the same'."""
    kind = item["kind"]
    if kind == "chrome":
        return False
    if kind == "kitty":
        key = ("kitty", item.get("cwd"))
        if counts.get(key, 0) > 0:
            counts[key] -= 1
            return True
        return False
    if kind == "code":
        name = os.path.basename(item.get("folder") or "")
        return any(c["class"] in CODE and code_folder_name(c["title"]) == name for c in clients) if name else False
    if counts.get(item["class"], 0) > 0:
        counts[item["class"]] -= 1
        return True
    return False


def before_open(item):
    """What its window needs once it's up: a Chrome window's pinned tabs, left in REGROUP for the extension
    (Chrome's command line can't pin). A Chrome window that starts Chrome: its own last session set aside first."""
    if item["kind"] != "chrome":
        return
    set_chrome_session_aside()
    urls, pinned = loose_tabs(item)
    if pinned:
        os.makedirs(RUNTIME, exist_ok=True)
        try:
            pending = [p for p in json.load(open(REGROUP)) if time.time() - p["at"] < 120]
        except (OSError, ValueError, KeyError, TypeError):
            pending = []
        pending.append({"at": time.time(), "urls": urls, "pinned": pinned, "groups": []})
        with open(REGROUP, "w") as f:
            json.dump(pending, f)


def after_open(item):
    """What follows its window: agentmux shows the item's project. Its own unit (uwsm app), like the windows: it
    may start the agents' tmux server, which would otherwise live on in the restore's unit and keep it loaded."""
    if item["kind"] == "agentmux" and item.get("project"):
        subprocess.Popen(["uwsm", "app", "--", AGENTMUX, "project", item["project"]], start_new_session=True,
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


def open_gently(item, stop, held=None, timeout=30, calm=0.5, settle=15):
    """Open one item and wait until its window is up, then until the CPU is under `calm` (at most `settle`
    s): the next one starts on a quiet system, not on top of this one's start-up. Its window goes to the
    workspace it was on, the screen stays where it is. Returns the seconds its window took, or None (no window).
    held: a dict the caller releases (release_quiet) once everything is open: the window an app shows first may
    be a splash (Discord's updater), and the app's own one, seconds later, switched the screen to its workspace
    once the group rules were back."""
    argv, cwd = argv_of(item)
    if not argv:
        return None
    before = {c["address"] for c in query("clients") or []}
    home = (query("activeworkspace") or {}).get("name")
    cls = re.escape(item["class"])
    wsgroups.quietly(cls, item["workspace"])
    if held is not None:
        held[cls] = item["workspace"]
    before_open(item)
    try:
        subprocess.Popen(["uwsm", "app", "--", *argv], cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start = time.monotonic()
        new = None
        while not new and time.monotonic() - start < timeout and not stop():
            time.sleep(0.25)
            new = next((c for c in query("clients") or [] if c["address"] not in before
                        and c["class"] == item["class"]), None)
    finally:
        if held is None:
            wsgroups.quiet_done(cls)
    if not new:
        return None
    took = time.monotonic() - start
    if new["workspace"]["name"] != item["workspace"]:
        wsgroups.dispatch(f'hl.dsp.window.move({{ workspace = "{item["workspace"]}", follow = false, '
                          f'window = "address:{new["address"]}" }})')
    # a window that came up elsewhere anyway (an app that switches by itself): back to where you were
    now = (query("activeworkspace") or {}).get("name")
    if home and now != home and now in (new["workspace"]["name"], item["workspace"]):
        wsgroups.dispatch(f'hl.dsp.focus({{ workspace = "{home}" }})')
    after_open(item)
    time.sleep(1.5)   # the app's own start-up continues after its first window
    end = time.monotonic() + settle
    while time.monotonic() < end and not stop() and cpu_busy() > calm:
        pass
    return took


def release_quiet(held, home, quiet_for=5, at_most=30):
    """The group rules back for the classes open_gently held, once none of their windows has opened or closed
    for `quiet_for` s (at most `at_most`): an app's own window after its splash still opens quietly. Then the
    screen back to `home` if one of those windows switched it anyway."""
    def theirs():
        return {c["address"] for c in query("clients") or []
                if any(re.fullmatch(cls, c["class"]) for cls in held)}
    try:
        seen, still, end = theirs(), time.monotonic(), time.monotonic() + at_most
        while held and time.monotonic() < end and time.monotonic() - still < quiet_for:
            time.sleep(0.5)
            now = theirs()
            if now != seen:
                seen, still = now, time.monotonic()
    finally:
        for cls in held:
            wsgroups.quiet_done(cls)
    now = (query("activeworkspace") or {}).get("name")
    if held and home and now != home and now in held.values():
        wsgroups.dispatch(f'hl.dsp.focus({{ workspace = "{home}" }})')


def open_items(items, stop, progress):
    """Open the items in order, gently; progress(i, state, text) after each step (state: busy, done,
    stuck). What's open already is left alone."""
    clients = query("clients") or []   # what's open before: the windows opened here don't count
    counts = {}
    for c in clients:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
        if c["class"] == "kitty":
            key = ("kitty", shell_cwd(c["pid"]))
            counts[key] = counts.get(key, 0) + 1
    held, home = {}, (query("activeworkspace") or {}).get("name")
    try:
        for i, item in enumerate(items):
            if stop():
                return
            if is_open(item, clients, counts):
                after_open(item)
                progress(i, "done", "already open")
                continue
            progress(i, "busy", "opening…")
            took = open_gently(item, stop, held)
            if took is None and stop():   # stopped while it was starting: it may still come up
                progress(i, "wait", "stopped")
                return
            progress(i, "stuck", "no window") if took is None else progress(i, "done", f"open · {took:.1f} s")
    finally:
        release_quiet(held, home)


# ---- a restore, in the background --------------------------------------------------------------------------
def write_restore(job):
    os.makedirs(RUNTIME, exist_ok=True)
    with open(RESTORE + ".tmp", "w") as f:
        json.dump(job, f)
    os.replace(RESTORE + ".tmp", RESTORE)   # whole at once for the panel reading it (a runtime file, never a link)


def restore_job():
    """The last restore's job ({sid, name, items, states: {i: [state, text]}, state: starting / running /
    done / stopped}), or None. One that died without saying so counts as stopped."""
    try:
        job = json.load(open(RESTORE))
    except (OSError, ValueError):
        return None
    if job.get("state") == "starting" and time.time() - job.get("at", 0) > 15:   # the unit never got going
        job["state"] = "stopped"
    elif job.get("state") == "running":
        try:
            os.kill(job["pid"], 0)
        except (OSError, KeyError):
            job["state"] = "stopped"
    return job


def restoring():
    """The restore under way, or None."""
    job = restore_job()
    return job if job and job["state"] in ("starting", "running") else None


def start_restore(sid, name, items):
    """Open the items in a transient user unit, setups-restore-<time>: it outlives the panel (closing the panel no
    longer stops it), stop_restore() stops it. The apps it opens are their own units (uwsm app), so
    KillMode=process: stopping it never takes a window with it. A name of its own each time: a unit with a
    process left in it stays loaded, and its name couldn't be used again (systemd-run failed). False if it
    couldn't start."""
    unit = f"{UNIT}-{int(time.time())}"
    write_restore({"sid": sid, "name": name, "items": items, "states": {}, "state": "starting", "pid": 0,
                   "unit": unit, "at": time.time()})
    ok = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}", "-p", "KillMode=process",
                         "-p", "TimeoutStopSec=10", sys.executable, os.path.abspath(__file__), "restore"],
                        capture_output=True).returncode == 0
    if not ok:
        write_restore({"sid": sid, "name": name, "items": items, "states": {}, "state": "stopped", "pid": 0})
    return ok


def stop_restore():
    unit = (restore_job() or {}).get("unit")
    if unit:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    job = restore_job()
    if job and job["state"] in ("starting", "running"):   # it never got going, or died: say so
        job["state"] = "stopped"
        write_restore(job)


def run_restore():
    """The unit's work: open the job's items, writing each one's progress to RESTORE; SIGTERM (Stop) ends
    it after the window being opened (its workspace rules put back first). A notification once all are open."""
    job = restore_job()
    if not job or job["state"] != "starting":
        return 1
    stopped = []
    signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
    job.update(state="running", pid=os.getpid())
    write_restore(job)

    def progress(i, state, text):
        job["states"][str(i)] = [state, text]
        write_restore(job)
    open_items(job["items"], lambda: bool(stopped), progress)
    job["state"] = "stopped" if stopped else "done"
    write_restore(job)
    if not stopped:
        subprocess.run(["notify-send", "-t", "3000", "Setups", f"{job['name']} is open"], capture_output=True)
    return 0


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
    elif argv[:1] == ["restore"]:
        return run_restore()
    elif argv[:1] == ["show"]:
        session, asking = last_session()
        print(f"last session ({'to ask about' if asking else 'asked'}):", json.dumps(session, indent=1))
        print("chrome tabs:", json.dumps(chrome_windows(), indent=1))
    else:
        print("usage: sessionlib.py ask|show|restore")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
