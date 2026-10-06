-- Workspace groups: reads ~/.config/hypr/workspaces.conf, sends each group's windows to its
-- workspace (monocle layout), and binds Alt + 1…0 / Super + Ctrl + 1…0 / Super + N to the wsgroups program.
-- Edit workspaces.conf, not this file.

local conf     = os.getenv("HOME") .. "/.config/hypr/workspaces.conf"
local wsgroups = "~/.local/bin/wsgroups"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local grouped = {} -- workspace numbers that have a group line
local classesOf = {} -- workspace number -> { class = true } of its apps, as written in the file

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
                    hl.window_rule({ match = { class = "^(" .. class .. ")$" }, workspace = ws })
                end
            end
        end
    end
    file:close()
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

-- Alt + N: window N of this workspace, oldest first (same order as `wsgroups focus N`). Done here
-- rather than in the script so a fullscreen video stays fullscreen (binds.lua).
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
    local key = "code:" .. (9 + n)
    hl.bind("ALT + " .. key,          function() focusNth(n) end)
    hl.bind("SUPER + CTRL + " .. key, hl.dsp.exec_cmd(wsgroups .. " go " .. n))
end

-- another window of the current workspace's app (a new Chrome window on Web, kitty on Terminal…), or the app
-- itself when none is open there
hl.bind("SUPER + N", hl.dsp.exec_cmd(wsgroups .. " new"))
