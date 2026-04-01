"""
CNC Direct Editor — Feed Rate Audit dialog.

Scans G-code files for F-values outside an acceptable range
and presents every violation in a sortable table.
Double-click a row to open the file in the editor at that line.
"""

import re
import csv
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDoubleSpinBox, QCheckBox, QRadioButton, QButtonGroup,
    QGroupBox, QProgressBar, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import direct_database as db


# ─────────────────────────────────────────────────────────────────────────────
#  Regex  — matches F followed by a decimal or integer  (not inside comments)
# ─────────────────────────────────────────────────────────────────────────────
_F_RE = re.compile(r"\bF(\d*\.?\d+)", re.IGNORECASE)
_G00_RE = re.compile(r"\bG0?0\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
#  Background worker
# ─────────────────────────────────────────────────────────────────────────────

class _AuditWorker(QThread):
    progress   = pyqtSignal(int, int)          # current, total
    violation  = pyqtSignal(dict)              # one result row
    completed  = pyqtSignal(int, int)          # files_scanned, violations_found

    def __init__(self, files: list, f_min: float, f_max: float,
                 skip_g00: bool, parent=None):
        super().__init__(parent)
        self.files    = files     # list of dicts with file_path, o_number, file_name
        self.f_min    = f_min
        self.f_max    = f_max
        self.skip_g00 = skip_g00
        self._stop    = False

    def stop(self):
        self._stop = True

    def run(self):
        total      = len(self.files)
        violations = 0

        for i, rec in enumerate(self.files):
            if self._stop:
                break
            self.progress.emit(i + 1, total)

            path = rec.get("file_path", "")
            if not path or not os.path.isfile(path):
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except Exception:
                continue

            for line_no, raw in enumerate(lines, 1):
                stripped = raw.strip()
                # Skip blank lines and pure comments
                if not stripped or stripped.startswith("(") or stripped.startswith(";"):
                    continue
                # Skip G00 rapid lines if requested
                if self.skip_g00 and _G00_RE.search(stripped):
                    continue

                for m in _F_RE.finditer(stripped):
                    try:
                        val = float(m.group(1))
                    except ValueError:
                        continue

                    if val < self.f_min or val > self.f_max:
                        direction = "Too Low" if val < self.f_min else "Too High"
                        self.violation.emit({
                            "o_number":  rec.get("o_number", ""),
                            "file_name": rec.get("file_name", ""),
                            "file_path": path,
                            "line_no":   line_no,
                            "f_value":   val,
                            "direction": direction,
                            "content":   stripped[:120],
                        })
                        violations += 1

        self.completed.emit(total, violations)


# ─────────────────────────────────────────────────────────────────────────────
#  Dialog
# ─────────────────────────────────────────────────────────────────────────────

class FeedAuditDialog(QDialog):
    # Emitted when user wants to open a file in the editor at a specific line
    open_in_editor = pyqtSignal(str, int)    # file_path, line_no

    def __init__(self, db_path: str, file_ids: list | None = None, parent=None):
        super().__init__(parent)
        self.db_path  = db_path
        self.file_ids = file_ids   # None → all non-deleted files
        self._worker: _AuditWorker | None = None
        self._results: list[dict] = []

        title = "Feed Rate Audit"
        if file_ids is not None:
            title += f"  ({len(file_ids)} file{'s' if len(file_ids) != 1 else ''})"
        self.setWindowTitle(title)
        self.resize(1000, 620)
        self.setStyleSheet("""
            QDialog    { background: #0d0e18; color: #ccccdd; }
            QLabel     { color: #aaaacc; font-size: 11px; }
            QGroupBox  { color: #8899bb; font-size: 11px; border: 1px solid #1a1d2e;
                         border-radius: 3px; margin-top: 6px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
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
            QDoubleSpinBox {
                background: #0f1018; color: #ccccdd;
                border: 1px solid #2a2d45; padding: 2px 4px;
                border-radius: 3px; font-size: 11px;
            }
            QRadioButton { color: #aaaacc; font-size: 11px; }
            QCheckBox    { color: #aaaacc; font-size: 11px; }
            QProgressBar {
                background: #0f1018; border: 1px solid #1a1d2e;
                border-radius: 3px; text-align: center; color: #8899bb;
                font-size: 10px; height: 14px;
            }
            QProgressBar::chunk { background: #1a4070; border-radius: 2px; }
        """)

        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)

        # ── Settings row ──────────────────────────────────────────────────
        settings_row = QHBoxLayout()

        # F-value range
        range_box = QGroupBox("F-Value Range")
        range_lay = QHBoxLayout(range_box)
        range_lay.setSpacing(6)

        range_lay.addWidget(QLabel("Min:"))
        self._min_spin = QDoubleSpinBox()
        self._min_spin.setDecimals(4)
        self._min_spin.setRange(0.0, 99.0)
        self._min_spin.setSingleStep(0.001)
        self._min_spin.setValue(0.002)
        self._min_spin.setFixedWidth(90)
        range_lay.addWidget(self._min_spin)

        range_lay.addWidget(QLabel("Max:"))
        self._max_spin = QDoubleSpinBox()
        self._max_spin.setDecimals(4)
        self._max_spin.setRange(0.0, 99.0)
        self._max_spin.setSingleStep(0.001)
        self._max_spin.setValue(0.020)
        self._max_spin.setFixedWidth(90)
        range_lay.addWidget(self._max_spin)

        settings_row.addWidget(range_box)

        # Options
        opts_box = QGroupBox("Options")
        opts_lay = QHBoxLayout(opts_box)
        self._skip_g00_cb = QCheckBox("Skip G00 lines")
        self._skip_g00_cb.setChecked(True)
        opts_lay.addWidget(self._skip_g00_cb)
        settings_row.addWidget(opts_box)

        settings_row.addStretch()

        # Run button
        self._run_btn = QPushButton("Run Audit")
        self._run_btn.setStyleSheet(
            "QPushButton { background:#0a1a2a; border:1px solid #2a6aaa;"
            " color:#66aaff; padding:5px 18px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#0e2038; }"
            "QPushButton:disabled { color:#333355; border-color:#1a1d2e; background:#0a0b14; }")
        self._run_btn.clicked.connect(self._run_audit)
        settings_row.addWidget(self._run_btn)

        lay.addLayout(settings_row)

        # ── Progress / status ─────────────────────────────────────────────
        prog_row = QHBoxLayout()
        self._status_lbl = QLabel("Ready.")
        prog_row.addWidget(self._status_lbl, stretch=1)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedWidth(260)
        prog_row.addWidget(self._progress)
        lay.addLayout(prog_row)

        # ── Results table ─────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["O-Number", "File Name", "Line", "F-Value", "Issue", "Line Content"])
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(0, 90)
        hdr.resizeSection(1, 140)
        hdr.resizeSection(2, 60)
        hdr.resizeSection(3, 80)
        hdr.resizeSection(4, 80)
        hdr.setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._on_double_click)
        lay.addWidget(self._table, stretch=1)

        # ── Bottom buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._open_btn = QPushButton("Open in Editor")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_editor)
        btn_row.addWidget(self._open_btn)

        self._export_btn = QPushButton("Export CSV…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self._export_btn)

        btn_row.addStretch()

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_audit)
        btn_row.addWidget(self._stop_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        lay.addLayout(btn_row)

        self._table.selectionModel().selectionChanged.connect(
            lambda: self._open_btn.setEnabled(
                bool(self._table.selectedItems())))

    # ── Audit logic ───────────────────────────────────────────────────────────

    def _get_files(self) -> list:
        """Fetch file records to audit from the DB."""
        all_files = db.get_all_files(self.db_path)
        if self.file_ids is not None:
            id_set = set(self.file_ids)
            return [f for f in all_files if f["id"] in id_set]
        # Exclude deleted/trash
        return [f for f in all_files
                if f.get("status") not in ("delete", "trash")]

    def _run_audit(self):
        if self._worker and self._worker.isRunning():
            return

        if not self.db_path:
            QMessageBox.warning(self, "No Database", "No database is open.")
            return

        f_min = self._min_spin.value()
        f_max = self._max_spin.value()
        if f_min >= f_max:
            QMessageBox.warning(self, "Bad Range",
                                "Min F-value must be less than Max F-value.")
            return

        try:
            files = self._get_files()
        except Exception as exc:
            QMessageBox.critical(self, "Error Loading Files", str(exc))
            return

        if not files:
            self._status_lbl.setText("No files to scan.")
            return

        # Clear previous results
        self._table.setRowCount(0)
        self._results.clear()
        self._export_btn.setEnabled(False)
        self._open_btn.setEnabled(False)

        self._progress.setRange(0, len(files))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_lbl.setText(f"Scanning {len(files):,} files…")

        self._worker = _AuditWorker(
            files, f_min, f_max,
            skip_g00=self._skip_g00_cb.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.violation.connect(self._on_violation)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()

    def _stop_audit(self):
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    def _on_progress(self, current: int, total: int):
        self._progress.setValue(current)
        self._status_lbl.setText(
            f"Scanning {current:,} / {total:,}  —  "
            f"{len(self._results):,} violations so far…")

    def _on_violation(self, row: dict):
        self._results.append(row)
        self._add_table_row(row)

    def _add_table_row(self, row: dict):
        self._table.setSortingEnabled(False)
        r = self._table.rowCount()
        self._table.insertRow(r)

        is_low  = row["direction"] == "Too Low"
        hi_color = QColor("#ff6666")   # red  — too high
        lo_color = QColor("#ffaa44")   # amber — too low

        def _item(text, align=Qt.AlignmentFlag.AlignLeft):
            it = QTableWidgetItem(str(text))
            it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            return it

        f_str = f"{row['f_value']:.4f}".rstrip("0").rstrip(".")

        items = [
            _item(row["o_number"]),
            _item(row["file_name"]),
            _item(str(row["line_no"]),
                  Qt.AlignmentFlag.AlignRight),
            _item(f_str, Qt.AlignmentFlag.AlignRight),
            _item(row["direction"]),
            _item(row["content"]),
        ]

        color = lo_color if is_low else hi_color
        for col, it in enumerate(items):
            if col in (3, 4):          # F-Value and Issue columns get color
                it.setForeground(color)
            self._table.setItem(r, col, it)

        self._table.setSortingEnabled(True)

    def _on_finished(self, files_scanned: int, violations: int):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._export_btn.setEnabled(bool(self._results))
        self._status_lbl.setText(
            f"Done — {files_scanned:,} files scanned, "
            f"{violations:,} violation{'s' if violations != 1 else ''} found.")

    # ── Open in editor ────────────────────────────────────────────────────────

    def _selected_result(self) -> dict | None:
        rows = self._table.selectedItems()
        if not rows:
            return None
        # Find the result dict matching the selected table row
        r = self._table.currentRow()
        # The table may be sorted, so match by file_path + line_no stored in items
        try:
            file_name = self._table.item(r, 1).text()
            line_no   = int(self._table.item(r, 2).text())
            for res in self._results:
                if res["file_name"] == file_name and res["line_no"] == line_no:
                    return res
        except (AttributeError, ValueError):
            pass
        return None

    def _on_open_editor(self):
        res = self._selected_result()
        if res:
            self.open_in_editor.emit(res["file_path"], res["line_no"])

    def _on_double_click(self, _index):
        self._on_open_editor()

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Feed Audit Results", "feed_audit.csv",
            "CSV Files (*.csv);;All Files (*.*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["o_number", "file_name", "file_path",
                                "line_no", "f_value", "direction", "content"])
                writer.writeheader()
                writer.writerows(self._results)
            QMessageBox.information(self, "Exported",
                                    f"Results saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def closeEvent(self, event):          # noqa: N802
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)
