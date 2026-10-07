-- Window rules wiki https://wiki.hypr.land/Configuring/Basics/Window-Rules/

-- Generic floating position
hl.window_rule({ match = { float = true }, persistent_size = true })

-- Picture-in-Picture
hl.window_rule({
    match             = { title = "^([Pp]icture[-\\s]?[Ii]n[-\\s]?[Pp]icture)(.*)$" },
    float             = true,
    keep_aspect_ratio = true,
    size              = { "max(monitor_w, monitor_h)*0.25", "min(monitor_w, monitor_h)*0.25" },
    pin               = true,
})

-- Gaming
local gamingApps = "^(steam_app.*|gamescope)$"
local gamingWorkspace = "name:gaming"

hl.window_rule({ match = { content = "game" }, workspace = gamingWorkspace })
hl.window_rule({ match = { xdg_tag = "^(.*game.*)$" }, workspace = gamingWorkspace, fullscreen_state = 2, content = "game", sync_fullscreen = true })
hl.window_rule({ match = { class = gamingApps }, workspace = gamingWorkspace })
hl.window_rule({ match = { class = "^(steam)$", title = "^(Friends List)$" }, float = true })
hl.window_rule({ match = { class = "^(steam)$", title = "^(Launching\\.{3})$" }, float = true, center = true, workspace = gamingWorkspace })
hl.window_rule({
    match = {
        class         = gamingApps,
        title         = "^(.+)$",
        initial_title = "negative:^(.*\\\\home\\\\.*)$",
    },
    content          = "game",
    decorate         = false,
    fullscreen_state = 2,
    size             = { "monitor_w", "monitor_h" },
    sync_fullscreen  = true,
})
hl.window_rule({
    match = {
        class         = "^(steam_app.*)$",
        initial_title = "^$",
    },
    center           = true,
    float            = true,
    fullscreen       = false,
    fullscreen_state = 0,
    workspace        = gamingWorkspace,
})

-- Apps
hl.window_rule({ match = { class = "^(.*\\.exe)$", float = true }, monitor = PRIMARY_MONITOR, center = true, fullscreen_state = 0 })
hl.window_rule({ match = { class = "^(.*[Ll]auncher.*)$" }, float = true, monitor = PRIMARY_MONITOR })
hl.window_rule({ match = { class = "^(vesktop|discord)$" }, monitor = PRIMARY_MONITOR })
hl.window_rule({ match = { class = "^(.*[Cc]alc.*)$" }, float = true, size = { "max(monitor_w, monitor_h)*0.17", "min(monitor_w, monitor_h)*0.43" } })
hl.window_rule({ match = { class = "^(org\\.kde\\.keditfiletype)$" }, float = true })
hl.window_rule({ match = { class = "^(org\\.kde\\.ark)$" }, size = { "max(monitor_w, monitor_h)*0.40", "min(monitor_w, monitor_h)*0.40" } })
hl.window_rule({ match = { class = "^(.*swash)$", title = "^(Swash)$" }, min_size = { "max(monitor_w, monitor_h)*0.35", "min(monitor_w, monitor_h)*0.35" }, float = true })
hl.window_rule({ match = { class = "^(dev\\.)?(noctalia\\.Noctalia(\\.Settings)?)$" }, float = true, size = { "monitor_w*0.70", "monitor_h*0.70" } })

-- Opacity Overrides
local terminals = "^(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal)$"

hl.window_rule({ match = { class = "^(firefox|zen)$" }, opacity = "1.0 override" })
hl.window_rule({ match = { class = terminals }, opacity = "1.0 override" }) -- Override opacity in favor of terminal settings for opacity. If your terminal doesn't support transparency, you can remove this rule.
hl.window_rule({ match = { class = "^(mpv|org.kde.haruna|.*plex.*|org\\.kde\\.gwenview|.*vlc.*)$" }, opacity = "1.0 override" })

-- Float Utility Windows
local floatApps = {
    { class = "^(kvantummanager|qt[56]ct|nwg-look)$" },
    { class = "^(org.pulseaudio.pavucontrol|blueman-manager|nm-applet|nm-connection-editor)$" },
    { title = "^(Winetricks.*|Protontricks.*)$" },
}
for _, m in ipairs(floatApps) do hl.window_rule({ match = m, float = true }) end

-- Float Common Modals
local modalMatches = {
    { title = "^(Open|Authentication Required|Add Folder to Workspace|Choose Files|Save As|Confirm to replace files|File Operation Progress)$" },
    { initial_title = "^(Open File)$" },
    { class = "^([Xx]dg-desktop-portal-gtk)$" },
    { title = "^(File Upload|Choose wallpaper|Library)(.*)$" },
    { class = "^(.*dialog.*)$" },
    { title = "^(.*dialog.*)$" },
    { class = "^(hyprland-share-picker)$"},
}
for _, m in ipairs(modalMatches) do hl.window_rule({ match = m, float = true }) end

-- Ignore maximize requests from all apps. You'll probably like this.
hl.window_rule({
    name  = "suppress-maximize-events",
    match = { class = ".*" },
    suppress_event = "maximize",
})

-- Fix some dragging issues with XWayland
hl.window_rule({
    name  = "fix-xwayland-drags",
    match = {
        class      = "^$",
        title      = "^$",
        xwayland   = true,
        float      = true,
        fullscreen = false,
        pin        = false,
    },
    no_focus = true,
})

-- Omarchy-style floating windows: TUIs, viewers, file dialogs
hl.window_rule({ match = { class = "^(TUI\\.float|imv|mpv|org\\.gnome\\.NautilusPreviewer|org\\.gnome\\.Evince|xdg-desktop-portal-gtk|codesync\\.folderpick|panels\\..*)$", title = "negative:^(panels-backdrop)$" }, tag = "+floating-window" })
hl.window_rule({ match = { title = "^(Open.*Files?|Open [Ff]older.*|Save.*Files?|Save.*As|Save|All Files|[Cc]hoose.*)$" }, tag = "+floating-window" })
hl.window_rule({ match = { tag = "floating-window" }, float = true, center = true, size = { 875, 600 } })
-- the code sync panel (waybar sync icon): two columns of details
hl.window_rule({ match = { tag = "floating-window", title = "^(syncpanel\\.py)$" }, size = { 1180, 640 } })
-- GUI panels (local/lib/panels, GTK): sized here, not by the app, so they open the same every time
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.workspaces)$" }, size = { 900, 560 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.system)$" }, size = { 980, 680 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.audio)$" }, size = { 900, 520 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.network)$" }, size = { 680, 590 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.bluetooth)$" }, size = { 760, 560 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.notifications)$" }, size = { 460, 620 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.theme)$" }, size = { 1060, 560 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.shortcuts)$" }, size = { 760, 600 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.codesync)$" }, size = { 1180, 640 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.settings)$" }, size = { 1000, 680 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.pair)$" }, size = { 680, 670 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.agentmux)$" }, size = { 860, 620 } })
-- agentmux's pickers (agentpickgui.py): New thread (which agent), Open project (folder browser)
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.newthread)$" }, size = { 620, 440 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.openproject)$" }, size = { 820, 600 } })
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.quitagentmux)$" }, size = { 560, 300 } })
-- Commit, from the Projects panel (commitgui.py): the message and the changed files
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.commit)$" }, size = { 900, 640 } })
-- Ask, from the Projects panel (askgui.py): large, by the screen's size (1440×886 on 1920×1080, 1024×630 on 1366×768)
hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.ask)$" }, size = { "monitor_w*0.75", "monitor_h*0.82" } })
-- the audio mixer (waybar volume click, Super + Ctrl + A): a few rows of streams, not a full page
hl.window_rule({ match = { tag = "floating-window", title = "^(wiremix)$" }, size = { 720, 300 } })

-- Slight transparency everywhere, except media. The same for unfocused windows: 0.9 made them look
-- dimmed (more of the dark blur behind showing through); Settings → Appearance sets the rest
hl.window_rule({ match = { class = ".*" }, opacity = "0.97 0.97" })
hl.window_rule({ match = { class = "^(zoom|vlc|mpv|imv|org\\.kde\\.kdenlive|com\\.obsproject\\.Studio|steam_app.*|gamescope)$" }, opacity = "1 1" })

-- The backdrop behind a GUI popup (gtkkit.py, System Settings → Appearance → Panels): a window over
-- the whole screen that draws it blurred itself (Hyprland's blur is one strength for everything), so
-- no blur of its own; its bar strip is see-through. Focusable: with follow_mouse = 2 hovering doesn't
-- take the focus, and a click must land on it (it closes the popup). Not tagged floating-window
-- (that one centres and sizes the panels).
hl.window_rule({
    match       = { class = "^(panels\\..*)$", title = "^(panels-backdrop)$" },
    float       = true,
    size        = { "monitor_w", "monitor_h" },
    move        = { 0, 0 },
    border_size = 0,
    no_shadow   = true,
    animation   = "popin 100%",   -- fades in and out with the popup, no zoom
    no_blur     = true,
    opacity     = "1.0 override 1.0 override",
})

-- Blur behind the bar, launcher and notifications
hl.layer_rule({
  name = "omarchy-stack",
  match = { namespace = "^(waybar|walker|notifications|swayosd)$" },
  blur = true,
  ignore_alpha = 0.5,
})
hl.layer_rule({ name = "walker-noanim", match = { namespace = "^walker$" }, no_anim = true })
