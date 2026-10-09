#!/usr/bin/env python3
# dbaskgui.py — Ask, the database client's AI: a popup like every other panel. The Databases window
# (dbgui.py) opens it (a, or Ask) for the connection and what's selected, in DBASK_CONTEXT (the
# environment, not the command line: other users can read a process's arguments, not its environment):
#   {"id": connection id, "selected": "public.orders", "columns": "id, status…", "query": the query box}
# It's the System agent's window (sysagentgui.SysAgent) with dbagent.py answering: read-only on a
# connection of its own, the structure only unless the connection's "AI sees data" is on (the switch
# here). A query it suggests gets "Put in query box": sent to the Databases window (its "use-query"
# action, on your session bus), which puts it there and asks for a second Run if it writes.
# The chats are kept per connection (dbagent.chats_dir), for you only.
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gio, GLib, Gtk, box, button, label, run, switch
import dbagent
import dblib as db
import sysagentgui

CSS = """
.ask-about { color: #565f89; }
button.ask-use { padding: 2px 10px; min-height: 0; font-size: 9pt; }
"""
APP = "sami.databases"   # the Databases window's app id: its actions are on the bus under its path


class DbAsk(sysagentgui.SysAgent):
    css = sysagentgui.CSS + CSS
    title = "Ask"

    def __init__(self, ctx):
        self.ctx = ctx
        self.conn = next((c for c in db.load() if c["id"] == ctx.get("id")), None)
        self.chats_dir = dbagent.chats_dir(ctx.get("id", "none"))
        self.subtitle = (self.conn["name"] if self.conn else "no connection") + (
            f" › {ctx['selected']}" if ctx.get("selected") else "")
        self.intro = dbagent.intro(self.conn) if self.conn else "Open Ask from a connection in the Databases window."
        super().__init__()

    def build(self):
        content = super().build()
        main = content.get_last_child()   # the conversation and the question line
        self.sees = switch(bool(self.conn and self.conn.get("ai_data")), self.set_data)
        self.sees_note = label("", "ask-about", wrap=True)
        self.sees_note.set_hexpand(True)
        top = box(False, 10, label("AI sees data", "ask-about"), self.sees, self.sees_note)
        main.prepend(top)
        self.paint_sees()
        if not self.conn:
            self.entry.set_sensitive(False)
            self.sees.set_sensitive(False)
        else:
            self.entry.set_placeholder_text(f"Ask about {self.ctx.get('selected') or self.conn['name']}…")
        return content

    def paint_sees(self):
        on = bool(self.conn and self.conn.get("ai_data"))
        self.sees_note.set_text("on: it can read rows and values, and they go to the model" if on else
                                "off: it sees names, types, indexes and counts, never rows or values")

    def set_data(self, on):
        """This connection's "AI sees data", kept with it (connections.json, this PC only)."""
        conns = db.load()
        for c in conns:
            if c["id"] == self.conn["id"]:
                c["ai_data"] = on
                self.conn = c
        db.save(conns)
        self.paint_sees()

    def answer(self, question, chat, model, on_call, stop):
        conn = next((c for c in db.load() if c["id"] == self.conn["id"]), self.conn)   # the switch as it is now
        return dbagent.ask(conn, question, chat["history"], self.ctx.get("selected", ""), self.ctx.get("columns", ""),
                           self.ctx.get("query", ""), on_call, stop, model)

    def paint_message(self, m):
        super().paint_message(m)
        if m["who"] != "ai" or not m.get("ok", True):
            return
        msg = self.log.get_last_child()
        for q in dbagent.queries(m["text"]):
            use = button("↳ Put in query box", lambda q=q: self.use(q), "flat", "ask-use",
                         tooltip=q.splitlines()[0][:200])
            use.set_halign(Gtk.Align.START)
            msg.append(use)

    def use(self, q):
        """The query to the Databases window's query box (it asks for a second Run if it writes), then close."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            actions = Gio.DBusActionGroup.get(bus, APP, "/" + APP.replace(".", "/"))
            actions.activate_action("use-query", GLib.Variant("s", json.dumps({"id": self.conn["id"], "q": q})))
            bus.flush_sync(None)
        except GLib.Error as e:
            return self.say(f"couldn't reach the Databases window: {e.message}", "bad")
        self.close()


if __name__ == "__main__":
    try:
        ctx = json.loads(os.environ.get("DBASK_CONTEXT") or "{}")
    except ValueError:
        ctx = {}
    run(DbAsk(ctx), "panels.dbask", (1100, 680))
