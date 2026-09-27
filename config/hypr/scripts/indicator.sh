#!/usr/bin/env bash
# Waybar status indicators.   indicator.sh idle | notifications
case "$1" in
  idle)
    pgrep -x hypridle >/dev/null \
      && echo '{"text": ""}' \
      || echo '{"text": "󱫖", "tooltip": "Idle lock disabled", "class": "active"}' ;;
  notifications)
    makoctl mode | grep -qx do-not-disturb \
      && echo '{"text": "󰂛", "tooltip": "Notifications silenced", "class": "active"}' \
      || echo '{"text": ""}' ;;
esac
