#!/usr/bin/env python3
# polkitgui.py — the password prompt (polkit's authentication agent): pkexec, a service started or stopped, a
# disk mounted… ask for the password in a popup like every panel's, in the colour theme. A user service
# (setup/systemd/polkit-agent.service, started by modules/autostart.lua); hyprpolkitagent takes over if it fails.
# It registers this session's agent with polkitd (org.freedesktop.PolicyKit1.AuthenticationAgent on the system
# bus); each request is a window: what wants it, the command (pkexec), who to authenticate as when there's a
# choice, the password, in the login screen's field (lockscreen/pwfield.py). polkit's own helper checks it
# (PolkitAgent.Session, PAM): nothing here sees more than the text typed. Enter or the arrow authenticates, Ctrl + H
# shows the password, Esc / Cancel / a click on the backdrop says no; a wrong password shakes the field and asks again.
import gc, grp, math, os, pwd, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, Gio, GLib, Gtk, View, ViewHost, box, button, collect, dropdown, install_css, label,
                    scrolled)
import gi
gi.require_version("Polkit", "1.0")
gi.require_version("PolkitAgent", "1.0")
from gi.repository import Polkit, PolkitAgent
# the login screen's password field (squares popping in, a glow per state, a sweep while checking)
sys.path.insert(0, os.path.expanduser("~/.local/lib/lockscreen"))
from pwfield import FIELD_CSS, Dots, Sweep, keycaps

PATH = "/org/sami/PolkitAgent"
AGENT = Gio.DBusNodeInfo.new_for_xml("""<node>
  <interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
    <method name="BeginAuthentication">
      <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
      <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
      <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
    </method>
    <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
  </interface>
</node>""").interfaces[0]
CANCELLED = "org.freedesktop.PolicyKit1.Error.Cancelled"
# details polkit or the caller add for itself, not worth showing
HIDDEN = {"polkit.message", "polkit.gettext_domain", "polkit.icon_name"}

CSS = """
.auth-message { font-size: 11pt; }
.auth-command { background: alpha(#c0caf5, .04); border: 1px solid alpha(#3b4261, .7); padding: 8px 12px; }
.auth-command .accent { font-weight: 700; }
.auth-details { background: alpha(#c0caf5, .03); border: 1px solid alpha(#3b4261, .7); padding: 8px 12px; }
.lock-card .field { margin: 4px 3px 0 3px; }   /* room for its glow */
"""
SHAKE = 0.45   # s the field shakes after a wrong password (as on the lock screen)


def users(identities):
    """polkit's identities as [(uid, name)]: a group stands for its members. This user first: the usual one."""
    uids = []
    for kind, d in identities:
        if kind == "unix-user" and "uid" in d:
            uids.append(d["uid"])
        elif kind == "unix-group" and "gid" in d:
            try:
                g = grp.getgrgid(d["gid"])
            except KeyError:
                continue
            for m in g.gr_mem:
                try:
                    uids.append(pwd.getpwnam(m).pw_uid)
                except KeyError:
                    pass
    out, me = [], os.getuid()
    for uid in sorted(dict.fromkeys(uids), key=lambda u: (u != me, u)):
        try:
            out.append((uid, pwd.getpwuid(uid).pw_name))
        except KeyError:
            pass
    return out


class Auth(View):
    icon = "\U000f099d"   # shield lock
    title = "Authentication required"
    interval = 0
    css = CSS + FIELD_CSS
    hints = [("Enter", "authenticate"), ("Esc", "cancel")]

    def __init__(self, action_id, message, details, cookie, identities, done):
        super().__init__()
        self.action_id, self.message, self.details, self.cookie = action_id, message, details, cookie
        self.users = users(identities) or [(os.getuid(), pwd.getpwuid(os.getuid()).pw_name)]
        self.uid = self.users[0][0]
        self.done = done           # done(ok): answers polkitd, once
        self.answered = False
        self.session = None
        self.subtitle = "as " + self.users[0][1]

    def build(self):
        self.asked = False      # the helper is waiting for the password
        self.pending = False    # Enter came before it asked: sent when it does
        self.checking = self.passed = False
        self.failed_text = self.note = ""
        self.reveal = False
        self.shaken = 0.0

        col = box(True, 12)
        col.set_margin_top(20), col.set_margin_start(24), col.set_margin_end(24), col.set_margin_bottom(14)
        col.append(label(self.message or "An application wants to do something that needs your password.",
                         "auth-message", wrap=True))
        cmd = self.details.get("command_line") or self.details.get("program")
        if cmd:   # pkexec: the command, as it will run
            run_as = self.details.get("user", "root")
            line = box(False, 8, label("#" if run_as == "root" else "$", "accent"),
                       label(cmd, wrap=True), classes=("auth-command",))
            line.get_last_child().set_hexpand(True)
            col.append(line)
        if len(self.users) > 1:   # who to authenticate as: polkit offers several (an admin group)
            col.append(box(False, 12, label("As", "sub"),
                           dropdown([(u, n) for u, n in self.users], self.uid, self.pick_user)))
        col.append(self.make_field())

        self.more = box(True, 4, classes=("auth-details",))
        self.more.append(label(f"<span foreground='#565f89'>action</span>  {GLib.markup_escape_text(self.action_id)}",
                               markup=True, wrap=True))
        for k, v in sorted(self.details.items()):
            if k not in HIDDEN:
                self.more.append(label(f"<span foreground='#565f89'>{GLib.markup_escape_text(k)}</span>  "
                                       f"{GLib.markup_escape_text(v)}", markup=True, wrap=True))
        self.more_area = scrolled(self.more)
        self.more_area.set_visible(False)
        col.append(self.more_area)
        self.spacer = Gtk.Box(vexpand=True)   # the buttons at the bottom; the details take its place
        col.append(self.spacer)

        self.more_btn = button("Details", self.toggle_details, "flat")
        col.append(box(False, 8, self.more_btn, Gtk.Box(hexpand=True), button("Cancel", self.cancel, "flat")))
        keyboard = Gdk.Display.get_default().get_default_seat().get_keyboard()
        self.keyboard = keyboard
        if keyboard:
            keyboard.connect("notify::caps-lock-state", lambda *_: self.show())
        self.show()
        GLib.idle_add(lambda: self.start() and False)
        return col

    def make_field(self):
        """The login screen's field: a padlock, the entry under its squares, the eye, the arrow; the line under it."""
        self.buffer = Gtk.EntryBuffer()
        self.buffer.connect("notify::text", self.on_typed)
        self.icon = label("\uf023", "icon", xalign=0.5)   # a padlock
        self.entry = Gtk.Entry(buffer=self.buffer, visibility=False, xalign=0, hexpand=True,
                               placeholder_text="Password")
        self.entry.connect("activate", lambda *_: self.respond())
        self.dots = Dots()
        text = Gtk.Overlay(hexpand=True)
        text.set_child(self.entry)
        text.add_overlay(self.dots)
        self.eye = Gtk.Button(label="\uf06e", focusable=False, tooltip_text="Show the password (Ctrl+H)")
        self.eye.add_css_class("eye")
        self.eye.connect("clicked", lambda *_: self.toggle_reveal())
        go = Gtk.Button(label="\uf061", focusable=False, tooltip_text="Authenticate")   # an arrow
        go.add_css_class("go")
        go.connect("clicked", lambda *_: self.respond())
        self.field = Gtk.Overlay()
        self.field.add_css_class("field")
        self.field.set_child(box(False, 0, self.icon, text, self.eye, go))
        self.sweep = Sweep()
        self.field.add_overlay(self.sweep)
        self.msg = Gtk.Label(use_markup=True, xalign=0)
        self.msg.add_css_class("msg")
        return box(True, 6, self.field, self.msg, classes=("lock-card",))

    def toggle_details(self):
        on = not self.more_area.get_visible()
        self.more_area.set_visible(on)
        self.spacer.set_visible(not on)
        self.more_btn.set_label("Hide details" if on else "Details")

    def toggle_reveal(self):
        self.reveal = not self.reveal
        self.entry.set_visibility(self.reveal)
        self.eye.set_label("\uf070" if self.reveal else "\uf06e")   # eye slashed / eye
        self.eye.set_tooltip_text(("Hide" if self.reveal else "Show") + " the password (Ctrl+H)")
        (self.eye.add_css_class if self.reveal else self.eye.remove_css_class)("on")
        self.show()

    def pick_user(self, uid):
        self.uid = uid
        self.set_subtitle("as " + dict(self.users)[uid])
        self.start()

    def on_typed(self, *_):
        if self.buffer.get_length():
            self.failed_text = ""
        self.show()

    def show(self):
        """The field and the line under it, from the state: right, checking, wrong, Caps Lock, PAM's note."""
        caps = bool(self.keyboard and self.keyboard.get_caps_lock_state())
        esc = GLib.markup_escape_text
        if self.passed:
            text, kind = esc("\uf00c  Authenticated"), "ok"
        elif self.checking:
            text, kind = esc("Checking…"), ""
        elif self.failed_text:
            text, kind = esc(f"\uf00d  {self.failed_text}"), "fail"
        elif caps:
            text, kind = esc("\uf071  Caps Lock is on"), "caps"
        elif self.note:
            text, kind = esc(self.note), ""
        else:
            text, kind = keycaps([("Enter", "authenticate"), ("Ctrl+H", "show")]), ""
        n = self.buffer.get_length()
        self.msg.set_markup(text)
        for k in ("fail", "caps", "ok"):
            (self.msg.add_css_class if k == kind else self.msg.remove_css_class)(k)
        states = {"fail": bool(self.failed_text), "caps": caps, "ok": self.passed, "busy": self.checking,
                  "ready": n > 0 and not self.checking and not self.passed}
        for k, on in states.items():
            (self.field.add_css_class if on else self.field.remove_css_class)(k)
        self.icon.set_label("\uf09c" if self.passed else "\uf023")   # open padlock / padlock
        self.sweep.run(self.checking)
        self.entry.set_editable(not self.checking and not self.passed)
        masked = n > 0 and not self.reveal
        (self.entry.add_css_class if masked else self.entry.remove_css_class)("masked")
        self.dots.sync(n if masked else 0)

    def shake(self, field, _clock):
        """A wrong password: the field shakes side to side, settling within SHAKE seconds."""
        t = time.monotonic() - self.shaken
        off = 0 if t >= SHAKE else round(9 * math.sin(t * 42) * (1 - t / SHAKE))
        field.set_margin_start(3 + off)
        field.set_margin_end(3 - off)
        return GLib.SOURCE_CONTINUE if t < SHAKE else GLib.SOURCE_REMOVE

    # -- the conversation with polkit's helper (PAM) --
    def start(self):
        """A new session: the first try, another user picked, or after a wrong password."""
        self.stop_session()
        s = PolkitAgent.Session.new(Polkit.UnixUser.new(self.uid), self.cookie)
        s.connect("request", self.on_request)
        s.connect("show-error", lambda _s, text: self.set_note(text))
        s.connect("show-info", lambda _s, text: self.set_note(text))
        s.connect("completed", self.on_completed)
        self.session, self.asked = s, False
        s.initiate()

    def stop_session(self):
        if self.session:
            s, self.session = self.session, None
            s.cancel()   # its "completed" comes for a session that's no longer ours: ignored

    def on_request(self, s, prompt, echo):
        if s is not self.session:
            return
        prompt = prompt.strip().rstrip(":")
        self.entry.set_placeholder_text("Password" if prompt.lower() in ("password", "") else prompt)
        if echo and not self.reveal:   # a question that isn't secret (a user name, a code): shown as typed
            self.toggle_reveal()
        self.asked = True
        self.entry.grab_focus()
        if self.pending:
            self.respond()

    def respond(self):
        if self.checking or self.passed or not self.buffer.get_length():
            return
        if not self.session or not self.asked:   # the helper hasn't asked yet: as soon as it does
            self.pending = True
            return
        self.pending, self.asked, self.checking = False, False, True
        self.show()
        self.session.response(self.buffer.get_text())

    def on_completed(self, s, gained):
        if s is not self.session:
            return
        self.session, self.checking = None, False
        if gained:   # green for a moment, as on the login screen, then gone
            self.passed = True
            self.show()
            GLib.timeout_add(450, lambda: self.finish(True) or False)
            return
        self.buffer.set_text("", -1)   # wrong: the field clears, turns red, shakes; it asks again
        self.failed_text = self.note or "Wrong password"
        self.note = ""
        self.show()
        self.shaken = time.monotonic()
        self.field.add_tick_callback(self.shake)
        self.start()

    def set_note(self, text):
        """What PAM says besides the prompt (an account locked, tries left)."""
        self.note = text.strip()
        self.show()

    # -- answers --
    def finish(self, ok):
        if not self.answered:
            self.answered = True
            self.done(ok)
        if self.host and self.host.win:
            self.host.win.close()

    def cancel(self):
        self.stop_session()
        self.finish(False)

    def cancelled(self):
        """The window closing for any reason (Esc, Close, the backdrop, polkitd giving up): no, if not yet answered."""
        self.stop_session()
        if not self.answered:
            self.answered = True
            self.done(False)

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:   # also while typing the password (gtkkit leaves that Esc to the view)
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.respond()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self.toggle_reveal()
            return True
        return False


class Dialog(ViewHost):
    """A request's window, a popup like every panel's (gtkkit.ViewHost)."""
    def __init__(self, view, app, size=(560, 420)):
        self.view, self.app, view.host = view, app, self
        self.backdrop = self.make_backdrop() if view.backdrop else None
        self.build_window(app, size)
        self.win.connect("close-request", lambda *_: view.cancelled() or False)
        self.win.connect("unrealize", lambda *_: GLib.timeout_add_seconds(1, lambda: collect() and False))
        self.present()


def session_subject():
    """This login session, the one the agent answers for. The service runs outside the session's scope, so
    polkit can't tell from its PID: XDG_SESSION_ID (Hyprland's environment, imported at start), else logind's
    graphical session for this user."""
    sid = os.environ.get("XDG_SESSION_ID")
    if not sid:
        sid = subprocess.run(["loginctl", "show-user", str(os.getuid()), "-p", "Display", "--value"],
                             capture_output=True, text=True).stdout.strip()
    if sid:
        return Polkit.UnixSession.new(sid)
    return Polkit.UnixSession.new_for_process_sync(os.getpid(), None)


class Service(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="panels.polkit", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.views = {}   # cookie → Auth

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()   # a service: it stays for the next request
        install_css(Auth.css)
        # kept: dropped, the connection closed a moment later, polkitd forgot the agent, and pkexec asked in the
        # terminal instead (the shared system bus connection, the one polkit's client uses too)
        self.conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.conn.register_object_with_closures2(PATH, AGENT, self.call, None, None)
        self.authority = Polkit.Authority.get_sync(None)
        # fails if another agent has this session (hyprpolkitagent still running): exit, systemd says why
        self.authority.register_authentication_agent_sync(session_subject(), os.environ.get("LANG", "C"), PATH, None)
        print("polkitgui: the authentication agent for this session", flush=True)

    def do_activate(self):
        pass

    def call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "CancelAuthentication":   # polkitd gave up on it (the caller went away)
            view = self.views.get(params.unpack()[0])
            if view:
                view.cancel()
            invocation.return_value(None)
            return
        action_id, message, _icon, details, cookie, identities = params.unpack()
        print(f"polkitgui: {action_id}", flush=True)

        def done(ok):
            self.views.pop(cookie, None)
            print(f"polkitgui: {action_id}: {'authenticated' if ok else 'cancelled'}", flush=True)
            if ok:
                invocation.return_value(None)
            else:
                invocation.return_dbus_error(CANCELLED, "Cancelled by the user")
        view = Auth(action_id, message, details, cookie, identities, done)
        self.views[cookie] = view
        Dialog(view, self)


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    Service().run([sys.argv[0]])


if __name__ == "__main__":
    main()
