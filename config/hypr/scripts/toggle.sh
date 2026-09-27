#!/usr/bin/env bash
# Omarchy-style toggles.   toggle.sh idle | nightlight | notifications
case "$1" in
  idle)
    if pkill -x hypridle; then
      notify-send -u low "󰅶  Stay awake enabled"
    else
      setsid uwsm app -- hypridle >/dev/null 2>&1 &
      notify-send -u low "󰾪  Stay awake disabled"
    fi
    pkill -RTMIN+9 waybar ;;
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
    pkill -RTMIN+10 waybar ;;
esac
