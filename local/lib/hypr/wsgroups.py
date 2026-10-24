"""wsgroups — one workspace per kind of app, every window full-size (monocle), Super + X then
Super + Q…P to switch.

  wsgroups                    open (or close) the manager window (Super + Ctrl + G, or click it in waybar)
  wsgroups tui                the same manager in the terminal
  wsgroups focus N            focus window N (1-10) of the current workspace, in the order opened
                              (or as reordered: move)
  wsgroups move ADDR up|down  move a window one place earlier / later in that order (Workspaces panel)
  wsgroups go N|CLASS         go to workspace N (or the one CLASS belongs to); launch its apps if none
                              are open there yet
  wsgroups new                open another window of the current workspace's app (Super + N)
  wsgroups launch N|all [-f]  launch a group's programs (-f: even if some are already open;
                              --background: opened on its workspace, the screen stays here)
  wsgroups tidy               move already-open windows to their group's workspace
  wsgroups list               show the groups and the numbered windows in each
  wsgroups bar                waybar module: follows Hyprland events, prints JSON lines
  wsgroups bar-refresh        redraw it on every screen (window-switcher mode, Super + X)

Groups are stored in ~/.config/hypr/workspaces.conf. This is the module (imported, so Python keeps it
compiled: it runs on every Super + Ctrl + number); the command is the launcher local/bin/wsgroups.
"""
import json, os, re, shlex, signal, subprocess, sys, time

CONF = os.path.expanduser("~/.config/hypr/workspaces.conf")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "wsgroups")
ORDER = os.path.join(STATE, "order.json")   # [address, …]: windows put in order by hand (move)
# one file per running bar module, named by its pid: waybar runs one per screen
BAR_PIDS = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "wsgroups-bar")
MODE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "hypr-winmode")   # window-switcher mode (wsgroups.lua)
ROW = "QWERTYUIOP"   # Super + Q…P in that mode: window 1…10
ICON = "\U000f0570"  # nf-md-view_carousel


# ---- hyprland ----------------------------------------------------------------------------------
def hypr(*args):
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True).stdout


sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from hyprsock import follow, query, request   # the socket: the bar asks on every window event, hyprctl was a process each


def dispatch(lua):
    hypr("dispatch", lua)


def quietly(cls, ws):
    """Windows of cls (a regex) open on ws without the screen going there, until quiet_done(cls)
    (openQuietly in wsgroups.lua)."""
    request(f'eval openQuietly({json.dumps(cls)}, {json.dumps(str(ws))})')


def quiet_done(cls):
    request(f'eval openQuietlyDone({json.dumps(cls)})')


def notify(msg):
    subprocess.run(["notify-send", "-t", "1500", "Workspaces", msg], capture_output=True)


POPUPS = ("TUI.float", "panels.")   # popup panels (and the backdrop behind one)


def windows(ws, clients=None):
    """Windows on workspace ws, oldest first — that order is what Super + Q…P counts — except those put in
    order by hand (move): they come first, as placed, and windows opened since follow them."""
    clients = query("clients") if clients is None else clients
    wins = [c for c in clients or [] if c["workspace"]["id"] == ws and c["mapped"]
            and not c["class"].startswith(POPUPS + ("code-term-",))]
    # popups, panel windows and the kitty beside each VS Code window (codepair.lua) don't count
    placed = {a: i for i, a in enumerate(load_order())}
    return sorted(wins, key=lambda c: (0, placed[c["address"]]) if c["address"] in placed
                  else (1, c.get("stableId", 0)))


_order = (None, [])   # (order.json's mtime, its list): windows() asks for every workspace


def load_order():
    global _order
    try:
        seen = os.stat(ORDER).st_mtime_ns
    except OSError:
        return []
    if seen != _order[0]:
        try:
            with open(ORDER) as f:
                _order = (seen, [a for a in json.load(f) if isinstance(a, str)])
        except (OSError, ValueError, TypeError):
            return []
    return list(_order[1])


def move(address, step):
    """Window address one place earlier (step -1) or later (+1) among its workspace's windows: Super + Q…P,
    the bar and the Workspaces panel follow. Kept in ORDER while the window lives (addresses end with
    the window); the windows of other workspaces keep theirs."""
    clients = query("clients") or []
    me = next((c for c in clients if c["address"] == address), None)
    if not me:
        return False
    order = [c["address"] for c in windows(me["workspace"]["id"], clients)]
    i = order.index(address) if address in order else -1
    return place(address, i + step, clients) if i >= 0 else False


def place(address, to, clients=None):
    """Window address to place `to` (0 = Super + Q) among its workspace's windows, the others moving up
    or down one (a drag in the Workspaces panel); move() is a step of it."""
    clients = clients if clients is not None else query("clients") or []
    me = next((c for c in clients if c["address"] == address), None)
    if not me:
        return False
    order = [c["address"] for c in windows(me["workspace"]["id"], clients)]
    if address not in order or not 0 <= to < len(order) or order.index(address) == to:
        return False
    order.remove(address)
    order.insert(to, address)
    live = {c["address"] for c in clients}
    keep = [a for a in load_order() if a in live and a not in order]
    os.makedirs(STATE, exist_ok=True)
    with open(ORDER, "w") as f:   # a plain file in ~/.local/state
        json.dump(keep + order, f)
    refresh_bars()   # the bar's "2/4" follows at once
    return True


def bar_pids():
    """The pids of the running bar modules; files of ones that ended are dropped (a pid can be reused)."""
    pids = []
    for name in os.listdir(BAR_PIDS) if os.path.isdir(BAR_PIDS) else []:
        try:
            if b"wsgroups" in open(f"/proc/{name}/cmdline", "rb").read():
                pids.append(int(name))
                continue
        except (OSError, ValueError):
            pass
        try:
            os.remove(os.path.join(BAR_PIDS, name))
        except OSError:
            pass
    return pids


def refresh_bars():
    """Every screen's bar module redraws now (SIGUSR1): a change without a Hyprland event."""
    for pid in bar_pids():
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass


def focused(clients=None):
    """The focused window's address (None on an empty workspace); while a popup (a panel, its
    backdrop, a TUI popup) has the focus, the window it opened over, so the bar doesn't change
    under a popup."""
    active = query("activewindow") or {}
    if not active.get("class", "").startswith(POPUPS):
        return active.get("address")
    clients = query("clients") if clients is None else clients
    recent = sorted((c for c in clients or [] if c.get("focusHistoryID", -1) >= 0),
                    key=lambda c: c["focusHistoryID"])
    ws = active["workspace"]["id"]
    return next((c["address"] for c in recent
                 if not c["class"].startswith(POPUPS) and c["workspace"]["id"] == ws), None)


def matches(win, cls):
    try:
        return re.fullmatch(cls, win["class"]) is not None
    except re.error:
        return False


# ---- workspaces.conf ---------------------------------------------------------------------------
def read_conf():
    """(header lines, {ws: {"name", "cls", "launch": [cmd], "new": cmd, "icon": glyph}})"""
    header, groups = [], {}
    try:
        lines = open(CONF).read().splitlines()
    except OSError:
        return header, groups
    body = False
    for line in lines:
        s = line.strip()
        parts = [p.strip() for p in s.split("|", 5)]
        if s and not s.startswith("#") and len(parts) >= 3 and parts[0].isdigit():
            body = True
            launch = [c.strip() for c in parts[3].split(";")] if len(parts) > 3 else []
            # several apps are "a, b" in the file (| separates its columns) and the regex a|b here
            cls = "|".join(c.strip() for c in parts[2].split(",") if c.strip())
            groups[int(parts[0])] = {"name": parts[1], "cls": cls, "launch": [c for c in launch if c],
                                     "new": parts[4] if len(parts) > 4 else "",
                                     "icon": parts[5] if len(parts) > 5 else ""}
        elif not body:
            header.append(line)
    return header, groups


def groups():
    return read_conf()[1]


def write_conf(groups_):
    header, _ = read_conf()
    while header and not header[-1].strip():
        header.pop()
    rows = []
    for ws in sorted(groups_):
        g = groups_[ws]
        cls = ", ".join(g["cls"].split("|"))
        row = f"{ws} | {g['name']:<8} | {cls:<13} | {' ; '.join(g['launch'])}"
        if g.get("icon"):   # the bar button's glyph: the 6th column (needs the 5th, even empty)
            row += f" | {g.get('new', '')} | {g['icon']}"
        elif g.get("new"):
            row += f" | {g['new']}"
        rows.append(row.rstrip())
    with open(CONF, "w") as f:           # write in place: the file is a symlink into ~/dotfiles
        f.write("\n".join(header) + "\n\n" + "\n".join(rows) + "\n")
    hypr("reload")


# ---- actions -----------------------------------------------------------------------------------
def focus(n):
    ws = query("activeworkspace")["id"]
    wins = windows(ws)
    if n > len(wins):
        notify(f"No window {n} here ({len(wins)} open)")
        return
    target = wins[n - 1]
    dispatch(f'hl.dsp.focus({{ window = "address:{target["address"]}" }})')


def launch(ws, force=False):
    g = groups().get(ws)
    if not g or not g["launch"]:
        return False
    if not force and any(matches(w, g["cls"]) for w in windows(ws)):
        return False
    for i, cmd in enumerate(g["launch"]):
        if i:
            time.sleep(0.8)  # keep the opening order = the Super + Q…P order
        spawn(cmd)
    return True


def spawn(cmd):
    argv = [os.path.expanduser(a) for a in shlex.split(cmd)]
    subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def new_window():
    """Another window of this workspace's app (its class rule keeps it on this workspace); none open
    yet (the Control Center closed, say): the app itself, as Super + Ctrl + N would."""
    ws = (query("activeworkspace") or {}).get("id", 0)
    g = groups().get(ws)
    if not g:
        notify(f"Workspace {ws % 10} has no app yet (Super + Ctrl + G to add one)")
        return
    if launch(ws):
        return
    cmd = g.get("new") or (g["launch"][0] if g["launch"] else "")
    if cmd in ("", "-"):
        notify(f"{g['name']} has no new-window command")
        return
    spawn(cmd)


def wait_for(ws, timeout=10):
    """Wait until one of the group's windows has opened on ws."""
    g = groups().get(ws)
    end = time.time() + timeout
    while g and time.time() < end:
        if any(matches(w, g["cls"]) for w in windows(ws)):
            return True
        time.sleep(0.25)
    return False


def go(ws):
    dispatch(f'hl.dsp.focus({{ workspace = "{ws}" }})')
    launch(ws)


def tidy():
    """Every open window to its app's workspace. The kitty beside a VS Code window (class
    code-term-<its address>) isn't a group of its own: it goes where its VS Code window is."""
    moved = 0
    gs = groups()
    clients = query("clients") or []
    home = {}   # VS Code window address -> the workspace its group is on
    for win in clients:
        for ws, g in gs.items():
            if matches(win, g["cls"]):
                home[win["address"]] = ws
    for win in clients:
        code = win["class"].removeprefix("code-term-") if win["class"].startswith("code-term-") else None
        if code and home.get(code) and win["workspace"]["id"] != home[code] and win["workspace"]["id"] > 0:
            dispatch(f'hl.dsp.window.move({{ workspace = "{home[code]}", follow = false, '
                     f'window = "address:{win["address"]}" }})')
            moved += 1
            continue
        for ws, g in gs.items():
            if matches(win, g["cls"]) and win["workspace"]["id"] != ws and win["workspace"]["id"] > 0:
                dispatch(f'hl.dsp.window.move({{ workspace = "{ws}", follow = false, '
                         f'window = "address:{win["address"]}" }})')
                moved += 1
                break
    return moved


def show():
    gs = groups()
    clients = query("clients")
    active = query("activeworkspace")["id"]
    for ws in sorted(set(gs) | {active}):
        g = gs.get(ws, {"name": "(no group)", "cls": "-", "launch": []})
        mark = "*" if ws == active else " "
        print(f"{mark}{ws:>2}  {g['name']:<10} class: {g['cls']}")
        for i, w in enumerate(windows(ws, clients), 1):
            state = " [max]" if w.get("fullscreen") else ""
            print(f"      Super+{ROW[i - 1] if i <= 10 else ' '}  {w['class']}: {w['title'][:50]}{state}")


def guess_launch(cls, pid=None):
    """The app's command from its .desktop entry (matched by StartupWMClass or file name)."""
    dirs = [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications"]
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".desktop"):
                continue
            try:
                text = open(os.path.join(d, n), errors="ignore").read()
            except OSError:
                continue
            wm = re.search(r"^StartupWMClass=(.+)$", text, re.M)
            exe = re.search(r"^Exec=(.+)$", text, re.M)
            if exe and ((wm and wm.group(1).strip().lower() == cls.lower())
                        or n[:-8].lower() == cls.lower()):
                cmd = re.sub(r"\s*%[a-zA-Z]", "", exe.group(1)).strip()
                return os.path.basename(cmd.split()[0]) + cmd[len(cmd.split()[0]):]
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except (OSError, TypeError):
        return cls.lower()


def assign(groups_, ws, cls, pid=None):
    """Put window class cls on workspace ws (in groups_, not saved); a class belongs to one
    workspace only. Returns an error message, or None."""
    try:
        re.compile(cls)
    except re.error:
        return f"not a valid class pattern: {cls}"
    for other, g in list(groups_.items()):
        if other == ws:
            continue
        parts = [p for p in g["cls"].split("|") if p != cls]
        if len(parts) != len(g["cls"].split("|")):
            if parts:
                g["cls"] = "|".join(parts)
            else:
                del groups_[other]
    g = groups_.get(ws)
    if g:
        if cls not in g["cls"].split("|"):
            g["cls"] = f"{g['cls']}|{cls}"
    else:
        name = cls.split(".")[-1].split("-")[-1].capitalize()
        groups_[ws] = {"name": name, "cls": cls, "launch": [guess_launch(cls, pid)]}
    return None


# ---- waybar module -----------------------------------------------------------------------------
def bar_status():
    ws = (query("activeworkspace") or {}).get("id", 0)
    g = groups().get(ws)
    clients = query("clients")
    wins = windows(ws, clients)
    active = focused(clients)
    pos = next((i for i, w in enumerate(wins, 1) if w["address"] == active), 0)
    try:
        switcher = open(MODE).read().startswith("on")
    except OSError:
        switcher = False
    lines = [f"Super+{ROW[i - 1]}  {w['class']}: {w['title'][:40]}" for i, w in enumerate(wins[:10], 1)]
    tip = (f"{g['name']} · workspace {ws}" if g else f"Workspace {ws} (no group)")
    tip += "\n" + ("\n".join(lines) if lines else "no windows")
    tip += "\n\n" + ("Window-switcher mode: on (Super + X: off)" if switcher else
                     "Super + X: window-switcher mode (Super + Q…P pick a window)") + "\nClick: manage groups · right-click: window-switcher mode"
    text = f"{ICON} {pos}/{len(wins)}" if wins else ICON  # the group's name is in the tooltip
    if switcher:   # the mode shows here: accent, and the focused window's letter
        text = f"{ICON} {ROW[pos - 1]} {pos}/{len(wins)}" if 1 <= pos <= 10 else text
    # "empty": just the icon (no count, no title beside it), so the bar centres it
    return {"text": text, "tooltip": tip, "class": ["group" if g else "none"] + ([] if wins else ["empty"])
            + (["switcher"] if switcher else [])}


def bar():
    wanted = (b"workspace>>", b"activewindow>>", b"openwindow>>", b"closewindow>>",
              b"movewindow>>", b"focusedmon>>", b"configreloaded>>")
    last = None
    def emit():
        nonlocal last
        out = json.dumps(bar_status(), ensure_ascii=False)
        if out != last:
            print(out, flush=True)
            last = out
    def events(lines):
        if any(e.startswith(wanted) for e in lines):
            time.sleep(0.05)  # let hyprland settle
            emit()
    # move() and the window-switcher mode change it without a Hyprland event: they signal the bar
    # (SIGUSR1, refresh_bars) instead
    signal.signal(signal.SIGUSR1, lambda *_: emit())
    bar_pids()   # drop the files of bars that ended
    os.makedirs(BAR_PIDS, exist_ok=True)
    open(os.path.join(BAR_PIDS, str(os.getpid())), "w").close()
    follow(events, emit)


def ui():
    sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
    import wsgui  # the GTK window
    wsgui.main(sys.modules[__name__])


# ---- entry -------------------------------------------------------------------------------------
def main(argv):
    if not argv or argv[0] == "ui":
        ui()
        return
    if argv[0] == "tui":
        sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
        import wstui   # the terminal manager (panelkit): imported only here, not on every key press
        wstui.main()
        return
    if argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    cmd, rest = argv[0], argv[1:]
    force = "-f" in rest or "--force" in rest
    background = "--background" in rest
    rest = [a for a in rest if a not in ("-f", "--force", "--background")]
    if cmd == "focus" and rest:
        focus(int(rest[0]))
    elif cmd == "move" and len(rest) == 2 and rest[1] in ("up", "down"):
        move(rest[0], -1 if rest[1] == "up" else 1)
    elif cmd == "new":
        new_window()
    elif cmd == "go" and rest:
        # a class: its group's workspace, wherever Settings → Workspaces put it (Super + Ctrl + P → Projects)
        ws = int(rest[0]) if rest[0].isdigit() else next(
            (n for n, g in groups().items() if matches({"class": rest[0]}, g["cls"])), None)
        if ws is None:
            sys.exit(f"no workspace has {rest[0]}")
        go(ws)
    elif cmd == "launch" and rest:
        targets = sorted(groups()) if rest[0] == "all" else [int(rest[0])]
        home = (query("activeworkspace") or {}).get("id")
        for ws in targets:
            g = groups().get(ws)
            if background and g and g["cls"]:   # opened where it belongs, the screen stays here
                quietly(g["cls"], ws)
            try:
                started = launch(ws, force)
                print(f"workspace {ws}: {'launched' if started else 'already open (use -f)'}")
                if started and background:
                    wait_for(ws)
            finally:
                if background and g and g["cls"]:
                    quiet_done(g["cls"])
        if background and home and (query("activeworkspace") or {}).get("id") != home:
            dispatch(f'hl.dsp.focus({{ workspace = "{home}" }})')
    elif cmd == "tidy":
        print(f"moved {tidy()} window(s)")
    elif cmd == "list":
        show()
    elif cmd == "bar":
        bar()
    elif cmd == "bar-refresh":
        refresh_bars()
    else:
        print(__doc__.strip())
        sys.exit(1)
