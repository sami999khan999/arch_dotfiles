#!/usr/bin/env python3
# snapshot.py — a GTK panel (local/lib/panels/*gui.py) drawn off-screen and saved as PNG, so it can be looked at
# without a window on the user's screen. It runs under gtk4-broadwayd :7 (started if needed); Broadway has no
# compositor, so the blurred backdrop and the window's transparency don't show (that's checked by reading
# the view: see SKILL.md), everything inside the window does.
#
#   snapshot.py <module> "<view expr>" out.png [--size 900x560] [--setup "<python>"] [--then "<python>"]
#
#   module      the panel's module, e.g. setupsgui
#   view expr   evaluated in that module: Setups(), make(), Restore(saved)…
#   --size      the size its window rule in config/hypr/modules/windowrules.lua gives it
#   --setup     run before the view is made, with the module as `m` (stub out what saves, pulls or opens:
#               m.sessionlib.save_setups = lambda s: None)
#   --then      run once it's drawn, with the view as `view` and click("Label") / find("Label") for its buttons
#               (another state: view.selected = "a"; view.paint() — or click("Add"))
#
# DOTFILES_THEME=crimson in front checks it in another colour theme.
import argparse, os, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("module")
ap.add_argument("expr")
ap.add_argument("out")
ap.add_argument("--size", default="900x560")
ap.add_argument("--setup", default="")
ap.add_argument("--then", default="")
a = ap.parse_args()
width, height = (int(x) for x in a.size.split("x"))

if subprocess.run(["pgrep", "-f", "^gtk4-broadwayd :7"], capture_output=True).returncode != 0:
    subprocess.Popen(["gtk4-broadwayd", ":7"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    time.sleep(1)
os.environ.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=":7")

sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
import importlib
import gtkkit
from gtkkit import GLib, Gtk

m = importlib.import_module(a.module)
if a.setup:
    exec(a.setup, {"m": m})
view = eval(a.expr, vars(m))
tries = [0]


def find(text, widget=None):
    """The button labelled text in the window (depth first), or None."""
    w = widget or view.host.win
    if isinstance(w, Gtk.Button) and w.get_label() == text:
        return w
    child = w.get_first_child()
    while child:
        found = find(text, child)
        if found:
            return found
        child = child.get_next_sibling()
    return None


def click(text):
    """Press the button labelled text, as a click would (its "clicked" signal); False if there's none."""
    b = find(text)
    if b:
        b.emit("clicked")
    else:
        print(f"snapshot.py: no button {text!r}", file=sys.stderr)
    return bool(b)


def shoot():
    win = view.host.win
    paintable = Gtk.WidgetPaintable.new(win)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    if node is None and tries[0] < 20:   # not drawn yet: try again in a moment
        tries[0] += 1
        return True
    if node is None:
        print("snapshot.py: the window never drew", file=sys.stderr)
    else:
        win.get_native().get_renderer().render_texture(node, None).save_to_png(a.out)
        print(a.out)
    view.host.quit()
    return False


def ready():
    if a.then:
        try:
            exec(a.then, {"view": view, "m": m, "click": click, "find": find})
        except Exception:   # a broken --then would leave the window open for ever
            import traceback
            traceback.print_exc()
            view.host.quit()
            return False
    GLib.timeout_add(300, shoot)
    return False


GLib.timeout_add(1200, ready)
gtkkit.run(view, f"panels.snapshot{os.getpid()}", (width, height))
