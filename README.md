# dotfiles

CachyOS + Hyprland desktop, riced after [Omarchy](https://github.com/basecamp/omarchy) (waybar,
walker, mako, swayosd, hyprlock, hypridle, swaybg) in Tokyo Night, with tools of its own: a tmux
workspace for coding agents, GTK panels, workspace groups and a code backup service. Shared between
PCs through this repo: a fresh install plus one script gives the same desktop.

**The full manual is [MANUAL.md](MANUAL.md):** every feature, key and file, and how it's put together.

![agentmux: projects, threads, an agent and its terminals side by side](docs/screenshots/agentmux.png)

**agentmux** (`Super + A`): coding agents (Claude Code, Codex, opencode, agy) by project. Each thread
is a live tmux session, shared with the kitty beside VS Code; terminals at the right; threads come
back on their own conversation after a restart. [More](MANUAL.md#agents-agentmux)

![The bar](docs/screenshots/bar.png)

The bar: workspace buttons, an audio visualizer while sound plays, the window's title, controls,
stats, the clock and the notification bell.

| | |
|---|---|
| ![Control Center](docs/screenshots/controlcenter.png) | ![Docker panel](docs/screenshots/docker.png) |
| **Control Center** (workspace 10): workspaces, system, shortcuts, audio, network at a glance. [More](MANUAL.md#control-center-workspace-10) | **Docker** (workspace 9): containers by compose project, images, volumes, networks; start, stop, logs, exec. [More](MANUAL.md#docker-panel-workspace-9) |
| ![System panel](docs/screenshots/system.png) | ![Settings](docs/screenshots/settings.png) |
| **System** (`Super + Ctrl + T`): CPU, memory and GPU with their history, processes, storage. [More](MANUAL.md#panels) | **Settings** (`Super + I`): every setting in one place, searchable. [More](MANUAL.md#settings-panel) |
| ![Audio panel](docs/screenshots/audio.png) | ![Network panel](docs/screenshots/network.png) |
| **Audio** (`Super + Ctrl + A`): outputs and inputs, volume, mute, the default device. [More](MANUAL.md#panels) | **Network** (`Super + Ctrl + W`): the connection, live traffic, addresses. [More](MANUAL.md#panels) |
| ![Workspaces panel](docs/screenshots/workspaces.png) | ![Notifications](docs/screenshots/notifications.png) |
| **Workspaces** (`Super + Ctrl + G`): which app each workspace is for, launch and go. [More](MANUAL.md#workspace-groups) | **Notifications** (the bell, `Super + .`): history by day, do not disturb. [More](MANUAL.md#notifications) |

## What's in it

- **Workspace groups:** one workspace per kind of app (Agents, Code, Web, Terminal, Files, Discord,
  Docker, Control Center), each opened or focused with one key. [More](MANUAL.md#workspace-groups)
- **agentmux:** the agents workspace above, with notifications when an agent needs you or finishes.
- **Panels:** GTK windows in one look: System, Audio, Network, Shortcuts, Settings, Notifications,
  Docker, Code Sync. [More](MANUAL.md#panels)
- **codesync:** backs up the code folder from the SSD to the HDD as you work. [More](MANUAL.md#code-backup-codesync)
- **Every PC the same:** packages, toolchains, coding agents and settings all come from the repo;
  `dotsync` keeps the PCs in step. [More](MANUAL.md#syncing-between-pcs)

## Install

On a fresh CachyOS with Hyprland:

```bash
git clone https://github.com/sami999khan999/arch_dotfiles.git ~/dotfiles
cd ~/dotfiles
setup/install.sh --packages   # packages, toolchains, coding agents, config links
```

Log out and back in. A few one-time steps need root (login screen, boot splash, swap) and are
listed at the end of the install; see [Bootstrap a new machine](MANUAL.md#bootstrap-a-new-machine).

## Handy keys

| Key | Does |
|---|---|
| `Super + A` | agentmux |
| `Super + I` | Settings |
| `Super + K` | every shortcut, searchable |
| `Super + .` | notifications |
| `Alt + 1…0` | the Nth window of the workspace |

All of them: [Shortcut list](MANUAL.md#shortcut-list), or `Super + K` on the desktop.
