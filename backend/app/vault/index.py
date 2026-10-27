"""SQLite index — throwaway, rebuildable by scanning ``work.json`` files (files are the truth).

It lives in this machine's cache (``location``), never in the vault: a vault may sit on a network share, where SQLite's
WAL can't work and every request would pay the network for its pages.

Tags are normalized (``tag``/``image_tag``/``work_tag``/``block_tag``) so the global gallery can
filter across many images efficiently. Image tags are inherited from the snapshot's block tags
(source ``block``; ``negative`` for a negative block's) plus manual tags (source ``manual``). The gallery's tags — what
it filters by and what a work's tags (``work_tag``) gather from its gallery images — are the image's own and its
positive blocks', as the app shows them; the Library's examples match a negative block's too (it was used).

Each image also carries where it sits in its work's gallery (``gallery_layout.placements``: an album, or none —
unsorted) and its creation time as a number to filter and order by. Its searchable text lives aside, casefolded
(``image_text``: the prompt, the description, its tags, its positive blocks' names, the album's heading and section —
never the negative prompt), so the rows every filter reads stay small. A work's ``search_text`` is its title and the
words of its gallery (section titles, headings, notes).
"""
import hashlib
import json
import logging
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app import appconfig
from app.vault import config as vaultcfg
from app.vault import gallery_layout, layout, migrate
from app.vault.models import WorkDoc

log = logging.getLogger(__name__)

_LEGACY = ".index.sqlite"  # where earlier builds kept the index: inside the vault
# 4: placements, times, search text · 5: work/block mtime_ns (what sync compares) — bumping rebuilds the throwaway index
_SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE work(id TEXT PRIMARY KEY, dir TEXT NOT NULL, title TEXT, slug TEXT,
  created_at TEXT, updated_at TEXT, preview_image_id TEXT, image_count INTEGER DEFAULT 0,
  search_text TEXT DEFAULT '', texts TEXT DEFAULT '[]', mtime_ns INTEGER);
CREATE TABLE snapshot(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, hash TEXT,
  positive TEXT, negative TEXT, created_at TEXT);
CREATE TABLE image(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, snapshot_id TEXT, file TEXT,
  role TEXT DEFAULT 'gallery', favorite INTEGER DEFAULT 0, group_name TEXT, seed INTEGER, model TEXT, created_at TEXT,
  created_ts INTEGER DEFAULT 0, ar REAL, description TEXT DEFAULT '',
  grid_id TEXT, grid_no INTEGER, heading TEXT, section TEXT);
CREATE TABLE image_text(image_id TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE block(id TEXT PRIMARY KEY, category TEXT, name TEXT, text TEXT,
  polarity TEXT DEFAULT 'positive', version INTEGER, created_at TEXT, updated_at TEXT, mtime_ns INTEGER);
CREATE TABLE tag(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
CREATE TABLE image_tag(image_id TEXT, tag_id INTEGER, source TEXT, PRIMARY KEY(image_id, tag_id, source));
CREATE TABLE work_tag(work_id TEXT, tag_id INTEGER, source TEXT, PRIMARY KEY(work_id, tag_id, source));
CREATE TABLE block_tag(block_id TEXT, tag_id INTEGER, PRIMARY KEY(block_id, tag_id));
CREATE INDEX idx_work_updated ON work(updated_at DESC);
CREATE INDEX idx_image_work ON image(work_id);
CREATE INDEX idx_snapshot_work ON snapshot(work_id);
CREATE INDEX idx_image_tag_tag ON image_tag(tag_id);
CREATE INDEX idx_work_tag_tag ON work_tag(tag_id);
CREATE INDEX idx_image_feed ON image(role, created_ts DESC, id);
CREATE INDEX idx_image_model ON image(model);
"""


def location(cache: Path, vault: Path) -> Path:
    """A vault's index file in the machine's cache dir: named after the folder for whoever looks, keyed by its full
    path — so two vaults never share one, and a moved vault starts afresh."""
    key = hashlib.sha256(os.path.normcase(str(vault)).encode("utf-8")).hexdigest()[:12]
    return cache / "index" / f"{layout.slugify(vault.name)}-{key}.sqlite"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")  # WAL has a single writer — wait out a concurrent save/rebuild
        conn.execute("PRAGMA journal_mode=WAL")  # the first read of the file: a non-database fails here
    except BaseException:
        conn.close()  # never left to the traceback: an open handle would keep Windows from replacing the file
        raise
    return conn


def _unlink(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):  # the DB together with its WAL sidecars
        db.with_name(db.name + suffix).unlink(missing_ok=True)


# Per-index lock so parallel first-run requests (the frontend fires several at once) don't both wipe +
# recreate the index — that races to "table already exists" / a Windows PermissionError unlinking a DB a
# sibling thread still holds.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()
_synced: set[str] = set()  # the indexes brought in line with their vault's files in this run


def _build_lock(db: Path) -> threading.Lock:
    key = str(db)
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = _build_locks[key] = threading.Lock()
        return lock


def _corrupt(exc: BaseException) -> bool:
    return getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def _index_fault(exc: BaseException) -> bool:
    """A failure of the index itself (corrupt, locked, out of space) — never to be taken for one bad file in the vault."""
    return isinstance(exc, sqlite3.OperationalError) or _corrupt(exc)


def _healthy(db: Path) -> sqlite3.Connection | None:
    """The index as it stands, or None when it must be built: missing/empty (user_version 0), an older schema, or not
    a SQLite file at all."""
    try:
        conn = _connect(db)
    except sqlite3.DatabaseError as exc:
        if not _corrupt(exc):  # busy or the like: an error for this request, not a reason to wipe the file
            raise
        return None
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION:
            return conn
    except sqlite3.DatabaseError as exc:
        if not _corrupt(exc):
            conn.close()
            raise
    conn.close()
    return None


def open_index(vault: Path, db: Path) -> sqlite3.Connection:
    """Open a vault's index at ``db`` (see ``location``); (re)build it if missing, unreadable, or on a schema version
    bump, and bring it in line with the files (``sync``) on its first open in a run.

    The per-index lock wraps the whole open (connect + health check + any rebuild), not just the rebuild:
    otherwise a sibling thread sitting between its own connect() and close() would hold a handle to the DB
    while this thread unlinks it — a Windows 'file in use' error during the first-run request burst. For the
    common healthy path the lock is held only for a connect + one PRAGMA, so contention is negligible."""
    with _build_lock(db):
        db.parent.mkdir(parents=True, exist_ok=True)
        if conn := _healthy(db):
            try:
                if str(db) not in _synced:
                    sync(conn, vault)
                    _synced.add(str(db))
            except BaseException:
                conn.close()
                raise
            return conn
        _unlink(db)
        conn = _connect(db)
        try:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()
            sync(conn, vault)
            _synced.add(str(db))
        except BaseException:
            conn.close()
            raise
    _drop_legacy(vault)
    return conn


@contextmanager
def connection(vault: Path, db: Path) -> Iterator[sqlite3.Connection]:
    """The index for one request, closed after. One SQLite finds corrupt is thrown away, so a damaged page costs one
    failed request and a rebuild — not a failure on every call (the health check reads the header only)."""
    conn = None
    try:
        conn = open_index(vault, db)
        yield conn
    except sqlite3.DatabaseError as exc:
        if _corrupt(exc):
            log.warning("The vault index is corrupt — it is rebuilt on the next request: %s", exc)
            if conn is not None:
                conn.close()
            discard(db)
        raise
    finally:
        if conn is not None:
            conn.close()


def discard(db: Path) -> None:
    """Throw an index away — its vault moved or went, or the file is corrupt; the next open builds it anew. A file
    still open elsewhere (Windows won't delete it) stays until then, and is synced again rather than trusted."""
    with _build_lock(db):
        _synced.discard(str(db))
        try:
            _unlink(db)
        except OSError as exc:
            log.info("Left the index %s for later: %s", db.name, exc)


def _drop_legacy(vault: Path) -> None:
    """Earlier builds kept the index in the vault itself — removed for good once its successor is built. One an older
    app still holds (on another computer) stays for a later build."""
    if not (vault / _LEGACY).exists():
        return
    try:
        _unlink(vault / _LEGACY)
    except OSError as exc:
        log.info("Left the vault's old index in place: %s", exc)


def _tag_id(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    # INSERT OR IGNORE + SELECT rather than SELECT-then-INSERT — the latter races to a UNIQUE IntegrityError
    # when two saves introduce the same new tag concurrently.
    conn.execute("INSERT OR IGNORE INTO tag(name) VALUES(?)", (name,))
    return conn.execute("SELECT id FROM tag WHERE name=?", (name,)).fetchone()[0]


def _delete_work(conn: sqlite3.Connection, work_id: str) -> None:
    conn.execute("DELETE FROM image_tag WHERE image_id IN (SELECT id FROM image WHERE work_id=?)", (work_id,))
    conn.execute("DELETE FROM image_text WHERE image_id IN (SELECT id FROM image WHERE work_id=?)", (work_id,))
    conn.execute("DELETE FROM work_tag WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM image WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM snapshot WHERE work_id=?", (work_id,))
    conn.execute("DELETE FROM work WHERE id=?", (work_id,))


def remove_work(conn: sqlite3.Connection, work_id: str) -> None:
    """Fully drop a work from the index (rows only — the caller removes the on-disk files)."""
    _delete_work(conn, work_id)
    conn.commit()


def epoch_ms(at: datetime) -> int:
    """A moment as epoch milliseconds — a naive one taken as UTC, as the vault's stamps are."""
    return int((at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def _stamp_ms(stamp: str) -> int | None:
    """An ISO stamp as epoch milliseconds — the app writes "…Z", the backend "…+00:00"."""
    try:
        return epoch_ms(datetime.fromisoformat(stamp))
    except (TypeError, ValueError):
        return None


def _fold(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p).casefold()


def ratio(params: dict) -> float | None:
    """An image's aspect ratio from the size it was generated at (an image saved before it kept its own)."""
    w, h = params.get("width"), params.get("height")
    return w / h if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0 else None


def upsert_work(conn: sqlite3.Connection, doc: WorkDoc, dir_name: str, mtime_ns: int | None = None) -> None:
    """``mtime_ns``: the work folder's modification time as ``sync`` lists it — none for the app's own saves, which
    ``sync`` then reads back once."""
    _delete_work(conn, doc.id)
    gallery_count = sum(1 for im in doc.images if im.role == "gallery")  # scratch never counts
    texts = gallery_layout.texts(doc)
    conn.execute(
        "INSERT INTO work(id,dir,title,slug,created_at,updated_at,preview_image_id,image_count,search_text,texts,"
        "mtime_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (doc.id, dir_name, doc.title, doc.slug, doc.created_at, doc.updated_at, doc.preview_image_id, gallery_count,
         _fold(doc.title, *(t for _, t in texts)), json.dumps(texts, ensure_ascii=False), mtime_ns),
    )
    snaps = {s.id: s for s in doc.snapshots}
    for s in doc.snapshots:
        conn.execute(
            "INSERT OR REPLACE INTO snapshot(id,work_id,hash,positive,negative,created_at) VALUES(?,?,?,?,?,?)",
            (s.id, doc.id, s.hash, s.assembled_positive, s.assembled_negative, s.created_at),
        )
    placed = gallery_layout.placements(doc)
    fallback_ts = _stamp_ms(doc.created_at) or 0
    work_tag_ids: set[int] = set()
    for im in doc.images:
        snap = snaps.get(im.snapshot_id or "")
        params = snap.params if snap else {}  # generation params live on the snapshot
        blocks = snap.components if snap else []
        positive = [c for c in blocks if c.polarity != "negative"]
        where = placed.get(im.id)
        heading, section = (where.heading, where.section) if where else (None, None)
        conn.execute(
            "INSERT OR REPLACE INTO image(id,work_id,snapshot_id,file,role,favorite,group_name,seed,model,created_at,"
            "created_ts,ar,description,grid_id,grid_no,heading,section) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (im.id, doc.id, im.snapshot_id, im.file, im.role, int(im.favorite), im.group,
             params.get("seed"), params.get("model"), im.created_at,
             _stamp_ms(im.created_at) or fallback_ts, im.ar or ratio(params), im.description,
             where.grid_id if where else None, where.number if where else None, heading, section),
        )
        conn.execute("INSERT OR REPLACE INTO image_text(image_id,text) VALUES(?,?)", (im.id, _fold(
            snap.assembled_positive if snap else "", im.description, *im.tags, *(c.name for c in positive),
            heading, section)))
        tags = [(t, "block") for c in positive for t in c.tags] + [(t, "manual") for t in im.tags]
        tags += [(t, "negative") for c in blocks if c.polarity == "negative" for t in c.tags]
        for name, source in tags:
            tid = _tag_id(conn, name)
            conn.execute("INSERT OR IGNORE INTO image_tag(image_id,tag_id,source) VALUES(?,?,?)", (im.id, tid, source))
            if im.role == "gallery" and source != "negative":  # a work's tags are its gallery's, as the app shows them
                work_tag_ids.add(tid)
    for tid in work_tag_ids:
        conn.execute("INSERT OR IGNORE INTO work_tag(work_id,tag_id,source) VALUES(?,?,'derived')", (doc.id, tid))
    conn.commit()


def upsert_block(conn: sqlite3.Connection, data: dict, mtime_ns: int | None = None) -> None:
    """``mtime_ns``: the block file's modification time as ``sync`` lists it — none for the app's own saves."""
    conn.execute(
        "INSERT OR REPLACE INTO block(id,category,name,text,polarity,version,created_at,updated_at,mtime_ns) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (data["id"], data.get("category", "custom"), data.get("name", ""), data.get("text", ""),
         data.get("polarity", "positive"), data.get("version", 1),
         data.get("created_at", ""), data.get("updated_at", ""), mtime_ns),
    )
    conn.execute("DELETE FROM block_tag WHERE block_id=?", (data["id"],))
    for t in data.get("tags", []):
        conn.execute("INSERT OR IGNORE INTO block_tag(block_id,tag_id) VALUES(?,?)", (data["id"], _tag_id(conn, t)))
    conn.commit()


def delete_block(conn: sqlite3.Connection, block_id: str) -> None:
    conn.execute("DELETE FROM block_tag WHERE block_id=?", (block_id,))
    conn.execute("DELETE FROM block WHERE id=?", (block_id,))
    conn.commit()


_BLOCK_SORTS = {  # whitelisted ORDER BY clauses — `sort` is validated at the router, this is belt & braces
    "updated": "b.updated_at DESC, b.name",
    "created": "b.created_at DESC, b.name",
    "category": "b.category ASC, b.name",
}


def list_blocks(conn, categories: list[str], tags: list[str], search: str | None, sort: str,
                page: int, per_page: int, category_order: list[str] | None = None):
    where, params = [], []
    if categories:
        ph = ",".join("?" * len(categories))
        where.append(f"b.category IN ({ph})")
        params += categories
    if search:
        where.append(r"(b.name LIKE ? ESCAPE '\' OR b.text LIKE ? ESCAPE '\')")
        params += [_like(search)] * 2
    if tags:
        ph = ",".join("?" * len(tags))
        where.append(
            f"b.id IN (SELECT bt.block_id FROM block_tag bt JOIN tag t ON t.id=bt.tag_id "
            f"WHERE t.name IN ({ph}) GROUP BY bt.block_id HAVING COUNT(DISTINCT t.name)=?)"
        )
        params += tags + [len(tags)]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order_params: list = []
    if sort == "category" and category_order:
        # Sections follow the user's category order (one order everywhere), not slug-alphabetical.
        cases = " ".join(f"WHEN ? THEN {i}" for i in range(len(category_order)))
        order = f"CASE b.category {cases} ELSE {len(category_order)} END, b.name"
        order_params = list(category_order)
    else:
        order = _BLOCK_SORTS.get(sort, _BLOCK_SORTS["updated"])
    total = conn.execute(f"SELECT COUNT(*) FROM block b {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT b.id,b.category,b.name,b.text,b.polarity,b.version,b.created_at,b.updated_at, "
        f"(SELECT GROUP_CONCAT(t.name, char(31)) FROM block_tag bt JOIN tag t ON t.id=bt.tag_id "
        f"WHERE bt.block_id=b.id) AS tags FROM block b {clause} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        params + order_params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return total, rows


def blocks_by_ids(conn, ids: list[str]):
    """Current rows for specific block ids — the prompt widget uses this to detect version drift
    on its pinned (frozen) copies."""
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT b.id,b.category,b.name,b.text,b.polarity,b.version,b.created_at,b.updated_at, "
        f"(SELECT GROUP_CONCAT(t.name, char(31)) FROM block_tag bt JOIN tag t ON t.id=bt.tag_id "
        f"WHERE bt.block_id=b.id) AS tags FROM block b WHERE b.id IN ({ph})",
        ids,
    ).fetchall()


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
        f"WHERE t.name IN ({ph}) AND i.role='gallery' GROUP BY i.id HAVING COUNT(DISTINCT t.name)=? "
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


# Whitelisted ORDER BY columns (the router validates `sort`; this is belt & braces against injection).
_WORK_SORTS = {
    "updated": "updated_at",
    "created": "created_at",
    "name": "title COLLATE NOCASE",
    "image_count": "image_count",
}


def _like(text: str) -> str:
    """A LIKE pattern matching ``text`` anywhere, its wildcards literal (use with ESCAPE '\\')."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _in(column: str, values: tuple | list) -> str:
    return f"{column} IN ({','.join('?' * len(values))})"


_FACET_TAGS = 100  # the most used tags a filter offers
_TEXT = r"SELECT x.image_id FROM image_text x WHERE x.text LIKE ? ESCAPE '\'"  # images whose words match
_GALLERY_TAGGED = ("SELECT it.image_id FROM image_tag it JOIN tag t ON t.id=it.tag_id "
                   "WHERE it.source!='negative'")  # the gallery's tags: an image's own and its positive blocks'


def clean_tags(tags: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Tags as the index keeps them — trimmed, each once, none empty."""
    return tuple(dict.fromkeys(t.strip() for t in tags if t.strip()))


def _work_filter(search: str | None, tags: tuple[str, ...]) -> tuple[str, list]:
    """Works whose title or gallery words — or one of whose gallery images — match ``search``, and whose gallery images
    carry all ``tags`` between them."""
    where, params = ["1=1"], []
    if search:
        like = _like(search.casefold())
        where.append(rf"(w.search_text LIKE ? ESCAPE '\' OR w.id IN (SELECT i.work_id FROM image i "
                     rf"WHERE i.role='gallery' AND i.id IN ({_TEXT})))")
        params += [like, like]
    if tags:
        where.append(f"w.id IN (SELECT wt.work_id FROM work_tag wt JOIN tag t ON t.id=wt.tag_id "
                     f"WHERE {_in('t.name', tags)} GROUP BY wt.work_id HAVING COUNT(DISTINCT t.name)=?)")
        params += [*tags, len(tags)]
    return " AND ".join(where), params


def list_works(conn: sqlite3.Connection, page: int, per_page: int, search: str | None = None,
               sort: str = "updated", direction: str = "desc", tags: tuple[str, ...] = ()):
    where, params = _work_filter(search, tags)
    col = _WORK_SORTS.get(sort, _WORK_SORTS["updated"])
    dir_sql = "ASC" if direction == "asc" else "DESC"
    total = conn.execute(f"SELECT COUNT(*) FROM work w WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id,title,updated_at,image_count,preview_image_id,texts FROM work w WHERE {where} "
        f"ORDER BY {col} {dir_sql}, id LIMIT ? OFFSET ?",  # `id` tiebreak keeps paging deterministic
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return total, rows


def work_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM work").fetchone()[0]


def work_tag_counts(conn: sqlite3.Connection, search: str | None, tags: tuple[str, ...]) -> list[tuple[str, int]]:
    """The tags of the works the filter lists, by how many of them carry each — the picked ones always among them."""
    where, params = _work_filter(search, tags)
    rows = conn.execute(
        f"SELECT t.name AS name, COUNT(DISTINCT wt.work_id) AS count FROM work_tag wt JOIN tag t ON t.id=wt.tag_id "
        f"WHERE wt.work_id IN (SELECT w.id FROM work w WHERE {where}) GROUP BY t.name ORDER BY count DESC, t.name "
        f"LIMIT ?", params + [_FACET_TAGS]).fetchall()
    out = [(r["name"], r["count"]) for r in rows]
    if missing := [t for t in tags if t not in {n for n, _ in out}]:  # every listed work has them all
        total = conn.execute(f"SELECT COUNT(*) FROM work w WHERE {where}", params).fetchone()[0]
        out += [(t, total) for t in missing]
    return out


def matching_images(conn: sqlite3.Connection, work_ids: list[str], search: str) -> dict[str, int]:
    """How many gallery images of each work match ``search`` — where a work matched when its words didn't."""
    if not work_ids:
        return {}
    rows = conn.execute(
        f"SELECT work_id, COUNT(*) AS n FROM image WHERE role='gallery' AND {_in('work_id', work_ids)} "
        f"AND id IN ({_TEXT}) GROUP BY work_id", [*work_ids, _like(search.casefold())]).fetchall()
    return {r["work_id"]: r["n"] for r in rows}


Unsorted = Literal["include", "exclude", "only"]


@dataclass(frozen=True)
class ImageQuery:
    """The gallery's filters: ``text`` in an image's words; all ``tags``; any of ``works`` and ``models``; created
    within [``since``, ``until``) (epoch ms); ``unsorted`` — images in no album: include, exclude or only."""

    text: str = ""
    tags: tuple[str, ...] = ()
    works: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    favorite: bool = False
    since: int | None = None
    until: int | None = None
    unsorted: Unsorted = "include"


def _image_filter(q: ImageQuery, ignore: frozenset[str] = frozenset()) -> tuple[str, list]:
    """The WHERE of the images ``q`` lists, without the filters named in ``ignore`` ("works", "models", "unsorted")."""
    where, params = ["i.role='gallery'"], []  # scratch is working material — never listed
    if q.text:
        where.append(f"i.id IN ({_TEXT})")
        params.append(_like(q.text.casefold()))
    if q.tags:
        where.append(f"i.id IN ({_GALLERY_TAGGED} AND {_in('t.name', q.tags)} "
                     f"GROUP BY it.image_id HAVING COUNT(DISTINCT t.name)=?)")
        params += [*q.tags, len(q.tags)]
    if q.works and "works" not in ignore:
        where.append(_in("i.work_id", q.works))
        params += q.works
    if q.models and "models" not in ignore:
        where.append(_in("i.model", q.models))
        params += q.models
    if q.favorite:
        where.append("i.favorite=1")
    if q.since is not None:
        where.append("i.created_ts>=?")
        params.append(q.since)
    if q.until is not None:
        where.append("i.created_ts<?")
        params.append(q.until)
    if "unsorted" not in ignore and q.unsorted != "include":
        where.append("i.grid_id IS NULL" if q.unsorted == "only" else "i.grid_id IS NOT NULL")
    return " AND ".join(where), params


def gallery(conn: sqlite3.Connection, q: ImageQuery, per_page: int, *, page: int = 1,
            after: tuple[int, str] | None = None, oldest_first: bool = False):
    """A page of the images ``q`` lists, newest first unless ``oldest_first``: the ``page``-th — or the one after
    ``after`` (the last image seen: its ``created_ts`` and id), which images landing or leaving meanwhile don't shift.
    Returns the total, the rows and where the next page starts (None: this is the last)."""
    where, params = _image_filter(q)
    total = conn.execute(f"SELECT COUNT(*) FROM image i WHERE {where}", params).fetchone()[0]
    if after is not None:
        where += f" AND (i.created_ts{'>' if oldest_first else '<'}? OR (i.created_ts=? AND i.id>?))"
        params = [*params, after[0], after[0], after[1]]
    rows = conn.execute(
        f"SELECT i.id,i.work_id,w.title AS work_title,i.favorite,i.created_at,i.created_ts,i.ar,i.model,i.grid_id,"
        f"i.grid_no,i.heading,i.section FROM image i JOIN work w ON w.id=i.work_id WHERE {where} "
        f"ORDER BY i.created_ts {'ASC' if oldest_first else 'DESC'}, i.id LIMIT ? OFFSET ?",  # `id`: one save stamps
        params + [per_page + 1, 0 if after is not None else (page - 1) * per_page],     # a batch alike
    ).fetchall()
    more = len(rows) > per_page
    rows = rows[:per_page]
    return total, rows, (rows[-1]["created_ts"], rows[-1]["id"]) if more else None


def gallery_facets(conn: sqlite3.Connection, q: ImageQuery) -> dict:
    """What the gallery's filters offer for ``q``: the listed images' tags (most used first), the models and works
    each filter could pick with how many images it would list (its own filter left out), how many unsorted images
    match the rest — the picked values always among them. One pass over the images, one over their tags."""
    where, params = _image_filter(q, frozenset({"works", "models", "unsorted"}))
    rows = conn.execute(f"SELECT i.work_id AS work, i.model AS model, i.grid_id IS NULL AS loose FROM image i "
                        f"WHERE {where}", params).fetchall()
    works, models = set(q.works), set(q.models)

    def kept(r: sqlite3.Row, skip: str = "") -> bool:
        return ((skip == "works" or not works or r["work"] in works)
                and (skip == "models" or not models or r["model"] in models)
                and (skip == "unsorted" or q.unsorted == "include" or bool(r["loose"]) == (q.unsorted == "only")))

    total = sum(kept(r) for r in rows)
    unsorted = sum(bool(r["loose"]) and kept(r, "unsorted") for r in rows)
    by_model = Counter(r["model"] for r in rows if r["model"] is not None and kept(r, "models"))
    by_work = Counter(r["work"] for r in rows if kept(r, "works"))
    titles = {r["id"]: r["title"] or "" for r in conn.execute("SELECT id,title FROM work")}
    where_all, params_all = _image_filter(q)
    tags = [(r["name"], r["count"]) for r in conn.execute(
        f"SELECT t.name AS name, COUNT(DISTINCT it.image_id) AS count FROM image_tag it JOIN tag t ON t.id=it.tag_id "
        f"WHERE it.source!='negative' AND it.image_id IN (SELECT i.id FROM image i WHERE {where_all}) "
        f"GROUP BY t.name ORDER BY count DESC, t.name LIMIT ?", params_all + [_FACET_TAGS])]
    tags += [(t, total) for t in q.tags if t not in {n for n, _ in tags}]  # every listed image has them all
    model_counts = sorted(by_model.items(), key=lambda m: (-m[1], m[0]))
    model_counts += [(m, 0) for m in q.models if m not in by_model]
    work_counts = sorted(((w, titles.get(w, ""), n) for w, n in by_work.items()),
                         key=lambda w: (-w[2], w[1].casefold(), w[0]))
    work_counts += [(w, titles[w], 0) for w in q.works if w not in by_work and w in titles]
    return {"total": total, "unsorted": unsorted, "tags": tags, "models": model_counts, "works": work_counts}


def gallery_images(conn: sqlite3.Connection, image_ids: list[str]) -> set[tuple[str, str]]:
    """Which of these are gallery images — as (work id, image id)."""
    if not image_ids:
        return set()
    rows = conn.execute(f"SELECT work_id, id FROM image WHERE role='gallery' AND {_in('id', image_ids)}", image_ids)
    return {(r["work_id"], r["id"]) for r in rows}


def _out_of_reach(vault: Path) -> None:
    """Raise when the vault itself can't be seen — a share that dropped mid-sync reports its folders and files as
    missing, which must never read as "deleted": the request waits (503), and the next one syncs again."""
    if not vaultcfg.is_initialized(vault):
        raise appconfig.VaultUnavailable(str(vault))


def _listing(vault: Path, folder: Path) -> list[os.DirEntry]:
    """A folder's entries with their modification times: the listing carries them (on Windows, and over SMB in bulk),
    so reading them costs no request per entry — what keeps ``sync`` cheap on a network share."""
    try:
        with os.scandir(folder) as entries:
            return list(entries)
    except FileNotFoundError:
        _out_of_reach(vault)
        return []


def _block_files(vault: Path, folder: Path) -> Iterator[os.DirEntry]:
    for entry in _listing(vault, folder):
        if entry.name.startswith("._"):  # macOS's AppleDouble litter on a share — never a block
            continue
        if entry.is_dir(follow_symlinks=False):  # a linked folder could loop
            yield from _block_files(vault, Path(entry.path))
        elif entry.name.endswith(".json"):
            yield entry


def _gone(vault: Path, path: Path, exc: Exception) -> bool:
    """For a file ``sync`` couldn't read: whether it is gone for good (forget what the index holds for it) or only
    unreadable right now (keep that). A vault out of reach altogether raises instead."""
    if not isinstance(exc, OSError):  # unparseable: as good as gone
        log.warning("Skipping unreadable file: %s", path)
        return True
    _out_of_reach(vault)
    if isinstance(exc, FileNotFoundError):  # e.g. a work folder without a manifest: no work, not a fault
        return True
    log.warning("Can't read %s now — keeping what the index holds: %s", path, exc)
    return False


def _forget(conn: sqlite3.Connection, work_id: str | None, dir_name: str) -> int:
    """Drop what the index holds for a folder — unless that work has since turned up in another one (renamed)."""
    if work_id is None or find_work_dir(conn, work_id) != dir_name:
        return 0
    _delete_work(conn, work_id)
    return 1


def sync(conn: sqlite3.Connection, vault: Path) -> None:
    """Bring the index in line with the vault's files, which can change without this app (another build of it, a
    restored backup, a different vault at the same path). A work is read again only when its folder's modification time
    differs from the indexed one — every save replaces ``work.json`` in it (a hand edit made in place doesn't move it) —
    and a block when its file's does; whatever is gone is dropped. An empty index reads everything: that is the
    rebuild. One work or block in two places stays where the index first found it, rather than swapping every run."""
    read = dropped = 0
    known = {r["dir"]: (r["id"], r["mtime_ns"]) for r in conn.execute("SELECT id,dir,mtime_ns FROM work")}
    folders = [e for e in _listing(vault, vault / "works") if e.is_dir()]
    listed = {e.name for e in folders}
    for entry in folders:
        mtime = entry.stat().st_mtime_ns  # taken before the read: a save in between is caught by the next sync
        work_id, indexed = known.get(entry.name, (None, None))
        if indexed == mtime:
            continue
        wj = Path(entry.path) / "work.json"
        try:
            # Same migration choke point as store.read_work — a v1 work must index the same
            # way it loads, or the gallery and the canvas would disagree.
            doc = migrate.load_doc(wj.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001 — a bad file must not abort the whole sync
            if _gone(vault, wj, exc):
                dropped += _forget(conn, work_id, entry.name)
                conn.commit()
            continue
        where = find_work_dir(conn, doc.id)
        if where not in (None, entry.name) and where in listed:  # a copied folder (Explorer's "… - Copy")
            log.warning("Work %s is in two folders, %s and %s — the second is left out", doc.id, where, entry.name)
            continue
        try:
            _forget(conn, work_id, entry.name)  # the work this folder held may be another one now
            upsert_work(conn, doc, entry.name, mtime)
            read += 1
        except Exception as exc:  # noqa: BLE001 — one malformed work must not 500 the sync
            conn.rollback()
            if _index_fault(exc):
                raise
            log.warning("Skipping work that failed to index: %s", wj)
    for dir_name, (work_id, _) in known.items():
        if dir_name not in listed:
            dropped += _forget(conn, work_id, dir_name)
    conn.commit()
    known_blocks = {r["id"]: r["mtime_ns"] for r in conn.execute("SELECT id,mtime_ns FROM block")}
    keep: set[str] = set()
    files: set[str] = set()
    for entry in sorted(_block_files(vault, vault / "library" / "blocks"), key=lambda e: e.path):
        block_id = entry.name.removesuffix(".json")
        if block_id in files:  # one block in two category folders: the first by path stands, every run
            log.warning("Block %s is in two files — %s is left out", block_id, entry.path)
            continue
        files.add(block_id)
        mtime = entry.stat().st_mtime_ns
        if known_blocks.get(block_id) == mtime:
            keep.add(block_id)
            continue
        try:
            data = json.loads(Path(entry.path).read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            if not _gone(vault, Path(entry.path), exc):
                keep.add(block_id)
            continue
        try:
            upsert_block(conn, data, mtime)
            keep.add(data["id"])
            read += 1
        except Exception as exc:  # noqa: BLE001 — one malformed block must not 500 the sync
            conn.rollback()
            if _index_fault(exc):
                raise
            log.warning("Skipping block that failed to index: %s", entry.path)
    for block_id in known_blocks.keys() - keep:
        delete_block(conn, block_id)
        dropped += 1
    if read or dropped:
        log.info("Synced the vault index with %s: %d read, %d dropped", vault, read, dropped)
