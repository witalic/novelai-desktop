"""SQLite index — throwaway, rebuildable by scanning ``work.json`` files (files are the truth).

Tags are normalized (``tag``/``image_tag``/``work_tag``/``block_tag``) so the global gallery can
filter across many images efficiently. Image tags are inherited from the snapshot's block tags
(source ``block``) plus manual tags (source ``manual``); work tags are their union.
"""
import json
import logging
import sqlite3
from pathlib import Path

from app.vault.models import WorkDoc

log = logging.getLogger(__name__)

_DB = ".index.sqlite"
_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE work(id TEXT PRIMARY KEY, dir TEXT NOT NULL, title TEXT, slug TEXT,
  created_at TEXT, updated_at TEXT, preview_image_id TEXT, image_count INTEGER DEFAULT 0);
CREATE TABLE snapshot(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, hash TEXT,
  positive TEXT, negative TEXT, created_at TEXT);
CREATE TABLE image(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, snapshot_id TEXT, file TEXT,
  favorite INTEGER DEFAULT 0, group_name TEXT, seed INTEGER, model TEXT, created_at TEXT);
CREATE TABLE block(id TEXT PRIMARY KEY, category TEXT, name TEXT, text TEXT,
  version INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE tag(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
CREATE TABLE image_tag(image_id TEXT, tag_id INTEGER, source TEXT, PRIMARY KEY(image_id, tag_id, source));
CREATE TABLE work_tag(work_id TEXT, tag_id INTEGER, source TEXT, PRIMARY KEY(work_id, tag_id, source));
CREATE TABLE block_tag(block_id TEXT, tag_id INTEGER, PRIMARY KEY(block_id, tag_id));
CREATE INDEX idx_work_updated ON work(updated_at DESC);
CREATE INDEX idx_image_work ON image(work_id);
CREATE INDEX idx_snapshot_work ON snapshot(work_id);
CREATE INDEX idx_image_tag_tag ON image_tag(tag_id);
CREATE INDEX idx_work_tag_tag ON work_tag(tag_id);
"""


def _connect(vault: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(vault / _DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def open_index(vault: Path) -> sqlite3.Connection:
    """Open the index; (re)create + rebuild it if missing or on a schema version bump."""
    existed = (vault / _DB).exists()
    conn = _connect(vault)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if not existed or version != _SCHEMA_VERSION:
        conn.close()
        (vault / _DB).unlink(missing_ok=True)
        conn = _connect(vault)
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
        rebuild(conn, vault)
    return conn


def _tag_id(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    row = conn.execute("SELECT id FROM tag WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO tag(name) VALUES(?)", (name,)).lastrowid


def _delete_work(conn: sqlite3.Connection, work_id: str) -> None:
    conn.execute("DELETE FROM image_tag WHERE image_id IN (SELECT id FROM image WHERE work_id=?)", (work_id,))
    conn.execute("DELETE FROM work_tag WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM image WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM snapshot WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM work WHERE id=?", (work_id,))


def upsert_work(conn: sqlite3.Connection, doc: WorkDoc, dir_name: str) -> None:
    _delete_work(conn, doc.id)
    conn.execute(
        "INSERT INTO work(id,dir,title,slug,created_at,updated_at,preview_image_id,image_count) VALUES(?,?,?,?,?,?,?,?)",
        (doc.id, dir_name, doc.title, doc.slug, doc.created_at, doc.updated_at, doc.preview_image_id, len(doc.images)),
    )
    snap_tags: dict[str, set[str]] = {}
    for s in doc.snapshots:
        conn.execute(
            "INSERT INTO snapshot(id,work_id,hash,positive,negative,created_at) VALUES(?,?,?,?,?,?)",
            (s.id, doc.id, s.hash, s.assembled_positive, s.assembled_negative, s.created_at),
        )
        snap_tags[s.id] = {t for c in s.components for t in c.tags}
    work_tag_ids: set[int] = set()
    for im in doc.images:
        conn.execute(
            "INSERT INTO image(id,work_id,snapshot_id,file,favorite,group_name,seed,model,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (im.id, doc.id, im.snapshot_id, im.file, int(im.favorite), im.group,
             im.params.get("seed"), im.params.get("model"), im.created_at),
        )
        for t in snap_tags.get(im.snapshot_id or "", set()):
            tid = _tag_id(conn, t)
            conn.execute("INSERT OR IGNORE INTO image_tag(image_id,tag_id,source) VALUES(?,?,'block')", (im.id, tid))
            work_tag_ids.add(tid)
        for t in im.tags:
            tid = _tag_id(conn, t)
            conn.execute("INSERT OR IGNORE INTO image_tag(image_id,tag_id,source) VALUES(?,?,'manual')", (im.id, tid))
            work_tag_ids.add(tid)
    for tid in work_tag_ids:
        conn.execute("INSERT OR IGNORE INTO work_tag(work_id,tag_id,source) VALUES(?,?,'derived')", (doc.id, tid))
    conn.commit()


def upsert_block(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO block(id,category,name,text,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (data["id"], data.get("category", "custom"), data.get("name", ""), data.get("text", ""),
         data.get("version", 1), data.get("created_at", ""), data.get("updated_at", "")),
    )
    conn.execute("DELETE FROM block_tag WHERE block_id=?", (data["id"],))
    for t in data.get("tags", []):
        conn.execute("INSERT OR IGNORE INTO block_tag(block_id,tag_id) VALUES(?,?)", (data["id"], _tag_id(conn, t)))
    conn.commit()


def find_work_dir(conn: sqlite3.Connection, work_id: str) -> str | None:
    row = conn.execute("SELECT dir FROM work WHERE id=?", (work_id,)).fetchone()
    return row[0] if row else None


def list_works(conn: sqlite3.Connection, page: int, per_page: int):
    total = conn.execute("SELECT COUNT(*) FROM work").fetchone()[0]
    rows = conn.execute(
        "SELECT id,title,updated_at,image_count,preview_image_id FROM work ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    return total, rows


def gallery(conn, tags: list[str], favorite: bool, page: int, per_page: int):
    where, params = [], []
    if favorite:
        where.append("i.favorite=1")
    if tags:
        ph = ",".join("?" * len(tags))
        where.append(
            f"i.id IN (SELECT it.image_id FROM image_tag it JOIN tag t ON t.id=it.tag_id "
            f"WHERE t.name IN ({ph}) GROUP BY it.image_id HAVING COUNT(DISTINCT t.name)=?)"
        )
        params += tags + [len(tags)]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM image i {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT i.id,i.work_id,i.favorite,i.group_name,i.created_at FROM image i {clause} "
        f"ORDER BY i.created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return total, rows


def rebuild(conn: sqlite3.Connection, vault: Path) -> dict:
    for tbl in ("image_tag", "work_tag", "block_tag", "image", "snapshot", "work", "block", "tag"):
        conn.execute(f"DELETE FROM {tbl}")
    works = 0
    works_root = vault / "works"
    if works_root.is_dir():
        for wj in works_root.glob("*/work.json"):
            try:
                doc = WorkDoc.model_validate_json(wj.read_text("utf-8"))
            except Exception:  # noqa: BLE001 — a bad file must not abort the whole rebuild
                log.warning("Skipping unreadable work: %s", wj)
                continue
            upsert_work(conn, doc, wj.parent.name)
            works += 1
    blocks = 0
    blocks_root = vault / "library" / "blocks"
    if blocks_root.is_dir():
        for bj in blocks_root.glob("**/*.json"):
            try:
                upsert_block(conn, json.loads(bj.read_text("utf-8")))
                blocks += 1
            except Exception:  # noqa: BLE001
                log.warning("Skipping unreadable block: %s", bj)
    conn.commit()
    counts = {"works": works, "blocks": blocks}
    log.info("Rebuilt vault index: %s", counts)
    return counts
