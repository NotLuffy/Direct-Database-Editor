"""
CNC Direct Editor — Revision History dialog.

Shows all named revisions saved for a file.
Allows viewing, comparing to current, or restoring a revision.
"""

import os
import shutil
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QSplitter, QPlainTextEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

import direct_database as db


class RevisionHistoryDialog(QDialog):

    def __init__(self, db_path: str, file_id: int, file_path: str,
                 file_name: str, parent=None):
        super().__init__(parent)
        self.db_path    = db_path
        self.file_id    = file_id
        self.file_path  = file_path
        self.file_name  = file_name

        self.setWindowTitle(f"Revision History — {file_name}")
        self.resize(860, 520)
        self.setStyleSheet("""
            QDialog    { background: #0d0e18; color: #ccccdd; }
            QLabel     { color: #aaaacc; font-size: 11px; }
            QPushButton {
                background: #1a2030; border: 1px solid #2a2d45;
                color: #aaaacc; padding: 4px 12px;
                border-radius: 3px; font-size: 11px;
            }
            QPushButton:hover  { background: #1e2840; }
            QPushButton:disabled { color: #333355; border-color: #1a1d2e; }
            QTableWidget {
                background: #0f1018; color: #ccccdd; border: none;
                font-size: 11px; gridline-color: #1a1d2e;
                selection-background-color: #1e2240;
            }
            QTableWidget QHeaderView::section {
                background: #1a1d2e; color: #8899bb;
                border: none; padding: 4px; font-size: 11px;
            }
            QPlainTextEdit {
                background: #0a0b14; color: #ccccdd; border: none;
                font-family: Consolas, monospace; font-size: 10pt;
            }
        """)

        self._build()
        self._load()

    # ------------------------------------------------------------------

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)

        lay.addWidget(QLabel(f"File: {self.file_path}"))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #1a1d2e; }")

        # ── Revision table ──────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Label", "Notes", "Saved", "Backup Path"])
        self._table.horizontalHeader().resizeSection(0, 200)
        self._table.horizontalHeader().resizeSection(1, 220)
        self._table.horizontalHeader().resizeSection(2, 140)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        splitter.addWidget(self._table)

        # ── Diff preview ────────────────────────────────────────────────
        diff_widget = QSplitter(Qt.Orientation.Horizontal)
        diff_widget.setHandleWidth(3)

        self._left_lbl  = QLabel("  Current file")
        self._right_lbl = QLabel("  (select a revision)")

        left_pane  = QVBoxLayout()
        right_pane = QVBoxLayout()
        self._left_edit  = QPlainTextEdit()
        self._right_edit = QPlainTextEdit()
        for edit in (self._left_edit, self._right_edit):
            edit.setReadOnly(True)
            edit.setFont(QFont("Consolas", 9))

        lw = self._make_pane(self._left_lbl,  self._left_edit)
        rw = self._make_pane(self._right_lbl, self._right_edit)
        diff_widget.addWidget(lw)
        diff_widget.addWidget(rw)
        splitter.addWidget(diff_widget)
        splitter.setSizes([220, 260])
        lay.addWidget(splitter, stretch=1)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._restore_btn = QPushButton("Restore this Revision")
        self._restore_btn.setStyleSheet(
            "QPushButton { background:#2a1a0a; border:1px solid #8a5a2a;"
            " color:#ffaa44; padding:4px 14px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#3a2a10; }"
            "QPushButton:disabled { color:#333355; border-color:#1a1d2e; background:#0a0b14; }")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_restore)
        btn_row.addWidget(self._restore_btn)

        self._delete_btn = QPushButton("Delete Revision")
        self._delete_btn.setStyleSheet(
            "QPushButton { background:#2a0a0a; border:1px solid #8a2a2a;"
            " color:#ff6666; padding:4px 14px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#3a1010; }"
            "QPushButton:disabled { color:#333355; border-color:#1a1d2e; background:#0a0b14; }")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        lay.addLayout(btn_row)

        # Load current file into left pane
        self._load_current()

    def _make_pane(self, label: QLabel, edit: QPlainTextEdit):
        from PyQt6.QtWidgets import QWidget
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        label.setStyleSheet("color:#666688; font-size:10px; padding:2px 4px;"
                            " background:#0f1018; border-bottom:1px solid #1a1d2e;")
        lay.addWidget(label)
        lay.addWidget(edit)
        return w

    # ------------------------------------------------------------------

    def _load_current(self):
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                self._left_edit.setPlainText(f.read())
            self._left_lbl.setText(f"  Current: {self.file_name}")
        except Exception:
            self._left_edit.setPlainText("(cannot read current file)")

    def _load(self):
        self._revisions = db.get_revisions_for_file(self.db_path, self.file_id)
        self._table.setRowCount(0)
        for rev in self._revisions:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(rev["label"]))
            self._table.setItem(r, 1, QTableWidgetItem(rev["notes"] or ""))
            ts = rev["created_at"][:16].replace("T", "  ") if rev["created_at"] else ""
            self._table.setItem(r, 2, QTableWidgetItem(ts))
            exists = os.path.exists(rev["backup_path"])
            path_item = QTableWidgetItem(rev["backup_path"])
            if not exists:
                path_item.setForeground(QColor("#ff5555"))
                path_item.setToolTip("Backup file not found on disk")
            self._table.setItem(r, 3, path_item)

        if not self._revisions:
            self._right_lbl.setText("  No revisions saved yet")

    def _selected_revision(self):
        rows = self._table.selectedItems()
        if not rows:
            return None
        idx = self._table.currentRow()
        if 0 <= idx < len(self._revisions):
            return self._revisions[idx]
        return None

    def _on_selection(self):
        rev = self._selected_revision()
        has_rev = rev is not None
        exists  = has_rev and os.path.exists(rev["backup_path"])
        self._restore_btn.setEnabled(exists)
        self._delete_btn.setEnabled(has_rev)

        if rev:
            self._right_lbl.setText(
                f"  Rev: {rev['label']}  ({rev['created_at'][:10]})")
            if exists:
                try:
                    with open(rev["backup_path"], "r",
                              encoding="utf-8", errors="replace") as f:
                        self._right_edit.setPlainText(f.read())
                except Exception:
                    self._right_edit.setPlainText("(cannot read backup file)")
            else:
                self._right_edit.setPlainText("(backup file not found on disk)")

    def _on_restore(self):
        rev = self._selected_revision()
        if not rev or not os.path.exists(rev["backup_path"]):
            return

        reply = QMessageBox.question(
            self, "Restore Revision",
            f"Restore revision "{rev['label']}" over the current file?\n\n"
            f"The current file will be backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Backup current before overwriting
        try:
            backup_folder = db.get_setting(self.db_path, "backup_folder", "")
            if backup_folder and os.path.isdir(backup_folder):
                fname      = os.path.basename(self.file_path)
                name_noext = os.path.splitext(fname)[0]
                timestamp  = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                ext        = os.path.splitext(fname)[1]
                sub_dir    = os.path.join(backup_folder, name_noext)
                os.makedirs(sub_dir, exist_ok=True)
                bak = os.path.join(sub_dir,
                                   f"{name_noext}_pre_restore_{timestamp}{ext}")
                shutil.copy2(self.file_path, bak)

            shutil.copy2(rev["backup_path"], self.file_path)

            # Update DB mtime
            mtime = datetime.datetime.fromtimestamp(
                os.path.getmtime(self.file_path)).isoformat()
            conn = db.get_connection(self.db_path)
            with conn:
                conn.execute(
                    "UPDATE files SET last_modified=? WHERE id=?",
                    (mtime, self.file_id))
            conn.close()

            self._load_current()
            QMessageBox.information(self, "Restored",
                f"Revision "{rev['label']}" restored successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Restore Failed", str(exc))

    def _on_delete(self):
        rev = self._selected_revision()
        if not rev:
            return
        reply = QMessageBox.question(
            self, "Delete Revision",
            f"Remove revision "{rev['label']}" from the DB?\n"
            f"The backup file on disk is NOT deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        db.delete_revision(self.db_path, rev["id"])
        self._load()
        self._right_edit.setPlainText("")
        self._right_lbl.setText("  (select a revision)")
        self._restore_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
