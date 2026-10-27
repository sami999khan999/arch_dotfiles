-- Mouse keys: the arrows drive the cursor (Super + Alt + M toggles, binds.lua).
-- A submap, so while it is on the arrows / Space / Return / Page Up-Down are the mouse's and every
-- other key types as usual. Held arrows glide and speed up (a timer moves the cursor every frame
-- while the key is down: key repeat would wait 600 ms first, then stutter); diagonals by holding two.
-- Whether a key is still down is asked each tick: with two arrows held, Hyprland sends the release
-- bind of the last one pressed only, and the other ran the cursor away. Hyprland moves the cursor
-- itself; clicks and scrolling go through wlrctl (the virtual-pointer protocol: Hyprland has no
-- dispatcher that clicks).

local SUBMAP = "mousekeys"
local TICK = 16                                  -- ms, about one frame
local SPEED = {                                  -- px per tick: at the press, after RAMP ms held
    normal  = { 2, 16 },
    fast    = { 6, 40 },                         -- + Shift
    precise = { 1, 1 },                          -- + Ctrl: a pixel at a time
}
local RAMP = 500
local DIRS = { left = { -1, 0 }, right = { 1, 0 }, up = { 0, -1 }, down = { 0, 1 } }
local KEYSYM = { left = "Left", right = "Right", up = "Up", down = "Down" }

local held, speed, heldFor = {}, SPEED.normal, 0
local x, y                                       -- fractional position: slow speeds don't round to 0

local function osd(text)
    hl.exec_cmd('swayosd-client --monitor "$(hyprctl activeworkspace -j | jq -r .monitor)" ' ..
        '--custom-icon input-mouse --custom-message "' .. text .. '"')
end

-- one move; false once no arrow is held
local function step()
    for dir in pairs(held) do
        if not hl.is_key_down(KEYSYM[dir]) then held[dir] = nil end
    end
    local dx, dy = 0, 0
    for dir in pairs(held) do
        dx, dy = dx + DIRS[dir][1], dy + DIRS[dir][2]
    end
    if dx == 0 and dy == 0 then return next(held) ~= nil end   -- Left + Right: still, still held
    local t = math.min(heldFor / RAMP, 1)
    local px = speed[1] + (speed[2] - speed[1]) * t * t
    local p = hl.get_cursor_pos()
    if not x or math.abs(p.x - x) > 1 or math.abs(p.y - y) > 1 then
        x, y = p.x, p.y                          -- the real mouse moved it meanwhile
    end
    x, y = x + dx * px, y + dy * px
    hl.dispatch(hl.dsp.cursor.move({ x = math.floor(x + 0.5), y = math.floor(y + 0.5) }))
    return true
end

local timer
timer = hl.timer(function()
    heldFor = heldFor + TICK
    if not step() then timer:set_enabled(false) end
end, { timeout = TICK, type = "repeat" })
timer:set_enabled(false)

local function press(dir, mode)
    if not next(held) then heldFor = 0 end
    held[dir], speed = true, SPEED[mode]
    step()                                       -- a tap moves at once
    timer:set_enabled(true)
end

local function release(dir)
    held[dir] = nil
    if not next(held) then timer:set_enabled(false) end
end

local function stop()
    held = {}
    timer:set_enabled(false)
    hl.dispatch(hl.dsp.submap("reset"))
    osd("Mouse keys off")
end

function toggleMouseKeys()
    if hl.get_current_submap() == SUBMAP then
        stop()
    else
        hl.dispatch(hl.dsp.submap(SUBMAP))
        osd("Mouse keys on")
    end
end

hl.define_submap(SUBMAP, function()
    for _, dir in ipairs({ "left", "right", "up", "down" }) do
        hl.bind(dir,              function() press(dir, "normal") end)
        hl.bind("SHIFT + " .. dir, function() press(dir, "fast") end)
        hl.bind("CTRL + " .. dir,  function() press(dir, "precise") end)
        hl.bind(dir,              function() release(dir) end, { release = true, ignore_mods = true })
    end
    for _, key in ipairs({ "space", "return", "KP_Enter" }) do
        hl.bind(key,               hl.dsp.exec_cmd("wlrctl pointer click left"))
        hl.bind("SHIFT + " .. key, hl.dsp.exec_cmd("wlrctl pointer click right"))
        hl.bind("CTRL + " .. key,  hl.dsp.exec_cmd("wlrctl pointer click middle"))
    end
    hl.bind("prior", hl.dsp.exec_cmd("wlrctl pointer scroll -3 0"), { repeating = true })
    hl.bind("next",  hl.dsp.exec_cmd("wlrctl pointer scroll 3 0"),  { repeating = true })
    hl.bind("escape",          stop)
    hl.bind("SUPER + ALT + M", stop)
end)
