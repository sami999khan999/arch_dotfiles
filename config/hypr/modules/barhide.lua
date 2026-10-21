-- The bar (waybar) hidden on some workspaces, or on all: Settings → Bar → Hide the bar. settings.lua
-- (generated from settings.json, required last) sets BAR_HIDE; each screen's bar follows the workspace
-- shown on that screen. Super + Shift + Space still flips the focused screen's bar by hand, until that
-- screen switches to a workspace that wants it the other way.
-- SIGUSR1 flips every bar of a waybar process, so each screen has its own (scripts/waybars.py), and
-- waybar can't be asked which way a bar is: that's read off its layer surface (hidden, waybar moves it
-- from the top layer to the bottom one).

BAR_HIDE = { all = false, workspaces = {} }   -- workspaces: id -> true
local wanted = {}   -- screen name -> shown (true) / hidden (false), as the last check wanted it

local function wantShown(mon)
    if BAR_HIDE.all then return false end
    local ws = mon.active_workspace
    return not (ws and BAR_HIDE.workspaces[ws.id])
end

-- force: set the bars as wanted even if the wish hasn't changed (a setting changed, a new bar)
function barApply(force)
    for _, l in ipairs(hl.get_layers({ namespace = "waybar" })) do
        local mon = l.monitor
        if mon then
            local want = wantShown(mon)
            if want ~= wanted[mon.name] or force then
                wanted[mon.name] = want
                if (l.layer >= 2) ~= want then hl.exec_cmd("kill -USR1 " .. l.pid) end
            end
        end
    end
end

-- Super + Shift + Space: the focused screen's bar shown / hidden for now
function barFlip()
    for _, l in ipairs(hl.get_layers({ namespace = "waybar" })) do
        if l.monitor and l.monitor.focused then hl.exec_cmd("kill -USR1 " .. l.pid) end
    end
end

hl.on("workspace.active", function() barApply() end)
hl.on("monitor.focused", function() barApply() end)
hl.on("workspace.move_to_monitor", function() barApply() end)
-- waybar (re)started or reloaded: its new bars show; once they're up, hide them if wanted
hl.on("layer.opened", function(l)
    if l and l.namespace == "waybar" then
        hl.timer(function() barApply(true) end, { timeout = 300, type = "oneshot" })
    end
end)
hl.timer(function() barApply() end, { timeout = 50, type = "oneshot" })   -- a config reload: after settings.lua
