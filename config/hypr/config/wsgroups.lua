-- Workspace groups: reads ~/.config/hypr/workspaces.conf, sends each group's windows to its
-- workspace (monocle layout), and binds Alt + 1…0 / Super + Ctrl + 1…0 / Super + N to the wsgroups program.
-- Edit workspaces.conf, not this file.

local conf     = os.getenv("HOME") .. "/.config/hypr/workspaces.conf"
local wsgroups = "~/.local/bin/wsgroups"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local grouped = {} -- workspace numbers that have a group line

local file = io.open(conf, "r")
if file then
    for raw in file:lines() do
        local line = trim(raw)
        if line ~= "" and not line:match("^#") then
            local ws, _, class = line:match("^(.-)|(.-)|([^|]*)")
            ws = ws and trim(ws)
            if ws and ws:match("^%d+$") then
                grouped[ws] = true
                if trim(class) ~= "" then
                    hl.window_rule({ match = { class = "^(" .. trim(class) .. ")$" }, workspace = ws })
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
for ws = 1, 10 do
    hl.workspace_rule({ workspace = tostring(ws), monitor = MONITOR1, default = ws == 1, persistent = true,
                        layout = grouped[tostring(ws)] and "monocle" or nil })
end

-- digits by physical keycode (AZERTY-safe, like binds.lua): 1..9 => 10..18, 0 => 19
for n = 1, 10 do
    local key = "code:" .. (9 + n)
    hl.bind("ALT + " .. key,          hl.dsp.exec_cmd(wsgroups .. " focus " .. n))
    hl.bind("SUPER + CTRL + " .. key, hl.dsp.exec_cmd(wsgroups .. " go " .. n))
end

-- another window of the current workspace's app (a new Chrome window on Web, kitty on Terminal…)
hl.bind("SUPER + N", hl.dsp.exec_cmd(wsgroups .. " new"))
