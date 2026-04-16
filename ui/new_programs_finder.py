"""
CNC Direct Editor — New Programs Finder.

Finds files in New Programs subfolders whose O-number is not in
Repository and copies them to New Programs\\new.

O-number comparison is extension-agnostic:
  O10101.txt, O10101.NC, O10101  →  all treated as "O10101"
"""

import os
import re
import shutil

from PyQt6.QtCore import QThread, pyqtSignal

_O_RE = re.compile(r'^(O\d{4,6})(?:[_-]\d+)?', re.IGNORECASE)

REPO_PATH      = r"N:\My Drive\Repository Share\repository"
NEW_PROGS_PATH = r"N:\My Drive\Repository Share\New Programs"


def extract_o(filename: str) -> str | None:
    """Return normalised O-number from a filename, ignoring extension."""
    base = os.path.splitext(filename)[0]
    m = _O_RE.match(base)
    return m.group(1).upper() if m else None


def _collect_o_numbers(folder: str) -> set[str]:
    """Walk *folder* and return the set of all O-numbers found in filenames."""
    result: set[str] = set()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            o = extract_o(f)
            if o:
                result.add(o)
    return result


def _get_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _consider(candidates: dict[str, tuple[str, float]],
               root: str, fname: str, known: set[str]) -> None:
    """Add *fname* to candidates if its O-number is new and not already known."""
    o = extract_o(fname)
    if not o or o in known:
        return
    path = os.path.normpath(os.path.join(root, fname))
    mtime = _get_mtime(path)
    if o not in candidates or mtime > candidates[o][1]:
        candidates[o] = (path, mtime)


def _find_candidates(new_progs: str, skip_folder: str,
                     known: set[str]) -> dict[str, tuple[str, float]]:
    """
    Walk New Programs subfolders (excluding *skip_folder*) and return a
    dict of  o_number → (file_path, mtime)  for every file whose O-number
    is not in *known*.  When the same O-number appears in multiple places
    the most-recently-modified file wins.
    """
    skip_low = os.path.normpath(skip_folder).lower()
    candidates: dict[str, tuple[str, float]] = {}

    for root, dirs, files in os.walk(new_progs):
        dirs[:] = [
            d for d in dirs
            if os.path.normpath(os.path.join(root, d)).lower() != skip_low
            and not d.startswith(".")
        ]
        for f in files:
            _consider(candidates, root, f, known)

    return candidates


def _copy_candidates(candidates: dict[str, tuple[str, float]],
                     dest_folder: str) -> tuple[int, int, list[str]]:
    """Copy candidate files to *dest_folder*. Returns (copied, skipped, errors)."""
    copied  = 0
    skipped = 0
    errors: list[str] = []

    for _o, (src, _) in sorted(candidates.items()):
        dest = os.path.join(dest_folder, os.path.basename(src))
        if os.path.exists(dest):
            skipped += 1
            continue
        try:
            shutil.copy2(src, dest)
            copied += 1
        except OSError as exc:
            errors.append(f"{os.path.basename(src)}: {exc}")

    return copied, skipped, errors


class NewProgsFinder(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int, list)   # copied, skipped, errors

    def __init__(self, repo: str = REPO_PATH,
                 new_progs: str = NEW_PROGS_PATH, parent=None):
        super().__init__(parent)
        self.repo      = repo
        self.new_progs = new_progs

    def run(self):
        try:
            new_folder = os.path.normpath(os.path.join(self.new_progs, "new"))
            os.makedirs(new_folder, exist_ok=True)

            self.progress.emit("Scanning Repository…")
            known = _collect_o_numbers(self.repo)
            known |= _collect_o_numbers(new_folder)
            self.progress.emit(f"{len(known):,} O-numbers already accounted for.")

            self.progress.emit("Scanning New Programs subfolders…")
            candidates = _find_candidates(self.new_progs, new_folder, known)
            self.progress.emit(f"{len(candidates):,} new file(s) to copy.")

            copied, skipped, errors = _copy_candidates(candidates, new_folder)
            self.finished.emit(copied, skipped, errors)
        except Exception as exc:
            import logging
            logging.exception("NewProgsFinder worker crashed")
            self.finished.emit(0, 0, [str(exc)])
