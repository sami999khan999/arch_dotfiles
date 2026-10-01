# gtkkit.py — the shared look and plumbing of the GUI panels (GTK 4, Tokyo Night).
#
# A panel is a View (build() returns its content, refresh() runs every `interval` s while it's
# shown). run(view, app_id) puts it in its own window; controlcenter.py shows several in one.
# Popups (app id "panels.<name>"): running one again while it's open closes it (GTK's single-
# instance D-Bus app: the second launch just activates the first), so one bind toggles it; Esc
# closes it too; Hyprland floats, sizes and centres every "panels.*" window (modules/windowrules.lua).
# Style: the Control Center cards' look everywhere (icon + title, blue underline, blue rule headings,
# amber keys / classes, green-amber-red levels, key hints) on translucent #16161e.
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

CSS = """
/* Tokens. bg: VS Code's Tokyo Night #16161e, 85 % opaque like kitty, so Hyprland blurs the wallpaper
   behind. text #c0caf5 · secondary #a9b1d6 · muted #565f89 · hairline alpha(#c0caf5, .07) ·
   accent #6b8fe0 · alert #f7768e. One typeface; hierarchy comes from size, weight and spacing,
   never from boxes or icons. Section labels are small spaced capitals; key numbers are large and light. */
window.panel { background: alpha(#16161e, .85); color: #c0caf5; font-family: "JetBrainsMono Nerd Font"; font-size: 10pt; }
window.panel * { border-radius: 0; }

/* every window has the Control Center card's chrome: icon + bold title, details on the right,
   an underline that's blue under the title, key hints on the last line */
.head { padding: 14px 20px 0 20px; }
.head .title { font-weight: 700; }
.head-icon { color: #6b8fe0; }
.head-line-lead { min-height: 2px; background: #6b8fe0; }
.head-line { min-height: 2px; background: #292e42; }
.foot { padding: 8px 20px 10px 20px; color: #565f89; min-height: 16px; }
.foot.bad { color: #f7768e; }

.dim { color: #565f89; }
.sub { color: #a9b1d6; }
.accent { color: #6b8fe0; }
.bold { font-weight: 700; }
.red { color: #f7768e; }
.amber, .yellow { color: #e0af68; }
.green { color: #9ece6a; }
.cyan { color: #7dcfff; }
.magenta { color: #bb9af7; }
/* a card section heading: blue title, a thin rule to the right */
.rule-title { color: #6b8fe0; font-weight: 700; }
.rule { min-height: 1px; background: alpha(#3b4261, .9); }
.heading { font-weight: 700; font-size: 13pt; }
/* section headings: blue and bold, as on the Control Center cards */
.section { color: #6b8fe0; font-weight: 700; margin: 18px 0 6px 0; }
.section.flush { margin: 0; }   /* a label that starts its block: no gap above or below */
.rule-head.spaced { margin: 18px 0 6px 0; }
/* the numbers that matter: a size up */
.readout { font-size: 15pt; font-weight: 700; }
.readout-small { font-size: 12pt; font-weight: 700; }

.side { border-right: 1px solid alpha(#c0caf5, .07); }
.hairline { border-top: 1px solid alpha(#c0caf5, .07); }
list { background: transparent; color: #c0caf5; }
list > row { padding: 4px 12px; }
list > row:hover { background: alpha(#292e42, .45); }
list > row:selected { background: #292e42; color: #c0caf5; }
list > row:focus { outline: none; }

entry { background: alpha(#000000, .22); color: #c0caf5; border: none; border-bottom: 1px solid alpha(#c0caf5, .12);
        padding: 5px 10px; min-height: 22px; box-shadow: none; caret-color: #6b8fe0; }
entry:focus-within { border-bottom-color: #6b8fe0; background: alpha(#000000, .32); outline: none; }
entry placeholder, entry > text > placeholder { color: #565f89; }

/* quiet tinted text buttons; blue only for the one main action */
button { background: alpha(#c0caf5, .06); color: #c0caf5; border: none; padding: 5px 14px;
         box-shadow: none; text-shadow: none; min-height: 22px; }
button:hover { background: alpha(#c0caf5, .11); }
button:active { background: alpha(#c0caf5, .16); }
button:focus-visible { outline: 1px solid #6b8fe0; outline-offset: -1px; }
button.primary { background: #6b8fe0; color: #16161e; font-weight: 700; }
button.primary:hover { background: #7f9fe8; }
button.danger { color: #f7768e; }
button.armed { background: #f7768e; color: #16161e; font-weight: 700; }
button.flat { background: transparent; color: #a9b1d6; padding: 3px 10px; }
button.flat:hover { background: alpha(#c0caf5, .08); color: #c0caf5; }
button:disabled { background: alpha(#c0caf5, .03); color: #565f89; }

menubutton arrow { -gtk-icon-source: none; min-width: 0; min-height: 0; margin: 0; }
/* text tabs (Docker, System): the current one bright and underlined in blue */
.tabs { border-bottom: 1px solid alpha(#c0caf5, .07); padding: 0 14px; }
.tab { background: transparent; border-bottom: 2px solid transparent; color: #565f89; padding: 8px 12px; font-weight: 700; }
.tab:hover { background: transparent; color: #a9b1d6; }
.tab.on { color: #c0caf5; border-bottom-color: #6b8fe0; }

popover > contents { background: #16161e; color: #c0caf5; border: 1px solid alpha(#c0caf5, .1); padding: 6px; }
/* scrollbars: a thin bar, no track, no arrow buttons */
scrollbar, scrollbar trough { background: transparent; border: none; box-shadow: none; }
scrollbar button { min-width: 0; min-height: 0; padding: 0; -gtk-icon-source: none; border: none; }
scrollbar slider { background: alpha(#565f89, .45); border: none; min-width: 3px; min-height: 3px; margin: 0 2px; }
scrollbar slider:hover, scrollbar.dragging slider { background: #565f89; }
"""


def hint_markup(pairs):
    """Key hints as markup: keys bright, what they do dim, " · " between."""
    esc = GLib.markup_escape_text
    return "<span foreground='#3b4261'> · </span>".join(
        f"<span foreground='#c0caf5'>{esc(k)}</span> <span foreground='#565f89'>{esc(v)}</span>" for k, v in pairs)


def level(pct):
    """The colour class for a percentage: green, amber from 60, red from 85."""
    return "green" if pct < 60 else "yellow" if pct < 85 else "red"


def rule_heading(title, right="", spaced=False):
    """A section heading: blue title, a thin rule, dim text at the end. spaced: with the gap above
    and below a heading needs between blocks of a window (a card's own spacing does without)."""
    line = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER, css_classes=["rule"])
    head = box(False, 8, label(title, "rule-title"), line, classes=("rule-head", "spaced") if spaced else ("rule-head",))
    if right:
        head.append(label(right, "dim"))
    return head


def label(text="", *classes, xalign=0.0, ellipsize=False, wrap=False, markup=False):
    w = Gtk.Label(xalign=xalign)
    w.set_markup(text) if markup else w.set_text(text)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_hexpand(True)
    if wrap:
        w.set_wrap(True)
    return w


def button(text, on_click, *classes, tooltip=None):
    b = Gtk.Button(label=text)
    for c in classes:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    b.connect("clicked", lambda *_: on_click())
    return b


def box(vertical=False, spacing=0, *children, classes=()):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                spacing=spacing)
    for c in classes:
        b.add_css_class(c)
    for child in children:
        b.append(child)
    return b


def clear(container):
    while (child := container.get_first_child()) is not None:
        container.remove(child)


def scrolled(child, vexpand=True):
    s = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=vexpand)
    s.set_child(child)
    return s


def install_css(extra=""):
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS + extra)
    # above USER priority: ~/.config/gtk-4.0/gtk.css (the Noctalia/GTK theme) mustn't restyle panels
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)


class View:
    """A panel's content. The same view runs on its own (run()) or as a section of the Control
    Center; either way its host gives it a header, a status line and a refresh timer."""
    title, subtitle = "Panel", ""
    interval = 2.0           # seconds between refresh() calls while shown; 0 = never
    hints = []               # [(key, what)] shown on the status line when there's no message
    css = ""                 # extra CSS this view needs (added to CSS above)
    header = True            # False: no title row (the Control Center's tiles have their own)
    compact = False          # set by the Control Center before build(): a simplified tile
    popup = ""               # command for the full panel (Edit / Open from its Control Center card)
    icon = ""                # the Control Center card's header glyph
    footer = True            # False: the window shows no hint line (the Control Center's cards have their own)

    @property
    def tile_info(self):
        """Dim text at the right of the card's header."""
        return self.subtitle

    @property
    def tile_footer(self):
        """The card's last line: its key hints (Pango markup)."""
        return hint_markup([h for h in self.hints if h[0] != "Esc"])

    def __init__(self):
        self.host = None

    # -- override --
    def build(self):
        """The content between the header and the status line. Called once."""
        return Gtk.Box()

    def header_extra(self):
        """Widgets at the right end of the header (e.g. a global action button)."""
        return []

    def refresh(self):
        pass

    def key(self, keyval, state):
        """A key the focused widget didn't use. Return True if handled."""
        return False

    # -- provided by the host --
    def say(self, msg, kind="ok"):
        """A message on the status line; kind "bad" shows it in red. It clears itself."""
        self.host.say(msg, kind)

    def typing(self):
        """True while a text field has the keyboard (so letter shortcuts stay off)."""
        return self.host.typing()

    def set_subtitle(self, text):
        """Change the line next to the title (only in the panel's own window; tiles have none)."""
        if self.host and self.host.view is self:
            self.host.subtitle_label.set_text(text)

    def close(self):
        """Done with the panel (after "go to workspace", focusing a window…): closes a popup,
        does nothing inside the Control Center."""
        self.host.close_view()

    @property
    def window(self):
        return self.host.win


class PanelApp(Gtk.Application):
    """A window hosting one view. toggle=True (popups): running the panel again while it's open
    closes it, and Esc closes it. toggle=False (the Docker panel and Control Center, which are
    their workspace's app): running it again just brings it up."""

    def __init__(self, view, app_id, size=(900, 560), toggle=True):
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.view, self.size, self.toggle = view, size, toggle
        view.host = self
        self.win = None
        self.status_timer = 0

    def do_activate(self):
        if self.win:
            self.win.close() if self.toggle else self.win.present()
            return
        install_css(self.view.css)
        self.win = Gtk.ApplicationWindow(application=self, title=self.view.title, decorated=False)
        self.win.add_css_class("panel")
        self.win.set_default_size(*self.size)

        lead = box(False, 10, *([label(self.view.icon, "head-icon")] if self.view.icon else []),
                   label(self.view.title, "title"))
        self.subtitle_label = label(self.view.subtitle, "dim", xalign=1.0, ellipsize=True)
        top = box(False, 12, lead, self.subtitle_label, *self.view.header_extra(),
                  *([button("Close", self.win.close, "flat", tooltip="Esc")] if self.toggle else []))
        under_lead = Gtk.Box(css_classes=["head-line-lead"])
        group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # blue exactly under icon + title
        group.add_widget(lead)
        group.add_widget(under_lead)
        underline = box(False, 0, under_lead, Gtk.Box(hexpand=True, css_classes=["head-line"]))
        underline.set_margin_top(8)
        head = box(True, 0, top, underline, classes=("head",))

        self.status = label("", "foot")
        self.status.set_visible(self.view.footer)
        body = self.view.build()
        body.set_vexpand(True)
        self.show_hints()
        self.win.set_child(box(True, 0, *([head] if self.view.header else []), body, self.status))

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.win.add_controller(keys)
        if self.view.interval:
            GLib.timeout_add(int(self.view.interval * 1000), self._tick)
        self.win.present()

    def _tick(self):
        if not self.win:
            return False
        self.view.refresh()
        return True

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape and self.toggle and not self.typing():
            self.win.close()
            return True
        return self.view.key(keyval, state)

    def close_view(self):
        if self.toggle:
            self.win.close()

    def say(self, msg, kind="ok", seconds=4):
        self.status.set_visible(True)
        self.status.set_text(msg)
        self.status.remove_css_class("bad")
        if kind == "bad":
            self.status.add_css_class("bad")
        if self.status_timer:
            GLib.source_remove(self.status_timer)

        def clear_msg():
            self.status_timer = 0
            self.show_hints()
            return False
        self.status_timer = GLib.timeout_add(seconds * 1000, clear_msg)

    def show_hints(self):
        self.status.remove_css_class("bad")
        self.status.set_visible(self.view.footer)
        self.status.set_markup(hint_markup(self.view.hints))

    def typing(self):
        w = self.win.get_focus() if self.win else None
        while w is not None:
            if isinstance(w, Gtk.Editable):
                return True
            w = w.get_parent()
        return False


def run(view, app_id, size=(900, 560), toggle=True):
    PanelApp(view, app_id, size, toggle).run([])
