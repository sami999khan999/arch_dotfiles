# dotfiles: the manual

CachyOS + Hyprland desktop configuration, riced after [Omarchy](https://github.com/basecamp/omarchy)
(its classic v3 stack: waybar, walker, mako, swayosd, hyprlock, hypridle; awww for the wallpaper) in Tokyo Night.

This is the full manual: what everything does and how it's put together. For screenshots and a
short overview, see the [README](README.md).

## Bootstrap a new machine

Install CachyOS with Hyprland, then:

```bash
git clone https://github.com/sami999khan999/arch_dotfiles.git ~/dotfiles
cd ~/dotfiles
setup/install.sh --dry-run --packages   # preview, changes nothing
setup/install.sh --packages             # packages + /etc files + symlinks
```

Then log out and back in. Re-run `setup/install.sh` (no flag) any time to relink.

That gives the same packages (`setup/packages*.txt` hold everything installed by name on the
first PC), the same config, toolchains, coding agents and their agentmux hooks. The rest needs
root or a choice, once per PC (`install.sh` prints the list at the end):

```bash
pkexec ~/dotfiles/system/greeter/install.sh   # login screen: greetd + noctalia-greeter
pkexec ~/dotfiles/system/boot-splash.sh       # loading screen instead of boot text
pkexec ~/dotfiles/system/swap-setup.sh        # SSD swapfile after zram (the root btrfs)
pkexec ~/dotfiles/system/root-setup.sh        # Chrome policy; LocalSend through the firewall; snapshots listable; Docker closed to the network + its on/off switch; the data drive, where it's attached
pkexec ~/dotfiles/system/dokploy-setup.sh     # Dokploy (deploy dashboard, localhost:23300), off at boot
codesync enable                               # code backup (asks where the code is)
gh auth login && projects clone               # every project back into ~/code, with its branches
```

Then sign in: VS Code (Settings Sync brings its settings), Chrome, and the coding agents.
What isn't in git follows the repo by itself: at every login `settingslib.py restore` puts the
theme, cursor, fonts and clock format in gsettings back in line with `config/gtk-3.0/settings.ini`
and `config/hypr/settings.json` (also how a theme change made on one PC reaches the other).
Per-PC on purpose: monitor modes (`settings.local.json`), `config/codesync/machine.json`, time
zone, power profile, the GPU's own services. `install.sh --packages` leaves the NVIDIA driver
packages out on a PC without an NVIDIA card. The XDG Projects folder (file manager) is `~/code`.

## Layout

| Path | Links to | Contains |
|---|---|---|
| `config/` | `~/.config/` | settings — hypr, noctalia, gtk, qt, terminals, fish |
| `local/bin/` | `~/.local/bin/` | commands — `wsgroups`, `codesync`, `dotsync` |
| `local/lib/panels/` | `~/.local/lib/panels/` | the panels — GTK windows (`*gui.py`, shared look in `gtkkit.py`) and their older terminal versions (`panelkit.py`) |
| `local/share/` | `~/.local/share/` | assets — cursor theme, wallpapers, launcher overrides |
| `icons/` | `~/.icons/` | legacy cursor stub (`default/index.theme`) |
| `system/` | *manual, needs root* | `/etc` files — Chrome policy, this PC's data-drive fstab line |
| `system/greeter/` | *manual, needs root* | login screen (noctalia-greeter): Tokyo Night, the desktop's wallpaper blurred and darkened; unlocks the keyring at login (its password must be the login password) — `pkexec ~/dotfiles/system/greeter/install.sh` |
| `system/swap-setup.sh` | *manual, needs root* | 16 GB swapfile on the SSD (subvolume `@swap` at `/swap`, priority 10, after zram) so systemd-oomd doesn't kill VS Code when RAM fills — `pkexec ~/dotfiles/system/swap-setup.sh` |
| `system/boot-splash.sh` | *manual, needs root* | boot and shutdown show the loading screen (Plymouth) instead of text: adds `quiet …` to GRUB's kernel command line — `pkexec ~/dotfiles/system/boot-splash.sh` |
| `setup/` | — | `install.sh`, `setup-dev.sh`, package lists (`packages.txt`, `packages-aur.txt`), `vscode-extensions.txt` |
| `docs/screenshots/` | — | the screenshots in the README: `tokyo-night/` and `crimson/`, private text blurred |

`setup/install.sh` is idempotent and never deletes: anything real it finds in the way
is moved to `~/.config-backup-<timestamp>/` first.

## Settings vs assets

Two different things, two locations:

- **`config/`** names a theme  — e.g. `gtk-theme-name=adw-gtk3`
- **`local/share/`** holds the theme itself

A setting pointing at an absent asset fails silently. Most themes here come from
packages (`/usr/share/themes`, `/usr/share/icons`), so `setup/packages.txt` matters as
much as the files. The exception is **Bibata-Modern-Ice**, which was hand-placed
and belongs to no package — it is committed here because nothing else restores it.

## Colour themes: Tokyo Night, Crimson

`Super + Shift + T` (or Settings → Theme & fonts → Colour theme) opens the **theme picker**: every
theme's preview in a row, the chosen one large with the accent border, its name and colours below.
`←` `→` choose, `Enter` (or a click on the chosen one) applies it to the whole desktop at once: the
bar, window borders, terminals (kitty, alacritty, the starship prompt, btop, the greeting), the
launcher, notifications, the volume OSD, the lock screen, GTK and Qt apps, every panel and agentmux.
`theme next` from a terminal goes to the next one. The choice is `config/themes/active` (synced like
any other file: `dotsync` on the other PC applies it there).

| Theme | |
|---|---|
| Tokyo Night | the original: deep blue-black, a soft blue accent (`#6b8fe0`, a bit darker than stock) |
| Crimson | oxblood almost black (base `#140b0d`) with a quiet silver accent (`#a0918f`); red (`#e4656b`) only where something needs you (errors, alerts, critical levels), so the bloodborne wallpaper carries the red. Meets WCAG AA: every text colour is at least 4.5:1 on the background, a panel and a selected row (body text 13:1), dim icons 3:1, dark text on a red or silver button 6:1 and up |

**How it works.** Every file in this repo is written in Tokyo Night's colours, by hand, as before.
A theme is `config/themes/<id>/theme.json`: one colour for each of Tokyo Night's 32 roles (base,
overlay, line, muted, text, accent, alert, green…; the reference is `tokyo-night/theme.json`) and a
`preview.png` (a screenshot of the Control Center in it). `theme apply <id>` (`local/bin/theme`) makes
each app's file by swapping every Tokyo Night colour for the theme's colour of the same role, into
`config/themes/current/` (gitignored), and reloads what's running (Hyprland, waybar, mako, kitty,
walker, swayosd, agentmux, and the workspace panels: Control Center, Docker, Projects; one busy with
git, a restore cloning, is left to take it when it next starts). Each app reads that file after its own
colours, with one line:

| App | Its line | Made from |
|---|---|---|
| Hyprland borders | `colors.lua`: `dofile …/hypr.lua` | `modules/colors.lua` |
| waybar, walker, swayosd | `@import` at the end of their `style.css` | the statements that change |
| mako | `include=` | `mako/config` |
| hyprlock | `source =` | `hyprlock.conf` |
| kitty, alacritty | `include` / `import` | `themes/tokyo-night.*` |
| btop | `color_theme = "current"` (a link in `btop/themes/`) | `themes/tokyo-night.theme` |
| GTK 3 / 4 apps | `@import` in `gtk.css` | `tokyo-night.css`, `prompt.css` |
| Qt apps | `qt6ct.conf` → `themes/current/qt6ct.conf` | `qt6ct/colors/tokyo-night.conf` |
| agentmux (tmux) | `source-file -q` in `app.conf`, `agents.conf` | the same files |
| starship | `STARSHIP_CONFIG` (`fish/config.fish`) | `starship.toml` |

The GTK panels, agentmux's screens, the clock's tooltip and the greeting swap their colours
themselves at start (`local/lib/theme/themelib.py`: `recolor(text)`, `rgbf("#6b8fe0")` for cairo).
With Tokyo Night active none of this changes anything: its files are the originals' bytes or empty,
and `recolor` returns its text untouched. A missing file (a new PC before its first login) means
Tokyo Night; `hyprland.lua` runs `theme restore` first thing, which makes the files when they're
missing or out of date: only when one of the files they're made from (listed in `current/.sources`) is
newer than `current/.stamp`, a quick `find` on every other config reload.

**A new theme:** copy `config/themes/crimson/` to `config/themes/<id>/`, change the name and the 32
colours, `theme apply <id>`, then take its preview: `grim config/themes/<id>/preview.png` on the
Control Center's workspace. (No preview: the picker draws a sketch from the colours.) **A new colour
in a file:** use one of the roles (any Tokyo Night colour in `tokyo-night/theme.json`); a colour that
isn't one stays the same in every theme.

Not themed: Dolphin's own KDE colours (Files, the file manager, is) (`config/kdeglobals`: KDE has no include, and Settings writes
that file), the login screen (`system/greeter`, installed as root), the polkit password prompt
(`hypr/hyprtoolkit.conf`), lazydocker, wiremix, and VS Code (Settings Sync).

**The audio visualizer** fills the bar's gap (between the workspace buttons and the right side) while
something plays (cava), and hides when it's silent; click it to play / pause. `Super + Alt + B` (or a
right click on it) switches its style, and the choice is kept:
- **bars**: bars with a little space between them
- **solid**: the same bars side by side, one unbroken strip
- **mirror**: bars mirrored, the bass in the middle and the treble out at both ends
- **dots**: braille dots, twice as fine as the bars
- **wave**: a moving line, three sine waves added up (the bass a slow swell, the mids, the treble
  quick ripples), each as tall as its part of the sound is loud
- **off**: nothing
It's drawn exactly as wide as the gap, which it measures on the bar, so it follows the right side as
that changes (a longer title, the play button, the clock's long view) and never moves or resizes
anything else (`scripts/visualizer.py`). It costs nothing while silent (it doesn't follow the bar until
something plays), ends cava on **off** (frozen, it held up all audio once the earbuds reconnected), and draws nothing under a fullscreen window.

The stats start with the frame rate: `󰓅 60/60` is how smoothly *this* screen drew whatever moved
in the last second (an animation, a scroll, a video), out of its refresh rate. Each screen's bar counts its
own. Hyprland only draws when something changes, so the frames that came back to back are what's
measured: a smooth panel animation reads `60/60`, a stuttering one less. A still screen, or a lone
redraw like the clock ticking, reads `0`, dimmed. The tooltip has the plain count of frames drawn.
Workspace switches and window switches within a workspace aren't animated (one frame, instant), so they
don't move it. Hyprland has no counter to ask, so `scripts/fps.py` gets a one-pixel screen copy of each
frame Hyprland draws (wlr-screencopy), with the frame's time. That costs next to nothing, but while it runs
Hyprland counts a screencast, and a fullscreen game isn't scanned out directly. Hide it in Settings →
Power & bar → Top bar.

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. awww draws them (`scripts/wallpaper.sh`): a screen whose workspace has windows shows a
blurred, darker copy of the picture (made once, kept in `~/.cache/wallpaper`), so the gaps around the
windows are soft; an empty workspace shows the picture itself, with a short fade between the two
(`modules/wallblur.lua`). Switching windows on a workspace is instant (no fade for the tiled windows
of the monocle workspaces, `wsgroups.lua`), so the wallpaper never flashes through. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

The bar runs as waybar's own systemd user service (`autostart.lua` starts `waybar.service`): if
it crashes it comes back by itself within a second. Restart it with `systemctl --user restart waybar`.

Noctalia is still installed but no longer autostarted.

## Development

`setup/install.sh --packages` installs everything below, then runs `setup/setup-dev.sh`
for the toolchains that don't come from pacman.

| Tool | Installed by | Update with |
|---|---|---|
| docker (+ compose, buildx, lazydocker), go, uv, cmake, ninja, CLI tools | pacman (`setup/packages.txt`) | `sudo pacman -Syu` |
| VS Code (Microsoft build) | AUR (`setup/packages-aur.txt`) | `paru -Syu` |
| Node LTS (+ npm), pnpm, bun | mise (`config/mise/config.toml`) | `mise upgrade` |
| Rust stable, clippy, rustfmt, rust-analyzer | rustup | `rustup update` |
| Python 3 | system python; use `uv` for venvs and tools | `sudo pacman -Syu` |

- Git settings (identity, delta diffs, rebase on pull): `config/git/config`.
  The gh login helper stays in `~/.gitconfig`, which is per machine.
- VS Code: settings and keybindings are not in this repo; they come from VS Code Settings Sync
  (GitHub account). Extensions are listed in `setup/vscode-extensions.txt`.
- VS Code keeps its secrets (logins, tokens) in gnome-keyring: `"password-store": "gnome-libsecret"` in
  `~/.vscode/argv.json`, written by `setup/setup-dev.sh`. Without it VS Code doesn't recognise Hyprland
  as a desktop, finds no keyring and asks to use "weaker encryption" on every start.
- VS Code terminals don't take the editor down when memory runs out: the built-in terminal starts
  each shell with `systemd-run --user --scope` (VS Code setting `terminal.integrated.profiles.linux`;
  shell integration is then loaded from `~/.zshrc`), so systemd-oomd kills that terminal, not
  VS Code. `Ctrl+Shift+C` opens kitty in the project folder instead (`local/bin/kitty-here`, set
  as `terminal.external.linuxExec`), also as its own app. Memory rarely runs out at all: a 16 GB
  SSD swapfile (`system/swap-setup.sh`) takes what overflows zram, so oomd's 90 % swap line is far away.
- Per-project versions: put a `mise.toml` in the project (`mise use node@22`).
- `Super + Shift + C` jumps to or opens VS Code.

## Syncing between PCs

```bash
dotsync                  # save local changes, pull the other PC's, push, relink
dotsync "what changed"   # same, with your own commit message
```

Run it after changing something, and before starting on the other PC. In order: it commits
everything changed here (`git add -A`), pulls the other PC's commits (`git pull --rebase`), pushes,
links any new files (`setup/install.sh`) and reloads Hyprland. When the pull brought a theme change
(`config/themes/`: the other PC switched theme, or changed one's colours), it also runs
`theme apply`, so the bar, terminals and panels follow at once. If both PCs changed the same lines it
stops and names the files to fix. `dotsync` lives in `local/bin/` and is linked into `~/.local/bin`.

Three minutes after a sync, `dotperf --quiet` measures the desktop (below): a notification only if it
got heavier.

What doesn't travel, on purpose: monitor modes (`settings.local.json`), `codesync/machine.json`, the
generated theme files (`config/themes/current/`, made on each PC from the synced palette), agentmux's
window layout and threads (`~/.local/state`), shell history, and anything outside the repo (VS Code
syncs through its own Settings Sync, Chrome through your Google account).

## Desktop health: dotperf

```bash
dotperf          # a minute's measurement of the desktop at idle, against this PC's baseline
dotperf --save   # ... and make it the new baseline (after a deliberate change)
```

What the desktop itself costs: every part running a program from this repo (the bar's scripts, each
panel, the agentmux sidebars, codesync) — its CPU, the processes it starts a minute, its memory. The
first run is the baseline (`~/.local/state/dotperf/baseline.json`, per PC). Worse is red in the table,
and `--quiet` (the daily `dotperf.timer`, and three minutes after `dotsync`) sends a notification
instead: CPU over twice the baseline, 30 more processes a minute, half as much memory again. It
waits for a calm PC and a settled desktop (not right after login). Agents, terminals and apps you run
aren't the desktop and don't count. `setup/install.sh` enables the timer.

## Code backup (codesync)

Code lives on the SSD in `~/code` (fast); `codesync` copies every change to the HDD at
`/mnt/data/code` a few seconds after you save, so a crash loses at most those seconds.

- Runs in the background as a systemd user service, starts at login (`codesync enable`).
- Mirror: the HDD matches the SSD. Anything the sync deletes or overwrites on the HDD is moved to
  `/mnt/data/.code-trash/<date>/` first and kept 30 days.
- Ignore list: `config/codesync/ignore` (`codesync ignore` opens it) — node_modules, .next, dist,
  .venv, caches… A project can add its own `.syncignore`. Ignored files are never copied and are left
  untouched on the HDD.
- Safety: nothing runs if the HDD isn't mounted, and an empty `~/code` is never mirrored.
- Besides the syncs after a change, a full one runs at start, after inotify loses track (an overflow),
  and every 6 hours (`full_every`): rarely, so the HDD can spin down. Every 15 min instead on a PC out
  of inotify watches, where some folders aren't watched.
- **Empty code folder, full backup** (new PC, wiped SSD, wrong folder picked): syncing holds, the
  icon turns amber and a notification says so. In the panel press `r` (or run `codesync restore`) to
  copy the backup into the code folder — nothing is deleted — and syncing resumes by itself.

**Waybar:** the sync icon next to the logo shows the state: grey (up to date), white (changes waiting),
blue (syncing or restoring), amber (paused, or restore needed), red (problem). Hover for a summary
(the last sync's time);
click opens the Code Sync panel (a window, `panels/syncgui.py`: backed-up files/folders/size, what the ignore list skips, free space,
old versions, per-folder breakdown, today's activity, recent changed files, the two folders and the
timing settings: ↑↓ pick, ←→ change a setting). Right-click syncs now, middle-click pauses/resumes.
Timing is stored in `config/codesync/settings.json`.

**Changing the folders:** in the panel, pick *Code folder* (where you work) or *Backup* (where copies
go) and press Enter (or click it). A folder dialog opens; after you choose, the panel shows what the
switch would do to the backup (+ new, ~ changed, − moved to old versions) and applies it on `y`.

| Command | Does |
|---|---|
| `codesync` | status: last sync, errors, paths |
| `codesync pause` / `resume` | hold syncing (changes still noticed, synced on resume) |
| `codesync now` | sync immediately |
| `codesync log` | follow what it's doing |
| `codesync restore` | copy the backup into the code folder (new machine, emptied SSD); never deletes. Alias: `pull` |
| `codesync enable` / `disable` | start / stop the background service |

**On another PC** the folders can be anywhere: `codesync enable` first asks where the code is and
where the backup goes (on the other drive), and saves that to `config/codesync/machine.json`, which is
gitignored, so every PC keeps its own paths while the ignore list and timing stay shared.
`codesync setup` (or the panel) changes them later. If that PC's backup already holds code, run
`codesync restore` first to copy it to the fast drive. Until codesync is enabled on a PC, its waybar icon stays hidden.
The backup drive must be mounted at boot on that PC: an `/etc/fstab` line (this PC's is `system/fstab-data`).
The program is `local/bin/codesync`.

## Projects

Every project in `~/code`, with its git remotes, is recorded in a list, so one command brings them all
back on a new PC or after losing the SSD. The Projects panel browses them: workspace 5's app (a tiled window, class `sami.projects`, like Docker), reached with **Super + Ctrl + P**, `Super + 5` or its workspace button in waybar.

`~/code` is flat: each project is `~/code/<name>`, and its status (Active, Paused, Done…) is in the list,
not in a folder. A folder holds only the repos of one project (`potherbazar/frontend`, `potherbazar/backend`).
Besides those: `templates/`, `notes`, and `inbox/` (scratch: not recorded, not backed up).

**The list** is `~/code/.projects/projects.json`, a git repo of its own, **private** on GitHub
(`code-projects`): it names private and organisation repos, so it never goes in this public repo. It has
two copies, GitHub and the HDD (codesync backs up `~/code`, `.projects/` included). It follows `~/code` by
itself: `projects scan` runs at login and each time the panel opens, and commits + pushes what changed.
A folder you move is recorded as moved; a project that isn't on this PC stays listed. A project moved on
another PC is moved here too, at this PC's next scan (each PC remembers where it last saw every repo, so a
PC still on an old layout follows the list instead of writing its old folders back; one with something
running in it waits for the next scan). A `projects` older than the list (dotfiles not pulled yet) doesn't
write it.

| Command | Does |
|---|---|
| `projects` | status: one line per project — here / not cloned / local only, changed files, ahead/behind |
| `projects clone [<list url>]` | **new PC:** fetch the list (from the URL, else your `code-projects`), then clone every listed project that's missing into its folder, with a local branch for every remote branch. Projects already there are left alone. `projects clone <path>` for one |
| `projects scan` | record `~/code` now (`-n`: only show what would change) |
| `projects new <folder>/<name> [--template <t>] [--github <owner>] [--public]` | a new project: git + a first commit (from `templates/<t>`), a GitHub repo (private unless `--public`), recorded |
| `projects add <url> [path]` | clone a repo (default `~/code/<name>`) and record it |
| `projects forget <path>` | drop a project from the list |
| `projects mark <path> <status>` | set its status: `in-progress`, `active`, `testing`, `blocked`, `planned`, `idea`, `paused`, `done` (`none` clears it) |
| `projects init` | once, ever: create the private repo (already done) |

**Local only** projects (a folder with files and no git remote) can't be cloned back: only the SSD and
the HDD have them. The panel and `projects` mark them in amber; put them in git and push to make them safe.
A folder with repos right inside it is a group, not a local-only project, even with files of its own
(`potherbazar/`: its frontend and backend repos, and a `docker-compose.yml` for both, which only the HDD backs up).

**The panel** (`panels/projectsgui.py`): the `~/code` tree on the left (groups fold with ←/→ or a click;
a repo has a repo icon in the folders' icon column and shows its branch when it isn't on main, ● when it has uncommitted changes; a cloud for one that
isn't cloned; ⚠ local only; it follows the list, so `projects delete` / `move` on the command line show
up in a couple of seconds); `/` searches (the matches in their folders, best first); the dropdown above the search shows one GitHub account's or
org's projects only (or other remotes', or the local-only folders), in every section. Under the tabs, **Sort**:
*by folder* (the tree), *by status* (a section per status, under way first: In progress, Active, Testing, Blocked,
Planned, Idea, Paused, Done (folded), then No status), or *Only …* one status's projects. A project's status is the
dropdown left of Pin on its page (or `projects mark`); its row shows it at the right end, in its colour. Statuses
are in the list, so every PC has them, and follow a project that moves. On the right, the selected project:

- **State**: its branch, then `in sync` / `↑N to push` / `↓N to pull` / `diverged` / `no upstream`, changed
  files, and when it last fetched. Selecting a project whose last fetch is over 10 minutes old fetches it in
  the background, so "to pull" is current (offline: the last known state, and the status line says so).
- **Buttons**: **VS Code** (Enter), **Terminal** (`t`), **Agent** (`a`, an agentmux thread there), **Fetch** (`f`),
  **Pull** (fast-forward only; off when there's nothing to pull or the branch has diverged), **Push**, **GitHub**
  (`g`); **Clone** for one that isn't here.
- **Start / Stop** (`r`, or `projects up` / `projects down <path>`): the whole project at once. Start runs its
  docker compose services (`up -d`), each dev command in an agentmux terminal of its own named `▶ dev` (the
  command typed into a shell there: Ctrl+C stops it, ↑ Enter runs it again), switches agentmux to the
  project with its terminals shown, and opens VS Code on it. Stop sends Ctrl+C to those terminals (closed
  once stopped) and stops the services (the containers stay). A green ▶ in the list marks a project running
  something (its services or a ▶ terminal; looked at every 10 s while the panel shows: one `docker compose
  ls` and one tmux listing). **Run setup…** (beside Start; its own popup, `panels/runsetupgui.py`) shows what Start starts: found by itself (compose files up to two
  folders down, with the project's `.env` — or the one its `.env.example` asks for — when the compose file's folder has neither; `package.json`'s `dev`
  script with its lockfile's runner, else a Makefile's `dev` target, else `manage.py runserver`), or edited
  and saved in the list for both PCs: a row per compose file and the `.env` passed to them, a row per dev
  command (its name, the folder it runs in, the command; ✕ drops a row, + adds one), VS Code or not; a file or
  folder that isn't in the project is outlined red. A `.env` the project wants and lacks here (an `.env.example` /
  `.sample` / `.template`… beside it, or the other PC's encrypted copy) is listed in an amber box with **Create**
  (a copy of the example, to fill in on the .env tab, which shows it at once) or **Pull**; Start still starts
  without it but says which is missing. **Save** (`Ctrl+S`); **Reset to what's found** forgets it. `projects running` lists what runs.
- **.env** tab: the project's `.env` files, as the .env panel (`Super + Ctrl + K`) shows them: keys (hidden, edit
  and Save or `Ctrl+S`), the ones its `.env.example` has that it lacks in red, Create from the example, and its
  encrypted copy for the other PC (Push / Pull / pick one when both changed). Its count: files with something to do.
- **Graph** tab: the last 300 commits of every branch as coloured lanes (merges and forks drawn, ◯ is HEAD),
  with branch / remote / tag labels on their commits: a remote label is bright when the local branch of the
  same name is there too. The dropdown shows one branch's history instead.
- **Branches** tab (Tab switches): each local branch next to its remote, `in sync` (green), `↑ to push`
  (amber), `↓ to pull` (cyan), `diverged` or `remote deleted` (red), `local only` (never pushed), and the
  remote's branches you don't have locally (`remote only`). **Check out** (refused while there are
  uncommitted changes; a remote one becomes a local branch tracking it), **Pull** (fast-forward, even for a
  branch that isn't checked out). Click a row for its history in the graph.

**+ New** (`n`) makes a project: a name, a folder (an existing group, or *New folder…* to type a path such
as `clients/acme`), and what it starts from: *Empty* (a README and git), a *template* (`templates/*`, copied
without git, dependencies and build output, then a fresh git), or *Clone a URL* (every branch). Unless it's
a clone, it can also create the GitHub repo (owner: you or one of your orgs; private by default) and push.
It's recorded in the list, which is pushed.

**On a new PC** the panel opens on a setup page when there's no list: the list's URL (your `code-projects`
repo, filled in when `gh` finds it; or paste any) and **Restore**, which clones it, then every project in it,
with a progress list (12 at a time: GitHub is slow per connection from here, not in total; ✓ / ✗ per project, Stop skips the rest). The folder structure is the
list's: before cloning, what's already in `~/code` is matched by remote URL (`git@host:o/r` and
`https://host/o/r.git` count as the same). A listed repo moves to its listed folder; a project the list
doesn't have (an unlisted repo, a folder without git) moves to `~/code/unsorted/` and is added to the list;
a listed folder already taken stays put. `projects arrange` (`-n`: just show) does the same any time. If `gh` isn't logged in, a
button above opens `gh auth login`. *Start a new list instead* creates the private repo from what's in `~/code`; then build the structure with + New
(*New folder…*, *Clone a URL*), one project at a time. Whenever listed projects aren't on this PC, the
header shows **Clone missing (N)**, which uses the same progress list. The panel notices projects
cloned or removed outside it (`projects clone` / `delete` in a terminal) within a couple of seconds.

**The list on the left** has tabs (`1`–`4`): **All** (the pinned projects first, with a pin, in their folders, then the
folder tree; each project once, folders fold with a click, Enter or ← →), **Changes** (uncommitted work or
commits on no remote), **Behind** (GitHub has commits or branches you don't) and **Conflicts** (branches
or files to merge by hand). Each tab says how many it would show; an empty one is greyed out. The counts
follow every repo on their own: a commit, push, pull or fetch anywhere (VS Code, a terminal, an agent) recounts
that repo within a couple of seconds, and coming back to the workspace recounts them all (edits) when it's
been a couple of minutes. **Sync** at the end of the tabs syncs every project; while it runs it counts up
in blue (`12 / 75`) and the list and tabs follow as each repo is done. A row says what needs doing in words, under the name: *17
changed · 3 to push · 5 to pull · 2 new branches · ⚠ 1 to merge* (hover for the details), with the branch
checked out at the right when it isn't the main one. In a filtered tab the projects are one flat list
(by name, or by status with *Sort: by status*), with their folder in front of the name (pin a project with *Pin* or `p`;
pins are in the list, so every PC has them). When the panel
opens, and when you come back to it after an hour, every repo not fetched in the last hour is fetched in
the background, 8 at a time, so "behind" is current.

**Branches against main.** A project's page opens with two cards: **Status** (the checked-out branch's
state, its remotes, when it was last fetched) and **Branches**, where main (the repo's main branch, whatever
it's called) heads a short list with its own state on GitHub: the branches under it are compared to it. Each
other local branch is a row (the checked-out one has the dot): its state on **GitHub** (`✓ pushed`,
`↑2 to push`, `↓1 to sync`, `↑1 ↓3 diverged`, `• never pushed`, `✗ deleted on GitHub`), then how it stands
**against main**: `same as main`, `in main` (all of it is in main, which has moved on since), or
`3 not in main` (3 of its commits main doesn't have yet). So a `dev` that's pushed but not merged reads
`✓ pushed · 3 not in main`. A click on a branch shows its history (Graph). Up to 6 branches; the
*Branches* tab has them all.

**Sync with GitHub.** Every branch on GitHub is also a branch here, kept up to date by **Sync** (`s`, one
project) or **Sync all** (in the *Out of date* section's header; 8 projects at a time, progress in the
status line, a summary after). Sync fetches, makes a local branch for every branch on GitHub,
fast-forwards the branches that are only behind, and deletes a local branch whose GitHub branch was
deleted, but only when all its commits are in main. It **never merges, pushes, forces or stashes**, and
never touches a folder with uncommitted changes: that branch is skipped and the reason said ("commit
them, then sync"). A branch where you and GitHub both have new commits is left as it is and listed in
**Conflicts**. From a shell: `projects sync [path…] [-n]` (`-n`: only say what it would do).

**The sections** on the left: Pinned · Changes (uncommitted `● N`, unpushed `↑N`) · **Out of date**
(`↓N`: commits behind GitHub, all branches together, as `main ↓N` on the page; `+N new`: branches on
GitHub not here yet; *Sync all*) · **Conflicts** (`⚠ N`) · All projects.

**Conflicts tab** (in a project's page, beside Changes): files left with conflicts in the folder (from a
merge or pull you started: resolve them in VS Code, then commit), and every diverged branch: how many
commits each side has, and the files that would clash (or "would merge cleanly"). Merging is yours to
do, in VS Code; the panel only shows where things stand. The branch table's **main** column says
how each branch stands against main.

**Commit…** (`c`, when there are changes) opens a popup like every other panel (`commitgui.py`: the screen blurred behind it, `Esc` or a click outside closes it): a message (subject line, then a body if it needs one;
`Ctrl+Enter` commits; the subject's length beside it, amber past 72), the changed files (untick what stays
out; each with its `+`/`−` lines, the name before its folder; a filter, and *All* / *None* for the files
it shows; `Space` ticks the selected one), and on the right the selected file's diff (a click on a file,
or `↑↓` in the list: each line numbered, added / removed lines tinted, a header saying where each change
starts, a long line wrapped under its text); **Discard…** (small, red, above the diff, nowhere near Commit)
asks first, then throws away the changes to the file on the right or to every ticked file (a new file is
removed); they go into a `git stash`, so `git stash pop` in the project brings them back. At the bottom,
**Commit & push N files** (`Ctrl+Enter`; it publishes a branch GitHub doesn't have yet) closes Commit once
done; **Commit N files** (`Ctrl+Shift+Enter`) only commits and stays open on the files left; **Push N**
pushes the commits not on GitHub yet (**Publish** for a new branch); **Sync** brings GitHub's side down, as
the Projects panel's Sync does (fetch, fast-forward what's only behind, never merge or push). A normal
`git commit`: the project's hooks run. **Write it for me** has a free model write the message from the
ticked files' changes, in the style of the repo's recent commits, through `opencode run` (the free tier only
works from inside opencode). The dropdown beside it picks the model from opencode's free ones
(`mimo-v2.6-flash` at first); the choice is kept in the project list, so every PC uses it
(`PROJECTS_AI_MODEL` overrides it). Only
the diff is sent, from an empty temporary folder, and the opencode session it makes is deleted after;
free models may keep what they're sent, so don't use it on secrets.

**Ask** (`?`, or *Ask* in the header) is a chat about your projects (`askgui.py`): when a branch was last
pushed and by whom, what isn't pushed, what's behind GitHub, what changed lately… It starts on the project
selected in the panel ("this project"). The model dropdown is the same as Commit's. The chats are kept:
the side list has them, newest first (*+ New chat* or `Ctrl+N` starts one); a chat's bin deletes it (click
it again within 3 s; deleting the one still answering stops its answer). They stay on this PC, in `~/.local/share/projects/ask/` (only you can read them), since
they hold this PC's project data. It is **read-only**, by
construction rather than by instruction (`asklib.py`):
- the model (opencode, `--standalone`) runs in a **bubblewrap sandbox**: the system read-only, a throwaway
  empty home, network only; `~/code`, `~/.ssh`, git's and `gh`'s credentials aren't there at all. It only
  sees the text the panel writes for it, and can only answer.
- to look further it asks for queries from a fixed list (branches, log, one commit, status, pushes); the
  panel checks every argument against the project list and git and runs fixed read-only commands (no
  shell). *Pushes* are this PC's reflog of the remote branches (pushed from here / fetched) and GitHub's
  push events of the last 90 days (`gh api -X GET`, with who pushed). Each query shows as *looked at: …*.
- what it suggests doing (Sync, Sync every project, Check out, Push, Commit…) comes back as **buttons**,
  checked against the real projects and branches; only a click runs one, through the panel's own code.
  Commit… opens the Commit popup, where you write or check the message and the files.
It's sent the project list, branches, commit messages and authors and changed files' names, never a file's
contents; free models may keep what they're sent. Commit's *Write it for me* runs in the same sandbox.

**Changes, live.** A project's page opens on its *Changes* tab: every changed file (modified, new,
deleted, renamed; staged or not; a click on one opens Commit on its diff), and the chips above (branch, ahead / behind, how many changed). While
the panel shows, the project's git status is looked at every 2 s (read-only: no index lock), so an edit,
a commit, a checkout or a pull shows up by itself, the graph and the tree's dot too. *Graph* and
*Branches* are the other tabs (`Tab`).

**Move, delete** (at the right of a project's buttons; each opens a popup over the panel):
*Move…* (`m`) shows the folder tree of `~/code`: pick where it goes, optionally type a new folder to make
inside it and a new name; the line under them says where it ends up. Its HDD backup copy moves with it
(`codesync move`) and the list records it; it's refused while something runs inside the project (a
shell, a dev server, an agent). *Delete* (`Delete`) deletes the folder and drops it from the list,
first saying what only this copy has (uncommitted changes, commits on no remote, stashes); the HDD
backup keeps a deleted copy in codesync's trash for 30 days. *Delete on GitHub* shows only when your
GitHub account may delete the repo (admin on it): type the repo's name to confirm; the folder stays,
without that remote (local-only). GitHub can restore a deleted repo for 90 days. The first time, gh
needs the `delete_repo` permission: the popup offers to grant it (`gh auth refresh`). A listed project
that isn't here has *Forget* instead. From a shell: `projects move`, `projects delete`,
`projects delete-github`, `projects delete-git` (remove `.git` only), `projects forget`.

The list syncs by itself: every change is committed and pushed, after pulling what the other PC pushed
(a scan, at login and whenever the panel opens, pulls first, and pushes a commit a failed push left
behind). If both PCs changed it at once and the two can't be put together, it says so and leaves the
merge to you in `~/code/.projects`.

The program is `local/bin/projects`.

## Databases

A database client for Postgres, Redis, MongoDB, S3 (AWS or anything S3-compatible: MinIO, R2…) and SQLite,
local or remote. Workspace 6's app (a tiled window, class `sami.databases`): `Super + 6`, `Super + Ctrl + 6`
or its workspace button. The program is `local/lib/panels/dbgui.py`; its drivers are `dblib.py`.

- **Connections** (`+ Connection` or `n`, `e` edits the selected one): a form per kind. For Postgres and
  Redis, paste a `postgres://` / `redis://` / `rediss://` URL and it fills the fields; MongoDB takes its
  connection string (`mongodb+srv://…`). **Test** tries it without saving. **SSH tunnel**: `user@server`
  or a host from `~/.ssh/config` reaches a Postgres / Redis that only listens on that server (key login).
  The list is `~/.local/share/dbclient/connections.json` (hosts and users only: it stays on this PC,
  never in this repo); passwords, keys and connection strings are in the keyring (gnome-keyring).
- **Projects**: the list is grouped by project (a connection's *Project* in its form; the rest are under
  *Other*). The arrow at a group's left, Enter or a double-click collapses it; its page on the right counts
  what's in it. `Esc` closes the connection form without saving (also from one of its fields).
- **Docker**: the databases running in Docker (Postgres and its variants such as pgvector / PostGIS / Timescale,
  Redis / Valkey / KeyDB / Dragonfly, MongoDB, MinIO as S3) are added by themselves, named after their
  compose service, in a group named after the compose project, with the user, password and database
  their container's environment sets up (`POSTGRES_USER`…, `--requirepass`, `MONGO_INITDB_ROOT_*`,
  `MINIO_ROOT_*`). The window looks when it comes on screen (and `F5` on a group). It connects on the
  published port, else on the container's own address. A Docker connection shows its port (the
  Docker glyph), dimmed with *stopped* while its container is down. A new port or password is picked up
  by itself; the name and group you gave it stay. Deleting one keeps it out of the list for good
  (`~/.local/share/dbclient/docker-ignored.json`).
- **Rename** (`F2`, or *Rename*): a connection's or a group's name is edited where it's shown; Enter saves,
  Esc leaves it. A group renamed to an existing group's name merges into it.
- **The tree** (left): Enter or a double-click opens a connection (a green dot while connected), then
  schemas → tables and views, Redis keys (with their type), databases → collections, buckets → folders →
  files. `/` filters what's listed; on a Redis connection, Enter in the filter asks the server for keys
  containing it. The dropdown under it shows one kind of database only (*All types* by default). Redis keys and S3 files come a page at a time: open *more* for the next.
- **The right side**: the selected table / key / collection / file with its size and columns (a bucket or
  folder: every file under it, however deep, with its size and date; an image file, picked in the tree or
  in that list, shows beside the table), a page at a
  time, and the selected row in full underneath. **Pages**: `«` `‹` `›` `»` (or `[` `]`), *rows 1,201–1,400
  of 3,000*, a page number to type (Enter), and 50 to 1000 rows a page. A table, a Redis list or sorted set
  and a Mongo collection jump to any page; a Redis hash / set / stream and a query's result go page by page
  (a query's pages are kept: going back doesn't run it again; its total shows once the last page is in).
  Postgres counts a table exactly when that's quick (else the planner's estimate, `~`). The **query box** runs SQL, a Redis
  command (`HGETALL session:42`) or, on a collection, a Mongo filter `{…}` or pipeline `[…]`:
  **Ctrl + Enter** runs it, or just the selected part. **Stop** cancels a long Postgres / SQLite query.
  Full access: what you run applies at once. S3: Download, Upload…, Delete; Redis: Delete (click twice).
- **Diagram** (`d`, or *Diagram* on a Postgres connection, schema or table, or a SQLite file): the schema in
  [Liam ERD](https://liambx.com) inside the panel: the tables and the keys between them, laid out by Liam;
  drag, zoom, click a table for its columns, keys and indexes, *Show* switches names / keys / all fields,
  *Export* saves it. While it shows, the window is solid (no blur behind it): see-through, every frame of a
  pan or zoom was composited again over the blurred desktop, a third slower. The bar above it: the schema picker; **search** (`/`): only the tables whose name has it
  (just that one on an exact name); **Related** (`r`) adds the tables they join, by a key or by a column that
  only stores their id without one (`impersonated_by` → user: guessed from the name and the key's type, its
  key named `guessed: …`); *All tables*: the search cleared and every table back (also after Liam's *Open in main
  area*, which hides the rest); *Close* or `Esc`. Liam's web app is fetched once (pinned version, checked against
  npm's hash, kept in `~/.local/share/dbclient/`); the panel hands it the schema from memory, nothing goes
  over the network. Needs `webkitgtk-6.0` (in `setup/packages.txt`).
- **Ask** (`a`, or *Ask* in the header): a popup like the other panels (`dbaskgui.py`), an AI about the
  connection and what's selected (`dbagent.py`; the System agent's model and machinery: opencode in a
  sandbox with no files, only the panel's tools). It reads on a connection of its own and can't change
  anything: one statement in a read-only transaction that's rolled back (Postgres), the file opened
  read-only (SQLite), reading commands only (Redis), find / aggregate without `$out`, `$merge` or
  JavaScript (MongoDB). *Put in query box* under a query it suggests sends it to the Databases window's
  query box and closes Ask; a write from it needs a second Run. **AI sees data** (per connection, off by
  default): off, the model only gets the structure (tables, columns, types, indexes, counts, Redis key and
  S3 file names), never rows or values; on, it can query the data, which is then sent to the model (free
  models may keep it). It never gets the host, user or password. Chats are kept per connection in
  `~/.local/share/dbclient/chats/` (for you only), deleted with the connection.
- **Light on the PC**: nothing runs until you open the workspace, and nothing polls. A connection opens
  when you use it and closes, with its tunnel, after 3 idle minutes. Each one does its work in a thread of
  its own, so a slow server never freezes the window. A driver's library loads the first time you open
  one of its kind.

## Files

Files (`Super + E`, `Super + Shift + F`, workspace 7; `local/lib/panels/filesgui.py`) is the file
manager: a GTK panel in the panels' look, so it follows the colour theme. It's the default for folders
(`sami-files.desktop` in `config/mimeapps.list`): `xdg-open` of a folder, a browser's "show in folder", a
run of `filesgui.py <folder>` all open the running one there. Dolphin is still installed (it lands on
workspace 7 too).

- **Left:** Places (home, the XDG folders, `~/code`, `~/dotfiles`) and Drives (`/`, what's mounted under
  `/mnt`, `/media`, `/run/media`). **Top:** back, forward, up, the path as crumbs (click one to go there;
  click beside them or `Ctrl+L` to type a path), a filter (`/` or `Ctrl+F`: names containing what you type;
  the first match is selected, `↑` / `↓` move through the matches while you keep typing, `Enter` opens one),
  the details button.
- **The folder:** a table (name, size or a folder's item count, modified, type), folders first, sorted
  by a click on a column. The folder is watched: changes made anywhere show by themselves.
- **Details** (`Ctrl+I`, the button at the top right, or Details in the right-click menu): a pane on the right
  for what's selected, or the folder you're in when nothing is — type, size (a folder's added up as you
  watch: its files and folders in all, links not followed, not into another drive), where it is, what a link
  points to, modified / accessed / created, permissions, owner, MIME type, a picture's size in pixels.
  Several selected: how many and what they add up to. It stays open (or closed) next time.
- **Keys:** `Enter` open (a file in its default app), `Backspace` / `Alt+←` back, `Alt+→` forward,
  `Alt+↑` up, `F2` rename, `Ctrl+Shift+N` new folder, `Del` to the trash, `Shift+Del` delete permanently
  (asks), `Ctrl+C` / `Ctrl+X` / `Ctrl+V` copy / cut / paste, `Ctrl+Shift+C` copy the path, `Ctrl+H` hidden
  files, `F4` a terminal here, `F5` reload, `Esc` clears the filter or the selection. Right-click a file or
  the empty part for a menu.
- Copy and cut put the files on the clipboard as file URIs too, so they paste into other apps; files another
  app copied paste here. A name that's taken gets " (2)". A drive without a trash (NTFS, `/tmp`) asks
  before deleting for good. It remembers the last folder, the hidden-files choice and whether the details
  pane is open (`~/.local/state/files`).

**The file picker.** The Open / Save dialogs apps show (a browser's upload and Save as, VS Code's Open
Folder, GTK apps…) are Files windows too (`pickergui.py`): the same places, path, table and theme, the
app's file types as a filter, and for Save a name field (a click on a file takes its name; saving over one
asks first). It opens like every panel popup: the screen blurred behind it, pinned above it. `Enter` or a double click chooses; `Esc`, Cancel, a click outside it or a workspace switch cancels. It only picks: rename, trash and
paste are off (a new folder is allowed). It remembers its own last folder (`picker.json`).
How it's wired: it's the FileChooser backend of xdg-desktop-portal, a D-Bus service started on the first
dialog (`local/share/dbus-1/services/`), registered per user (`local/share/xdg-desktop-portal/portals/sami.portal`)
and chosen for Hyprland in `config/xdg-desktop-portal/hyprland-portals.conf` (GTK's picker after it, if it's
missing). `GTK_USE_PORTAL` / `GDK_DEBUG=portals` (`config/uwsm/env`) send GTK apps' dialogs there too. On a
PC where it's new, log out and in once (or `systemctl --user restart xdg-desktop-portal`).

**Open with…** The app chooser (Images' / Videos' / Documents' `o`, a browser opening a download, any app
asking the portal which app to use) is a panel too (`openwithgui.py`, shown by the same service as its
AppChooser backend): the apps the portal offers for the file's type with their icons, the default marked in
amber (two apps of one name show their ids). Type to filter, `↑` / `↓`, `Enter` or a double click opens it
with that app; `Esc`, Cancel or a click outside it doesn't.

## Images

Images (`local/lib/panels/imagegui.py`) is the image viewer, the default for pictures (`sami-images.desktop`
in `config/mimeapps.list`): opening a picture in Files (`Enter`), `xdg-open`, a browser's download… shows it
there, in the panels' look and colour theme. `imagegui.py <picture>` shows it and the other pictures in its
folder; several files, just those; a folder, its pictures. A running viewer takes what's opened next. Anything
GdkPixbuf / glycin reads: JPEG, PNG, GIF and WebP (animated too), SVG, AVIF, HEIC, JPEG XL, TIFF, BMP, ICO…

- **Move:** `←` / `→`, `Space` / `Backspace`, `PgUp` / `PgDn`, the mouse's back / forward buttons, `Home` /
  `End`; or a click in the thumbnail strip (`t`). Sorted by name (numbers by value), date or size (menu).
- **Zoom:** the wheel (at the pointer), a pinch, `+` / `−`, `0` fitted (never past 100 %), `1`–`4` that many
  times 100 %, a double-click 100 % there and back. Drag to pan; zoomed past the window, the arrows pan.
  Shrunk it's mipmapped, from 200 % it shows sharp pixels.
- **Turn:** `r` / `Shift+R` rotate, `h` / `v` flip; `Ctrl+S` writes that into the file (ImageMagick).
- **Show:** `f` / `F11` / the header's Fullscreen fullscreen (`Esc` leaves it), `s` slideshow (`[` / `]` slower / faster), `i` info
  (file, pixels, the camera's EXIF), `b` background behind transparency (panel, checkerboard, light), `p`
  pauses an animation.
- **Do:** `F2` rename, `Del` to the trash, `Ctrl+C` copy the picture, `Ctrl+Shift+C` its path, `w` set as
  the wallpaper, `e` show it in Files, `o` open with another app, `Ctrl+O` open, or drop files on it.
  Right-click for all of it. `Esc` / `q` closes.
- The folder is watched (a new screenshot shows up). It remembers the panes, the background, the sort
  and the slideshow's pace (`~/.local/state/images`). imv is still installed.

## Videos

Videos (`local/lib/panels/videogui.py`) is the video player, the default for video and music files
(`sami-videos.desktop` in `config/mimeapps.list`): opening one in Files, `xdg-open`, a browser's download… plays
it there, in the panels' look. mpv does the playing (`libmpv.py`: its renderer draws into the window, hardware
decoding where the GPU has it). `videogui.py <file>` plays it, then the rest of its folder; several files, just
those; a folder, its videos. A running player takes what's opened next.

- **Play:** `Space` / `k` or a click play / pause, `←` / `→` 5 s, `Shift` 30 s, `j` / `l` 10 s, `0`–`9` jump to
  that tenth, `Home` the start, `,` / `.` a frame back / on, drag the bar.
- **Sound:** `↑` / `↓` or the wheel volume (to 150 %), `m` mute; `a` the next audio track.
- **Show:** `f` / `F11` / a double-click fullscreen (the bar floats over the picture and hides with the pointer;
  `Esc` leaves), `c` the next subtitles (or off), `[` / `]` slower / faster, `Backspace` normal speed, `r` loop
  the file, `i` info (codecs, size, the decoder), `t` the playlist (numbered, each file's type and size,
  the playing one marked and kept in view).
- **Live captions:** `Shift+C` (or the CC button) writes what's said at the bottom of the picture, for any
  file, as it plays: whisper.cpp (`whisper-server`, package `whisper-cpp`) hears the sound up to a minute ahead
  of the playhead, so the lines come in time; a seek starts it again there. Only the speech is heard (a voice
  finder skips music and quiet, where whisper made lines up), in one language per file: yours (the locale) until
  what's heard is clearly another, then that one. A line whisper doubts, or a bare tag like `(music)`, isn't
  shown — speech in a language the model can't follow shows nothing rather than nonsense. Right-click →
  Captions in English translates. It runs only while captions are on (on 3 of the 4 cores, at low priority);
  the models (whisper's multilingual base, ~150 MB, and silero, ~1 MB) are downloaded the first time, to
  `~/.local/share/videos` (`captions.py`).
- **Do:** `n` / `p` (`PgDn` / `PgUp`, the mouse's back / forward buttons) next / previous, `s` a screenshot to
  `~/Pictures/Screenshots`, `Ctrl+Shift+C` copy the path, `e` show it in Files, `o` open with another app,
  `Del` to the trash, `Ctrl+O` open, or drop files on it. Right-click for all of it, tracks and speeds
  included. `Esc` / `q` closes.
- Each file resumes where it was left (`~/.local/state/videos/watch_later`); one watched to the end starts
  over, and the next in the folder plays. The folder is watched (a finished download joins the list). It
  remembers the volume, mute, the playlist pane and live captions (`~/.local/state/videos`). mpv is still installed.

## Documents

Documents (`local/lib/panels/docgui.py`) is the PDF viewer, the default for PDFs (`sami-documents.desktop` in
`config/mimeapps.list`): opening one in Files, `xdg-open`, a browser's download… shows it there, in the panels'
look. Poppler draws the pages (in a thread of their own, only those near the view; the paper stays white
whatever the colour theme). A running viewer takes what's opened next.

- **Move:** scroll, `Space` / `PgDn` / `→` on (the rest of a page first), `Shift+Space` / `PgUp` / `←` back, `↑`
  / `↓` a little, `Home` / `End`, `g` go to a page, the mouse's back / forward buttons; `t` the outline (the
  document's chapters, a click jumps there). A page link jumps, a web link opens in the browser.
- **Zoom:** `w` fit the width (the default), `z` fit the page, `+` / `−` or `Ctrl` + the wheel or a pinch,
  `1` printed size.
- **Show:** `f` / `F11` / the header's Fullscreen: a page fills the screen and `←` / `→`, `Space` or the
  wheel turn them, as a presentation (`Esc` or a double-click leaves).
- **Find:** `Ctrl+F`, `Enter` the next match, `Shift+Enter` the previous; matches amber, the current one
  blue; `Esc` clears them.
- **Do:** `Ctrl+C` copy the page's text, `Ctrl+Shift+C` its path, `i` info, `e` show it in Files, `o` open
  with another app, `F5` reload, `Ctrl+O` open, or drop a file on it. Right-click for all of it. A locked
  PDF asks for its password. `Esc` / `q` closes.
- The file is watched: rewritten (a LaTeX build), it's read again at the same place. Each file reopens at
  its page and zoom; the outline pane is remembered (`~/.local/state/documents`).

## LocalSend (phone transfers)

`Super + Ctrl + D` opens LocalSend: send files, folders or a message to the phone, and take what it sends.
Our own (`local/lib/lsend/lsend.py` + the window `local/lib/panels/lsendgui.py`), speaking the LocalSend
protocol: on the phone install the **LocalSend** app (Android, iPhone), be on the same Wi-Fi, and this PC
shows up in it under its hostname; no LocalSend package on the PC.

- **Send:** pick the device on the left (scan with `r` if it isn't there: open LocalSend on the phone
  first), drop files or folders on the window, `Ctrl+O` files / `Ctrl+Shift+O` a folder, `Ctrl+V` pastes
  files copied in Files or text; or type a message (a link, a code). `Enter` sends; the phone asks its user
  to accept. `lsend send <file>…` opens the window with those picked.
- **Receive:** send from the phone to this PC. The window opens (with a notification) and asks: `y` / Accept,
  `n` / Decline; nothing is saved without it (unanswered for 90 s = declined). Files go to `~/Downloads`
  (`name (2).ext` if taken; a folder keeps its folders); a file that arrives short of the size the phone
  announced fails instead of being kept (an empty JPG opened in the browser: there was no picture to read
  its type from). Uploads with or without a length (chunked) both work. A message shows with Copy. Activity lists both
  ways with progress, Cancel, Open, Show in Files.
- **Safety:** HTTPS with this PC's own certificate (`~/.local/share/lsend`, made once, never in the repo);
  only private-network addresses are answered; before sending, the device's certificate must match the
  fingerprint it announced; an upload needs its session's token from the address that asked; received
  names can't leave `~/Downloads`.
- **The service:** `lsend.service` (a user service; `setup/install.sh` runs `lsend enable`) announces this
  PC and receives; it waits on its sockets, no timers. `lsend status` shows this PC's name and the devices
  seen. The firewall must let the phone in: `system/root-setup.sh` opens port 53317 (TCP + UDP) to private
  networks only. Another name than the hostname: `{"alias": "…"}` in `~/.local/share/lsend/settings.json`.

## Ports

`Super + Ctrl + O` (`panels/portsgui.py`): everything listening on this PC, one row a port: who can reach
it (**this PC** only, or the **network**, amber), what it is and how long it's been up. A dev server shows
its program (`next-server`, `vite`, `http.server`) and its project in `~/code` (the folder it runs in); an
app or service its name (its cgroup, as the System panel's Apps tab names it); a Docker container its name
and compose project; root's services (DNS, mDNS…) are named by their port. The chips (`f` cycles) show
**All**, **Dev** (your processes in `~/code` and agentmux terminals), **Docker** or **System**; `/` searches.
**Open** (Enter) opens `http://localhost:<port>`, **Copy URL** (`y`), **Stop** (`d`, twice within 3 s):
SIGTERM to the process (its whole job when a shell started it, so `pnpm dev` and its node stop together),
`docker stop` for a container; root's services can't be stopped here. It asks `ss` every 2 s while open
(docker at most every 10 s), nothing when closed.

## .env files

`Super + Ctrl + K` (`panels/envgui.py`, or `projects env`): every project's `.env` files (`.env`, `.env.local`,
`.env.production`…, in the project and its apps two folders down), with a dot per project: green synced,
amber something to do, red changed on both PCs. A project's page lists each file's keys (values hidden; the
eye shows one): edit and **Save** (`Ctrl+S`) writes the file in place. A key its `.env.example` has and the file
lacks is in red: fill it in and Save. An example with no `.env` beside it: **Create from the example**.

**Synced to the other PC, encrypted**: each file is kept gpg-encrypted (AES-256) in the private list repo
(`~/code/.projects/env/`), with a passphrase you set once per PC (the same on both; kept in the keyring). The
repo only ever holds ciphertext and a keyed hash of each file (to tell which side changed). A file's state:
*synced*, *changed here* (**Push**), *newer on the other PC* or *only on the other PC* (**Pull**), *changed on
both* (**Keep this PC's** / **Take the other's**). **Sync** (`s`) pushes and pulls everything that isn't a
clash. `projects clone` pulls a project's files after cloning it. Command line: `projects env` (status),
`env push`, `env pull`, `env passphrase`.

## Updates

`Super + Ctrl + U` (`panels/updatesgui.py`). **Updates**: what pacman (`checkupdates`, which never locks the
database) and paru (`paru -Qua`) would update, the ones worth a look first in amber with why: the kernels
(in use after a reboot; the LTS one stays bootable), the graphics stack and drivers, Hyprland (a new version
can change the config's syntax: `hyprctl configerrors` after), the core (systemd, glibc), the boot loader,
what the panels run on (GTK, PyGObject, VTE), tmux (`agentmux reload` after), waybar, Docker, and a Python
version change (pip-installed packages stop loading), each warning once over the packages it's about. Above
them, Arch's news since your last full upgrade (a manual intervention is announced there). **Update all**
(`u`) runs `paru -Syu` in the terminal popup, where sudo asks for the password. It runs in a tmux session of
its own (`tmux -L update`), so closing the popup doesn't stop it: `Super + Ctrl + U` while it runs opens it
again where you left it (the wheel scrolls; Shift + drag selects text). Nothing checks in the background: it
looks when it opens (`F5` again).

**Snapshots**: snap-pac snapshots the system before and after every pacman run and grub-btrfs lists them in
the boot menu. After a bad update: reboot, open GRUB's **Arch Linux snapshots** submenu, boot the one from
before it (read-only: check it works), then **Open btrfs-assistant** → Snapper → Restore, and reboot. `/home`
isn't in them: only the system goes back. Listing them here needs `system/root-setup.sh` once (it lets you
read snapper's root config).

## Storage

`Super + Ctrl + F` (`panels/storagegui.py`): the SSD and the HDD (which drive each is: its model, partition,
filesystem and kind; used / free; the bar green, amber from 60 %, red from 85 %), then what can be freed, each with its size (measured when it opens; `F5` again), where it is
on disk and on which drive ("on the SSD"; **Open** shows the folder in Files; Docker's `/var/lib/docker` is root's, so only its path) and a
**Clean** button (twice within 3 s):
- **Docker build cache** (`docker builder prune -af`), **images no container uses** (`docker image prune -af`;
  a stopped container's image stays), **stopped containers** (`docker container prune -f`). Volumes never:
  they hold databases (the Docker panel deletes one by one).
- **node_modules** of `~/code` projects untouched for 30+ days (last commit, else newest file): codesync never
  backs them up, `pnpm install` brings them back. The arrow lists them, to delete one project's.
- **Package cache**: older versions of installed packages (the newest kept) and uninstalled ones
  (`paccache -rk1`, `paccache -ruk0`), in the terminal popup where sudo asks for the password.
- **~/.cache** (the arrow lists its folders, to delete one) and the **Trash** (`gio trash --empty`).
It deletes only inside `~/.cache` and `node_modules` folders in `~/code`; never `/mnt/data` (codesync's
`.code-trash` is codesync's).

## App hotkeys (AutoHotkey-style)

`config/hypr/apps.conf` maps keys to apps, one per line:

```
SUPER + B    | google-chrome     | google-chrome-stable
```

The key jumps to that app's window on any workspace, cycles through its windows
on repeat presses, and launches it if nothing is open. Find a window's class with
`hyprctl clients | grep class`, then `hyprctl reload`. Loaded by
`config/hypr/modules/apps.lua`, run by `scripts/focus-or-launch.sh`.

## Workspace groups

`config/hypr/workspaces.conf` gives each kind of app its own workspace:

| Workspace | Group | Apps |
|---|---|---|
| 1 | Agents | agentmux (Super + A) |
| 2 | Code | VS Code windows, each with its kitty (below) |
| 3 | Web | Chrome windows |
| 4 | Terminal | kitty |
| 5 | Projects | the Projects panel (below) |
| 6 | Data | the database client (see Databases above) |
| 7 | Files | Files (`filesgui.py`; Dolphin opens here too) |
| 8 | Discord | Discord |
| 9 | Docker | the Docker panel and the Dokploy window beside it (below) |
| 10 (key 0) | Control | the Control Center (below) |

```
1 | Code     | code            | code ~/dotfiles
2 | Web      | google-chrome   | google-chrome-stable
```

**Settings → Workspaces** (Super + I) edits all of it: what opens on each workspace (name, bar icon,
window class, launch command, Super + N; or "Use an open app"), the order (↑ ↓ swap two
workspaces: their open windows, bar icons and screens go along, and the VS Code pairs' scrolling
layout and the Control Center follow their apps), and, with more than one screen, which screen
each workspace lives on (saved per PC in `settings.local.json`, since screens differ). The bar
icon is the 6th column of `workspaces.conf`.

With two screens, the focused screen (the one the keys act on) changes only with a click or a
shortcut, not when the cursor crosses over: point at one screen and `Super + 1…0` still switches the
other.

Windows of that class always open on that workspace, maximized (waybar and gaps stay).
A fullscreen video stays fullscreen when you switch away with `Super + Q…P` (window-switcher mode) or `Alt + Tab`,
and is fullscreen again when you come back (a mouse click on another window exits it).
**Floating windows:** `Super + T` turns the focused window into a desktop-style window: out of
maximize, 60 % × 70 % of the screen, centred (the first time). Drag it with `Super` + left mouse,
resize it by grabbing any edge (the cursor shows the arrows; tiled windows' edges resize the split)
or with `Super` + right mouse. It stacks like a desktop window: focus another window of the workspace
and that one covers it; it's in front again once you focus it (`Super + Q…P`, `Alt + Tab`). (The focused
tiled window takes the maximized state for that, the same size in monocle; not on the VS Code + kitty
workspace, where floats stay on top.) `Super + T` again puts it back in the tiles; floated again later,
it comes back where you left it, at the same size. `Super + O` does the same and pins it (it follows
you to every workspace, always on top).
The waybar module on the right shows the current group and window, e.g. `Code 2/3`;
hover for the numbered window list. A popup panel doesn't count: while one is open, the count
and the title beside it stay on the window it opened over.

**Code workspace: VS Code + kitty pairs.** Every VS Code window (Super + N, `code`, File > New
Window…) opens with its own kitty beside it: kitty 45 % on the left, VS Code 55 %, together the
whole screen. kitty starts in that window's project folder (found from the window title under
`~/code`; an empty window gets `~/code`) and runs as its own app, so a dev server that runs out of
memory takes down that kitty, not VS Code. The workspace uses Hyprland's scrolling layout: pairs
sit side by side and `Super + Q…P` / `Alt + Tab` slide to VS Code window N with its kitty (kittys
aren't counted). Opening another project in a VS Code window takes its kitty along: it `cd`s
there if it's idle, or opens a new tab there if something is running. Closing a VS Code window
closes its kitty. `Super + F` (fullscreen) or `Super + Alt + F` (maximized, bar stays) makes
the focused VS Code or kitty fill the screen; press it again and the pair is back side by side.
`Super + Alt + C` / `Super + Alt + T` do the same for the VS Code / the kitty on screen, whichever
has the focus. **Pair layout:** `Super + Alt + P` on a VS Code window or its kitty opens a popup
(`panels/pairgui.py`): the kitty's width (presets 20–80 % or a slider), either one full width
(Kitty full / VS Code full / Side by side), the kitty's side (left / right), this pair or every
pair, and whether new pairs open like this (saved in `config/hypr/codepair.conf`). VS Code can't
go below ~640 px (its title bar doesn't draw narrower; squeezed, it gets cropped). It applies as you
change it; keys: `← →` width,
`l` / `r` side, `b` / `k` / `c` side by side / kitty full / VS Code full, `a` every pair, Esc closes. Lost a kitty? `kitty-pair <address>`
(address from `hyprctl clients`) opens it again. Code: `modules/codepair.lua`, `local/bin/kitty-pair`.

| Key / command | Does |
|---|---|
| `Super + Ctrl + G`, or click it in waybar | open (or close) the manager window: map open apps to workspaces, edit, launch, tidy |
| `Super + X` | window-switcher mode on / off. While it's on, the bar's window counter takes the accent colour and shows the focused window's letter (`W 2/4`), and `Super + Q W E R T Y U I O P` don't do their usual things (close, Files, Settings…): they pick window 1…10. It stays on until `Super + X` again (also across `hyprctl reload`) |
| `Super + Q…P` (in that mode) | switch to window N of the current workspace: the letter row under the digits, Q = 1 … P = 10 (in the order they were opened; reorder them by dragging a row in the Workspaces panel's Open windows list (Super + Ctrl + G), or Alt + ↑ ↓ on it — the bar's `2/4` and the letters follow, for as long as the windows stay open) |
| `Alt + Tab` / `Alt + Shift + Tab` | next / previous window of the current workspace, same order |
| `Super + Ctrl + 1…0` | go to workspace N; launch its programs if none are open |
| `Super + N` | open another window of the current workspace's app (a new Chrome window on Web…), or the app itself when none is open there (the Control Center after it was closed) |
| `Super + Shift + Alt + ←/→/↑/↓` | move the current workspace, with its windows, to the screen in that direction (counted from the focused screen); it stays there, also after a restart: its screen in Settings → Workspaces follows (per PC) |
| `wsgroups launch N\|all [-f]` | launch a group's programs |
| `wsgroups tidy` | move already-open windows to their workspaces |
| `wsgroups list` | show groups and numbered windows |

The manager is a real window (GTK, `local/lib/panels/wsgui.py`): the ten workspaces on the left,
the selected one's settings, actions and open windows on the right. Edit a field and press Enter
(or Save); "Use an open app" puts a running app on the workspace; Remove asks for a second click;
click an open window to jump to it. Keys when no field is focused: `1`–`0` select, `l` launch,
`g` go, `t` tidy, `Delete` remove, `Esc` close. `wsgroups tui` is the old terminal version.

Several launch commands are separated with `;` and open in that order.
Loaded by `config/hypr/modules/wsgroups.lua`; the program is `local/lib/hypr/wsgroups.py` (the command `local/bin/wsgroups` launches it).

### Panels

Every panel is a GTK window (`local/lib/panels/*gui.py`) in the look of the Control Center cards:
icon and title with details on the right, a blue underline, blue section headings, amber keys and
window classes, green / amber / red levels, key hints at the bottom. The popups float in the middle of the screen; running one again (its
key or waybar click) closes it, and so does `Esc`. The hints at the bottom list each panel's keys.
`F11` makes any panel fullscreen and back (`Esc` leaves fullscreen first; `set_fullscreen` in `gtkkit.py`
unpins a popup for the while, as Hyprland won't make a pinned window fullscreen). Images, Videos and
Documents also hide their header and hints there.
A click anywhere outside a text field (a search box…) leaves it, so the panel's keys work again
(`leave_text` in `gtkkit.py`; Projects goes back to its list).
While a popup is open the screen behind it is blurred and darkened, the bar excepted (a see-through,
darkened full-screen window under it that Hyprland blurs like every window, so panels and windows have
the same blur, set in one place: Settings → Appearance → Blur; `Backdrop` in `gtkkit.py`,
`panels-backdrop` in `modules/windowrules.lua`); a click there closes the popup. The popup stays on the workspace it opened on: switch away and back
and it's still there over its backdrop (it's pinned above the backdrop, so that click can't cover it,
and unpinned while another workspace shows). Settings → Appearance → Panels: on / off, darkness, and Panel opacity
(how solid the panel's own background is: lower shows more of the blur behind it; the blur is
Appearance → Blur's, the windows').
The pair popup (`Super + Alt + P`) has none: you watch the pair while you change it.

| Panel | Opens with | Does |
|---|---|---|
| Workspaces (`wsgui.py`) | `Super + Ctrl + G`, waybar groups icon | above |
| System (`sysgui.py`) | `Super + Ctrl + T`, waybar CPU / memory, the busy warning | tabs (`1`–`5`): **Overview** (CPU, memory, GPU — a 90 s graph and the details: temperature, clocks, swap, video memory, power…), **Apps** (CPU by what caused it: each app, service, Docker container and agentmux thread, with what in it is busy — `vitest 150%` — and the new processes a minute. From the cgroups systemd gives each of them, so the short processes a list never catches count too: a test runner's helper commands, Docker's health checks. `Enter`: its processes, or the Docker panel for a container. 100% is one whole core), **Processes** (every process; sort by a column, `/` search, End process / Kill on a second click), **Storage** (each drive: SSD / HDD, read / write now, each partition's usage), **Ports** (`4`: what's listening — port, *this PC* or *network*, the program, its project in `~/code`, how long it's been up; Docker ports show their container. Open / Copy URL / Process, and **Close port** (`Delete`, click twice: stops what holds it, `docker stop` for a container; Kill forces it). *Show all* adds UDP, the system's, and the random high ports apps open for themselves). In Processes a program's ports follow its name (`node · :3100`); `Enter` on it opens them in Ports |
| Audio (`audiogui.py`) | `Super + Ctrl + A`, waybar volume | outputs, inputs, what's playing: volume, mute, make default; the Wiremix button opens wiremix |
| Network (`netgui.py`) | `Super + Ctrl + W`, waybar network | connection, traffic graph, addresses; "Manage connections" opens nmtui |
| Bluetooth (`btgui.py`) | `Super + Ctrl + B`, waybar Bluetooth | your paired devices (Connect / Disconnect, battery, Forget on a second click) and the ones nearby: it scans while open, and Pair pairs, trusts and connects in one go. A device that shows a code (phone, keyboard) has it on the status line. The switch (`p`) turns Bluetooth on / off, unblocking rfkill if needed; `s` stops / starts the scan |
| Shortcuts (`keysgui.py`) | `Super + K`, waybar keyboard icon | every shortcut, live from the config; type to fuzzy-search |
| Projects (`projectsgui.py`) | workspace 5: `Super + Ctrl + P`, its workspace button | see Projects above |
| Code Sync (`syncgui.py`) | waybar sync icon | see Code backup above |
| Settings (`settingsgui.py`) | `Super + I`, waybar cog icon, "Settings" in Walker | see Settings panel below |
| Notifications (`notifgui.py`) | `Super + .`, the bell beside the clock | see Notifications below |
| Power (`powergui.py`) | `Super + Esc`, the power key, waybar power button | Lock, Suspend, Log out, Restart, Shutdown as tiles: a tile's letter (`L S O R P`), `←→` + `Enter` or a click; Log out, Restart and Shutdown ask once (the tile turns red: the same again within 3 s does it) |
| System agent (`sysagentgui.py`) | `Super + Ctrl + S`, waybar sparkles icon | ask an AI about this PC, strictly read-only: see System agent below |

**Busy warning.** While the CPU stays busy (85% for 30 s) a red chip appears left of the idle indicator,
naming what causes it (`󰍛 starter-kits·1`: an agentmux thread; a container, an app); its tooltip has the top
three with what they run, a click opens System on Apps. It goes once the CPU has been calm for 30 s.
`config/hypr/scripts/loadwatch.py` reads `/proc/stat` every 5 s and the cgroups only while busy (no
process started); it tells the visualizer, which makes room.

### Notifications

The bell beside the clock shows them: a plain bell, blue with a dot when something new came since you
last looked, crossed out (red) while **do not disturb** is on. Click it for the panel; right-click it, `Super + Ctrl + ,` or the panel's switch for do not disturb (nothing
pops up; everything is still kept for the panel). The panel lists every notification, newest first,
by day, with its time; the new ones have a blue edge. Click one to open what it's about (an agentmux
one shows its thread; one still on screen runs its own action); `×` (or `Delete`) takes it out,
Clear all empties it. At the bottom, what notifies at all: **Apps** (Settings → Power & bar →
Notifications: hide an app's notifications, the corner, how long they stay) and **Agents**
(agentmux's settings: needs you / finished / error / agent quit, each on or off). `Super + ,`
dismisses the newest on screen, `Super + Shift + ,` all of them.
How: mako runs `scripts/notifications.py log` for every notification (`on-notify` in
`config/mako/config`), which keeps the last 200 with their time in `~/.local/state/notifications.json`
and signals the bell (waybar signal 10). Nothing polls.

### System agent

`Super + Ctrl + S` or the sparkles in the bar: a chat with a free AI model (through opencode, picked in the
dropdown by Ask) about this PC — why a service failed, what's using the memory, where a setting lives, what
a config file does, what Hyprland sees, which packages are installed. It looks before it answers (each
thing it read is listed under the answer) and says where the answer comes from. The chats are kept in
`~/.local/share/sysagent/chats` (only for you); a chat's bin deletes it (a second click within 3 s).

It is **strictly read-only**, by construction (`sysagentlib.py`), in three layers, each enough alone:

1. The model runs in opencode in a bubblewrap sandbox holding none of your files (an empty, throwaway
   home), with network only to reach the model. opencode's own tools are refused (edit, write, bash,
   read, web…): `bash` and `read` stay listed as *ask*, which opencode's free tier requires and a
   non-interactive run always rejects.
2. Its only usable tools are the panel's own (an MCP server on `127.0.0.1` at a random secret path,
   alive only while it answers), and they only read: a file, a folder, find, search, a shell command
   in the reader sandbox, and fixed status commands (processes, services and their status, the journal,
   Hyprland's state, packages, Docker, ports, system info) with every argument checked.
3. Files and commands are read in the reader sandbox: the whole filesystem mounted read-only, no
   network, an empty `/run` and `/tmp` (no session bus, Hyprland, Docker or other sockets), its own
   process namespace (it can't see or signal your programs), no privileges, and a seccomp filter that
   refuses `socket()`, `connect()` and io_uring. Keys, tokens, browser profiles, shell history and every
   `.env` under `~/code` are covered: it can't read them either.

When something needs changing it tells you what to run. Free models may keep what they're sent.

### Settings panel

`Super + I` (or the cog in waybar, or "Settings" in Walker) opens one window for every setting: a sidebar of sections
(type anywhere to fuzzy-search sections and the settings in them) and a page per section. Every
control applies at once; the bottom line says where it was saved.

| Group | Sections |
|---|---|
| Look & feel | Appearance (gaps, borders, corners, opacity, blur, shadow, what's behind an open panel: on / off, how blurry, how dark) · Animations (on/off, speed, workspace slide) · Wallpaper (thumbnails, fill / fit) · Theme & fonts (dark mode, icons, cursor, fonts; nwg-look / qt6ct for more) |
| Input & display | Keyboard (layouts, switch key, repeat, Num Lock) · Mouse (speed, acceleration, natural scroll, focus follows mouse, zoom) · Key mapping (any key or mouse button, side buttons included, to Copy / Paste / media / a shortcut / a command) · Display (mode and scale, with a 15 s "keep?" that reverts; VRR) · Night light (warmth, schedule) |
| Power & bar | Idle & lock (lock / screen-off timeouts, lock before sleep, stay awake) · Power profile · Notifications (corner, timeout, Do not disturb, hidden apps) · Top bar (position, height, stats: frames drawn, processor, memory…) |
| System | Default apps (terminal, browser…, and what opens links / files) · Date & time (time zone, NTP, 24-hour clock) · More settings (opens the other panels) · About this PC |

Where values go (`local/lib/panels/settingslib.py` does the reading and writing; `settingsgui.py`
is only the layout):

- **Hyprland options** → `config/hypr/settings.json` → generated `modules/settings.lua`, required
  last in `hyprland.lua`, so a value set in the panel wins over the hand-written modules. "Back to the
  config's values" on Appearance forgets them. Display modes are per PC: `settings.local.json` →
  `modules/settings_local.lua` (both gitignored).
- **Everything else** in its own file, by line edits of the known keys: `hypridle.conf`,
  `mako/config`, `waybar/config.jsonc`, `variables.lua` (+ `uwsm/env`, `kdeglobals`),
  `mimeapps.list`, `gtk-3.0/settings.ini`, `xsettingsd.conf`, `qt6ct.conf`, plus gsettings.
- Key mapping: press a key or mouse button in the box (it shows its name, and what already uses it),
  pick an action, Add. Saved as `remaps` in settings.json; `modules/settings.lua` gets an `hl.bind`
  per mapping (Hyprland reloads). Copy / Paste send Ctrl+C / Ctrl+V to the focused window; in a
  terminal (kitty, agentmux, the VS Code kittys) Copy takes the selection to the clipboard instead
  (`wl-paste --primary | wl-copy`: Ctrl+C would interrupt) and Paste is Ctrl+Shift+V. A mapped key
  or button does only that, everywhere. Super + K lists the mappings.
- Night light: `toggle.sh nightlight [on|off|auto]` reads the warmth and schedule from
  settings.json; the schedule is two systemd user timers (`settings-nightlight-on/off.timer`), and
  `auto` at login. The bar clock's 24-hour switch and the wallpaper's fill / fit are in
  settings.json too.

The older terminal versions (`sysmon.py`, `keys.py`, `audiopanel.py`, `netpanel.py`,
`syncpanel.py`, `dockerpanel.py`, `controlcenter.py`, `wsgroups tui`) still work if run directly.

### Agents: agentmux

`Super + A` (or workspace 1's button in waybar) opens **agentmux**, a tmux workspace for coding agents, by
project: **Projects** | **Agents** (that project's threads) | the thread | **terminals** (regular
shells). Drag the borders (or `Ctrl+Alt+←→`) to resize; widths are remembered.

```
┌Projects─┬Agents──────┬─ the thread ───────────────┬─ terminal ──┬─────────────┐
│▸cloud_t │◐ 1 diagram │                           │ $ pnpm dev  │ › 3  + ◫ ─  │
│ inkwell │✳ 2 docs    │                           │             │▎▣ pnpm  ◫ × │
│+ open   │+ new thread│                           │             │ ┌◫ fish     │
│         │            │                           │             │ └◫ git      │
└─────────┴────────────┴───────────────────────────┴─────────────┴─────────────┘
 agentmux │ + Terminal Ctrl+Alt+T  × Terminal Ctrl+Alt+W  + Thread Ctrl+Alt+N  Open project Ctrl+Alt+O  ? keys   cloud_track · 1
```

- **A thread's rows in Threads:** its title; then which agent (`claude`, `codex`, `agy`; left out while
  the title is still the agent's name), its account (its name: Rename in the Accounts panel), what it's doing and how
  long it's been up (left out when it doesn't fit); then a bar of how far its account is into its limit, with the window
  (`━━━━━━ 87% wk · Opus 5.5`: green, amber from 60 %, red from 85 %; its 5-hour window, else weekly,
  else monthly; for Antigravity the model's group) and the model its conversation last used (from the conversation
  itself: Claude Code's and Codex's file, agy's database; before its first answer the model the agent
  is set to start with). Read from files, and again only once they change. The usage is asked again
  live when a thread finishes a turn or stops on an error (`agentmux usage`, at most once a minute
  per account), and whenever the Accounts panel is open.
- **Synced with the VS Code kittys.** Every thread is a session on a separate tmux server
  (`tmux -L agents`). The kitty beside a VS Code window starts in one (kitty-pair), and agentmux's
  middle pane attaches to the same one: one agent, two live views, nothing restarted or copied. A
  new thread made in agentmux also opens as a tab in that project's VS Code kitty, and the other way
  round: opening a project in VS Code that agentmux already has threads in, its kitty opens with a tab
  on each of them (a fresh thread only when there are none); a VS Code window switching to such a
  project adds the ones its kitty doesn't show yet. The agent
  redraws at the size of the view you last typed in. A VS Code kitty's session is listed as a thread
  only while an agent runs in it (`claude`, `opencode`, `codex`, `agy` typed there): a dev server,
  tests or a plain shell in it stay that kitty's terminal (`lib.runs_agent`).
  `Ctrl+Shift+T` in a VS Code kitty opens the new tab where the shown tab is (its shell's directory,
  not the folder kitty started in), as a thread of the same project (`kitty-pair --new-tab`). Each
  tab is titled after its thread: the agent's own title, or the shell's directory.
- **`Super + N`** on its workspace (1) starts a new thread (`Ctrl+Alt+N`), opens agentmux if it
  isn't open, or asks for a project first if none is open.
- **The home screen** (the middle pane with no thread shown) has a prompt box: `↵` starts a thread
  in the agent picked with `Tab`, your text as its first message. Above it are the project's threads,
  or with none yet, every agent (uninstalled ones greyed out): click one (or `↑↓` `↵`) to start it.
- **New thread** asks which agent: Claude Code, opencode, agy, Codex (the installed ones; the others
  greyed out). Any number of threads, of any agent, per project; each is its own session. This
  picker and the project picker are GTK popups like every other panel (blurred backdrop; Esc or a
  click outside closes them).
- **The Projects list** shows what you opened, what has a thread and what's open in VS Code, in a
  fixed order: each project keeps the place it first got (a new one goes last), so nothing
  moves as threads and VS Code windows come and go (`order` in `state.json`).
- **Picking a project** (`Ctrl+Alt+O`, or `+ Open project`) is a folder browser. The first time (agentmux
  opens it by itself) it asks for your projects folder, the **root**, and remembers it
  (`~/.local/state/agentmux/state.json`); after that it opens there. `→` or a click goes into a folder,
  `←` goes up (also above the root, to open something anywhere), `↵` opens the selected folder (or
  the shown one: "Open this folder"), `^R` makes the shown folder the root. Typing searches folders
  up to 3 levels below (fuzzy, like the shortcut list); a query starting with `/` or `~` is a path.
  Git repos show a branch icon, open projects a `●`.
- **Threads come back where you left them.** Each thread remembers its own conversation
  (`sessions` in `state.json`): Claude Code, Codex and agy report its id through their hooks;
  opencode has no hooks, so agentmux names its session itself when it starts the thread
  (`opencode --session <id>`). When agentmux opens after a restart or a logout, every open project
  gets its threads back at once, each on its exact conversation (`claude --resume <id>`,
  `codex resume <id>`, `agy --conversation <id>`, `opencode --session <id>`) under its old name, and
  the thread you had in front of you is shown. Opening a project later brings back any of its
  threads that should be running but aren't. A thread without a known conversation (one that hadn't
  heard from its agent yet) continues the latest session of its agent there instead (`--continue`,
  `codex resume --last`). Closing a thread yourself (`x`, `×`, `Ctrl+Alt+X`) or quitting its agent forgets
  it; closing the project doesn't (reopen it and its threads come back). A project with nothing
  remembered (a new one) starts nothing: the home screen asks which agent, with the keyboard on its
  prompt. `+ thread` starts fresh. Skipped for an agent whose conversation already runs straight in
  the project's VS Code kitty. Settings → Projects switches it all off.
- **More than one Claude Code, Codex or Antigravity account.** All stay logged in. One per agent is
  the **default**: new threads of that agent start on it, and it stays the default until you change
  it. **`Ctrl+Alt+K`** opens the **Accounts** panel: the agents down the left (each with its number of
  accounts and its default; a click or `Tab` picks one, the shown thread's agent first), the picked
  agent's accounts at the right, a card each: who's logged in (or *not logged in*), the plan, and each
  usage window as a bar (green, amber from 60 %, red from 85 %) with what's left and when it resets (`38% left ·
  resets Mon 01:49 · in 2h 54m`): Claude Code's **5-hour** and **weekly**, Codex's (a Free plan has one **monthly**),
  Antigravity's per group of models, as agy itself shows them (*Gemini Models*: Flash, Pro; *Claude and
  GPT models*: Opus, Sonnet, GPT-OSS; each a weekly limit). Percentages are *used* (agy's own screen
  says what's *left*). Under them, the **daily budget** shows how much of the weekly (or monthly) limit you can use per day
to make it last, counted from midnight: what was left at 12 AM divided by the days from then to the
reset (`36% left`, `4d 12h` → `8.0% a day`), so what you use today doesn't shrink today's share. On
the last day, all of what's left is the day's. Its bar shows *used today* against that share, amber
and then red as it fills, with what's left today at the right end. Midnight's usage comes from
`agentmux-usage.timer`, which reads every account at 00:00 (after a PC was off or asleep, as soon as it's
back; kept in `~/.cache/agentmux/usage.json`, also read after every turn). If the nearest reading is more
than an hour from midnight, the line says *used since 08:10* (or *since Tue 18:40*) instead of *today*. *this thread* marks the shown thread's account, the green dot and *default* the
  default. `Enter` restarts the shown thread's agent in place on the selected account, on the same
  conversation (`claude --resume <id>`: for when its account runs out of usage); the default doesn't
  change. `Ctrl+D` or a row's **Make default** makes it the default (with no thread shown, `Enter` does).
  A click on a card does what `Enter` does for the shown thread; it never makes it the default (with no
  thread shown it only selects the card).
  Not while the agent is working. Any number of accounts per agent: **+ Add account** (under the cards)
  goes straight to its login (below); once logged in it's named after its email (before the @, Rename
  changes it), and one that doesn't get logged in, or turns out to be an account already there (the
  same email), isn't kept. Or type a name that isn't an account yet in the search line (any text,
  `Client: Acme`; its folder's name is made from it): the same, under that name. A card that isn't logged in has **Log in**: the agent's
  own login on a page of the panel that's a terminal (vte4; `Ctrl+W` or **‹ Back** returns to the cards,
  and so does the login ending, the panel then saying who's logged in; a click on a link opens it in the
  browser, agy's *Click here to authenticate* too, `Ctrl+C` copies a selection; `Ctrl+V`, the **Paste** button or a middle-click
  types the clipboard's text in, a sign-in code: as keys, since agy's code field ignores a terminal's paste), no thread, and the threads that
  run are left alone (`agentmux account login`; without vte4 it runs in a kitty and the panel comes back
  after): Claude Code `claude auth login` and Codex `codex login` into the account's folder; agy, whose
  login lives in one keyring entry, runs with the keyring out of reach and a home of its own, so it keeps
  the new login in a file there, which agentmux moves into the account's keyring slot (and closes agy)
  as soon as it appears: pick *Google OAuth* and sign in in the browser (it opens in your session).
  **Remove** (or `Delete`; a second click confirms) logs it out on this PC and drops it from every
  PC's list (not `main`, the agent's own login; not one a thread runs on; its conversations stay). **Rename** (or `F2`)
  gives an account the name it's shown by everywhere (the panel, the threads, the status bar, the
  home screen; at most 24 characters, a longer one from before is shown cut with …): Enter keeps
  it, Esc doesn't, an empty name is its own again (`main` for the agent's own
  login, else the name it was added as, which stays its folder's). The names and the list of accounts
  are in `config/agentmux/accounts.json`, in the repo: another PC shows the same accounts under the same
  names, *not logged in* until you log in there (logins never go in the repo; an account added on
  another PC gets its folder here the first time a thread runs on it). The default stays per PC.
  The usage first shows what was read last (Claude Code's own in each account's `.claude.json`,
  agentmux's in `~/.cache/agentmux/usage.json`), then asks live with the account's own login (what
  Claude Code's `/usage`, Codex's `/status` and Antigravity read, all undocumented; every account at
  once, again every minute while the panel is open, and right away with **Update** in the header or `F5`).
  A thread's turn shows at once: the panel follows the usage files. An account whose login token has expired (idle for hours) shows
  its saved numbers, *as of …*: agentmux doesn't renew the token itself (it could log out a Claude
  Code running on it); it renews when a thread next runs on that account.
  An account is a config folder (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`): `main` is `~/.claude` /
  `~/.codex`, the others are `~/.local/share/agentmux/accounts/<agent>/<name>/` on each PC (logins
  never go in the repo), linked to main's for everything but the login: settings and hooks, skills,
  plugins, history and the conversations (Claude's `projects/`, Codex's `sessions/`), which is why
  another account can resume one. **Antigravity** keeps its login in the keyring (one entry, whatever
  folder it runs from), so agentmux keeps each account's token in a keyring slot of its own
  (`agentmux-agy`) and swaps it into agy's entry: agy is on **one account at a time**. Switching an agy
  thread restarts every agy thread on that account (each on its own conversation; not while any of
  them works), and a new agy thread starts on the default only when no agy thread runs (else on the
  one they're on). A new agy account starts logged out: agy shows a Google sign-in link in the thread. Its own `.claude.json`
  starts with main's theme, MCP servers and folder trust. Add one in the panel (or `agentmux account
  add <name>`), make it the default, start a thread: Claude Code's first-run screens and `/login` (as
  that account). `agentmux account` lists them (`*` the default); `agentmux account default <name> [agent]`, `agentmux account add "<name>" [agent]` / `remove <account> [agent]`,
  `agentmux account switch [thread] [name]` do the panel's two things. Each thread's row
  and the status bar name its account, and the home screen the default.
  The first message after a switch reads the whole conversation again on the new account (the cache
  is per account): `/compact` first on a long one. A thread reopened after a restart is on the account
  it had. Remove one: delete its folder.
- **Panels close and reopen:** `Ctrl+Alt+1` / `Ctrl+Alt+2` / `Ctrl+Alt+3` (Projects / Threads / Terminals), or the `×` in a
  panel's header (`─` in the terminal list's); a closed one shows as `▸ projects Ctrl+Alt+1` in the status bar (click it) and stays closed
  until reopened. Closing Terminals only hides the column: its shells keep running.
- **Collapse Projects / Threads:** `‹` beside a sidebar's `×` (or `c` in it) folds it to a narrow strip:
  a row per project (its state dot and initial) or thread (its state and number), `+` at the bottom;
  click one to open it, `›` at the top (or `c`) opens the sidebar back to its width. It stays
  collapsed until opened (`collapsed` in `state.json`); dragging its border wide opens it too. Each sidebar
  has a minimum and a maximum width (Projects 20–40 columns, Threads 24–50): dragged narrower than its
  minimum it collapses to its strip, and it stops at its maximum.
- **States and notifications, for every agent.** Each thread shows *needs you* (a permission prompt,
  a question, a plan to approve), *error*, *working*, *done* (finished, not looked at yet), *waiting*
  or *agent exited*; a desktop notification comes when it needs you (stays until you act, and goes away by itself once
  you open the thread, answer it (a permission approved counts: Claude Code's PostToolUse hook), the
  agent finishes or quits, or the thread is closed), finishes,
  fails or exits, once, and not for the thread you're looking at; clicking it opens the thread.
  Where it comes from: the agents' own hooks (`agentmux hooks` installs them into
  `~/.claude/settings.json`, `~/.codex/hooks.json` + `codex_hooks` in `~/.codex/config.toml`,
  `~/.gemini/config/hooks.json`; `setup/setup-dev.sh` runs it), and the screen, read by tmux in the
  sidebars' subscription: "esc to interrupt" (agy: "esc to cancel") = working, a dialog's footer
  ("Esc to cancel", Codex "Press a number to choose", opencode "Permission required", agy "↑/↓
  Navigate") = needs you. opencode 2.x has no hooks: screen only. **Subagents count as working**: a
  thread stays *working* ("· 2 agents") while its subagents or background agents run, even after its
  own turn ended, and notifies *done* when the last one finishes (SubagentStart / SubagentStop hooks,
  Claude's `◯ <agent> <task>` rows on screen, agy's `fullyIdle`).
- **Terminals, like VS Code's:** the shown terminal takes the column's full height; a thin list at
  its right names every terminal of the project (`+` in its header, or `Ctrl+Alt+T`, adds one).
  Click a row (or `↑↓` `↵` in the list, `Ctrl+Alt+↑↓` anywhere) to show it; a long list scrolls
  (the wheel, `↑ more` / `↓ more`). Split is separate: the split button (`Ctrl+Alt+D`) splits the
  shown terminal in two, stacked, sharing the height evenly; the list shows each half as its own
  row, joined by `┌ └`. The shown and the hovered row have their own split button (that terminal in
  two) and a `×` that closes that terminal alone (one half of a split stays); `Ctrl+Alt+W` closes
  the focused one; the last one closes the column. `─` in the header minimizes the column (`Ctrl+Alt+3`): its shells keep running,
  `▸ terminals` in the status bar brings it back. Drag the border left of the list (or of the
  column) to resize; both widths are remembered. Dragged narrower than its names, the list collapses to its
  icons; the terminal itself stops at 20 columns. The shown terminal's row has the blue edge at its right, collapsed too.
  `›` at the left of its header (beside the count of terminals) collapses it to just that (`‹` expands it back to its width; it stays
  collapsed in every project until expanded). Each terminal is a window of the project's
  `<project>·terms` session on the agents server (`local/lib/agentmux/termlist.py` is the list).
- **Maximize a panel:** `Ctrl+Alt+Z` gives the focused panel (Projects, Threads, the thread, or the
  terminals, which count as one with their list) the whole width; the others are hidden and the
  status bar says `maximized`. `Ctrl+Alt+Z` again puts the layout back as it was (the widths aren't
  touched). A focus key for another panel, or closing one, also puts it back first.
- **Terminals are per project:** the column shows only in the projects where you opened it (`Ctrl+Alt+T`);
  switching to another project hides it (its shells keep running) and brings it back when you return.
- **Agents started before this** (straight in a kitty, not in tmux) show as `⚠ title`; Enter on
  one (or `a`) **adopts** it: `/exit`, then the same conversation resumes in tmux in that same kitty
  (`claude --resume <its session>`, found by its title). Only when you choose; it asks first.
- Keys (no prefix; Ctrl+Alt so nothing clashes with Hyprland or Claude Code), all **remappable** in
  Settings: `Ctrl+Alt+P` / `Ctrl+Alt+A` / `Ctrl+Alt+M` / `Ctrl+Alt+E` focus Projects / Threads / the
  thread / the terminals (a closed one is brought back), `Ctrl+Alt+F` the next part (left to right,
  wrapping), `Ctrl+Alt+N` new thread, `Ctrl+Alt+K` accounts, `Ctrl+Alt+O` open project, `Ctrl+Alt+T` terminal,
  `Ctrl+Tab` / `Ctrl+Shift+Tab` next / previous thread of the project (round again), `Ctrl+1` … `Ctrl+9` its 1st … 9th thread
  (in the Threads list's order; an option in Settings → Projects, off gives those keys to the agent); with Projects
  focused (`Ctrl+Alt+P`) the same keys go through the projects, and the focus stays in Projects,
  `Ctrl+Alt+D` split terminal, `Ctrl+Alt+↑↓` previous / next terminal, `Ctrl+Alt+W` close terminal, `Ctrl+Alt+X` close, `Ctrl+Alt+1/2/3` panels, `Ctrl+Alt+Z` maximize, `Ctrl+Alt+←→`
  resize, `Ctrl+Alt+S` settings, `Ctrl+Alt+R` reload (configs read again, every pane restarted; agents
  and shells keep running), `Ctrl+Alt+Q` quit, `Ctrl+Alt+?` this list. In the sidebars: `↑↓`, Enter or a click, the
  wheel; long lists scroll with the selection (`↑ more` / `↓ more`); Agents: `n` new, `a` adopt, `r` rescan.
- **Closing the window only detaches:** the agents keep working (and notifying), the terminals keep
  running, and `Super + A` comes back to it as it was. **`Ctrl+Alt+Q` really quits:** a popup asks
  first (`↵` quit, `Esc` cancel), saying what's still running (agents at work or waiting on you, terminals running a
  program), then ends the workspace, its threads and its terminals. Threads also open in a VS Code
  kitty keep running there; the others are remembered and come back on their conversations the next
  time agentmux opens, as after a restart. Terminals don't come back.
- **Settings** (`Ctrl+Alt+S`, or System Settings → More settings → agentmux): a window laid out
  like System Settings (search, sections: Shortcuts, Notifications, Projects). Click a shortcut and
  press the new keys (Esc cancels); a key already in use is refused, and so are Super (Hyprland's) and
  Ctrl + a letter without Alt (the shell's and the agents'). `Default` / `Reset all` put them back.
  Switches for the notifications (needs you / finished / error / agent quit) and for resuming a
  project's agents when it opens. Saved in `config/agentmux/settings.json` (only once something
  changes; synced with the dotfiles) and applied at once: the keys, the status bar, `Ctrl+Alt+?` and
  the Super + K list follow.
- **Closing:** a project or thread shows its `×` when selected or under the pointer; a click on it
  asks right under that row without opening it (also `x` / `Delete` in a sidebar). The question says
  what ends; `y Close` (or `y` / `↵`) does it, `n Cancel` (`n`, `Esc`, a click elsewhere) doesn't.
  `Ctrl+Alt+X` (remappable in Settings) asks the same for the selected row of the focused sidebar,
  or else for the shown thread. Closing a thread ends its agent. Closing a project ends its threads
  and terminals and takes it off the list, except a thread open in a VS Code kitty (ending it would
  close that kitty): it stays, and the project stays listed while VS Code has it open.
- **A thread's CPU:** one using 10% of a core or more shows it in Threads, in place of *working*:
  `claude · 156% vitest` (amber from a whole core: it's what slows the PC). Read from the thread's
  cgroup (each tmux pane is one), so the commands it ran that already finished count too.
- Light by design: the sidebars sleep until tmux reports a change (a control-mode subscription,
  checked by tmux once a second); nothing polls tmux. While a thread works or shows its CPU, Threads
  looks at its cgroup every 5 s (a file read). A thread nobody watches that is just an
  idle shell is closed when its last viewer leaves; running agents and terminals stay.
- Copy and paste work as in any terminal, in agentmux and in the VS Code kitty (both are tmux): the
  wheel scrolls the history; a drag (or double / triple click) selects; `Ctrl+C` copies the selection
  to the clipboard (nothing selected: interrupt, as usual); `Ctrl+V` pastes; typing or `Esc` drops the
  selection. A selection also goes to the primary selection (middle-click). Shift + drag is kitty's
  own selection (`Ctrl+Shift+C` / `V`). The agents differ, and `agents.conf` evens them out: Claude
  Code and opencode select and paste themselves (the mouse goes to them). Codex and agy take the
  mouse but can't select, so there a drag or double / triple click is tmux's selection; `Ctrl+V`
  there pastes the clipboard's text as a terminal paste (`agentmux paste`; Codex reads Ctrl+V only
  as "paste an image", agy not at all), an image on the clipboard still goes to Codex as its image
  paste; and `Ctrl+Backspace` in agy (which ignores it) is sent as `Ctrl+W`, delete the word.

Code: `local/bin/agentmux`, `local/lib/agentmux/` (`lib.py`, `sidebar.py`, `home.py`, `termlist.py`), `local/lib/panels/agentmuxgui.py` (settings), `local/lib/panels/agentpickgui.py` (the pickers: new thread, open project, accounts),
`config/agentmux/{agents,app}.conf`, `local/bin/kitty-pair`. Needs `tmux` (in `setup/packages.txt`).

### Docker panel (workspace 9)

`panels/dockergui.py`, workspace 9's app (a tiled window, class `sami.docker`), opened together with the
Dokploy window (below), its second window on 9: the header's **Dokploy** / **Docker** button brings the
other one up (workspace 9 shown, that window in front; opened if it was closed), or `Super + X`, then `Q` / `W`. Only the one in front polls. Tabs for
Containers, Images, Volumes and Networks (`1`–`4`, `Tab`, `←→` or a click); Dokploy's containers aren't
on Containers (nor in its counts or Delete stopped), they're in the Dokploy window. The list on the left,
the selected item's details, actions and a lower pane on the right. Both dividers drag: the one between the
two sides, and the one between the details and the pane below (until it's dragged it sits under the details;
dragged up, the details scroll).
The header sums it up: how many run and are stopped, what the images and volumes take on disk.

- **Find:** the search box over the list (`/`; `Esc` clears it) matches names, images, projects, ports,
  subnets and a network's containers; the chips beside it (`f` cycles) show all, running or stopped
  containers (in use / unused for the other tabs). A collapsed project shows its matches. A dot per row
  says its state: green up, amber starting / restarting / paused, red unhealthy or failed, dim stopped.
- Containers are grouped by compose project. A project is a row too: Stop all / Start all,
  Restart all, **Pull & up** (`U`: newer images, then recreate what changed) and **Rebuild** (`B`: build
  again, then recreate) from its compose files, Logs (all its containers in one stream), Folder (`F`, in
  Files) / VS Code (`C`), Collapse (`Enter`) to hide its containers, and **Delete all** (`d` twice:
  `docker compose -p NAME down`, its containers and network; volumes stay).
- Container: `s` start/stop · `r` restart · `p` pause / resume · `l` logs · `e` shell · `x` run a command ·
  `o` open the first port · `y` copy its ID · `d` twice deletes it (stopped first if it runs). The details
  add its networks and IPs and its restart policy. The lower pane (`w` cycles, or a click) is its
  **Logs** (formatted by `panels/dockerlogs.py`: local time, level, message, details dimmed; it follows
  the end while you're there; **Errors** keeps the errors and warnings), **Env**, **Mounts** or the whole
  **Inspect**; the pane's own filter (`Ctrl+F`) keeps the lines with that text. `l` follows the logs in
  `less` in a terminal popup. **Shell** (`e`) opens the container's own shell straight away in a
  terminal popup (bash, else ash, else sh); **Run…** (`x`) runs a command (presets, the last ones,
  "as root"). The terminal popup (`panels/termgui.py`) opens over the blurred backdrop like every panel
  popup and closes when its program ends; every key is the program's (Esc too), `Ctrl+Shift+C / V` copy and
  paste, a click opens a link, Close or a click on the backdrop ends it.
- **Delete stopped (N)** in the header (`D`, twice) deletes every stopped container at once; their
  volumes and images stay. On Images it's **Delete dangling (N)**: the untagged images no container uses.
- Images: `u` pull, `n` run it once in a terminal (`docker run --rm -it`), `y` copy its name. Images,
  volumes, networks: `d` twice deletes it (docker refuses if in use). A first press turns the button red
  ("Click again to delete") for 3 s.
- `L` opens lazydocker in the terminal popup.
- **Docker on / off:** the header's **Turn off Docker** (twice) stops `docker.service` and its socket, so no
  `docker` command starts it again; every container stops, Dokploy goes off first. While it's off the window is
  only a card in the middle ("Docker is off", its service, data folder, whether it starts at boot) and
  **Start Docker** (`Enter`) brings it back (with the containers set to restart). No password:
  `system/root-setup.sh` lets the `docker` group start / stop those two units
  (`system/docker/50-docker-switch.rules`).
- **Closed to the network:** Docker opens published ports to everyone, ahead of ufw. `root-setup.sh`
  installs `system/docker/docker-firewall.sh`, which docker.service runs as it starts: new connections from
  the network into a container are dropped (`DOCKER-USER`). This PC still reaches every port on
  `localhost`; containers still reach out (the internet, a Tailscale tunnel).
- Light on Docker: it follows `docker events` instead of asking every second, waits while workspace 9
  isn't on screen, reads CPU / memory (`docker stats`) only on Containers and the volumes' sizes
  (`docker system df`, which measures every volume) only on Volumes. It used to keep dockerd at about half
  a core; now ~10% while you look at it, nothing while you don't.
- **Idle stacks:** every 15 min (`stacks.timer`) a Compose stack whose project has had nothing working
  in it for an hour (no editor, agent, dev server or shell with its folder open), or whose folder is
  gone, gets a notification with a **Stop** button (`docker compose -p NAME stop`, as Stop all). It
  never stops one itself and asks once per idle stretch. `stacks` lists them (in use / idle for how
  long); `local/bin/stacks`, `setup/install.sh` enables the timer where Docker is installed.

### Dokploy

[Dokploy](https://dokploy.com) deploys apps (Git repos, Compose files, templates) into Docker, with a
dashboard on <http://localhost:23300>. Installed once per PC with `pkexec system/dokploy-setup.sh` (its
official installer: Swarm mode, its Postgres, the dashboard, Traefik on ports 80 / 443; then the dashboard
moved off the installer's port 3000, which dev servers want).

- **Off unless started:** it never comes on by itself. `docker.service` runs `dokployctl down` as it stops
  and again as it starts (`system/docker/docker.service.conf`, installed by `root-setup.sh`), so a reboot,
  a crash or Docker switched off leaves it off. Start it from the Dokploy window or with `dokployctl up`;
  it brings back the apps that were running when it went down.
- `dokployctl status | up | down` (`local/lib/dokploy/dokployctl.py`): off = its Swarm services scaled to
  0, Traefik and its apps stopped (`unless-stopped`, so Docker doesn't restart them); what was running is
  kept in `~/.local/state/dokploy/stopped.json` for `up`.
- **The Dokploy window** (`dockergui.py --dokploy`, class `sami.dokploy`, on workspace 9 beside Docker and
  opened with it) is the Docker panel's window for Dokploy alone: the apps it deployed (the **Apps** tab),
  with the same search, filter, logs, shell and panes. Its own containers (the dashboard, its Postgres,
  Traefik) aren't listed: the header switches Dokploy. While Dokploy is off the window is only a card in the
  middle: "Dokploy is off", its dashboard, version, apps, data and boot setting, and **Start Dokploy**
  (`Enter`); while Docker is off, the same with **Start Docker**. The list comes back once it's on. An app scaled to 0 while Dokploy is off has no
  container after a reboot: its Swarm service is listed in its place (stopped, its logs from the service).
  The header says on / off, what runs and how many apps, with **Stop / Start Dokploy** (Start
  Docker while Docker is off) and **Open dashboard**. Dokploy's details: its state, version, services,
  apps, CPU / memory, whether this PC keeps it off at boot and closed to the network, and **Start / Stop
  Dokploy**, **Open dashboard**, **Logs**.
- Dokploy's things are switched and deleted by Dokploy: its Swarm containers' Start / Stop is Dokploy's
  switch, and an app it deployed has no Pull & up / Rebuild / Delete all here (redeploy or delete it in the
  dashboard), or the panel and Dokploy would disagree.
- **On the internet:** an app gets a public `https://NAME.<tailnet>.ts.net` address from a Tailscale
  container beside it, with Funnel on (Tailscale's ACL: `tag:app` may Funnel and reach nothing else). The
  auth key lives in `~/.local/share/dokploy/tailscale-authkey` (not in git); Dokploy gets it as the app's
  `TS_AUTHKEY`. Nothing else of it is published: the network can't reach its ports (above).

### Control Center (workspace 10)

`panels/ccgui.py`, workspace 10's app (class `sami.controlcenter`): one GTK window, five cards,
all visible at once, in the look of the original terminal version (`controlcenter.py`) —
Workspaces and System on top; Shortcuts, Audio and Network below. Every card has an icon and
title with details on the right, a blue-then-grey underline, and its own key hints at the bottom.

| Card | Shows | Keys |
|---|---|---|
| Workspaces | the ten workspaces: name, window class, a dot per open window; the selected one's launch command | `↑↓` / `1`–`0` select, `g` / Enter go, `l` launch, `t` tidy; `a` `e` `d` open the full manager |
| System | CPU, memory, disk, GPU (bar in green / amber / red), the biggest apps; load in the footer | — |
| Shortcuts | search and every shortcut | type to search, `esc` clear |
| Audio | outputs, inputs, what's playing; ● marks the default | `←→` volume, `m` mute, Enter default, `w` wiremix |
| Network | the connection, down / up with the last minute as bars, the details | Enter manage connections |

Click a card (or Tab into it) to focus it — blue border; keys go to it. It starts at login in the
background (`wsgroups launch 10 --background` in `autostart.lua`); `Super + Ctrl + 0` goes there.

## Shortcut list

`Super + K` (or the keyboard icon in waybar) opens a window listing every
shortcut, read live from `binds.lua`, `apps.conf` and `workspaces.conf`. Type to search, Esc clears / closes.
The search is fuzzy (plain letter matching, no index): a word matches as a substring, as an
abbreviation (`nwin` → "next window", `kpaw` → "keep screen awake") or with one typo (`fulscren`);
the best matches come first, with the matched letters highlighted. `win` / `mod` also find Super.
A bind's description comes from a built-in table in `panels/keys.py`; to label
a new bind yourself, end its line with a comment:

```lua
bind("SUPER + M", launch .. "mpv") -- open mpv
```

## Launcher and default apps

- `local/share/applications/*.desktop` hide apps from Walker (`NoDisplay=true`).
  Delete one to bring that app back.
- `config/mimeapps.list` sets default apps: Images for every picture type GdkPixbuf reads, Videos for
  every video and audio type (playlists and MIDI excepted), Documents for PDFs. A type that's in neither
  list (or a file with no type, like an empty one) goes to `xdg-open`'s fallback, the browser.

## Editing

The files in `~/.config/*` are symlinks to this repo, so editing either path edits
the same file. Commit here afterwards.

Never replace a symlink with a regular file — some editors "save" by delete-and-
recreate, which silently detaches the file from the repo. Check with `ls -l ~/.config`.
