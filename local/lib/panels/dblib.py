#!/usr/bin/env python3
"""dblib.py — the database client's data side: saved connections, their secrets, SSH tunnels and one
driver per kind (Postgres, Redis, MongoDB, S3, SQLite). dbgui.py (workspace 6) draws it.

Connections are ~/.local/share/dbclient/connections.json (hosts and users only, mode 600, never in the
dotfiles: they name private servers); passwords, keys and URIs with a password in them are in the
keyring (secret-tool, gnome-keyring), under `app dbclient id <id>`.

Light on the PC: a driver's library is imported when a connection of its kind first opens, nothing
connects until you open it, results come a page at a time (LIMIT/OFFSET, a server-side cursor, SCAN,
S3 pages), and the panel closes a connection (and its tunnel) after IDLE seconds unused.

Every driver has the same shape, called from one worker thread per connection:
  children(path)          the tree under path (() = the top): [Node]
  browse(path, offset, limit)   a page of a leaf's contents: Result (next_at: where a cursor's next page starts)
  count(path)             (rows, exact?) in a leaf, or None when that isn't cheap
  run(text, path, limit)  the query box, with the selected node as context: its first page
  more(limit)             the next page of the last run()
  info(path)              [(key, value)] about a node
  close()
"""
import json, os, shlex, socket, subprocess, time, uuid

DIR = os.path.expanduser("~/.local/share/dbclient")
LIST = f"{DIR}/connections.json"
PAGE = 200            # rows / keys / objects fetched at a time
IDLE = 180            # seconds unused before a connection closes
CELL = 2000           # a cell's text is cut here (the row view shows what's left of a long value)
KINDS = {             # kind: (name, Nerd Font glyph, default port)
    "postgres": ("Postgres", "", 5432),
    "redis": ("Redis", "", 6379),
    "mongo": ("MongoDB", "", 27017),
    "s3": ("S3", "\uf0c2", 0),
    "sqlite": ("SQLite", "", 0),
}
# the fields of each kind's form: (key, label, secret?)
FIELDS = {
    "postgres": [("host", "Host", False), ("port", "Port", False), ("user", "User", False),
                 ("password", "Password", True), ("database", "Database", False), ("sslmode", "SSL mode", False)],
    "redis": [("host", "Host", False), ("port", "Port", False), ("user", "User (optional)", False),
              ("password", "Password", True), ("database", "Database number", False), ("tls", "TLS (yes / no)", False)],
    "mongo": [("uri", "Connection string", True), ("database", "Database (optional)", False)],
    "s3": [("endpoint", "Endpoint (empty = AWS)", False), ("region", "Region", False),
           ("access_key", "Access key", False), ("secret_key", "Secret key", True), ("bucket", "Bucket (optional)", False)],
    "sqlite": [("path", "File", False)],
}
SECRET_KEYS = {k for fields in FIELDS.values() for k, _l, s in fields if s}


class DbError(Exception):
    pass


class Node:
    """A row of the tree. path: the keys from the connection down to it; leaf: has contents to browse."""
    __slots__ = ("path", "name", "kind", "leaf", "note")

    def __init__(self, path, name, kind, leaf=False, note=""):
        self.path, self.name, self.kind, self.leaf, self.note = tuple(path), name, kind, leaf, note


class Result:
    """A page of rows: columns, rows (lists of cell text), more (another page exists), message.
    next_at: where the next page starts when it isn't an offset (a Redis hash / set scan's cursor)."""

    def __init__(self, columns=(), rows=(), more=False, message="", next_at=None):
        self.columns, self.rows, self.more, self.message = list(columns), list(rows), more, message
        self.next_at = next_at


def cell(v):
    if v is None:
        return "NULL"
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        try:
            v = b.decode()
        except UnicodeDecodeError:
            return f"<{len(b)} bytes> " + b[:48].hex()
    elif isinstance(v, (dict, list)):
        v = json.dumps(v, default=str, ensure_ascii=False)
    else:
        v = str(v)
    return v if len(v) <= CELL else v[:CELL] + "…"


def size(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ---- saved connections -------------------------------------------------------------------------
def load():
    try:
        with open(LIST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(conns):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)   # for you only: the list names private servers, Ask's chats are kept under it
    fd = os.open(LIST, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conns, f, indent=2)


def secret(cid):
    """The connection's secrets ({field: value}) from the keyring."""
    try:
        out = subprocess.run(["secret-tool", "lookup", "app", "dbclient", "id", cid],
                             capture_output=True, text=True, timeout=10).stdout
        return json.loads(out) if out else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def store_secret(cid, name, values):
    if not values:
        subprocess.run(["secret-tool", "clear", "app", "dbclient", "id", cid], capture_output=True, timeout=10)
        return
    p = subprocess.run(["secret-tool", "store", "--label", f"Database client: {name}", "app", "dbclient", "id", cid],
                       input=json.dumps(values), capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise DbError("couldn't save the password in the keyring: " + (p.stderr.strip() or "secret-tool failed"))


def put(conn, secrets):
    """Add or update a connection (conn["id"] set = update); secrets go to the keyring."""
    conns = load()
    conn.setdefault("id", uuid.uuid4().hex[:12])
    store_secret(conn["id"], conn["name"], {k: v for k, v in secrets.items() if v})
    conns = [c for c in conns if c["id"] != conn["id"]] + [conn]
    save(sorted(conns, key=lambda c: c["name"].lower()))
    return conn


def remove(cid):
    save([c for c in load() if c["id"] != cid])
    store_secret(cid, "", {})


def parse_url(url):
    """A pasted postgres:// / redis(s):// URL → (fields, secrets), so a provider's string fills the form."""
    from urllib.parse import parse_qs, unquote, urlparse
    u = urlparse(url.strip())
    q = parse_qs(u.query)
    fields = {"host": u.hostname or "", "port": str(u.port or ""), "user": unquote(u.username or "")}
    if u.scheme.startswith("postgres"):
        fields.update(database=u.path.lstrip("/"), sslmode=q.get("sslmode", [""])[0])
    elif u.scheme.startswith("redis"):
        fields.update(database=u.path.lstrip("/") or "0", tls="yes" if u.scheme == "rediss" else "no")
    else:
        raise DbError("not a postgres:// or redis:// URL")
    return fields, {"password": unquote(u.password or "")}


# ---- SSH tunnels -------------------------------------------------------------------------------
class Tunnel:
    """ssh -L to host:port through an SSH host (your ~/.ssh/config and agent: key login only)."""

    def __init__(self, ssh, host, port):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.proc = subprocess.Popen(
            ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=30", "-L", f"127.0.0.1:{self.port}:{host}:{port}", ssh],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise DbError(f"SSH to {ssh}: " + (self.proc.stderr.read().strip() or "failed"))
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.3).close()
                return
            except OSError:
                time.sleep(0.2)
        self.close()
        raise DbError(f"SSH to {ssh}: the tunnel didn't open")

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def open_driver(conn):
    """Connect: the keyring's secrets, a tunnel if the connection goes through SSH, then the driver."""
    sec = secret(conn["id"])
    cfg = {**conn, **sec}
    tunnel = None
    if conn.get("ssh") and conn["kind"] in ("postgres", "redis"):
        tunnel = Tunnel(conn["ssh"], cfg.get("host") or "127.0.0.1", int(cfg.get("port") or KINDS[conn["kind"]][2]))
        cfg.update(host="127.0.0.1", port=tunnel.port)
    try:
        d = DRIVERS[conn["kind"]](cfg)
    except Exception:
        if tunnel:
            tunnel.close()
        raise
    d.tunnel = tunnel
    return d


class Driver:
    tunnel = None
    run_hint = ""     # the query box's placeholder; empty = this kind has no query box

    def close(self):
        if self.tunnel:
            self.tunnel.close()

    def more(self, limit=PAGE):
        raise DbError("nothing more")

    def count(self, path):
        """(rows in the leaf at path, exact?) for "page 3 of 12", or None when it isn't cheap to know."""
        return None

    def info(self, path):
        return []


class Lookahead:
    """A cursor's rows a page at a time, one row read ahead: "more" is then exact (a query that ends on a
    page boundary has no empty last page)."""

    def __init__(self, it):
        self.it, self.ahead = iter(it), []

    def take(self, n):
        rows = self.ahead
        for r in self.it:
            rows.append(r)
            if len(rows) > n:
                break
        self.ahead = rows[n:]
        return rows[:n], bool(self.ahead)


def need(module, package):
    try:
        return __import__(module)
    except ImportError:
        raise DbError(f"{package} isn't installed: sudo pacman -S {package}")


# ---- SQL: Postgres, SQLite ---------------------------------------------------------------------
def statement_kind(text):
    """The first keyword: a query that returns rows gets a cursor that pages."""
    words = text.lstrip().split(None, 1)
    return words[0].lower() if words else ""


class Postgres(Driver):
    run_hint = "SQL — Ctrl+Enter runs it (or the selected part)"

    def __init__(self, cfg):
        self.pg = need("psycopg", "python-psycopg")
        kw = {"host": cfg.get("host") or "localhost", "port": int(cfg.get("port") or 5432),
              "user": cfg.get("user") or None, "password": cfg.get("password") or None,
              "dbname": cfg.get("database") or "postgres", "connect_timeout": 10, "application_name": "dbclient"}
        if cfg.get("sslmode"):
            kw["sslmode"] = cfg["sslmode"]
        try:
            self.conn = self.pg.connect(autocommit=True, **kw)   # full access: every statement applies at once
        except self.pg.Error as e:
            raise DbError(str(e).strip())
        self.cur = None

    def children(self, path):
        if not path:
            rows = self.q("select schema_name from information_schema.schemata where schema_name !~ '^pg_' "
                          "and schema_name <> 'information_schema' order by schema_name <> 'public', schema_name")
            return [Node((s,), s, "schema") for (s,) in rows]
        rows = self.q("select table_name, table_type from information_schema.tables where table_schema = %s "
                      "order by table_name", (path[0],))
        return [Node((path[0], t), t, "view" if ty == "VIEW" else "table", True) for t, ty in rows]

    def q(self, sql, args=()):
        try:
            with self.conn.cursor() as c:
                c.execute(sql, args)
                return c.fetchall()
        except self.pg.Error as e:
            raise DbError(str(e).strip())

    def browse(self, path, offset=0, limit=PAGE):
        s = self.pg.sql
        query = s.SQL("select * from {}.{} limit {} offset {}").format(
            s.Identifier(path[0]), s.Identifier(path[1]), s.Literal(limit + 1), s.Literal(offset))
        try:
            with self.conn.cursor() as c:
                c.execute(query)
                rows = c.fetchall()
                cols = [d.name for d in c.description]
        except self.pg.Error as e:
            raise DbError(str(e).strip())
        return Result(cols, [[cell(v) for v in r] for r in rows[:limit]], len(rows) > limit)

    def count(self, path):
        """Exact (count(*), given 3 s) unless the planner knows the table is huge; then its estimate.
        Views and never-analyzed tables have no estimate (-1): they're counted too."""
        est = self.q("select c.reltuples::bigint from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                     "where n.nspname = %s and c.relname = %s", path)
        est = est[0][0] if est else -1
        if est <= 200000:
            s = self.pg.sql
            try:
                with self.conn.transaction(), self.conn.cursor() as c:
                    c.execute("set local statement_timeout = 3000")
                    c.execute(s.SQL("select count(*) from {}.{}").format(s.Identifier(path[0]), s.Identifier(path[1])))
                    return c.fetchone()[0], True
            except self.pg.Error:
                pass
        return (est, False) if est > 0 else None

    def run(self, text, path, limit=PAGE):
        self.drop_cursor()
        try:
            if statement_kind(text) in ("select", "with", "table", "values", "show", "explain"):
                # a server-side cursor: the rows stay on the server, a page comes over at a time
                self.cur = self.conn.cursor(name=f"dbclient_{uuid.uuid4().hex[:8]}", withhold=True)
                self.cur.execute(text)
                # rows come over PAGE at a time whatever the page size: few round trips, little memory
                self.flat = Lookahead(r for batch in iter(lambda: self.cur.fetchmany(PAGE), []) for r in batch)
                return self.page(limit)
            with self.conn.cursor() as c:
                c.execute(text)
                if c.description:
                    rows = c.fetchmany(limit)
                    return Result([d.name for d in c.description], [[cell(v) for v in r] for r in rows],
                                  message=c.statusmessage or "")
                return Result(message=f"{c.statusmessage or 'done'}" + (f" · {c.rowcount} rows" if c.rowcount >= 0 else ""))
        except self.pg.Error as e:
            self.drop_cursor()
            raise DbError(str(e).strip())

    def page(self, limit):
        cols = [d.name for d in self.cur.description] if self.cur.description else []
        rows, more = self.flat.take(limit)
        if not more:
            self.drop_cursor()
        return Result(cols, [[cell(v) for v in r] for r in rows], more)

    def more(self, limit=PAGE):
        if not self.cur:
            raise DbError("nothing more")
        try:
            return self.page(limit)
        except self.pg.Error as e:
            self.drop_cursor()
            raise DbError(str(e).strip())

    def drop_cursor(self):
        if self.cur:
            try:
                self.cur.close()
            except Exception:
                pass
            self.cur = None

    def cancel(self):
        """From another thread: stop the running statement."""
        self.conn.cancel_safe()

    def info(self, path):
        if len(path) < 2:
            return [("server", self.conn.info.parameter_status("server_version") or "")]
        rows = self.q("select pg_size_pretty(pg_total_relation_size(c.oid)), c.reltuples::bigint from pg_class c "
                      "join pg_namespace n on n.oid = c.relnamespace where n.nspname = %s and c.relname = %s", path)
        cols = self.q("select column_name || ' ' || data_type from information_schema.columns "
                      "where table_schema = %s and table_name = %s order by ordinal_position", path)
        out = [("size", rows[0][0])] if rows else []
        if rows and rows[0][1] > 0:   # the planner's estimate: -1 / 0 until the table is first analyzed
            out.append(("rows (estimate)", str(rows[0][1])))
        return out + [("columns", ", ".join(c for (c,) in cols))]

    def close(self):
        self.drop_cursor()
        try:
            self.conn.close()
        finally:
            super().close()


class Sqlite(Driver):
    run_hint = "SQL — Ctrl+Enter runs it (or the selected part)"

    def __init__(self, cfg):
        import sqlite3
        self.sq = sqlite3
        path = os.path.expanduser(cfg.get("path") or "")
        if not os.path.isfile(path):
            raise DbError(f"no such file: {path}")
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.cur = None

    def children(self, path):
        rows = self.conn.execute("select name, type from sqlite_master where type in ('table', 'view') "
                                 "and name not like 'sqlite_%' order by name").fetchall()
        return [Node((n,), n, t, True) for n, t in rows]

    def name(self, path):
        return '"' + path[0].replace('"', '""') + '"'

    def browse(self, path, offset=0, limit=PAGE):
        try:
            c = self.conn.execute(f"select * from {self.name(path)} limit ? offset ?", (limit + 1, offset))
        except self.sq.Error as e:
            raise DbError(str(e))
        rows = c.fetchall()
        return Result([d[0] for d in c.description], [[cell(v) for v in r] for r in rows[:limit]], len(rows) > limit)

    def count(self, path):
        try:
            return self.conn.execute(f"select count(*) from {self.name(path)}").fetchone()[0], True
        except self.sq.Error:
            return None

    def run(self, text, path, limit=PAGE):
        try:
            self.cur = self.conn.execute(text) if statement_kind(text) else None
        except self.sq.Error as e:
            raise DbError(str(e))
        if not self.cur or not self.cur.description:
            n = self.cur.rowcount if self.cur else -1
            self.cur = None
            return Result(message="done" + (f" · {n} rows" if n >= 0 else ""))
        self.flat = Lookahead(self.cur)
        return self.page(limit)

    def page(self, limit):
        cols = [d[0] for d in self.cur.description]
        rows, more = self.flat.take(limit)
        if not more:
            self.cur = None
        return Result(cols, [[cell(v) for v in r] for r in rows], more)

    def more(self, limit=PAGE):
        if not self.cur:
            raise DbError("nothing more")
        return self.page(limit)

    def cancel(self):
        self.conn.interrupt()

    def info(self, path):
        if not path:
            return []
        cols = self.conn.execute(f"pragma table_info({self.name(path)})").fetchall()
        return [("columns", ", ".join(f"{c[1]} {c[2]}".strip() for c in cols))]

    def close(self):
        self.conn.close()


# ---- Redis -------------------------------------------------------------------------------------
class Redis(Driver):
    run_hint = "a Redis command, e.g. GET user:1 · HGETALL session:42 · DEL key — Ctrl+Enter"

    def __init__(self, cfg):
        redis = need("redis", "python-redis")
        self.r = redis.Redis(host=cfg.get("host") or "localhost", port=int(cfg.get("port") or 6379),
                             username=cfg.get("user") or None, password=cfg.get("password") or None,
                             db=int(cfg.get("database") or 0), ssl=(cfg.get("tls") or "").lower() in ("yes", "true", "1"),
                             socket_connect_timeout=10, socket_timeout=30)
        self.err = redis.RedisError
        try:
            self.r.ping()
        except self.err as e:
            raise DbError(str(e))
        self.pattern = "*"
        self.cursor = 0     # the key scan's next cursor (0 once it has gone round)
        self.reply = None   # a command's reply, paged here (KEYS * can be huge)

    def children(self, path, pattern=None):
        """The keys, SCAN a page at a time: path () starts again, ("…",) continues the same scan."""
        if not path:   # a new scan: for the pattern, or every key
            self.pattern, self.cursor = pattern or "*", 0
        keys, cur = [], self.cursor
        try:
            while len(keys) < PAGE:
                cur, got = self.r.scan(cur, match=self.pattern, count=500)
                keys += got
                if cur == 0:
                    break
            pipe = self.r.pipeline(transaction=False)
            for k in keys:
                pipe.type(k)
            types = pipe.execute()
        except self.err as e:
            raise DbError(str(e))
        self.cursor = cur
        nodes = [Node((k.decode(errors="replace"),), k.decode(errors="replace"), t.decode(), True)
                 for k, t in sorted(zip(keys, types))]
        if cur:
            nodes.append(Node(("…",), "more keys", "more"))
        return nodes

    def browse(self, path, offset=0, limit=PAGE):
        """offset: a list / sorted set's index; for a hash or set the SCAN cursor and for a stream the last
        id seen (those can't jump to a page: the GUI walks them with next_at)."""
        k = path[0]
        try:
            t = self.r.type(k).decode()
            if t == "string":
                return Result(["value"], [[cell(self.r.get(k))]])
            if t == "hash":
                cur, items = self.r.hscan(k, offset or 0, count=limit)
                return Result(["field", "value"], [[cell(f), cell(v)] for f, v in items.items()], bool(cur), next_at=cur)
            if t == "list":
                items = self.r.lrange(k, offset, offset + limit)
                return Result(["#", "value"], [[str(offset + i), cell(v)] for i, v in enumerate(items[:limit])], len(items) > limit)
            if t == "set":
                cur, items = self.r.sscan(k, offset or 0, count=limit)
                return Result(["member"], [[cell(v)] for v in items], bool(cur), next_at=cur)
            if t == "zset":
                items = self.r.zrange(k, offset, offset + limit, withscores=True)
                return Result(["member", "score"], [[cell(m), str(s)] for m, s in items[:limit]], len(items) > limit)
            if t == "stream":
                items = self.r.xrange(k, min=f"({offset}" if offset else "-", count=limit + 1)
                rows = [[cell(i), cell({cell(a): cell(b) for a, b in f.items()})] for i, f in items[:limit]]
                return Result(["id", "fields"], rows, len(items) > limit, next_at=rows[-1][0] if rows else None)
            return Result(message=f"{k}: {t}")
        except self.err as e:
            raise DbError(str(e))

    def count(self, path):
        k = path[0]
        try:
            t = self.r.type(k).decode()
            if t == "string":
                return 1, True
            n = {"hash": self.r.hlen, "list": self.r.llen, "set": self.r.scard, "zset": self.r.zcard,
                 "stream": self.r.xlen}.get(t)
            return (n(k), True) if n else None
        except self.err:
            return None

    def run(self, text, path, limit=PAGE):
        try:
            args = shlex.split(text)
        except ValueError as e:
            raise DbError(str(e))
        if not args:
            return Result()
        try:
            out = self.r.execute_command(*args)
        except self.err as e:
            raise DbError(str(e))
        if isinstance(out, dict):
            self.reply = (["field", "value"], [[cell(a), cell(b)] for a, b in out.items()])
        elif isinstance(out, (list, tuple)):
            self.reply = (["#", "value"], [[str(i), cell(v)] for i, v in enumerate(out)])
        else:
            return Result(["result"], [[cell(out)]])
        self.at = 0
        return self.more(limit)

    def more(self, limit=PAGE):
        if not self.reply:
            raise DbError("nothing more")
        cols, rows = self.reply
        page, self.at = rows[self.at:self.at + limit], self.at + limit
        more = self.at < len(rows)
        if not more:
            self.reply = None
        return Result(cols, page, more)

    def info(self, path):
        if not path:
            i = self.r.info()
            return [("server", i.get("redis_version", "")), ("keys", str(self.r.dbsize())), ("memory", i.get("used_memory_human", ""))]
        k = path[0]
        ttl = self.r.ttl(k)
        out = [("type", self.r.type(k).decode()), ("expires", "never" if ttl == -1 else f"in {ttl} s")]
        try:
            out.append(("memory", size(self.r.memory_usage(k) or 0)))
        except self.err:
            pass
        return out

    def delete(self, path):
        self.r.delete(path[0])

    def close(self):
        self.r.close()
        super().close()


# ---- MongoDB -----------------------------------------------------------------------------------
class Mongo(Driver):
    run_hint = 'on the selected collection: a filter {"age": {"$gt": 30}} or a pipeline [{"$group": …}] — Ctrl+Enter'

    def __init__(self, cfg):
        pymongo = need("pymongo", "python-pymongo")
        from bson import ObjectId, json_util
        self.ju, self.oid = json_util, ObjectId
        self.err = pymongo.errors.PyMongoError
        try:
            self.c = pymongo.MongoClient(cfg.get("uri") or "mongodb://localhost", serverSelectionTimeoutMS=10000,
                                         appname="dbclient")
            self.c.admin.command("ping")
        except self.err as e:
            raise DbError(str(e))
        self.only = cfg.get("database") or ""
        self.cur = None

    def children(self, path):
        try:
            if not path:
                names = [self.only] if self.only else self.c.list_database_names()
                return [Node((n,), n, "database") for n in names]
            return [Node((path[0], n), n, "collection", True) for n in sorted(self.c[path[0]].list_collection_names())]
        except self.err as e:
            raise DbError(str(e))

    def docs(self, docs):
        cols = []
        for d in docs:
            cols += [k for k in d if k not in cols]
        return cols, [[self.value(d[k]) if k in d else "" for k in cols] for d in docs]

    def value(self, v):
        """A field as text: strings and ids plain, the rest as (extended) JSON."""
        if isinstance(v, (str, self.oid)):
            return cell(str(v))
        return cell(self.ju.dumps(v))

    def browse(self, path, offset=0, limit=PAGE):
        try:
            docs = list(self.c[path[0]][path[1]].find().skip(offset).limit(limit + 1))
        except self.err as e:
            raise DbError(str(e))
        cols, rows = self.docs(docs[:limit])
        return Result(cols, rows, len(docs) > limit)

    def count(self, path):
        try:   # from the collection's metadata: no scan
            return self.c[path[0]][path[1]].estimated_document_count(), True
        except self.err:
            return None

    def run(self, text, path, limit=PAGE):
        if len(path) < 2:
            raise DbError("select a collection first")
        try:
            q = self.ju.loads(text or "{}")
        except ValueError as e:
            raise DbError(f"not JSON: {e}")
        coll = self.c[path[0]][path[1]]
        try:
            self.cur = Lookahead(coll.aggregate(q) if isinstance(q, list) else coll.find(q))
            return self.page(limit)
        except self.err as e:
            raise DbError(str(e))

    def page(self, limit):
        try:
            docs, more = self.cur.take(limit)
        except self.err as e:
            self.cur = None
            raise DbError(str(e))
        if not more:
            self.cur = None
        cols, rows = self.docs(docs)
        return Result(cols, rows, more)

    def more(self, limit=PAGE):
        if not self.cur:
            raise DbError("nothing more")
        return self.page(limit)

    def info(self, path):
        try:
            if len(path) == 2:
                s = self.c[path[0]].command("collStats", path[1])
                return [("documents", str(s.get("count", ""))), ("size", size(s.get("size", 0))),
                        ("indexes", ", ".join(self.c[path[0]][path[1]].index_information()))]
            if path:
                s = self.c[path[0]].command("dbStats")
                return [("collections", str(s.get("collections", ""))), ("size", size(s.get("dataSize", 0)))]
            return [("server", self.c.server_info().get("version", ""))]
        except self.err:
            return []

    def close(self):
        self.c.close()


# ---- S3 ----------------------------------------------------------------------------------------
class S3(Driver):
    def __init__(self, cfg):
        need("boto3", "python-boto3")
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
        self.err = (BotoCoreError, ClientError)
        self.s3 = boto3.client("s3", endpoint_url=cfg.get("endpoint") or None, region_name=cfg.get("region") or None,
                               aws_access_key_id=cfg.get("access_key") or None,
                               aws_secret_access_key=cfg.get("secret_key") or None,
                               config=Config(connect_timeout=10, retries={"max_attempts": 2}))
        self.only = cfg.get("bucket") or ""
        self.tokens = {}    # (bucket, prefix): the next page's continuation token

    def children(self, path):
        """() → buckets; (bucket, prefix) → its folders and files, a page at a time."""
        try:
            if not path:
                names = [self.only] if self.only else [b["Name"] for b in self.s3.list_buckets().get("Buckets", [])]
                if not names:   # keys limited to one bucket can't list them: an empty tree would just look broken
                    raise DbError("no buckets visible with these keys: put the bucket's name in the connection (e)")
                return [Node((b, ""), b, "bucket") for b in names]
            bucket, prefix = path[0], path[1]
            more = len(path) > 2   # (bucket, prefix, "…"): the next page
            kw = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/", "MaxKeys": PAGE}
            if more and self.tokens.get((bucket, prefix)):
                kw["ContinuationToken"] = self.tokens[(bucket, prefix)]
            r = self.s3.list_objects_v2(**kw)
        except self.err as e:
            raise DbError(str(e))
        self.tokens[(bucket, prefix)] = r.get("NextContinuationToken")
        nodes = [Node((bucket, p["Prefix"]), p["Prefix"][len(prefix):], "folder") for p in r.get("CommonPrefixes", [])]
        nodes += [Node((bucket, o["Key"], "object"), o["Key"][len(prefix):], "object", True, size(o["Size"]))
                  for o in r.get("Contents", []) if o["Key"] != prefix]
        if r.get("IsTruncated"):
            nodes.append(Node((bucket, prefix, "…"), "more", "more"))
        return nodes

    def browse(self, path, offset=0, limit=PAGE):
        bucket, key = path[0], path[1]
        try:
            head = self.s3.head_object(Bucket=bucket, Key=key)
            rows = [["size", size(head["ContentLength"])], ["type", head.get("ContentType", "")],
                    ["modified", str(head.get("LastModified", ""))], ["etag", head.get("ETag", "").strip('"')]]
            rows += [[f"meta: {k}", v] for k, v in head.get("Metadata", {}).items()]
            ctype = head.get("ContentType", "")
            if head["ContentLength"] and (ctype.startswith("text/") or ctype.endswith(("json", "xml", "yaml", "csv"))):
                body = self.s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-16383")["Body"].read()
                rows.append(["contents (first 16 K)", body.decode(errors="replace")])
        except self.err as e:
            raise DbError(str(e))
        return Result(["field", "value"], rows)

    def download(self, path, dest):
        try:
            self.s3.download_file(path[0], path[1], dest)
        except self.err as e:
            raise DbError(str(e))

    def upload(self, path, src):
        """Into the folder (or bucket) at path, under the file's own name."""
        key = path[1] + os.path.basename(src)
        try:
            self.s3.upload_file(src, path[0], key)
        except self.err as e:
            raise DbError(str(e))
        return key

    def delete(self, path):
        try:
            self.s3.delete_object(Bucket=path[0], Key=path[1])
        except self.err as e:
            raise DbError(str(e))

    def run(self, text, path, limit=PAGE):
        raise DbError("S3 has no queries: browse the buckets on the left")

    def close(self):
        self.s3.close()


DRIVERS = {"postgres": Postgres, "redis": Redis, "mongo": Mongo, "s3": S3, "sqlite": Sqlite}
