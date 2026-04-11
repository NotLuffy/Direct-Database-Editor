"""
CNC Direct Editor — IndexWorker.

Scans one or more folders, indexes files in-place (no copying, no renaming),
detects duplicates, scores each file, and writes [DUP] notes to the DB.

Phases:
  1. Walk   — collect all O-number file paths
  2. Analyze — hash + header + score each file (thread pool)
  3. Commit  — merge into DB, run 5 dedup passes, write scan log
"""

import os
import re
import time
import shutil
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import xxhash
from PyQt6.QtCore import QThread, pyqtSignal

import direct_database as db
from direct_scorer import score_file
from utils import parse_o_number
from verifier import check_o_range_title_only

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THREAD_WORKERS    = 12
PROGRESS_INTERVAL = 200

_O_NUMBER_RE = re.compile(r'^(O\d{4,6})(?:_(\d+))?', re.IGNORECASE)

_TITLE_RE    = re.compile(r'^O\d{4,6}\s*\(([^)]*)\)', re.IGNORECASE)
_INTERNAL_O_RE = re.compile(r'^(O\d{4,6})\b', re.IGNORECASE)
_GCODE_RE    = re.compile(r'\b[GM]\d+\b', re.IGNORECASE)
_EOP_RE      = re.compile(r'\bM0*(?:30|99|2)\b', re.IGNORECASE)  # M30 / M99 / M02
_DERIVED_RE = re.compile(
    r'^\s*\(\s*(?:MADE\s+FROM|CHANGED\s+FROM|COPY\s+OF|FROM)\s+(O\d{4,6}(?:_\d+)?)\s*\)',
    re.IGNORECASE
)

# Tags written by this scanner — stripped before each rescan
_AUTO_TAG_RE = re.compile(
    r'\[DUP:[^\]]*\]'
    r'|\[MISSING\][^\n]*'
    r'|\[ONUM MISMATCH\][^\n]*'
    r'|\[NO GCODE\][^\n]*'
    r'|\[NO EOP\][^\n]*'
    r'|\[RANGE CONFLICT\][^\n]*',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# File-level helpers (thread-safe, stateless)
# ---------------------------------------------------------------------------

def _is_o_number_file(filename: str) -> bool:
    """True if filename looks like an O-number file (O12345, O12345.nc, O12345_1.txt)."""
    base = os.path.splitext(filename)[0]
    # Ignore .bak files entirely
    if filename.lower().endswith(".bak"):
        return False
    return bool(_O_NUMBER_RE.match(base))


def _parse_filename(filename: str) -> tuple[str, Optional[str]]:
    """
    Return (o_number, o_suffix) from a filename.
    'O65123'   → ('O65123', None)
    'O65123_1' → ('O65123', '1')
    """
    base = os.path.splitext(filename)[0]
    m = _O_NUMBER_RE.match(base)
    if not m:
        return "", None
    return m.group(1).upper(), m.group(2) or None


def _extract_header_info(chunk: bytes) -> tuple[str, str, str, bool]:
    """Return (title, derived_from, internal_o_number, has_gcode).
    internal_o_number: the O#### on the first code line (e.g. 'O62579'), or ''.
    has_gcode: True if any G/M code word is found in the chunk.
    """
    title = ""
    derived_from = ""
    internal_o  = ""
    found_o_line = False
    text = chunk.decode("utf-8", errors="replace")
    has_gcode = bool(_GCODE_RE.search(text))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "%":
            continue
        if not found_o_line:
            om = _INTERNAL_O_RE.match(stripped)
            internal_o = om.group(1).upper() if om else ""
            m = _TITLE_RE.match(stripped)
            title = m.group(1).strip() if m else ""
            found_o_line = True
            continue
        m = _DERIVED_RE.match(stripped)
        if m:
            derived_from = m.group(1).upper()
            break
        if not stripped.startswith("("):
            break
    return title, derived_from, internal_o, has_gcode


def _count_lines(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


def _analyze_file(path: str) -> tuple[str, int, str, str, str, bool, bool]:
    """Hash + header info in one read.
    Returns (hash, line_count, title, derived_from, internal_o_number, has_gcode, has_eop).
    has_eop: True if any M30/M99/M02 end-of-program code is found in the file.
    """
    h = xxhash.xxh128()
    title = derived_from = internal_o = ""
    has_gcode    = False
    has_eop      = False
    header_found = False
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
                text = chunk.decode("utf-8", errors="replace")
                if not header_found:
                    title, derived_from, internal_o, has_gcode = _extract_header_info(chunk)
                    header_found = True
                elif not has_gcode:
                    has_gcode = bool(_GCODE_RE.search(text))
                if not has_eop:
                    has_eop = bool(_EOP_RE.search(text))
    except Exception:
        return "", 0, "", "", "", False, False
    return h.hexdigest(), _count_lines(path), title, derived_from, internal_o, has_gcode, has_eop


def _file_mtime_iso(path: str) -> str:
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return ""


def _strip_auto_tags(notes: str) -> str:
    """Remove all auto-generated [DUP:...] and [MISSING] tags from notes."""
    cleaned = _AUTO_TAG_RE.sub("", notes)
    # Collapse multiple blank lines left behind
    return re.sub(r'\n{3,}', '\n\n', cleaned).strip()


# ---------------------------------------------------------------------------
# Per-file worker (runs in thread pool)
# ---------------------------------------------------------------------------

def _process_one(path: str, source_folder: str,
                 db_path: str, existing: dict,
                 now_iso: str) -> Optional[dict]:
    """
    Analyze one file and return a complete DB record dict.
    existing: the current DB row for this path (dict), or None if new.
    """
    try:
        fname = os.path.basename(path)
        o_number, o_suffix = _parse_filename(fname)
        if not o_number:
            return None

        mtime = _file_mtime_iso(path)

        # Skip full re-analysis if hash is unchanged
        if existing and existing.get("last_modified") == mtime and existing.get("file_hash"):
            file_hash   = existing["file_hash"]
            line_count  = existing["line_count"]
            title       = existing["program_title"]
            derived     = existing["derived_from"]
            internal_o  = existing.get("internal_o_number", "")
            has_gcode   = not existing.get("no_gcode_flag", 0)
            has_eop     = not existing.get("no_eop_flag", 0)
        else:
            file_hash, line_count, title, derived, internal_o, has_gcode, has_eop = _analyze_file(path)

        try:
            score, vstatus = score_file(path, title, o_number=o_number)
        except Exception:
            score, vstatus = 0, ""

        # ── Safeguard 1: filename O-number ≠ internal O-number ─────────────
        has_onum_mismatch = 1 if (internal_o and internal_o != o_number.upper()) else 0

        # ── Safeguard 2: file contains no G or M codes ──────────────────────
        no_gcode_flag = 0 if has_gcode else 1

        # ── Safeguard 3: no end-of-program code (M30 / M99 / M02) ──────────
        no_eop_flag = 1 if (has_gcode and not has_eop) else 0

        # ── Safeguard 4: title round size doesn't match O-number range ──────
        try:
            range_ok, range_msg = check_o_range_title_only(title, o_number)
            has_range_conflict = 0 if range_ok else 1
        except Exception:
            range_msg = ""
            has_range_conflict = 0

        # Preserve user-written notes; strip old auto-tags (re-added in dedup phase)
        base_notes = _strip_auto_tags(existing.get("notes", "") if existing else "")

        # Auto-tag notes for persistent issues
        if has_onum_mismatch and "[ONUM MISMATCH]" not in base_notes:
            base_notes = (
                f"[ONUM MISMATCH] Filename={o_number} but file says {internal_o}. "
                f"Machine will run {internal_o}.\n" + base_notes
            ).strip()
        if no_gcode_flag and "[NO GCODE]" not in base_notes:
            base_notes = (
                "[NO GCODE] No G/M codes found — may not be a valid program.\n"
                + base_notes
            ).strip()
        if no_eop_flag and "[NO EOP]" not in base_notes:
            base_notes = (
                "[NO EOP] No M30/M99/M02 end-of-program found — file may be truncated.\n"
                + base_notes
            ).strip()
        if has_range_conflict and "[RANGE CONFLICT]" not in base_notes:
            base_notes = (
                f"[RANGE CONFLICT] {range_msg}\n" + base_notes
            ).strip()

        return {
            "file_path":         path,
            "file_name":         fname,
            "o_number":          o_number,
            "o_suffix":          o_suffix,
            "file_hash":         file_hash,
            "line_count":        line_count,
            "program_title":     title,
            "derived_from":      derived,
            "source_folder":     source_folder,
            "status":            existing.get("status", "active") if existing else "active",
            "verify_status":     vstatus,
            "verify_score":      score,
            "has_dup_flag":      0,          # reset; dedup phase will re-set
            "has_onum_mismatch": has_onum_mismatch,
            "no_gcode_flag":     no_gcode_flag,
            "internal_o_number": internal_o,
            "no_eop_flag":       no_eop_flag,
            "has_range_conflict": has_range_conflict,
            "notes":             base_notes,
            "last_seen":         now_iso,
            "last_modified":     mtime,
            "index_date":        existing.get("index_date", now_iso) if existing else now_iso,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

def _recommended_keep(members: list) -> dict:
    """
    Pick the best file from a list of record dicts.
    Tie-break: score DESC → no o_suffix first → lowest numeric suffix → path alpha.
    """
    def key(r):
        suf = r.get("o_suffix")
        return (
            -r.get("verify_score", 0),
            0 if suf is None else 1,
            int(suf) if suf and suf.isdigit() else 9999,
            r.get("file_path", ""),
        )
    return sorted(members, key=key)[0]


def _append_dup_note(record: dict, tag: str) -> dict:
    notes = record.get("notes", "")
    record["notes"] = (notes + "\n" + tag).strip() if notes else tag
    record["has_dup_flag"] = 1
    return record


# ---------------------------------------------------------------------------
# Dedup passes (operate on in-memory record list; IDs from DB after upsert)
# ---------------------------------------------------------------------------

def _run_dedup_passes(records_by_path: dict, db_path: str,
                      db_conn, now_iso: str) -> int:
    """
    Run 5 duplicate detection passes over the indexed records.
    Writes [DUP:...] notes and inserts dup_group rows.
    Returns total number of dup groups created.
    """
    # Build lookups
    path_to_id = {}
    for row in db_conn.execute("SELECT id, file_path FROM files").fetchall():
        path_to_id[row["file_path"]] = row["id"]

    def _file_id(path):
        return path_to_id.get(path)

    total_groups = 0

    # --- Pass 1: Exact duplicates (same hash) ---
    from collections import defaultdict
    hash_groups = defaultdict(list)
    for r in records_by_path.values():
        h = r.get("file_hash")
        if h:
            hash_groups[h].append(r)

    for h, members in hash_groups.items():
        if len(members) < 2:
            continue
        rec_keep = _recommended_keep(members)
        rec_name = rec_keep["file_name"]
        rec_score = rec_keep["verify_score"]
        rec_id = _file_id(rec_keep["file_path"])
        member_ids = []
        member_scores = {}
        for m in members:
            mid = _file_id(m["file_path"])
            if mid is None:
                continue
            member_ids.append(mid)
            member_scores[mid] = m["verify_score"]
            if m["file_path"] == rec_keep["file_path"]:
                tag = (f"[DUP:exact] {len(members)-1} identical "
                       f"cop{'ies' if len(members)>2 else 'y'} found. "
                       f"This file is the recommended keep (score {rec_score}/7).")
            else:
                tag = (f"[DUP:exact] Same content as {rec_name} "
                       f"(score {rec_score}/7). Recommended keep: {rec_keep['file_path']}")
            _append_dup_note(m, tag)
            db_conn.execute(
                "UPDATE files SET notes=?, has_dup_flag=1 WHERE file_path=?",
                (m["notes"], m["file_path"]))
        if member_ids:
            db.insert_dup_group(db_path, db_conn, "exact", None, h,
                                rec_id, member_ids, member_scores, now_iso)
            total_groups += 1

    # --- Pass 2: Name conflicts (same o_number, different hash, no suffix) ---
    o_groups = defaultdict(list)
    for r in records_by_path.values():
        if r.get("o_suffix") is None:
            o_groups[r["o_number"]].append(r)

    for onum, members in o_groups.items():
        # Filter to groups where hashes actually differ
        hashes = {m["file_hash"] for m in members if m.get("file_hash")}
        if len(hashes) < 2:
            continue
        rec_keep = _recommended_keep(members)
        rec_id = _file_id(rec_keep["file_path"])
        member_ids = []
        member_scores = {}
        for m in members:
            mid = _file_id(m["file_path"])
            if mid is None:
                continue
            member_ids.append(mid)
            member_scores[mid] = m["verify_score"]
            if m["file_path"] == rec_keep["file_path"]:
                tag = (f"[DUP:conflict] {len(members)-1} file(s) share O-number {onum} "
                       f"with different content. This is the recommended keep "
                       f"(score {rec_keep['verify_score']}/7).")
            else:
                tag = (f"[DUP:conflict] Same O-number {onum}, different content. "
                       f"Score: {m['verify_score']}/7. "
                       f"Recommended keep: {rec_keep['file_path']} "
                       f"(score {rec_keep['verify_score']}/7).")
            _append_dup_note(m, tag)
            db_conn.execute(
                "UPDATE files SET notes=?, has_dup_flag=1 WHERE file_path=?",
                (m["notes"], m["file_path"]))
        if member_ids:
            db.insert_dup_group(db_path, db_conn, "name_conflict", onum, None,
                                rec_id, member_ids, member_scores, now_iso)
            total_groups += 1

    # --- Pass 3: Backup chains (O#####_N alongside base O#####) ---
    # Group by base o_number regardless of suffix
    base_groups = defaultdict(list)
    for r in records_by_path.values():
        base_groups[r["o_number"]].append(r)

    for onum, members in base_groups.items():
        has_base    = any(m["o_suffix"] is None for m in members)
        has_suffixed = any(m["o_suffix"] is not None for m in members)
        if not (has_base and has_suffixed):
            continue
        if len(members) < 2:
            continue
        rec_keep = _recommended_keep(members)
        rec_id = _file_id(rec_keep["file_path"])
        member_ids = []
        member_scores = {}
        for m in members:
            mid = _file_id(m["file_path"])
            if mid is None:
                continue
            member_ids.append(mid)
            member_scores[mid] = m["verify_score"]
            if m["file_path"] == rec_keep["file_path"]:
                tag = (f"[DUP:backup_chain] Backup chain for {onum}. "
                       f"{len(members)} files total. "
                       f"This is the recommended keep (score {m['verify_score']}/7).")
            else:
                tag = (f"[DUP:backup_chain] Backup chain for {onum}. "
                       f"{len(members)} files total. "
                       f"Recommended keep: {rec_keep['file_path']} "
                       f"(score {rec_keep['verify_score']}/7). "
                       f"Your score: {m['verify_score']}/7.")
            _append_dup_note(m, tag)
            db_conn.execute(
                "UPDATE files SET notes=?, has_dup_flag=1 WHERE file_path=?",
                (m["notes"], m["file_path"]))
        if member_ids:
            db.insert_dup_group(db_path, db_conn, "backup_chain", onum, None,
                                rec_id, member_ids, member_scores, now_iso)
            total_groups += 1

    # --- Pass 4: Derived copies (MADE FROM / CHANGED FROM header comment) ---
    o_number_to_records = defaultdict(list)
    for r in records_by_path.values():
        o_number_to_records[r["o_number"]].append(r)

    for r in records_by_path.values():
        derived = r.get("derived_from", "")
        if not derived:
            continue
        base_onum = derived.split("_")[0].upper()
        originals = o_number_to_records.get(base_onum, [])
        if not originals:
            continue
        orig = _recommended_keep(originals)
        orig_id = _file_id(orig["file_path"])
        derived_id = _file_id(r["file_path"])
        if derived_id is None:
            continue
        tag = (f"[DUP:derived] Derived from {derived}. "
               f"Original: {orig['file_path']} "
               f"(score {orig['verify_score']}/7). "
               f"This file score: {r['verify_score']}/7.")
        _append_dup_note(r, tag)
        db_conn.execute(
            "UPDATE files SET notes=?, has_dup_flag=1 WHERE file_path=?",
            (r["notes"], r["file_path"]))
        member_ids = [derived_id]
        member_scores = {derived_id: r["verify_score"]}
        if orig_id and orig_id != derived_id:
            member_ids.append(orig_id)
            member_scores[orig_id] = orig["verify_score"]
        db.insert_dup_group(db_path, db_conn, "derived", base_onum, None,
                            orig_id, member_ids, member_scores, now_iso)
        total_groups += 1

    # --- Pass 5: Title matches (same non-empty title, different o_numbers) ---
    title_groups = defaultdict(list)
    for r in records_by_path.values():
        t = (r.get("program_title") or "").strip()
        if t:
            title_groups[t].append(r)

    for title, members in title_groups.items():
        o_numbers = {m["o_number"] for m in members}
        if len(o_numbers) < 2:
            continue
        rec_keep = _recommended_keep(members)
        rec_id = _file_id(rec_keep["file_path"])
        member_ids = []
        member_scores = {}
        for m in members:
            mid = _file_id(m["file_path"])
            if mid is None:
                continue
            member_ids.append(mid)
            member_scores[mid] = m["verify_score"]
            if m["file_path"] != rec_keep["file_path"]:
                tag = (f"[DUP:title_match] Same program title as {rec_keep['o_number']} "
                       f"({rec_keep['file_path']}). "
                       f"Scores: this={m['verify_score']}/7, "
                       f"other={rec_keep['verify_score']}/7.")
                _append_dup_note(m, tag)
                db_conn.execute(
                    "UPDATE files SET notes=?, has_dup_flag=1 WHERE file_path=?",
                    (m["notes"], m["file_path"]))
        if len(member_ids) >= 2:
            db.insert_dup_group(db_path, db_conn, "title_match", None, None,
                                rec_id, member_ids, member_scores, now_iso)
            total_groups += 1

    return total_groups


# ---------------------------------------------------------------------------
# IndexWorker QThread
# ---------------------------------------------------------------------------

class IndexWorker(QThread):
    """
    Background thread that indexes a list of folders.

    Signals:
        progress(current, total, message)
            current=-1, total=-1 → indeterminate (pulsing bar)
        finished(found, new_files, changed, removed, dup_groups)
        error(message)
    """

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int, int, int, int)
    error    = pyqtSignal(str)

    def __init__(self, db_path: str, folder_paths: list[str], parent=None):
        super().__init__(parent)
        self.db_path      = db_path
        self.folder_paths = folder_paths
        self._cancelled   = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        t_start = time.time()
        now_iso = datetime.datetime.now().isoformat()

        try:
            db.init_schema(self.db_path)

            # ── Phase 1: Walk folders ──────────────────────────────────────
            self.progress.emit(0, 0, "Scanning folders…")
            all_paths   = []   # (file_path, source_folder)
            for folder in self.folder_paths:
                for root, dirs, files in os.walk(folder):
                    # Skip hidden dirs and our own DB
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fname in files:
                        if self._cancelled:
                            return
                        if _is_o_number_file(fname):
                            all_paths.append(
                                (os.path.normpath(os.path.join(root, fname)), folder))

            total = len(all_paths)
            self.progress.emit(0, total, f"Found {total:,} files — analyzing…")

            # ── Phase 2: Analyze (thread pool) ────────────────────────────
            existing_map = db.get_all_paths(self.db_path)  # {path: row_dict}
            results      = {}   # path → record dict

            with ThreadPoolExecutor(max_workers=THREAD_WORKERS) as pool:
                futures = {
                    pool.submit(_process_one, path, src_folder,
                                self.db_path, existing_map.get(path), now_iso): path
                    for path, src_folder in all_paths
                }
                done = 0
                for fut in as_completed(futures):
                    if self._cancelled:
                        return
                    path = futures[fut]
                    rec  = fut.result()
                    if rec:
                        results[path] = rec
                    done += 1
                    if done % PROGRESS_INTERVAL == 0 or done == total:
                        eta = ""
                        elapsed = time.time() - t_start
                        if done > 0 and elapsed > 1:
                            rate = done / elapsed
                            remaining = (total - done) / rate
                            eta = f"  ETA {int(remaining)}s"
                        self.progress.emit(done, total,
                            f"Analyzed {done:,}/{total:,}{eta}")

            # ── Phase 3: Commit ───────────────────────────────────────────
            self.progress.emit(-1, -1, "Committing to database…")

            found_paths    = set(results.keys())
            existing_paths = set(existing_map.keys())
            new_paths      = found_paths - existing_paths
            removed_paths  = existing_paths - found_paths
            changed_paths  = {
                p for p in found_paths & existing_paths
                if results[p].get("file_hash") != existing_map[p].get("file_hash")
            }

            # Only mark a path as missing if it belongs to one of the current
            # scan folders — records from OTHER locations must never be touched.
            scan_norm = {
                os.path.normcase(os.path.normpath(f))
                for f in self.folder_paths
            }
            scoped_removed = [
                path for path in removed_paths
                if os.path.normcase(os.path.normpath(
                    existing_map[path].get("source_folder") or ""))
                   in scan_norm
            ]

            conn = db.get_connection(self.db_path)
            with conn:
                # Clear old dup data — will be regenerated
                db.clear_dup_data(self.db_path, conn)

                # Upsert all found files
                for rec in results.values():
                    db.upsert_file(self.db_path, conn, rec)

                # Mark missing — only files that belong to the active folders
                for path in scoped_removed:
                    row = existing_map[path]
                    db.mark_file_missing(self.db_path, row["id"], now_iso, conn)

            # Re-open after commit for dedup (needs IDs to be committed)
            self.progress.emit(-1, -1, "Detecting duplicates…")
            conn2 = db.get_connection(self.db_path)
            with conn2:
                dup_count = _run_dedup_passes(results, self.db_path, conn2, now_iso)

            # ── Copy new files to New Programs folder ─────────────────────
            new_progs_folder = db.get_setting(
                self.db_path, "new_programs_folder", "")
            if new_progs_folder and os.path.isdir(new_progs_folder) and new_paths:
                self.progress.emit(-1, -1, "Copying new files to New Programs folder…")
                for path in new_paths:
                    try:
                        dest = os.path.join(new_progs_folder,
                                            os.path.basename(path))
                        if not os.path.exists(dest):
                            shutil.copy2(path, dest)
                    except Exception:
                        pass

            # ── Scan log ──────────────────────────────────────────────────
            duration = time.time() - t_start
            db.insert_scan_log(self.db_path, {
                "scan_date":    now_iso,
                "folder_paths": "|".join(self.folder_paths),
                "files_found":  len(found_paths),
                "files_new":    len(new_paths),
                "files_removed": len(removed_paths),
                "files_changed": len(changed_paths),
                "dup_groups":   dup_count,
                "duration_sec": round(duration, 2),
            })
            db.set_setting(self.db_path, "last_scan_date", now_iso)
            db.set_setting(self.db_path, "last_scan_folders",
                           "|".join(self.folder_paths))

            self.finished.emit(
                len(found_paths),
                len(new_paths),
                len(changed_paths),
                len(removed_paths),
                dup_count,
            )

        except Exception as exc:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# ImportNewWorker — import only new files; surface O-number conflicts
# ---------------------------------------------------------------------------

class ImportNewWorker(QThread):
    """
    Scans a single folder for O-number files and classifies each one:

      - Already indexed (path in DB)           → skipped
      - Hash known (content already in DB)     → skipped (copy of known program)
      - O-number NOT in DB                     → auto-imported
      - O-number in DB, same/blank title       → skipped (same program, different path)
      - O-number in DB, DIFFERENT title        → conflict (shown to user for review)

    Signals:
        progress(current, total, message)
        finished(imported, skipped, conflicts)
            imported  = count of auto-imported records
            skipped   = count silently skipped
            conflicts = list of dicts:
                {
                  "path":           str  (full path to the new file),
                  "o_number":       str  (O-number from filename),
                  "new_title":      str  (title in the new file),
                  "existing_title": str  (title in the DB record),
                  "existing_path":  str  (path of the existing DB record),
                }
        error(message)
    """

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int, object, object)  # imported, skipped, conflicts, imported_names
    error    = pyqtSignal(str)

    def __init__(self, db_path: str, folder: str, parent=None):
        super().__init__(parent)
        self.db_path    = db_path
        self.folder     = folder
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        now_iso = datetime.datetime.now().isoformat()
        try:
            # ── Phase 1: Walk folder ─────────────────────────────────────
            self.progress.emit(0, 0, "Walking folder…")
            all_paths = []
            for root, dirs, files in os.walk(self.folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    if self._cancelled:
                        return
                    if _is_o_number_file(fname):
                        all_paths.append(os.path.normpath(os.path.join(root, fname)))

            total = len(all_paths)
            if total == 0:
                self.finished.emit(0, 0, [])
                return

            self.progress.emit(0, total, f"Found {total:,} files — checking…")

            # ── Phase 2: Load DB state ───────────────────────────────────
            conn = db.get_connection(self.db_path)
            known_hashes = {
                row[0] for row in
                conn.execute(
                    "SELECT file_hash FROM files WHERE file_hash IS NOT NULL"
                ).fetchall()
            }
            known_paths = {
                row[0] for row in
                conn.execute("SELECT file_path FROM files").fetchall()
            }
            # o_number → {title, file_path, id} for conflict detection
            o_map = {
                row["o_number"].upper(): {
                    "title": (row["program_title"] or "").strip(),
                    "path":  row["file_path"],
                    "id":    row["id"],
                }
                for row in conn.execute(
                    "SELECT id, o_number, program_title, file_path FROM files"
                ).fetchall()
            }
            conn.close()

            # ── Phase 3: Classify ────────────────────────────────────────
            to_import  = []   # paths — brand-new O-numbers
            conflicts  = []   # dicts for user review
            skipped    = 0
            # Track O-numbers already queued in this batch so that two files
            # with the same O-number (e.g. O12345 and O12345.nc) in the same
            # import folder are flagged as conflicts with each other, not both
            # silently imported.
            batch_o_map: dict[str, dict] = {}  # o_upper → {path, title, id: None}

            done = 0
            for path in all_paths:
                if self._cancelled:
                    return

                fname    = os.path.basename(path)
                o_number, _ = _parse_filename(fname)
                o_upper  = o_number.upper() if o_number else ""

                if path in known_paths:
                    skipped += 1
                    done += 1
                    if done % PROGRESS_INTERVAL == 0 or done == total:
                        self.progress.emit(done, total, f"Checking {done:,}/{total:,}…")
                    continue

                # Hash the file
                try:
                    h = xxhash.xxh128()
                    with open(path, "rb") as f:
                        while chunk := f.read(1024 * 1024):
                            h.update(chunk)
                    file_hash = h.hexdigest()
                except Exception:
                    skipped += 1
                    done += 1
                    continue

                if file_hash in known_hashes:
                    # Content already known — it's a copy of an existing program
                    skipped += 1
                    done += 1
                    if done % PROGRESS_INTERVAL == 0 or done == total:
                        self.progress.emit(done, total, f"Checking {done:,}/{total:,}…")
                    continue

                # Check against DB first, then against same-batch queue.
                # Extension is irrelevant: O12345.nc and O12345 share o_upper.
                existing_source = o_map.get(o_upper) or batch_o_map.get(o_upper)

                if existing_source is None:
                    # Completely new O-number — queue for import and reserve slot
                    to_import.append(path)
                    batch_o_map[o_upper] = {
                        "title": "",       # filled in Phase 4; unknown here
                        "path":  path,
                        "id":    None,     # not yet in DB
                    }
                    done += 1
                    if done % PROGRESS_INTERVAL == 0 or done == total:
                        self.progress.emit(done, total, f"Checking {done:,}/{total:,}…")
                    continue

                # O-number exists (in DB or same batch) — check if the title is different.
                # Invert the skip logic: ONLY skip when both titles are readable
                # AND identical.  If either is blank (title unreadable) we flag
                # for review rather than silently discarding a real conflict.
                _, __, new_title, *_rest = _analyze_file(path)
                new_title      = new_title.strip()
                existing_title = existing_source["title"]

                # If the DB has no title for the existing file, try reading it
                # from disk so the conflict dialog shows something useful.
                if not existing_title and existing_source.get("path"):
                    try:
                        _, __, ex_disk_title, *_ = _analyze_file(
                            existing_source["path"])
                        existing_title = ex_disk_title.strip()
                    except Exception:
                        pass

                if (new_title and existing_title
                        and new_title.upper() == existing_title.upper()):
                    # Confirmed same program at a different path — skip
                    skipped += 1
                else:
                    conflicts.append({
                        "path":           path,
                        "o_number":       o_upper,
                        "new_title":      new_title or "(title unreadable)",
                        "existing_title": existing_title or "(no title in DB)",
                        "existing_path":  existing_source["path"],
                        "existing_id":    existing_source["id"],  # None if same-batch
                    })

                done += 1
                if done % PROGRESS_INTERVAL == 0 or done == total:
                    self.progress.emit(done, total, f"Checking {done:,}/{total:,}…")

            if not to_import:
                self.finished.emit(0, skipped, conflicts, [])
                return

            # ── Phase 4: Full analysis of new files ──────────────────────
            self.progress.emit(0, len(to_import),
                               f"Analyzing {len(to_import):,} new files…")
            results = {}
            with ThreadPoolExecutor(max_workers=THREAD_WORKERS) as pool:
                futures = {
                    pool.submit(_process_one, path, self.folder,
                                self.db_path, None, now_iso): path
                    for path in to_import
                }
                done2 = 0
                for fut in as_completed(futures):
                    if self._cancelled:
                        return
                    path = futures[fut]
                    rec  = fut.result()
                    if rec:
                        results[path] = rec
                    done2 += 1
                    if done2 % PROGRESS_INTERVAL == 0 or done2 == len(to_import):
                        self.progress.emit(done2, len(to_import),
                                           f"Analyzed {done2:,}/{len(to_import):,}…")

            # ── Phase 5: Commit auto-imports ─────────────────────────────
            self.progress.emit(-1, -1, "Saving to database…")
            conn2 = db.get_connection(self.db_path)
            with conn2:
                for rec in results.values():
                    db.upsert_file(self.db_path, conn2, rec)
            conn2.close()

            self.progress.emit(-1, -1, "Checking for duplicates…")
            conn3 = db.get_connection(self.db_path)
            with conn3:
                _run_dedup_passes(results, self.db_path, conn3, now_iso)
            conn3.close()

            imported_names = sorted(os.path.basename(p) for p in results)
            self.finished.emit(len(results), skipped, conflicts, imported_names)

        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# commit_renamed_import — called from UI after user resolves conflicts
# ---------------------------------------------------------------------------

def commit_renamed_import(db_path: str, path: str, new_o_number: str,
                           source_folder: str) -> bool:
    """
    Rename file on disk to new_o_number (preserving extension), then insert
    into DB.  Returns True on success.
    """
    try:
        ext      = os.path.splitext(path)[1]
        new_name = new_o_number.upper() + ext
        new_path = os.path.join(os.path.dirname(path), new_name)
        if os.path.exists(new_path):
            return False  # collision — caller should warn
        os.rename(path, new_path)
        now_iso = datetime.datetime.now().isoformat()
        rec = _process_one(new_path, source_folder, db_path, None, now_iso)
        if rec is None:
            return False
        conn = db.get_connection(db_path)
        with conn:
            db.upsert_file(db_path, conn, rec)
        conn.close()
        return True
    except Exception:
        return False


def commit_renamed_existing(db_path: str, existing_id: int, new_o_number: str,
                             new_file_path: str, source_folder: str) -> bool:
    """
    Rename the existing DB file to new_o_number on disk + update its DB record,
    then import the new incoming file under its original O-number.
    Returns True on success.
    """
    try:
        conn = db.get_connection(db_path)
        row = conn.execute("SELECT * FROM files WHERE id=?", (existing_id,)).fetchone()
        conn.close()
        if row is None:
            return False

        old_path = row["file_path"]
        ext      = os.path.splitext(old_path)[1]
        folder   = os.path.dirname(old_path)
        new_name = new_o_number.upper() + ext
        new_path = os.path.join(folder, new_name)
        if os.path.exists(new_path):
            return False

        os.rename(old_path, new_path)

        # Re-analyse both files
        now_iso = datetime.datetime.now().isoformat()
        rec_existing = _process_one(new_path, source_folder, db_path, None, now_iso)
        rec_new      = _process_one(new_file_path, source_folder, db_path, None, now_iso)
        if rec_existing is None or rec_new is None:
            return False

        conn2 = db.get_connection(db_path)
        with conn2:
            # Remove old record (it will be re-inserted under the new path)
            conn2.execute("DELETE FROM files WHERE id=?", (existing_id,))
            db.upsert_file(db_path, conn2, rec_existing)
            db.upsert_file(db_path, conn2, rec_new)
        conn2.close()
        return True
    except Exception:
        return False
