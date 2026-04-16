"""
CNC Direct Editor — Bug Reports panel.

Shows all submitted bug reports with status, lets the developer
update status/notes, and exports reports to JSON for sharing.
"""

import os
import json
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QFrame, QDialog, QPlainTextEdit, QFormLayout, QLineEdit,
    QDialogButtonBox, QFileDialog, QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

import direct_database as db

_SEVERITY_COLORS = {
    "low":    QColor("#778899"),
    "normal": QColor("#aaaacc"),
    "high":   QColor("#ffaa33"),
    "crash":  QColor("#ff5555"),
}
_STATUS_COLORS = {
    "open":        QColor("#ffaa33"),
    "in_progress": QColor("#66aaff"),
    "fixed":       QColor("#44dd88"),
    "wontfix":     QColor("#556677"),
}
_MONO = QFont("Consolas", 10)

_COLS = ["#", "Date", "Severity", "Status", "Title"]


class BugListPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_path = ""
        self.setStyleSheet("""
            QWidget      { background:#0d0e18; color:#ccccdd; }
            QLabel       { color:#aaaacc; font-size:11px; }
            QPushButton  {
                background:#1a2030; border:1px solid #2a2d45;
                color:#aaaacc; padding:3px 10px;
                border-radius:3px; font-size:11px;
            }
            QPushButton:hover { background:#1e2840; }
            QTableWidget {
                background:#080910; color:#ccccdd;
                border:1px solid #1a1d2e; gridline-color:#141620;
                font-family:Consolas; font-size:10pt;
            }
            QTableWidget::item:selected { background:#1a2840; color:#ccddff; }
            QHeaderView::section {
                background:#0f1018; color:#8899bb;
                border:none; border-right:1px solid #1a1d2e;
                padding:4px 6px; font-size:10px;
            }
            QComboBox {
                background:#0a0b14; border:1px solid #2a2d45;
                color:#ccccdd; padding:2px 6px; border-radius:3px;
            }
        """)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # ── Toolbar ──
        bar = QHBoxLayout()
        bar.setSpacing(6)

        hdr = QLabel("Bug Reports")
        hdr.setStyleSheet(
            "color:#88aacc; font-size:12px; font-weight:bold;")
        bar.addWidget(hdr)
        bar.addStretch()

        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "open", "in_progress", "fixed", "wontfix"])
        self._filter_cb.currentTextChanged.connect(self._refresh)
        bar.addWidget(QLabel("Filter:"))
        bar.addWidget(self._filter_cb)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh)
        bar.addWidget(self._refresh_btn)

        self._export_btn = QPushButton("Export JSON")
        self._export_btn.setStyleSheet(
            "QPushButton{background:#1a1a2a;border:1px solid #3a3a6a;"
            "color:#8888dd;padding:3px 10px;border-radius:3px;}"
            "QPushButton:hover{background:#252545;}")
        self._export_btn.clicked.connect(self._on_export)
        bar.addWidget(self._export_btn)

        root.addLayout(bar)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1a1d2e;"); root.addWidget(sep)

        # ── Table ──
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        hdr_h = self._table.horizontalHeader()
        hdr_h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for i in range(4):
            hdr_h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.doubleClicked.connect(self._on_open_detail)
        root.addWidget(self._table, stretch=1)

        # ── Action buttons ──
        act_row = QHBoxLayout()
        act_row.setSpacing(6)

        self._detail_btn = QPushButton("View / Edit")
        self._detail_btn.clicked.connect(self._on_open_detail)
        act_row.addWidget(self._detail_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet(
            "QPushButton{background:#2a0a0a;border:1px solid #5a1a1a;"
            "color:#ff6655;padding:3px 10px;border-radius:3px;}"
            "QPushButton:hover{background:#3a1010;}")
        self._delete_btn.clicked.connect(self._on_delete)
        act_row.addWidget(self._delete_btn)

        act_row.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#445566; font-size:10px;")
        act_row.addWidget(self._count_lbl)
        root.addLayout(act_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_db_path(self, db_path: str):
        self._db_path = db_path
        self._refresh()

    # ------------------------------------------------------------------

    def _refresh(self):
        if not self._db_path:
            return
        sel = self._filter_cb.currentText()
        status = None if sel == "All" else sel
        rows = db.get_bug_reports(self._db_path, status)
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            def _item(text, color=None):
                it = QTableWidgetItem(str(text or ""))
                it.setFont(_MONO)
                if color:
                    it.setForeground(color)
                it.setData(Qt.ItemDataRole.UserRole, dict(row))
                return it
            sev_color = _SEVERITY_COLORS.get(row["severity"], QColor("#aaaacc"))
            sta_color = _STATUS_COLORS.get(row["status"],   QColor("#aaaacc"))
            date_short = (row["created_at"] or "")[:16].replace("T", " ")
            self._table.setItem(r, 0, _item(row["id"]))
            self._table.setItem(r, 1, _item(date_short))
            self._table.setItem(r, 2, _item(row["severity"], sev_color))
            self._table.setItem(r, 3, _item(row["status"],   sta_color))
            self._table.setItem(r, 4, _item(row["title"]))

        open_count = sum(1 for row in rows if row["status"] == "open")
        self._count_lbl.setText(
            f"{len(rows)} report(s)  •  {open_count} open")

    def _selected_row(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_open_detail(self):
        data = self._selected_row()
        if not data:
            return
        dlg = _BugDetailDialog(data, self._db_path, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _on_delete(self):
        data = self._selected_row()
        if not data:
            return
        reply = QMessageBox.question(
            self, "Delete Report",
            f"Delete bug report #{data['id']}: \"{data['title']}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_bug_report(self._db_path, data["id"])
            self._refresh()

    def _on_export(self):
        if not self._db_path:
            return
        rows = db.get_bug_reports(self._db_path)
        if not rows:
            QMessageBox.information(self, "No Reports", "No bug reports to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Bug Reports", "bug_reports.json",
            "JSON files (*.json)")
        if not path:
            return
        export = [dict(r) for r in rows]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, ensure_ascii=False)
        QMessageBox.information(
            self, "Exported",
            f"Exported {len(export)} report(s) to:\n{path}")


# ---------------------------------------------------------------------------
# Detail / edit dialog
# ---------------------------------------------------------------------------

class _BugDetailDialog(QDialog):

    def __init__(self, data: dict, db_path: str, parent=None):
        super().__init__(parent)
        self._data    = data
        self._db_path = db_path
        self.setWindowTitle(f"Bug #{data['id']} — {data['title']}")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setStyleSheet("""
            QDialog { background:#0d0e18; color:#ccccdd; }
            QLabel  { color:#aaaacc; font-size:11px; }
            QPlainTextEdit, QLineEdit, QComboBox {
                background:#0a0b14; border:1px solid #2a2d45;
                color:#ccccdd; padding:4px; border-radius:3px;
                font-family:Consolas; font-size:10pt;
            }
            QPushButton {
                background:#1a2030; border:1px solid #2a2d45;
                color:#aaaacc; padding:4px 12px;
                border-radius:3px; font-size:11px;
            }
            QPushButton:hover { background:#1e2840; }
        """)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(14, 12, 14, 10)

        form = QFormLayout()
        form.setSpacing(6)

        # Read-only fields
        def _ro(text):
            w = QLineEdit(str(text or ""))
            w.setReadOnly(True)
            w.setStyleSheet(
                "QLineEdit{background:#07080f;border:1px solid #1a1d2e;"
                "color:#556688;padding:4px;border-radius:3px;}")
            return w

        form.addRow("Title:",       _ro(self._data["title"]))
        form.addRow("Submitted:",   _ro((self._data["created_at"] or "")[:16].replace("T"," ")))
        form.addRow("Severity:",    _ro(self._data["severity"]))
        form.addRow("App version:", _ro(self._data["app_version"]))
        lay.addLayout(form)

        # Description
        lay.addWidget(QLabel("Description:"))
        desc = QPlainTextEdit(self._data["description"] or "")
        desc.setReadOnly(True)
        desc.setFixedHeight(70)
        desc.setStyleSheet(
            "QPlainTextEdit{background:#07080f;border:1px solid #1a1d2e;color:#aaaacc;}")
        lay.addWidget(desc)

        # Steps
        if self._data.get("steps"):
            lay.addWidget(QLabel("Steps to reproduce:"))
            steps = QPlainTextEdit(self._data["steps"])
            steps.setReadOnly(True)
            steps.setFixedHeight(60)
            steps.setStyleSheet(
                "QPlainTextEdit{background:#07080f;border:1px solid #1a1d2e;color:#aaaacc;}")
            lay.addWidget(steps)

        # Error log
        if self._data.get("error_log"):
            lay.addWidget(QLabel("Attached error log:"))
            elog = QPlainTextEdit(self._data["error_log"])
            elog.setReadOnly(True)
            elog.setFixedHeight(80)
            elog.setFont(QFont("Consolas", 8))
            elog.setStyleSheet(
                "QPlainTextEdit{background:#07080f;border:1px solid #1a1d2e;"
                "color:#445566;font-size:9pt;}")
            lay.addWidget(elog)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1a1d2e;"); lay.addWidget(sep)

        # Editable: status, resolved_in, dev_notes
        lay.addWidget(QLabel("Update status:"))
        status_row = QHBoxLayout()
        self._status_cb = QComboBox()
        self._status_cb.addItems(["open", "in_progress", "fixed", "wontfix"])
        cur = self._data.get("status", "open")
        self._status_cb.setCurrentText(cur)
        self._status_cb.setFixedWidth(140)
        status_row.addWidget(self._status_cb)
        status_row.addWidget(QLabel("Resolved in version:"))
        self._resolved = QLineEdit(self._data.get("resolved_in") or "")
        self._resolved.setPlaceholderText("e.g. v1.4.2")
        self._resolved.setFixedWidth(120)
        status_row.addWidget(self._resolved)
        status_row.addStretch()
        lay.addLayout(status_row)

        lay.addWidget(QLabel("Developer notes:"))
        self._dev_notes = QPlainTextEdit(self._data.get("dev_notes") or "")
        self._dev_notes.setFixedHeight(70)
        self._dev_notes.setPlaceholderText("Internal notes, fix description, etc.")
        lay.addWidget(self._dev_notes)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_save(self):
        db.update_bug_status(
            self._db_path,
            self._data["id"],
            self._status_cb.currentText(),
            self._dev_notes.toPlainText().strip(),
            self._resolved.text().strip(),
        )
        self.accept()
