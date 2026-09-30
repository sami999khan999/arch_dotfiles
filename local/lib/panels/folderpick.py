#!/usr/bin/env python3
# folderpick.py — a GTK "Select Folder" dialog for the terminal panels (syncpanel.py).
#
#   folderpick.py [start folder] [title]
#
# Prints the chosen folder and exits 0; exits 1 when cancelled. Floated and centred by the
# codesync.folderpick rule in config/hypr/modules/windowrules.lua.
import os, sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

start = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else os.path.expanduser("~")
title = sys.argv[2] if len(sys.argv) > 2 else "Select Folder"
while start != "/" and not os.path.isdir(start):  # a folder that's gone: open its nearest parent
    start = os.path.dirname(start)

app = Gtk.Application(application_id="codesync.folderpick", flags=Gio.ApplicationFlags.NON_UNIQUE)
result = {"path": None}


def picked(dialog, res):
    try:
        folder = dialog.select_folder_finish(res)
        result["path"] = folder.get_path() if folder else None
    except GLib.Error:
        pass  # cancelled
    app.quit()


def activate(app):
    app.hold()  # no window of our own: stay alive until the dialog answers
    dialog = Gtk.FileDialog(title=title, modal=True, initial_folder=Gio.File.new_for_path(start),
                            accept_label="Select")
    dialog.select_folder(None, None, picked)


app.connect("activate", activate)
app.run([])
if not result["path"]:
    sys.exit(1)
print(result["path"])
