-- Auto-start config: the classic Omarchy stack (waybar, walker, mako, swayosd, hypridle, swaybg)
-- if you dont use UWSM add your auto start programs here, otherwise use XDG autostart https://wiki.archlinux.org/title/XDG_Autostart

local launch = "uwsm app -- "

hl.on("hyprland.start", function ()
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")
    hl.exec_cmd("xhost +SI:localuser:root")

    -- the packaged user service (Restart=on-failure): waybar comes back by itself if it crashes
    hl.exec_cmd("systemctl --user start waybar.service")
    hl.exec_cmd(launch .. "mako")
    hl.exec_cmd(launch .. "swayosd-server")
    hl.exec_cmd(launch .. "hypridle")
    hl.exec_cmd(launch .. "elephant")
    hl.exec_cmd(launch .. "walker --gapplication-service")
    hl.exec_cmd("systemctl --user start hyprpolkitagent")
    hl.exec_cmd("~/.config/hypr/scripts/wallpaper.sh restore")
    hl.exec_cmd("~/.config/hypr/scripts/toggle.sh nightlight auto quiet") -- on now if inside its schedule (Settings)
    -- gsettings (theme, cursor, fonts, clock) back in line with the repo: a new PC, or a change synced from the other one
    hl.exec_cmd("python3 ~/.local/lib/panels/settingslib.py restore")
    hl.exec_cmd("~/.local/bin/wsgroups launch 10 --background") -- Control Center on workspace 10 (Super + Ctrl + 0)
end)
