#!/usr/bin/env python3
# settingslib.py — the Settings panel's backend (settingsgui.py is the window): reads every setting,
# writes it where it lives, applies it live. No GTK here, so each part can be tried from a shell:
#   python3 -c 'import settingslib as s; print(s.hypr_get("general:gaps_in"))'
#
# Where things are kept:
#   Hyprland options     config/hypr/settings.json (shared, in git) -> generated modules/settings.lua,
#                        required last in hyprland.lua, so a value set here wins over the hand-written
#                        modules. Per-PC values (monitor modes) go to settings.local.json ->
#                        modules/settings_local.lua (gitignored, like codesync/machine.json).
#   everything else      line edits of the known keys in that tool's own file (hypridle.conf,
#                        mako/config, waybar/config.jsonc, mimeapps.list, variables.lua…).
# Every file is rewritten in place (open(path, "w")): config/* are symlinks into the dotfiles repo
# and a write-to-temp-then-rename would replace the link with a plain file.
import glob, json, os, re, shutil, subprocess

HOME = os.path.expanduser("~")
CFG = f"{HOME}/.config"
HYPR = f"{CFG}/hypr"
SETTINGS = f"{HYPR}/settings.json"
LOCAL = f"{HYPR}/settings.local.json"
LUA = f"{HYPR}/modules/settings.lua"
LUA_LOCAL = f"{HYPR}/modules/settings_local.lua"
SCRIPTS = f"{HYPR}/scripts"
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")


# ---- plumbing ----------------------------------------------------------------------------------
def sh(*cmd, timeout=10):
    """(returncode, stdout) of a command; never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def spawn(*cmd):
    """Start a program detached from the panel (it outlives the window)."""
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def write(path, text):
    """In place: a symlink stays a symlink."""
    with open(path, "w") as f:
        f.write(text)


def load(path):
    try:
        return json.loads(read(path, "{}") or "{}")
    except ValueError:
        return {}


def save(path, data):
    write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def store(local=False):
    return load(LOCAL if local else SETTINGS)


def remember(section, key, value, local=False):
    """settings.json[section][key] = value (None removes it)."""
    path = LOCAL if local else SETTINGS
    data = load(path)
    part = data.setdefault(section, {})
    if value is None:
        part.pop(key, None)
    else:
        part[key] = value
    save(path, data)


def set_line(path, pattern, line, after=None):
    """Replace the first line matching pattern with line; if none matches, insert it after the
    first line matching `after` (or at the end). Returns True if the file changed."""
    text = read(path)
    lines = text.splitlines()
    rx = re.compile(pattern)
    for i, l in enumerate(lines):
        if rx.search(l):
            if l == line:
                return False
            lines[i] = line
            break
    else:
        at = len(lines)
        if after:
            ax = re.compile(after)
            at = next((i + 1 for i, l in enumerate(lines) if ax.search(l)), len(lines))
        lines.insert(at, line)
    write(path, "\n".join(lines) + "\n")
    return True


# ---- Hyprland options --------------------------------------------------------------------------
def hypr_get(opt):
    """Current value of a Hyprland option ("general:gaps_in"), as Hyprland has it now."""
    rc, out = sh("hyprctl", "getoption", opt, "-j")
    try:
        data = json.loads(out)
    except ValueError:
        return None
    for kind in ("int", "float", "bool", "str"):
        if kind in data:
            v = data[kind]
            return round(v, 2) if kind == "float" else v
    if "css" in data:   # gaps: "top right bottom left"
        return int(str(data["css"]).split()[0])
    return None


def lua_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))   # a JSON string is a valid Lua string


def lua_table(flat, indent="    "):
    """{"general:gaps_in": 4, "decoration:blur:size": 8} -> nested Lua table source."""
    tree = {}
    for key, value in flat.items():
        node = tree
        parts = key.split(":")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def emit(node, depth):
        pad = indent * depth
        out = []
        for k in sorted(node):
            v = node[k]
            if isinstance(v, dict):
                out.append(f"{pad}{k} = {{")
                out.extend(emit(v, depth + 1))
                out.append(f"{pad}}},")
            else:
                out.append(f"{pad}{k} = {lua_value(v)},")
        return out
    return "{\n" + "\n".join(emit(tree, 1)) + "\n}"


def hypr_eval(code):
    """Run Lua in the running Hyprland; returns an error message or None."""
    rc, out = sh("hyprctl", "eval", code)
    return None if rc == 0 and out.strip() == "ok" else (out or "hyprctl eval failed")


def hypr_set(opt, value):
    """Set a Hyprland option now and keep it (settings.json -> settings.lua)."""
    remember("hypr", opt, value)
    generate()
    return hypr_eval(f"hl.config({lua_table({opt: value})})")


def hypr_reset(opt):
    """Forget a value set here: the hand-written modules decide again (after a reload)."""
    remember("hypr", opt, None)
    generate()
    sh("hyprctl", "reload")


# ---- animations: one speed for all (scales the speeds in animations.lua) ------------------------
ANIM_SPEEDS = {"slow": 1.6, "normal": 1.0, "fast": 0.6}
ANIM_RX = re.compile(r'^hl\.animation\(\{\s*leaf\s*=\s*"(\w+)",\s*enabled\s*=\s*true,\s*speed\s*=\s*([\d.]+),(.*)\}\)')


def animation_lines(speed_name, workspaces):
    """hl.animation() calls for the chosen speed and workspace slide, from animations.lua's own."""
    k = ANIM_SPEEDS.get(speed_name, 1.0)
    out = []
    for line in read(f"{HYPR}/modules/animations.lua").splitlines():
        m = ANIM_RX.match(line.strip())
        if m and k != 1.0:
            leaf, speed, rest = m.group(1), float(m.group(2)), m.group(3).rstrip().rstrip(",")
            out.append(f'hl.animation({{ leaf = "{leaf}", enabled = true, speed = {round(speed * k, 2)},{rest} }})')
    if workspaces:
        out.append(f'hl.animation({{ leaf = "workspaces", enabled = true, speed = {round(4 * k, 2)}, '
                   f'bezier = "easeOutQuint", style = "slide" }})')
    return out


def anim_get():
    a = store().get("anim", {})
    return a.get("speed", "normal"), bool(a.get("workspaces", False))


def anim_set(speed=None, workspaces=None):
    cur_speed, cur_ws = anim_get()
    speed = cur_speed if speed is None else speed
    workspaces = cur_ws if workspaces is None else workspaces
    remember("anim", "speed", speed)
    remember("anim", "workspaces", workspaces)
    generate()
    # back to the file's own speeds first (a slower preset must not stack on a faster one)
    sh("hyprctl", "reload")
    return None


# ---- generated Lua -----------------------------------------------------------------------------
HEADER = """-- GENERATED by the Settings panel (Super + I, local/lib/panels/settingsgui.py) from {src}.
-- Don't edit: change the setting in the panel, or remove it from {src} and the hand-written
-- module that sets it decides again. Required last in hyprland.lua, so these values win.
"""


def generate():
    shared = store()
    parts = [HEADER.format(src="settings.json")]
    if shared.get("hypr"):
        parts.append(f"hl.config({lua_table(shared['hypr'])})\n")
    speed, ws = anim_get()
    lines = animation_lines(speed, ws)
    if lines:
        parts.append("\n".join(lines) + "\n")
    if shared.get("remaps"):
        parts.append(remap_lua(shared["remaps"]))
    write(LUA, "\n".join(parts))

    local = store(local=True)
    parts = [HEADER.format(src="settings.local.json (this PC only)")]
    for name, m in sorted(local.get("monitors", {}).items()):
        parts.append(monitor_lua(name, m["mode"], m["scale"]))
    for ws, mon in sorted(ws_monitors().items()):   # after wsgroups.lua's rule: this monitor wins
        parts.append(f'hl.workspace_rule({{ workspace = "{ws}", monitor = {lua_value(mon)}, persistent = true }})')
    write(LUA_LOCAL, "\n".join(parts))


# ---- key mapping: keys and mouse buttons that do something else -------------------------------
# settings.json "remaps": [{"key": "mouse:275" / "F5" (a Hyprland key name), "mods": "CTRL SHIFT"
# (may be ""), "action": an id below, "arg": a shortcut or a command}]. generate() makes them
# hl.bind() calls in settings.lua; a mapped key or button does only that, everywhere.
# Copy / Paste… send the usual shortcut to the focused window, except in a terminal (kitty, agentmux,
# the VS Code kittys): there Ctrl+C interrupts the program, so Copy takes the selection (kitty and
# tmux keep it in the primary selection) to the clipboard, and Paste is Ctrl+Shift+V.
MOUSE = {272: "Left button", 273: "Right button", 274: "Middle button", 275: "Back side button",
         276: "Forward side button", 277: "Forward button", 278: "Back button", 279: "Task button"}
# keys some mice send for their side buttons (from their own keyboard part), and other named keys
KEY_NAMES = {"XF86Back": "Back key (XF86Back)", "XF86Forward": "Forward key (XF86Forward)"}
OSD = 'swayosd-client --monitor "$(hyprctl activeworkspace -j | jq -r .monitor)" '
REMAP_ACTIONS = [   # (id, label, Lua for the bind's action; {arg} filled in)
    ("copy", "Copy", 'function() remapCopy() end'),
    ("paste", "Paste", 'function() remapSend("CTRL", "v", "CTRL SHIFT") end'),
    ("cut", "Cut", 'function() remapSend("CTRL", "x", nil) end'),
    ("undo", "Undo", 'function() remapSend("CTRL", "z") end'),
    ("redo", "Redo", 'function() remapSend("CTRL SHIFT", "z") end'),
    ("select_all", "Select all", 'function() remapSend("CTRL", "a") end'),
    ("back", "Back (browser, files)", 'function() remapSend("ALT", "Left") end'),
    ("forward", "Forward (browser, files)", 'function() remapSend("ALT", "Right") end'),
    ("play", "Play / pause", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--playerctl play-pause") + ')'),
    ("next", "Next track", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--playerctl next") + ')'),
    ("previous", "Previous track", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--playerctl previous") + ')'),
    ("volume_up", "Volume up", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--output-volume raise") + ')'),
    ("volume_down", "Volume down", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--output-volume lower") + ')'),
    ("mute", "Mute", 'hl.dsp.exec_cmd(' + json.dumps(OSD + "--output-volume mute-toggle") + ')'),
    ("screenshot", "Screenshot of a region", 'hl.dsp.exec_cmd("~/.config/hypr/scripts/screenshot.sh region")'),
    ("ws_next", "Next workspace", 'hl.dsp.focus({ workspace = "e+1" })'),
    ("ws_prev", "Previous workspace", 'hl.dsp.focus({ workspace = "e-1" })'),
    ("close", "Close the window", 'hl.dsp.window.close()'),
    ("shortcut", "Send a shortcut…", None),   # arg: "CTRL + T"
    ("command", "Run a command…", None),      # arg: a shell command
    ("nothing", "Nothing (turn it off)", 'hl.dsp.no_op()'),
]
REMAP_LABEL = {a: label for a, label, _ in REMAP_ACTIONS}
REMAP_HELPERS = """-- key mapping (Settings → Key mapping): Copy / Paste… to the focused window, terminals aside
local remapTerms = { kitty = true, agentmux = true, ghostty = true, Alacritty = true, konsole = true }
local function remapInTerm()
    local w = hl.get_active_window()
    return w ~= nil and (remapTerms[w.class] or w.class:find("^code%-term%-") ~= nil)
end
function remapSend(mods, key, termMods)
    local inTerm = remapInTerm()
    hl.dispatch(hl.dsp.send_shortcut({ mods = (inTerm and termMods) or mods, key = key }))
end
function remapCopy()
    if remapInTerm() then
        hl.exec_cmd("sh -c 'wl-paste --primary --no-newline | wl-copy'")
    else
        hl.dispatch(hl.dsp.send_shortcut({ mods = "CTRL", key = "c" }))
    end
end
"""


def remap_combo(r):
    """The key as hl.bind() takes it: "CTRL + SHIFT + F5", "mouse:275"."""
    return " + ".join(r.get("mods", "").split() + [r["key"]])


def remap_label(r):
    """For people: "Ctrl + Shift + F5", "Back side button"."""
    key = r["key"]
    if key.startswith("mouse:"):
        name = MOUSE.get(int(key[6:]), f"Mouse button {key[6:]}")
    elif key in KEY_NAMES:
        name = KEY_NAMES[key]
    else:
        name = key.upper() if len(key) == 1 else key.replace("_", " ")
    mods = {"CTRL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt", "SUPER": "Super"}
    return " + ".join([mods.get(m, m.title()) for m in r.get("mods", "").split()] + [name])


def remap_describe(r):
    """What a mapping does, for people: "Copy", "Send CTRL + T", "Run: kitty"."""
    arg = (r.get("arg") or "").strip()
    if r.get("action") == "shortcut":
        return f"Send {arg}"
    if r.get("action") == "command":
        return f"Run: {arg}"
    return REMAP_LABEL.get(r.get("action"), r.get("action", ""))


def remap_action_lua(r):
    """The Lua action of one mapping, or None if it can't be made (an empty shortcut / command)."""
    action, arg = r.get("action"), (r.get("arg") or "").strip()
    if action == "command":
        return f"hl.dsp.exec_cmd({lua_value(arg)})" if arg else None
    if action == "shortcut":
        parts = [p.strip() for p in arg.replace("+", " ").split() if p.strip()]
        if not parts:
            return None
        mods, key = " ".join(p.upper() for p in parts[:-1]), parts[-1]
        return f"function() remapSend({lua_value(mods)}, {lua_value(key)}) end"
    return next((lua for a, _, lua in REMAP_ACTIONS if a == action and lua), None)


def remap_lua(remaps):
    lines = [REMAP_HELPERS]
    for r in remaps:
        lua = remap_action_lua(r)
        if lua:
            lines.append(f"hl.bind({lua_value(remap_combo(r))}, {lua}) -- {REMAP_LABEL.get(r['action'], r['action'])}")
    return "\n".join(lines) + "\n"


def remaps():
    return list(store().get("remaps", []))


def remaps_set(new):
    """Save the mappings and apply them (a reload: a bind made earlier can't be taken back live)."""
    data = load(SETTINGS)
    if new:
        data["remaps"] = new
    else:
        data.pop("remaps", None)
    save(SETTINGS, data)
    generate()
    sh("hyprctl", "reload")
    rc, out = sh("hyprctl", "configerrors")
    return out.strip() or None


# ---- workspaces on screens: per PC (screen names differ), settings.local.json "ws_monitors" -------
def ws_monitors():
    """{workspace: monitor name} for the workspaces pinned to a screen."""
    return {int(k): v for k, v in store(local=True).get("ws_monitors", {}).items() if v}


def ws_monitor_set(ws, monitor):
    """Pin workspace ws to a screen (None: wherever you are) and move it there now."""
    remember("ws_monitors", str(ws), monitor or None, local=True)
    generate()
    sh("hyprctl", "reload")
    if monitor:
        return hypr_eval(f'hl.dispatch(hl.dsp.workspace.move({{ workspace = "{ws}", monitor = {lua_value(monitor)} }}))')
    return None


def ws_monitors_swap(a, b):
    """Two workspaces swapped places (Settings → Workspaces, reorder): their screens go with them."""
    pins = ws_monitors()
    remember("ws_monitors", str(a), pins.get(b), local=True)
    remember("ws_monitors", str(b), pins.get(a), local=True)
    generate()


# ---- display -----------------------------------------------------------------------------------
def monitors():
    rc, out = sh("hyprctl", "monitors", "all", "-j")
    try:
        return json.loads(out)
    except ValueError:
        return []


def monitor_lua(name, mode, scale):
    return (f'hl.monitor({{ output = "{name}", mode = "{mode}", position = "auto", '
            f'scale = "{scale}" }})\n')


def monitor_set(name, mode, scale, keep=True):
    """Apply a mode now; keep=True also saves it for this PC."""
    err = hypr_eval(monitor_lua(name, mode, scale).strip())
    if keep and not err:
        data = store(local=True)
        data.setdefault("monitors", {})[name] = {"mode": mode, "scale": scale}
        save(LOCAL, data)
        generate()
    return err


# ---- keyboard layouts --------------------------------------------------------------------------
def xkb_layouts():
    """[(code, description)] from the xkb rules."""
    out, on = [], False
    for line in read("/usr/share/X11/xkb/rules/evdev.lst").splitlines():
        if line.startswith("! "):
            on = line.strip() == "! layout"
            continue
        if on and line.strip():
            code, _, desc = line.strip().partition(" ")
            out.append((code, desc.strip()))
    return out


SWITCH_KEYS = [("", "None"), ("grp:alt_shift_toggle", "Alt + Shift"), ("grp:win_space_toggle", "Super + Space"),
               ("grp:ctrl_shift_toggle", "Ctrl + Shift"), ("grp:caps_toggle", "Caps Lock"),
               ("grp:alt_space_toggle", "Alt + Space")]


# ---- wallpaper ---------------------------------------------------------------------------------
WALLS = f"{HOME}/.local/share/backgrounds/wallpapers"
WALL_STATE = f"{os.environ.get('XDG_STATE_HOME', HOME + '/.local/state')}/wallpaper"


def wallpapers():
    return sorted(p for p in glob.glob(f"{WALLS}/*") if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))


def wallpaper_get():
    return read(WALL_STATE).strip()


def wallpaper_set(path=None, mode=None):
    if mode:
        remember("wallpaper", "mode", mode)
    spawn(f"{SCRIPTS}/wallpaper.sh", path or wallpaper_get() or "restore")


def wallpaper_mode():
    return store().get("wallpaper", {}).get("mode", "fill")


# ---- theme -------------------------------------------------------------------------------------
GTK_INI = f"{CFG}/gtk-3.0/settings.ini"
XSETTINGS = f"{CFG}/xsettingsd/xsettingsd.conf"
UWSM_ENV = f"{CFG}/uwsm/env"
QT6CT = f"{CFG}/qt6ct/qt6ct.conf"
ICON_DIRS = ["/usr/share/icons", f"{HOME}/.local/share/icons", f"{HOME}/.icons"]


def gsettings(key, value=None, schema="org.gnome.desktop.interface"):
    if value is None:
        return sh("gsettings", "get", schema, key)[1].strip("'")
    sh("gsettings", "set", schema, key, str(value))


def themes(kind):
    """Installed icon themes (kind "icons") or cursor themes (kind "cursors")."""
    names = set()
    for d in ICON_DIRS:
        for t in glob.glob(f"{d}/*/"):
            name = os.path.basename(t.rstrip("/"))
            has_cursors = os.path.isdir(f"{t}cursors")
            is_icons = os.path.isfile(f"{t}index.theme") and any(
                os.path.isdir(f"{t}{s}") for s in ("scalable", "48x48", "symbolic", "16x16", "apps", "places"))
            if (kind == "cursors" and has_cursors) or (kind == "icons" and is_icons and name not in ("default", "hicolor")):
                names.add(name)
    return sorted(names, key=str.lower)


def theme_get():
    return {"dark": gsettings("color-scheme") == "prefer-dark", "icons": gsettings("icon-theme"),
            "cursor": gsettings("cursor-theme"), "cursor_size": int(gsettings("cursor-size") or 24),
            "font": gsettings("font-name"), "mono": gsettings("monospace-font-name")}


def theme_set_dark(dark):
    gsettings("color-scheme", "prefer-dark" if dark else "default")
    gsettings("gtk-theme", "adw-gtk3-dark" if dark else "adw-gtk3")
    set_line(GTK_INI, r"^gtk-application-prefer-dark-theme=", f"gtk-application-prefer-dark-theme={int(dark)}", r"^\[Settings\]")


def theme_set_icons(name):
    gsettings("icon-theme", name)
    set_line(GTK_INI, r"^gtk-icon-theme-name=", f"gtk-icon-theme-name={name}", r"^\[Settings\]")
    set_line(XSETTINGS, r"^Net/IconThemeName ", f'Net/IconThemeName "{name}"')
    set_line(QT6CT, r"^icon_theme=", f"icon_theme={name}", r"^\[Appearance\]")


def theme_set_cursor(name, size):
    gsettings("cursor-theme", name)
    gsettings("cursor-size", size)
    set_line(GTK_INI, r"^gtk-cursor-theme-name=", f"gtk-cursor-theme-name={name}", r"^\[Settings\]")
    set_line(GTK_INI, r"^gtk-cursor-theme-size=", f"gtk-cursor-theme-size={size}", r"^\[Settings\]")
    set_line(XSETTINGS, r"^Gtk/CursorThemeName ", f'Gtk/CursorThemeName "{name}"')
    set_line(XSETTINGS, r"^Gtk/CursorThemeSize ", f"Gtk/CursorThemeSize {size}")
    for var, val in (("HYPRCURSOR_THEME", f'"{name}"'), ("HYPRCURSOR_SIZE", size),
                     ("XCURSOR_THEME", f'"{name}"'), ("XCURSOR_SIZE", size)):
        set_line(UWSM_ENV, rf"^export {var}=", f"export {var}={val}")
    sh("hyprctl", "setcursor", name, str(size))


def theme_set_font(name, mono=False):
    gsettings("monospace-font-name" if mono else "font-name", name)
    if mono:   # no file has a monospace font: settings.json keeps it, for restore_desktop()
        remember("theme", "mono", name)
    else:
        set_line(GTK_INI, r"^gtk-font-name=", f"gtk-font-name={name}", r"^\[Settings\]")


def restore_desktop():
    """Put gsettings (dconf: not in the repo) back in line with the repo's files: the theme, icons,
    cursor and fonts from gtk-3.0/settings.ini, the monospace font and the clock format from
    settings.json, and the night-light timers. Run at every login (autostart.lua), so a new PC and a
    change synced from the other PC look the same everywhere; also `settingslib.py restore`."""
    ini = dict(line.split("=", 1) for line in read(GTK_INI).splitlines() if "=" in line and not line.startswith("#"))
    done = []

    def put(key, value):
        if value not in (None, "") and str(gsettings(key)) != str(value):
            gsettings(key, value)
            done.append(f"{key}={value}")
    dark = ini.get("gtk-application-prefer-dark-theme", "0").strip() == "1"
    put("color-scheme", "prefer-dark" if dark else "default")
    put("gtk-theme", "adw-gtk3-dark" if dark else "adw-gtk3")
    put("icon-theme", ini.get("gtk-icon-theme-name", "").strip())
    put("cursor-theme", ini.get("gtk-cursor-theme-name", "").strip())
    put("cursor-size", ini.get("gtk-cursor-theme-size", "").strip())
    put("font-name", ini.get("gtk-font-name", "").strip())
    shared = store()
    put("monospace-font-name", shared.get("theme", {}).get("mono", ""))
    if "h24" in shared.get("clock", {}):
        put("clock-format", "24h" if shared["clock"]["h24"] else "12h")
    n = shared.get("nightlight", {})
    if n.get("schedule") and not os.path.exists(f"{UNITS}/settings-nightlight-on.timer"):
        night_set_schedule(True, n.get("start", "20:00"), n.get("end", "07:00"))
        done.append("night-light timers")
    return done


# ---- night light -------------------------------------------------------------------------------
UNITS = f"{CFG}/systemd/user"


def night_get():
    n = store().get("nightlight", {})
    return {"on": sh("pgrep", "-x", "hyprsunset")[0] == 0, "temp": int(n.get("temp", 4000)),
            "schedule": bool(n.get("schedule", False)), "start": n.get("start", "20:00"), "end": n.get("end", "07:00")}


def night_set_on(on):
    spawn(f"{SCRIPTS}/toggle.sh", "nightlight", "on" if on else "off")


def night_set_temp(temp):
    remember("nightlight", "temp", int(temp))
    if sh("pgrep", "-x", "hyprsunset")[0] == 0:   # running: change it live over its IPC
        sh("hyprctl", "hyprsunset", "temperature", str(int(temp)))


def night_set_schedule(enabled, start, end):
    """Two systemd user timers switch it on and off: nothing runs in between."""
    remember("nightlight", "schedule", bool(enabled))
    remember("nightlight", "start", start)
    remember("nightlight", "end", end)
    os.makedirs(UNITS, exist_ok=True)
    for name, when, arg in (("on", start, "on"), ("off", end, "off")):
        unit = f"settings-nightlight-{name}"
        write(f"{UNITS}/{unit}.service",
              f"[Unit]\nDescription=Night light {arg} (Settings panel schedule)\n\n"
              f"[Service]\nType=oneshot\nExecStart={SCRIPTS}/toggle.sh nightlight {arg} quiet\n")
        write(f"{UNITS}/{unit}.timer",
              f"[Unit]\nDescription=Night light {arg} at {when}\n\n"
              f"[Timer]\nOnCalendar=*-*-* {when}:00\n\n[Install]\nWantedBy=timers.target\n")
    sh("systemctl", "--user", "daemon-reload")
    for name in ("on", "off"):
        sh("systemctl", "--user", "enable" if enabled else "disable", "--now", f"settings-nightlight-{name}.timer")
    if enabled:   # right now: on if inside the window
        spawn(f"{SCRIPTS}/toggle.sh", "nightlight", "auto", "quiet")


# ---- idle & lock -------------------------------------------------------------------------------
HYPRIDLE = f"{HYPR}/hypridle.conf"
NEVER = 31536000   # a year: hypridle has no "never", so a timeout that never comes


def _listener_timeout(text, marker):
    """(line index, seconds) of the timeout in the listener block whose on-timeout contains marker."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip().startswith("on-timeout") and marker in l:
            for j in range(i, -1, -1):
                m = re.match(r"\s*timeout\s*=\s*(\d+)", lines[j])
                if m:
                    return j, int(m.group(1))
                if lines[j].strip().startswith("listener"):
                    break
    return None, None


def idle_get():
    text = read(HYPRIDLE)
    _, lock = _listener_timeout(text, "lock-session")
    _, off = _listener_timeout(text, "dpms")
    before = bool(re.search(r"^\s*before_sleep_cmd\s*=", text, re.M))
    return {"lock": lock, "off": off, "before_sleep": before, "awake": sh("pgrep", "-x", "hypridle")[0] != 0}


def idle_set(lock=None, off=None, before_sleep=None):
    text = read(HYPRIDLE)
    lines = text.splitlines()
    for marker, secs in (("lock-session", lock), ("dpms", off)):
        if secs is None:
            continue
        i, _ = _listener_timeout("\n".join(lines), marker)
        if i is not None:
            lines[i] = re.sub(r"\d+", str(int(secs)), lines[i], count=1)
    if before_sleep is not None:
        for i, l in enumerate(lines):
            if re.match(r"\s*#?\s*before_sleep_cmd\s*=", l):
                body = re.sub(r"^(\s*)#\s*", r"\1", l)
                lines[i] = body if before_sleep else re.sub(r"^(\s*)", r"\1# ", body, count=1)
    write(HYPRIDLE, "\n".join(lines) + "\n")
    if sh("pgrep", "-x", "hypridle")[0] == 0:   # restart it to read the file (unless "stay awake" is on)
        sh("pkill", "-x", "hypridle")
        spawn("uwsm", "app", "--", "hypridle")


def idle_stay_awake(on):
    awake = sh("pgrep", "-x", "hypridle")[0] != 0
    if on != awake:
        spawn(f"{SCRIPTS}/toggle.sh", "idle")


# ---- power profile -----------------------------------------------------------------------------
def power_get():
    rc, out = sh("powerprofilesctl", "get")
    return out if rc == 0 else None


def power_profiles():
    rc, out = sh("powerprofilesctl", "list")
    return [m.group(1) for m in re.finditer(r"^\*?\s*([\w-]+):$", out, re.M)] if rc == 0 else []


def power_set(profile):
    rc, out = sh("powerprofilesctl", "set", profile)
    return None if rc == 0 else out


# ---- notifications (mako) ----------------------------------------------------------------------
MAKO = f"{CFG}/mako/config"
CORNERS = [("top-left", "Top left"), ("top-center", "Top centre"), ("top-right", "Top right"),
           ("bottom-left", "Bottom left"), ("bottom-center", "Bottom centre"), ("bottom-right", "Bottom right")]


def _mako_global(text):
    """The lines before the first [section]."""
    lines = text.splitlines()
    end = next((i for i, l in enumerate(lines) if l.startswith("[")), len(lines))
    return lines, end


def mako_get():
    text = read(MAKO)
    lines, end = _mako_global(text)
    vals = dict(l.split("=", 1) for l in lines[:end] if "=" in l and not l.startswith("#"))
    hidden = re.findall(r"^\[app-name=([^\]\s]+)\]\s*\ninvisible=(?:1|true)", text, re.M)
    rc, mode = sh("makoctl", "mode")
    return {"anchor": vals.get("anchor", "top-right"), "timeout": int(vals.get("default-timeout", 5000)),
            "hidden": hidden, "dnd": "do-not-disturb" in mode.split()}


def mako_set(key, value):
    text = read(MAKO)
    lines, end = _mako_global(text)
    for i in range(end):
        if lines[i].startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.insert(end, f"{key}={value}")
    write(MAKO, "\n".join(lines) + "\n")
    sh("makoctl", "reload")


def mako_hidden_set(apps):
    """The apps whose notifications never show ([app-name=X] invisible=1 blocks)."""
    if sorted(apps) == sorted(mako_get()["hidden"]):
        return
    text = re.sub(r"^\[app-name=[^\]\s]+\]\s*\ninvisible=(?:1|true)\s*\n\n?", "", read(MAKO), flags=re.M)
    lines, end = _mako_global(text)
    head = "\n".join(lines[:end]).rstrip("\n") + "\n\n"
    blocks = "".join(f"[app-name={a}]\ninvisible=1\n\n" for a in apps)
    rest = text[text.index(lines[end]):] if end < len(lines) else ""
    write(MAKO, head + blocks + rest)
    sh("makoctl", "reload")


def mako_dnd(on):
    if on != mako_get()["dnd"]:
        spawn(f"{SCRIPTS}/toggle.sh", "notifications")


def mako_test():
    sh("notify-send", "Settings", "This is how a notification looks.")


# ---- top bar (waybar) --------------------------------------------------------------------------
WAYBAR = f"{CFG}/waybar/config.jsonc"
STATS = ["cpu", "memory", "temperature", "battery"]


def bar_get():
    text = read(WAYBAR)
    pos = re.search(r'^\s*"position":\s*"(\w+)"', text, re.M)
    height = re.search(r'^\s*"height":\s*(\d+)', text, re.M)
    block = re.search(r'"group/stats":\s*\{.*?"modules":\s*\[(.*?)\]', text, re.S)
    shown = re.findall(r'"([\w/-]+)"', block.group(1)) if block else []
    return {"position": pos.group(1) if pos else "top", "height": int(height.group(1)) if height else 27,
            "stats": shown}


def _bar_write(text):
    write(WAYBAR, text)
    sh("pkill", "-SIGUSR2", "waybar")


def bar_set_position(pos):
    _bar_write(re.sub(r'^(\s*"position":\s*)"\w+"', rf'\1"{pos}"', read(WAYBAR), count=1, flags=re.M))


def bar_set_height(h):
    _bar_write(re.sub(r'^(\s*"height":\s*)\d+', rf"\g<1>{int(h)}", read(WAYBAR), count=1, flags=re.M))


def bar_set_stats(shown):
    text = read(WAYBAR)
    m = re.search(r'("group/stats":\s*\{.*?"modules":\s*\[)(.*?)(\n\s*\])', text, re.S)
    if not m:
        return
    indent = re.search(r"\n(\s*)\"", m.group(2))
    pad = indent.group(1) if indent else "      "
    items = ",".join(f'\n{pad}"{s}"' for s in STATS if s in shown)
    _bar_write(text[:m.start(2)] + items + text[m.end(2):])


# ---- default apps ------------------------------------------------------------------------------
VARIABLES = f"{HYPR}/modules/variables.lua"
ROLES = [  # (variable, title, candidates: the command it's launched with)
    ("TERMINAL", "Terminal", ["kitty", "alacritty", "foot", "wezterm", "ghostty", "konsole"]),
    ("BROWSER", "Browser", ["google-chrome-stable", "firefox", "chromium", "brave", "zen-browser"]),
    ("FILE_MANAGER", "File manager", ["dolphin", "nautilus", "thunar", "nemo", "pcmanfm-qt"]),
    ("EDITOR", "Text editor", ["gnome-text-editor --new-window", "kate", "mousepad", "gedit", "code"]),
    ("CALCULATOR", "Calculator", ["gnome-calculator", "kcalc", "qalculate-gtk"]),
]


def role_get(var):
    m = re.search(rf'^{var}\s*=\s*"([^"]*)"', read(VARIABLES), re.M)
    return m.group(1) if m else ""


def role_options(var):
    current = role_get(var)
    cands = next(c for v, _, c in ROLES if v == var)
    found = [c for c in cands if shutil.which(c.split()[0])]
    return found if current in found or not current else [current] + found


def role_set(var, cmd):
    text = read(VARIABLES)
    text = re.sub(rf'^({var}\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{cmd}"', text, count=1, flags=re.M)
    write(VARIABLES, text)
    if var == "BROWSER":   # apps that read $BROWSER (uwsm env, next login)
        set_line(UWSM_ENV, r"^export BROWSER=", f"export BROWSER={cmd.split()[0]}")
    if var == "TERMINAL":
        set_line(f"{CFG}/kdeglobals", r"^TerminalApplication=", f"TerminalApplication={cmd.split()[0]}")
    sh("hyprctl", "reload")   # the binds are built from these variables


MIMEAPPS = f"{CFG}/mimeapps.list"
MIME_ROLES = [  # (title, mime types it covers; the first decides what's shown)
    ("Web links", ["x-scheme-handler/http", "x-scheme-handler/https", "text/html"]),
    ("Folders", ["inode/directory"]),
    ("Text files", ["text/plain"]),
    ("Images", ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/svg+xml"]),
    ("Video", ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo", "video/mpeg"]),
    ("Music", ["audio/mpeg", "audio/flac", "audio/x-wav", "audio/ogg"]),
    ("PDF", ["application/pdf"]),
]


def mime_get(mime):
    m = re.search(rf"^{re.escape(mime)}=([^;\n]*)", read(MIMEAPPS), re.M)
    return m.group(1) if m else ""


def mime_options(mime):
    """[(desktop id, name)] of the installed apps that can open mime."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    cur = mime_get(mime)
    names, out = set(), []
    apps = sorted(Gio.AppInfo.get_all_for_type(mime), key=lambda a: a.get_id() != cur)   # the current one first
    for app in apps:
        aid, name = app.get_id(), app.get_display_name()
        if aid and name not in names:   # one entry per app (Chrome ships two .desktop files)
            names.add(name)
            out.append((aid, name))
    if cur and all(aid != cur for aid, _ in out):
        out.insert(0, (cur, cur.removesuffix(".desktop")))
    return out


def mime_set(mimes, desktop_id):
    """Written by us under [Default Applications]: xdg-mime and Gio replace the symlink."""
    for mime in mimes:
        old = re.search(rf"^{re.escape(mime)}=.*$", read(MIMEAPPS), re.M)
        end = ";" if old and old.group(0).endswith(";") else ""   # keep the line's own style
        set_line(MIMEAPPS, rf"^{re.escape(mime)}=", f"{mime}={desktop_id}{end}", r"^\[Default Applications\]")


# ---- date & time -------------------------------------------------------------------------------
def time_get():
    rc, out = sh("timedatectl", "show")
    vals = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {"zone": vals.get("Timezone", ""), "ntp": vals.get("NTP") == "yes",
            "h24": store().get("clock", {}).get("h24", True)}


def timezones():
    return sh("timedatectl", "list-timezones")[1].splitlines()


def time_set_zone(zone):
    rc, out = sh("timedatectl", "set-timezone", zone, timeout=60)
    clock_poke()
    return None if rc == 0 else out


def time_set_ntp(on):
    rc, out = sh("timedatectl", "set-ntp", "true" if on else "false", timeout=60)
    return None if rc == 0 else out


def time_set_h24(on):
    remember("clock", "h24", bool(on))
    gsettings("clock-format", "24h" if on else "12h")
    clock_poke()


def clock_poke():
    """The bar's clock redraws now (clock.py reads the 24-hour setting on each draw)."""
    try:
        os.kill(int(read(f"{RUN}/waybar-clock.pid")), 10)   # SIGUSR1
    except (OSError, ValueError):
        pass


# ---- about -------------------------------------------------------------------------------------
def about():
    os_name = re.search(r'^PRETTY_NAME="?([^"\n]*)', read("/etc/os-release"), re.M)
    cpu = re.search(r"^model name\s*:\s*(.*)$", read("/proc/cpuinfo"), re.M)
    mem = re.search(r"^MemTotal:\s*(\d+)", read("/proc/meminfo"), re.M)
    up = float(read("/proc/uptime", "0").split()[0])
    gpus = [l.split(": ", 1)[1] for l in sh("lspci")[1].splitlines()
            if re.search(r"VGA|3D controller|Display controller", l) and ": " in l]
    disks, seen = [], set()
    for line in sh("df", "-h", "--output=target,size,used,pcent", "-x", "tmpfs", "-x", "devtmpfs", "-x", "efivarfs")[1].splitlines()[1:]:
        target, size, used, pct = line.split()
        if (target in ("/", "/home") or target.startswith("/mnt")) and (size, used) not in seen:
            seen.add((size, used))   # btrfs subvolumes of one disk show the same numbers
            disks.append(f"{target}  {used} / {size} ({pct})")
    hypr = re.search(r"Hyprland ([\d.]+)", sh("hyprctl", "version")[1])
    return [
        ("Computer", os.uname().nodename),
        ("System", os_name.group(1) if os_name else "Linux"),
        ("Kernel", os.uname().release),
        ("Desktop", f"Hyprland {hypr.group(1)}" if hypr else "Hyprland"),
        ("Processor", f"{cpu.group(1).strip()} ({os.cpu_count()} threads)" if cpu else "?"),
        *[("Graphics", g) for g in gpus],
        ("Memory", f"{int(mem.group(1)) / 1048576:.1f} GiB" if mem else "?"),
        *[("Disk", d) for d in disks],
        ("Uptime", f"{int(up // 86400)} d {int(up % 86400 // 3600)} h {int(up % 3600 // 60)} min" if up >= 86400
                   else f"{int(up // 3600)} h {int(up % 3600 // 60)} min"),
    ]


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["restore"]:
        changed = restore_desktop()
        print("restored: " + ", ".join(changed) if changed else "gsettings already match the repo")
    elif sys.argv[1:2] == ["pin-workspace"] and len(sys.argv) == 3 and sys.argv[2].isdigit():
        # Super + Shift + Alt + arrow moved the workspace: it keeps the screen it's on now
        ws = int(sys.argv[2])
        rc, out = sh("hyprctl", "workspaces", "-j")
        mon = next((w["monitor"] for w in json.loads(out or "[]") if w["id"] == ws), None) if rc == 0 else None
        if mon:
            err = ws_monitor_set(ws, mon)
            sys.exit(err)
    else:
        sys.exit("usage: settingslib.py restore | pin-workspace N")
