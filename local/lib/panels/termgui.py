#!/usr/bin/env python3
# termgui.py — a program in a terminal popup over the blurred backdrop, in place of a floating kitty:
# the Docker panel's full-screen things (a container's logs in less, its shell, Run…, compose logs,
# lazydocker) open here.
#
#   termgui.py TITLE -- COMMAND [ARG…]
#
# The window closes when the program ends; Close (or a click on the backdrop) hangs it up. Esc and every
# other key are the program's (less, a shell); Ctrl+Shift+C / V copy and paste, a click opens a link.
import os, signal, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gi
gi.require_version("Vte", "3.91")
from gi.repository import Pango, Vte
from gtkkit import Gdk, GLib, Gtk, View, box, recolor, run

# the terminal's 16 colours, Tokyo Night's (as kitty's), each one of the theme's (recolor)
TERM_FG, TERM_BG = "#c0caf5", "#1a1b26"
TERM_PALETTE = ["#32344a", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                "#444b6a", "#ff7a93", "#b9f27c", "#ff9e64", "#7da6ff", "#ad8ee6", "#73daca", "#c0caf5"]
CSS = """
.term { border-top: 1px solid alpha(#3b4261, .7); padding: 10px 14px; }
"""


def rgba(colour, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(recolor(colour))
    c.alpha = alpha
    return c


class Term(View):
    icon = ""   # nf-fa-terminal
    interval = 0
    css = CSS
    hints = [("Ctrl+Shift+C / V", "copy / paste"), ("click", "a link opens it")]

    def __init__(self, title, argv):
        super().__init__()
        self.title, self.argv, self.pid = title, argv, None

    def build(self):
        t = self.term = Vte.Terminal(hexpand=True, vexpand=True, css_classes=["term"])
        t.set_font(Pango.FontDescription("JetBrainsMono Nerd Font 10"))
        # see-through like the panel, so the blur shows behind the text as it does around it
        t.set_colors(rgba(TERM_FG), rgba(TERM_BG, 0), [rgba(c) for c in TERM_PALETTE])
        t.set_scrollback_lines(10000)
        t.set_mouse_autohide(True)
        t.connect("child-exited", lambda *_: (setattr(self, "pid", None), self.window.close()))
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)   # before the program
        keys.connect("key-pressed", self.term_key)
        t.add_controller(keys)
        t.set_allow_hyperlink(True)
        tag = t.match_add_regex(Vte.Regex.new_for_match(r"https?://[^\s\"'<>]+", -1, 0x00000400), 0)   # PCRE2_MULTILINE
        t.match_set_cursor_name(tag, "pointer")
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda _g, _n, x, y: self.open_link(x, y))
        t.add_controller(click)
        GLib.idle_add(self.start)
        return t

    def start(self):
        """The program, once the terminal has its size: one started at 80 columns would lay out its
        first screen for that (the logs' wrapping, tput cols)."""
        if not self.term.get_mapped() or self.term.get_width() < 100:
            GLib.timeout_add(30, self.start)
            return False
        self.window.connect("close-request", lambda *_: self.hang_up() or False)
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, os.path.expanduser("~"), self.argv, None,
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None,
                              lambda _t, pid, _err, _data: setattr(self, "pid", pid), None)
        self.term.grab_focus()
        return False

    def hang_up(self):
        """Closed while it runs: SIGHUP to its process group, as closing a terminal does."""
        if self.pid:
            try:
                os.killpg(self.pid, signal.SIGHUP)
            except OSError:
                pass

    def term_key(self, _ctrl, keyval, _code, state):
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        if mods != Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK:
            return False
        if keyval in (Gdk.KEY_c, Gdk.KEY_C):
            if self.term.get_has_selection():
                self.term.copy_clipboard_format(Vte.Format.TEXT)
                self.say("Copied")
            return True
        if keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.term.paste_clipboard()
            return True
        return False

    def open_link(self, x, y):
        """A click on a link (not the end of a drag that selected text) opens it in the browser."""
        if self.term.get_has_selection():
            return
        url = self.term.check_hyperlink_at(x, y) or (self.term.check_match_at(x, y) or (None,))[0]
        if url:
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say(f"Opened {url}")


def main():
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        sys.exit("usage: termgui.py TITLE -- COMMAND [ARG…]")
    run(Term(sys.argv[1], sys.argv[3:]), "panels.term", (1100, 680))


if __name__ == "__main__":
    main()
