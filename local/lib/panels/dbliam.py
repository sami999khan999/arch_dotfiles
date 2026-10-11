#!/usr/bin/env python3
# dbliam.py — the database client's schema diagram (dbgui.py: Diagram, or d, on a Postgres connection,
# schema or table, or a SQLite file): Liam ERD (liambx.com, open source) in a web view inside the panel.
# dblib fetches Liam's web app once (pinned, hash-checked) and each driver's liam() writes the schema.json
# it reads; this hands both to the view from memory, through its own dbliam: scheme (no server, nothing
# on the network), and restyles Liam in the panels' colours.
#
#   the bar   the schema picker; search (/): only the tables whose name has it (just that one on an exact
#             name); Related (r): the tables they join too, by a key or by a column that only stores their
#             id (guessed from its name and the key's type; such a key is named "guessed: …"); Close (Esc)
#   in Liam   drag, zoom, a click shows a table's columns and keys, Show: names / keys / all fields, Export
#
# The view (a WebKit process, ~150 MB) is made when the diagram opens and dropped when it closes.
import json, mimetypes, os

from gtkkit import Gdk, Gio, GLib, Gtk, box, button, dropdown, label, recolor
import gi
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
except (ValueError, ImportError):   # webkitgtk-6.0 not installed (setup/packages.txt has it)
    WebKit = None

SCHEME = "dbliam"
# Liam's colour tokens as the panels' (recolor() makes them the active theme's), square corners, our font.
# !important: a stylesheet the view adds is a user one, and an author's (Liam's own) beats it without
STYLE = """
:root {
  --global-background: #1a1b26 !important; --global-muted-background: #16161e !important; --global-foreground: #c0caf5 !important;
  --global-border: #3b4261 !important; --global-body-text: #c0caf5 !important; --global-mute-text: #a9b1d6 !important;
  --pane-background: #16161e !important; --pane-muted-background: #1a1b26 !important; --pane-border: #3b4261 !important;
  --pane-border-hover: #565f89 !important; --pane-border-active: #565f89 !important; --pane-background-hover: #292e42 !important;
  --pane-background-active: #292e42 !important;
  --node-background: #1a1b26 !important; --node-accent-background: #292e42 !important; --node-border: #3b4261 !important;
  --node-muted-border: #292e42 !important; --node-foreground: #c0caf5 !important; --node-accent-foreground: #a9b1d6 !important;
  --node-selected: #6b8fe0 !important; --node-layout: #6b8fe0 !important;
  --primary-accent: #6b8fe0 !important; --primary-color: #6b8fe0 !important; --primary-hover: #7aa2f7 !important; --accent: #6b8fe0 !important;
  --primary-overlay-100: #6b8fe0 !important; --primary-overlay-40: rgba(107, 143, 224, .4) !important;
  --primary-overlay-20: rgba(107, 143, 224, .2) !important; --primary-overlay-10: rgba(107, 143, 224, .1) !important;
  --primary-accent-overlay: rgba(107, 143, 224, .1) !important; --primary-accent-overlay-hover: rgba(107, 143, 224, .2) !important;
  --button-background: #292e42 !important; --button-border: #3b4261 !important; --button-background-hover: #3b4261 !important;
  --button-primary-background: #6b8fe0 !important; --button-primary-background-hover: #7aa2f7 !important;
  --toggle-background-checked: #6b8fe0 !important; --dropdown-border: #3b4261 !important; --dropdown-background-hover: #292e42 !important;
  --xy-edge-stroke-default: #565f89 !important; --xy-edge-stroke-selected-default: #6b8fe0 !important;
  --xy-background-pattern-dots-color-default: #3b4261 !important; --xy-background-color-default: #1a1b26 !important;
}
* { border-radius: 0 !important; font-family: "JetBrainsMono Nerd Font", monospace !important; }
:root {   /* Liam's green, wherever it's a token */
  --primary-overlay-5: #6b8fe00d !important; --primary-overlay-12: #6b8fe01f !important;
  --primary-overlay-15: #6b8fe026 !important; --primary-overlay-35: #6b8fe059 !important;
  --primary-overlay-50: #6b8fe080 !important; --primary-overlay-60: #6b8fe099 !important;
  --primary-overlay-70: #6b8fe0b3 !important; --primary-overlay-80: #6b8fe0cc !important;
  --primary-overlay-90: #6b8fe0e6 !important; --primary-gradation-overlay-start: #6b8fe01f !important;
  --primary-gradation-default-start: #6b8fe0 !important; --primary-on-white-shape: #6b8fe0 !important;
  --button-ghost-secondary-background: #292e42 !important; --button-solid-inverse-foreground: #6b8fe0 !important;
}
/* The rules below name Liam's own classes (hashed per build: LIAM_VERSION pins which). */
/* the header: no logo, no "Liam ERD" (the bar above already says what this is) */
[class*="_logoWrapper_j2hiu"], h1[class*="_title_j2hiu"] { display: none !important; }
[class*="_wrapper_j2hiu"] { justify-content: flex-end !important; }
/* the canvas: the panels' dark, dots in the line colour (set inline by React Flow: !important) */
.react-flow__background { background-color: #16161e !important; }
.react-flow__background-pattern.dots { fill: #3b4261 !important; }
/* a table: the panels' card, a green header as the diagram had, no glow (Liam's is a green shadow) */
[class*="_wrapper_kvl10"] { background: #1a1b26 !important; border: 1px solid #3b4261b3 !important; box-shadow: none !important; }
[class*="_wrapperHighlighted_kvl10"], [class*="_wrapper_kvl10"]:hover { border: 1px solid #6b8fe0 !important; box-shadow: none !important; }
[class*="_wrapperActive_kvl10"], [class*="_wrapperActive_kvl10"]:hover { border: 2px solid #6b8fe0 !important; box-shadow: none !important; }
[class*="_wrapper_1p5e4"], [class*="_container_1p5e4"] { background: #9ece6a !important; }
[class*="_container_1p5e4"] { color: #1a1b26 !important; font-weight: 700 !important; }
/* its columns: names in the text colour, types always shown (Liam showed them on hover only), the key amber */
[class*="_wrapper_1iyaa"] { background: #1a1b26 !important; }
[class*="_wrapper_1iyaa"]:not(:last-child) { border-bottom: 1px solid #292e42 !important; }
[class*="_wrapper_1iyaa"]:hover { background: #292e42 !important; }
[class*="_highlightRelatedColumn_1iyaa"] { background: #6b8fe026 !important; }
[class*="_columnWrapper_1iyaa"] { color: #c0caf5 !important; }
[class*="_columnType_1iyaa"] { opacity: 1 !important; color: #565f89 !important; }
[class*="_primaryKeyIcon_1iyaa"] { color: #e0af68 !important; }
[class*="_linkIcon_1iyaa"] { color: #6b8fe0 !important; }
[class*="_diamondIcon_1iyaa"] { color: #a9b1d6 !important; }
"""


def guessed_keys(schema):
    """Columns that store another table's id without a foreign key: x_id (or published_version_id, by its
    last words) → the table x / xs / *_x whose single-column primary key has the same type; x_by → the user
    table. Only one candidate counts. [(table, column, target table, its key column)]."""
    tables = schema["tables"]
    pk, keyed = {}, set()
    for name, t in tables.items():
        for k in t["constraints"].values():
            if k["type"] == "PRIMARY KEY" and len(k["columnNames"]) == 1:
                pk[name] = k["columnNames"][0]
            elif k["type"] == "FOREIGN KEY":
                keyed |= {(name, c) for c in k["columnNames"]}
    users = next((n for n in ("user", "users", "account", "accounts") if n in pk), None)

    def base(typ):
        return typ.split("(")[0]
    out = []
    for name, t in tables.items():
        for c in t["columns"].values():
            n = c["name"]
            if (name, n) in keyed or pk.get(name) == n:
                continue
            if n.endswith("_id"):
                words, cands = n[:-3].split("_"), []
                while words and not cands:   # published_version → version: the longest that names one
                    b = "_".join(words)
                    cands = [x for x in (b, b + "s", b + "es", b.removesuffix("s")) if x in pk] or \
                        [x for x in pk if x.endswith("_" + b)]
                    words = words[1:]
                if len(cands) > 1:   # entry_id in content_translation: the content_ table
                    same = [x for x in cands if x.split("_")[0] == name.split("_")[0]]
                    cands = same if len(same) == 1 else cands
                cands = [x for x in cands if x != name]   # account.account_id: some other system's id
            elif n.endswith("_by") and users:
                cands = [users]
            else:
                continue
            if len(cands) == 1 and base(tables[cands[0]]["columns"][pk[cands[0]]]["type"]) == base(c["type"]):
                out.append((name, n, cands[0], pk[cands[0]]))
    return out


def narrowed(schema, text, related):
    """The schema with only what the search shows: the tables whose name has text (just the one named
    exactly that, if there is one) and, with related, the tables they join; with related, the guessed keys
    too. Keys to a table left out go with it."""
    tables = schema["tables"]
    guesses = guessed_keys(schema) if related else []
    names = set(tables)
    if text:
        exact = [n for n in tables if n.lower() == text]
        names = set(exact or [n for n in tables if text in n.lower()])
        if related:
            pairs = [(n, k["targetTableName"]) for n, t in tables.items() for k in t["constraints"].values()
                     if k["type"] == "FOREIGN KEY"] + [(g[0], g[2]) for g in guesses]
            names |= {b for a, b in pairs if a in names} | {a for a, b in pairs if b in names}
    out = {}
    for n in sorted(names):
        t = tables[n]
        keys = {k: v for k, v in t["constraints"].items()
                if v["type"] != "FOREIGN KEY" or v["targetTableName"] in names}
        for a, col, b, bcol in guesses:
            if a == n and b in names:
                name = f"guessed: {col} → {b}"
                keys[name] = {"type": "FOREIGN KEY", "name": name, "columnNames": [col], "targetTableName": b,
                              "targetColumnNames": [bcol], "updateConstraint": "NO_ACTION", "deleteConstraint": "NO_ACTION"}
        out[n] = {**t, "constraints": keys}
    return {**schema, "tables": out}


class Diagram(Gtk.Box):
    """The bar and, under it, Liam. on_close(); on_schema(name): another schema picked."""
    registered = False   # the dbliam: scheme, once per process (WebKit's default context)
    current = None       # the Diagram the scheme serves

    def __init__(self, on_close, on_schema):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.on_close, self.on_schema = on_close, on_schema
        self.schema, self.json, self.app, self.focus = None, b"{}", None, None
        self.view = None
        self.schemas = box(False, 8)
        self.search = Gtk.SearchEntry(placeholder_text="find a table  /", width_chars=16)
        self.search.connect("search-changed", lambda *_: self.load())
        self.search.connect("stop-search", lambda *_: self.search.set_text(""))
        self.related = Gtk.ToggleButton(label="Related", tooltip_text="the tables these join too, by a key or by a "
                                        "column that stores their id (r; such a key is named \"guessed: …\")")
        self.related.add_css_class("flat")
        self.related.add_css_class("dg-toggle")
        self.related.connect("toggled", lambda *_: self.load())
        self.count = label("", "dim")
        bar = box(False, 8, self.schemas, self.search, self.related, self.count, Gtk.Box(hexpand=True),
                  button("Close", on_close, "flat", tooltip="Esc"))
        bar.add_css_class("dg-bar")
        self.append(bar)
        self.holder = box(True, 0)
        self.holder.set_vexpand(True)
        self.append(self.holder)

    # ---- the view ------------------------------------------------------------------------------------
    @classmethod
    def register(cls):
        if cls.registered:
            return
        ctx = WebKit.WebContext.get_default()
        ctx.register_uri_scheme(SCHEME, lambda req: cls.current and cls.current.serve(req))
        sec = ctx.get_security_manager()
        sec.register_uri_scheme_as_secure(SCHEME)        # its module script and fetch() load as from https
        sec.register_uri_scheme_as_cors_enabled(SCHEME)
        cls.registered = True

    def serve(self, req):
        """dbliam://app/<path>: Liam's files from its folder, schema.json from memory."""
        path = req.get_path().lstrip("/") or "index.html"
        if path == "schema.json":
            data, mime = self.json, "application/json"
        else:
            full = os.path.realpath(os.path.join(self.app, path))
            if not full.startswith(os.path.realpath(self.app) + os.sep) or not os.path.isfile(full):
                return req.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), f"no {path}",
                                                               Gio.IOErrorEnum.NOT_FOUND))
            with open(full, "rb") as f:
                data = f.read()
            mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        req.finish(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)), len(data), mime)

    def make_view(self):
        self.register()
        content = WebKit.UserContentManager()
        content.add_style_sheet(WebKit.UserStyleSheet(recolor(STYLE), WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                                      WebKit.UserStyleLevel.USER, None, None))
        view = WebKit.WebView(user_content_manager=content, hexpand=True, vexpand=True)
        view.set_background_color(Gdk.RGBA(*[c / 255 for c in bytes.fromhex(recolor("#1a1b26")[1:])], 1))
        view.connect("decide-policy", self.decide)
        keys = Gtk.EventControllerKey()   # Esc closes (Liam's own popups take theirs first)
        keys.connect("key-pressed", lambda _c, k, _code, _s: k == Gdk.KEY_Escape and (self.on_close() or True))
        view.add_controller(keys)
        return view

    def decide(self, _view, decision, kind):
        """Liam's links out (GitHub, its docs) open in the browser; the view only shows Liam."""
        if kind in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith(SCHEME + ":"):
                decision.ignore()
                Gio.AppInfo.launch_default_for_uri(uri, None)
                return True
        return False

    def drop(self):
        """Closed: the view and its WebKit process go (it's made again on the next open)."""
        if self.view:
            self.holder.remove(self.view)
            self.view.terminate_web_process()
            self.view = None

    # ---- what it shows ---------------------------------------------------------------------------------
    def show(self, app, schema, schemas=(), current=None, focus=None):
        """app: Liam's folder (dblib.liam_app()); schema: a driver's liam(); schemas: the picker's choices
        (none: no picker, SQLite); focus: a table to centre on."""
        if WebKit is None:
            raise RuntimeError("the diagram needs WebKitGTK: sudo pacman -S webkitgtk-6.0")
        self.app, self.schema, self.focus = app, schema, focus
        for w in list(self.schemas):
            self.schemas.remove(w)
        self.schemas.set_visible(bool(schemas))
        if schemas:
            self.schemas.append(label("schema", "dim"))
            self.schemas.append(dropdown([(s, s) for s in schemas], current, self.on_schema))
        self.search.set_text("")
        if not self.view:
            self.view = self.make_view()
            self.holder.append(self.view)
        Diagram.current = self
        self.load()
        self.view.grab_focus()

    def load(self):
        if not self.view or not self.schema:
            return
        text = self.search.get_text().strip().lower()
        shown = narrowed(self.schema, text, self.related.get_active())
        self.json = json.dumps(shown).encode()
        n, total = len(shown["tables"]), len(self.schema["tables"])
        keys = sum(k["type"] == "FOREIGN KEY" for t in shown["tables"].values() for k in t["constraints"].values())
        self.count.set_text(f"{n} of {total} tables · {keys} keys" if text else f"{n} tables · {keys} keys")
        focus = text if text in shown["tables"] else self.focus if self.focus in shown["tables"] else ""
        query = GLib.Uri.escape_string(focus, None, False) if focus else ""
        self.view.load_uri(f"{SCHEME}://app/index.html?showMode=ALL_FIELDS" + (f"&active={query}" if query else ""))

    def key_pressed(self, keyval):
        """From dbgui's key handler while the diagram shows and the view hasn't the keyboard."""
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.on_close()
        elif keyval == Gdk.KEY_slash:
            self.search.grab_focus()
        elif keyval == Gdk.KEY_r:
            self.related.set_active(not self.related.get_active())
        else:
            return False
        return True
