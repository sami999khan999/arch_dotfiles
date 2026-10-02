#!/usr/bin/env bash
# Steps that require root. Idempotent, and safe on any PC: the data-drive line (this PC's HDD) is
# only added where that drive is attached.
# Run with: pkexec /home/sami/dotfiles/system/root-setup.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 1. Chrome: block on-device AI model re-download"
install -Dm644 "$REPO/system/disable_ondevice_ai.json" /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
echo "   wrote /etc/opt/chrome/policies/managed/disable_ondevice_ai.json"

echo "== 2. Mount the data HDD at /mnt/data at every boot (system/fstab-data)"
uuid=$(grep -o '^UUID=[^ ]*' "$REPO/system/fstab-data")
if [ ! -e "/dev/disk/by-uuid/${uuid#UUID=}" ]; then
  echo "   that drive isn't in this PC: skipped"
  echo; echo "Done. /dev/sda was not touched."
  exit 0
elif grep -q "^$uuid[[:space:]]" /etc/fstab; then
  echo "   already in /etc/fstab"
else
  cp /etc/fstab /etc/fstab.bak
  { echo; cat "$REPO/system/fstab-data"; } >> /etc/fstab
  systemctl daemon-reload
  echo "   added to /etc/fstab (old one saved as /etc/fstab.bak)"
fi
mkdir -p /mnt/data
mountpoint -q /mnt/data || mount /mnt/data
findmnt /mnt/data || true

echo
echo "Done. /dev/sda was not touched."
