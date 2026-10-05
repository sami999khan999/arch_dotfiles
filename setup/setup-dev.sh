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

head_ "coding agents (agentmux harnesses)"
# opencode and Codex (openai-codex) come from packages.txt. Claude Code and Antigravity (agy) have
# no package: their official installers put one self-updating binary in ~/.local/bin.
install_agent() {   # install_agent <command> <installer url>
  if command -v "$1" >/dev/null || [[ -x "$HOME/.local/bin/$1" ]]; then
    say "ok       $1"
  elif curl -fsSL "$2" | bash >/dev/null; then
    say "installed $1"
  else
    say "FAILED   $1 (network?) - re-run setup/setup-dev.sh"
  fi
}
install_agent claude https://claude.ai/install.sh
install_agent agy    https://antigravity.google/cli/install.sh
for cmd in opencode codex; do
  command -v "$cmd" >/dev/null && say "ok       $cmd" || say "$cmd missing - install packages first"
done
# agentmux hears from the agents through their own hooks (Claude Code, Codex, agy configs live
# outside this repo): install them, merged into whatever is there
[[ -x "$HOME/.local/bin/agentmux" ]] && "$HOME/.local/bin/agentmux" hooks | sed 's/^/  /'

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

head_ "vs code keyring"
# VS Code picks its secret store by desktop; Hyprland isn't one it knows, so it found no keyring and
# asked to use "weaker encryption" on every start. Point it at gnome-keyring (Secret Service) instead.
argv="$HOME/.vscode/argv.json"
if grep -q '"password-store"' "$argv" 2>/dev/null; then
  say "ok       $(grep -o '"password-store"[^,]*' "$argv")"
elif [[ -s "$argv" ]]; then   # VS Code's own file (JSONC, crash reporter id): add the key after its "{"
  sed -i '0,/{/s//{\n\t"password-store": "gnome-libsecret",/' "$argv"
  say "set      password-store gnome-libsecret in $argv"
else
  mkdir -p "$HOME/.vscode"
  printf '{\n\t"password-store": "gnome-libsecret"\n}\n' > "$argv"
  say "wrote    $argv (password-store gnome-libsecret)"
fi
