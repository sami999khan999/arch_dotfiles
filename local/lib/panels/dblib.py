#!/usr/bin/env python3
"""dblib.py — the database client's data side: saved connections, their secrets, SSH tunnels and one
driver per kind (Postgres, Redis, MongoDB, S3, SQLite). dbgui.py (workspace 6) draws it.

Connections are ~/.local/share/dbclient/connections.json (hosts and users only, mode 600, never in the
dotfiles: they name private servers); passwords, keys and URIs with a password in them are in the
keyring (secret-tool, gnome-keyring), under `app dbclient id <id>`. A connection's "project" groups it in
the list. The databases running in Docker are found by docker_scan() and kept as connections of their
own (docker_merge(): "docker" is the container's compose project / service, the project theirs), so a
rename sticks; one deleted is remembered in docker-ignored.json and not added again.

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
and, to change what a leaf holds (its rows, documents, a key's items, a folder's files), one row or many:
  editor(path)            how: None (it can't be changed: a view) or an Editor
  record(path, key)       one row in full, for its edit form: {field: text or None}, or a document's JSON
  insert(path, values)    a new row (values {field: text or None}, or a document's / documents' JSON)
  update(path, keys, values)   the same change to every row in keys: only the fields in values
  remove(path, keys)      those rows; each returns a message ("3 rows deleted")
where a row's key is the hidden one browse() gave it (Result.keys: a primary key, a ctid, an _id…).
Postgres and SQLite also have liam(schema): the schema as Liam ERD's schema.json, for dbliam.py.
"""
import json, os, shlex, socket, subprocess, tempfile, time, uuid

DIR = os.path.expanduser("~/.local/share/dbclient")
LIST = f"{DIR}/connections.json"
PAGE = 200            # rows / keys / objects fetched at a time
IDLE = 180            # seconds unused before a connection closes
CELL = 2000           # a cell's text is cut here (the row view shows what's left of a long value)
IMAGE = 16 << 20      # an S3 image bigger than this isn't previewed
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".bmp", ".ico", ".tif", ".tiff")
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
    next_at: where the next page starts when it isn't an offset (a Redis hash / set scan's cursor).
    keys: each row's key for record() / update() / remove(), when the rows can be changed."""

    def __init__(self, columns=(), rows=(), more=False, message="", next_at=None, keys=None):
        self.columns, self.rows, self.more, self.message = list(columns), list(rows), more, message
        self.next_at, self.keys = next_at, keys


class Field:
    """A field of an edit form. type: shown beside its name; null: can be NULL; default: what an insert
    leaves out gets; fixed: computed by the database (a generated column), never written."""
    __slots__ = ("name", "type", "null", "default", "fixed")

    def __init__(self, name, type="", null=False, default="", fixed=False):
        self.name, self.type, self.null, self.default, self.fixed = name, type, null, default, fixed


class Editor:
    """How a leaf's rows are changed. noun: what a row is ("row", "document", "field"…); fields: the form's
    (json: one JSON text instead); new / edit / delete: which of those it has; many: what an edit of several
    rows sets (a hint; "" = they can't be edited together); files: new rows are uploaded files."""

    def __init__(self, noun, fields=(), json=False, new=True, edit=True, delete=True, many="", files=False, hint=""):
        self.noun, self.fields, self.json, self.files, self.hint = noun, list(fields), json, files, hint
        self.new, self.edit, self.delete, self.many = new, edit, delete, many


def text(v):
    """A stored value in full, for an edit form: str, or None (NULL)."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode(errors="replace")
    return str(v)


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
    gone = next((c for c in load() if c["id"] == cid), None)
    save([c for c in load() if c["id"] != cid])
    store_secret(cid, "", {})
    if gone and gone.get("docker"):   # found in Docker: not added back by the next scan
        ignored = docker_ignored() | {gone["docker"]}
        with open(IGNORED, "w") as f:
            json.dump(sorted(ignored), f)


def rename(cids, name=None, project=None):
    """A new name for a connection, or a new project for some (project "" takes them out of theirs)."""
    conns = load()
    for c in conns:
        if c["id"] in cids:
            if name is not None:
                c["name"] = name
            if project is not None:
                c.pop("project", None)
                if project:
                    c["project"] = project
    save(sorted(conns, key=lambda c: c["name"].lower()))


# ---- the databases running in Docker -------------------------------------------------------------
IGNORED = f"{DIR}/docker-ignored.json"
# an image's own name (its last part, no tag) → (kind, the port it listens on inside the container); exact,
# so redis-commander or mongo-express (admin UIs) aren't taken for the database
DOCKER_IMAGES = {**dict.fromkeys(("postgres", "postgis", "pgvector", "timescaledb", "timescaledb-ha"), ("postgres", 5432)),
                 **dict.fromkeys(("redis", "redis-stack", "redis-stack-server", "valkey", "keydb", "dragonfly"),
                                 ("redis", 6379)),
                 **dict.fromkeys(("mongo", "mongodb-community-server"), ("mongo", 27017)),
                 "minio": ("s3", 9000)}


def docker_ignored():
    try:
        with open(IGNORED) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def docker_kind(image):
    """(kind, port inside) for a database image, else None."""
    return DOCKER_IMAGES.get(image.split("@")[0].rsplit("/", 1)[-1].split(":")[0].lower())


def docker_scan():
    """The database containers running now: [{"docker": key, "project", "service", fields…, "secrets"}].
    Reached on their published port, else on the container's own address (Docker's bridge, from this PC);
    the credentials are the ones its image's environment sets up."""
    try:
        ids = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=10).stdout.split()
        if not ids:
            return []
        info = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True,
                                         timeout=15).stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None   # no docker: leave the list as it is
    out = []
    for c in info:
        cfg = c.get("Config") or {}
        found = docker_kind(cfg.get("Image") or "")
        if not found:
            continue
        kind, inner = found
        labels = cfg.get("Labels") or {}
        name = (c.get("Name") or "").lstrip("/")
        project = labels.get("com.docker.compose.project") or ""
        service = labels.get("com.docker.compose.service") or name
        env = dict(e.partition("=")[::2] for e in cfg.get("Env") or [])
        net = c.get("NetworkSettings") or {}
        bound = [b for b in (net.get("Ports") or {}).get(f"{inner}/tcp") or [] if b.get("HostPort")]
        if bound:
            host, port = "127.0.0.1", bound[0]["HostPort"]
        else:
            ips = [n.get("IPAddress") for n in (net.get("Networks") or {}).values() if n.get("IPAddress")]
            if not ips:
                continue
            host, port = ips[0], str(inner)
        d = {"docker": f"{project or name}/{service}", "project": project, "service": service, "kind": kind,
             "container": name}
        if kind == "postgres":
            user = env.get("POSTGRES_USER") or "postgres"
            d.update(host=host, port=port, user=user, database=env.get("POSTGRES_DB") or user,
                     secrets={"password": env.get("POSTGRES_PASSWORD", "")})
        elif kind == "redis":
            cmd = cfg.get("Cmd") or []
            pw = next((cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "--requirepass"),
                      env.get("REDIS_PASSWORD", ""))
            d.update(host=host, port=port, database="0", secrets={"password": pw})
        elif kind == "mongo":
            user, pw = env.get("MONGO_INITDB_ROOT_USERNAME", ""), env.get("MONGO_INITDB_ROOT_PASSWORD", "")
            from urllib.parse import quote
            auth = f"{quote(user, '')}:{quote(pw, '')}@" if user else ""
            d.update(host=host, port=port, secrets={"uri": f"mongodb://{auth}{host}:{port}/" +
                                                    ("?authSource=admin" if user else "")})
        else:
            d.update(endpoint=f"http://{host}:{port}", region="us-east-1",
                     access_key=env.get("MINIO_ROOT_USER") or env.get("MINIO_ACCESS_KEY") or "minioadmin",
                     secrets={"secret_key": env.get("MINIO_ROOT_PASSWORD") or env.get("MINIO_SECRET_KEY")
                              or "minioadmin"})
        out.append(d)
    return out


def docker_merge(found):
    """Save what docker_scan() found: a new database becomes a connection named after its service in its
    project's group; a known one gets its address and credentials again (its name and group are yours).
    Returns the ids whose settings changed (an open connection to one must connect again)."""
    import hashlib
    conns, ignored, changed = load(), docker_ignored(), []
    by_key = {c["docker"]: c for c in conns if c.get("docker")}
    for d in found:
        if d["docker"] in ignored:
            continue
        fields = {k: d[k] for k, _l, secret in FIELDS[d["kind"]] if not secret and k in d}
        sig = hashlib.sha256(json.dumps([d["kind"], fields, d["secrets"]], sort_keys=True).encode()).hexdigest()[:16]
        c = by_key.get(d["docker"])
        if c and c.get("docker_sig") == sig:
            continue
        if c:
            for k, _l, secret in FIELDS[c["kind"]]:
                c.pop(k, None)
            changed.append(c["id"])
        else:
            c = {"id": uuid.uuid4().hex[:12], "name": d["service"], "docker": d["docker"]}
            if d["project"]:
                c["project"] = d["project"]
            conns.append(c)
        c.update(kind=d["kind"], docker_sig=sig, **fields)
        store_secret(c["id"], c["name"], {k: v for k, v in d["secrets"].items() if v})
    if changed or len(conns) != len(load()):
        save(sorted(conns, key=lambda c: c["name"].lower()))
    return changed


# ---- Liam ERD: the schema diagram (dbliam.py shows it) --------------------------------------------
# Liam's web app, fetched once from npm (the @liam-hq/cli package, checked against its published hash; only
# its html/ is kept, no Node needed), reads a schema.json the drivers' liam() write.
LIAM_VERSION = "0.7.24"
LIAM_SHA512 = "kb2tcAFYEJ3twbTYnCYEYn1AnjDaCnpP4inSiZR5kh46ZTKlAlt0DZ+61GePLsR+lWBCuPjqOjraHPJ9pxy+Ng=="
LIAM_URL = f"https://registry.npmjs.org/@liam-hq/cli/-/cli-{LIAM_VERSION}.tgz"
LIAM_APP = f"{DIR}/liam-{LIAM_VERSION}"
ACTIONS = {"a": "NO_ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET_NULL", "d": "SET_DEFAULT"}
SHORT_TYPES = {"timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp",
               "time with time zone": "timetz", "time without time zone": "time", "character varying": "varchar",
               "character": "char", "integer": "int4", "bigint": "int8", "smallint": "int2", "boolean": "bool",
               "double precision": "float8", "real": "float4"}


def short_type(t):
    """Postgres' long type names as its short ones (timestamptz, varchar(40), int8[]…), as Liam shows them."""
    base, paren, rest = t.partition("(")
    arr = "[]" if t.endswith("[]") and not paren else ""
    base = base.removesuffix("[]").strip()
    return SHORT_TYPES.get(base, base) + (paren + rest if paren else arr)


def liam_app():
    """The folder holding Liam ERD's web app (index.html, assets/): fetched the first time (on a worker
    thread: a few seconds), then kept."""
    if os.path.isfile(f"{LIAM_APP}/index.html"):
        return LIAM_APP
    import base64, hashlib, io, shutil, tarfile, tempfile, urllib.request
    try:
        with urllib.request.urlopen(LIAM_URL, timeout=60) as r:
            data = r.read()
    except OSError as e:
        raise DbError(f"couldn't fetch Liam ERD: {e}")
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != LIAM_SHA512:
        raise DbError("Liam ERD's download doesn't match its published hash: not used")
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=DIR, prefix=".liam-")
    prefix = "package/dist-cli/html/"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            rel = os.path.normpath(m.name[len(prefix):]) if m.name.startswith(prefix) else ""
            if not m.isfile() or not rel or rel.startswith("..") or os.path.isabs(rel):
                continue
            dest = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with tar.extractfile(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    if not os.path.isfile(f"{tmp}/index.html"):
        shutil.rmtree(tmp)
        raise DbError("Liam ERD's package has no web app in it")
    try:
        os.rename(tmp, LIAM_APP)
    except OSError:   # fetched twice at once: the other one won
        shutil.rmtree(tmp)
    return LIAM_APP


def liam_column(name, typ, not_null, default=None, comment=None):
    return {"name": name, "type": typ, "default": default, "check": None, "notNull": bool(not_null), "comment": comment}


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
        # ssh's messages go to a file, not a pipe: it writes a line per failed forwarded connection for
        # the tunnel's whole life, and a pipe nobody reads fills up (64 KB) and blocks it
        self.log = tempfile.TemporaryFile("w+")
        self.proc = subprocess.Popen(
            ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=30", "-L", f"127.0.0.1:{self.port}:{host}:{port}", ssh],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.log, text=True)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.log.seek(0)
                msg = self.log.read().strip()
                self.log.close()
                raise DbError(f"SSH to {ssh}: " + (msg or "failed"))
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
        self.log.close()


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

    def editor(self, path):
        return None


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
        self.shapes = {}   # path: (its columns [Field], its key's columns, a table?), asked once

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
        _f, key, table = self.shape(path)
        ctid = table and key == ["ctid"]   # no primary key: the row's place in the table says which it is
        # by its primary key (its index): a row edited stays where it was, pages don't overlap
        order = s.SQL(" order by {}").format(s.SQL(", ").join(map(s.Identifier, key))) if table and not ctid else s.SQL("")
        query = s.SQL("select {}* from {}.{}{} limit {} offset {}").format(
            s.SQL("ctid, " if ctid else ""), s.Identifier(path[0]), s.Identifier(path[1]), order, s.Literal(limit + 1),
            s.Literal(offset))
        try:
            with self.conn.cursor() as c:
                c.execute(query)
                rows = c.fetchall()
                cols = [d.name for d in c.description]
        except self.pg.Error as e:
            raise DbError(str(e).strip())
        more, rows, keys = len(rows) > limit, rows[:limit], None
        if ctid:
            keys, rows, cols = [(r[0],) for r in rows], [r[1:] for r in rows], cols[1:]
        elif table:
            at = [cols.index(k) for k in key]
            keys = [tuple(r[i] for i in at) for r in rows]
        return Result(cols, [[cell(v) for v in r] for r in rows], more, keys=keys)

    # ---- changing rows ----
    def shape(self, path):
        if path not in self.shapes:
            rel = self.q("select c.oid, c.relkind from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                         "where n.nspname = %s and c.relname = %s", path)
            oid, kind = rel[0] if rel else (0, "")
            fields = [Field(n, t, null, d or "", fixed) for n, t, null, d, fixed in self.q("""
                select a.attname, format_type(a.atttypid, a.atttypmod), not a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
                       a.attgenerated <> '' or a.attidentity = 'a'
                from pg_attribute a left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
                where a.attrelid = %s and a.attnum > 0 and not a.attisdropped order by a.attnum""", (oid,))]
            key = [k for (k,) in self.q("select a.attname from pg_index i join pg_attribute a on a.attrelid = i.indrelid "
                                        "and a.attnum = any(i.indkey) where i.indrelid = %s and i.indisprimary", (oid,))]
            self.shapes[path] = (fields, key or ["ctid"], kind in ("r", "p"))
        return self.shapes[path]

    def editor(self, path):
        fields, key, table = self.shape(path)
        if not table:
            return None
        return Editor("row", fields, many="the fields you change, on every selected row",
                      hint="" if key != ["ctid"] else "no primary key: rows are told apart by their place (ctid)")

    def where(self, path, key):
        """The WHERE matching one key, and its arguments."""
        s = self.pg.sql
        names = self.shape(path)[1]
        return s.SQL(" and ").join(s.SQL("ctid = %s::tid") if n == "ctid" else s.SQL("{} = %s").format(s.Identifier(n))
                                   for n in names), list(key)

    def assign(self, path, values):
        """[(column, a placeholder cast to its type)] for values: what's typed is text, Postgres reads it as the
        column's type (an int, a date, an array, JSON…), as psql would."""
        s = self.pg.sql
        types = {f.name: f.type for f in self.shape(path)[0]}
        unknown = [k for k in values if k not in types]
        if unknown:
            raise DbError(f"no column {unknown[0]}")
        return [(s.Identifier(k), s.SQL("cast(%s as {})").format(s.SQL(types[k]))) for k in values]

    def record(self, path, key):
        s = self.pg.sql
        fields = self.shape(path)[0]
        where, args = self.where(path, key)
        rows = self.q(s.SQL("select {} from {}.{} where {}").format(
            s.SQL(", ").join(s.SQL("{}::text").format(s.Identifier(f.name)) for f in fields),
            s.Identifier(path[0]), s.Identifier(path[1]), where), args)
        if not rows:
            raise DbError("that row isn't there any more: Refresh")
        return dict(zip((f.name for f in fields), rows[0]))

    def change(self, sql, many):
        """Run sql once per argument list, in one transaction: all of them or none. Rows it touched."""
        try:
            with self.conn.transaction(), self.conn.cursor() as c:
                n = 0
                for args in many:
                    c.execute(sql, args)
                    n += max(c.rowcount, 0)
                return n
        except self.pg.Error as e:
            raise DbError(str(e).strip())

    def insert(self, path, values):
        s = self.pg.sql
        cols = self.assign(path, values)
        sql = s.SQL("insert into {}.{} ({}) values ({})").format(
            s.Identifier(path[0]), s.Identifier(path[1]), s.SQL(", ").join(c for c, _v in cols),
            s.SQL(", ").join(v for _c, v in cols)) if cols else \
            s.SQL("insert into {}.{} default values").format(s.Identifier(path[0]), s.Identifier(path[1]))
        self.change(sql, [list(values.values())])
        return "1 row added"

    def update(self, path, keys, values):
        s = self.pg.sql
        if not values:
            return "nothing changed"
        where, _a = self.where(path, keys[0])
        sql = s.SQL("update {}.{} set {} where {}").format(
            s.Identifier(path[0]), s.Identifier(path[1]),
            s.SQL(", ").join(s.SQL("{} = {}").format(c, v) for c, v in self.assign(path, values)), where)
        n = self.change(sql, [list(values.values()) + list(k) for k in keys])
        return f"{n} row{'s' * (n != 1)} changed"

    def remove(self, path, keys):
        s = self.pg.sql
        where, _a = self.where(path, keys[0])
        n = self.change(s.SQL("delete from {}.{} where {}").format(s.Identifier(path[0]), s.Identifier(path[1]), where),
                        [list(k) for k in keys])
        return f"{n} row{'s' * (n != 1)} deleted"

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

    def liam(self, schema):
        """The schema as Liam ERD's schema.json: its tables and views (partitions under their parent) with
        their columns, keys, checks and indexes, and its enums. A key to another schema's table isn't a
        line (that table isn't in it): its column's comment names it."""
        tables = {}
        for t, kind, tcomment, name, typ, not_null, default, comment in self.q("""
                select c.relname, c.relkind, obj_description(c.oid, 'pg_class'), a.attname,
                       format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
                       col_description(c.oid, a.attnum)
                from pg_class c join pg_namespace n on n.oid = c.relnamespace
                join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
                left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
                where n.nspname = %s and c.relkind in ('r', 'p', 'v', 'm', 'f') and not c.relispartition
                order by c.relname, a.attnum""", (schema,)):
            note = "view" if kind in ("v", "m") else None
            tb = tables.setdefault(t, {"name": t, "columns": {}, "comment": tcomment or note, "indexes": {}, "constraints": {}})
            tb["columns"][name] = liam_column(name, short_type(typ), not_null, default, comment)
        for name, kind, t, cols, rschema, rt, rcols, upd, dele, definition in self.q("""
                select k.conname, k.contype, t.relname,
                       array(select a.attname from unnest(k.conkey) with ordinality u(n, i)
                             join pg_attribute a on a.attrelid = k.conrelid and a.attnum = u.n order by u.i),
                       rn.nspname, rt.relname,
                       array(select a.attname from unnest(k.confkey) with ordinality u(n, i)
                             join pg_attribute a on a.attrelid = k.confrelid and a.attnum = u.n order by u.i),
                       k.confupdtype, k.confdeltype, pg_get_constraintdef(k.oid)
                from pg_constraint k join pg_class t on t.oid = k.conrelid join pg_namespace n on n.oid = t.relnamespace
                left join pg_class rt on rt.oid = k.confrelid left join pg_namespace rn on rn.oid = rt.relnamespace
                where n.nspname = %s and k.contype in ('p', 'u', 'f', 'c') and not t.relispartition""", (schema,)):
            tb = tables.get(t)
            if not tb:
                continue
            if kind == "f":
                if rschema != schema or rt not in tables:
                    for c in cols:
                        if c in tb["columns"] and not tb["columns"][c]["comment"]:
                            tb["columns"][c]["comment"] = f"→ {rschema}.{rt}"
                    continue
                tb["constraints"][name] = {"type": "FOREIGN KEY", "name": name, "columnNames": list(cols),
                                           "targetTableName": rt, "targetColumnNames": list(rcols),
                                           "updateConstraint": ACTIONS.get(upd, "NO_ACTION"),
                                           "deleteConstraint": ACTIONS.get(dele, "NO_ACTION")}
            elif kind == "c":
                tb["constraints"][name] = {"type": "CHECK", "name": name,
                                           "detail": definition.removeprefix("CHECK ").strip()}
            else:
                tb["constraints"][name] = {"type": "PRIMARY KEY" if kind == "p" else "UNIQUE", "name": name,
                                           "columnNames": list(cols)}
        for t, name, unique, method, cols in self.q("""
                select t.relname, i.relname, x.indisunique, am.amname,
                       array(select pg_get_indexdef(x.indexrelid, k, true) from generate_series(1, x.indnkeyatts) k)
                from pg_index x join pg_class i on i.oid = x.indexrelid join pg_class t on t.oid = x.indrelid
                join pg_namespace n on n.oid = t.relnamespace join pg_am am on am.oid = i.relam
                where n.nspname = %s and not exists (select 1 from pg_constraint k where k.conindid = x.indexrelid)""",
                                                    (schema,)):
            if t in tables:   # a key's own index is the key: only the others
                tables[t]["indexes"][name] = {"name": name, "unique": unique, "columns": list(cols), "type": method}
        enums = {name: {"name": name, "values": list(values), "comment": comment} for name, values, comment in self.q("""
                select t.typname, array(select e.enumlabel from pg_enum e where e.enumtypid = t.oid order by e.enumsortorder),
                       obj_description(t.oid, 'pg_type')
                from pg_type t join pg_namespace n on n.oid = t.typnamespace where n.nspname = %s and t.typtype = 'e'""",
                                                                               (schema,))}
        return {"tables": tables, "enums": enums, "extensions": {}}

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
        self.shapes = {}   # as Postgres'

    def children(self, path):
        rows = self.conn.execute("select name, type from sqlite_master where type in ('table', 'view') "
                                 "and name not like 'sqlite_%' order by name").fetchall()
        return [Node((n,), n, t, True) for n, t in rows]

    def name(self, path):
        return '"' + path[0].replace('"', '""') + '"'

    def liam(self, _schema=None):
        """As Postgres.liam: the file's tables and views, their columns, keys and indexes."""
        tables = {}
        try:
            names = self.conn.execute("select name, type from sqlite_master where type in ('table', 'view') "
                                      "and name not like 'sqlite_%' order by name").fetchall()
            for t, typ in names:
                q = self.name((t,))
                info = self.conn.execute(f"pragma table_info({q})").fetchall()   # cid, name, type, notnull, default, pk
                tb = tables[t] = {"name": t, "comment": "view" if typ == "view" else None, "indexes": {}, "constraints": {},
                                  "columns": {r[1]: liam_column(r[1], (r[2] or "").lower(), r[3] or r[5], r[4]) for r in info}}
                pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
                if pk:
                    tb["constraints"][f"{t}_pkey"] = {"type": "PRIMARY KEY", "name": f"{t}_pkey", "columnNames": pk}
                for idx in self.conn.execute(f"pragma index_list({q})").fetchall():   # seq, name, unique, origin, partial
                    cols = [r[2] for r in self.conn.execute(f"pragma index_info({self.name((idx[1],))})")]
                    if idx[3] == "u":     # a UNIQUE in the table's definition
                        tb["constraints"][idx[1]] = {"type": "UNIQUE", "name": idx[1], "columnNames": cols}
                    elif idx[3] == "c":   # CREATE INDEX
                        tb["indexes"][idx[1]] = {"name": idx[1], "unique": bool(idx[2]), "columns": cols, "type": "btree"}
                fks = {}
                for r in self.conn.execute(f"pragma foreign_key_list({q})").fetchall():   # id, seq, table, from, to, on_update, on_delete
                    fks.setdefault(r[0], []).append(r)
                for rows in fks.values():
                    rt = rows[0][2]
                    rcols = [r[4] for r in rows]
                    if None in rcols:   # REFERENCES t with no column: its primary key
                        rcols = [r[1] for r in self.conn.execute(f"pragma table_info({self.name((rt,))})") if r[5]]
                    name = f"{t}_{'_'.join(r[3] for r in rows)}_fkey"
                    tb["constraints"][name] = {"type": "FOREIGN KEY", "name": name, "columnNames": [r[3] for r in rows],
                                               "targetTableName": rt, "targetColumnNames": rcols,
                                               "updateConstraint": rows[0][5].replace(" ", "_"),
                                               "deleteConstraint": rows[0][6].replace(" ", "_")}
        except self.sq.Error as e:
            raise DbError(str(e))
        for tb in tables.values():   # a key to a table that isn't there would break the diagram
            tb["constraints"] = {n: k for n, k in tb["constraints"].items()
                                 if k["type"] != "FOREIGN KEY" or k["targetTableName"] in tables}
        return {"tables": tables, "enums": {}, "extensions": {}}

    def browse(self, path, offset=0, limit=PAGE):
        _f, key, table = self.shape(path)
        rowid = table and key == ["rowid"]
        try:
            c = self.conn.execute(f"select {'rowid, ' if rowid else ''}* from {self.name(path)} limit ? offset ?",
                                  (limit + 1, offset))
        except self.sq.Error as e:
            raise DbError(str(e))
        rows = c.fetchall()
        more, rows, cols, keys = len(rows) > limit, rows[:limit], [d[0] for d in c.description], None
        if rowid:
            keys, rows, cols = [(r[0],) for r in rows], [r[1:] for r in rows], cols[1:]
        elif table:
            at = [cols.index(k) for k in key]
            keys = [tuple(r[i] for i in at) for r in rows]
        return Result(cols, [[cell(v) for v in r] for r in rows], more, keys=keys)

    # ---- changing rows ----
    def shape(self, path):
        if path not in self.shapes:
            try:
                kind, sql = (self.conn.execute("select type, sql from sqlite_master where name = ?", path).fetchone()
                             or ("", ""))
                info = self.conn.execute(f"pragma table_xinfo({self.name(path)})").fetchall()   # …, hidden
            except self.sq.Error as e:
                raise DbError(str(e))
            fields = [Field(r[1], r[2] or "", not r[3] and not r[5], "" if r[4] is None else str(r[4]), r[6] in (2, 3))
                      for r in info if r[6] != 1]
            pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            without = "without rowid" in " ".join((sql or "").lower().split())
            self.shapes[path] = (fields, pk if without else ["rowid"], kind == "table")
        return self.shapes[path]

    def editor(self, path):
        return Editor("row", self.shape(path)[0], many="the fields you change, on every selected row") \
            if self.shape(path)[2] else None

    def where(self, path):
        return " and ".join("rowid = ?" if k == "rowid" else f"{self.name((k,))} = ?" for k in self.shape(path)[1])

    def check(self, path, values):
        names = {f.name for f in self.shape(path)[0]}
        unknown = [k for k in values if k not in names]
        if unknown:
            raise DbError(f"no column {unknown[0]}")

    def record(self, path, key):
        fields = self.shape(path)[0]
        row = self.conn.execute(f"select {', '.join(self.name((f.name,)) for f in fields)} from {self.name(path)} "
                                f"where {self.where(path)}", key).fetchone()
        if not row:
            raise DbError("that row isn't there any more: Refresh")
        return {f.name: text(v) for f, v in zip(fields, row)}

    def change(self, sql, many):
        """As Postgres.change: in one transaction (sqlite's file changes once, all or nothing)."""
        try:
            self.conn.execute("begin")
            n = 0
            for args in many:
                n += max(self.conn.execute(sql, args).rowcount, 0)
            self.conn.execute("commit")
            return n
        except self.sq.Error as e:
            if self.conn.in_transaction:
                self.conn.execute("rollback")
            raise DbError(str(e))

    def insert(self, path, values):
        # what's typed is text: the column's affinity makes "42" an INTEGER, "1.5" a REAL, as the sqlite3 shell does
        self.check(path, values)
        cols = ", ".join(self.name((k,)) for k in values)
        sql = f"insert into {self.name(path)} ({cols}) values ({', '.join('?' * len(values))})" if values else \
            f"insert into {self.name(path)} default values"
        self.change(sql, [list(values.values())])
        return "1 row added"

    def update(self, path, keys, values):
        if not values:
            return "nothing changed"
        self.check(path, values)
        sets = ", ".join(f"{self.name((k,))} = ?" for k in values)
        n = self.change(f"update {self.name(path)} set {sets} where {self.where(path)}",
                        [list(values.values()) + list(k) for k in keys])
        return f"{n} row{'s' * (n != 1)} changed"

    def remove(self, path, keys):
        n = self.change(f"delete from {self.name(path)} where {self.where(path)}", [list(k) for k in keys])
        return f"{n} row{'s' * (n != 1)} deleted"

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
                return Result(["value"], [[cell(self.r.get(k))]], keys=[None])
            if t == "hash":
                cur, items = self.r.hscan(k, offset or 0, count=limit)
                return Result(["field", "value"], [[cell(f), cell(v)] for f, v in items.items()], bool(cur), next_at=cur,
                              keys=list(items))
            if t == "list":
                items = self.r.lrange(k, offset, offset + limit)
                return Result(["#", "value"], [[str(offset + i), cell(v)] for i, v in enumerate(items[:limit])], len(items) > limit,
                              keys=[offset + i for i in range(len(items[:limit]))])
            if t == "set":
                cur, items = self.r.sscan(k, offset or 0, count=limit)
                return Result(["member"], [[cell(v)] for v in items], bool(cur), next_at=cur, keys=list(items))
            if t == "zset":
                items = self.r.zrange(k, offset, offset + limit, withscores=True)
                return Result(["member", "score"], [[cell(m), str(s)] for m, s in items[:limit]], len(items) > limit,
                              keys=[m for m, _s in items[:limit]])
            if t == "stream":
                items = self.r.xrange(k, min=f"({offset}" if offset else "-", count=limit + 1)
                rows = [[cell(i), cell({cell(a): cell(b) for a, b in f.items()})] for i, f in items[:limit]]
                return Result(["id", "fields"], rows, len(items) > limit, next_at=rows[-1][0] if rows else None,
                              keys=[i for i, _f in items[:limit]])
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

    # ---- changing a key's items (a hash's fields, a list's items, members…) and the keys themselves ----
    EDITORS = {   # type: (noun, fields, new / edit / delete, what an edit of several sets)
        "string": ("value", ["value"], (False, True, False), ""),
        "hash": ("field", ["field", "value"], (True, True, True), "the value, on every selected field"),
        "list": ("item", ["value"], (True, True, True), "the value, on every selected item"),
        "set": ("member", ["member"], (True, True, True), ""),
        "zset": ("member", ["member", "score"], (True, True, True), "the score, on every selected member"),
        "stream": ("entry", ["fields"], (True, False, True), ""),
    }
    NEW_KEY = {   # type: how the new key's value is typed
        "string": "its value", "hash": 'JSON: {"field": "value", …}', "list": "one item a line",
        "set": "one member a line", "zset": 'JSON: {"member": score, …}', "stream": 'its first entry, JSON: {"field": "value"}',
    }

    def kind_of(self, k):
        try:
            return self.r.type(k).decode()
        except self.err as e:
            raise DbError(str(e))

    def editor(self, path):
        t = self.kind_of(path[0])
        if t not in self.EDITORS:
            return None
        noun, names, (new, edit, delete), many = self.EDITORS[t]
        return Editor(noun, [Field(n, "JSON object" if t == "stream" else "") for n in names], new=new, edit=edit,
                      delete=delete, many=many, hint="a list's item is told apart by its place: if the list moved "
                      "since you looked (another client pushed), Refresh first" if t == "list" else "")

    def record(self, path, key):
        k, t = path[0], self.kind_of(path[0])
        try:
            if t == "string":
                return {"value": text(self.r.get(k))}
            if t == "hash":
                return {"field": text(key), "value": text(self.r.hget(k, key))}
            if t == "list":
                return {"value": text(self.r.lindex(k, key))}
            if t == "set":
                return {"member": text(key)}
            if t == "zset":
                return {"member": text(key), "score": text(self.r.zscore(k, key))}
        except self.err as e:
            raise DbError(str(e))
        raise DbError(f"a {t}'s entries can't be edited")

    @staticmethod
    def score(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise DbError(f"a score is a number, not {v!r}")

    def insert(self, path, values):
        k, t = path[0], self.kind_of(path[0])
        v = values.get("value") or ""
        try:
            if t == "hash":
                if not values.get("field"):
                    raise DbError("a field needs a name")
                if not self.r.hsetnx(k, values["field"], v):
                    raise DbError(f"{values['field']} is already in {k}: edit it instead")
            elif t == "list":
                self.r.rpush(k, v)
            elif t == "set":
                if not self.r.sadd(k, values.get("member") or ""):
                    raise DbError("that's already a member")
            elif t == "zset":
                self.r.zadd(k, {values.get("member") or "": self.score(values.get("score") or 0)}, nx=True)
            elif t == "stream":
                fields = json.loads(values.get("fields") or "{}")
                if not isinstance(fields, dict) or not fields:
                    raise DbError('an entry is a JSON object of its fields: {"field": "value"}')
                self.r.xadd(k, {a: b if isinstance(b, str) else json.dumps(b) for a, b in fields.items()})
            else:
                raise DbError(f"nothing is added to a {t}")
        except ValueError as e:
            raise DbError(f"not JSON: {e}")
        except self.err as e:
            raise DbError(str(e))
        return f"1 {self.EDITORS[t][0]} added to {k}"

    def update(self, path, keys, values):
        k, t = path[0], self.kind_of(path[0])
        if not values:
            return "nothing changed"
        if len(keys) > 1 and set(values) & {"field", "member"}:
            raise DbError(f"several {self.EDITORS[t][0]}s can't all get one name")
        try:
            pipe = self.r.pipeline(transaction=True)   # MULTI / EXEC: all of it or none
            for key in keys:
                if t == "string":
                    pipe.set(k, values["value"] or "", keepttl=True)
                elif t == "hash":
                    field = values.get("field", text(key))
                    value = values["value"] if "value" in values else self.r.hget(k, key)
                    if field != text(key):
                        if self.r.hexists(k, field):
                            raise DbError(f"{field} is already in {k}")
                        pipe.hdel(k, key)
                    pipe.hset(k, field, value or "")
                elif t == "list":
                    pipe.lset(k, key, values.get("value") or "")
                elif t == "set":
                    pipe.srem(k, key)
                    pipe.sadd(k, values.get("member") or "")
                elif t == "zset":
                    member = values.get("member", text(key))
                    score = self.score(values["score"]) if "score" in values else self.r.zscore(k, key)
                    if member != text(key):
                        pipe.zrem(k, key)
                    pipe.zadd(k, {member: score})
                else:
                    raise DbError(f"a {t}'s entries can't be edited")
            pipe.execute()
        except self.err as e:
            raise DbError(str(e))
        return f"{len(keys)} {self.EDITORS[t][0]}{'s' * (len(keys) != 1)} changed"

    def remove(self, path, keys):
        k, t = path[0], self.kind_of(path[0])
        try:
            if t == "hash":
                n = self.r.hdel(k, *keys)
            elif t == "set":
                n = self.r.srem(k, *keys)
            elif t == "zset":
                n = self.r.zrem(k, *keys)
            elif t == "stream":
                n = self.r.xdel(k, *keys)
            elif t == "list":   # by place: each marked, then the marks taken out at once
                mark = f"\0dbclient-delete-{uuid.uuid4().hex}"
                pipe = self.r.pipeline(transaction=True)
                for i in keys:
                    pipe.lset(k, i, mark)
                pipe.lrem(k, 0, mark)
                n = pipe.execute()[-1]
            else:
                raise DbError(f"delete the {t} key itself (the key on the left)")
        except self.err as e:
            raise DbError(str(e))
        return f"{n} {self.EDITORS[t][0]}{'s' * (n != 1)} deleted"

    def new_key(self, name, t, value, ttl=None):
        """A new key of type t, its value typed as NEW_KEY says; ttl: seconds until it expires (None: never)."""
        if not name:
            raise DbError("a key needs a name")
        try:
            if self.r.exists(name):
                raise DbError(f"{name} already exists")
            if t in ("hash", "zset", "stream"):
                data = json.loads(value or "{}")
                if not isinstance(data, dict) or not data:
                    raise DbError(f"the value is {self.NEW_KEY[t]}")
            lines = [x for x in (value or "").splitlines() if x]
            if t in ("list", "set") and not lines:
                raise DbError(f"a {t} needs one item at least: {self.NEW_KEY[t]}")
            pipe = self.r.pipeline(transaction=True)
            if t == "string":
                pipe.set(name, value or "")
            elif t == "hash":
                pipe.hset(name, mapping={a: b if isinstance(b, str) else json.dumps(b) for a, b in data.items()})
            elif t == "list":
                pipe.rpush(name, *lines)
            elif t == "set":
                pipe.sadd(name, *lines)
            elif t == "zset":
                pipe.zadd(name, {m: self.score(sc) for m, sc in data.items()})
            elif t == "stream":
                pipe.xadd(name, {a: b if isinstance(b, str) else json.dumps(b) for a, b in data.items()})
            else:
                raise DbError(f"no such type: {t}")
            if ttl:
                pipe.expire(name, ttl)
            pipe.execute()
        except ValueError as e:
            raise DbError(f"not JSON: {e}")
        except self.err as e:
            raise DbError(str(e))

    def ttl(self, name):
        """Seconds until the key expires, or None (never)."""
        try:
            t = self.r.ttl(name)
        except self.err as e:
            raise DbError(str(e))
        return t if t >= 0 else None

    def edit_keys(self, names, rename=None, ttl=None):
        """Rename one key (to a name not taken), and / or give keys a time to live: seconds, 0 = never expire."""
        try:
            pipe = self.r.pipeline(transaction=True)
            if rename and rename != names[0]:
                if self.r.exists(rename):
                    raise DbError(f"{rename} already exists")
                pipe.rename(names[0], rename)
                names = [rename]
            for k in names if ttl is not None else ():
                pipe.expire(k, ttl) if ttl else pipe.persist(k)
            pipe.execute()
        except self.err as e:
            raise DbError(str(e))

    def delete_keys(self, names):
        try:
            n = sum(self.r.delete(*names[i:i + 500]) for i in range(0, len(names), 500))
        except self.err as e:
            raise DbError(str(e))
        return f"{n} key{'s' * (n != 1)} deleted"

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
        return Result(cols, rows, len(docs) > limit, keys=[d.get("_id") for d in docs[:limit]])

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

    # ---- changing documents: as (extended) JSON ----
    def editor(self, path):
        return Editor("document", json=True, many='a change for every selected document: {"field": value} sets those '
                      'fields, or an update {"$inc": {"n": 1}, "$unset": {"old": ""}}',
                      hint='JSON, as mongosh writes it: {"name": "x", "at": {"$date": "2024-05-01T00:00:00Z"}}; '
                           'a list [{…}, {…}] adds several')

    def parse(self, text):
        try:
            return self.ju.loads(text)
        except ValueError as e:
            raise DbError(f"not JSON: {e}")

    def record(self, path, key):
        doc = self.c[path[0]][path[1]].find_one({"_id": key})
        if doc is None:
            raise DbError("that document isn't there any more: Refresh")
        return self.ju.dumps(doc, indent=2, ensure_ascii=False)

    def insert(self, path, text):
        docs = self.parse(text)
        if isinstance(docs, dict):
            docs = [docs]
        if not docs or not all(isinstance(d, dict) for d in docs):
            raise DbError("a document is a JSON object {…} (or a list of them)")
        try:
            n = len(self.c[path[0]][path[1]].insert_many(docs).inserted_ids)
        except self.err as e:
            raise DbError(str(e))
        return f"{n} document{'s' * (n != 1)} added"

    def update(self, path, keys, text):
        """One document: what's typed replaces it (its _id kept). Several: fields set on each, or an update."""
        doc = self.parse(text)
        if not isinstance(doc, dict):
            raise DbError("a JSON object {…}")
        coll = self.c[path[0]][path[1]]
        operators = any(k.startswith("$") for k in doc)
        try:
            if len(keys) == 1 and not operators:
                if doc.get("_id", keys[0]) != keys[0]:
                    raise DbError("a document's _id can't change: add it again as a new one, then delete this one")
                n = coll.replace_one({"_id": keys[0]}, {k: v for k, v in doc.items() if k != "_id"}).matched_count
            else:
                n = coll.update_many({"_id": {"$in": list(keys)}}, doc if operators else {"$set": doc}).matched_count
        except self.err as e:
            raise DbError(str(e))
        return f"{n} document{'s' * (n != 1)} changed"

    def remove(self, path, keys):
        try:
            n = self.c[path[0]][path[1]].delete_many({"_id": {"$in": list(keys)}}).deleted_count
        except self.err as e:
            raise DbError(str(e))
        return f"{n} document{'s' * (n != 1)} deleted"

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
        """A file: its details (and the start of a text file). A bucket / folder: every file under it,
        however deep (offset: the continuation token where the page starts, 0 for the first)."""
        if len(path) == 2:
            return self.files(path, offset, limit)
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

    def files(self, path, token, limit):
        bucket, prefix = path
        kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": limit}
        if token:
            kw["ContinuationToken"] = token
        try:
            r = self.s3.list_objects_v2(**kw)
        except self.err as e:
            raise DbError(str(e))
        objs = [o for o in r.get("Contents", []) if o["Key"] != prefix]
        rows = [[o["Key"][len(prefix):], size(o["Size"]), str(o.get("LastModified", ""))[:19]] for o in objs]
        more = bool(r.get("IsTruncated"))
        return Result(["file", "size", "modified"], rows, more, "" if rows or more else "no files",
                      r.get("NextContinuationToken") if more else None, keys=[o["Key"] for o in objs])

    # ---- changing a bucket / folder's files: upload, rename / move, delete ----
    def editor(self, path):
        if len(path) != 2:
            return None
        return Editor("file", [Field("file", "its name in this folder: a/b.txt makes folders")], files=True,
                      many="the folder they move to, under this one (empty: here)")

    def record(self, path, key):
        return {"file": key[len(path[1]):]}

    def replace(self, bucket, key, src):
        """The file at key's contents replaced by the local file src (its name stays; its type is src's)."""
        import mimetypes
        ctype = mimetypes.guess_type(src)[0]
        try:
            self.s3.upload_file(src, bucket, key, ExtraArgs={"ContentType": ctype} if ctype else None)
        except self.err as e:
            raise DbError(str(e))
        return f"replaced {os.path.basename(key)} with {os.path.basename(src)}"

    def update(self, path, keys, values):
        """Rename one file, or move several into a folder (under this one): a copy, then the old one deleted."""
        bucket, prefix = path
        if "file" not in values:
            return "nothing changed"
        to = (values["file"] or "").strip().lstrip("/")
        if len(keys) == 1:
            if not to or to.endswith("/"):
                to += os.path.basename(keys[0])
            moves = [(keys[0], prefix + to)]
        else:
            to = to and to.rstrip("/") + "/"
            moves = [(k, prefix + to + os.path.basename(k)) for k in keys]
        moves = [(a, b) for a, b in moves if a != b]
        targets = [b for _a, b in moves]
        if len(set(targets)) < len(targets):
            raise DbError("two of them have the same name: they'd land on each other")
        try:
            for new in targets:   # S3 overwrites without a word: never over a file that's there
                if self.s3.list_objects_v2(Bucket=bucket, Prefix=new, MaxKeys=1).get("Contents", [{}])[0].get("Key") == new:
                    raise DbError(f"{new} is already there")
            for old, new in moves:
                self.s3.copy({"Bucket": bucket, "Key": old}, bucket, new)   # managed: a multipart copy past 5 GB
            self.remove(path, [old for old, _n in moves])
        except self.err as e:
            raise DbError(str(e))
        return f"{len(moves)} file{'s' * (len(moves) != 1)} " + ("renamed" if len(keys) == 1 else "moved")

    def remove(self, path, keys):
        try:
            for i in range(0, len(keys), 1000):   # DeleteObjects takes 1000 at a time
                r = self.s3.delete_objects(Bucket=path[0], Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                                                                    "Quiet": True})
                if r.get("Errors"):
                    e = r["Errors"][0]
                    raise DbError(f"{e.get('Key')}: {e.get('Message') or e.get('Code')}")
        except self.err as e:
            raise DbError(str(e))
        return f"{len(keys)} file{'s' * (len(keys) != 1)} deleted"

    def image(self, bucket, key):
        """An image file's bytes for the preview, or None (not an image, or too big to fetch for a look)."""
        try:
            head = self.s3.head_object(Bucket=bucket, Key=key)
            if not (head.get("ContentType", "").startswith("image/") or key.lower().endswith(IMAGE_EXT)) \
                    or not 0 < head["ContentLength"] <= IMAGE:
                return None
            return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except self.err as e:
            raise DbError(str(e))

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
