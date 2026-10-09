#!/usr/bin/env python3
# dbgui.py — the database client (workspace 6, config/hypr/workspaces.conf): Postgres, Redis, MongoDB,
# S3 and SQLite, local or remote (directly or through an SSH tunnel). Tiled, so running it again just
# brings it up. The connections, drivers and tunnels are dblib.py's; this draws them.
#
#   left   the connections; open one (Enter / double-click) for its schemas → tables, keys,
#          databases → collections, buckets → folders → files. A green dot: connected.
#   right  the selected table / key / collection / file, a page at a time (« ‹ page › », the page size), the query box
#          (SQL, a Redis command, a Mongo filter or pipeline; Ctrl+Enter runs it or the selected part)
#          and the selected row in full. Full access: what you run applies at once.
#
# Each connection has one worker thread (a slow server never freezes the window) and closes, with its
# tunnel, after dblib.IDLE seconds unused; nothing polls a database.
import os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dblib as db
from gtkkit import Gdk, Gio, GLib, Gtk, View, box, button, clear, dropdown, label, recolor, run, scrolled
from gi.repository import GObject

INDENT = 16
GLYPHS = {"schema": "", "database": "", "folder": "", "bucket": "", "table": "",
          "view": "", "collection": "", "object": "", "more": "…"}
KEY_GLYPH = ""   # a Redis key (its type is the dim note)
CSS = """
.db-tree > row { padding: 3px 10px; }
.db-glyph { min-width: 18px; color: #565f89; }
.db-conn { font-weight: 700; }
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
button.pager-btn { padding: 2px 10px; min-width: 0; font-size: 12pt; }
button.pager-btn:disabled { background: transparent; color: #3b4261; }
entry.page-entry { padding: 2px 4px; min-height: 0; }
"""


class Row(GObject.Object):
    """One result row (a ColumnView needs GObjects)."""
    __gtype_name__ = "DbguiRow"

    def __init__(self, cells):
        super().__init__()
        self.cells = cells


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


class Databases(View):
    title, subtitle = "Databases", "Postgres · Redis · MongoDB · S3 · SQLite"
    icon = ""
    interval = 5   # refresh(): only closes idle connections
    css = CSS
    hints = [("Enter", "open"), ("Ctrl+Enter", "run"), ("[ ]", "page"), ("n", "new connection"), ("e", "edit"), ("F5", "reload"),
             ("Del", "delete")]

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

    def conn(self, cid):
        return next((c for c in self.conns if c["id"] == cid), None)

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        return [button("+ Connection", lambda: self.form(None), "flat", tooltip="n")]

    def build(self):
        self.filter = Gtk.Entry(placeholder_text="filter (Redis: Enter scans for it)", hexpand=True)
        self.filter.connect("changed", lambda *_: self.paint_tree())
        self.filter.connect("activate", lambda *_: self.scan_redis())
        self.tree = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.tree.add_css_class("db-tree")
        self.tree.set_activate_on_single_click(False)
        self.tree.connect("row-selected", lambda _l, row: row and self.picked(row.key))
        self.tree.connect("row-activated", lambda _l, row: self.toggle(row.key))
        top = box(False, 0, self.filter)
        for edge in ("top", "start", "end", "bottom"):
            getattr(top, f"set_margin_{edge}")(10)
        left = box(True, 0, top, scrolled(self.tree), classes=("side",))
        left.set_size_request(320, -1)
        left.set_hexpand(False)   # the filter's hexpand mustn't widen the side: the results get the room

        self.stack = Gtk.Stack(hexpand=True)
        self.stack.add_named(self.build_browse(), "browse")
        self.form_page = box(True, 0)
        self.stack.add_named(scrolled(self.form_page), "form")
        self.paint_tree()
        self.show_empty()
        GLib.idle_add(lambda: self.tree.grab_focus() and False)
        return box(False, 0, left, self.stack)

    def build_browse(self):
        self.heading = label("", "heading", ellipsize=True)
        self.state = label("", "dim", ellipsize=True)
        self.actions = box(False, 8)
        self.actions.set_margin_top(10)
        head = box(True, 2, self.heading, self.state, self.actions)

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
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected", lambda *_: self.show_row())
        self.table = Gtk.ColumnView(model=self.selection, reorderable=False, show_row_separators=False,
                                    show_column_separators=False)
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
        foot = box(False, 4, self.count, self.nav["first"], self.nav["prev"], self.page_entry, self.page_of,
                   self.nav["next"], self.nav["last"], sizes)
        sizes.set_margin_start(8)
        foot.set_margin_top(6)

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
        split.set_margin_top(12)

        page = box(True, 0, head, self.query_box, split)
        for edge in ("top", "start", "end", "bottom"):
            getattr(page, f"set_margin_{edge}")(20 if edge != "bottom" else 10)
        return page

    # ---- the tree --------------------------------------------------------------------------------
    def paint_tree(self):
        keep = self.tree.get_selected_row().key if self.tree.get_selected_row() else None
        self.painting = True
        clear(self.tree)
        text = self.filter.get_text().strip().lower()
        for c in self.conns:
            cid = c["id"]
            self.add_row((cid, ()), 0, c)
            if (cid, ()) in self.open:
                self.add_kids(cid, (), 1, text)
        if not self.conns:
            hint = Gtk.ListBoxRow(selectable=False, activatable=False)
            hint.set_child(label("No connections yet: + Connection (n)", "dim"))
            hint.key = None
            self.tree.append(hint)
        if keep:
            self.select_key(keep)
        self.painting = False

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
        row.key = key
        line = box(False, 8)
        line.set_margin_start(depth * INDENT)
        if conn:
            kind = db.KINDS[conn["kind"]]
            glyph = label(kind[1], "db-glyph")
            name = label(conn["name"], "db-conn")
            note = label(conn.get("ssh") and f"via {conn['ssh']}" or conn.get("host") or os.path.basename(conn.get("path", ""))
                         or conn.get("endpoint") or kind[0], "dim", xalign=1.0, ellipsize=True)
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

    def select_key(self, key):
        r = self.tree.get_first_child()
        while r is not None:
            if getattr(r, "key", None) == key:
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

    def refresh(self):
        now = time.time()
        for cid, live in list(self.live.items()):
            if not live.jobs and now - live.used > db.IDLE:
                self.disconnect(cid)

    def set_busy(self, busy):
        self.stop_btn.set_visible(busy and self.sel is not None and self.conn(self.sel[0])["kind"] in ("postgres", "sqlite"))

    def cancel(self):
        live = self.live.get(self.sel[0]) if self.sel else None
        if live and live.driver and hasattr(live.driver, "cancel"):
            live.driver.cancel()   # thread-safe for both: libpq's cancel request, sqlite's interrupt

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
        if node and node.leaf:
            self.browse(cid, node.path)
        elif not same_conn or not node:
            self.pager = None
            self.fill(db.Result())
            self.results.set_visible(False)
        if cid in self.live or node:
            gen = self.gen
            self.job(cid, lambda d: d.info(node.path if node else ()), lambda kv: gen == self.gen and self.show_info(kv))

    def show_info(self, kv):
        if kv:
            self.state.set_text(self.state.get_text() + "  ·  " + "  ·  ".join(f"{k} {v}" for k, v in kv if v))

    def paint_actions(self, c, node):
        clear(self.actions)
        cid = c["id"]
        add = self.actions.append
        if node is None:
            add(button("Open" if cid not in self.live else "Reload", lambda: self.reload(cid), "primary", tooltip="Enter / F5"))
            add(button("Edit", lambda: self.form(c), tooltip="e"))
            if cid in self.live:
                add(button("Disconnect", lambda: self.disconnect(cid), "flat"))
            return
        if node.leaf:
            add(button("Refresh", lambda: self.reload_page(cid, node.path), tooltip="F5"))
        if c["kind"] in ("postgres", "sqlite") and node.kind in ("table", "view"):
            name = ".".join(f'"{p}"' for p in node.path)
            add(button("Query it", lambda: self.prefill(f"select * from {name} limit 100"), "flat"))
        if c["kind"] == "s3":
            if node.kind == "object":
                add(button("Download", lambda: self.s3_download(cid, node)))
            else:
                add(button("Upload…", lambda: self.s3_upload(cid, node)))
        if c["kind"] in ("redis", "s3") and node.kind not in ("bucket", "folder", "more"):
            self.del_btn = button("Delete", lambda: self.delete_node(cid, node), "danger", tooltip="Del; click twice")
            add(self.del_btn)

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
        self.show_page(0)
        self.job(cid, lambda d: d.count(path), lambda n: self.pager is pg and self.counted(n))

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
            new.total = pg.total
            self.pager = new
            self.show_page(top // size if pg.seekable else 0)
        else:
            pg.size = size

    def paint_pager(self):
        pg = self.pager
        has = bool(pg and self.table.get_columns())
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
        if [c.get_title() for c in self.table.get_columns()] != r.columns:
            for col in list(self.table.get_columns()):
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
        self.store.splice(0, self.store.get_n_items(), [Row(cells) for cells in r.rows])
        if r.columns:
            self.paint_pager()
        else:   # a statement with no rows (an UPDATE…): its message instead of a pager
            self.count.set_text(r.message or "")
            self.paint_pager()
        if len(r.rows) == 1:
            self.selection.set_selected(0)
        else:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        GLib.idle_add(lambda: self.table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None) if self.store.get_n_items() else None)

    def show_row(self):
        item = self.selection.get_selected_item()
        buf = self.rowview.get_buffer()
        buf.set_text("")
        self.rowpane.set_visible(item is not None)
        if not item:
            return
        cols = [c.get_title() for c in self.table.get_columns()]
        end = buf.get_end_iter
        for title, value in zip(cols, item.cells):
            if title:
                buf.insert_with_tags_by_name(end(), title, "key")
                buf.insert(end(), "  ")
            buf.insert(end(), value + "\n")

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
            r = 1
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
            if c:
                conn["id"] = c["id"]
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
            self.select_key((conn["id"], ()))
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

        buttons = box(False, 8, button("Save", save, "primary"), button("Test", test),
                      button("Cancel", lambda: self.stack.set_visible_child_name("browse"), "flat"))
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
        self.conns = db.load()
        self.sel = None
        self.stack.set_visible_child_name("browse")
        self.show_empty()
        self.paint_tree()
        self.say(f"deleted the connection {c['name']} (the database itself is untouched)")

    # ---- keys ------------------------------------------------------------------------------------
    def leave_text(self):
        self.tree.grab_focus()

    def key(self, keyval, state):
        if self.typing():
            return False
        row = self.tree.get_selected_row()
        key = row.key if row else None
        if keyval == Gdk.KEY_n:
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
            else:
                self.reload(key[0])
        elif keyval == Gdk.KEY_Delete and self.sel and self.sel[1] and getattr(self, "del_btn", None) \
                and self.del_btn.get_parent() is not None:
            self.delete_node(self.sel[0], self.sel[1])
        elif keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            self.turn("prev" if keyval == Gdk.KEY_bracketleft else "next")
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
