#!/usr/bin/env bash
# panel.sh <cmd...> — run a tool in a Control Center pane and restart it when it quits
source "$(dirname "$0")/newt-theme.sh"
export CONTROL_CENTER=1  # panelkit.py: the card has its own padding, draw edge to edge
while :; do "$@"; sleep 0.5; done
