-- Code + terminal pairs on the Code workspace: every VS Code window gets its own kitty beside it,
-- kitty 45 % on the left and VS Code 55 %, so one pair fills the screen. The workspace uses the
-- scrolling layout: pairs sit side by side as columns, and Alt + N (VS Code windows only) slides
-- the view to pair N. kitty-pair (local/bin) opens each kitty in its window's project folder, as
-- its own app, with the class code-term-<VS Code address>: that's how a pair is found again.
-- Opening another project in a VS Code window takes its kitty there too; closing the window closes it.
-- Globals: PAIR_WS (wsgroups.lua gives it this layout), isPairTerm and pairMain (binds.lua:
-- Alt + N / Alt + Tab count VS Code windows, not their kittys), focusPair (Alt + N, Alt + Tab).

PAIR_WS = "1"
-- VS Code's window class: "code" in older versions, "com.microsoft.VSCode" in newer ones
local CODE = { ["code"] = true, ["com.microsoft.VSCode"] = true }
local TERM = "code-term-"
local TERM_WIDTH = 0.45

hl.config({ scrolling = { column_width = 1 - TERM_WIDTH, focus_fit_method = 1 } })
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

-- the window that stands for w in Alt + N / Alt + Tab: a pair's kitty counts as its VS Code
function pairMain(w)
    return isPairTerm(w) and codeOf(w) or w
end

-- Alt + N / Alt + Tab: show the whole pair. Focusing the kitty first scrolls the view to the
-- pair's left edge; the two columns together are exactly the screen, so VS Code then fits
-- without scrolling again. Any other window: focused as usual.
function focusPair(w)
    local term = CODE[w.class] and termOf(w)
    if term then focus(term) end
    focusKeepingFullscreen(w)
end

hl.on("window.open", function(w)
    if not w or w.floating then return end
    if CODE[w.class] and onPairWs(w) then
        -- a new column opens right of the focused one: if that was another pair's kitty, this
        -- window landed between the kitty and its VS Code; hop over to the right of that pair
        local left = leftOf(w)
        if isPairTerm(left) then
            focus(w)
            hl.dispatch(hl.dsp.layout("swapcol r"))
        end
        hl.exec_cmd("~/.local/bin/kitty-pair " .. w.address)
    elseif isPairTerm(w) then
        -- kitty-pair focused the VS Code window, so the kitty opened as the column to its right:
        -- make it the narrow column on the left, then give the focus back to VS Code
        focus(w)
        hl.dispatch(hl.dsp.layout("colresize " .. TERM_WIDTH))
        hl.dispatch(hl.dsp.layout("swapcol l"))
        -- kitty takes the focus again while it finishes starting up: hand it back, twice
        local code = codeOf(w)
        if code then
            focus(code)
            for _, ms in ipairs({ 250, 700 }) do
                hl.timer(function() focus(code) end, { timeout = ms, type = "oneshot" })
            end
        end
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
        folderOf[w.address] = nil
        local term = termOf(w)
        if term then hl.dispatch(hl.dsp.window.close({ window = "address:" .. term.address })) end
    end
end)
