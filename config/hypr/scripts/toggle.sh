#!/usr/bin/env bash
# Omarchy-style toggles.   toggle.sh idle | nightlight [on|off|auto] [quiet] | notifications
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
    # toggle.sh nightlight [toggle|on|off|auto] [quiet]. Temperature and schedule come from the
    # Settings panel (hypr/settings.json); auto = on inside the schedule's window, off outside it
    # (its timers and the login autostart call it), and nothing at all without a schedule.
    settings="$HOME/.config/hypr/settings.json"
    temp=$(jq -r '.nightlight.temp // 4000' "$settings" 2>/dev/null)
    want="${2:-toggle}"
    if [[ $want == auto ]]; then
      [[ $(jq -r '.nightlight.schedule // false' "$settings" 2>/dev/null) == true ]] || exit 0
      start=$(jq -r '.nightlight.start // "20:00"' "$settings"); end=$(jq -r '.nightlight.end // "07:00"' "$settings")
      now=$(date +%H:%M)
      if [[ $start < $end ]]; then [[ ! $now < $start && $now < $end ]] && want=on || want=off
      else [[ ! $now < $start || $now < $end ]] && want=on || want=off; fi
    fi
    if [[ $want == toggle ]]; then pgrep -x hyprsunset >/dev/null && want=off || want=on; fi
    if [[ $want == on ]]; then
      pgrep -x hyprsunset >/dev/null || { setsid hyprsunset -t "${temp:-4000}" >/dev/null 2>&1 & }
      [[ $3 == quiet ]] || notify-send -u low "  Nightlight on"
    else
      pkill -x hyprsunset
      [[ $3 == quiet ]] || notify-send -u low "  Nightlight off"
    fi ;;
  notifications)   # do not disturb (the bell beside the clock shows it)
    ~/.config/hypr/scripts/notifications.py dnd toggle
    poke_visualizer ;;
esac
