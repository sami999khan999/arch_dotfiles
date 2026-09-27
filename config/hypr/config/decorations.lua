-- Look and feel, after Omarchy's default/hypr/looknfeel.lua

local active_border_color   = { colors = { TN_ACCENT, TN_MAGENTA }, angle = 45 }
local inactive_border_color = TN_MUTED

hl.config({
    general = {
        gaps_in = 5,
        gaps_out = 10,
        border_size = 2,
        resize_on_border = false,
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
            enabled = false,
        },
        blur = {
            enabled = true,
            size = 2,
            passes = 2,
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
        warp_on_change_workspace = 1,
    },
    binds = {
        hide_special_on_workspace_change = true,
    },
})
