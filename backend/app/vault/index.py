"""SQLite index — throwaway, rebuildable by scanning ``work.json`` files (files are the truth).

Tags are normalized (``tag``/``image_tag``/``work_tag``/``block_tag``) so the global gallery can
filter across many images efficiently. Image tags are inherited from the snapshot's block tags
(source ``block``) plus manual tags (source ``manual``); work tags are their union.
"""
import json
import logging
import sqlite3
import threading
from pathlib import Path

from app.vault.models import WorkDoc

log = logging.getLogger(__name__)

_DB = ".index.sqlite"
_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE work(id TEXT PRIMARY KEY, dir TEXT NOT NULL, title TEXT, slug TEXT,
  created_at TEXT, updated_at TEXT, preview_image_id TEXT, image_count INTEGER DEFAULT 0);
CREATE TABLE snapshot(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, hash TEXT,
  positive TEXT, negative TEXT, created_at TEXT);
CREATE TABLE image(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, snapshot_id TEXT, file TEXT,
  favorite INTEGER DEFAULT 0, group_name TEXT, seed INTEGER, model TEXT, created_at TEXT);
CREATE TABLE block(id TEXT PRIMARY KEY, category TEXT, name TEXT, text TEXT,
  polarity TEXT DEFAULT 'positive', version INTEGER, created_at TEXT, updated_at TEXT);
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
    conn.execute("PRAGMA busy_timeout=5000")  # WAL has a single writer — wait out a concurrent save/rebuild
    return conn


# Per-vault lock so parallel first-run requests (the frontend fires several at once) don't both wipe +
# recreate the index — that races to "table already exists" / a Windows PermissionError unlinking a DB a
# sibling thread still holds.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


def _build_lock(vault: Path) -> threading.Lock:
    key = str(vault)
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = _build_locks[key] = threading.Lock()
        return lock


def _healthy(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
    except sqlite3.DatabaseError:  # missing/empty (user_version 0) or not a valid SQLite file
        return False


def open_index(vault: Path) -> sqlite3.Connection:
    """Open the index; (re)build it if missing, corrupt, or on a schema version bump.

    The per-vault lock wraps the whole open (connect + health check + any rebuild), not just the rebuild:
    otherwise a sibling thread sitting between its own connect() and close() would hold a handle to the DB
    while this thread unlinks it — a Windows 'file in use' error during the first-run request burst. For the
    common healthy path the lock is held only for a connect + one PRAGMA, so contention is negligible."""
    with _build_lock(vault):
        conn = _connect(vault)
        if _healthy(conn):
            return conn
        conn.close()
        for suffix in ("", "-wal", "-shm"):  # drop the DB together with its WAL sidecars
            (vault / f"{_DB}{suffix}").unlink(missing_ok=True)
        conn = _connect(vault)
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()
        rebuild(conn, vault)
        return conn


def _tag_id(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    # INSERT OR IGNORE + SELECT rather than SELECT-then-INSERT — the latter races to a UNIQUE IntegrityError
    # when two saves introduce the same new tag concurrently.
    conn.execute("INSERT OR IGNORE INTO tag(name) VALUES(?)", (name,))
    return conn.execute("SELECT id FROM tag WHERE name=?", (name,)).fetchone()[0]


def _delete_work(conn: sqlite3.Connection, work_id: str) -> None:
    conn.execute("DELETE FROM image_tag WHERE image_id IN (SELECT id FROM image WHERE work_id=?)", (work_id,))
    conn.execute("DELETE FROM work_tag WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM image WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM snapshot WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM work WHERE id=?", (work_id,))


def remove_work(conn: sqlite3.Connection, work_id: str) -> None:
    """Fully drop a work from the index (rows only — the caller removes the on-disk files)."""
    _delete_work(conn, work_id)
    conn.commit()


def upsert_work(conn: sqlite3.Connection, doc: WorkDoc, dir_name: str) -> None:
    _delete_work(conn, doc.id)
    conn.execute(
        "INSERT INTO work(id,dir,title,slug,created_at,updated_at,preview_image_id,image_count) VALUES(?,?,?,?,?,?,?,?)",
        (doc.id, dir_name, doc.title, doc.slug, doc.created_at, doc.updated_at, doc.preview_image_id, len(doc.images)),
    )
    snap_tags: dict[str, set[str]] = {}
    snap_params: dict[str, dict] = {}
    for s in doc.snapshots:
        conn.execute(
            "INSERT OR REPLACE INTO snapshot(id,work_id,hash,positive,negative,created_at) VALUES(?,?,?,?,?,?)",
            (s.id, doc.id, s.hash, s.assembled_positive, s.assembled_negative, s.created_at),
        )
        snap_tags[s.id] = {t for c in s.components for t in c.tags}
        snap_params[s.id] = s.params
    work_tag_ids: set[int] = set()
    for im in doc.images:
        params = snap_params.get(im.snapshot_id or "", {})  # generation params live on the snapshot
        conn.execute(
            "INSERT OR REPLACE INTO image(id,work_id,snapshot_id,file,favorite,group_name,seed,model,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (im.id, doc.id, im.snapshot_id, im.file, int(im.favorite), im.group,
             params.get("seed"), params.get("model"), im.created_at),
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
        "INSERT OR REPLACE INTO block(id,category,name,text,polarity,version,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (data["id"], data.get("category", "custom"), data.get("name", ""), data.get("text", ""),
         data.get("polarity", "positive"), data.get("version", 1),
         data.get("created_at", ""), data.get("updated_at", "")),
    )
    conn.execute("DELETE FROM block_tag WHERE block_id=?", (data["id"],))
    for t in data.get("tags", []):
        conn.execute("INSERT OR IGNORE INTO block_tag(block_id,tag_id) VALUES(?,?)", (data["id"], _tag_id(conn, t)))
    conn.commit()


def delete_block(conn: sqlite3.Connection, block_id: str) -> None:
    conn.execute("DELETE FROM block_tag WHERE block_id=?", (block_id,))
    conn.execute("DELETE FROM block WHERE id=?", (block_id,))
    conn.commit()


def list_blocks(conn, category: str | None, tags: list[str], search: str | None, page: int, per_page: int):
    where, params = [], []
    if category:
        where.append("b.category=?")
        params.append(category)
    if search:
        where.append(r"(b.name LIKE ? ESCAPE '\' OR b.text LIKE ? ESCAPE '\')")
        esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # literal wildcards
        like = f"%{esc}%"
        params += [like, like]
    if tags:
        ph = ",".join("?" * len(tags))
        where.append(
            f"b.id IN (SELECT bt.block_id FROM block_tag bt JOIN tag t ON t.id=bt.tag_id "
            f"WHERE t.name IN ({ph}) GROUP BY bt.block_id HAVING COUNT(DISTINCT t.name)=?)"
        )
        params += tags + [len(tags)]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM block b {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT b.id,b.category,b.name,b.text,b.polarity,b.version,b.created_at,b.updated_at, "
        f"(SELECT GROUP_CONCAT(t.name, char(31)) FROM block_tag bt JOIN tag t ON t.id=bt.tag_id "
        f"WHERE bt.block_id=b.id) AS tags FROM block b {clause} "
        f"ORDER BY b.updated_at DESC, b.name LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return total, rows


def category_counts(conn, tags: list[str] | None = None) -> dict[str, int]:
    if tags:
        ph = ",".join("?" * len(tags))
        rows = conn.execute(
            f"SELECT b.category AS category, COUNT(*) AS c FROM block b WHERE b.id IN "
            f"(SELECT bt.block_id FROM block_tag bt JOIN tag t ON t.id=bt.tag_id WHERE t.name IN ({ph}) "
            f"GROUP BY bt.block_id HAVING COUNT(DISTINCT t.name)=?) GROUP BY b.category",
            tags + [len(tags)],
        ).fetchall()
    else:
        rows = conn.execute("SELECT category, COUNT(*) AS c FROM block GROUP BY category").fetchall()
    return {r["category"]: r["c"] for r in rows}


def examples_by_tags(conn, tags: list[str], limit: int):
    """Images that carry ALL of the given tags (exact match — the block was actually used)."""
    if not tags:
        return []
    ph = ",".join("?" * len(tags))
    return conn.execute(
        f"SELECT i.id AS id, i.work_id AS work_id, i.favorite AS favorite, i.group_name AS group_name, "
        f"i.created_at AS created_at "
        f"FROM image_tag it JOIN tag t ON t.id=it.tag_id JOIN image i ON i.id=it.image_id "
        f"WHERE t.name IN ({ph}) GROUP BY i.id HAVING COUNT(DISTINCT t.name)=? "
        f"ORDER BY RANDOM() LIMIT ?",  # random pick so a large match set stays varied
        [*tags, len(tags), limit],
    ).fetchall()


def tag_counts(conn, category: str | None):
    clause, params = "", []
    if category:
        clause = "WHERE b.category=?"
        params.append(category)
    return conn.execute(
        f"SELECT t.name AS name, COUNT(*) AS count FROM block_tag bt "
        f"JOIN tag t ON t.id=bt.tag_id JOIN block b ON b.id=bt.block_id {clause} "
        f"GROUP BY t.name ORDER BY count DESC, t.name",
        params,
    ).fetchall()


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
            try:
                upsert_work(conn, doc, wj.parent.name)
                works += 1
            except Exception:  # noqa: BLE001 — one corrupt/duplicate work must not 500 the rebuild
                conn.rollback()
                log.warning("Skipping work that failed to index: %s", wj)
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
