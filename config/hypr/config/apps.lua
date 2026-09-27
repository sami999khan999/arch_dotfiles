-- App hotkeys: reads ~/.config/hypr/apps.conf and binds each line to focus-or-launch.sh.
-- Edit apps.conf, not this file.

local conf   = os.getenv("HOME") .. "/.config/hypr/apps.conf"
local script = "~/.config/hypr/scripts/focus-or-launch.sh"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local file = io.open(conf, "r")
if not file then return end

for raw in file:lines() do
    local line = trim(raw)
    if line ~= "" and not line:match("^#") then
        local keys, class, cmd = line:match("^(.-)|(.-)|(.+)$")
        if keys then
            keys, class, cmd = trim(keys), trim(class), trim(cmd)
            hl.bind(keys, hl.dsp.exec_cmd(script .. " '" .. class .. "' " .. cmd))
        end
    end
end

file:close()
