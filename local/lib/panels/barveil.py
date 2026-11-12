# barveil.py — the popup backdrop's part over the bar: a darkened strip on top of the bar, blurred by
# Hyprland like the backdrop (layer rule "panels-veil" in modules/windowrules.lua), so the bar goes under
# the frost with the rest of the screen.
#
#   barveil.py <monitor> <darken 0-1>     (gtkkit's make_backdrop starts it; it ends with the backdrop)
#
# The backdrop is a window, and the bar a layer-shell surface on the top layer, always above windows:
# the backdrop can't cover it. This is a layer surface of its own, on the overlay layer, over exactly the
# bar's strip. Its own process because gtk4-layer-shell must be loaded before libwayland (LD_PRELOAD,
# like the lock screen), which a panel already running can't do. Clicks go through to the bar. It
# quits when its stdin closes: the panel that started it ended, however it ended.
import os, sys

os.environ.setdefault("GSK_RENDERER", "gl")   # as gtkkit: GTK's Vulkan renderer flickers here
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as Layer  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
try:
    from themelib import recolor
except ImportError:
    def recolor(text, tid=None):
        return text
from hyprsock import query  # noqa: E402


def bar_height(monitor):
    """The bar's height on that screen while it shows (on the top layer), else 0: hidden, it's under
    the windows and the backdrop covers it already."""
    for name, mon in (query("layers") or {}).items():
        if name == monitor:
            levels = mon.get("levels", {})
            return max((l["h"] for lvl in ("2", "3") for l in levels.get(lvl, [])
                        if l.get("namespace") == "waybar"), default=0)
    return 0


def main(monitor, darken):
    height = bar_height(monitor)
    if not height or not Layer.is_supported():
        return
    gdk_mon = next((m for m in Gdk.Display.get_default().get_monitors() if m.get_connector() == monitor), None)
    if gdk_mon is None:
        return
    css = Gtk.CssProvider()
    # the backdrop's colour and darkness (gtkkit.Backdrop); the backdrop-filter asks Hyprland for its blur
    # (GTK 4 takes part in the Wayland blur protocol and asks for none without one: see gtkkit's CSS)
    css.load_from_string(recolor(f"window.veil {{ background: rgba(22, 22, 30, {darken}); "
                                 "backdrop-filter: blur(10px); }"))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    loop = GLib.MainLoop()
    win = Gtk.Window(title="panels-veil", decorated=False)
    win.add_css_class("veil")
    Layer.init_for_window(win)
    Layer.set_namespace(win, "panels-veil")
    Layer.set_layer(win, Layer.Layer.OVERLAY)
    Layer.set_monitor(win, gdk_mon)
    for edge in (Layer.Edge.TOP, Layer.Edge.LEFT, Layer.Edge.RIGHT):
        Layer.set_anchor(win, edge, True)
    Layer.set_exclusive_zone(win, -1)   # over the bar, not below its reserved strip
    Layer.set_keyboard_mode(win, Layer.KeyboardMode.NONE)
    win.set_default_size(-1, height)

    def click_through(*_):   # an empty input region: the bar under it stays clickable
        import cairo
        win.get_surface().set_input_region(cairo.Region())
    win.connect("realize", click_through)
    win.present()

    # the panel's end closes our stdin (a pipe it holds): quit then, however it ended
    GLib.io_add_watch(GLib.IOChannel.unix_new(sys.stdin.fileno()), GLib.PRIORITY_DEFAULT,
                      GLib.IOCondition.HUP | GLib.IOCondition.IN | GLib.IOCondition.ERR,
                      lambda *_: loop.quit() or False)
    loop.run()


if __name__ == "__main__":
    main(sys.argv[1], max(0.0, min(float(sys.argv[2]), 0.9)))
