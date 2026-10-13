-- The wallpaper blurs behind windows: a screen whose workspace has a window shows the wallpaper's
-- blurred copy, so the thin gaps around the windows are soft; an empty workspace shows the picture
-- itself. scripts/wallpaper.sh keeps both pictures and fades between them (awww); this says when.

local WALLPAPER = "~/.config/hypr/scripts/wallpaper.sh"
local STATE = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/wallblur-"   -- wallpaper.sh's: on / off per screen
local shown = {}   -- monitor name -> "on" / "off": what that screen shows now

-- a config reload starts this module afresh: what each screen shows is read back from wallpaper.sh's
-- state, so a reload doesn't fade every screen again (and start wallpaper.sh for each)
local function was(name)
    local f = io.open(STATE .. name)
    if not f then return nil end
    local v = f:read("l")
    f:close()
    return v
end

local function update()
    for _, m in ipairs(hl.get_monitors()) do
        local ws = m.active_workspace
        local want = (ws and not ws.is_empty) and "on" or "off"
        if shown[m.name] == nil then shown[m.name] = was(m.name) end
        if shown[m.name] ~= want then
            shown[m.name] = want
            hl.exec_cmd(WALLPAPER .. " blur " .. m.name .. " " .. want)
        end
    end
end

-- after the event has settled: a closing window is still counted while its own event runs
local function later()
    hl.timer(update, { timeout = 80, type = "oneshot" })
end

for _, event in ipairs({ "window.open", "window.close", "window.destroy", "window.move_to_workspace",
                         "workspace.active", "monitor.added", "hyprland.start" }) do
    hl.on(event, later)
end
later()   -- a config reload: what each screen shows now
