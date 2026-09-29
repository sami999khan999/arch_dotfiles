-- Workspace groups: reads ~/.config/hypr/workspaces.conf, sends each group's windows to its
-- workspace (maximized), and binds Alt + 1…0 / Super + Ctrl + 1…0 / Super + N to the wsgroups program.
-- Edit workspaces.conf, not this file.

local conf     = os.getenv("HOME") .. "/.config/hypr/workspaces.conf"
local wsgroups = "~/.local/bin/wsgroups"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local classes = {} -- lowercase class alternatives ("code|cursor" -> code, cursor) of every group

-- workspaces 1..10 always exist, assigned or not, so waybar always shows all ten
for ws = 1, 10 do
    hl.workspace_rule({ workspace = tostring(ws), monitor = MONITOR1, default = ws == 1, persistent = true })
end

local file = io.open(conf, "r")
if file then
    for raw in file:lines() do
        local line = trim(raw)
        if line ~= "" and not line:match("^#") then
            local ws, _, class = line:match("^(.-)|(.-)|([^|]*)")
            ws = ws and trim(ws)
            if ws and ws:match("^%d+$") then
                if trim(class) ~= "" then
                    hl.window_rule({ match = { class = "^(" .. trim(class) .. ")$" }, workspace = ws })
                    for alt in trim(class):gmatch("[^|]+") do classes[#classes + 1] = trim(alt):lower() end
                end
            end
        end
    end
    file:close()
end

-- Maximize group windows once, when they open (waybar and gaps stay). Not a fullscreen_state
-- rule: that pins the state, so a YouTube video asking for real fullscreen stays in the tile.
hl.on("window.open", function(w)
    if not w or w.floating then return end
    local cls = (w.class or ""):lower()
    for _, pat in ipairs(classes) do
        if cls:match("^" .. pat .. "$") then
            hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 1, client = 0, window = "address:" .. w.address }))
            return
        end
    end
end)

-- digits by physical keycode (AZERTY-safe, like binds.lua): 1..9 => 10..18, 0 => 19
for n = 1, 10 do
    local key = "code:" .. (9 + n)
    hl.bind("ALT + " .. key,          hl.dsp.exec_cmd(wsgroups .. " focus " .. n))
    hl.bind("SUPER + CTRL + " .. key, hl.dsp.exec_cmd(wsgroups .. " go " .. n))
end

-- another window of the current workspace's app (a new Chrome window on Web, kitty on Terminal…)
hl.bind("SUPER + N", hl.dsp.exec_cmd(wsgroups .. " new"))
