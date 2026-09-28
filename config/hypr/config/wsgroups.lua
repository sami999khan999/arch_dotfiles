-- Workspace groups: reads ~/.config/hypr/workspaces.conf, sends each group's windows to its
-- workspace (maximized), and binds Alt + 1…0 / Super + Ctrl + 1…0 to the wsgroups program.
-- Edit workspaces.conf, not this file.

local conf     = os.getenv("HOME") .. "/.config/hypr/workspaces.conf"
local wsgroups = "~/.local/bin/wsgroups"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local file = io.open(conf, "r")
if file then
    for raw in file:lines() do
        local line = trim(raw)
        if line ~= "" and not line:match("^#") then
            local ws, _, class = line:match("^(.-)|(.-)|([^|]*)")
            if ws and trim(class) ~= "" then
                hl.window_rule({
                    match            = { class = "^(" .. trim(class) .. ")$" },
                    workspace        = trim(ws),
                    fullscreen_state = 1, -- maximized: waybar and gaps stay
                })
            end
        end
    end
    file:close()
end

-- digits by physical keycode (AZERTY-safe, like binds.lua): 1..9 => 10..18, 0 => 19
for n = 1, 10 do
    local key = "code:" .. (9 + n)
    hl.bind("ALT + " .. key,          hl.dsp.exec_cmd(wsgroups .. " focus " .. n))
    hl.bind("SUPER + CTRL + " .. key, hl.dsp.exec_cmd(wsgroups .. " go " .. n))
end
