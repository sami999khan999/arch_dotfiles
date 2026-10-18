#!/usr/bin/env bash
# Steps that require root. Idempotent, and safe on any PC: the data-drive line (this PC's HDD) is
# only added where that drive is attached.
# Run with: pkexec /home/sami/dotfiles/system/root-setup.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 1. Chrome: block on-device AI model re-download"
install -Dm644 "$REPO/system/disable_ondevice_ai.json" /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
echo "   wrote /etc/opt/chrome/policies/managed/disable_ondevice_ai.json"

echo "== 2. Firewall: LocalSend (lsend) from this network only"
if command -v ufw >/dev/null; then
  for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12; do
    ufw allow proto tcp from "$net" to any port 53317 comment LocalSend >/dev/null
    ufw allow proto udp from "$net" to any port 53317 comment LocalSend >/dev/null
  done
  echo "   port 53317 (tcp + udp) open to private networks"
else
  echo "   no ufw: skipped"
fi

echo "== 3. Snapshots readable by you (the Updates panel lists them): snapper's root config"
if [ -f /etc/snapper/configs/root ]; then
  owner=$(stat -c %U "$REPO")   # the dotfiles' owner, not root (pkexec / sudo)
  snapper -c root set-config "ALLOW_USERS=$owner" "SYNC_ACL=yes"
  echo "   $owner may list root's snapshots"
else
  echo "   no snapper root config: skipped"
fi

echo "== 4. Docker: containers unreachable from the network, an on/off switch, Dokploy off at boot"
if command -v docker >/dev/null; then
  owner=$(stat -c %U "$REPO")
  install -Dm755 "$REPO/system/docker/docker-firewall.sh" /usr/local/lib/dotfiles/docker-firewall
  install -Dm644 "$REPO/system/docker/50-docker-switch.rules" /etc/polkit-1/rules.d/50-docker-switch.rules
  mkdir -p /etc/systemd/system/docker.service.d
  sed -e "s|@USER@|$owner|g" -e "s|@HOME@|$(getent passwd "$owner" | cut -d: -f6)|g" \
    "$REPO/system/docker/docker.service.conf" > /etc/systemd/system/docker.service.d/dotfiles.conf
  systemctl daemon-reload
  # now too, not only from Docker's next start
  if systemctl -q is-active docker.service; then /usr/local/lib/dotfiles/docker-firewall; fi
  echo "   new connections from the network into containers dropped (DOCKER-USER)"
  echo "   $owner (group docker) may start / stop Docker without a password: the Docker panel's switch"
  echo "   docker.service runs 'dokployctl down' as it starts and stops"
else
  echo "   no docker: skipped"
fi

echo "== 5. Mount the data HDD at /mnt/data at every boot (system/fstab-data)"
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
