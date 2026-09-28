#!/usr/bin/env bash
# tui.sh <cmd> — toggle a TUI in a floating terminal: close it if already open, else open it
# The title is set up front so window rules can match it (see windowrules.lua)
source "$(dirname "$0")/newt-theme.sh"
pkill -f "^(\S*/)?kitty --class=TUI.float --title=.* -e $1\$" ||
    exec uwsm app -- kitty --class=TUI.float --title="$(basename "$1")" -e "$1"
