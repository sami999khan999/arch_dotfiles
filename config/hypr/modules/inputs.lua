-- Input configuration

hl.config({
    input = {
        -- sensitivity = -0.25,
        accel_profile = "flat",
        -- the mouse crossing between a floating and a tiled window doesn't move the focus (1 did: hovering
        -- off a Super + T window focused the tile under it, which then covered the float, binds.lua);
        -- a click or a shortcut does, as with follow_mouse = 2 everywhere else
        float_switch_override_focus = 0,
    },
    -- Uncomment the section below to enable software cursors; this can help with cursor display or behavior issues
    -- cursor = {
    --     no_hardware_cursors = 1,
    -- },
})

hl.gesture({ fingers = 4, direction = "horizontal", action = "workspace" })
hl.gesture({ fingers = 3, direction = "down",       action = "close" })
hl.gesture({ fingers = 3, direction = "up",         action = "fullscreen" })
hl.gesture({ fingers = 3, direction = "left",       action = "float" })
