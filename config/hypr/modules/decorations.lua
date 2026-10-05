-- Look and feel, after Omarchy's default/hypr/looknfeel.lua

local active_border_color   = THEME_BORDER
local inactive_border_color = THEME_BORDER_DIM

hl.config({
    general = {
        gaps_in = 2,
        gaps_out = 4,
        border_size = 1,
        -- resize a window by dragging its edge, as on a desktop (the floating ones above all: Super + T);
        -- the grab area reaches a few px past the 1px border, and the cursor shows the resize arrows
        resize_on_border = true,
        extend_border_grab_area = 8,
        hover_icon_on_border = true,
        allow_tearing = false,
        layout = "dwindle",
        col = {
            active_border = active_border_color,
            inactive_border = inactive_border_color,
        },
    },
    decoration = {
        rounding = 0,
        dim_special = 0.3,
        shadow = {
            enabled = true,
            range = 18,
            render_power = 3,
            color = "rgba(0000004d)",
        },
        blur = {
            enabled = true,
            size = 6,
            passes = 3,
            special = true,
            brightness = 0.60,
            contrast = 0.75,
        },
    },
    group = {
        col = {
            border_active = active_border_color,
            border_inactive = inactive_border_color,
        },
        groupbar = {
            font_size = 12,
            font_family = "JetBrainsMono Nerd Font",
            font_weight_active = "ultraheavy",
            font_weight_inactive = "normal",
            indicator_height = 1,
            indicator_gap = 5,
            height = 22,
            gaps_in = 5,
            gaps_out = 0,
            text_color = "rgb(ffffff)",
            text_color_inactive = "rgba(ffffff90)",
            col = {
                active = "rgba(00000040)",
                inactive = "rgba(00000020)",
            },
            gradients = true,
            gradient_rounding = 0,
            gradient_round_only_edges = false,
        },
    },
    dwindle = {
        force_split = 2,
    },
    cursor = {
        hide_on_key_press = true,
        -- the pointer stays where it is: switching windows or workspaces (keys, bar clicks, Alt + Tab)
        -- doesn't jump it to the centre of the window
        no_warps = true,
        warp_on_change_workspace = 0,
    },
    binds = {
        hide_special_on_workspace_change = true,
    },
})
