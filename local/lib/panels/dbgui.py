#!/usr/bin/env python3
# dbgui.py — the database client (workspace 6, config/hypr/workspaces.conf): Postgres, Redis, MongoDB,
# S3 and SQLite, local or remote (directly or through an SSH tunnel). Tiled, so running it again just
# brings it up. The connections, drivers and tunnels are dblib.py's; this draws them.
#
#   left   the connections, grouped by project; open one (Enter / double-click) for its schemas → tables, keys,
#          databases → collections, buckets → folders → files. A green dot: connected. The search filters
#          what's open; the dropdown below it shows one kind of database only. The databases running in
#          Docker are added by themselves, in their compose project's group (looked for when the window
#          comes on screen: dblib.docker_scan). F2 renames a connection or a group, in place. Ctrl / Shift +
#          click picks several connections (move them to a group, delete them), Redis keys (expiry, delete)
#          or S3 files (delete) to change together.
#   right  the selected table / key / collection / file, a page at a time (« ‹ page › », the page size), the query box
#          (SQL, a Redis command, a Mongo filter or pipeline; Ctrl+Enter runs it or the selected part)
#          and the selected row in full. Full access: what you run applies at once. An S3 image (the file
#          picked in the tree, or a row of a bucket / folder's file list) shows beside the table.
#   rows   under the table: + New, Edit, Delete for what it shows (a table's rows, a collection's documents,
#          a key's fields / items / members, a folder's files), on one row or the several picked (the checkbox
#          beside each, All for the page; or Ctrl / Shift + click, Ctrl+A). Enter on a row edits it, Del deletes the picked ones (twice). The form
#          writes only the fields you change: a new row gets the database's defaults for the rest, an edit of
#          several sets those fields on each. Views and query results are read-only.
#   Diagram  (d, or Diagram on a Postgres connection / schema / table or a SQLite file) the schema's tables
#          and the keys between them, in Liam ERD (dbliam.py)
#   Ask    (a, or Ask in the header) an AI about this connection and what's selected: its own popup
#          (dbaskgui.py, dbagent.py), read-only on a connection of its own; "Put in query box" there sends
#          a query back here (the use-query action), and a write from it needs a second Run.
#
# Each connection has one worker thread (a slow server never freezes the window) and closes, with its
# tunnel, after dblib.IDLE seconds unused; nothing polls a database.
import json, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbagent
import dbliam
import dblib as db
from gtkkit import Gdk, Gio, GLib, Gtk, View, box, button, clear, dropdown, label, recolor, run, scrolled
from gi.repository import GObject

HERE = os.path.dirname(os.path.abspath(__file__))

INDENT = 16
GROUP_GLYPH = "\uf07b"   # nf-fa-folder: a group of your own
FOLD_GLYPHS = ("\uf054", "\uf078")   # a group's button: folded / open (nf-fa-chevron_right, chevron_down)
DOCKER_GLYPH = "\uf308"   # nf-linux-docker: a group or connection Docker made
SCAN_EVERY = 10   # seconds: the window coming on screen again looks in Docker at most this often
GLYPHS = {"schema": "", "database": "", "folder": "", "bucket": "", "table": "",
          "view": "", "collection": "", "object": "", "more": "…"}
KEY_GLYPH = ""   # a Redis key (its type is the dim note)
CSS = """
.db-tree > row { padding: 3px 10px; }
.db-glyph { min-width: 18px; color: #565f89; }
.db-conn { font-weight: 700; }
.db-conn.off { color: #565f89; }   /* a Docker database whose container isn't running */
/* a project's group: space and a thin line above, as the Docker panel's */
.db-tree > row.db-group-row { padding-top: 8px; margin-top: 6px; border-top: 1px solid alpha(#3b4261, .7); }
.db-tree > row.db-group-row.first { margin-top: 0; border-top: none; }
.db-group { color: #a9b1d6; font-weight: 700; }
.db-fold { min-width: 16px; min-height: 0; padding: 0 2px; border: none; background: transparent; color: #565f89;
           font-size: 8pt; }
.db-fold:hover { color: #c0caf5; background: transparent; }
entry.rename { font-size: 13pt; font-weight: 700; padding: 0 6px; min-height: 0; }
.db-live { color: #9ece6a; }
.db-err { color: #f7768e; }
.query, .query text { background: #16161e; color: #c0caf5; }
.query { border: 1px solid alpha(#3b4261, .7); padding: 6px 8px; font-family: "JetBrainsMono Nerd Font"; font-size: 10pt; }
.query:focus-within { border-color: #6b8fe0; }
.rowview, .rowview text { background: transparent; color: #a9b1d6; }
paned > separator { background: alpha(#3b4261, .7); background-image: none; min-height: 1px; margin: 0; padding: 0;
                    border: none; box-shadow: none; }   /* the table / row view divider (drag it) */
columnview, columnview listview { background: transparent; color: #c0caf5; }
columnview > header > button { background: transparent; border: none; box-shadow: none; color: #565f89; padding: 4px 8px; }
columnview listview > row { padding: 0; }
columnview listview > row:hover { background: alpha(#292e42, .35); }
columnview listview > row:selected { background: alpha(#292e42, .75); color: #c0caf5; }
columnview listview > row > cell { padding: 3px 8px; }
.form-label { color: #a9b1d6; min-width: 160px; }
.dg-bar { padding: 8px 20px; border-bottom: 1px solid alpha(#3b4261, .7); }   /* over Liam ERD */
/* while the diagram shows the window is solid, no blur behind: see-through, every frame of a pan or zoom
   was composited again over the blurred desktop (a third more per frame: ~45 ms instead of ~30) */
window.panel.solid { background: #16161e; backdrop-filter: none; }
.dg-toggle:checked { color: #6b8fe0; background: alpha(#292e42, .9); }
.db-preview { border-left: 1px solid alpha(#3b4261, .7); padding-left: 14px; }

.rec-field { min-height: 30px; }
.form-type { color: #565f89; font-size: 9pt; }

button.pager-btn { padding: 2px 10px; min-width: 0; font-size: 12pt; }
button.pager-btn:disabled { background: transparent; color: #3b4261; }
entry.page-entry { padding: 2px 4px; min-height: 0; }
"""


class Row(GObject.Object):
    """One result row (a ColumnView needs GObjects); key: the driver's, when the row can be changed."""
    __gtype_name__ = "DbguiRow"

    def __init__(self, cells, key=None):
        super().__init__()
        self.cells, self.key = cells, key


class Pager:
    """The pages of what the table shows: a browsed table / key / collection or a query's result.
    A table pages by offset, so any page can be jumped to; a Redis hash / set / stream pages by a cursor
    (starts[i]: where page i begins) and a query by its open cursor, so those go page by page (a query's
    pages are kept, going back doesn't run it again)."""

    def __init__(self, kind, cid, path, size):
        self.kind, self.cid, self.path, self.size = kind, cid, path, size
        self.index = 0             # the page shown
        self.starts = {0: 0}       # browse: page → where it starts (an offset, or a cursor / last id)
        self.lens = {}             # page → its number of rows (row numbers when pages aren't all `size`)
        self.seekable = True       # browse by offset: page n starts at n * size
        self.pages = []            # run: the pages fetched so far
        self.more = False          # the page shown has a next one
        self.total = None          # (rows, exact?) when cheap to know
        self.editor = None         # browse: how its rows are changed (a dblib.Editor), None: they aren't

    def first_row(self):
        if self.kind == "browse" and self.seekable:
            return self.index * self.size
        return sum(self.lens.get(i, 0) for i in range(self.index))

    def last_page(self):
        """The last page's index, when it can be jumped to."""
        if self.kind == "browse" and self.seekable and self.total and self.total[1]:
            return max(0, (self.total[0] - 1) // self.size)
        return None


SIZES = [50, 100, 200, 500, 1000]


class Live:
    """An open connection: its driver, its one worker thread, when it was last used."""

    def __init__(self):
        self.driver = None
        self.pool = ThreadPoolExecutor(1)
        self.used = time.time()
        self.jobs = 0


FIELD_MAX = 200   # px: a form field's text box grows to this (about ten lines), then scrolls


class FormField:
    """A field of a row's form: a text box (a long or multi-line value wraps; Tab moves on) and, for a
    column that can be one, NULL. touched: you changed it; only those are written."""

    def __init__(self, f, value, enabled=True):
        self.f, self.touched = f, False
        enabled = enabled and not f.fixed
        self.view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=4,
                                 bottom_margin=4, hexpand=True)
        self.view.add_css_class("query")
        self.view.add_css_class("rec-field")
        # in a box as tall as its text up to FIELD_MAX, then it scrolls: a wrapping text view measured before
        # the form has its width (a long name with no spaces, wrapped a letter a line) took the whole window
        self.widget = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                         max_content_height=FIELD_MAX, hexpand=True, valign=Gtk.Align.START)
        self.widget.set_child(self.view)
        buf = self.view.get_buffer()
        buf.set_text(value or "")
        self.null = None
        if f.null and enabled:
            self.null = Gtk.ToggleButton(label="NULL", active=value is None, valign=Gtk.Align.START,
                                         tooltip_text="NULL instead of what's typed")
            self.null.add_css_class("flat")
            self.null.add_css_class("dg-toggle")
            self.null.connect("toggled", self.nulled)
        self.view.set_sensitive(enabled and not (self.null and self.null.get_active()))
        buf.connect("changed", lambda *_: setattr(self, "touched", True))

    def nulled(self, b):
        self.touched = True
        self.view.set_sensitive(not b.get_active())

    def value(self):
        if self.null and self.null.get_active():
            return None
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


class Databases(View):
    title, subtitle = "Databases", "Postgres · Redis · MongoDB · S3 · SQLite"
    icon = ""
    interval = 5   # refresh(): only closes idle connections
    css = CSS
    hints = [("Enter", "open"), ("Ctrl+click", "pick several"), ("Ctrl+Enter", "run / save"), ("[ ]", "page"), ("a", "ask AI"), ("d", "diagram"), ("n", "new connection"), ("e", "edit"),
             ("F2", "rename"), ("F5", "reload"), ("Del", "delete")]

    def __init__(self):
        super().__init__()
        self.conns = db.load()
        self.live = {}           # id: Live
        self.errors = {}         # id: the last connect error (the row turns red)
        self.open = set()        # (id, path) unfolded in the tree
        self.kids = {}           # (id, path): [Node], fetched once per unfold (F5 again)
        self.sel = None          # (id, Node or None) on the right
        self.pager = None        # the pages of what the table shows
        self.size = db.PAGE      # rows a page
        self.queries = {}        # id: the query box's text, kept per connection
        self.armed = 0           # time Delete was pressed once
        self.gen = 0             # bumped when the right side changes: late results are dropped
        self.painting = False    # paint_tree() re-selecting the row: not a pick
        self.ai_queries = set()  # queries the AI suggested and you put in the query box
        self.confirm = None      # (text, time): Run pressed once on a write the AI suggested
        self.only = ""           # the one kind of database shown ("": every kind)
        self.folded = set()      # groups (projects) shown as just their row
        self.group = None        # the group picked in the list (its page on the right), or None
        self.docker_up = set()   # the Docker databases running ("docker" keys); the rest show dimmed
        self.scanned = 0         # when Docker was last looked in
        self.renaming = None     # ("conn", id) or ("group", name) while the name is being edited
        self.many = None         # several picked in the tree: ("conns" | "keys" | "files" | "none", [what])
        self.save_form = None    # the form showing's Save (Ctrl+Enter)

    def conn(self, cid):
        return next((c for c in self.conns if c["id"] == cid), None)

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        return [button("\U000f0674 Ask", self.ask, "flat", tooltip="ask an AI about this database (a)"),
                button("+ Connection", lambda: self.form(None), "flat", tooltip="n")]

    def build(self):
        self.filter = Gtk.Entry(placeholder_text="filter (Redis: Enter scans for it)", hexpand=True)
        self.filter.connect("changed", lambda *_: self.paint_tree())
        self.filter.connect("activate", lambda *_: self.scan_redis())
        self.kind = dropdown([("", "All types")] + [(k, f"{v[1]}  {v[0]}") for k, v in db.KINDS.items()], "",
                             lambda k: (setattr(self, "only", k), self.paint_tree()))
        self.kind.set_tooltip_text("show one kind of database only")
        # MULTIPLE: Ctrl / Shift + click picks several (picked_many); a plain click still picks one
        self.tree = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.tree.add_css_class("db-tree")
        self.tree.set_activate_on_single_click(False)
        self.tree.connect("row-selected", lambda _l, row: row and len(self.tree.get_selected_rows()) == 1 and (
            self.picked_group(row.group) if row.group is not None else self.picked(row.key)))
        self.tree.connect("selected-rows-changed", lambda _l: self.picked_many())
        self.tree.connect("row-activated", lambda _l, row: self.fold(row.group) if row.group is not None
                          else self.toggle(row.key))
        top = box(True, 6, self.filter, self.kind)
        for edge in ("top", "start", "end", "bottom"):
            getattr(top, f"set_margin_{edge}")(10)
        left = box(True, 0, top, scrolled(self.tree), classes=("side",))
        left.set_size_request(320, -1)
        left.set_hexpand(False)   # the filter's hexpand mustn't widen the side: the results get the room

        self.stack = Gtk.Stack(hexpand=True)
        self.stack.add_named(self.build_browse(), "browse")
        self.form_page = box(True, 0)
        # Ctrl+Enter saves a form, caught before its text boxes (they'd take it as a new line)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.form_key)
        self.form_page.add_controller(keys)
        self.stack.add_named(scrolled(self.form_page), "form")
        self.diagram = dbliam.Diagram(lambda: self.stack.set_visible_child_name("browse"),
                                      lambda s: self.show_diagram(self.diagram_cid, s))
        self.diagram_cid = None
        self.stack.add_named(self.diagram, "diagram")
        # the diagram's web view only lives while it shows (a WebKit process: ~150 MB); the window is solid then
        self.stack.connect("notify::visible-child-name", self.diagram_shown)
        self.paint_tree()
        self.show_empty()
        self.scan_docker()
        GLib.idle_add(lambda: self.tree.grab_focus() and False)
        use = Gio.SimpleAction.new("use-query", GLib.VariantType.new("s"))   # Ask's "Put in query box"
        use.connect("activate", self.use_query)
        self.host.app.add_action(use)
        return box(False, 0, left, self.stack)

    def build_browse(self):
        self.heading = label("", "heading", ellipsize=True)
        # F2 / Rename: the name edited in its place (Enter saves, Esc or a click elsewhere leaves it)
        self.rename_entry = Gtk.Entry(visible=False, css_classes=["rename"])
        self.rename_entry.connect("activate", lambda *_: self.end_rename(True))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, _code, _s: k == Gdk.KEY_Escape and self.end_rename(False) is None)
        self.rename_entry.add_controller(keys)
        away = Gtk.EventControllerFocus()
        away.connect("leave", lambda *_: self.end_rename(False))
        self.rename_entry.add_controller(away)
        self.state = label("", "dim", ellipsize=True)
        self.actions = box(False, 8)
        self.actions.set_margin_top(10)
        head = box(True, 2, self.heading, self.rename_entry, self.state, self.actions)

        self.query = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=2, bottom_margin=2)
        self.query.add_css_class("query")
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.query_key)
        self.query.add_controller(keys)
        self.query_hint = label("", "dim")
        self.run_btn = button("Run", self.run_query, "primary", tooltip="Ctrl+Enter")
        self.stop_btn = button("Stop", self.cancel, "danger", tooltip="cancel the running statement")
        qs = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        qs.set_size_request(-1, 76)   # a fixed few lines (a long query scrolls): the results keep the height
        qs.set_child(self.query)
        self.query_box = box(True, 6, qs, box(False, 8, self.query_hint, Gtk.Box(hexpand=True), self.stop_btn, self.run_btn))
        self.query_box.set_margin_top(14)

        self.store = Gio.ListStore(item_type=Row)
        # several rows can be picked (Ctrl / Shift + click, Ctrl+A) to edit or delete together
        self.selection = Gtk.MultiSelection(model=self.store)
        self.selection.connect("selection-changed", lambda *_: (self.show_row(), self.paint_rec()))
        self.table = Gtk.ColumnView(model=self.selection, reorderable=False, show_row_separators=False,
                                    show_column_separators=False)
        self.table.connect("activate", lambda _t, i: self.edit_rows([i]))   # Enter / double-click: edit it
        # a checkbox a row, first: ticking picks it without unpicking the others (a click picks one only).
        # Kept across fill()s (only the data columns are rebuilt); shown when the rows can be changed
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self.check_setup)
        f.connect("bind", self.check_bind)
        f.connect("unbind", lambda _f, item: item.binding.unbind())
        self.check_col = Gtk.ColumnViewColumn(title="", factory=f, visible=False)
        self.table.append_column(self.check_col)
        self.count = label("", "dim", ellipsize=True)
        self.nav = {}
        for name, text, tip in (("first", "\u00ab", "first page"), ("prev", "\u2039", "previous page ( [ )"),
                                ("next", "\u203a", "next page ( ] )"), ("last", "\u00bb", "last page")):
            self.nav[name] = button(text, lambda n=name: self.turn(n), "flat", "pager-btn", tooltip=tip)
        self.page_entry = Gtk.Entry(width_chars=4, max_width_chars=6, xalign=0.5, tooltip_text="go to page (Enter)")
        self.page_entry.add_css_class("page-entry")
        self.page_entry.connect("activate", lambda e: self.jump(e.get_text()))
        self.page_of = label("", "dim")
        sizes = dropdown([(n, f"{n} / page") for n in SIZES], self.size, self.resize)
        sizes.set_tooltip_text("rows a page")
        # + New, Edit, Delete for what the table shows (paint_rec: when it can be changed)
        self.rec_new = button("+ New", self.new_row, "flat")
        self.rec_edit = button("Edit", lambda: self.edit_rows(self.picked_rows()), "flat", tooltip="Enter")
        self.rec_del = button("Delete", self.delete_rows, "flat", "danger", tooltip="Del; click twice")
        self.check_all = Gtk.CheckButton(label="All", tooltip_text="pick every row on this page (Ctrl+A)")
        self.check_all.connect("toggled", self.all_toggled)
        self.check_all.set_margin_end(6)
        self.rec = box(False, 6, self.check_all, self.rec_new, self.rec_edit, self.rec_del)
        self.rec_armed = 0
        foot = box(False, 4, self.count, self.nav["first"], self.nav["prev"], self.page_entry, self.page_of,
                   self.nav["next"], self.nav["last"], sizes, Gtk.Box(hexpand=True), self.rec)
        sizes.set_margin_start(8)
        foot.set_margin_top(6)
        foot.set_margin_bottom(6)

        self.rowview = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    top_margin=8, bottom_margin=8)
        self.rowview.add_css_class("rowview")
        buf = self.rowview.get_buffer()
        buf.create_tag("key", foreground=recolor("#e0af68"))
        rs = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=110, visible=False)
        rs.set_child(self.rowview)
        self.rowpane = rs   # shown while a row is selected

        tbl = Gtk.ScrolledWindow(vexpand=True)
        tbl.set_child(self.table)
        split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=False, vexpand=True)
        split.set_start_child(box(True, 0, tbl, foot))
        split.set_end_child(rs)
        split.set_resize_end_child(False)
        split.set_shrink_end_child(False)   # the selected row stays in view under the table
        # ...at its minimum height: the table gets the rest. The position can only be set once the pane has
        # a size (set before, GTK kept the halfway default), and again whenever its range changes
        split.connect("notify::max-position", lambda p, _: p.set_position(p.props.max_position))
        self.results = split
        split.set_hexpand(True)

        # an S3 image, beside the table. A scrolled window that doesn't take its child's natural width:
        # a big image is scaled down into the pane instead of widening it
        self.preview = Gtk.Picture(content_fit=Gtk.ContentFit.SCALE_DOWN, can_shrink=True, vexpand=True)
        frame = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                   propagate_natural_width=False, vexpand=True)
        frame.set_size_request(380, -1)
        frame.set_child(self.preview)
        self.preview_note = label("", "dim", ellipsize=True)
        self.previewpane = box(True, 6, frame, self.preview_note)
        self.previewpane.add_css_class("db-preview")
        self.previewpane.set_visible(False)
        self.img_gen = 0
        body = box(False, 14, split, self.previewpane)
        body.set_margin_top(12)

        page = box(True, 0, head, self.query_box, body)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(20 if edge != "bottom" else 10)
        return page

    # ---- the tree --------------------------------------------------------------------------------
    def groups(self):
        """[(project, [connection])] as the list shows them: the projects by name, then the connections
        in none ("" — under "Other" when there are groups)."""
        named = {}
        for c in self.conns:
            if not self.only or c["kind"] == self.only:
                named.setdefault(c.get("project", ""), []).append(c)
        return sorted(named.items(), key=lambda g: (not g[0], g[0].lower()))

    def paint_tree(self):
        sel = self.tree.get_selected_rows()
        keep = (sel[0].group, sel[0].key) if len(sel) == 1 else None
        self.painting = True
        clear(self.tree)
        text = self.filter.get_text().strip().lower()
        groups = self.groups()
        headed = any(p for p, _ in groups)
        for i, (project, conns) in enumerate(groups):
            if headed:
                self.add_group_row(project, conns, i == 0)
                if project in self.folded:
                    continue
            for c in conns:
                cid = c["id"]
                self.add_row((cid, ()), int(headed), c)
                if (cid, ()) in self.open:
                    self.add_kids(cid, (), 1 + headed, text)
        if not groups:
            hint = Gtk.ListBoxRow(selectable=False, activatable=False)
            hint.set_child(label(f"No {db.KINDS[self.only][0]} connections" if self.conns else
                                 "No connections yet: + Connection (n)", "dim"))
            hint.key, hint.group = None, None
            self.tree.append(hint)
        if keep and keep != (None, None):
            self.select_key(*keep)
        self.painting = False

    def add_group_row(self, project, conns, first):
        row = Gtk.ListBoxRow(css_classes=["db-group-row"] + (["first"] if first else []))
        row.key, row.group = None, project or ""
        docker = all(c.get("docker") for c in conns)
        up = sum(c.get("docker") in self.docker_up for c in conns)
        note = f"{up}/{len(conns)} running" if any(c.get("docker") for c in conns) else str(len(conns))
        fold = button(FOLD_GLYPHS[project not in self.folded], lambda: self.fold(row.group), "db-fold",
                      tooltip="collapse / expand (Enter)")
        fold.set_focusable(False)
        row.set_child(box(False, 6, fold, label(DOCKER_GLYPH if docker and project else GROUP_GLYPH, "db-glyph"),
                          label(project or "Other", "db-group", ellipsize=True),
                          label(note, "dim", xalign=1.0, ellipsize=True)))
        row.get_child().get_last_child().set_hexpand(True)
        self.tree.append(row)

    def fold(self, project):
        """Enter / double-click on a group: show it as just its row, or open it again."""
        self.folded ^= {project}
        self.paint_tree()
        if self.group == project:
            self.picked_group(project)

    def add_kids(self, cid, path, depth, text):
        for n in self.kids.get((cid, path), []):
            # the filter shows matching leaves (and every folder, so a match deeper down stays reachable)
            if text and n.leaf and text not in n.name.lower():
                continue
            self.add_row((cid, n.path), depth, None, n)
            if (cid, n.path) in self.open:
                self.add_kids(cid, n.path, depth + 1, text)

    def add_row(self, key, depth, conn=None, node=None):
        row = Gtk.ListBoxRow()
        row.key, row.group = key, None
        line = box(False, 8)
        line.set_margin_start(depth * INDENT)
        if conn:
            kind = db.KINDS[conn["kind"]]
            glyph = label(kind[1], "db-glyph")
            name = label(conn["name"], "db-conn")
            if conn.get("docker"):   # where Docker publishes it; dimmed while its container is stopped
                port = conn.get("port") or conn.get("endpoint", "").rsplit(":", 1)[-1]
                up = conn["docker"] in self.docker_up
                note = label(f"{DOCKER_GLYPH} :{port}" if up else f"{DOCKER_GLYPH} stopped", "dim", xalign=1.0)
                if not up:
                    name.add_css_class("off")
            else:
                note = label(conn.get("ssh") and f"via {conn['ssh']}" or conn.get("host") or
                             os.path.basename(conn.get("path", "")) or conn.get("endpoint") or kind[0],
                             "dim", xalign=1.0, ellipsize=True)
            note.set_hexpand(True)
            dot = label("●" if conn["id"] in self.live else "", "db-live")
            if conn["id"] in self.errors:
                name.add_css_class("db-err")
            line.append(glyph), line.append(name), line.append(note), line.append(dot)
        else:
            g = GLYPHS.get(node.kind, KEY_GLYPH)
            if not node.leaf and node.kind != "more":
                g = "" if key in self.open else g
            name = label(node.name, *(["dim"] if node.kind == "more" else []), ellipsize=True)
            line.append(label(g, "db-glyph")), line.append(name)
            note = node.note or (node.kind if self.conn(key[0])["kind"] == "redis" and node.kind != "more" else "")
            if note:
                line.append(label(note, "dim"))
        row.set_child(line)
        self.tree.append(row)

    def select_key(self, group, key=None):
        """Select the row of a group (its project; None: a connection or node's row, by key)."""
        r = self.tree.get_first_child()
        while r is not None:
            if (group is not None and getattr(r, "group", None) == group) or \
                    (group is None and getattr(r, "key", None) == key):
                self.tree.unselect_all()   # a multiple selection's select_row adds to it
                self.tree.select_row(r)
                return
            r = r.get_next_sibling()

    def node_of(self, key):
        cid, path = key
        for (c, _p), nodes in self.kids.items():
            if c == cid:
                for n in nodes:
                    if n.path == path:
                        return n
        return None

    def toggle(self, key):
        """Enter / double-click: fold or unfold (connecting first), or fetch a "more" row's next page."""
        if not key:
            return
        cid, path = key
        node = self.node_of(key) if path else None
        if node and node.kind == "more":
            return self.more_kids(cid, node)
        if node and node.leaf:
            return self.picked(key)
        if key in self.open:
            self.open.discard(key)
            return self.paint_tree()
        self.open.add(key)
        if key in self.kids:
            return self.paint_tree()
        self.fetch_kids(cid, path)

    def fetch_kids(self, cid, path, then=None):
        def done(nodes):
            self.kids[(cid, path)] = nodes
            self.paint_tree()
            if then:
                then()
        self.job(cid, lambda d: d.children(path), done, fail=lambda: self.open.discard((cid, path)))

    def more_kids(self, cid, node):
        """The next page of keys / objects replaces the "more" row it came from."""
        parent = next(k for k, ns in self.kids.items() if k[0] == cid and node in ns)

        def done(nodes):
            self.kids[parent] = [n for n in self.kids[parent] if n is not node] + nodes
            self.paint_tree()
        self.job(cid, lambda d: d.children(node.path), done)

    def scan_redis(self):
        """Enter in the filter on a Redis connection: SCAN the server for keys containing it."""
        cid = self.sel[0] if self.sel else None
        c = self.conn(cid) if cid else None
        if not c or c["kind"] != "redis":
            return
        text = self.filter.get_text().strip()

        def done(nodes):
            self.kids[(cid, ())] = nodes
            self.open.add((cid, ()))
            self.filter.set_text("")
            self.paint_tree()
            self.say(f"{len([n for n in nodes if n.kind != 'more'])} keys matching *{text}*" if text else "all keys")
        self.job(cid, lambda d: d.children((), pattern=f"*{text}*" if text else "*"), done)

    # ---- connections: one worker thread each -----------------------------------------------------
    def job(self, cid, fn, done, fail=None):
        """fn(driver) on the connection's thread (connecting first if needed), then done(result) here."""
        c = self.conn(cid)
        live = self.live.get(cid)
        if not live:
            live = self.live[cid] = Live()
        live.used = time.time()
        live.jobs += 1
        self.set_busy(True)

        def work():
            try:
                if live.driver is None:
                    GLib.idle_add(lambda: self.say(f"connecting to {c['name']}…", seconds=20) and False)
                    live.driver = db.open_driver(c)
                    GLib.idle_add(self.connected, cid)
                out, err = fn(live.driver), None
            except Exception as e:   # a driver error, a lost connection, a missing library
                out, err = None, str(e) or type(e).__name__
            GLib.idle_add(finish, out, err)

        def finish(out, err):
            live.jobs -= 1
            live.used = time.time()
            self.set_busy(any(lv.jobs for lv in self.live.values()))
            if err:
                if live.driver is None:   # never connected: forget it, so the next try connects again
                    self.live.pop(cid, None)
                    live.pool.shutdown(wait=False)
                    self.errors[cid] = err
                    self.paint_tree()
                self.say(err.splitlines()[0][:300], "bad", 10)
                if fail:
                    fail()
            else:
                done(out)
            return False
        live.pool.submit(work)

    def connected(self, cid):
        self.errors.pop(cid, None)
        self.say(f"connected to {self.conn(cid)['name']}")
        self.paint_tree()
        return False

    def disconnect(self, cid):
        live = self.live.pop(cid, None)
        if live:
            def bye():
                if live.driver:
                    live.driver.close()
            live.pool.submit(bye)
            live.pool.shutdown(wait=False)
        for k in [k for k in self.kids if k[0] == cid]:
            del self.kids[k]
        self.open = {k for k in self.open if k[0] != cid}
        if self.pager and self.pager.cid == cid:
            self.pager = None
        self.paint_tree()
        # its page on the right, if that's what shows: Open again instead of Reload / Disconnect, no server line
        if self.sel == (cid, None) and self.conn(cid) and self.stack.get_visible_child_name() == "browse":
            self.picked((cid, ()))

    def refresh(self):
        now = time.time()
        for cid, live in list(self.live.items()):
            if not live.jobs and now - live.used > db.IDLE:
                self.disconnect(cid)

    # idle connections and their SSH tunnels close on time also while the workspace is hidden (refresh()
    # pauses then): it's a look at a few timestamps
    hidden_refresh = refresh

    def set_busy(self, busy):
        self.stop_btn.set_visible(busy and self.sel is not None and self.conn(self.sel[0])["kind"] in ("postgres", "sqlite"))

    def cancel(self):
        live = self.live.get(self.sel[0]) if self.sel else None
        if live and live.driver and hasattr(live.driver, "cancel"):
            live.driver.cancel()   # thread-safe for both: libpq's cancel request, sqlite's interrupt

    # ---- groups, Docker, renaming -----------------------------------------------------------------
    def picked_group(self, project):
        """A group's row: its page on the right (what's in it, Rename, Collapse)."""
        if self.painting:
            return
        if self.sel:
            self.queries[self.sel[0]] = self.query_text()
        self.sel, self.group, self.pager, self.many = None, project, None, None
        self.end_rename(False)
        self.gen += 1
        self.stack.set_visible_child_name("browse")
        conns = dict(self.groups()).get(project, [])
        docker = [c for c in conns if c.get("docker")]
        self.heading.set_text(project or "Other")
        what = f"{len(conns)} connection{'s' * (len(conns) != 1)}"
        if docker:
            up = sum(c["docker"] in self.docker_up for c in docker)
            what += f" · {len(docker)} from Docker, {up} running"
        self.state.set_text(what)
        self.query_box.set_visible(False)
        self.results.set_visible(False)
        self.previewpane.set_visible(False)
        clear(self.actions)
        if project:
            self.actions.append(button("Rename", lambda: self.start_rename(("group", project)), "primary", tooltip="F2"))
        self.actions.append(button("Expand" if project in self.folded else "Collapse", lambda: self.fold(project),
                                   tooltip="Enter"))

    def scan_docker(self, force=False):
        """Look in Docker for databases (in a thread): new ones join the list in their project's group, a
        known one gets its address and password again (one that changed while open connects again)."""
        if not force and time.time() - self.scanned < SCAN_EVERY:
            return
        self.scanned = time.time()

        def work():
            found = db.docker_scan()
            changed = db.docker_merge(found) if found is not None else []
            GLib.idle_add(done, found, changed)

        def done(found, changed):
            if found is None:
                return False
            new = {d["docker"] for d in found} - {c.get("docker") for c in self.conns}
            for cid in changed:
                self.disconnect(cid)
            self.docker_up = {d["docker"] for d in found}
            self.conns = db.load()
            self.paint_tree()
            if self.group is not None:
                self.picked_group(self.group)
            added = [c["name"] for c in self.conns if c.get("docker") in new]
            if added:
                self.say(f"from Docker: {', '.join(added)}")
            elif force:
                self.say(f"{len(found)} database{'s' * (len(found) != 1)} running in Docker")
            return False
        threading.Thread(target=work, daemon=True).start()

    def shown_changed(self, shown):
        if shown:
            self.refresh()
            self.scan_docker()

    def start_rename(self, what):
        """F2 / Rename: the heading becomes the name to edit."""
        kind, ident = what
        old = self.conn(ident)["name"] if kind == "conn" else ident
        self.renaming = what
        self.heading.set_visible(False)
        self.rename_entry.set_text(old)
        self.rename_entry.set_visible(True)
        self.rename_entry.grab_focus()
        self.say("Enter saves the name, Esc leaves it", seconds=10)

    def end_rename(self, save):
        """Leave the name's entry; save: the typed name for the connection, or for the group (its project,
        on each connection in it: a group of the same name merges them)."""
        what, self.renaming = self.renaming, None
        if not what:
            return
        self.rename_entry.set_visible(False)
        self.heading.set_visible(True)
        name = self.rename_entry.get_text().strip()
        kind, ident = what
        if save and name and name != (self.conn(ident)["name"] if kind == "conn" else ident):
            if kind == "conn":
                db.rename({ident}, name=name)
            else:
                db.rename({c["id"] for c in self.conns if c.get("project", "") == ident}, project=name)
                if ident in self.folded:
                    self.folded = self.folded - {ident} | {name}
            self.conns = db.load()
            self.paint_tree()
            if kind == "conn":
                self.select_key(None, (ident, ()))
                self.heading.set_text(name)
            else:
                self.select_key(name)
            self.say(f"renamed to {name}")
        self.tree.grab_focus()

    # ---- several picked in the tree: connections, Redis keys, S3 files -----------------------------
    def picked_many(self):
        """Ctrl / Shift + click: the picked rows' page on the right, what they can have done together.
        Back to one row: its own page."""
        if self.painting:
            return
        rows = self.tree.get_selected_rows()
        if len(rows) == 1 and self.many:
            self.many = None
            row = rows[0]
            return self.picked_group(row.group) if row.group is not None else self.picked(row.key)
        if len(rows) < 2:
            return
        if self.sel:
            self.queries[self.sel[0]] = self.query_text()
        self.sel, self.group, self.pager = None, None, None
        self.end_rename(False)
        self.gen += 1
        self.stack.set_visible_child_name("browse")
        for w in (self.query_box, self.results, self.previewpane):
            w.set_visible(False)
        clear(self.actions)
        keys = [r.key for r in rows if r.key]
        conns = [k[0] for k in keys if not k[1]]
        nodes = [(k[0], self.node_of(k)) for k in keys if k[1]]
        cids = {cid for cid, _n in nodes}
        kind = self.conn(next(iter(cids)))["kind"] if len(cids) == 1 else ""
        self.many_armed = 0
        if conns and len(conns) == len(rows):
            self.many = ("conns", conns)
            self.heading.set_text(f"{len(conns)} connections")
            self.state.set_text(", ".join(self.conn(c)["name"] for c in conns))
            self.many_entry = Gtk.Entry(placeholder_text="a group's name (empty: none)", width_chars=24)
            self.many_entry.connect("activate", lambda *_: self.many_group())
            self.actions.append(self.many_entry)
            self.actions.append(button("Move to group", self.many_group, "primary"))
        elif nodes and len(nodes) == len(rows) and kind == "redis" and all(n and n.leaf for _c, n in nodes):
            self.many = ("keys", [n.path[0] for _c, n in nodes])
            self.heading.set_text(f"{len(nodes)} keys")
            self.state.set_text(", ".join(n.name for _c, n in nodes))
            self.many_entry = Gtk.Entry(placeholder_text="expire in … seconds (empty: never)", width_chars=28)
            self.many_entry.connect("activate", lambda *_: self.many_expire())
            self.actions.append(self.many_entry)
            self.actions.append(button("Set expiry", self.many_expire, "primary"))
        elif nodes and len(nodes) == len(rows) and kind == "s3" and all(n and n.kind == "object" for _c, n in nodes):
            self.many = ("files", [n.path for _c, n in nodes])
            self.heading.set_text(f"{len(nodes)} files")
            self.state.set_text(", ".join(n.name for _c, n in nodes))
        else:
            self.many = ("none", [])
            self.heading.set_text(f"{len(rows)} picked")
            self.state.set_text("Pick connections, or one Redis connection's keys, or one S3 connection's files, "
                                "to change them together.")
            return
        self.many_cid = next(iter(cids)) if cids else None
        self.many_btn = button(f"Delete {len(rows)}", self.many_delete, "flat", "danger", tooltip="Del; click twice")
        self.actions.append(self.many_btn)

    def many_group(self):
        _w, cids = self.many
        name = self.many_entry.get_text().strip()
        db.rename(set(cids), project=name)
        self.conns = db.load()
        self.many = None
        self.paint_tree()
        self.select_key(name) if name else self.show_empty()
        self.say(f"{len(cids)} connections moved to {name or 'no group'}")

    def many_expire(self):
        _w, names = self.many
        text = self.many_entry.get_text().strip()
        try:
            ttl = int(text) if text else 0
        except ValueError:
            return self.say("seconds: a whole number", "bad")
        self.job(self.many_cid, lambda d: d.edit_keys(names, ttl=ttl),
                 lambda _r: self.say(f"{len(names)} keys expire in {ttl} s" if ttl else f"{len(names)} keys never expire"))

    def many_delete(self):
        """Delete what's picked (twice): connections (the databases themselves are untouched), keys, files."""
        what, items = self.many or ("none", [])
        if what == "none":
            return
        if time.time() - self.many_armed > 3:
            self.many_armed = time.time()
            self.many_btn.set_label(f"Click again to delete {len(items)}")
            self.many_btn.add_css_class("armed")
            return
        self.many_armed = 0
        if what == "conns":
            for cid in items:
                self.disconnect(cid)
                db.remove(cid)
                dbagent.forget(cid)
            self.conns = db.load()
            self.many = None
            self.show_empty()
            self.paint_tree()
            return self.say(f"deleted {len(items)} connections (the databases themselves are untouched)")
        cid = self.many_cid

        def done(msg):
            gone = set(items)
            for k, ns in self.kids.items():
                if k[0] == cid:
                    ns[:] = [n for n in ns if (n.path[0] if what == "keys" else n.path) not in gone]
            self.many = None
            self.paint_tree()
            self.show_empty()
            self.say(msg)
        if what == "keys":
            self.job(cid, lambda d: d.delete_keys(items), done)
        else:   # files: by bucket
            def rm(d):
                buckets = {}
                for b, key, _o in items:
                    buckets.setdefault(b, []).append(key)
                for b, keys in buckets.items():
                    d.remove((b, ""), keys)
                return f"{len(items)} files deleted"
            self.job(cid, rm, done)

    # ---- Redis keys: a new one, or one's name and expiry -----------------------------------------
    def key_form(self, c, node=None, ttl=None):
        """+ Key on a Redis connection (node None), or Edit key: its name and when it expires."""
        clear(self.form_page)
        self.stack.set_visible_child_name("form")
        cid = c["id"]
        page = box(True, 10)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(24)
        page.append(label("Edit key" if node else "New key", "heading"))
        page.append(label(c["name"], "dim"))
        grid = Gtk.Grid(column_spacing=14, row_spacing=8)

        def line(text, widget, r):
            grid.attach(label(text, "form-label"), 0, r, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, r, 1, 1)
        name = Gtk.Entry(text=node.path[0] if node else "", placeholder_text="e.g. user:42")
        line("Name", name, 0)
        expire = Gtk.Entry(text=str(ttl) if ttl else "", placeholder_text="seconds; empty: never")
        line("Expires in", expire, 1)
        state = {"type": "string"}
        if not node:
            value = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=4,
                                 bottom_margin=4)
            value.add_css_class("query")
            vs = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            vs.set_size_request(-1, 160)
            vs.set_child(value)
            how = label(db.Redis.NEW_KEY["string"], "form-type")

            def typed(t):
                state["type"] = t
                how.set_text(db.Redis.NEW_KEY[t])
            line("Type", dropdown([(t, t) for t in db.Redis.NEW_KEY], "string", typed), 2)
            grid.attach(box(True, 2, label("Value", "form-label"), how), 0, 3, 1, 1)
            grid.attach(vs, 1, 3, 1, 1)
        page.append(grid)

        def save():
            n = name.get_text().strip()
            t = expire.get_text().strip()
            try:
                secs = int(t) if t else None
            except ValueError:
                return self.say("expires in: seconds, a whole number", "bad")
            if node:
                def done(_r):
                    self.say(f"saved {n}")
                    self.refetch(cid, ())
                    self.stack.set_visible_child_name("browse")
                return self.job(cid, lambda d: d.edit_keys([node.path[0]], rename=n, ttl=secs or 0), done)
            buf = value.get_buffer()
            v = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

            def made(_r):
                self.say(f"added {n}")
                self.kids.pop((cid, ()), None)
                self.open.add((cid, ()))
                self.stack.set_visible_child_name("browse")
                self.fetch_kids(cid, (), then=lambda: self.select_key(None, (cid, (n,))))
            self.job(cid, lambda d: d.new_key(n, state["type"], v, secs), made)
        self.save_form = save
        buttons = box(False, 8, button("Save", save, "primary", tooltip="Ctrl+Enter"),
                      button("Cancel", lambda: self.stack.set_visible_child_name("browse"), "flat", tooltip="Esc"))
        buttons.set_margin_top(8)
        page.append(buttons)
        self.form_page.append(page)
        GLib.idle_add(lambda: name.grab_focus() and False)

    def edit_key(self, cid, node):
        self.job(cid, lambda d: d.ttl(node.path[0]), lambda ttl: self.key_form(self.conn(cid), node, ttl))

    # ---- the right side --------------------------------------------------------------------------
    def show_empty(self):
        self.heading.set_text("Databases")
        self.state.set_text("Pick a connection on the left, or add one with + Connection.")
        clear(self.actions)
        self.query_box.set_visible(False)
        self.results.set_visible(False)

    def picked(self, key):
        if not key or self.painting:
            return
        cid, path = key
        c = self.conn(cid)
        node = self.node_of(key) if path else None
        if self.sel and self.sel[0] != cid:
            self.queries[self.sel[0]] = self.query_text()
        same_conn = self.sel and self.sel[0] == cid
        self.sel = (cid, node)
        self.group, self.many = None, None
        self.end_rename(False)
        self.gen += 1
        self.armed = 0
        self.stack.set_visible_child_name("browse")
        kind = c["kind"]
        self.heading.set_text(node.name if node else c["name"])
        where = c.get("host") or c.get("path") or c.get("endpoint") or ""
        self.state.set_text(" · ".join(x for x in (db.KINDS[kind][0], node.kind if node else where,
                                                   f"via {c['ssh']}" if c.get("ssh") else "") if x))
        hint = db.DRIVERS[kind].run_hint
        self.query_box.set_visible(bool(hint))
        self.query_hint.set_text(hint)
        if not same_conn:
            self.query.get_buffer().set_text(self.queries.get(cid, ""))
        self.paint_actions(c, node)
        self.show_image(cid, *(node.path[:2] if kind == "s3" and node and node.kind == "object" else ()))
        if node and self.browsable(c, node):
            self.browse(cid, node.path)
        elif not same_conn or not node:
            self.pager = None
            self.fill(db.Result())
            self.results.set_visible(False)
        if cid in self.live or node:
            gen = self.gen
            self.job(cid, lambda d: d.info(node.path if node else ()), lambda kv: gen == self.gen and self.show_info(kv))

    @staticmethod
    def browsable(c, node):
        """Has contents for the table: a leaf, or an S3 bucket / folder (the files under it)."""
        return node.leaf or (c["kind"] == "s3" and node.kind in ("bucket", "folder"))

    def show_info(self, kv):
        if kv:
            self.state.set_text(self.state.get_text() + "  ·  " + "  ·  ".join(f"{k} {v}" for k, v in kv if v))

    def paint_actions(self, c, node):
        clear(self.actions)
        cid = c["id"]
        add = self.actions.append
        if self.diagram_of(c, node):
            add(button("Diagram", lambda: self.show_diagram(cid, *self.diagram_of(c, node)), tooltip="d"))
        if node is None:
            add(button("Open" if cid not in self.live else "Reload", lambda: self.reload(cid), "primary", tooltip="Enter / F5"))
            add(button("Edit", lambda: self.form(c), tooltip="e"))
            add(button("Rename", lambda: self.start_rename(("conn", cid)), "flat", tooltip="F2"))
            if c["kind"] == "redis":
                add(button("+ Key", lambda: self.key_form(c), "flat"))
            if cid in self.live:
                add(button("Disconnect", lambda: self.disconnect(cid), "flat"))
            return
        if self.browsable(c, node):
            add(button("Refresh", lambda: self.reload_page(cid, node.path), tooltip="F5"))
        if c["kind"] in ("postgres", "sqlite") and node.kind in ("table", "view"):
            name = ".".join(f'"{p}"' for p in node.path)
            add(button("Query it", lambda: self.prefill(f"select * from {name} limit 100"), "flat"))
        if c["kind"] == "s3":
            if node.kind == "object":
                add(button("Download", lambda: self.s3_download(cid, node)))
                add(button("Replace…", lambda: self.replace_file(cid, node.path[0], node.path[1]), "flat",
                           tooltip="upload a file from this PC in its place: same name, new contents"))
            else:
                add(button("Upload…", lambda: self.s3_upload(cid, node)))
        if c["kind"] == "redis" and node.leaf:
            add(button("Edit key", lambda: self.edit_key(cid, node), "flat", tooltip="its name, when it expires"))
        if c["kind"] in ("redis", "s3") and node.kind not in ("bucket", "folder", "more"):
            self.del_btn = button("Delete", lambda: self.delete_node(cid, node), "danger", tooltip="Del; click twice")
            add(self.del_btn)

    # ---- the schema diagram: Liam ERD (dbliam.py) -------------------------------------------------
    def diagram_shown(self, stack, _prop):
        shown = stack.get_visible_child_name() == "diagram"
        (self.window.add_css_class if shown else self.window.remove_css_class)("solid")
        if not shown:
            self.diagram.drop()

    @staticmethod
    def diagram_of(c, node):
        """(schema, table to centre on) for the diagram from this node, or None (no diagram for it)."""
        if c["kind"] == "postgres":
            if node is None:
                return "public", None
            if node.kind in ("schema", "table", "view"):
                return node.path[0], node.path[1] if len(node.path) > 1 else None
        elif c["kind"] == "sqlite":
            return "", node.path[0] if node else None
        return None

    def show_diagram(self, cid, schema, focus=None):
        c = self.conn(cid)

        if dbliam.WebKit is None:
            return self.say("the diagram needs WebKitGTK: sudo pacman -S webkitgtk-6.0", "bad", 10)
        if not os.path.isfile(f"{db.LIAM_APP}/index.html"):
            self.say(f"getting Liam ERD {db.LIAM_VERSION} (once)…", seconds=30)

        def fetch(d):
            schemas = [n.name for n in d.children(())] if c["kind"] == "postgres" else []
            return db.liam_app(), d.liam(schema), schemas

        def done(got):
            app, data, schemas = got
            self.diagram_cid = cid
            self.stack.set_visible_child_name("diagram")
            self.diagram.show(app, data, schemas, schema, focus)
            self.say(f"{c['name']}{' · ' + schema if schema else ''}: {len(data['tables'])} tables")
        self.job(cid, fetch, done)

    def reload(self, cid):
        """Drop what's been fetched for the connection and list its top again."""
        for k in [k for k in self.kids if k[0] == cid]:
            del self.kids[k]
        self.open = {k for k in self.open if k[0] != cid} | {(cid, ())}
        self.fetch_kids(cid, (), then=lambda: self.picked((cid, ())))

    def prefill(self, text):
        self.query.get_buffer().set_text(text)
        self.query.grab_focus()

    # ---- results ---------------------------------------------------------------------------------
    def browse(self, cid, path):
        """Show a table / key / collection from its first page, and count its rows (when that's cheap)."""
        self.pager = pg = Pager("browse", cid, path, self.size)
        self.paint_rec()
        self.job(cid, lambda d: d.editor(path), lambda e: self.pager is pg and self.got_editor(pg, e))
        self.show_page(0)
        self.job(cid, lambda d: d.count(path), lambda n: self.pager is pg and self.counted(n))

    def got_editor(self, pg, editor):
        pg.editor = editor
        self.paint_rec()

    def reload_page(self, cid, path):
        """Refresh: the page shown again (and the count), or the first page of something else."""
        pg = self.pager
        if not pg or pg.kind != "browse" or (pg.cid, pg.path) != (cid, path):
            return self.browse(cid, path)
        self.show_page(pg.index if pg.seekable or pg.index in pg.starts else 0)
        self.job(cid, lambda d: d.count(path), lambda n: self.pager is pg and self.counted(n))

    def counted(self, n):
        self.pager.total = n
        self.paint_pager()

    def query_text(self):
        buf = self.query.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def query_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.run_query()
            return True
        return False

    def run_query(self):
        if not self.sel:
            return
        buf = self.query.get_buffer()
        sel = buf.get_selection_bounds()
        text = (buf.get_text(*sel, False) if sel else self.query_text()).strip()
        if not text and self.conn(self.sel[0])["kind"] != "mongo":
            return
        if self.ai_write(text):   # the AI's write: a second Run within 5 s, after reading it
            if not self.confirm or self.confirm[0] != text or time.time() - self.confirm[1] > 5:
                self.confirm = (text, time.time())
                return self.say("the AI suggested this and it changes data: read it, then Run again to run it", "bad", 6)
            self.confirm = None
        cid, node = self.sel
        path = node.path if node else ()
        gen = self.gen = self.gen + 1
        self.pager = pg = Pager("run", cid, path, self.size)
        started = time.time()
        size = self.size

        def done(r):
            if gen != self.gen:
                return
            pg.pages.append(r)
            pg.lens[0], pg.more = len(r.rows), r.more
            if not r.more and r.columns:
                pg.total = (len(r.rows), True)
            self.fill(r)
            took = f"{time.time() - started:.2f} s"
            self.say(f"{r.message} · {took}" if r.message else took)
        self.job(cid, lambda d: d.run(text, path, size), done)

    # ---- pages -----------------------------------------------------------------------------------
    def show_page(self, i):
        """Fetch page i of what's shown (a query's pages already fetched come from memory)."""
        pg, gen = self.pager, self.gen
        if pg.kind == "run":
            if i < len(pg.pages):
                pg.index, pg.more = i, i < len(pg.pages) - 1 or pg.total is None
                return self.fill(pg.pages[i])
            if i != len(pg.pages) or not pg.more:
                return

            def got(r):
                if self.pager is not pg or gen != self.gen:
                    return
                pg.pages.append(r)
                pg.index, pg.lens[i], pg.more = i, len(r.rows), r.more
                if not r.more:
                    pg.total = (sum(pg.lens.values()), True)
                self.fill(r)
            return self.job(pg.cid, lambda d: d.more(pg.size), got)
        at = i * pg.size if pg.seekable else pg.starts.get(i)
        if at is None:
            return

        def done(r):
            if self.pager is not pg or gen != self.gen:
                return
            if r.next_at is not None:   # a cursor: page i + 1 starts where this one stopped
                pg.seekable = False
                pg.starts[i + 1] = r.next_at
            pg.index, pg.lens[i], pg.more = i, len(r.rows), r.more
            self.fill(r)
        self.job(pg.cid, lambda d: d.browse(pg.path, at, pg.size), done)

    def turn(self, where):
        pg = self.pager
        if not pg:
            return
        i = {"first": 0, "prev": pg.index - 1, "next": pg.index + 1, "last": pg.last_page()}[where]
        if i is None or i < 0 or (where == "next" and not pg.more):
            return
        self.show_page(i)

    def jump(self, text):
        pg = self.pager
        try:
            i = int(text) - 1
        except ValueError:
            return self.paint_pager()
        last = pg.last_page() if pg else None
        if not pg or i < 0 or (last is not None and i > last) or not (pg.kind == "browse" and pg.seekable):
            self.say("can't go to that page" if pg and pg.kind == "browse" and pg.seekable
                     else "this one goes page by page: \u2039 \u203a", "bad")
            return self.paint_pager()
        self.show_page(i)

    def resize(self, size):
        """A new page size: a table starts again at the page with the row now on top; a query keeps its pages
        and uses the size from the next one (running it again could repeat what it changed)."""
        self.size = size
        pg = self.pager
        if not pg:
            return
        if pg.kind == "browse":
            top = pg.first_row()
            new = Pager("browse", pg.cid, pg.path, size)
            new.total, new.editor = pg.total, pg.editor
            self.pager = new
            self.show_page(top // size if pg.seekable else 0)
        else:
            pg.size = size

    def paint_pager(self):
        pg = self.pager
        has = bool(pg and self.data_columns())
        for w in (*self.nav.values(), self.page_entry, self.page_of):
            w.set_visible(has)
        if not has:
            return
        n = len(pg.pages[pg.index].rows) if pg.kind == "run" else pg.lens.get(pg.index, 0)
        first = pg.first_row()
        total = pg.total
        last = pg.last_page()
        if total:
            pages = -(-total[0] // pg.size) if total[0] else 1
            of_pages = f"of {pages:,}" if total[1] and (pg.kind == "run" or pg.seekable) else f"of ~{pages:,}"
            of_rows = f" of {'' if total[1] else '~'}{total[0]:,}"
        else:
            of_pages, of_rows = ("" if pg.more else f"of {pg.index + 1}"), ""
        self.page_entry.set_text(str(pg.index + 1))
        self.page_entry.set_sensitive(pg.kind == "browse" and pg.seekable)
        self.page_of.set_text(of_pages)
        self.count.set_text(f"rows {first + 1:,}\u2013{first + n:,}{of_rows}" if n else f"no rows{of_rows}")
        self.nav["first"].set_sensitive(pg.index > 0)
        self.nav["prev"].set_sensitive(pg.index > 0)
        self.nav["next"].set_sensitive(pg.more)
        self.nav["last"].set_sensitive(last is not None and last > pg.index)

    def fill(self, r):
        """Show a page of rows (new columns when they changed: a Mongo page can bring new fields)."""
        self.results.set_visible(True)
        if [c.get_title() for c in self.data_columns()] != r.columns:
            for col in self.data_columns():
                self.table.remove_column(col)
            for i, title in enumerate(r.columns):
                f = Gtk.SignalListItemFactory()
                f.connect("setup", lambda _f, item: item.set_child(label("", xalign=0.0)))
                f.connect("bind", lambda _f, item, i=i: item.get_child().set_text(
                    _short(item.get_item().cells[i]) if i < len(item.get_item().cells) else ""))
                col = Gtk.ColumnViewColumn(title=title, factory=f, resizable=True)
                col.set_expand(len(r.columns) <= 3)
                self.table.append_column(col)
        self.rowview.get_buffer().set_text("")
        keys = r.keys if r.keys is not None else [None] * len(r.rows)
        self.store.splice(0, self.store.get_n_items(), [Row(cells, k) for cells, k in zip(r.rows, keys)])
        if r.columns:
            self.paint_pager()
        else:   # a statement with no rows (an UPDATE…): its message instead of a pager
            self.count.set_text(r.message or "")
            self.paint_pager()
        if len(r.rows) == 1:
            self.selection.select_item(0, True)
        else:
            self.selection.unselect_all()
        self.paint_rec()
        GLib.idle_add(lambda: self.table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None) if self.store.get_n_items() else None)

    def data_columns(self):
        """The table's columns but the checkboxes'."""
        return [c for c in self.table.get_columns() if c is not self.check_col]

    def picked_rows(self):
        """The positions of the table's picked rows."""
        bits = self.selection.get_selection()
        return [bits.get_nth(i) for i in range(bits.get_size())]

    # ---- the checkboxes: a row's follows its selection, ticking one adds it to the others -----------
    def check_setup(self, _f, item):
        check = Gtk.CheckButton(halign=Gtk.Align.CENTER)
        check.connect("toggled", lambda c: self.check_toggled(item, c))
        item.set_child(check)

    def check_bind(self, _f, item):
        item.binding = item.bind_property("selected", item.get_child(), "active", GObject.BindingFlags.SYNC_CREATE)

    def check_toggled(self, item, check):
        if check.get_active() == item.get_selected():   # the binding showing the selection: nothing to do
            return
        if check.get_active():
            self.selection.select_item(item.get_position(), False)
        else:
            self.selection.unselect_item(item.get_position())

    def all_toggled(self, check):
        n = self.store.get_n_items()
        if check.get_active() == (n and len(self.picked_rows()) == n):   # paint_rec() showing it
            return
        self.selection.select_all() if check.get_active() else self.selection.unselect_all()

    def show_row(self):
        rows = self.picked_rows()
        item = self.store.get_item(rows[0]) if len(rows) == 1 else None   # several: no one row to show
        pg = self.pager
        if pg and pg.kind == "browse" and len(pg.path) == 2 and self.conn(pg.cid)["kind"] == "s3":
            # a bucket / folder's file list: the picked file's image, if it is one
            self.show_image(pg.cid, *((pg.path[0], pg.path[1] + item.cells[0]) if item else ()))
        buf = self.rowview.get_buffer()
        buf.set_text("")
        self.rowpane.set_visible(item is not None)
        if not item:
            return
        cols = [c.get_title() for c in self.data_columns()]
        end = buf.get_end_iter
        for title, value in zip(cols, item.cells):
            if title:
                buf.insert_with_tags_by_name(end(), title, "key")
                buf.insert(end(), "  ")
            buf.insert(end(), value + "\n")

    def show_image(self, cid, bucket=None, key=None):
        """The preview beside the table: an S3 image file, fetched and decoded on the connection's thread
        (a large one doesn't stall the window); no key, or not an image, hides it."""
        self.img_gen += 1
        gen = self.img_gen
        if not key:
            return self.previewpane.set_visible(False)

        def fetch(d):
            data = d.image(bucket, key)
            return data and (Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)), len(data))

        def done(got):
            if gen != self.img_gen:
                return
            self.previewpane.set_visible(bool(got))
            if got:
                tex, n = got
                self.preview.set_paintable(tex)
                self.preview_note.set_text(f"{tex.get_width()} × {tex.get_height()} · {db.size(n)}")
        self.job(cid, fetch, done, fail=lambda: gen == self.img_gen and self.previewpane.set_visible(False))

    # ---- changing rows: + New, Edit, Delete under the table -----------------------------------------
    def editor(self):
        """The Editor of what the table shows, when it's a browsed leaf that can be changed."""
        pg = self.pager
        return pg.editor if pg and pg.kind == "browse" else None

    def paint_rec(self):
        e = self.editor()
        self.rec.set_visible(e is not None)
        self.check_col.set_visible(e is not None)
        if not e:
            return
        n = len(self.picked_rows())
        rows = self.store.get_n_items()
        self.check_all.set_active(bool(rows) and n == rows)
        self.check_all.set_sensitive(rows > 0)
        noun = e.noun + "s" * (n != 1)
        self.rec_new.set_visible(e.new)
        self.rec_new.set_label("Upload…" if e.files else f"+ {e.noun.capitalize()}")
        self.rec_new.set_tooltip_text("upload files into this folder" if e.files else f"add a {e.noun}")
        self.rec_edit.set_visible(e.edit)
        self.rec_edit.set_sensitive(n == 1 or n > 1 and bool(e.many))
        self.rec_edit.set_label(f"Edit {n} {noun}" if n > 1 else "Edit")
        self.rec_edit.set_tooltip_text(f"several {e.noun}s can't be edited together" if n > 1 and not e.many
                                       else "Enter")
        self.rec_del.set_visible(e.delete)
        self.rec_del.set_sensitive(n > 0)
        self.rec_armed = 0
        self.rec_del.remove_css_class("armed")
        self.rec_del.set_label(f"Delete {n} {noun}" if n > 1 else "Delete")

    def new_row(self):
        e, pg = self.editor(), self.pager
        if not e or not e.new:
            return
        if e.files:
            return self.upload_files(pg.cid, pg.path)
        self.record_form(pg, e, [])

    def edit_rows(self, rows):
        """The form for one row (its values in full, fetched again: a cell is cut short) or for several."""
        e, pg = self.editor(), self.pager
        if not e or not e.edit or not rows:
            return
        keys = [self.store.get_item(i).key for i in rows]
        if len(keys) > 1:
            if not e.many:
                return self.say(f"several {e.noun}s can't be edited together: pick one", "bad")
            return self.record_form(pg, e, keys)
        self.job(pg.cid, lambda d: d.record(pg.path, keys[0]),
                 lambda rec: self.pager is pg and self.record_form(pg, e, keys, rec))

    def delete_rows(self):
        e, pg = self.editor(), self.pager
        rows = self.picked_rows()
        if not e or not e.delete or not rows:
            return
        if time.time() - self.rec_armed > 3:
            self.rec_armed = time.time()
            self.rec_del.set_label(f"Click again to delete {len(rows)}" if len(rows) > 1 else "Click again to delete")
            self.rec_del.add_css_class("armed")
            GLib.timeout_add(3000, self.disarm_rec)
            return
        self.rec_armed = 0
        keys = [self.store.get_item(i).key for i in rows]
        self.job(pg.cid, lambda d: d.remove(pg.path, keys), lambda msg: self.changed_rows(pg, msg))

    def disarm_rec(self):
        if self.rec_armed and time.time() - self.rec_armed >= 3:
            self.paint_rec()
        return False

    def changed_rows(self, pg, msg):
        """After a change: the page again (with its count), and an S3 folder's files in the tree."""
        self.say(msg)
        self.stack.set_visible_child_name("browse")
        if self.conn(pg.cid)["kind"] == "s3":
            self.refetch(pg.cid, pg.path)
        if self.pager is pg:
            self.reload_page(pg.cid, pg.path)

    def refetch(self, cid, path):
        """What the tree has under path, fetched again when it's open (forgotten when it isn't)."""
        self.kids.pop((cid, path), None)
        if (cid, path) in self.open:
            self.fetch_kids(cid, path)

    def record_form(self, pg, e, keys, rec=None):
        """New (keys []), edit one (rec: its values) or edit several rows, on the right. Only the fields you
        change are written: a new row gets the database's defaults for the rest."""
        clear(self.form_page)
        self.stack.set_visible_child_name("form")
        c = self.conn(pg.cid)
        where = self.selected_name() or "/".join(pg.path)
        many = len(keys) > 1
        page = box(True, 10)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(24)
        title = f"Edit {len(keys)} {e.noun}s" if many else f"Edit {e.noun}" if keys else f"New {e.noun}"
        page.append(label(title, "heading"))
        page.append(label(f"{c['name']} · {where}", "dim", ellipsize=True))
        hint = e.many if many else e.hint
        if hint:
            page.append(label(("Sets " if many and not e.json else "") + hint + (" — the rest stay as they are."
                              if many and not e.json else ""), "dim", wrap=True))
        fields = []
        if e.json:
            text = rec if rec is not None else "{\n  \n}" if not keys else "{}"
            doc = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=4,
                               bottom_margin=4, vexpand=True)
            doc.add_css_class("query")
            doc.get_buffer().set_text(text)
            ds = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            ds.set_size_request(-1, 320)
            ds.set_child(doc)
            page.append(ds)
            first = doc
        else:
            grid = Gtk.Grid(column_spacing=14, row_spacing=8)
            for r, f in enumerate(e.fields):
                if many and e.files:   # several files: the one field is where they move to
                    f = db.Field("folder", e.many)
                value = (rec or {}).get(f.name) if keys and not many else ""
                ff = FormField(f, value, enabled=not (many and f.name in ("field", "member")))
                fields.append(ff)
                note = "computed" if f.fixed else f"default {f.default}" if not keys and f.default else f.type
                name = box(True, 2, label(f.name, "form-label", ellipsize=True), label(note, "form-type", ellipsize=True))
                name.set_valign(Gtk.Align.START)
                name.set_hexpand(False)   # the text boxes get the width: a long type / default is cut instead
                for w in (name.get_first_child(), name.get_last_child()):
                    w.set_max_width_chars(26)
                    w.set_tooltip_text(w.get_text())
                grid.attach(name, 0, r, 1, 1)
                grid.attach(ff.widget, 1, r, 1, 1)
                if ff.null:
                    grid.attach(ff.null, 2, r, 1, 1)
            page.append(grid)
            first = next((ff.view for ff in fields if ff.view.get_sensitive()), None)
            if not keys:
                page.append(label("Fields left as they are aren't written: the database fills them (its default, "
                                  "or NULL).", "dim", wrap=True))

        def save():
            if e.json:
                buf = doc.get_buffer()
                values = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            else:
                values = {("file" if ff.f.name == "folder" else ff.f.name): ff.value() for ff in fields if ff.touched}
                if keys and not values:
                    return self.say("nothing changed", "bad")
            self.job(pg.cid, lambda d: d.update(pg.path, keys, values) if keys else d.insert(pg.path, values),
                     lambda msg: self.changed_rows(pg, msg))
        self.save_form = save   # Ctrl+Enter (key())
        buttons = box(False, 8, button("Save", save, "primary", tooltip="Ctrl+Enter"),
                      button("Cancel", lambda: self.stack.set_visible_child_name("browse"), "flat", tooltip="Esc"))
        if e.files and len(keys) == 1:   # one file: its contents can be replaced by one from this PC
            buttons.append(Gtk.Box(hexpand=True))
            buttons.append(button("Replace with a file…", lambda: self.replace_file(pg.cid, pg.path[0], keys[0], pg),
                                  tooltip="upload a file from this PC in its place: same name, new contents"))
        buttons.set_margin_top(8)
        page.append(buttons)
        self.form_page.append(page)
        if first:
            GLib.idle_add(lambda: first.grab_focus() and False)
        # a text view first measures its height before it has its width (a name wrapped a few letters a line)
        # and doesn't ask again once it has one: measured again as soon as the boxes have their width, each
        # box fits its text
        def fit():
            if page.get_parent() is None or self.stack.get_visible_child_name() != "form":   # left already
                return False
            if any(not ff.view.get_width() for ff in fields):
                return True
            for ff in fields:
                ff.widget.queue_resize()
            return False
        if fields:
            GLib.timeout_add(40, fit)

    def upload_files(self, cid, path):
        """Upload… in a bucket / folder's file list: the files picked, into it."""
        def chosen(d, res):
            try:
                files = [f.get_path() for f in d.open_multiple_finish(res)]
            except GLib.Error:
                return
            pg = self.pager

            def up(drv):
                for src in files:
                    drv.upload(path, src)
                return f"uploaded {len(files)} file{'s' * (len(files) != 1)}"
            self.job(cid, up, lambda msg: self.changed_rows(pg, msg) if pg and pg.path == path else
                     (self.say(msg), self.refetch(cid, path)))
        Gtk.FileDialog().open_multiple(self.window, None, chosen)

    def replace_file(self, cid, bucket, key, pg=None):
        """Replace with a file…: an S3 file's contents uploaded again from one picked here, under its own name.
        Then the file list (pg) or the file's own page shows the new one."""
        def chosen(d, res):
            try:
                src = d.open_finish(res).get_path()
            except GLib.Error:
                return

            def done(msg):
                if pg:
                    return self.changed_rows(pg, msg)
                self.say(msg)
                if self.sel and self.sel[1] and self.sel[1].path[:2] == (bucket, key):
                    self.picked((cid, self.sel[1].path))   # its size, date and preview again
            self.job(cid, lambda drv: drv.replace(bucket, key, src), done)
        Gtk.FileDialog(title=f"Replace {os.path.basename(key)} with").open(self.window, None, chosen)

    # ---- S3 / Redis actions ----------------------------------------------------------------------
    def s3_download(self, cid, node):
        dlg = Gtk.FileDialog(initial_name=os.path.basename(node.path[1]))
        dlg.set_initial_folder(Gio.File.new_for_path(os.path.expanduser("~/Downloads")))

        def chosen(d, res):
            try:
                dest = d.save_finish(res).get_path()
            except GLib.Error:
                return
            self.job(cid, lambda drv: drv.download(node.path, dest), lambda _r: self.say(f"saved {dest}"))
        dlg.save(self.window, None, chosen)

    def s3_upload(self, cid, node):
        def chosen(d, res):
            try:
                src = d.open_finish(res).get_path()
            except GLib.Error:
                return
            path = node.path if node.kind == "folder" else (node.path[0], "")

            def done(key):
                self.say(f"uploaded {key}")
                self.kids.pop((cid, path), None)
                if (cid, path) in self.open:
                    self.fetch_kids(cid, path)
            self.job(cid, lambda drv: drv.upload(path, src), done)
        Gtk.FileDialog().open(self.window, None, chosen)

    def delete_node(self, cid, node):
        if time.time() - self.armed > 3:
            self.armed = time.time()
            self.del_btn.set_label("Click again to delete")
            self.del_btn.add_css_class("armed")
            GLib.timeout_add(3000, self.disarm)
            return
        self.armed = 0

        def done(_r):
            for k, ns in self.kids.items():
                if k[0] == cid and node in ns:
                    ns.remove(node)
            self.say(f"deleted {node.name}")
            self.paint_tree()
            self.show_empty()
        self.job(cid, lambda d: d.delete(node.path), done)

    def disarm(self):
        if self.armed and time.time() - self.armed >= 3 and getattr(self, "del_btn", None):
            self.armed = 0
            self.del_btn.set_label("Delete")
            self.del_btn.remove_css_class("armed")
        return False

    # ---- the connection form ---------------------------------------------------------------------
    def form(self, c):
        """New (c None) or edit a connection, on the right."""
        clear(self.form_page)
        self.stack.set_visible_child_name("form")
        kind = c["kind"] if c else "postgres"
        sec = db.secret(c["id"]) if c else {}
        page = box(True, 10)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(24)
        page.append(label("Edit connection" if c else "New connection", "heading"))
        fields = {}
        grid = Gtk.Grid(column_spacing=14, row_spacing=8)

        def line(text, widget, r):
            grid.attach(label(text, "form-label"), 0, r, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, r, 1, 1)

        def paint(kind):
            while grid.get_first_child():
                grid.remove(grid.get_first_child())
            fields.clear()
            name = Gtk.Entry(text=c["name"] if c else "", placeholder_text="e.g. shop (prod)")
            fields["name"] = name
            line("Name", name, 0)
            known = sorted({x.get("project") for x in self.conns if x.get("project")}, key=str.lower)
            project = Gtk.Entry(text=(c or {}).get("project", "") or (self.group or ""),
                                placeholder_text="its group in the list" + (f", e.g. {known[0]}" if known else ""))
            fields["project"] = project
            line("Project (optional)", project, 1)
            r = 2
            if kind in ("postgres", "redis"):
                url = Gtk.Entry(placeholder_text=f"paste a {kind}:// URL to fill the fields")
                url.connect("changed", lambda e: self.paste_url(e.get_text(), fields))
                line("URL", url, r)
                r += 1
            for key, text, is_secret in db.FIELDS[kind]:
                value = (sec if is_secret else (c or {})).get(key, "")
                if key == "port" and not value:
                    value = str(db.KINDS[kind][2])
                e = Gtk.PasswordEntry(show_peek_icon=True) if is_secret else Gtk.Entry()
                e.set_text(str(value))
                fields[key] = e
                if key == "path":
                    pick = button("Choose…", lambda e=e: self.choose_file(e), "flat")
                    line(text, box(False, 8, e, pick), r)
                else:
                    line(text, e, r)
                r += 1
            if kind in ("postgres", "redis"):
                ssh = Gtk.Entry(text=(c or {}).get("ssh", ""), placeholder_text="user@server or a ~/.ssh/config host (key login)")
                fields["ssh"] = ssh
                line("SSH tunnel (optional)", ssh, r)
            state["kind"] = kind

        state = {"kind": kind}
        if not c:
            page.append(dropdown([(k, v[0]) for k, v in db.KINDS.items()], kind, paint))
        paint(kind)
        page.append(grid)
        self.test_note = label("", "dim", wrap=True)

        def collect():
            k = state["kind"]
            conn = {"kind": k, "name": fields["name"].get_text().strip() or db.KINDS[k][0]}
            if fields["project"].get_text().strip():
                conn["project"] = fields["project"].get_text().strip()
            if c:
                conn["id"] = c["id"]
                for k in ("ai_data", "docker", "docker_sig"):   # set in the Ask pane / by Docker, not here: kept
                    if c.get(k):
                        conn[k] = c[k]
            secrets = {}
            for key, _t, is_secret in db.FIELDS[k] + [("ssh", "", False)]:
                if key in fields:
                    v = fields[key].get_text().strip()
                    if is_secret:
                        secrets[key] = v
                    elif v:
                        conn[key] = v
            return conn, secrets

        def save():
            conn, secrets = collect()
            try:
                conn = db.put(conn, secrets)
            except db.DbError as e:
                return self.say(str(e), "bad")
            if c:
                self.disconnect(conn["id"])   # its settings changed: connect again with them
            self.conns = db.load()
            self.errors.pop(conn["id"], None)
            self.paint_tree()
            self.select_key(None, (conn["id"], ()))
            self.say(f"saved {conn['name']}")

        def test():
            conn, secrets = collect()
            self.test_note.set_text("trying…")

            def go():
                try:
                    cfg = dict(conn, id="__test__")
                    tunnel = None
                    if cfg.get("ssh") and cfg["kind"] in ("postgres", "redis"):
                        tunnel = db.Tunnel(cfg["ssh"], cfg.get("host") or "127.0.0.1",
                                           int(cfg.get("port") or db.KINDS[cfg["kind"]][2]))
                        cfg.update(host="127.0.0.1", port=tunnel.port)
                    d = db.DRIVERS[cfg["kind"]]({**cfg, **secrets})
                    d.tunnel = tunnel
                    d.children(())
                    d.close()
                    msg, kind = "✓ it works", "green"
                except Exception as e:
                    msg, kind = str(e).strip() or type(e).__name__, "red"
                GLib.idle_add(lambda: (self.test_note.set_text(msg), self.test_note.set_css_classes([kind])) and False)
            threading.Thread(target=go, daemon=True).start()

        self.save_form = save
        buttons = box(False, 8, button("Save", save, "primary"), button("Test", test),
                      button("Cancel", lambda: self.stack.set_visible_child_name("browse"), "flat", tooltip="Esc"))
        if c:
            self.forget_btn = button("Delete connection", lambda: self.forget(c), "danger", tooltip="click twice")
            buttons.append(Gtk.Box(hexpand=True))
            buttons.append(self.forget_btn)
        buttons.set_margin_top(8)
        page.append(buttons)
        page.append(self.test_note)
        page.append(label("Passwords and keys are kept in the keyring, not in a file. A connection opens when you "
                          f"use it and closes after {db.IDLE // 60} minutes unused.", "dim", wrap=True))
        self.form_page.append(page)
        GLib.idle_add(lambda: fields["name"].grab_focus() and False)

    def form_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK and self.save_form:
            self.save_form()
            return True
        return False

    def paste_url(self, text, fields):
        if "://" not in text:
            return
        try:
            values, secrets = db.parse_url(text)
        except db.DbError:
            return
        for k, v in {**values, **secrets}.items():
            if k in fields and v:
                fields[k].set_text(v)

    def choose_file(self, entry):
        def chosen(d, res):
            try:
                entry.set_text(d.open_finish(res).get_path())
            except GLib.Error:
                pass
        Gtk.FileDialog().open(self.window, None, chosen)

    def forget(self, c):
        if time.time() - self.armed > 3:
            self.armed = time.time()
            self.forget_btn.set_label("Click again to delete")
            self.forget_btn.add_css_class("armed")
            return
        self.armed = 0
        self.disconnect(c["id"])
        db.remove(c["id"])
        dbagent.forget(c["id"])
        self.conns = db.load()
        self.sel = None
        self.stack.set_visible_child_name("browse")
        self.show_empty()
        self.paint_tree()
        self.say(f"deleted the connection {c['name']} (the database itself is untouched" +
                 ("; Docker won't add it again)" if c.get("docker") else ")"))

    # ---- Ask: the AI about this connection, its own popup (dbaskgui.py) ----------------------------
    def ask(self):
        """Open Ask about the selected connection and what's selected in it. The context goes in the
        environment: a process's arguments are readable by every user, its environment only by you."""
        if not self.sel:
            return self.say("pick a connection first", "bad")
        cid, node = self.sel
        ctx = {"id": cid, "selected": self.selected_name(), "query": self.query_text(),
               "columns": ", ".join(c.get_title() for c in self.data_columns()) if node and node.leaf else ""}
        subprocess.Popen(["uwsm", "app", "--", "python3", os.path.join(HERE, "dbaskgui.py")],
                         env={**os.environ, "DBASK_CONTEXT": json.dumps(ctx)}, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def selected_name(self):
        """The selected table / collection / key / file as the model should name it."""
        cid, node = self.sel
        if not node or node.kind == "more":
            return ""
        kind = self.conn(cid)["kind"]
        if kind in ("postgres", "mongo") and len(node.path) == 2:
            return f"{node.path[0]}.{node.path[1]}"
        if kind == "s3":
            return f"{node.path[0]}/{node.path[1]}"
        return node.path[0]

    def use_query(self, _action, param):
        """Ask's "Put in query box" (the use-query action, from dbaskgui.py on your session bus): the query
        goes in that connection's box. It's remembered as the AI's: a write in it needs a second Run (the AI
        may have been steered by what's in the data)."""
        try:
            msg = json.loads(param.get_string())
            cid, q = msg["id"], str(msg["q"])
        except (ValueError, KeyError, TypeError):
            return
        if not self.conn(cid):
            return
        self.ai_queries.add(q.strip())
        self.window.present()
        if not self.sel or self.sel[0] != cid:
            self.queries[cid] = q
            return self.say(f"the AI's query is in {self.conn(cid)['name']}'s query box")
        self.prefill(q)
        self.say("the AI's query is in the query box: read it, then Ctrl+Enter runs it")

    def ai_write(self, text):
        """True when text came from the AI (or contains a query that did) and doesn't only read."""
        if not any(q in text or text in q for q in self.ai_queries):
            return False
        kind = self.conn(self.sel[0])["kind"]
        if kind in ("postgres", "sqlite"):
            return any(dbagent.db.statement_kind(part) not in dbagent.SQL_KINDS
                       for part in text.split(";") if part.strip())
        if kind == "redis":
            return text.split()[0].upper() not in dbagent.REDIS_READ if text.split() else False
        return False   # Mongo's box only finds and aggregates; S3 has none

    # ---- keys ------------------------------------------------------------------------------------
    def leave_text(self):
        self.tree.grab_focus()

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape and self.stack.get_visible_child_name() == "form":   # also from a field
            self.stack.set_visible_child_name("browse")
            self.tree.grab_focus()
            return True
        if self.typing():
            return False
        focus = self.window.get_focus()
        if keyval == Gdk.KEY_Delete and focus and (focus is self.table or focus.is_ancestor(self.table)) and self.editor():
            self.delete_rows()   # the table's picked rows
            return True
        if keyval == Gdk.KEY_Delete and self.many and self.many[0] != "none":
            self.many_delete()
            return True
        if self.stack.get_visible_child_name() == "diagram" and self.diagram.key_pressed(keyval):
            return True
        row = self.tree.get_selected_row()
        key = row.key if row else None
        if keyval == Gdk.KEY_F2 and row:
            if row.group:
                self.start_rename(("group", row.group))
            elif key and not key[1]:
                self.start_rename(("conn", key[0]))
        elif keyval == Gdk.KEY_F5 and row and row.group is not None:
            self.scan_docker(force=True)
        elif keyval == Gdk.KEY_n:
            self.form(None)
        elif keyval == Gdk.KEY_e and key:
            self.form(self.conn(key[0]))
        elif keyval == Gdk.KEY_F5 and key:
            node = self.node_of(key) if key[1] else None
            if node and node.leaf:
                self.reload_page(key[0], node.path)
            elif node:
                self.kids.pop(key, None)
                self.open.add(key)
                self.fetch_kids(*key)
                if self.browsable(self.conn(key[0]), node):   # an S3 folder: its file list too
                    self.reload_page(key[0], node.path)
            else:
                self.reload(key[0])
        elif keyval == Gdk.KEY_Delete and self.sel and self.sel[1] and getattr(self, "del_btn", None) \
                and self.del_btn.get_parent() is not None:
            self.delete_node(self.sel[0], self.sel[1])
        elif keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            self.turn("prev" if keyval == Gdk.KEY_bracketleft else "next")
        elif keyval == Gdk.KEY_a:
            self.ask()
        elif keyval == Gdk.KEY_d and key and self.diagram_of(self.conn(key[0]), self.node_of(key) if key[1] else None):
            self.show_diagram(key[0], *self.diagram_of(self.conn(key[0]), self.node_of(key) if key[1] else None))
        elif keyval == Gdk.KEY_slash:
            self.filter.grab_focus()
        else:
            return False
        return True


CELL_CHARS = 40   # a table cell's most characters: a wide table stays scannable


def _short(text):
    """A cell on one line, cut at CELL_CHARS (the text itself, not a label's ellipsis: the columns size to
    their content, an ellipsizing label let them shrink to the header); the row view has it in full."""
    text = text.replace("\n", " \u21b5 ")
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 1] + "\u2026"


def make():
    return Databases()


def main():
    run(make(), "sami.databases", (1280, 720), toggle=False)


if __name__ == "__main__":
    main()
