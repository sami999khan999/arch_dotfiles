#!/usr/bin/env bash
# Waybar status indicators.   indicator.sh idle   (notifications: notifications.py bar)
case "$1" in
  idle)
    pgrep -x hypridle >/dev/null \
      && echo '{"text": ""}' \
      || echo '{"text": "󱫖", "tooltip": "Idle lock disabled", "class": "active"}' ;;
esac
