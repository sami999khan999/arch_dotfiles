#!/usr/bin/env bash
# docker-firewall.sh — no container can be reached from the network, only from this PC. Docker opens
# a published port (5432, 3000, 80…) to everyone with its own iptables rules, ahead of ufw, so ufw
# can't close it: this drops new connections forwarded *into* a Docker network, in DOCKER-USER, the
# chain Docker leaves for this. Replies and what containers start themselves (the internet, Tailscale's
# tunnel out) pass; this PC reaches its ports through localhost, which isn't forwarded.
# Installed root-owned to /usr/local/lib/dotfiles/ by system/root-setup.sh; docker.service runs it
# each time it starts (the rules don't survive a reboot). Idempotent.
set -u

for ipt in iptables ip6tables; do
  command -v "$ipt" >/dev/null || continue
  "$ipt" -N DOTFILES-DOCKER 2>/dev/null || "$ipt" -F DOTFILES-DOCKER
  "$ipt" -A DOTFILES-DOCKER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  for net in docker0 br-+ docker_gwbridge; do
    "$ipt" -A DOTFILES-DOCKER -i "$net" -j RETURN   # from a container: leave it to Docker
  done
  for net in docker0 br-+ docker_gwbridge; do
    "$ipt" -A DOTFILES-DOCKER -o "$net" -j DROP     # from the network, into a container
  done
  "$ipt" -N DOCKER-USER 2>/dev/null || true
  "$ipt" -C DOCKER-USER -j DOTFILES-DOCKER 2>/dev/null || "$ipt" -I DOCKER-USER -j DOTFILES-DOCKER
done
