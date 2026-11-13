"""SQLite data layer (swap for PostgreSQL later; schema is plain relational + JSON text)."""
import json, os, sqlite3, time, uuid
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = str(DATA / "app.db")
STORAGE = str(DATA / "files")

SCHEMA = """
create table if not exists users(id text primary key, email text unique, pw_hash text, salt text, created_at real);
create table if not exists sessions(token_hash text primary key, user_id text, expires_at real);
create table if not exists files(id text primary key, user_id text, filename text, path text, sha256 text, parse_status text, created_at real);
create table if not exists facts(id text primary key, user_id text, kind text, data text, confidence real, source_file_id text, verified int, priority int, updated_at real);
create table if not exists jobs(id text primary key, user_id text, raw_text text, req_json text, created_at real);
create table if not exists versions(id text primary key, user_id text, job_id text, template text, resume_json text, warnings_json text, status text, created_at real);
create table if not exists exports(id text primary key, user_id text, version_id text, fmt text, path text, created_at real);
create table if not exists templates(id text primary key, user_id text, name text, path text, style_json text, created_at real);
create table if not exists audit(id integer primary key autoincrement, user_id text, event text, resource_id text, ts real);
"""


def conn():
    DATA.mkdir(parents=True, exist_ok=True)
    Path(STORAGE).mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def q(sql, a=(), one=False):
    c = conn()
    try:
        r = c.execute(sql, a).fetchall()
    finally:
        c.close()
    return (r[0] if r else None) if one else r


def x(sql, a=()):
    c = conn()
    try:
        c.execute(sql, a)
        c.commit()
    finally:
        c.close()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:10]


def fact_row(r):
    return {"id": r["id"], "kind": r["kind"], "data": json.loads(r["data"]), "confidence": r["confidence"],
            "source_file_id": r["source_file_id"], "verified": bool(r["verified"]), "priority": r["priority"]}


def facts_of(user_id):
    return [fact_row(r) for r in q("select * from facts where user_id=? order by updated_at", (user_id,))]


def add_fact(user_id, kind, data, conf=1.0, file_id=None, verified=0):
    fid = uid("fact")
    x("insert into facts values(?,?,?,?,?,?,?,?,?)",
      (fid, user_id, kind, json.dumps(data), conf, file_id, verified, 0, time.time()))
    return fid
