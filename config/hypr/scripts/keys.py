#!/usr/bin/env python3
# keys.py — shortcut viewer (Tokyo Night). Reads binds.lua and apps.conf live, so it never goes stale.
#   type  search (every word must match)     ↑↓ / wheel  scroll     PgUp PgDn  page     Esc  clear / close
# A bind shows its trailing "-- comment" as its description when it has one.
import os, re, sys, shutil, termios, tty, select

HYPR = os.path.expanduser("~/.config/hypr")
BINDS, APPS = f"{HYPR}/config/binds.lua", f"{HYPR}/apps.conf"
WSGROUPS = f"{HYPR}/workspaces.conf"

def rgb(h): return f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
FG, DIM, ACCENT, KEY, TRACK = rgb("#c0caf5"), rgb("#565f89"), rgb("#7aa2f7"), rgb("#e0af68"), rgb("#292e42")
BOLD, RESET = "\033[1m", "\033[0m"

# ---- describing actions ------------------------------------------------------------------------
# first matching pattern wins; checked against the bind's action text
DESCRIBE = [
    (r"--incognito", "Browser (incognito)"),
    (r"launch \.\. TERMINAL", "Terminal (new window)"),
    (r"launch \.\. BROWSER", "Browser (new window)"),
    (r"launch \.\. FILE_MANAGER", "File manager (new window)"),
    (r"launch \.\. EDITOR", "Text editor"),
    (r"launch \.\. CALCULATOR", "Calculator"),
    (r"sysmon\.py", "System monitor"),
    (r"keys\.py", "This shortcut list"),
    (r'tui\("btop"\)', "btop"),
    (r'tui\("wiremix"\)', "Audio mixer"),
    (r'tui\("bluetui"\)', "Bluetooth"),
    (r'tui\("nmtui"\)', "Wi-Fi / network"),
    (r'"walker -m symbols"', "Emoji & symbols"),
    (r'"walker -m clipboard"', "Clipboard history"),
    (r'"walker"', "App launcher"),
    (r"power-menu", "Power menu"),
    (r"window\.close", "Close window"),
    (r"togglesplit", "Toggle split direction"),
    (r"window\.pseudo", "Pseudo-tile"),
    (r"float\(\{ action = \"toggle\" \}\)\)$", "Toggle floating"),
    (r'mode = "fullscreen"', "Fullscreen"),
    (r'mode = "maximized"', "Maximize (keeps bar and gaps)"),
    (r"focus\(\{ direction", "Focus window in direction"),
    (r"window\.swap", "Swap window in direction"),
    (r"workspace\.move\(\{ monitor", "Move workspace to monitor"),
    (r"into_group", "Move window into group"),
    (r'workspace = "special', "Send window to scratchpad"),
    (r"follow = false", "Move window to workspace (stay here)"),
    (r"window\.move\(\{ workspace", "Move window to workspace"),
    (r'workspace = "e\+1"', "Next workspace"),
    (r'workspace = "e-1"', "Previous workspace"),
    (r'workspace = "previous"', "Last used workspace"),
    (r"focus\(\{ workspace", "Go to workspace"),
    (r"toggle_special", "Show/hide scratchpad"),
    (r"cycle_next\(\{ next = false", "Previous window"),
    (r"cycle_next", "Next window"),
    (r'monitor = "\+1"', "Next monitor"),
    (r'monitor = "-1"', "Previous monitor"),
    (r"x = -step", "Shrink width"), (r"x = step", "Grow width"),
    (r"y = -step", "Shrink height"), (r"y = step", "Grow height"),
    (r"window\.drag", "Move window"),
    (r"window\.resize\(\)", "Resize window"),
    (r"out_of_group", "Remove window from group"),
    (r"group\.toggle", "Toggle group (tabs)"),
    (r"group\.next", "Next tab in group"),
    (r"group\.prev", "Previous tab in group"),
    (r"group\.active", "Go to tab in group"),
    (r"output-volume raise", "Volume up"), (r"output-volume lower", "Volume down"),
    (r"output-volume \+1", "Volume up (fine)"), (r"output-volume -1", "Volume down (fine)"),
    (r"output-volume mute", "Mute"), (r"input-volume mute", "Mute microphone"),
    (r"brightness raise", "Brightness up"), (r"brightness lower", "Brightness down"),
    (r"brightness \+1", "Brightness up (fine)"), (r"brightness -1", "Brightness down (fine)"),
    (r"play-pause", "Play / pause"), (r"playerctl next", "Next track"), (r"playerctl previous", "Previous track"),
    (r'"makoctl dismiss --all"', "Dismiss all notifications"),
    (r'"makoctl dismiss"', "Dismiss notification"),
    (r"makoctl invoke", "Open notification action"),
    (r"makoctl restore", "Bring back last notification"),
    (r"toggle\.sh notifications", "Do not disturb"),
    (r"SIGUSR1 waybar", "Show/hide waybar"),
    (r"toggle\.sh idle", "Keep screen awake"),
    (r"toggle\.sh nightlight", "Night light"),
    (r"wallpaper\.sh next", "Next wallpaper"),
    (r"lock-session", "Lock screen"),
    (r"screenshot\.sh region", "Screenshot (region)"),
    (r"screenshot\.sh output", "Screenshot (screen)"),
    (r"hyprpicker", "Color picker"),
    (r"zoom_factor = math\.min", "Zoom in"),
    (r"zoom_factor = 1", "Reset zoom"),
]

KEYNAMES = {
    "SUPER": "Super", "SHIFT": "Shift", "CTRL": "Ctrl", "ALT": "Alt", "RETURN": "Enter", "SPACE": "Space",
    "TAB": "Tab", "ESCAPE": "Esc", "Escape": "Esc", "comma": ",", "PRINT": "PrtSc",
    "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓", "code:20": "-", "code:21": "=",
    "mouse:272": "Left drag", "mouse:273": "Right drag", "mouse_down": "Scroll ↓", "mouse_up": "Scroll ↑",
    "XF86AudioRaiseVolume": "Vol+", "XF86AudioLowerVolume": "Vol−", "XF86AudioMute": "Mute key",
    "XF86AudioMicMute": "Mic key", "XF86MonBrightnessUp": "Bright+", "XF86MonBrightnessDown": "Bright−",
    "XF86AudioPlay": "Play", "XF86AudioPause": "Pause", "XF86AudioNext": "Next", "XF86AudioPrev": "Prev",
    "XF86Calculator": "Calc key", "XF86PowerOff": "Power key",
}

def pretty_keys(keys):
    return " + ".join(KEYNAMES.get(k.strip(), k.strip().upper() if len(k.strip()) == 1 else k.strip())
                      for k in keys.split("+"))

def describe(action, comment):
    if comment:
        return comment[0].upper() + comment[1:]
    for pat, text in DESCRIBE:
        if re.search(pat, action):
            return text
    return action.strip()[:50]

# ---- reading binds.lua -------------------------------------------------------------------------
def key_expr(expr, loop):
    """'"SUPER + " .. key' -> 'SUPER + ←/→/↑/↓', using what the enclosing for-loop iterates over."""
    parts = []
    for tok in re.split(r"\s*\.\.\s*", expr.strip()):
        if tok.startswith('"'):
            parts.append(tok.strip('"'))
        else:
            parts.append(loop.get(re.sub(r"\(.*", "", tok), tok))
    return "".join(parts)

def read_binds():
    sections, current, loop, seen = [], None, {}, set()
    lines = open(BINDS).read().splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r"^-+\s*([A-Z][A-Z &]+?)\s*-+$", s)
        if m:
            current = (m.group(1).title(), []); sections.append(current); continue
        if s.startswith("for key, dir in pairs"):
            loop = {"key": "←/→/↑/↓"}
        elif s.startswith("for ws = 1, 10"):
            loop = {"key": "1…0"}
        elif s.startswith("for mods, step"):
            loop = {"mods": ""}
        elif s.startswith("for i = 1, 5"):
            loop = {"digitCode": "1…5"}
        elif s == "end" and not line.startswith(" "):
            loop = {}
        m = re.match(r'^bind\((.+?),\s*(.*)$', s)
        if not m or current is None:
            continue
        keys = key_expr(m.group(1), loop)
        rest = m.group(2)
        comment = ""
        cm = re.search(r"\)\s*--\s*(.+)$", rest)
        if cm:
            comment, rest = cm.group(1), rest[:cm.start() + 1]
        if rest.startswith("function"):   # inline function: describe from its body
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip().startswith("end"): break
                body.append(nxt.strip())
            rest = " ".join(body)
        keys = pretty_keys(keys)
        desc = describe(rest, comment)
        if (keys, desc) in seen:
            continue
        seen.add((keys, desc))
        current[1].append((keys, desc))
    return [s for s in sections if s[1]]

def read_apps():
    rows = []
    try:
        for line in open(APPS):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3:
                rows.append((pretty_keys(parts[0]), f"Jump to / open {parts[2].split()[0]}"))
    except OSError:
        pass
    return rows

def read_wsgroups():
    rows = [("Alt + 1…0", "Switch to window N in this workspace"),
            ("Super + Ctrl + 1…0", "Go to workspace, launch its apps if needed")]
    try:
        for line in open(WSGROUPS):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0].isdigit():
                rows.append((f"Workspace {parts[0]}", f"{parts[1]} — {parts[2]}"))
    except OSError:
        return []
    return rows

def sections():
    out = read_binds()
    apps = read_apps()
    if apps:
        out.insert(1, ("App Hotkeys (apps.conf)", apps))
    groups = read_wsgroups()
    if groups:
        out.insert(2 if apps else 1, ("Workspace Groups (workspaces.conf)", groups))
    return out

# ---- drawing -----------------------------------------------------------------------------------
def build(query):
    rows = []
    words = query.lower().split()
    for title, items in sections():
        hits = [(k, d) for k, d in items if all(w in f"{k} {d}".lower() for w in words)]
        if not hits: continue
        if rows: rows.append(("", ""))
        rows.append(("#", title))
        rows.extend(hits)
    return rows

def render(rows, top, query):
    cols, height = shutil.get_terminal_size()
    w = min(cols - 4, 90)
    pad = " " * max((cols - w) // 2, 0)
    kw = min(max((len(k) for k, _ in rows if k and k != "#"), default=10) + 2, w // 2)
    body = height - 7
    box = f"{query}{FG}▏{RESET}" if query else f"{FG}▏{RESET}{DIM}type to search…{RESET}"
    out = [pad + f"{FG}{BOLD}Shortcuts{RESET}", "",
           pad + f"{ACCENT}\uf002{RESET}  {FG}{box}",
           pad + f"{TRACK}{'─' * w}{RESET}", ""]
    if not rows:
        out.append(pad + f"{DIM}No shortcut matches “{query}”{RESET}")
    for key, desc in rows[top:top + body]:
        if key == "#":
            out.append(pad + f"{ACCENT}{BOLD}{desc}{RESET} {TRACK}{'─' * (w - len(desc) - 1)}{RESET}")
        elif key:
            out.append(pad + f"{KEY}{key:<{kw}}{RESET}{FG}{desc[:w - kw]}{RESET}")
        else:
            out.append("")
    out += [""] * (body - max(len(rows[top:top + body]), 0 if rows else 1))
    more = f"   {top + 1}–{min(top + body, len(rows))} of {len(rows)}" if len(rows) > body else ""
    out.append(pad + f"{DIM}↑↓ scroll   esc {'clear' if query else 'close'}{more}{RESET}")
    sys.stdout.write("\033[H\033[2J" + "\n".join(out))
    sys.stdout.flush()
    return body

def read_key(fd):
    ch = os.read(fd, 1)
    if ch != b"\x1b":
        return ch.decode(errors="ignore")
    if not select.select([fd], [], [], 0.03)[0]:
        return "ESC"
    seq = os.read(fd, 16).decode(errors="ignore")
    if seq.startswith("[<"):        # SGR mouse: wheel = 64 / 65
        btn = seq[2:].split(";")[0]
        return {"64": "UP", "65": "DOWN"}.get(btn, "")
    return {"[A": "UP", "[B": "DOWN", "[5~": "PGUP", "[6~": "PGDN", "[H": "HOME", "[F": "END"}.get(seq, "")

def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l\033[?1000h\033[?1006h")  # alt screen, no cursor, mouse wheel
    query, top = "", 0
    try:
        while True:
            rows = build(query)
            body = render(rows, top, query)
            k = read_key(fd)
            last = max(len(rows) - body, 0)
            if k == "ESC":
                if not query: break
                query, top = "", 0
            elif k in ("\x7f", "\b"):  query, top = query[:-1], 0
            elif k == "\x15":          query, top = "", 0          # Ctrl+U
            elif k == "DOWN":          top = min(top + 1, last)
            elif k == "UP":            top = max(top - 1, 0)
            elif k == "PGDN":          top = min(top + body, last)
            elif k == "PGUP":          top = max(top - body, 0)
            elif k == "HOME":          top = 0
            elif k == "END":           top = last
            elif len(k) == 1 and k.isprintable():
                query, top = query + k, 0
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?1000l\033[?1006l\033[?7h\033[?25h\033[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

if __name__ == "__main__":
    main()
