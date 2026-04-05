"""
CNC Direct Editor — New Programs Finder.

Scans the Repository folder and New Programs subfolders. Any file in
New Programs whose O-number is not already present in the Repository
(regardless of extension) is copied to  New Programs\\new.

O-number comparison is extension-agnostic:
  O10101.txt, O10101.NC, O10101  →  all treated as "O10101"
"""

import os
import re
import shutil

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFileDialog, QProgressBar, QMessageBox,
)
from PyQt6.QtCore import QThread, pyqtSignal

_O_RE = re.compile(r'^(O\d{4,6})(?:[_-]\d+)?', re.IGNORECASE)

_DEFAULT_REPO      = r"N:\My Drive\Repository Share\repository"
_DEFAULT_NEW_PROGS = r"N:\My Drive\Repository Share\New Programs"


def _extract_o(filename: str) -> str | None:
    """Return normalised O-number from a filename, ignoring extension."""
    base = os.path.splitext(filename)[0]
    m = _O_RE.match(base)
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _FinderWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int, list)   # copied, skipped, errors

    def __init__(self, repo_path: str, new_progs_path: str, parent=None):
        super().__init__(parent)
        self.repo_path      = repo_path
        self.new_progs_path = new_progs_path

    def run(self):
        new_folder      = os.path.normpath(os.path.join(self.new_progs_path, "new"))
        new_folder_low  = new_folder.lower()
        os.makedirs(new_folder, exist_ok=True)

        # ── Step 1: Known O-numbers in Repository ────────────────────────
        self.progress.emit("Scanning Repository…")
        known: set[str] = set()
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                o = _extract_o(fname)
                if o:
                    known.add(o)
        self.progress.emit(f"  {len(known):,} O-numbers found in Repository.")

        # ── Step 2: O-numbers already in New Programs/new ────────────────
        already_new: set[str] = set()
        if os.path.isdir(new_folder):
            for fname in os.listdir(new_folder):
                o = _extract_o(fname)
                if o:
                    already_new.add(o)
        if already_new:
            self.progress.emit(
                f"  {len(already_new):,} O-numbers already in 'new' folder — will skip.")

        # ── Step 3: Walk New Programs subfolders (skip /new) ─────────────
        self.progress.emit("Scanning New Programs subfolders…")
        # o_number → (file_path, mtime) — keep most-recently-modified per O-number
        candidates: dict[str, tuple[str, float]] = {}

        for root, dirs, files in os.walk(self.new_progs_path):
            # Exclude the output folder itself from source scanning
            dirs[:] = [
                d for d in dirs
                if os.path.normpath(os.path.join(root, d)).lower() != new_folder_low
                and not d.startswith(".")
            ]
            for fname in files:
                o = _extract_o(fname)
                if not o:
                    continue
                if o in known:
                    continue        # already in repository
                if o in already_new:
                    continue        # already copied to /new

                path = os.path.normpath(os.path.join(root, fname))
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0

                # Keep the most recent file when the same O-number appears in
                # multiple subfolders
                if o not in candidates or mtime > candidates[o][1]:
                    candidates[o] = (path, mtime)

        self.progress.emit(
            f"  {len(candidates):,} new O-number(s) not found in Repository.")

        if not candidates:
            self.finished.emit(0, 0, [])
            return

        # ── Step 4: Copy to New Programs/new ─────────────────────────────
        self.progress.emit(f"\nCopying to: {new_folder}")
        copied  = 0
        skipped = 0
        errors: list[str] = []

        for o, (src_path, _) in sorted(candidates.items()):
            fname = os.path.basename(src_path)
            dest  = os.path.join(new_folder, fname)

            if os.path.exists(dest):
                skipped += 1
                continue
            try:
                shutil.copy2(src_path, dest)
                copied += 1
                self.progress.emit(f"  Copied  {fname}")
            except OSError as exc:
                errors.append(f"{fname}: {exc}")
                self.progress.emit(f"  ERROR   {fname}: {exc}")

        self.finished.emit(copied, skipped, errors)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

_STYLE = """
QDialog  { background: #0d0e18; color: #ccccdd; }
QLabel   { color: #aaaacc; font-size: 11px; }
QLineEdit {
    background: #1a1d2e; border: 1px solid #2a2d45;
    color: #ccccdd; padding: 3px 6px; border-radius: 3px; font-size: 11px;
}
QPushButton {
    background: #1a2030; border: 1px solid #2a2d45;
    color: #aaaacc; padding: 4px 10px; border-radius: 3px; font-size: 11px;
}
QPushButton:hover  { background: #1e2840; }
QPushButton:disabled { color: #334455; }
QTextEdit {
    background: #080910; border: 1px solid #1a1d2e;
    color: #8899aa; font-family: Consolas, monospace; font-size: 10px;
}
QProgressBar {
    background: #0f1018; border: 1px solid #1a1d2e;
    border-radius: 3px; height: 6px; text-align: center;
}
QProgressBar::chunk { background: #2255aa; border-radius: 3px; }
"""


class NewProgramsFinderDialog(QDialog):

    def __init__(self, repo_path: str = "", new_progs_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Programs Finder")
        self.setMinimumWidth(620)
        self.setMinimumHeight(500)
        self.setStyleSheet(_STYLE)
        self._worker: _FinderWorker | None = None
        self._build(
            repo_path      or _DEFAULT_REPO,
            new_progs_path or _DEFAULT_NEW_PROGS,
        )

    # ------------------------------------------------------------------
    def _build(self, repo_path: str, new_progs_path: str):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(14, 14, 14, 14)

        def folder_row(label_text: str, default: str) -> QLineEdit:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(120)
            edit = QLineEdit(default)
            btn  = QPushButton("Browse…")
            btn.setFixedWidth(72)
            btn.clicked.connect(lambda: self._browse(edit))
            row.addWidget(lbl)
            row.addWidget(edit)
            row.addWidget(btn)
            lay.addLayout(row)
            return edit

        self._repo_edit      = folder_row("Repository:",     repo_path)
        self._new_progs_edit = folder_row("New Programs:",   new_progs_path)

        info = QLabel(
            "Files in New Programs (any subfolder) whose O-number is not in the Repository "
            "will be copied to  New Programs\\new.  "
            "Extension is ignored — O10101.txt, O10101.NC, and O10101 all count as O10101."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#556677; font-size:10px;")
        lay.addWidget(info)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Find & Copy New Programs")
        self._run_btn.setStyleSheet(
            "QPushButton { background:#1a2840; border:1px solid #2a4060; "
            "color:#66aadd; font-weight:bold; padding:6px 14px; border-radius:3px; }"
            "QPushButton:hover { background:#1e3448; color:#88ccff; }"
            "QPushButton:disabled { color:#334455; }"
        )
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)   # indeterminate pulse
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(6)
        lay.addWidget(self._progress_bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Results will appear here…")
        lay.addWidget(self._log)

    # ------------------------------------------------------------------
    def _browse(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Folder", edit.text())
        if path:
            edit.setText(path)

    def _run(self):
        repo   = self._repo_edit.text().strip()
        new_p  = self._new_progs_edit.text().strip()

        if not os.path.isdir(repo):
            QMessageBox.warning(self, "Invalid Path",
                                f"Repository folder not found:\n{repo}")
            return
        if not os.path.isdir(new_p):
            QMessageBox.warning(self, "Invalid Path",
                                f"New Programs folder not found:\n{new_p}")
            return

        self._log.clear()
        self._run_btn.setEnabled(False)
        self._progress_bar.setVisible(True)

        self._worker = _FinderWorker(repo, new_p, self)
        self._worker.progress.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _on_done(self, copied: int, skipped: int, errors: list):
        self._progress_bar.setVisible(False)
        self._run_btn.setEnabled(True)
        self._log.append("")
        if copied == 0 and skipped == 0 and not errors:
            self._log.append("No new files found — New Programs folder is up to date.")
        else:
            self._log.append(
                f"Done.  {copied:,} file(s) copied to 'new' folder."
                + (f"  {skipped:,} already existed." if skipped else "")
                + (f"  {len(errors):,} error(s)." if errors else "")
            )
