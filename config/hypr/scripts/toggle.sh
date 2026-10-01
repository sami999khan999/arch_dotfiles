#!/usr/bin/env bash
# Omarchy-style toggles.   toggle.sh idle | nightlight | notifications
# an indicator came or went: the visualizer (waybar) makes room. By pid: a name match would also
# signal an editor that has visualizer.py open
poke_visualizer() {
  kill -USR1 "$(cat "$XDG_RUNTIME_DIR/waybar-visualizer.pid" 2>/dev/null)" 2>/dev/null
}

case "$1" in
  idle)
    if pkill -x hypridle; then
      notify-send -u low "󰅶  Stay awake enabled"
    else
      setsid uwsm app -- hypridle >/dev/null 2>&1 &
      # it starts in the background: wait for it, or the indicator (and the visualizer's count of
      # it) would still see it missing and stay on for up to 10 s
      for _ in $(seq 20); do pgrep -x hypridle >/dev/null && break; sleep 0.1; done
      notify-send -u low "󰾪  Stay awake disabled"
    fi
    pkill -RTMIN+9 waybar
    poke_visualizer ;;
  nightlight)
    if pkill -x hyprsunset; then
      notify-send -u low "  Nightlight off"
    else
      setsid hyprsunset -t 4000 >/dev/null 2>&1 &
      notify-send -u low "  Nightlight on"
    fi ;;
  notifications)
    if makoctl mode | grep -qx do-not-disturb; then
      makoctl mode -r do-not-disturb
      notify-send -u low "󰂚  Notifications on"
    else
      notify-send -u low "󰂛  Notifications silenced"
      makoctl mode -a do-not-disturb
    fi
    pkill -RTMIN+10 waybar
    poke_visualizer ;;
esac
