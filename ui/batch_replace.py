"""
CNC Direct Editor — Batch Find & Replace dialog.

Finds a text pattern across indexed files (optionally filtered by round size
or title keyword) and replaces it in-place, with backups.
"""

import os
import re
import shutil
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox,
    QProgressBar, QMessageBox, QGroupBox, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import direct_database as db


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _ReplaceWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, current_file
    finished = pyqtSignal(int, int)        # files_changed, replacements_made
    error    = pyqtSignal(str)

    def __init__(self, rows: list, pattern: str, replacement: str,
                 use_regex: bool, backup_folder: str, db_path: str,
                 parent=None):
        super().__init__(parent)
        self.rows        = rows
        self.pattern     = pattern
        self.replacement = replacement
        self.use_regex   = use_regex
        self.backup_folder = backup_folder
        self.db_path     = db_path
        self._cancelled  = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self.use_regex:
                rx = re.compile(self.pattern)
            else:
                rx = None

            total          = len(self.rows)
            files_changed  = 0
            replacements   = 0

            for i, row in enumerate(self.rows):
                if self._cancelled:
                    break
                path = row["file_path"]
                self.progress.emit(i, total, os.path.basename(path))

                if not os.path.exists(path):
                    continue

                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    if rx:
                        new_content, n = rx.subn(self.replacement, content)
                    else:
                        n = content.count(self.pattern)
                        new_content = content.replace(self.pattern, self.replacement)

                    if n == 0:
                        continue

                    # Backup before writing
                    if self.backup_folder and os.path.isdir(self.backup_folder):
                        fname      = os.path.basename(path)
                        name_noext = os.path.splitext(fname)[0]
                        timestamp  = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                        sub_dir    = os.path.join(self.backup_folder, name_noext)
                        os.makedirs(sub_dir, exist_ok=True)
                        bak_path   = os.path.join(sub_dir, f"{name_noext}_{timestamp}{os.path.splitext(fname)[1]}")
                        shutil.copy2(path, bak_path)

                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)

                    files_changed += 1
                    replacements  += n

                    # Update mtime in DB
                    mtime = datetime.datetime.fromtimestamp(
                        os.path.getmtime(path)).isoformat()
                    conn = db.get_connection(self.db_path)
                    with conn:
                        conn.execute(
                            "UPDATE files SET last_modified=? WHERE file_path=?",
                            (mtime, path)
                        )
                    conn.close()

                except Exception as exc:
                    pass  # skip files that can't be read/written

            self.finished.emit(files_changed, replacements)

        except Exception as exc:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# Preview helper
# ---------------------------------------------------------------------------

def _preview_matches(rows: list, pattern: str, use_regex: bool,
                     max_files: int = 500) -> list[dict]:
    """
    Scan files for pattern matches without writing.
    Returns list of dicts: {path, file_name, line_no, line_text, match_count}.
    """
    if use_regex:
        try:
            rx = re.compile(pattern)
        except re.error:
            return []
    else:
        rx = None

    results = []
    for row in rows[:max_files]:
        path = row["file_path"]
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue

        file_hits = []
        for ln_no, line in enumerate(lines, 1):
            if rx:
                n = len(rx.findall(line))
            else:
                n = line.count(pattern)
            if n:
                file_hits.append((ln_no, line.rstrip(), n))

        if file_hits:
            results.append({
                "path":      path,
                "file_name": os.path.basename(path),
                "hits":      file_hits,
                "total":     sum(h[2] for h in file_hits),
            })
    return results


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class BatchReplaceDialog(QDialog):

    def __init__(self, db_path: str, initial_find: str = "",
                 initial_filter: str = "", parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.setWindowTitle("Batch Find & Replace")
        self.resize(820, 620)
        self.setStyleSheet("""
            QDialog    { background: #0d0e18; color: #ccccdd; }
            QLabel     { color: #aaaacc; font-size: 11px; }
            QLineEdit  { background: #1a1d2e; border: 1px solid #2a2d45;
                         color: #ccccdd; padding: 4px; border-radius: 3px; font-size: 11px; }
            QCheckBox  { color: #aaaacc; font-size: 11px; }
            QComboBox  { background: #1a1d2e; border: 1px solid #2a2d45;
                         color: #ccccdd; padding: 3px 6px; border-radius: 3px; font-size: 11px; }
            QComboBox QAbstractItemView { background: #1a1d2e; color: #ccccdd;
                         selection-background-color: #2a3055; }
            QPushButton { background: #1a2030; border: 1px solid #2a2d45;
                          color: #aaaacc; padding: 4px 12px;
                          border-radius: 3px; font-size: 11px; }
            QPushButton:hover { background: #1e2840; }
            QPushButton:disabled { color: #333355; border-color: #1a1d2e; }
            QGroupBox  { color: #666688; border: 1px solid #1a1d2e;
                         border-radius: 4px; margin-top: 8px; font-size: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QTableWidget { background: #0f1018; color: #ccccdd; border: none;
                           font-size: 11px; gridline-color: #1a1d2e; }
            QTableWidget QHeaderView::section { background: #1a1d2e; color: #8899bb;
                           border: none; padding: 4px; font-size: 11px; }
            QProgressBar { background: #1a1d2e; border: none; border-radius: 3px;
                           color: #ccccdd; text-align: center; font-size: 11px; }
            QProgressBar::chunk { background: #2255aa; border-radius: 3px; }
        """)

        self._worker: _ReplaceWorker | None = None
        self._preview_rows: list = []
        self._all_rows: list = []
        self._build(initial_find, initial_filter)
        self._load_all_rows()

    def _build(self, initial_find: str, initial_filter: str):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        # ── Search criteria ──────────────────────────────────────────────
        crit = QGroupBox("Search criteria")
        crit_form = QFormLayout(crit)
        crit_form.setSpacing(6)
        crit_form.setContentsMargins(10, 16, 10, 10)

        self._filter_edit = QLineEdit(initial_filter)
        self._filter_edit.setPlaceholderText("Filter files by title keyword (e.g. 13.0, leave blank = all)")
        crit_form.addRow("Title filter:", self._filter_edit)

        self._find_edit = QLineEdit(initial_find)
        self._find_edit.setPlaceholderText(r"Text to find  (e.g.  X12.96  or  X12\.9[0-9]+  in regex mode)")
        crit_form.addRow("Find:", self._find_edit)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("Replacement text  (e.g.  X12.903)")
        crit_form.addRow("Replace with:", self._replace_edit)

        self._regex_chk = QCheckBox("Use regular expressions")
        crit_form.addRow("", self._regex_chk)

        lay.addWidget(crit)

        # ── Buttons row ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._preview_btn = QPushButton("Preview Matches")
        self._preview_btn.setStyleSheet(
            "QPushButton { background:#0a1a2a; border:1px solid #3366aa;"
            " color:#66aaff; padding:4px 14px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#112233; }")
        self._preview_btn.clicked.connect(self._on_preview)
        btn_row.addWidget(self._preview_btn)

        self._match_lbl = QLabel("")
        btn_row.addWidget(self._match_lbl)
        btn_row.addStretch()

        self._run_btn = QPushButton("Run Replace")
        self._run_btn.setStyleSheet(
            "QPushButton { background:#1a2a0a; border:1px solid #4a8a2a;"
            " color:#88dd44; padding:4px 14px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#253a10; }"
            "QPushButton:disabled { color:#333355; border-color:#1a1d2e; background:#0a0b14; }")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self._run_btn)

        lay.addLayout(btn_row)

        # ── Preview table ─────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["File", "Line #", "Current Line", "Matches"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().resizeSection(0, 180)
        self._table.horizontalHeader().resizeSection(1, 60)
        self._table.horizontalHeader().resizeSection(3, 70)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFont(QFont("Consolas", 9))
        lay.addWidget(self._table, stretch=1)

        # ── Progress bar ──────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFixedHeight(16)
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        # ── Dialog buttons ────────────────────────────────────────────────
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(self._close_btn)
        lay.addLayout(close_row)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_all_rows(self):
        try:
            conn = db.get_connection(self.db_path)
            self._all_rows = conn.execute(
                "SELECT id, file_path, program_title FROM files "
                "WHERE last_seen IS NOT NULL ORDER BY o_number"
            ).fetchall()
            conn.close()
        except Exception:
            self._all_rows = []

    def _filtered_rows(self) -> list:
        keyword = self._filter_edit.text().strip().upper()
        if not keyword:
            return list(self._all_rows)
        return [r for r in self._all_rows
                if keyword in (r["program_title"] or "").upper()]

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _on_preview(self):
        find    = self._find_edit.text()
        replace = self._replace_edit.text()
        if not find:
            QMessageBox.warning(self, "Find", "Enter a search pattern first.")
            return

        rows = self._filtered_rows()
        if not rows:
            QMessageBox.information(self, "No Files",
                "No files match the title filter.")
            return

        use_regex = self._regex_chk.isChecked()
        if use_regex:
            try:
                re.compile(find)
            except re.error as e:
                QMessageBox.warning(self, "Regex Error", str(e))
                return

        matches = _preview_matches(rows, find, use_regex)
        self._preview_rows = [r for r in rows
                              if r["file_path"] in {m["path"] for m in matches}]

        self._table.setRowCount(0)
        total_hits = 0
        for m in matches:
            for ln_no, line_text, n in m["hits"]:
                row_i = self._table.rowCount()
                self._table.insertRow(row_i)
                self._table.setItem(row_i, 0,
                    QTableWidgetItem(m["file_name"]))
                self._table.setItem(row_i, 1,
                    QTableWidgetItem(str(ln_no)))
                item = QTableWidgetItem(line_text.strip())
                item.setForeground(QColor("#ffcc44"))
                self._table.setItem(row_i, 2, item)
                self._table.setItem(row_i, 3,
                    QTableWidgetItem(str(n)))
                total_hits += n

        n_files = len(matches)
        self._match_lbl.setText(
            f"  {n_files} file(s)  •  {total_hits} replacement(s)")
        self._match_lbl.setStyleSheet(
            "color:#88dd44; font-size:11px;" if n_files else "color:#ff6666; font-size:11px;")
        self._run_btn.setEnabled(n_files > 0 and bool(replace))

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _on_run(self):
        find    = self._find_edit.text()
        replace = self._replace_edit.text()
        n_files = len(self._preview_rows)

        reply = QMessageBox.question(
            self, "Confirm Replace",
            f"Replace  "{find}"  →  "{replace}"\n"
            f"in {n_files} file(s)?\n\n"
            f"Backups will be created if a backup folder is configured in Settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        backup_folder = db.get_setting(self.db_path, "backup_folder", "")

        self._run_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, n_files)
        self._progress.setValue(0)

        self._worker = _ReplaceWorker(
            self._preview_rows, find, replace,
            self._regex_chk.isChecked(),
            backup_folder, self.db_path, self
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, done: int, total: int, fname: str):
        self._progress.setValue(done)
        self._progress.setFormat(f"{done}/{total}  {fname}")

    def _on_done(self, files_changed: int, replacements: int):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)
        self._match_lbl.setText(
            f"  Done — {files_changed} file(s) updated  •  {replacements} replacement(s)")
        QMessageBox.information(self, "Complete",
            f"Replaced in {files_changed} file(s)\n"
            f"{replacements} total replacement(s) made.")
        # Re-run preview to show remaining matches (should be 0)
        self._on_preview()

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)
