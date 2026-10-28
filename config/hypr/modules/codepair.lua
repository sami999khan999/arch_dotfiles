-- Code + terminal pairs on the Code workspace: every VS Code window gets its own kitty beside it,
-- kitty 45 % on the left and VS Code 55 %, so one pair fills the screen. The workspace uses the
-- scrolling layout: pairs sit side by side as columns, and Super + Q…P (VS Code windows only) slides
-- the view to pair N. kitty-pair (local/bin) opens each kitty in its window's project folder, as
-- its own app, with the class code-term-<VS Code address>: that's how a pair is found again.
-- Opening another project in a VS Code window takes its kitty there too; closing the window closes it.
-- The layout of a pair (the kitty's width, its side) is set in a popup: Super + Alt + P on a VS Code
-- window or its kitty opens local/lib/panels/pairgui.py, which calls applyPairLayout. Its defaults
-- for new pairs are in ~/.config/hypr/codepair.conf (the popup writes it).
-- Globals: PAIR_WS (wsgroups.lua gives it this layout), isPairTerm and pairMain (binds.lua:
-- Super + Q…P / Alt + Tab count VS Code windows, not their kittys), focusPair (Super + Q…P, Alt + Tab),
-- toggleFullscreen and widenPairHalf (binds.lua: Super + F, Super + Alt + F / C / T),
-- openPairPanel (binds.lua: Super + Alt + P), applyPairLayout (pairgui.py, through hyprctl eval).

-- VS Code's window class: "code" in older versions, "com.microsoft.VSCode" in newer ones
local CODE = { ["code"] = true, ["com.microsoft.VSCode"] = true }
-- the Code workspace: the group in workspaces.conf whose apps include VS Code (it can be moved in
-- Settings → Workspaces), 1 without one
PAIR_WS = "1"
do
    local f = io.open(os.getenv("HOME") .. "/.config/hypr/workspaces.conf")
    if f then
        for line in f:lines() do
            local ws, classes = line:match("^%s*(%d+)%s*|[^|]*|([^|]*)")
            if ws then
                for c in classes:gmatch("[^,%s]+") do
                    if CODE[c] then PAIR_WS = ws end
                end
            end
        end
        f:close()
    end
end
local TERM = "code-term-"
local MIN_SHARE  = 0.10   -- the kitty never gets less than this share of a pair
-- VS Code doesn't draw narrower than its title bar allows (the search box and layout buttons: about
-- 600 px, sidebar or not); squeezed further it keeps its width and Hyprland crops its right side off
local CODE_MIN_PX = 640

-- the layout new pairs open with: the kitty's share of the screen and its side ("left" / "right")
local CONF = os.getenv("HOME") .. "/.config/hypr/codepair.conf"
local termShare, termSide = 0.45, "left"
do
    local f = io.open(CONF)
    if f then
        for line in f:lines() do
            local k, v = line:match("^%s*([%w_]+)%s*=%s*(%S+)")
            if k == "share" and tonumber(v) then termShare = tonumber(v) end
            if k == "side" and (v == "left" or v == "right") then termSide = v end
        end
        f:close()
    end
end

hl.config({ scrolling = { column_width = 1 - termShare, focus_fit_method = 1 } })
-- a pair's kitty stays with its VS Code (other kitty windows go to the Terminal workspace)
hl.window_rule({ match = { class = "^(code-term-.*)$" }, workspace = PAIR_WS })
-- a pair appears and switches like one window: no sliding between pairs, no open/close animation
hl.window_rule({ match = { class = "^(code|com\\.microsoft\\.VSCode|code-term-.*)$" }, no_anim = true })

function isPairTerm(w)
    return w ~= nil and w.class:sub(1, #TERM) == TERM
end

local function find(test)
    for _, w in ipairs(hl.get_windows()) do
        if test(w) then return w end
    end
end

local function termOf(code)
    return find(function(w) return w.class == TERM .. code.address end)
end

local function codeOf(term)
    local addr = term.class:sub(#TERM + 1)
    return find(function(w) return w.address == addr end)
end

local function onPairWs(w)
    return w.workspace ~= nil and tostring(w.workspace.id) == PAIR_WS
end

local function focus(w)
    hl.dispatch(hl.dsp.focus({ window = "address:" .. w.address }))
end

-- the tiled window just left of w on its workspace (columns are ordered by x)
local function leftOf(w)
    local best
    for _, o in ipairs(w.workspace:get_windows()) do
        if o.address ~= w.address and o.mapped and not o.floating and o.at.x < w.at.x
                and (not best or o.at.x > best.at.x) then
            best = o
        end
    end
    return best
end

-- the column at a pair's left edge (the kitty, or VS Code when the kitty sits on the right)
local function pairLeft(code, term)
    if not term then return code end
    return term.at.x < code.at.x and term or code
end

-- the window that stands for w in Super + Q…P / Alt + Tab: a pair's kitty counts as its VS Code
function pairMain(w)
    return isPairTerm(w) and codeOf(w) or w
end

-- Super + Q…P / Alt + Tab: show the whole pair. Focusing its left column first scrolls the view to the
-- pair's left edge; the two columns together are exactly the screen, so VS Code then fits
-- without scrolling again. Any other window: focused as usual.
function focusPair(w)
    local term = CODE[w.class] and termOf(w)
    if term then focus(pairLeft(w, term)) end
    focusKeepingFullscreen(w)
end

-- Super + F (fullscreen) / Super + Alt + F (maximized, bar stays): the focused window fills the
-- screen; pressed again, a VS Code or its kitty goes back beside its partner. Leaving fullscreen
-- alone left the view scrolled with the kitty off screen; focusing the kitty first brings the pair
-- back into view, then the focus returns to the window that was full.
function toggleFullscreen(mode)
    local w = hl.get_active_window()
    if not w then return end
    local leaving = w.fullscreen ~= 0
    hl.dispatch(hl.dsp.window.fullscreen({ mode = mode }))
    if leaving and onPairWs(w) and (CODE[w.class] or isPairTerm(w)) then
        local main = pairMain(w)
        local term = main and termOf(main)
        hl.timer(function()
            if term then focus(pairLeft(main, term)) end
            focus(w)
        end, { timeout = 50, type = "oneshot" })
    end
end

-- Super + Alt + C / Super + Alt + T: the VS Code / the kitty of the pair on screen takes the full
-- width (bar stays), whichever of the two has the focus; the same key again: back side by side.
-- Sets the state explicitly (fullscreen_state) rather than toggling, so it also undoes Super + F.
local function setFull(w, on)
    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = on and 1 or 0, client = 0, window = "address:" .. w.address }))
end

function widenPairHalf(which)
    local active = hl.get_active_window()
    local code = active and onPairWs(active) and pairMain(active)
    if not (code and CODE[code.class]) then return end
    local term = termOf(code)
    local target = which == "code" and code or term
    if not target then return end
    local other = target == code and term or code
    if target.fullscreen ~= 0 then
        setFull(target, false)
        hl.timer(function()        -- as in toggleFullscreen: bring the whole pair back into view
            if term then focus(pairLeft(code, term)) end
            focus(target)
        end, { timeout = 50, type = "oneshot" })
        return
    end
    if other and other.fullscreen ~= 0 then setFull(other, false) end
    focus(target)
    setFull(target, true)
end

-- ---- the layout of a pair (Super + Alt + P: pairgui.py) ----------------------------------------
-- the width a pair fills: the screen less the outer gaps, the gap between its columns, four borders
local function fillWidth(code)
    local mon = code.monitor
    local out, inner = hl.get_config("general.gaps_out"), hl.get_config("general.gaps_in")
    if not mon or type(out) ~= "table" or type(inner) ~= "table" then return nil end
    local border = hl.get_config("general.border_size") or 0
    return mon.width - out.left - out.right - inner.left - inner.right - 4 * border
end

-- the kitty's share, kept between MIN_SHARE and what leaves VS Code CODE_MIN_PX
local function clampShare(code, share)
    local total = fillWidth(code)
    local most = total and math.min(1 - MIN_SHARE, 1 - CODE_MIN_PX / total) or 1 - MIN_SHARE
    return math.max(MIN_SHARE, math.min(most, share))
end

-- the kitty right beside its VS Code, on `side` (nil: where it is if it's already beside it, else the default): swapcol steps it over the columns between them. A kitty
-- opens right of whatever has the focus as it appears, and during a restore that can be another
-- window (B C kittyC kittyB). Positions are read once: at.x lags the swaps.
local function adjoin(code, term, side)
    local cols = {}
    for _, o in ipairs(code.workspace:get_windows()) do
        if o.mapped and not o.floating then cols[#cols + 1] = o end
    end
    table.sort(cols, function(a, b) return a.at.x < b.at.x end)
    local i, j
    for k, o in ipairs(cols) do
        if o.address == term.address then i = k end
        if o.address == code.address then j = k end
    end
    if not (i and j) or (not side and math.abs(i - j) == 1) then return end   -- no side: kept if together
    side = side or termSide
    local target = side == "left" and (i > j and j or j - 1) or (i > j and j + 1 or j)
    if target == i then return end
    focus(term)
    for _ = 1, math.abs(target - i) do
        hl.dispatch(hl.dsp.layout("swapcol " .. (target < i and "l" or "r")))
    end
    return true
end

-- every pair on the Code workspace back together, the focus where it was (hyprctl eval 'tidyPairs()').
-- One pair per step, a moment apart, so each step reads the positions the last one left.
function tidyPairs(active)
    active = active or hl.get_active_window()
    for _, w in ipairs(hl.get_windows()) do
        local term = CODE[w.class] and onPairWs(w) and termOf(w)
        if term and adjoin(w, term) then
            hl.timer(function() tidyPairs(active) end, { timeout = 50, type = "oneshot" })
            return
        end
    end
    if active then focus(active) end
end

-- one pair: back side by side, the kitty to its side, then both widths with colresize (the only
-- resize the scrolling layout always honours, on the focused column), then the view back to the
-- pair's left edge.
local function layoutPair(code, share, side)
    local term = termOf(code)
    if not term then return end
    if term.fullscreen ~= 0 then setFull(term, false) end
    if code.fullscreen ~= 0 then setFull(code, false) end
    share = clampShare(code, share)
    local termLeft = term.at.x < code.at.x
    if (side == "right") == termLeft then
        focus(term)
        hl.dispatch(hl.dsp.layout("swapcol " .. (termLeft and "r" or "l")))
    end
    focus(term)
    hl.dispatch(hl.dsp.layout("colresize " .. share))
    focus(code)
    hl.dispatch(hl.dsp.layout("colresize " .. (1 - share)))
    focus(side == "right" and code or term)
    focus(code)
end

-- pairgui.py (hyprctl eval): lay out the pair of VS Code window `address` (or every pair, all = true)
-- with the kitty's share and side; remember = new pairs open like this. The focus goes back to the
-- popup, and the view ends on the chosen pair.
function applyPairLayout(address, share, side, all, remember)
    local active = hl.get_active_window()
    local target = find(function(w) return w.address == address end)
    if all then
        for _, w in ipairs(hl.get_windows()) do
            if CODE[w.class] and onPairWs(w) and not (target and w.address == target.address) then
                layoutPair(w, share, side)
            end
        end
    end
    if target then layoutPair(target, share, side) end
    if remember then
        termShare, termSide = target and clampShare(target, share) or share, side
    end
    if active and target and active.address ~= target.address then focus(active) end
end

-- pairgui.py: "term" / "code" takes the whole width, the other column moves off screen beside it
-- (a plain column width, not fullscreen: the popup stays above it, the bar stays); "none" is left to
-- applyPairLayout, which puts the pair back side by side. The focus goes back to the popup.
function fullPair(address, which)
    local active = hl.get_active_window()
    local code = find(function(w) return w.address == address end)
    local term = code and termOf(code)
    if not term then return end
    if term.fullscreen ~= 0 then setFull(term, false) end
    if code.fullscreen ~= 0 then setFull(code, false) end
    local target = (which == "term" and term) or (which == "code" and code) or nil
    if target then
        focus(target)
        hl.dispatch(hl.dsp.layout("colresize 1.0"))
        focus(target)   -- the view on it
    end
    if active and active.address ~= code.address and active.address ~= term.address then focus(active) end
end

-- Super + Alt + P: the popup for the pair of the focused VS Code window or kitty
function openPairPanel()
    local w = hl.get_active_window()
    local code = w and onPairWs(w) and pairMain(w)
    if code and CODE[code.class] and termOf(code) then
        hl.exec_cmd("~/.local/lib/panels/pairgui.py " .. code.address)
    else
        hl.exec_cmd("notify-send -a Pairs 'No VS Code pair focused' 'Super + Alt + P works on a VS Code window or the kitty beside it'")
    end
end

local function pairUp(w)
    -- a new column opens right of the focused one: if that was the left column of another pair,
    -- this window landed inside that pair; hop over to the right of it
    local left = leftOf(w)
    focus(w)
    if left and ((termSide == "left" and isPairTerm(left)) or (termSide == "right" and CODE[left.class])) then
        hl.dispatch(hl.dsp.layout("swapcol r"))
    end
    -- full width until its kitty is there, so it never shows half-width beside the old pair
    hl.dispatch(hl.dsp.layout("colresize 1.0"))
    hl.exec_cmd("~/.local/bin/kitty-pair " .. w.address)
end

-- VS Code windows waiting for their kitty. A window opened where no screen shows (Setups opens windows
-- quietly) waits for its workspace to be shown: pairing it moves the focus there, and the screen with
-- it. They're paired one at a time: a kitty opens beside whichever window has the focus when it
-- appears, and two kitty-pairs at once (a restore's VS Code windows, when the workspace is first
-- shown) each focused their own VS Code, so one kitty landed inside the other pair.
local unpaired = {}   -- address -> true
local pairing = nil   -- the VS Code window whose kitty is on its way
local PAIR_GIVEUP_MS = 10000   -- a kitty that never opens doesn't hold up the others
local PAIR_SETTLE_MS = 900     -- after the kitty's focus hand-backs (250, 700 ms below)

local function pairNext()
    if pairing then return end
    for address in pairs(unpaired) do
        local w = hl.get_window("address:" .. address)
        if not w or not onPairWs(w) then
            unpaired[address] = nil
        elseif w.workspace.visible then
            unpaired[address], pairing = nil, address
            pairUp(w)
            hl.timer(function()
                if pairing == address then pairing = nil; pairNext() end
            end, { timeout = PAIR_GIVEUP_MS, type = "oneshot" })
            return
        end
    end
end

hl.on("workspace.active", pairNext)

hl.on("window.open", function(w)
    if not w or w.floating then return end
    if CODE[w.class] and onPairWs(w) then
        unpaired[w.address] = true
        pairNext()
    elseif isPairTerm(w) then
        -- the kitty opened as the column right of whatever had the focus (kitty-pair focused its VS
        -- Code, but a restore's windows can take it): give it its width, move it beside VS Code on its
        -- side once it has a position (adjoin), VS Code the rest, then show the pair and give the focus
        -- back to VS Code
        local code = codeOf(w)
        local share = code and clampShare(code, termShare) or termShare   -- VS Code keeps CODE_MIN_PX
        focus(w)
        hl.dispatch(hl.dsp.layout("colresize " .. share))
        if not code then return end
        hl.timer(function()
            code, w = codeOf(w), hl.get_window("address:" .. w.address)
            if not (code and w) then return end
            adjoin(code, w, termSide)
            focus(code)
            hl.dispatch(hl.dsp.layout("colresize " .. (1 - share)))
            focus(termSide == "left" and w or code)   -- the view to the pair's left edge (see focusPair)
            focus(code)
            -- kitty takes the focus again while it finishes starting up: hand it back, twice
            for _, ms in ipairs({ 250, 700 }) do
                hl.timer(function() focus(code) end, { timeout = ms, type = "oneshot" })
            end
            -- this pair is done once those have run: the next VS Code window's turn
            if pairing == code.address then
                hl.timer(function()
                    if pairing == code.address then pairing = nil; pairNext() end
                end, { timeout = PAIR_SETTLE_MS, type = "oneshot" })
            end
        end, { timeout = 50, type = "oneshot" })
    end
end)

-- Another project opened in a VS Code window (its title's folder part changed): the kitty follows,
-- by cd if it's idle or with a new tab if something is running (kitty-pair --follow). The title
-- also changes with every file switched to, so only a different folder part counts.
local folderOf = {}   -- VS Code address -> the folder part of its title last seen

local function titleFolder(title)
    local rest = title:match("^(.*) %- Visual Studio Code$")
    return rest and (rest:match(".* %- (.-)$") or rest)
end

hl.on("window.title", function(w)
    if not (w and CODE[w.class] and onPairWs(w)) then return end
    local folder = titleFolder(w.title)
    if folder and folder ~= folderOf[w.address] then
        folderOf[w.address] = folder
        hl.exec_cmd("~/.local/bin/kitty-pair --follow " .. w.address)
    end
end)

hl.on("window.close", function(w)
    if w and CODE[w.class] then
        folderOf[w.address], unpaired[w.address] = nil, nil
        local term = termOf(w)
        if term then hl.dispatch(hl.dsp.window.close({ window = "address:" .. term.address })) end
    end
end)
