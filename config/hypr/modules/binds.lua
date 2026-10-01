-- Keybindings, after Omarchy's default/hypr/bindings/*.lua
-- omarchy-* helper scripts are replaced with plain tools (swayosd, makoctl, walker, grim/slurp).

local launch  = "uwsm app -- " -- if you are not using UWSM, make this empty (e.g. "")
local scripts = "~/.config/hypr/scripts/"
local panels  = "~/.local/lib/panels/"

local function bind(keys, action, opts)
    if type(action) == "string" then
        action = hl.dsp.exec_cmd(action)
    end
    hl.bind(keys, action, opts)
end

-- Toggle a TUI in a floating terminal window (second press closes it)
local function tui(cmd)
    return scripts .. "tui.sh " .. cmd
end

-- AZERTY-safe: digits are bound by physical keycode. 1..9 => 10..18, 0 => 19
local function digitCode(d)
    return "code:" .. (d == 0 and 19 or (9 + d))
end

----------------------
---- APPLICATIONS ----
----------------------

bind("SUPER + RETURN",               launch .. TERMINAL)
bind("SUPER + SHIFT + RETURN",       launch .. BROWSER)
bind("SUPER + SHIFT + B",            launch .. BROWSER)
bind("SUPER + SHIFT + ALT + B",      launch .. BROWSER .. " --incognito")
bind("SUPER + SHIFT + F",            launch .. FILE_MANAGER)
bind("SUPER + SHIFT + N",            launch .. EDITOR)
bind("SUPER + CTRL + Q",             launch .. CALCULATOR)
bind("XF86Calculator",               launch .. CALCULATOR)
bind("SUPER + CTRL + T",             tui(panels .. "sysmon.py"))
bind("CTRL + SHIFT + Escape",        tui("btop"))
bind("SUPER + CTRL + A",             tui("wiremix"))
bind("SUPER + CTRL + B",             tui("bluetui"))
bind("SUPER + CTRL + W",             tui("nmtui"))

---------------
---- MENUS ----
---------------

bind("SUPER + SPACE",                "walker")
bind("SUPER + K",                    tui(panels .. "keys.py")) -- shortcut list
bind("SUPER + CTRL + G",             tui("~/.local/bin/wsgroups")) -- workspace groups manager
bind("SUPER + CTRL + E",             "walker -m symbols")
bind("SUPER + CTRL + V",             "walker -m clipboard")
bind("SUPER + V",                    "walker -m clipboard")
bind("SUPER + ESCAPE",               scripts .. "power-menu.sh")
bind("SUPER + ALT + SPACE",          scripts .. "power-menu.sh")
bind("XF86PowerOff",                 scripts .. "power-menu.sh", { locked = true })

-------------------------
---- WINDOW TILING ----
-------------------------

bind("SUPER + W",                    hl.dsp.window.close())
bind("SUPER + Q",                    hl.dsp.window.close())
bind("SUPER + J",                    hl.dsp.layout("togglesplit"))
bind("SUPER + P",                    hl.dsp.window.pseudo())
bind("SUPER + T",                    hl.dsp.window.float({ action = "toggle" }))
bind("SUPER + F",                    function() toggleFullscreen("fullscreen") end) -- fullscreen (a VS Code + kitty pair: again = side by side)
bind("SUPER + ALT + F",              function() toggleFullscreen("maximized") end) -- maximize, bar stays (pairs: again = side by side)
bind("SUPER + O", function()          -- pop window out: float & pin
    hl.dispatch(hl.dsp.window.float({ action = "toggle" }))
    hl.dispatch(hl.dsp.window.pin())
end)

-- Focus and swap with arrows
for key, dir in pairs({ LEFT = "l", RIGHT = "r", UP = "u", DOWN = "d" }) do
    bind("SUPER + " .. key,               hl.dsp.focus({ direction = dir }))
    bind("SUPER + SHIFT + " .. key,       hl.dsp.window.swap({ direction = dir }))
    bind("SUPER + SHIFT + ALT + " .. key, hl.dsp.workspace.move({ monitor = dir }))
    bind("SUPER + ALT + " .. key,         hl.dsp.window.move({ into_group = dir }))
end

-- Workspaces 1..10
for ws = 1, 10 do
    local key = digitCode(ws % 10)
    bind("SUPER + " .. key,               hl.dsp.focus({ workspace = tostring(ws) }))
    bind("SUPER + SHIFT + " .. key,       hl.dsp.window.move({ workspace = tostring(ws) }))
    bind("SUPER + SHIFT + ALT + " .. key, hl.dsp.window.move({ workspace = tostring(ws), follow = false }))
end

bind("SUPER + TAB",                  hl.dsp.focus({ workspace = "e+1" }))
bind("SUPER + SHIFT + TAB",          hl.dsp.focus({ workspace = "e-1" }))
bind("SUPER + CTRL + TAB",           hl.dsp.focus({ workspace = "previous" }))
bind("SUPER + mouse_down",           hl.dsp.focus({ workspace = "e+1" }))
bind("SUPER + mouse_up",             hl.dsp.focus({ workspace = "e-1" }))

-- Scratchpad
bind("SUPER + S",                    hl.dsp.workspace.toggle_special("scratchpad"))
bind("SUPER + ALT + S",              hl.dsp.window.move({ workspace = "special:scratchpad", follow = false }))

-- Switching windows keeps a fullscreen video fullscreen: before the switch the window leaves
-- Hyprland's fullscreen but Chrome is still told it's fullscreen (internal 0, client 2), so the
-- video stays full inside its tile; focusing it again makes it real fullscreen (window.active below).
-- Without this, misc.on_focus_under_fullscreen = 2 exits the video's fullscreen.
-- Globals: wsgroups.lua (Alt + N) uses workspaceWindows and focusKeepingFullscreen.

local function setFullscreen(w, internal)
    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = internal, client = 2, window = "address:" .. w.address }))
end

-- Windows of the active workspace, oldest first: the order Alt + N counts and Alt + Tab cycles.
function workspaceWindows()
    local ws = hl.get_active_workspace()
    local wins = {}
    for _, w in ipairs(ws and ws:get_windows() or {}) do
        if w.mapped and w.class ~= "TUI.float" and not isPairTerm(w) then table.insert(wins, w) end
    end
    table.sort(wins, function(a, b) return a.stable_id < b.stable_id end)
    return wins
end

-- Focuses target by address (not cycle_next: it doesn't move on a monocle workspace once the
-- fullscreen window is parked, which left the video in its tile, shifted under the bar).
function focusKeepingFullscreen(target)
    local w = hl.get_active_window()
    if not target or (w and w.address == target.address) then
        return -- focusing the focused window on a monocle workspace jumps to another one
    end
    if w and w.fullscreen == 2 then
        setFullscreen(w, 0)
    end
    hl.dispatch(hl.dsp.focus({ window = "address:" .. target.address }))
    -- safety net: if focus didn't move, the parked video would stay shifted in its tile
    hl.timer(function()
        local now = hl.get_active_window()
        if now and now.fullscreen_client == 2 and now.fullscreen == 0 then setFullscreen(now, 2) end
    end, { timeout = 150, type = "oneshot" })
end

hl.on("window.active", function(w)
    if w and w.fullscreen_client == 2 and w.fullscreen == 0 then
        setFullscreen(w, 2)
    end
end)

local function cycle(step)
    local wins, active = workspaceWindows(), hl.get_active_window()
    if #wins < 2 then return end
    active = active and pairMain(active)   -- in a VS Code + kitty pair, the kitty counts as its VS Code
    local i = 0
    for k, w in ipairs(wins) do
        if active and w.address == active.address then i = k end
    end
    focusPair(wins[(i - 1 + step) % #wins + 1])
end

-- Window cycling and monitors
bind("ALT + TAB",                    function() cycle(1) end)
bind("ALT + SHIFT + TAB",            function() cycle(-1) end)
bind("CTRL + ALT + TAB",             hl.dsp.focus({ monitor = "+1" }))
bind("CTRL + ALT + SHIFT + TAB",     hl.dsp.focus({ monitor = "-1" }))

-- Resize with the two keys right of 0 (keycodes 20/21: - = on QWERTY, ) = on AZERTY)
for mods, step in pairs({ [""] = 100, ["ALT + "] = 25, ["CTRL + "] = 300 }) do
    bind("SUPER + " .. mods .. "code:20",         hl.dsp.window.resize({ x = -step, y = 0, relative = true }), { repeating = true })
    bind("SUPER + " .. mods .. "code:21",         hl.dsp.window.resize({ x = step,  y = 0, relative = true }), { repeating = true })
    bind("SUPER + SHIFT + " .. mods .. "code:20", hl.dsp.window.resize({ x = 0, y = -step, relative = true }), { repeating = true })
    bind("SUPER + SHIFT + " .. mods .. "code:21", hl.dsp.window.resize({ x = 0, y = step,  relative = true }), { repeating = true })
end

-- Mouse
bind("SUPER + mouse:272",            hl.dsp.window.drag(),   { mouse = true })
bind("SUPER + mouse:273",            hl.dsp.window.resize(), { mouse = true })

-- Groups
bind("SUPER + G",                    hl.dsp.group.toggle())
bind("SUPER + ALT + G",              hl.dsp.window.move({ out_of_group = true }))
bind("SUPER + ALT + TAB",            hl.dsp.group.next())
bind("SUPER + ALT + SHIFT + TAB",    hl.dsp.group.prev())
bind("SUPER + CTRL + LEFT",          hl.dsp.group.prev())
bind("SUPER + CTRL + RIGHT",         hl.dsp.group.next())
for i = 1, 5 do
    bind("SUPER + ALT + " .. digitCode(i), hl.dsp.group.active({ index = i }))
end

---------------------------
---- HARDWARE CONTROLS ----
---------------------------

local osd = "swayosd-client --monitor \"$(hyprctl activeworkspace -j | jq -r .monitor)\" "

bind("XF86AudioRaiseVolume",         osd .. "--output-volume raise",   { locked = true, repeating = true })
bind("XF86AudioLowerVolume",         osd .. "--output-volume lower",   { locked = true, repeating = true })
bind("ALT + XF86AudioRaiseVolume",   osd .. "--output-volume +1",      { locked = true, repeating = true })
bind("ALT + XF86AudioLowerVolume",   osd .. "--output-volume -1",      { locked = true, repeating = true })
bind("XF86AudioMute",                osd .. "--output-volume mute-toggle", { locked = true })
bind("XF86AudioMicMute",             osd .. "--input-volume mute-toggle",  { locked = true })
bind("XF86MonBrightnessUp",          osd .. "--brightness raise",      { locked = true, repeating = true })
bind("XF86MonBrightnessDown",        osd .. "--brightness lower",      { locked = true, repeating = true })
bind("ALT + XF86MonBrightnessUp",    osd .. "--brightness +1",         { locked = true, repeating = true })
bind("ALT + XF86MonBrightnessDown",  osd .. "--brightness -1",         { locked = true, repeating = true })

bind("XF86AudioPlay",                osd .. "--playerctl play-pause",  { locked = true })
bind("XF86AudioPause",               osd .. "--playerctl play-pause",  { locked = true })
bind("XF86AudioNext",                osd .. "--playerctl next",        { locked = true })
bind("XF86AudioPrev",                osd .. "--playerctl previous",    { locked = true })

-------------------
---- UTILITIES ----
-------------------

-- Notifications
bind("SUPER + comma",                "makoctl dismiss")
bind("SUPER + SHIFT + comma",        "makoctl dismiss --all")
bind("SUPER + ALT + comma",          "makoctl invoke")
bind("SUPER + SHIFT + ALT + comma",  "makoctl restore")
bind("SUPER + CTRL + comma",         scripts .. "toggle.sh notifications")

-- Toggles
bind("SUPER + SHIFT + SPACE",        "pkill -SIGUSR1 waybar")
bind("SUPER + CTRL + I",             scripts .. "toggle.sh idle")
bind("SUPER + CTRL + N",             scripts .. "toggle.sh nightlight")
bind("SUPER + CTRL + SPACE",         scripts .. "wallpaper.sh next")
bind("SUPER + CTRL + L",             "loginctl lock-session")

-- Captures
bind("PRINT",                        scripts .. "screenshot.sh region")
bind("SUPER + SHIFT + S",            scripts .. "screenshot.sh region")
bind("SHIFT + PRINT",                scripts .. "screenshot.sh output")
bind("SUPER + PRINT",                "pkill hyprpicker || hyprpicker -a")

-- Zoom
bind("SUPER + CTRL + Z", function()
    local zoom = hl.get_config("cursor.zoom_factor") or 1
    hl.config({ cursor = { zoom_factor = math.min(zoom + 1, 4) } })
end)
bind("SUPER + CTRL + ALT + Z", function()
    hl.config({ cursor = { zoom_factor = 1 } })
end)
