hl.config({
    dwindle = {
        preserve_split = true,
    },
    ecosystem = {
        no_update_news = true,
        no_donation_nag = true,
    },
    misc = {
        col = {
            splash = THEME_ACCENT,
        },
        -- behind the wallpaper: plain base, not Hyprland's own (an anime mascot, with its logo), which
        -- flashed on shutdown once awww had quit before Hyprland
        force_default_wallpaper = 0,
        disable_hyprland_logo = true,
        disable_splash_rendering = true,
        background_color = THEME_BACKGROUND,
        -- wake a switched-off monitor (hypridle, 5.5 min idle) on any key or mouse move. hypridle's
        -- own on-resume command is the usual way back; this is the safety net if it doesn't fire,
        -- which once left the screen black until a power-button shutdown
        key_press_enables_dpms = true,
        mouse_move_enables_dpms = true,
        middle_click_paste = false,
        enable_swallow = true,
        swallow_regex = "(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal)",
        vrr = 3,
        -- the cursor crossing to the other screen doesn't make it the focused one: the keys (Super + N,
        -- the bar's active workspace) stay on the screen they were on; a click or a shortcut moves it
        mouse_move_focuses_monitor = false,
        on_focus_under_fullscreen = 2, -- other focus changes drop fullscreen; Alt+N / Alt+Tab keep a video fullscreen (binds.lua)
    },
    render = {
        direct_scanout = 2,
        -- Use the option below if you find games constantly black screening for a couple seconds whenever direct scanout enables/disables
        -- non_shader_cm = 0,
    },
    xwayland = {
        force_zero_scaling = true
    },
})
