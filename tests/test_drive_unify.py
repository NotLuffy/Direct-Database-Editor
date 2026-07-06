"""
test_drive_unify.py — drive-letter unification (Google Drive G:/P:/Q: remounts).

Verifies that unify_drive_letters:
  - re-points records whose only copy is on an old drive letter
  - merges old-letter duplicates into the current-letter record, preserving
    status, notes, earliest index_date and revision history
  - never touches records outside the scan folders

Run directly:  python tests/test_drive_unify.py
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import direct_database as db

_FAILS = []


def _check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _FAILS.append(label)


def _rec(path, folder, status="active", notes="", index_date="2026-01-01"):
    name = os.path.basename(path)
    return {
        "file_path": path, "file_name": name, "o_number": name.split(".")[0].upper(),
        "o_suffix": None, "file_hash": "h" + name, "line_count": 10,
        "program_title": f"TITLE {name}", "derived_from": "", "source_folder": folder,
        "status": status, "verify_status": "", "verify_score": 0, "has_dup_flag": 0,
        "notes": notes, "last_seen": "2026-01-01", "last_modified": "2026-01-01",
        "index_date": index_date,
    }


def _main():
    global _FAILS
    _FAILS = []
    tmp = tempfile.mkdtemp()
    try:
        dbp = os.path.join(tmp, "t.db")
        db.init_schema(dbp)
        conn = db.get_connection(dbp)
        with conn:
            # Same file on old G: and current P: — must merge (P: wins, G: data kept)
            db.upsert_file(dbp, conn, _rec(r"G:\My Drive\repo\O1.nc", r"G:\My Drive\repo",
                                           status="flagged", notes="old note",
                                           index_date="2025-01-01"))
            db.upsert_file(dbp, conn, _rec(r"P:\My Drive\repo\O1.nc", r"P:\My Drive\repo",
                                           index_date="2026-02-02"))
            # Only on old G: — must be re-pointed to P:
            db.upsert_file(dbp, conn, _rec(r"G:\My Drive\repo\sub\O2.nc",
                                           r"G:\My Drive\repo"))
            # Unrelated folder — must never be touched
            db.upsert_file(dbp, conn, _rec(r"D:\Other\O3.nc", r"D:\Other"))
            # Revision attached to the G: duplicate — must follow the merge
            gid = conn.execute("SELECT id FROM files WHERE file_path=?",
                               (r"G:\My Drive\repo\O1.nc",)).fetchone()["id"]
            conn.execute(
                "INSERT INTO file_revisions (file_id, label, backup_path, created_at) "
                "VALUES (?, 'Rev A', 'x', '2025-06-01')", (gid,))

        stats = db.unify_drive_letters(dbp, [r"P:\My Drive\repo"])
        _check("stats: 1 merged, 1 repointed",
               stats == {"repointed": 1, "merged": 1}, f"got {stats}")

        conn = db.get_connection(dbp)
        paths = sorted(r["file_path"] for r in
                       conn.execute("SELECT file_path FROM files").fetchall())
        _check("no G: records remain",
               paths == [r"D:\Other\O3.nc", r"P:\My Drive\repo\O1.nc",
                         r"P:\My Drive\repo\sub\O2.nc"], f"got {paths}")

        keeper = conn.execute("SELECT * FROM files WHERE file_path=?",
                              (r"P:\My Drive\repo\O1.nc",)).fetchone()
        _check("merge keeps donor status", keeper["status"] == "flagged",
               f"got {keeper['status']}")
        _check("merge keeps donor notes", "old note" in (keeper["notes"] or ""),
               f"got {keeper['notes']!r}")
        _check("merge keeps earliest index_date",
               keeper["index_date"] == "2025-01-01", f"got {keeper['index_date']}")

        rev = conn.execute("SELECT file_id FROM file_revisions").fetchone()
        _check("revision re-pointed to keeper", rev["file_id"] == keeper["id"],
               f"rev file_id {rev['file_id']} vs keeper {keeper['id']}")

        o2 = conn.execute("SELECT source_folder FROM files WHERE file_path=?",
                          (r"P:\My Drive\repo\sub\O2.nc",)).fetchone()
        _check("repointed source_folder updated",
               o2["source_folder"] == r"P:\My Drive\repo", f"got {o2['source_folder']}")

        # Idempotent: second run does nothing
        stats2 = db.unify_drive_letters(dbp, [r"P:\My Drive\repo"])
        _check("second run is a no-op",
               stats2 == {"repointed": 0, "merged": 0}, f"got {stats2}")
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("RESULT:", "PASS" if not _FAILS else f"FAIL ({_FAILS})")
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    raise SystemExit(_main())
