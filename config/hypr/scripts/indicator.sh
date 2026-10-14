#!/usr/bin/env bash
# Waybar status indicators.   indicator.sh idle   (notifications: notifications.py bar)
case "$1" in
  idle)
    pgrep -x hypridle >/dev/null \
      && echo '{"text": ""}' \
      || echo '{"text": "󱫖", "tooltip": "<b>Idle lock off</b>\\n<span alpha='55%'>Click: turn it back on</span>", "class": "active"}' ;;
esac
