#!/usr/bin/env bash
# Dokploy (deploys apps from Git into Docker, a dashboard on http://localhost:23300): its official
# installer, once, then what makes it fit here. Idempotent: an installed Dokploy is left as it is.
# Run with: pkexec /home/sami/dotfiles/system/dokploy-setup.sh   (after root-setup.sh: its Docker step
# keeps Dokploy off at boot and its ports closed to the network)
# On / off afterwards: the Docker panel's Dokploy tab, or `dokployctl up` / `down`.
set -euo pipefail

echo "== 1. Dokploy"
if docker service inspect dokploy >/dev/null 2>&1; then
  echo "   already installed: $(docker service inspect -f '{{.Spec.TaskTemplate.ContainerSpec.Image}}' dokploy | cut -d@ -f1)"
else
  # Swarm mode, its network, Postgres, the dashboard and Traefik on 80 / 443 (they must be free)
  curl -sSL https://dokploy.com/install.sh | sh
fi

echo "== 2. Tidy for an on / off service"
# a stopped service leaves one old container, not five per service per switch
docker swarm update --task-history-limit 1 >/dev/null
# Traefik's 'always' would start it again with Docker even after `dokployctl down`
docker update --restart unless-stopped dokploy-traefik >/dev/null
echo "   Swarm keeps 1 old task, Traefik restarts unless stopped"

echo "== 3. The dashboard on port 23300"
# the installer publishes it on 3000, which dev servers want (Next.js, Express…); Dokploy's own updates
# change only the image, so the port stays moved
if docker service inspect -f '{{range .Endpoint.Spec.Ports}}{{.PublishedPort}} {{end}}' dokploy | grep -qw 3000; then
  # two updates: a remove and an add in one call kept 3000 (the remove matches only with mode=host, and
  # then takes every port to the dashboard's 3000 inside)
  docker service update --detach=false --quiet --publish-rm published=3000,target=3000,mode=host dokploy >/dev/null
  docker service update --detach=false --quiet --publish-add published=23300,target=3000,mode=host dokploy >/dev/null
fi
echo "   http://localhost:23300"

echo
echo "Done. Open http://localhost:23300 (first time: make the admin account, then Profile → 2FA)."
echo "It stays off after a reboot: start it from the Docker panel's Dokploy tab or with: dokployctl up"
