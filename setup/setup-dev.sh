#!/usr/bin/env bash
# Set up the dev toolchains that don't come straight from pacman. Idempotent: safe to re-run.
# Run by `setup/install.sh --packages` after the packages are installed; can also be run on its own.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # the repo root; this script lives in setup/
say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }

head_ "rust (rustup, stable)"
if command -v rustup >/dev/null; then
  rustup default stable
  rustup component add rust-analyzer clippy rustfmt
else
  say "rustup missing - install packages first"
fi

head_ "node LTS, pnpm, bun (mise)"
if command -v mise >/dev/null; then
  mise trust --quiet "$REPO/config/mise/config.toml" 2>/dev/null || true
  mise install --yes
else
  say "mise missing - install packages first"
fi

head_ "docker"
if command -v docker >/dev/null; then
  systemctl is-enabled --quiet docker.socket || sudo systemctl enable --now docker.socket
  if id -nG "$USER" | grep -qw docker; then
    say "ok       $USER is in the docker group"
  else
    sudo usermod -aG docker "$USER"
    say "added $USER to the docker group - log out and back in once to use docker without sudo"
  fi
else
  say "docker missing - install packages first"
fi

head_ "vs code extensions"
if command -v code >/dev/null; then
  installed="$(code --list-extensions)"
  while read -r ext; do
    [[ -z "$ext" || "$ext" == \#* ]] && continue
    if grep -qix "$ext" <<<"$installed"; then say "ok       $ext"
    elif code --install-extension "$ext" --force >/dev/null 2>&1; then say "installed $ext"
    else say "FAILED   $ext (network?) - re-run setup/setup-dev.sh"
    fi
  done < "$REPO/setup/vscode-extensions.txt"
else
  say "code missing - install setup/packages-aur.txt first"
fi
