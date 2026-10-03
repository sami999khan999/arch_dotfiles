#!/usr/bin/env python3
# agentmuxgui.py — agentmux's settings (GTK 4, laid out like System Settings, Super + I). Ctrl+Alt+S in
# agentmux (`agentmux settings`) opens it; running it again closes it. Every shortcut (remappable: click
# it, press the new keys) and every option, saved in config/agentmux/settings.json (lib.SETTINGS) and
# applied at once: `agentmux apply-keys` rebinds the keys, the status bar and the help line follow.
#
#   type  search     ↑↓  sections     Esc  cancel the key being recorded / clear / close
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/.local/lib/agentmux"))
from gtkkit import Gdk, GLib, Gtk, View, box, button, label, rule_heading, run, scrolled, setting_row, switch
import keys
import lib

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
DEFAULT_KEYS = {a: k for a, _, _, k in lib.ACTIONS}
DEFAULT_OPTS = {o: d for o, _, _, d in lib.OPTIONS}
# the shortcuts page, in groups: (heading, action ids)
GROUPS = [
    ("Focus", ["focus-projects", "focus-threads", "focus-thread", "focus-terminals", "focus-next"]),
    ("Threads & projects", ["new-thread", "close-thread", "open-project"]),
    ("Terminals", ["new-terminal", "split-terminal", "next-terminal", "previous-terminal", "close-terminal"]),
    ("Columns", ["toggle-projects", "toggle-threads", "toggle-terminals", "resize-left", "resize-right"]),
    ("agentmux", ["settings", "reload", "help"]),
]
EXPLAIN = {   # the dim line under a shortcut
    "focus-terminals": "Opens the column first if it's closed (the same for Projects and Threads)",
    "focus-next": "Left to right: Projects, Threads, the thread, the terminals, round again",
    "close-thread": "The selected project or thread in a sidebar, else the shown thread; asks under its row first",
    "new-terminal": "Full height, listed at the right of the column, like VS Code's terminals",
    "split-terminal": "The shown terminal in two, stacked; each half is its own row in the list",
    "close-terminal": "The focused shell (one half of a split, else the terminal); the last one closes the column",
    "toggle-terminals": "Hiding the column keeps its shells running",
    "reload": "Configs read again, every pane restarted; agents and shells keep running",
    "help": "The list in the status bar",
}
LABEL = {a: label_ for a, label_, _, _ in lib.ACTIONS}
MODIFIER_KEYS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Super_L", "Super_R",
                 "Meta_L", "Meta_R", "ISO_Level3_Shift", "Hyper_L", "Hyper_R", "Caps_Lock"}
GDK_NAMES = {"Return": "enter", "KP_Enter": "enter", "BackSpace": "backspace", "Delete": "delete",
             "Insert": "insert", "Page_Up": "pageup", "Page_Down": "pagedown", "Home": "home", "End": "end",
             "Left": "left", "Right": "right", "Up": "up", "Down": "down", "Tab": "tab", "ISO_Left_Tab": "tab",
             "space": "space"}


def pressed_key(keyval, state):
    """A key press as agentmux's "ctrl+alt+k" text, or (None, why not)."""
    name = Gdk.keyval_name(keyval) or ""
    if state & Gdk.ModifierType.SUPER_MASK:
        return None, "Super shortcuts belong to Hyprland; use Ctrl / Alt"
    ctrl, alt = state & Gdk.ModifierType.CONTROL_MASK, state & Gdk.ModifierType.ALT_MASK
    shift = state & Gdk.ModifierType.SHIFT_MASK
    if name in GDK_NAMES:
        key = GDK_NAMES[name]
    elif name.startswith("F") and name[1:].isdigit():
        key = name.lower()
    else:
        ch = chr(Gdk.keyval_to_unicode(Gdk.keyval_to_lower(keyval)) or 0)
        if not ch.isprintable() or ch in "\0 ":
            return None, f"{name or 'That key'} can't be a shortcut"
        if not ch.isalpha():
            shift = False   # "?" is already the shifted key
        key = ch
    fkey = key.startswith("f") and key[1:].isdigit()
    if not alt and not fkey and not (ctrl and len(key) > 1):
        return None, "Add Alt: Ctrl + a letter (and plain keys) are the shell's and the agents' own"
    if ctrl and shift and len(key) == 1:
        return None, "Ctrl + Shift + a letter doesn't reach tmux; try Ctrl + Alt or Alt + Shift"
    mods = [m for m, on in (("ctrl", ctrl), ("alt", alt), ("shift", shift)) if on]
    return "+".join(mods + [key]), None


class Section:
    def __init__(self, key, icon, title, words, build):
        self.key, self.icon, self.title, self.words, self.build = key, icon, title, words, build


class AgentmuxSettings(View):
    title, subtitle = "agentmux", "settings"
    icon = "\U000f06a9"
    interval = 0
    hints = [("type", "search"), ("↑↓", "sections"), ("click a shortcut", "record a new one"), ("Esc", "close")]
    css = """
    .settings-side { min-width: 210px; border-right: 1px solid alpha(#c0caf5, .07); }
    .settings-side list > row { padding: 4px 14px; }
    .settings-side .side-icon { color: #6b8fe0; min-width: 18px; }
    .settings-page { padding: 6px 26px 20px 22px; }
    .page-title { font-size: 14pt; font-weight: 700; margin: 8px 0 2px 0; }
    button.keycap { color: #e0af68; min-width: 130px; font-weight: 700; }
    button.keycap.recording { background: #6b8fe0; color: #16161e; }
    .changed { color: #565f89; font-size: 9pt; }
    """

    def __init__(self):
        super().__init__()
        self.settings = lib.load_settings()
        self.pages, self.keycaps, self.defaults, self.changed = {}, {}, {}, {}
        self.recording = None
        self.sections = [
            Section("keys", "\uf11c", "Shortcuts", " ".join(LABEL.values()) + " keys remap keyboard focus",
                    self.page_keys),
            Section("notify", "\uf0f3", "Notifications", "notify alert needs permission question finished done "
                    "error exited quit desktop", self.page_notify),
            Section("projects", "\uf07b", "Projects", "resume session agents open project", self.page_projects),
        ]

    # ---- frame (as in System Settings) ---------------------------------------------------------
    def build(self):
        self.search = Gtk.Entry(placeholder_text="Search settings…")
        for edge in ("start", "end", "top"):
            getattr(self.search, f"set_margin_{edge}")(12 if edge != "top" else 10)
        self.search.connect("changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.first_match())

        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.connect("row-selected", self.selected)
        self.rows = {}
        for s in self.sections:
            row = Gtk.ListBoxRow(child=box(False, 10, label(s.icon, "side-icon"), label(s.title)))
            row.section = s
            self.rows[s.key] = row
            self.side.append(row)
        self.side.set_filter_func(self.matches)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True)
        left = box(True, 6, self.search, scrolled(self.side), classes=("settings-side",))
        self.side.select_row(self.rows["keys"])

        # recording a shortcut takes every key first, before Esc closes the window or a text field types
        grab = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        grab.connect("key-pressed", self.record)
        self.window.add_controller(grab)
        GLib.idle_add(lambda: self.search.grab_focus() and False)
        return box(False, 0, left, self.stack)

    def matches(self, row):
        q = self.search.get_text().strip()
        return not q or keys.fuzzy(q, row.section.title, row.section.words) is not None

    def filter(self):
        self.side.invalidate_filter()
        sel = self.side.get_selected_row()
        if sel is None or not self.matches(sel):
            self.first_match()

    def first_match(self):
        for s in self.sections:
            if self.matches(self.rows[s.key]):
                self.side.select_row(self.rows[s.key])
                return

    def selected(self, _list, row):
        if row is None:
            return
        s = row.section
        if s.key not in self.pages:
            content = box(True, 0, label(s.title, "page-title"), classes=("settings-page",))
            s.build(content)
            self.pages[s.key] = scrolled(content)
            self.stack.add_named(self.pages[s.key], s.key)
        self.stack.set_visible_child_name(s.key)

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and self.search.has_focus():
            visible = [self.rows[s.key] for s in self.sections if self.matches(self.rows[s.key])]
            cur = self.side.get_selected_row()
            i = visible.index(cur) if cur in visible else -1
            i = min(i + 1, len(visible) - 1) if keyval == Gdk.KEY_Down else max(i - 1, 0)
            if visible:
                self.side.select_row(visible[i])
            return True
        if keyval == Gdk.KEY_Escape and self.search.get_text():
            self.search.set_text("")
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    # ---- saving --------------------------------------------------------------------------------
    def save(self, said):
        lib.save_settings(self.settings)
        subprocess.Popen([AGENTMUX, "apply-keys"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.say(f"{said} · saved to config/agentmux/settings.json")

    # ---- Shortcuts -----------------------------------------------------------------------------
    def page_keys(self, p):
        for i, (heading, ids) in enumerate(GROUPS):
            p.append(rule_heading(heading, spaced=i > 0))
            for aid in ids:
                cap = button("", lambda a=aid: self.start_recording(a), "keycap",
                             tooltip="Click, then press the new shortcut")
                reset = button("Default", lambda a=aid: self.set_key(a, DEFAULT_KEYS[a]), "flat",
                               tooltip=lib.key_label(DEFAULT_KEYS[aid]))
                self.keycaps[aid], self.defaults[aid] = cap, reset
                p.append(setting_row(LABEL[aid], EXPLAIN.get(aid, ""), reset, cap))
                self.show_key(aid)
        p.append(rule_heading("All shortcuts", spaced=True))
        p.append(setting_row("Back to the defaults", "Every agentmux shortcut as it came",
                             button("Reset all", self.reset_all, "danger")))

    def show_key(self, aid):
        cap = self.keycaps[aid]
        if self.recording == aid:
            cap.set_label("Press the keys…")
            cap.add_css_class("recording")
        else:
            key = self.settings["keys"].get(aid)
            cap.set_label(lib.key_label(key) if key else "—")
            cap.remove_css_class("recording")
        self.defaults[aid].set_visible(self.settings["keys"].get(aid) != DEFAULT_KEYS[aid])

    def start_recording(self, aid):
        was, self.recording = self.recording, aid
        if was and was != aid:
            self.show_key(was)
        self.show_key(aid)
        self.say("Press the new shortcut (Esc cancels)")

    def record(self, _ctrl, keyval, _code, state):
        aid = self.recording
        if not aid:
            return False
        name = Gdk.keyval_name(keyval) or ""
        if name in MODIFIER_KEYS:
            return True   # still holding the modifiers
        if keyval == Gdk.KEY_Escape and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            self.recording = None
            self.show_key(aid)
            self.say("Not changed")
            return True
        text, why = pressed_key(keyval, state)
        if why:
            self.say(why, "bad")
            return True
        self.set_key(aid, lib.parse_key(text))
        return True

    def set_key(self, aid, key):
        if not key:
            return self.say("That isn't a key tmux knows", "bad")
        clash = next((a for a in LABEL if a != aid and self.settings["keys"].get(a) == key), None)
        if clash:
            return self.say(f"{lib.key_label(key)} is already “{LABEL[clash]}”: change that one first", "bad")
        self.settings["keys"][aid] = key
        self.recording = None
        self.show_key(aid)
        self.save(f"{LABEL[aid]}: {lib.key_label(key)}")

    def reset_all(self):
        self.settings["keys"] = dict(DEFAULT_KEYS)
        self.recording = None
        for aid in self.keycaps:
            self.show_key(aid)
        self.save("Every shortcut back to its default")

    # ---- options -------------------------------------------------------------------------------
    def option_row(self, oid, title, sub):
        def flipped(on):
            self.settings["options"][oid] = on
            self.save(f"{title}: {'on' if on else 'off'}")
        return setting_row(title, sub, switch(self.settings["options"][oid], flipped))

    def page_notify(self, p):
        p.append(rule_heading("Desktop notifications"))
        p.append(self.option_row("notify-needs", "Needs you",
                                 "Permission prompts, questions, plans to approve; stays until clicked"))
        p.append(self.option_row("notify-done", "Finished", "An agent's turn is over"))
        p.append(self.option_row("notify-error", "Error", "An agent stopped on an error (API, crash)"))
        p.append(self.option_row("notify-exited", "Agent quit", "The agent closed and its thread is a shell again"))
        p.append(label("Never for the thread you're looking at. Clicking one opens its thread.", "setting-sub",
                       wrap=True))

    def page_projects(self, p):
        p.append(rule_heading("Opening a project"))
        p.append(self.option_row("resume-on-open", "Resume its agents",
                                 "Every agent the project used comes back in its last session "
                                 "(claude, codex, opencode, agy); off: an empty project"))


def make():
    return AgentmuxSettings()


def main():
    run(make(), "panels.agentmux", (860, 620))


if __name__ == "__main__":
    main()
