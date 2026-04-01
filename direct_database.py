"""
CNC Direct Editor — Database layer.

The DB file (condenser_direct.db) lives inside the scanned folder.
All state is stored here: file index, duplicate groups, scan log, settings.
"""

import sqlite3
import os
from typing import Optional


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_schema(db_path: str):
    """Create all tables and indexes if they don't exist."""
    conn = get_connection(db_path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path       TEXT NOT NULL UNIQUE,   -- absolute path on disk
                file_name       TEXT NOT NULL,           -- bare filename e.g. O65123
                o_number        TEXT NOT NULL,           -- normalized uppercase e.g. O65123
                o_suffix        TEXT,                    -- numeric backup suffix _N, or NULL
                file_hash       TEXT,                    -- xxhash xxh64 hex
                line_count      INTEGER DEFAULT 0,
                program_title   TEXT DEFAULT '',
                derived_from    TEXT DEFAULT '',
                source_folder   TEXT DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'active',
                                                         -- active | flagged | review | delete
                verify_status   TEXT DEFAULT '',
                verify_score    INTEGER DEFAULT 0,       -- count of PASS tokens 0-6
                has_dup_flag    INTEGER NOT NULL DEFAULT 0,
                notes           TEXT DEFAULT '',
                last_seen       TEXT,                    -- ISO timestamp of last scan
                last_modified   TEXT,                    -- file mtime at last scan
                index_date      TEXT                     -- ISO timestamp of first index
            );

            CREATE TABLE IF NOT EXISTS dup_groups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_type      TEXT NOT NULL,
                                -- exact | name_conflict | backup_chain | derived | title_match
                o_number        TEXT,
                group_hash      TEXT,
                recommended_id  INTEGER REFERENCES files(id),
                member_count    INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS dup_group_members (
                file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                group_id    INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
                score       INTEGER DEFAULT 0,
                PRIMARY KEY (file_id, group_id)
            );

            CREATE TABLE IF NOT EXISTS scan_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date       TEXT NOT NULL,
                folder_paths    TEXT NOT NULL,
                files_found     INTEGER DEFAULT 0,
                files_new       INTEGER DEFAULT 0,
                files_removed   INTEGER DEFAULT 0,
                files_changed   INTEGER DEFAULT 0,
                dup_groups      INTEGER DEFAULT 0,
                duration_sec    REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );

            CREATE TABLE IF NOT EXISTS file_revisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                label       TEXT NOT NULL,          -- e.g. "Rev A", "Before drill change"
                notes       TEXT DEFAULT '',        -- optional operator notes
                backup_path TEXT NOT NULL,          -- absolute path to the backed-up file
                created_at  TEXT NOT NULL           -- ISO timestamp
            );

            CREATE INDEX IF NOT EXISTS idx_revisions_file ON file_revisions(file_id);
            CREATE INDEX IF NOT EXISTS idx_files_o_number ON files(o_number);
            CREATE INDEX IF NOT EXISTS idx_files_hash     ON files(file_hash);
            CREATE INDEX IF NOT EXISTS idx_files_status   ON files(status);
            CREATE INDEX IF NOT EXISTS idx_files_has_dup  ON files(has_dup_flag);
            CREATE INDEX IF NOT EXISTS idx_files_score    ON files(verify_score);
            CREATE INDEX IF NOT EXISTS idx_files_path     ON files(file_path);
        """)

        # Add new safeguard columns to existing DBs (ALTER TABLE is safe with IF NOT EXISTS
        # workaround — SQLite doesn't support IF NOT EXISTS on ADD COLUMN, so we catch errors)
        for col_sql in [
            "ALTER TABLE files ADD COLUMN has_onum_mismatch INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE files ADD COLUMN no_gcode_flag     INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE files ADD COLUMN internal_o_number TEXT    DEFAULT ''",
            "ALTER TABLE files ADD COLUMN no_eop_flag       INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE files ADD COLUMN has_range_conflict INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass  # column already exists

        # Seed default settings (INSERT OR IGNORE — never overwrite user values)
        defaults = {
            "auto_backup_on_edit":  "1",
            "backup_extension":     ".bak",
            "backup_folder":        "",      # chosen on first edit
            "allow_delete":         "1",
            "last_scan_folders":    "",
            "last_scan_date":       "",
        }
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (k, v))
    conn.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(db_path: str, key: str, default: str = "") -> str:
    conn = get_connection(db_path)
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(db_path: str, key: str, value: str):
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.close()


def get_all_settings(db_path: str) -> dict:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# File queries
# ---------------------------------------------------------------------------

def get_all_files(db_path: str,
                  status: Optional[str] = None,
                  has_dup_flag: Optional[int] = None,
                  score_min: Optional[int] = None,
                  score_max: Optional[int] = None,
                  source_folder: Optional[str] = None,
                  recent_days: Optional[int] = None,
                  verify_filter: Optional[str] = None,
                  scope_folders: Optional[list] = None,
                  attention_filter: Optional[str] = None) -> list:
    """Return all file rows matching the given filters.
    verify_filter: 'all_pass' | 'has_fail' | 'not_verified'
    scope_folders: when set, only files whose source_folder matches one of these
                   paths are returned — prevents stale records from other locations.
    """
    conn = get_connection(db_path)
    clauses = []
    params  = []

    # Scope to current open folder(s) — always applied when provided
    if scope_folders:
        placeholders = ",".join("?" * len(scope_folders))
        clauses.append(f"source_folder IN ({placeholders})")
        params.extend(scope_folders)

    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if has_dup_flag is not None:
        clauses.append("has_dup_flag = ?")
        params.append(has_dup_flag)
    if score_min is not None:
        clauses.append("verify_score >= ?")
        params.append(score_min)
    if score_max is not None:
        clauses.append("verify_score <= ?")
        params.append(score_max)
    if source_folder is not None:
        clauses.append("source_folder = ?")
        params.append(source_folder)
    if recent_days is not None:
        clauses.append(
            "last_modified >= datetime('now', ?)"
        )
        params.append(f"-{recent_days} days")
    if verify_filter == "all_pass":
        clauses.append(
            "verify_status IS NOT NULL AND verify_status != '' "
            "AND verify_status NOT LIKE '%FAIL%'")
    elif verify_filter == "has_fail":
        clauses.append("verify_status LIKE '%FAIL%'")
    elif verify_filter == "not_verified":
        clauses.append("(verify_status IS NULL OR verify_status = '')")
    if attention_filter == "onum_mismatch":
        clauses.append("has_onum_mismatch = 1")
    elif attention_filter == "no_gcode":
        clauses.append("no_gcode_flag = 1")
    elif attention_filter == "no_eop":
        clauses.append("no_eop_flag = 1")
    elif attention_filter == "range_conflict":
        clauses.append("has_range_conflict = 1")
    elif attention_filter == "folder_conflict":
        # Files that share their O-number with another file in the same source folder
        clauses.append(
            "EXISTS ("
            "  SELECT 1 FROM files f2 "
            "  WHERE f2.o_number = files.o_number "
            "  AND f2.source_folder = files.source_folder "
            "  AND f2.id != files.id "
            "  AND COALESCE(f2.file_hash,'') != COALESCE(files.file_hash,'') "
            ")"
        )
    elif attention_filter == "shop_special":
        clauses.append("status = 'shop_special'")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM files {where} ORDER BY last_modified DESC, o_number", params
    ).fetchall()
    conn.close()
    return rows


def get_file_by_id(db_path: str, file_id: int):
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    conn.close()
    return row


def get_file_by_path(db_path: str, file_path: str):
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM files WHERE file_path=?", (file_path,)).fetchone()
    conn.close()
    return row


def get_all_paths(db_path: str) -> dict:
    """Return {file_path: row} for all files. Used by scanner for merge."""
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM files").fetchall()
    conn.close()
    return {r["file_path"]: dict(r) for r in rows}


def upsert_file(db_path: str, conn: sqlite3.Connection, record: dict):
    """Insert or update a file record. Uses open connection (caller manages tx)."""
    # Provide defaults for new safeguard columns so old callers don't need updating
    r = dict(record)
    r.setdefault("has_onum_mismatch",  0)
    r.setdefault("no_gcode_flag",      0)
    r.setdefault("internal_o_number",  "")
    r.setdefault("no_eop_flag",        0)
    r.setdefault("has_range_conflict", 0)
    conn.execute("""
        INSERT INTO files (
            file_path, file_name, o_number, o_suffix, file_hash,
            line_count, program_title, derived_from, source_folder,
            status, verify_status, verify_score, has_dup_flag,
            has_onum_mismatch, no_gcode_flag, internal_o_number,
            no_eop_flag, has_range_conflict,
            notes, last_seen, last_modified, index_date
        ) VALUES (
            :file_path, :file_name, :o_number, :o_suffix, :file_hash,
            :line_count, :program_title, :derived_from, :source_folder,
            :status, :verify_status, :verify_score, :has_dup_flag,
            :has_onum_mismatch, :no_gcode_flag, :internal_o_number,
            :no_eop_flag, :has_range_conflict,
            :notes, :last_seen, :last_modified, :index_date
        )
        ON CONFLICT(file_path) DO UPDATE SET
            file_name          = excluded.file_name,
            o_number           = excluded.o_number,
            o_suffix           = excluded.o_suffix,
            file_hash          = excluded.file_hash,
            line_count         = excluded.line_count,
            program_title      = excluded.program_title,
            derived_from       = excluded.derived_from,
            source_folder      = excluded.source_folder,
            verify_status      = excluded.verify_status,
            verify_score       = excluded.verify_score,
            has_dup_flag       = excluded.has_dup_flag,
            has_onum_mismatch  = excluded.has_onum_mismatch,
            no_gcode_flag      = excluded.no_gcode_flag,
            internal_o_number  = excluded.internal_o_number,
            no_eop_flag        = excluded.no_eop_flag,
            has_range_conflict = excluded.has_range_conflict,
            last_seen          = excluded.last_seen,
            last_modified      = excluded.last_modified,
            notes              = excluded.notes
            -- status is NOT updated on conflict: preserve user value
    """, r)


def update_file_status(db_path: str, file_id: int, status: str):
    conn = get_connection(db_path)
    with conn:
        conn.execute("UPDATE files SET status=? WHERE id=?", (status, file_id))
    conn.close()


def update_file_notes(db_path: str, file_id: int, notes: str):
    conn = get_connection(db_path)
    with conn:
        conn.execute("UPDATE files SET notes=? WHERE id=?", (notes, file_id))
    conn.close()


def update_file_after_edit(db_path: str, file_id: int,
                           file_hash: str, line_count: int,
                           program_title: str, derived_from: str,
                           verify_status: str, verify_score: int,
                           has_dup_flag: int, last_modified: str):
    """Called after in-place edit: refresh all computed fields."""
    conn = get_connection(db_path)
    with conn:
        conn.execute("""
            UPDATE files SET
                file_hash     = ?,
                line_count    = ?,
                program_title = ?,
                derived_from  = ?,
                verify_status = ?,
                verify_score  = ?,
                has_dup_flag  = ?,
                last_modified = ?
            WHERE id = ?
        """, (file_hash, line_count, program_title, derived_from,
              verify_status, verify_score, has_dup_flag, last_modified, file_id))
    conn.close()


def update_file_path_and_name(db_path: str, file_id: int,
                              new_path: str, new_name: str, new_o_number: str):
    """Called after on-disk rename. Updates path, name, and o_number."""
    conn = get_connection(db_path)
    with conn:
        conn.execute("""
            UPDATE files SET file_path=?, file_name=?, o_number=?
            WHERE id=?
        """, (new_path, new_name, new_o_number.upper(), file_id))
    conn.close()


def mark_file_missing(db_path: str, file_id: int, timestamp: str,
                      conn: sqlite3.Connection = None):
    """File was not found on disk during rescan.
    If conn is supplied it is used directly (caller manages the transaction).
    """
    def _do(c):
        row = c.execute("SELECT notes FROM files WHERE id=?", (file_id,)).fetchone()
        notes = row["notes"] if row else ""
        tag = f"[MISSING] Not found on disk since {timestamp}"
        if "[MISSING]" not in (notes or ""):
            notes = ((notes or "") + "\n" + tag).strip()
        c.execute("UPDATE files SET last_seen=NULL, notes=? WHERE id=?", (notes, file_id))

    if conn is not None:
        _do(conn)
    else:
        _conn = get_connection(db_path)
        with _conn:
            _do(_conn)
        _conn.close()


# ---------------------------------------------------------------------------
# Source-folder introspection (used for stale-DB detection)
# ---------------------------------------------------------------------------

def get_used_o_numbers(db_path: str) -> set:
    """Return a set of all O-numbers currently in the database (uppercase, e.g. 'O12345')."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT o_number FROM files WHERE o_number IS NOT NULL"
    ).fetchall()
    conn.close()
    return {r["o_number"].upper() for r in rows}


def get_distinct_source_folders(db_path: str) -> list[str]:
    """Return all distinct source_folder values recorded in the DB."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT source_folder FROM files WHERE source_folder IS NOT NULL"
    ).fetchall()
    conn.close()
    return [r["source_folder"] for r in rows]


def clear_all_files(db_path: str):
    """Delete every file record and all duplicate data — clean slate.
    Safe to call on old-schema databases that may not have all tables yet."""
    conn = get_connection(db_path)
    with conn:
        # Use CREATE TABLE IF NOT EXISTS indirectly by just ignoring missing tables
        for table in ("group_members", "duplicate_groups", "files"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass  # table doesn't exist yet — init_schema will create it
    conn.close()


# ---------------------------------------------------------------------------
# Status counts (for sidebar)
# ---------------------------------------------------------------------------

def _scope_clause(scope_folders: list | None) -> tuple[str, list]:
    """Return (WHERE/AND clause fragment, params) for scoping to source_folders."""
    if not scope_folders:
        return "", []
    placeholders = ",".join("?" * len(scope_folders))
    return f"source_folder IN ({placeholders})", list(scope_folders)


def get_status_counts(db_path: str, scope_folders: list | None = None) -> dict:
    scope_sql, scope_params = _scope_clause(scope_folders)
    where = f"WHERE {scope_sql}" if scope_sql else ""
    and_  = f"AND {scope_sql}" if scope_sql else ""

    conn = get_connection(db_path)
    rows    = conn.execute(
        f"SELECT status, COUNT(*) as n FROM files {where} GROUP BY status",
        scope_params
    ).fetchall()
    total   = conn.execute(
        f"SELECT COUNT(*) as n FROM files {where}", scope_params
    ).fetchone()["n"]
    has_dup = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE has_dup_flag=1 {and_}",
        scope_params
    ).fetchone()["n"]
    missing = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE last_seen IS NULL {and_}",
        scope_params
    ).fetchone()["n"]
    conn.close()

    counts = {"total": total, "has_dup": has_dup, "missing": missing}
    for r in rows:
        counts[r["status"]] = r["n"]
    return counts


def get_score_counts(db_path: str, scope_folders: list | None = None) -> dict:
    """Return counts bucketed: perfect(6), good(4-5), fair(2-3), poor(0-1)."""
    scope_sql, scope_params = _scope_clause(scope_folders)
    where = f"WHERE {scope_sql}" if scope_sql else ""

    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT verify_score, COUNT(*) as n FROM files {where} GROUP BY verify_score",
        scope_params
    ).fetchall()
    conn.close()
    buckets = {"6": 0, "4-5": 0, "2-3": 0, "0-1": 0}
    for r in rows:
        s = r["verify_score"]
        if s == 6:            buckets["6"]   += r["n"]
        elif s in (4, 5):     buckets["4-5"] += r["n"]
        elif s in (2, 3):     buckets["2-3"] += r["n"]
        else:                 buckets["0-1"] += r["n"]
    return buckets


# ---------------------------------------------------------------------------
# Verify pass/fail counts (for sidebar)
# ---------------------------------------------------------------------------

def get_verify_counts(db_path: str, scope_folders: list | None = None) -> dict:
    """Return {all_pass, has_fail, not_verified} for sidebar."""
    scope_sql, scope_params = _scope_clause(scope_folders)
    and_ = f"AND {scope_sql}" if scope_sql else ""

    conn = get_connection(db_path)
    all_pass = conn.execute(
        f"SELECT COUNT(*) as n FROM files "
        f"WHERE verify_status IS NOT NULL AND verify_status != '' "
        f"AND verify_status NOT LIKE '%FAIL%' {and_}",
        scope_params
    ).fetchone()["n"]
    has_fail = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE verify_status LIKE '%FAIL%' {and_}",
        scope_params
    ).fetchone()["n"]
    not_verified = conn.execute(
        f"SELECT COUNT(*) as n FROM files "
        f"WHERE (verify_status IS NULL OR verify_status = '') {and_}",
        scope_params
    ).fetchone()["n"]
    conn.close()
    return {"all_pass": all_pass, "has_fail": has_fail, "not_verified": not_verified}


# ---------------------------------------------------------------------------
# Safeguard / attention counts (for sidebar)
# ---------------------------------------------------------------------------

def get_attention_counts(db_path: str, scope_folders: list | None = None) -> dict:
    """Return counts for the NEEDS ATTENTION sidebar section."""
    scope_sql, scope_params = _scope_clause(scope_folders)
    and_ = f"AND {scope_sql}" if scope_sql else ""

    # Same-folder conflict: files where another file in the SAME source_folder
    # shares the same O-number but has different content.
    if scope_sql:
        folder_conflict_sql = (
            f"SELECT COUNT(DISTINCT f.id) FROM files f "
            f"WHERE {scope_sql} "
            f"AND EXISTS ("
            f"  SELECT 1 FROM files f2 "
            f"  WHERE f2.o_number = f.o_number "
            f"  AND f2.source_folder = f.source_folder "
            f"  AND f2.id != f.id "
            f"  AND COALESCE(f2.file_hash,'') != COALESCE(f.file_hash,'') "
            f")"
        )
        folder_conflict_params = list(scope_params)
    else:
        folder_conflict_sql = (
            "SELECT COUNT(DISTINCT f.id) FROM files f "
            "WHERE EXISTS ("
            "  SELECT 1 FROM files f2 "
            "  WHERE f2.o_number = f.o_number "
            "  AND f2.source_folder = f.source_folder "
            "  AND f2.id != f.id "
            "  AND COALESCE(f2.file_hash,'') != COALESCE(f.file_hash,'') "
            ")"
        )
        folder_conflict_params = []

    conn = get_connection(db_path)
    onum_mismatch = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE has_onum_mismatch=1 {and_}",
        scope_params
    ).fetchone()["n"]
    no_gcode = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE no_gcode_flag=1 {and_}",
        scope_params
    ).fetchone()["n"]
    no_eop = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE no_eop_flag=1 {and_}",
        scope_params
    ).fetchone()["n"]
    range_conflict = conn.execute(
        f"SELECT COUNT(*) as n FROM files WHERE has_range_conflict=1 {and_}",
        scope_params
    ).fetchone()["n"]
    folder_conflict = conn.execute(folder_conflict_sql, folder_conflict_params).fetchone()[0]
    conn.close()
    return {
        "onum_mismatch":   onum_mismatch,
        "no_gcode":        no_gcode,
        "no_eop":          no_eop,
        "range_conflict":  range_conflict,
        "folder_conflict": folder_conflict,
    }


# ---------------------------------------------------------------------------
# Duplicate groups
# ---------------------------------------------------------------------------

def get_dup_group_counts(db_path: str, scope_folders: list | None = None) -> dict:
    # When scoping, only count groups that have at least one member in scope
    if scope_folders:
        placeholders = ",".join("?" * len(scope_folders))
        scope_sql = (
            f"WHERE id IN ("
            f"  SELECT DISTINCT dg.id FROM dup_groups dg"
            f"  JOIN dup_group_members dgm ON dgm.group_id = dg.id"
            f"  JOIN files f ON f.id = dgm.file_id"
            f"  WHERE f.source_folder IN ({placeholders})"
            f")"
        )
        scope_params = list(scope_folders)
    else:
        scope_sql    = ""
        scope_params = []

    conn  = get_connection(db_path)
    rows  = conn.execute(
        f"SELECT group_type, COUNT(*) as n FROM dup_groups {scope_sql} GROUP BY group_type",
        scope_params
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) as n FROM dup_groups {scope_sql}", scope_params
    ).fetchone()["n"]
    conn.close()
    counts = {"total": total}
    for r in rows:
        counts[r["group_type"]] = r["n"]
    return counts


def get_files_in_dup_group(db_path: str, group_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT f.*, dgm.score as member_score
        FROM files f
        JOIN dup_group_members dgm ON f.id = dgm.file_id
        WHERE dgm.group_id = ?
        ORDER BY dgm.score DESC, f.o_suffix NULLS FIRST, f.file_path
    """, (group_id,)).fetchall()
    conn.close()
    return rows


def get_all_dup_groups(db_path: str, group_type: Optional[str] = None) -> list:
    conn = get_connection(db_path)
    if group_type:
        rows = conn.execute(
            "SELECT * FROM dup_groups WHERE group_type=? ORDER BY o_number, created_at",
            (group_type,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dup_groups ORDER BY group_type, o_number, created_at"
        ).fetchall()
    conn.close()
    return rows


def get_dup_groups_for_file(db_path: str, file_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT dg.*
        FROM dup_groups dg
        JOIN dup_group_members dgm ON dg.id = dgm.group_id
        WHERE dgm.file_id = ?
    """, (file_id,)).fetchall()
    conn.close()
    return rows


def clear_dup_data(db_path: str, conn: sqlite3.Connection):
    """Wipe all dup group data. Called at start of rescan dedup phase."""
    conn.execute("DELETE FROM dup_group_members")
    conn.execute("DELETE FROM dup_groups")
    # Reset dup flags on all files (scanner will re-set them)
    conn.execute("UPDATE files SET has_dup_flag=0")


def insert_dup_group(db_path: str, conn: sqlite3.Connection,
                     group_type: str, o_number: Optional[str],
                     group_hash: Optional[str], recommended_id: Optional[int],
                     member_ids: list, member_scores: dict,
                     timestamp: str) -> int:
    """Insert a dup_groups row and its members. Returns group id."""
    cur = conn.execute("""
        INSERT INTO dup_groups
            (group_type, o_number, group_hash, recommended_id, member_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (group_type, o_number, group_hash, recommended_id, len(member_ids), timestamp))
    group_id = cur.lastrowid
    for fid in member_ids:
        conn.execute("""
            INSERT OR IGNORE INTO dup_group_members (file_id, group_id, score)
            VALUES (?, ?, ?)
        """, (fid, group_id, member_scores.get(fid, 0)))
    return group_id


# ---------------------------------------------------------------------------
# Scan log
# ---------------------------------------------------------------------------

def insert_scan_log(db_path: str, record: dict):
    conn = get_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO scan_log
                (scan_date, folder_paths, files_found, files_new,
                 files_removed, files_changed, dup_groups, duration_sec)
            VALUES
                (:scan_date, :folder_paths, :files_found, :files_new,
                 :files_removed, :files_changed, :dup_groups, :duration_sec)
        """, record)
    conn.close()


def get_last_scan_log(db_path: str):
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM scan_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------

def save_revision(db_path: str, file_id: int, label: str,
                  notes: str, backup_path: str, timestamp: str):
    """Record a named revision for a file."""
    conn = get_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO file_revisions (file_id, label, notes, backup_path, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (file_id, label, notes, backup_path, timestamp))
    conn.close()


def get_revisions_for_file(db_path: str, file_id: int) -> list:
    """Return all revisions for a file, newest first."""
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT * FROM file_revisions WHERE file_id=?
        ORDER BY created_at DESC
    """, (file_id,)).fetchall()
    conn.close()
    return rows


def delete_revision(db_path: str, revision_id: int):
    conn = get_connection(db_path)
    with conn:
        conn.execute("DELETE FROM file_revisions WHERE id=?", (revision_id,))
    conn.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_file_record(db_path: str, file_id: int):
    """Remove a file's DB record (after physical deletion confirmed)."""
    conn = get_connection(db_path)
    with conn:
        conn.execute("DELETE FROM dup_group_members WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.close()


def delete_trash_files(db_path: str) -> tuple[int, int]:
    """
    Permanently delete all files with status='delete'.

    Removes the physical file from disk (if it exists) and removes the
    DB record including any dup_group_members rows.

    Returns (deleted_count, missing_count) where missing_count is files
    that were already absent from disk but whose records were still purged.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT id, file_path FROM files WHERE status = 'delete'"
    ).fetchall()

    # Delete physical files first, track which IDs succeeded
    ids_to_purge: list[int] = []
    deleted = 0
    missing = 0
    for r in rows:
        fid  = r["id"]
        path = r["file_path"] or ""
        if path and os.path.isfile(path):
            try:
                os.remove(path)
                deleted += 1
                ids_to_purge.append(fid)
            except OSError:
                pass  # leave DB record if we can't delete the physical file
        else:
            missing += 1
            ids_to_purge.append(fid)

    with conn:
        for fid in ids_to_purge:
            # NULL out any dup_group recommended_id refs to avoid FK violation
            conn.execute(
                "UPDATE dup_groups SET recommended_id=NULL WHERE recommended_id=?", (fid,)
            )
            conn.execute("DELETE FROM dup_group_members WHERE file_id=?", (fid,))
            conn.execute("DELETE FROM files WHERE id=?", (fid,))

        # Remove dup_groups that now have 0 or 1 members
        conn.execute("""
            DELETE FROM dup_groups
            WHERE (SELECT COUNT(*) FROM dup_group_members
                   WHERE group_id = dup_groups.id) < 2
        """)

        # Clear has_dup_flag on files no longer in a group with >=2 members
        conn.execute("""
            UPDATE files SET has_dup_flag = 0
            WHERE has_dup_flag = 1
            AND id NOT IN (
                SELECT DISTINCT dgm.file_id
                FROM dup_group_members dgm
                JOIN dup_group_members dgm2
                    ON dgm2.group_id = dgm.group_id
                    AND dgm2.file_id != dgm.file_id
            )
        """)

    conn.close()
    return deleted, missing

