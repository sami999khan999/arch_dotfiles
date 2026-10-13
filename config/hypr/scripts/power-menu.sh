#!/usr/bin/env bash
# The power menu: Lock, Suspend, Log out, Restart, Shutdown (local/lib/panels/powergui.py, a popup like the
# other panels; a second run closes it). Called by Super + Esc, the power key and the bar's power button
exec ~/.local/lib/panels/powergui.py
