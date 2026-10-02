#!/usr/bin/env bash
# A 16 GB swapfile on the SSD, after zram, for THIS PC. Idempotent.
# Why: zram is the only swap and it lives in RAM, so RAM + swap both pass 90 % quickly and
# systemd-oomd kills the biggest app (VS Code). The swapfile only takes what overflows zram
# (priority 10 vs zram's 100); oomd stays on as the last resort.
# The swapfile sits in its own subvolume @swap: snapper snapshots @, and a snapshot of a
# subvolume holding an active swapfile fails.
# Run with: pkexec /home/sami/dotfiles/system/swap-setup.sh
set -euo pipefail

# The root SSD's btrfs, by UUID, never /dev/sdX: disk names can change, and sda is Windows.
UUID=3abf5105-0076-43bc-8721-2c73a08b0adf
SIZE=16g
PRIO=10  # below zram (100): zram fills first
MOUNT_LINE="UUID=$UUID  /swap  btrfs  subvol=/@swap,defaults,noatime  0 0"
SWAP_LINE="/swap/swapfile  none  swap  defaults,pri=$PRIO  0 0"

[ -e "/dev/disk/by-uuid/$UUID" ] || { echo "btrfs $UUID not found, stopping"; exit 1; }

echo "== 1. Back up /etc/fstab"
if [ -e /etc/fstab.bak-swap ]; then
  echo "   /etc/fstab.bak-swap already there"
else
  cp /etc/fstab /etc/fstab.bak-swap
  echo "   saved as /etc/fstab.bak-swap"
fi

echo "== 2. Subvolume @swap"
top=$(mktemp -d)
mount -o subvolid=5 "/dev/disk/by-uuid/$UUID" "$top"
trap 'umount "$top" 2>/dev/null; rmdir "$top" 2>/dev/null' EXIT
if [ -d "$top/@swap" ]; then
  echo "   already exists"
else
  btrfs subvolume create "$top/@swap"
fi
umount "$top"; rmdir "$top"; trap - EXIT

echo "== 3. Mount it at /swap at every boot"
mkdir -p /swap
if grep -qE '^[^#]*[[:space:]]/swap[[:space:]]' /etc/fstab; then
  echo "   already in /etc/fstab"
else
  { echo; echo "# Swapfile subvolume (dotfiles system/swap-setup.sh)"; echo "$MOUNT_LINE"; } >> /etc/fstab
  systemctl daemon-reload
  echo "   added to /etc/fstab"
fi
mountpoint -q /swap || mount /swap

echo "== 4. The $SIZE swapfile"
if [ -e /swap/swapfile ]; then
  echo "   /swap/swapfile already exists"
else
  # mkswapfile makes it NOCOW and uncompressed, as btrfs needs for swap
  btrfs filesystem mkswapfile --size "$SIZE" --uuid clear /swap/swapfile
fi
if grep -qE '^[^#]*/swap/swapfile[[:space:]]' /etc/fstab; then
  echo "   already in /etc/fstab"
else
  echo "$SWAP_LINE" >> /etc/fstab
  systemctl daemon-reload
  echo "   added to /etc/fstab"
fi
swapon --show=NAME --noheadings | grep -qx /swap/swapfile || swapon --priority "$PRIO" /swap/swapfile  # swapon alone ignores fstab's pri=

echo "== 5. Check"
findmnt --verify
swapon --show

echo
echo "Done. /dev/sda was not touched."
