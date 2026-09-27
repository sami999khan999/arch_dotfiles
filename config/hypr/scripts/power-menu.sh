#!/usr/bin/env bash
# Omarchy-style system menu via walker's dmenu mode
choice=$(printf "%s\n" "  Lock" "󰤄  Suspend" "󰍃  Log out" "󰜉  Restart" "󰐥  Shutdown" \
  | walker --dmenu --placeholder "System…") || exit 0

case "$choice" in
  *Lock)      loginctl lock-session ;;
  *Suspend)   systemctl suspend ;;
  *"Log out") uwsm stop ;;
  *Restart)   systemctl reboot ;;
  *Shutdown)  systemctl poweroff ;;
esac
