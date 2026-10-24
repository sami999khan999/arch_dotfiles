-- Workspace groups: reads ~/.config/hypr/workspaces.conf, sends each group's windows to its
-- workspace (monocle layout), and binds Super + Ctrl + 1…0 / Super + N to the wsgroups program and
-- Super + X (window-switcher mode).
-- Edit workspaces.conf, not this file.

local conf     = os.getenv("HOME") .. "/.config/hypr/workspaces.conf"
local wsgroups = "~/.local/bin/wsgroups"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local grouped = {} -- workspace numbers that have a group line
local classesOf = {} -- workspace number -> { class = true } of its apps, as written in the file
local groupRules = {} -- workspace number -> its window rule (which follows the window there)

-- the workspace an app's class belongs to (autostart.lua: the Control Center's), nil if none
function workspaceOfClass(cls)
    for ws, classes in pairs(classesOf) do
        if classes[cls] then return ws end
    end
    return nil
end

local file = io.open(conf, "r")
if file then
    for raw in file:lines() do
        local line = trim(raw)
        if line ~= "" and not line:match("^#") then
            local ws, _, class = line:match("^(.-)|(.-)|([^|]*)")
            ws = ws and trim(ws)
            if ws and ws:match("^%d+$") then
                grouped[ws] = true
                -- several apps are "a, b" in the file (| separates its columns): regex a|b here
                class = trim(class):gsub("%s*,%s*", "|")
                if class ~= "" then
                    classesOf[ws] = {}
                    for c in class:gmatch("[^|]+") do classesOf[ws][c] = true end
                    groupRules[ws] = hl.window_rule({ match = { class = "^(" .. class .. ")$" }, workspace = ws })
                end
            end
        end
    end
    file:close()
end

-- Opening without going there (hyprctl eval, from wsgroups launch --background and a Setups restore):
-- the group rule would switch the screen to the new window's workspace and back again, so while
-- `class` (a regex: "a|b") is launched its group rules are off and a silent rule sends it to `ws`.
-- openQuietlyDone(class) puts the group rules back. Keyed by class: the Control Center starting at
-- login and a restore can overlap.
local quiet = {} -- class -> its silent rule
local function setGroupRules(class, on)
    for c in class:gmatch("[^|]+") do
        local ws = workspaceOfClass((c:gsub("\\(.)", "%1")))   -- escaped (sami\.files) as written
        if ws and groupRules[ws] then groupRules[ws]:set_enabled(on) end
    end
end

function openQuietly(class, ws)
    if quiet[class] then quiet[class]:set_enabled(false) end
    quiet[class] = hl.window_rule({ match = { class = "^(" .. class .. ")$" }, workspace = ws .. " silent" })
    setGroupRules(class, false)
end

function openQuietlyDone(class)
    if quiet[class] then quiet[class]:set_enabled(false) end
    quiet[class] = nil
    setGroupRules(class, true)
end

-- workspaces 1..10 always exist, assigned or not, so waybar always shows all ten.
-- Group workspaces use monocle: every tiled window fills the area (waybar and gaps stay) and
-- only the focused one shows. Nothing is maximized, so switching never resizes a window — a
-- maximize handed back and forth dropped the old window into a half-width tile and made VS Code
-- squeeze its sidebar and panel.
-- Switching windows there is instant: monocle fades the old window out while the new one fades in,
-- and halfway through both are see-through and the wallpaper flashes. So no animation for a monocle
-- workspace's tiled windows (a new one appears at once, without the zoom); floating ones keep theirs.
for ws = 1, 10 do
    local monocle = tostring(ws) ~= PAIR_WS and grouped[tostring(ws)]
    hl.workspace_rule({ workspace = tostring(ws), monitor = MONITOR1, default = ws == 1, persistent = true,
                        layout = tostring(ws) == PAIR_WS and "scrolling"   -- VS Code + kitty pairs (codepair.lua)
                            or monocle and "monocle" or nil })
    if monocle then
        hl.window_rule({ match = { workspace = tostring(ws), float = false }, no_anim = true })
    end
end

-- Window N of this workspace (Super + Q…P in window-switcher mode), oldest first (same order as
-- `wsgroups focus N`). Done here rather than in the script so a fullscreen video stays fullscreen (binds.lua).
local function focusNth(n)
    local wins = workspaceWindows()
    if not wins[n] then
        hl.exec_cmd("notify-send -t 1500 Workspaces 'No window " .. n .. " here (" .. #wins .. " open)'")
        return
    end
    focusPair(wins[n])   -- on the Code workspace: VS Code window n with its kitty
end

-- digits by physical keycode (AZERTY-safe, like binds.lua): 1..9 => 10..18, 0 => 19
for n = 1, 10 do
    hl.bind("SUPER + CTRL + code:" .. (9 + n), hl.dsp.exec_cmd(wsgroups .. " go " .. n))
end

-- Window-switcher mode (Super + X, on until pressed again): Super + Q…P = window 1…10 of this workspace,
-- the letter row under the digits Super + 1…0 go to the workspaces with. Off, those keys do what they
-- always do (close, Files, settings…): the mode swaps which binds are enabled. The state file keeps it
-- across a config reload and tells the bar (wsgroups.py), which shows the mode on its window counter.
local MODE_FILE = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/hypr-winmode"
local ROW = { "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P" }
local pickBinds = {}
for n, letter in ipairs(ROW) do
    -- by keycode (24..33: the row under the digits), so another layout keeps the place
    local b = hl.bind("SUPER + code:" .. (23 + n), function() focusNth(n) end)
    b:set_enabled(false)
    pickBinds[letter] = b
end

local function setWindowMode(on)
    for _, letter in ipairs(ROW) do
        pickBinds[letter]:set_enabled(on)
        for _, b in ipairs(KEYBINDS["SUPER + " .. letter] or {}) do b:set_enabled(not on) end
    end
    local f = io.open(MODE_FILE, "w")
    if f then f:write(on and "on" or "off"); f:close() end
    hl.exec_cmd(wsgroups .. " bar-refresh")   -- every screen's window counter re-reads the mode
end

local function windowModeOn()
    local f = io.open(MODE_FILE)
    if not f then return false end
    local on = f:read("a"):match("^on") ~= nil
    f:close()
    return on
end

-- global: a right-click on the bar's window counter calls it too (hyprctl eval)
function toggleWindowMode()
    setWindowMode(not windowModeOn())
end

if windowModeOn() then setWindowMode(true) end
hl.bind("SUPER + X", function() toggleWindowMode() end) -- window-switcher mode: Super + Q…P = window 1…10 (again = off)

-- another window of the current workspace's app (a new Chrome window on Web, kitty on Terminal…), or the app
-- itself when none is open there
hl.bind("SUPER + N", hl.dsp.exec_cmd(wsgroups .. " new"))
