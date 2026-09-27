#!/usr/bin/env bash
# Jump to a window by class, cycle its windows on repeat presses, or launch it.
#   focus-or-launch.sh <class-regex> <command...>
# Driven by ~/.config/hypr/apps.conf.
set -euo pipefail

class="$1"; shift

# windows of this class, most recently used first
mapfile -t wins < <(hyprctl clients -j | jq -r --arg re "^(${class})$" \
  '[.[] | select(.class | test($re; "i")) | select(.workspace.id > 0)]
   | sort_by(.focusHistoryID) | .[].address')

if (( ${#wins[@]} == 0 )); then
  exec uwsm app -- "$@"
fi

active=$(hyprctl activewindow -j | jq -r '.address // empty')
target="${wins[0]}"
if [[ "$active" == "$target" && ${#wins[@]} -gt 1 ]]; then
  # already on this app: go to its least recently used window, so repeats cycle through all
  target="${wins[-1]}"
fi

hyprctl dispatch "hl.dsp.focus({ window = \"address:$target\" })" >/dev/null
