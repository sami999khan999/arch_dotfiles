#!/usr/bin/env python3
# envgui.py — .env files (Super + Ctrl + K): every project's .env, its keys, what its .env.example has that
# it lacks, and its encrypted copy for the other PC. Running it again closes it. The Projects panel's .env tab
# is the same thing for the project it shows (EnvEditor).
#
#   left    the projects with a .env (or an example and no .env yet, or a copy only the other PC made); a
#           dot per project: green synced, amber something to push / pull or keys missing, red changed on both
#   right   its files: each with its state and what to do (Push, Pull, keep this PC's / take the other's when
#           both changed), its keys (values hidden; the eye shows one) — edit and Save writes the file in
#           place; a key the example has and the file lacks is in red, filled in and saved like the rest.
#           An example with no .env beside it: Create from the example.
#   Sync    push what changed here, pull what's newer there (a file changed on both PCs waits for you)
#
# The copies are the `projects env` ones: gpg (AES-256) in the private list repo, the passphrase in this PC's
# keyring (asked once, here or in the Projects tab). The data and the sync are the `projects` command's.
import importlib.machinery, importlib.util, os, shutil, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, run, scrolled

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)

EYE = ("\uf06e", "\uf070")   # nf-fa-eye / eye_slash: show a value, hide it again
STATE_CLASS = {"synced": "green", "changed here": "amber", "newer there": "amber", "not synced": "amber",
               "only there": "amber", "both changed": "red", "": "dim"}
STATE_WORDS = {"synced": "synced with the other PC", "changed here": "changed here: push it",
               "newer there": "newer on the other PC: pull it", "not synced": "not on the other PC yet: push it",
               "only there": "only on the other PC: pull it", "both changed": "changed on both PCs: pick one",
               "": "no passphrase yet"}

CSS = """
.env-list > row { padding: 6px 12px; }
.env-file { padding: 12px 0 6px 0; border-top: 1px solid alpha(#3b4261, .7); }
.env-file.first { border-top: none; padding-top: 0; }
.env-path { font-weight: 700; }
.env-key { color: #a9b1d6; min-width: 220px; }
.env-key.missing { color: #f7768e; }
.env-grid entry { min-height: 0; padding: 2px 8px; }
/* a hidden value's dots in the muted colour, not the text's white; shown (the eye), it's text again */
.env-grid entry text { color: #565f89; }
.env-grid entry.shown text { color: #c0caf5; }
.env-eye { min-width: 0; min-height: 0; padding: 0 6px; background: transparent; color: #565f89; }
.env-eye:hover { color: #c0caf5; background: transparent; }
.env-eye.on { color: #6b8fe0; }
.env-banner { padding: 10px 12px; border: 1px solid #e0af68; }
"""


def project_state(rel):
    """(status rows, examples without a .env) of one project: what EnvEditor.view shows."""
    path = f"{pj.ROOT}/{rel}"
    return pj.env_status([rel]), (pj.env_examples(path) if os.path.isdir(path) else [])


class EnvEditor:
    """One project's .env files as widgets, with their actions: the .env panel's right side and the Projects
    panel's .env tab. say(msg, kind): the host's status line; changed(rel): after a write or a sync, so the
    host loads the project again."""

    def __init__(self, say, changed):
        self.say, self.changed = say, changed
        self.edits = {}   # (rel, file) -> {key: (Gtk.Entry, value read)} of the shown project

    def banner(self, done):
        """The passphrase prompt (None when it's set): done() after it's kept."""
        if pj.passphrase():
            return None
        first = not os.path.isdir(pj.ENV_DIR)
        b = box(True, 8, classes=("env-banner",))
        b.append(label("Set the passphrase the copies are encrypted with" + (
            " (new: the other PC needs the same one)" if first else " (the one you set on the other PC)") +
            ". It stays in this PC's keyring.", wrap=True))
        one = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="passphrase", hexpand=True)
        two = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="again", hexpand=True)

        def go(*_):
            a, again = one.get_text(), two.get_text() if first else one.get_text()
            if not a or a != again:
                return self.say("the two don't match" if a else "type a passphrase", "bad")
            problem = pj.set_passphrase(a)
            if problem:
                return self.say(problem, "bad")
            self.say("passphrase kept in the keyring")
            done()
        one.connect("activate", go)
        two.connect("activate", go)
        b.append(box(False, 8, one, *([two] if first else []), button("Set", go, "primary")))
        return b

    def view(self, rel, status, examples):
        """The project's files (status: its env_status rows) and the examples with no .env beside them."""
        self.edits = {}
        page = box(True, 4)
        page.set_margin_end(14)   # the eyes clear of the scrollbar (an overlay: it widens over them on hover)
        path = f"{pj.ROOT}/{rel}"
        first = True
        for st in status:
            page.append(self.file_block(path, st, first))
            first = False
        for ex in examples:
            target = os.path.join(os.path.dirname(ex), ".env")
            n = len(pj.env_keys(open(os.path.join(path, ex), errors="replace").read()))
            head = box(False, 10, label(target, "env-path"), label(f"no such file · {ex} has {n} keys", "amber"),
                       label(""), button("Create from the example", lambda ex=ex, t=target: self.create(rel, ex, t), "primary"))
            head.get_last_child().get_prev_sibling().set_hexpand(True)
            page.append(box(True, 6, head, classes=("env-file",) + (("first",) if first else ())))
            first = False
        if first:
            page.append(label("No .env files in this project (nor an .env.example).", "dim"))
        return page

    def file_block(self, path, st, first):
        f, state = st["file"], st["state"]
        acts = []
        if state in ("changed here", "not synced"):
            acts.append(button("Push", lambda: self.job(st["project"], pj.env_push, [st["key"]]), "primary"))
        if state in ("newer there", "only there"):
            acts.append(button("Pull", lambda: self.job(st["project"], pj.env_pull, [st["key"]]), "primary"))
        if state == "both changed":
            acts += [button("Keep this PC's", lambda: self.job(st["project"], pj.env_push, [st["key"]], True)),
                     button("Take the other's", lambda: self.job(st["project"], pj.env_pull, [st["key"]], True))]
        here = os.path.isfile(os.path.join(path, f))
        if here:
            acts.append(button("Save", lambda: self.save(st["project"], f), "flat", tooltip="Ctrl+S"))
        head = box(False, 10, label(f, "env-path"), label(STATE_WORDS[state], STATE_CLASS[state]), label(""), *acts)
        head.get_first_child().get_next_sibling().get_next_sibling().set_hexpand(True)
        block = box(True, 8, head, classes=("env-file",) + (("first",) if first else ()))
        if not here:
            return block
        grid = Gtk.Grid(column_spacing=12, row_spacing=4, css_classes=["env-grid"])
        values = pj.env_keys(open(os.path.join(path, f), errors="replace").read())
        entries = {}
        for i, (k, v) in enumerate(list(values.items()) + [(k, None) for k in st["missing"]]):
            grid.attach(label(k, "env-key", *(("missing",) if v is None else ())), 0, i, 1, 1)
            e = Gtk.Entry(visibility=False, hexpand=True)
            e.set_text(v or "")
            entries[k] = (e, v)
            # the eye: a glyph of the panels' font (GTK's own peek icon came from the icon theme, in its colours)
            eye = Gtk.Button(label=EYE[0], css_classes=["env-eye"], focusable=False, tooltip_text="show / hide the value")
            eye.connect("clicked", lambda b, e=e: self.peek(e, b))
            grid.attach(eye, 2, i, 1, 1)
            if v is None:
                e.set_property("placeholder-text", "in the example, not here: fill in and Save")
            grid.attach(e, 1, i, 1, 1)
        self.edits[(st["project"], f)] = entries
        block.append(grid)
        return block

    @staticmethod
    def peek(entry, eye):
        shown = not entry.get_visibility()
        entry.set_visibility(shown)
        eye.set_label(EYE[shown])
        for w in (entry, eye):
            (w.add_css_class if shown else w.remove_css_class)("shown" if w is entry else "on")

    def save(self, rel, f):
        """The changed values (and filled-in missing keys) written into the file, each line in place."""
        changed = 0
        for k, (e, old) in self.edits.get((rel, f), {}).items():
            new = e.get_text()
            if new != (old or ""):
                pj.env_set(f"{pj.ROOT}/{rel}", f, k, new)
                changed += 1
        self.say(f"saved {changed} value{'s' * (changed != 1)} in {f}" if changed else "nothing changed")
        self.changed(rel)

    def save_all(self):
        for rel, f in list(self.edits):
            self.save(rel, f)

    def create(self, rel, example, target):
        dest = f"{pj.ROOT}/{rel}/{target}"
        if os.path.exists(dest):
            return self.say(f"{target} is already there", "bad")
        shutil.copyfile(f"{pj.ROOT}/{rel}/{example}", dest)
        os.chmod(dest, 0o600)
        self.say(f"made {target} from {example}: fill in its values")
        self.changed(rel)

    def job(self, rel, fn, keys, force=False):
        self.say("working…", seconds=60)

        def work():
            ok, msg = fn(keys=keys, force=force)
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad"), self.changed(rel)) and False)
        threading.Thread(target=work, daemon=True).start()


class EnvFiles(View):
    title, subtitle = ".env files", "every project's, synced encrypted"
    icon = ""   # nf-fa-key
    interval = 0
    css = CSS
    hints = [("↑↓", "project"), ("Ctrl+S", "save"), ("s", "sync"), ("/", "search"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.status, self.examples, self.sel = [], {}, None
        self.editor = EnvEditor(self.say, lambda rel: self.load(keep=rel))

    def header_extra(self):
        self.sync_btn = button("Sync", self.sync, "flat", tooltip="s: push what changed here, pull what's newer there")
        return [self.sync_btn]

    def build(self):
        self.search = Gtk.SearchEntry(placeholder_text="search  (/)")
        self.search.connect("search-changed", lambda *_: self.paint_list())
        self.search.connect("stop-search", lambda e: (e.set_text(""), self.list.grab_focus()))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("env-list")
        self.list.connect("row-selected", lambda _l, row: row and row.rel and self.pick(row.rel))
        left = box(True, 8, self.search, scrolled(self.list))
        left.set_size_request(280, -1)
        self.banner = box(True, 0)
        self.detail = box(True, 4)
        right = box(True, 10, self.banner, scrolled(self.detail))
        right.set_hexpand(True)
        page = box(False, 18, left, right)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(16)
        self.load()
        GLib.idle_add(lambda: self.list.grab_focus() and False)
        return page

    # ---- data --------------------------------------------------------------------------------------
    def load(self, keep=None):
        def work():
            status = pj.env_status()
            examples = {}
            for e in pj.load()["projects"] + pj.load()["local_only"]:
                path = f"{pj.ROOT}/{e['path']}"
                if os.path.isdir(path) and (ex := pj.env_examples(path)):
                    examples[e["path"]] = ex
            GLib.idle_add(self.loaded, status, examples, keep)
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, status, examples, keep):
        self.status, self.examples = status, examples
        clear(self.banner)
        b = self.editor.banner(self.load)
        if b:
            self.banner.append(b)
        self.sync_btn.set_sensitive(bool(pj.passphrase()))
        self.paint_list(keep or self.sel)
        return False

    def projects(self):
        rels = sorted({s["project"] for s in self.status} | set(self.examples))
        q = self.search.get_text().strip().casefold()
        return [r for r in rels if q in r.casefold()]

    def worst(self, rel):
        states = [s["state"] for s in self.status if s["project"] == rel]
        missing = any(s["missing"] for s in self.status if s["project"] == rel) or rel in self.examples
        if "both changed" in states:
            return "red"
        if any(st not in ("synced", "") for st in states) or missing:
            return "amber"
        return "green" if states and all(st == "synced" for st in states) else "dim"

    # ---- drawing -----------------------------------------------------------------------------------
    def paint_list(self, keep=None):
        clear(self.list)
        pick = None
        for rel in self.projects():
            row = Gtk.ListBoxRow()
            row.rel = rel
            n_missing = sum(len(s["missing"]) for s in self.status if s["project"] == rel)
            note = f"{n_missing} missing" if n_missing else ("no .env" if rel in self.examples and
                                                             not any(s["project"] == rel for s in self.status) else "")
            row.set_child(box(False, 8, label("●", self.worst(rel)), label(rel, ellipsize=True), label(note, "dim")))
            self.list.append(row)
            if rel == keep:
                pick = row
        if not self.projects():
            empty = Gtk.ListBoxRow(selectable=False)
            empty.rel = None
            empty.set_child(label("No .env files" if not self.search.get_text() else "No project matches", "dim"))
            self.list.append(empty)
        self.list.select_row(pick or self.list.get_row_at_index(0))

    def pick(self, rel):
        self.sel = rel
        clear(self.detail)
        self.detail.append(label(rel, "heading"))
        self.detail.append(label(f"{pj.ROOT}/{rel}".replace(os.path.expanduser("~"), "~"), "dim", ellipsize=True))
        self.detail.append(self.editor.view(rel, [s for s in self.status if s["project"] == rel],
                                            self.examples.get(rel, [])))

    def sync(self):
        if not pj.passphrase():
            return self.say("set the passphrase first", "bad")
        self.say("syncing…", seconds=60)

        def work():
            ok1, m1 = pj.env_push()
            ok2, m2 = pj.env_pull()
            GLib.idle_add(lambda: (self.say(f"{m1}; {m2}", "ok" if ok1 and ok2 else "bad"),
                                   self.load(keep=self.sel)) and False)
        threading.Thread(target=work, daemon=True).start()

    def leave_text(self):
        self.list.grab_focus()

    def key(self, keyval, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.editor.save_all()
            return True
        if self.typing():
            return False
        if keyval == Gdk.KEY_slash:
            self.search.grab_focus()
        elif keyval == Gdk.KEY_s:
            self.sync()
        else:
            return False
        return True


def make():
    return EnvFiles()


def main():
    run(make(), "panels.env", (1000, 640))


if __name__ == "__main__":
    main()
