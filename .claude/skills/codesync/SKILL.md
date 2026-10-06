---
name: codesync
description: Operate, debug or change codesync, the background service that backs up the code folder (~/code on the SSD) to the HDD (/mnt/data/code) — its ignore list, timing settings, per-PC folders, waybar icon and panel. Use for any question or change about the code backup.
---

# codesync

`local/bin/codesync` (Python, stdlib only) runs as the systemd user service `codesync.service`.
It watches the code folder with inotify (via libc/ctypes) and runs
`rsync -rlt --delete --backup --backup-dir=<drive>/.code-trash/<date>` a few seconds after changes
stop, plus a periodic full check. Overwritten/deleted files on the HDD go to `.code-trash/<date>/`,
cleaned after `trash_days`.

## Files

| File | Shared? | What |
|---|---|---|
| `config/codesync/ignore` | git | rsync exclude patterns (`node_modules/`, `.next/`, `dist/`…) |
| `config/codesync/settings.json` | git | `quiet`, `max_wait`, `full_every` (s), `trash_days` |
| `config/codesync/machine.json` | **gitignored** | this PC's `src`, `dst`, `mount` (`codesync setup`) |
| `~/.local/state/codesync.json` | runtime | status, last sync, today's counts, totals, recent changes |
| `~/.local/state/codesync.paused` | runtime | exists = paused |
| `~/.config/systemd/user/codesync.service` | per PC | written by `codesync enable` |

| `~/.local/state/codesync.restoring` | runtime | pid of a running `codesync restore` |

State dir is `$XDG_STATE_HOME` (set in this session), not `$HOME/.local/state`: a scratch test must
override **both** `HOME` and `XDG_STATE_HOME`, or it writes the real status file.

The service rereads `settings.json` every loop. Signals to the service: `SIGUSR1` = sync now,
`SIGUSR2` = pause state/settings changed. It signals waybar with `RTMIN+11` on status changes.
UI: waybar `custom/codesync` (`codesync bar`), panel `local/lib/panels/syncpanel.py`.
The panel changes the folders: `folderpick.py` (GTK4 dialog, floated by a window rule) →
`check_folders()` → `preview()` (rsync `-n`, file counts) → `y` → `save_machine()` (writes
machine.json, restarts the service) and reloads the codesync module.

Statuses: `ok` `pending` `syncing` `paused` `error` `off`, plus `restore` (code folder empty, backup
not: held, fixable with `r` / `codesync restore`) and `restoring` (percent in `detail`). A restore
holds `codesync.lock` for its whole run, so the service's first sync waits for a complete code
folder instead of mirroring a half-restored one as deletions.

## Commands

`codesync` (status) · `now` · `pause` / `resume` / `toggle` · `setup` · `restore` (alias `pull`) · `move <from> <to>` (a folder and its backup copy together, under LOCK: `projects move` uses it) · `enable` /
`disable` · `log` · `ignore` · `bar` · `watch` (the service).

## Rules

- **Never delete or write inside the backup folder or `.code-trash/` by hand**, except to remove
  test files you created yourself. The HDD is NTFS (`/dev/sdc1`); `/dev/sda` is Windows — don't touch.
- Don't ignore generic folder names that some projects use for source: `build/`, `vendor/`, `out/`,
  `target/` are deliberately not in the global list. Per-project ignores go in a `.syncignore`.
- `rsync --delete` never removes excluded files on the HDD (no `--delete-excluded`), so adding an
  ignore pattern leaves existing HDD copies alone. Keep it that way.
- Safety checks in `problem()` (drive not mounted, empty source, not set up) must stay in front of
  every sync; an empty or missing source would otherwise mirror as "delete everything".
- Before changing the rsync command, dry-run it: add `-n --itemize-changes` and count the lines.

## Debug

```bash
codesync status
journalctl --user -u codesync -n 50 -o cat
systemctl --user restart codesync          # after editing local/bin/codesync
python3 -m py_compile ~/dotfiles/local/bin/codesync
```

End-to-end test: create/edit/delete a file under `~/code/drafts/_codesync_test/`, wait ~5 s after
each step, check the HDD copy and `.code-trash/<today>/`, then remove the test folder from both.
Testing `setup` without touching the real config: `HOME=<scratch dir> python3 …/codesync setup`
(note it still restarts the real service if one is running — harmless).
