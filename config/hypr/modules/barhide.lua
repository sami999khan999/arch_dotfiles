-- The bar (waybar) hidden on some workspaces, or on all: Settings → Bar → Hide the bar. settings.lua
-- (generated from settings.json, required last) sets BAR_HIDE; this follows the focused workspace and
-- shows or hides the bar to match. Super + Shift + Space still flips it by hand, until a switch to a
-- workspace that wants it the other way.
-- waybar has one switch for all its bars (SIGUSR1 flips them) and can't be asked which way they are, so
-- that's read off its layer surface: hidden, waybar moves it from the top layer to the bottom one.

BAR_HIDE = { all = false, workspaces = {} }   -- workspaces: id -> true
local wanted   -- shown (true) / hidden (false), as the last check wanted it

local function wantShown()
    if BAR_HIDE.all then return false end
    local ws = hl.get_active_workspace()
    return not (ws and BAR_HIDE.workspaces[ws.id])
end

-- force: set the bar as wanted even if the wish hasn't changed (a setting changed, a new bar)
function barApply(force)
    local want = wantShown()
    if want == wanted and not force then return end
    wanted = want
    local pids, shown = {}, false
    for _, l in ipairs(hl.get_layers({ namespace = "waybar" })) do
        pids[l.pid] = true
        shown = shown or l.layer >= 2
    end
    if next(pids) and shown ~= want then
        for pid in pairs(pids) do hl.exec_cmd("kill -USR1 " .. pid) end
    end
end

hl.on("workspace.active", function() barApply() end)
hl.on("monitor.focused", function() barApply() end)
-- waybar (re)started or reloaded: its new bars show; once they're up, hide them if wanted
hl.on("layer.opened", function(l)
    if l and l.namespace == "waybar" then
        hl.timer(function() barApply(true) end, { timeout = 300, type = "oneshot" })
    end
end)
hl.timer(function() barApply() end, { timeout = 50, type = "oneshot" })   -- a config reload: after settings.lua
