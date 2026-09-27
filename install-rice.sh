#!/usr/bin/env bash
# Install the packages for the Omarchy-style rice (omarchy-rice branch).
set -euo pipefail

sudo pacman -S --needed \
  waybar walker mako swayosd hyprlock hypridle hyprpolkitagent hyprsunset hyprpicker \
  swaybg starship ttf-jetbrains-mono-nerd grim slurp satty wl-clipboard jq libnotify \
  pamixer playerctl brightnessctl bluetui wiremix

# walker's backend and its providers (AUR)
paru -S --needed \
  elephant elephant-desktopapplications elephant-calc elephant-clipboard \
  elephant-symbols elephant-files elephant-websearch elephant-providerlist

fc-cache -f >/dev/null
echo
echo "Done. Log out and back in (or reboot) to start the new desktop."
