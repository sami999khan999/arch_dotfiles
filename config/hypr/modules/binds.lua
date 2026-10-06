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
bind("SUPER + CTRL + T",             panels .. "sysgui.py") -- System monitor
bind("CTRL + SHIFT + Escape",        tui("btop"))
bind("SUPER + CTRL + A",             panels .. "audiogui.py") -- Audio (Wiremix button for streams)
bind("SUPER + CTRL + B",             panels .. "btgui.py") -- Bluetooth (pair, connect, battery)
bind("SUPER + CTRL + W",             panels .. "netgui.py") -- Network (Manage connections opens nmtui)

---------------
---- MENUS ----
---------------

bind("SUPER + SPACE",                "walker")
bind("SUPER + K",                    panels .. "keysgui.py") -- shortcut list
bind("SUPER + I",                    panels .. "settingsgui.py") -- settings
bind("SUPER + SHIFT + T",            panels .. "themegui.py") -- colour theme (Tokyo Night, Crimson…)
bind("SUPER + CTRL + G",             "~/.local/bin/wsgroups") -- workspace groups manager
bind("SUPER + CTRL + P",             "~/.local/bin/wsgroups go sami.projects") -- Projects workspace (every repo in ~/code: branches, history, open in VS Code)
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
bind("SUPER + T",                    function() toggleDesktopFloat() end) -- float like a desktop window: drag, resize at the edges (again = back in the tiles)
bind("SUPER + F",                    function() toggleFullscreen("fullscreen") end) -- fullscreen (a VS Code + kitty pair: again = side by side)
bind("SUPER + ALT + F",              function() toggleFullscreen("maximized") end) -- maximize, bar stays (pairs: again = side by side)
bind("SUPER + ALT + C",              function() widenPairHalf("code") end) -- VS Code of the pair on screen: full width (again = side by side)
bind("SUPER + ALT + T",              function() widenPairHalf("term") end) -- kitty of the pair on screen: full width (again = side by side)
bind("SUPER + ALT + P",              function() openPairPanel() end) -- VS Code + kitty pair: its layout (kitty width, side)
bind("SUPER + O", function()          -- pop window out: float & pin
    toggleDesktopFloat()
    hl.dispatch(hl.dsp.window.pin())
end)

-- Super + T: the focused window becomes a desktop-style floating window: out of maximize / fullscreen
-- and, the first time, 60 % x 70 % of the screen below the bar, centred. Back in the tiles and out
-- again, it returns where it was (Hyprland keeps the size, persistent_size in windowrules.lua, but
-- re-centres it). Move it with Super + drag, resize it at its edges (resize_on_border, decorations.lua)
-- or with Super + right-drag. It stacks like a desktop window too: tagged desktop-float, it goes behind
-- a tiled window that gets the focus and comes back in front when focused (coverDesktopFloats below).
-- Global: tests call it by eval.
local floatPlace = {}   -- window address -> { x, y, monitor } where it floated last
local DESKTOP_FLOAT = "desktop-float"   -- a window tag: kept by Hyprland across config reloads

local function isDesktopFloat(w)
    local tags = type(w.tags) == "table" and table.concat(w.tags, " ") or tostring(w.tags or "")
    return w.floating and tags:find(DESKTOP_FLOAT, 1, true) ~= nil
end

local function desktopFloatsOn(ws, except)
    for _, o in ipairs(hl.get_windows()) do
        if o.workspace and o.workspace.id == ws.id and o.address ~= except and isDesktopFloat(o) then
            return true
        end
    end
    return false
end

function toggleDesktopFloat(w)
    w = w or hl.get_active_window()
    if not w then return end
    local target = "address:" .. w.address
    local m = w.monitor or hl.get_active_monitor()
    if w.floating then
        floatPlace[w.address] = { x = w.at.x, y = w.at.y, monitor = m.name }
        hl.dispatch(hl.dsp.window.tag({ tag = "-" .. DESKTOP_FLOAT, window = target }))
        hl.dispatch(hl.dsp.window.float({ action = "disable", window = target }))
        -- the last desktop float gone: the tile maximized to cover them goes back to a plain tile
        if w.workspace and not desktopFloatsOn(w.workspace, w.address) then
            for _, o in ipairs(hl.get_windows()) do
                if o.workspace and o.workspace.id == w.workspace.id and not o.floating and o.fullscreen == 1 then
                    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = 0, window = "address:" .. o.address }))
                end
            end
        end
        return
    end
    if w.fullscreen ~= 0 then
        hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = 0, window = target }))
    end
    hl.dispatch(hl.dsp.window.float({ action = "enable", window = target }))
    hl.dispatch(hl.dsp.window.tag({ tag = "+" .. DESKTOP_FLOAT, window = target }))
    local last = floatPlace[w.address]
    if last then   -- floated before: its old place (on another screen now: Hyprland centres it there)
        if last.monitor == m.name then
            hl.dispatch(hl.dsp.window.move({ x = last.x, y = last.y, window = target }))
        end
        return
    end
    local r = m.reserved
    local aw, ah = m.width / m.scale - r.left - r.right, m.height / m.scale - r.top - r.bottom
    local fw, fh = math.floor(aw * 0.6), math.floor(ah * 0.7)
    hl.dispatch(hl.dsp.window.resize({ x = fw, y = fh, window = target }))
    hl.dispatch(hl.dsp.window.move({ x = math.floor(m.x + r.left + (aw - fw) / 2),
                                     y = math.floor(m.y + r.top + (ah - fh) / 2), window = target }))
end

-- A tiled window that gets the focus covers its workspace's desktop floats, as on a desktop. Hyprland
-- always draws floating windows above tiled ones, except above a maximized window: there a float shows
-- only once it's focused (Alt + N, Alt + Tab, a click while it's in view). So the focused tile takes the
-- maximized state (the same size in monocle, nothing moves), and takes it again when it already had it:
-- that's what sends a float that came to the front back behind. Not on the VS Code + kitty pairs'
-- workspace (scrolling: a maximized VS Code would cover its kitty), nor over a real fullscreen (video).
local function coverDesktopFloats(w)
    if not w or w.floating or w.fullscreen == 2 or not w.workspace then return end
    if tostring(w.workspace.id) == PAIR_WS or not desktopFloatsOn(w.workspace) then return end
    local target = "address:" .. w.address
    if w.fullscreen == 1 then
        hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = 0, window = target }))
    end
    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 1, client = 0, window = target }))
end
hl.on("window.active", coverDesktopFloats)

-- Super + Shift + Alt + arrow: the current workspace to the screen that way (counted from the focused
-- one), and it stays there: its screen in Settings → Workspaces (per PC) follows, so it opens there
-- again after a restart
local function moveWorkspaceToMonitor(dir)
    local ws = hl.get_active_workspace()
    if not ws then return end
    hl.dispatch(hl.dsp.workspace.move({ monitor = dir }))
    hl.exec_cmd("python3 ~/.local/lib/panels/settingslib.py pin-workspace " .. ws.id)
end

-- Focus and swap with arrows
for key, dir in pairs({ LEFT = "l", RIGHT = "r", UP = "u", DOWN = "d" }) do
    bind("SUPER + " .. key,               hl.dsp.focus({ direction = dir }))
    bind("SUPER + SHIFT + " .. key,       hl.dsp.window.swap({ direction = dir }))
    bind("SUPER + SHIFT + ALT + " .. key, function() moveWorkspaceToMonitor(dir) end) -- move workspace to monitor (it stays on that screen)
    bind("SUPER + ALT + " .. key,         hl.dsp.window.move({ into_group = dir }))
end

-- Workspaces 1..10. Super + N goes to workspace N; pressed again while there, it moves on to the
-- next window of that workspace (same order and pairing as Alt + Tab, via cycle below)
local cycle   -- defined with Alt + Tab further down

-- global: waybar's workspace buttons call it too (hyprctl eval), so clicking works like the keys
function goOrCycle(ws)
    local active = hl.get_active_workspace()
    if active and tostring(active.id) == ws then
        cycle(1)
    else
        hl.dispatch(hl.dsp.focus({ workspace = ws }))
    end
end

for ws = 1, 10 do
    local key = digitCode(ws % 10)
    bind("SUPER + " .. key,               function() goOrCycle(tostring(ws)) end) -- go to workspace, again: next window
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
-- The order set by hand (`wsgroups move`: ↑ ↓ in the Workspaces panel): window address -> place.
local HAND_ORDER = (os.getenv("XDG_STATE_HOME") or (os.getenv("HOME") .. "/.local/state")) .. "/wsgroups/order.json"
local function handOrder()
    local placed, f = {}, io.open(HAND_ORDER)
    if f then
        local i = 0
        for address in f:read("a"):gmatch('"(0x%x+)"') do
            i = i + 1
            placed[address] = i
        end
        f:close()
    end
    return placed
end

-- The current workspace's windows in Alt + N order, as `wsgroups` (its windows()) counts them: oldest
-- first, except the ones put in order by hand, which come first as placed. Popups don't count.
function workspaceWindows()
    local ws = hl.get_active_workspace()
    local wins = {}
    for _, w in ipairs(ws and ws:get_windows() or {}) do
        if w.mapped and w.class ~= "TUI.float" and not tostring(w.class):find("^panels%.") and not isPairTerm(w) then
            table.insert(wins, w)
        end
    end
    local placed = handOrder()
    table.sort(wins, function(a, b)
        local pa, pb = placed[a.address], placed[b.address]
        if pa and pb then return pa < pb end
        if pa or pb then return pa ~= nil end
        return a.stable_id < b.stable_id
    end)
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

function cycle(step)   -- assigns the local declared above the workspace binds
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
bind("SUPER + CTRL + M",             osd .. "--playerctl play-pause",  { locked = true }) -- play / pause the media (the bar's play button), for keyboards without media keys
bind("SUPER + ALT + B",              scripts .. "visualizer.py cycle") -- bar: the visualizer's next style (bars, solid, mirror, dots, wave, off)

-------------------
---- UTILITIES ----
-------------------

-- Notifications
bind("SUPER + comma",                "makoctl dismiss")
bind("SUPER + SHIFT + comma",        "makoctl dismiss --all")
bind("SUPER + ALT + comma",          "makoctl invoke")
bind("SUPER + SHIFT + ALT + comma",  "makoctl restore")
bind("SUPER + CTRL + comma",         scripts .. "toggle.sh notifications") -- Do not disturb
bind("SUPER + period",               panels .. "notifgui.py") -- Notifications (history, do not disturb)

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
