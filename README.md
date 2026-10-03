<div align="center">

# dotfiles

**A Tokyo Night desktop on CachyOS + Hyprland, built around coding agents.**

Riced after [Omarchy](https://github.com/basecamp/omarchy), with tools of its own: a tmux workspace
for coding agents, GTK panels in one look, workspace groups and a live code backup.
One repo, every PC the same.

[![CachyOS](https://img.shields.io/badge/CachyOS-1a1b26?style=for-the-badge&logo=archlinux&logoColor=6b8fe0)](https://cachyos.org) [![Hyprland](https://img.shields.io/badge/Hyprland-1a1b26?style=for-the-badge&logo=hyprland&logoColor=6b8fe0)](https://hyprland.org) [![tmux](https://img.shields.io/badge/tmux-1a1b26?style=for-the-badge&logo=tmux&logoColor=9ece6a)](https://github.com/tmux/tmux) [![GTK 4](https://img.shields.io/badge/GTK_4-1a1b26?style=for-the-badge&logo=gtk&logoColor=e0af68)](https://gtk.org) [![Tokyo Night](https://img.shields.io/badge/theme-Tokyo_Night-6b8fe0?style=for-the-badge&labelColor=1a1b26)](https://github.com/folke/tokyonight.nvim)

[**agentmux**](#agentmux) · [**Panels**](#panels) · [**Install**](#install) · [**Keys**](#keys) · [**Full manual**](MANUAL.md)

<br>

<img src="docs/screenshots/agentmux.png" alt="agentmux: projects, threads, an agent and its terminals side by side" width="100%">

<img src="docs/screenshots/bar.png" alt="The top bar" width="100%">

</div>

<br>

## agentmux

**`Super + A`** opens a workspace for coding agents, laid out by project: **Projects** · **Threads** ·
**the agent** · **terminals**.

- **Any agent, any number of threads:** Claude Code, Codex, opencode and Antigravity (agy), side by side
  in one project.
- **Live in two places:** every thread is a tmux session, shared with the kitty beside VS Code. One
  agent, two views, nothing copied.
- **Knows what each agent is doing:** *needs you*, *working*, *done*, *error*, from the agents' own
  hooks; a notification when one needs you or finishes, gone once you've answered.
- **Picks up where you left off:** after a restart every thread comes back on its own conversation.
- **Terminals like VS Code's:** each one full height, a thin list at the side, split as you like.
- **Any panel full width** with `Ctrl+Alt+Z`, every key remappable in its settings.

[Everything about agentmux →](MANUAL.md#agents-agentmux)

<br>

## Panels

Every panel is a GTK 4 window in the same look: an icon and title with a blue underline, blue
section headings, green / amber / red levels and the keys at the bottom. A key or a click on the
bar opens one; the same key or `Esc` closes it.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/controlcenter.png" alt="Control Center"></td>
    <td width="50%"><img src="docs/screenshots/docker.png" alt="Docker panel"></td>
  </tr>
  <tr>
    <td><b>Control Center</b> · workspace 10<br><sub>Workspaces, system, shortcuts, audio and network at a glance.</sub></td>
    <td><b>Docker</b> · workspace 9<br><sub>Containers by compose project, images, volumes, networks: start, stop, logs, exec.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/system.png" alt="System panel"></td>
    <td><img src="docs/screenshots/settings.png" alt="Settings panel"></td>
  </tr>
  <tr>
    <td><b>System</b> · <code>Super + Ctrl + T</code><br><sub>CPU, memory and GPU with their history, processes, storage.</sub></td>
    <td><b>Settings</b> · <code>Super + I</code><br><sub>Every setting in one place, searchable.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/audio.png" alt="Audio panel"></td>
    <td><img src="docs/screenshots/network.png" alt="Network panel"></td>
  </tr>
  <tr>
    <td><b>Audio</b> · <code>Super + Ctrl + A</code><br><sub>Outputs and inputs, volume, mute, the default device.</sub></td>
    <td><b>Network</b> · <code>Super + Ctrl + W</code><br><sub>The connection, live traffic, addresses.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/workspaces.png" alt="Workspaces panel"></td>
    <td><img src="docs/screenshots/notifications.png" alt="Notifications panel"></td>
  </tr>
  <tr>
    <td><b>Workspaces</b> · <code>Super + Ctrl + G</code><br><sub>Which app each workspace is for; launch it, go there.</sub></td>
    <td><b>Notifications</b> · the bell, <code>Super + .</code><br><sub>History by day, do not disturb, what may notify.</sub></td>
  </tr>
</table>

[All the panels →](MANUAL.md#panels)

<br>

## Also inside

<table>
  <tr>
    <td width="33%" valign="top"><b>Workspace groups</b><br><sub>One workspace per kind of app: Agents, Code, Web, Terminal, Files, Discord, Docker, Control Center. One key opens or focuses each.</sub><br><a href="MANUAL.md#workspace-groups">More →</a></td>
    <td width="33%" valign="top"><b>codesync</b><br><sub>Backs the code folder up from the SSD to the HDD as you work, with old versions kept in a trash.</sub><br><a href="MANUAL.md#code-backup-codesync">More →</a></td>
    <td width="33%" valign="top"><b>Every PC the same</b><br><sub>Packages, toolchains, coding agents and settings come from the repo; <code>dotsync</code> keeps the PCs in step.</sub><br><a href="MANUAL.md#syncing-between-pcs">More →</a></td>
  </tr>
</table>

<br>

## Install

On a fresh CachyOS with Hyprland:

```bash
git clone https://github.com/sami999khan999/arch_dotfiles.git ~/dotfiles
cd ~/dotfiles
setup/install.sh --packages   # packages, toolchains, coding agents, config links
```

Log out and back in. The few one-time steps that need root (login screen, boot splash, swap) are
listed at the end of the install: [Bootstrap a new machine](MANUAL.md#bootstrap-a-new-machine).

<br>

## Keys

| Key | Does |
|---|---|
| `Super + A` | agentmux |
| `Super + I` | Settings |
| `Super + K` | every shortcut, searchable |
| `Super + .` | notifications |
| `Super + Ctrl + T` | System |
| `Alt + 1…0` | the Nth window of the workspace |

The rest: [Shortcut list](MANUAL.md#shortcut-list), or `Super + K` on the desktop.

<br>

<div align="center">
<sub>Tokyo Night colours, square corners, JetBrains Mono everywhere · <a href="MANUAL.md">Read the full manual</a></sub>
</div>
