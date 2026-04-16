"""
CNC Direct Editor — Bug Report Dialog.

Lets users describe a bug, optionally attach recent error log entries,
and save the report to the database.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QComboBox, QCheckBox, QPushButton,
    QDialogButtonBox, QFrame, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

import direct_database as db

_STYLE = """
    QDialog      { background:#0d0e18; color:#ccccdd; }
    QLabel       { color:#aaaacc; font-size:11px; }
    QLineEdit, QPlainTextEdit, QComboBox {
        background:#0a0b14; border:1px solid #2a2d45;
        color:#ccccdd; padding:4px; border-radius:3px;
        font-family:Consolas; font-size:10pt;
    }
    QCheckBox    { color:#aaaacc; font-size:11px; }
    QCheckBox::indicator { width:14px; height:14px; }
    QPushButton  {
        background:#1a2030; border:1px solid #2a2d45;
        color:#aaaacc; padding:4px 12px;
        border-radius:3px; font-size:11px;
    }
    QPushButton:hover { background:#1e2840; }
"""

_SEVERITY_COLORS = {
    "low":    "#778899",
    "normal": "#aaaacc",
    "high":   "#ffaa33",
    "crash":  "#ff5555",
}


class BugReportDialog(QDialog):

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self.setWindowTitle("Report a Bug")
        self.setMinimumWidth(540)
        self.setMinimumHeight(480)
        self.setStyleSheet(_STYLE)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 14, 16, 12)

        # ── Header ──
        hdr = QLabel("Report a Bug")
        hdr.setStyleSheet(
            "color:#88aacc; font-size:14px; font-weight:bold;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1a1d2e;"); lay.addWidget(sep)

        # ── Title ──
        lay.addWidget(QLabel("Title  (short summary):"))
        self._title = QLineEdit()
        self._title.setPlaceholderText("e.g. Save button doesn't work after editing")
        lay.addWidget(self._title)

        # ── Severity ──
        sev_row = QHBoxLayout()
        sev_row.addWidget(QLabel("Severity:"))
        self._severity = QComboBox()
        self._severity.addItems(["low", "normal", "high", "crash"])
        self._severity.setCurrentIndex(1)
        self._severity.setFixedWidth(120)
        self._severity.currentTextChanged.connect(self._on_severity_changed)
        sev_row.addWidget(self._severity)
        sev_row.addStretch()
        lay.addLayout(sev_row)

        # ── Description ──
        lay.addWidget(QLabel("Description  (what happened):"))
        self._desc = QPlainTextEdit()
        self._desc.setPlaceholderText(
            "Describe what went wrong. Include any error messages you saw.")
        self._desc.setFixedHeight(100)
        lay.addWidget(self._desc)

        # ── Steps ──
        lay.addWidget(QLabel("Steps to reproduce  (optional):"))
        self._steps = QPlainTextEdit()
        self._steps.setPlaceholderText(
            "1. Open a file\n2. Click Edit\n3. Change a value\n4. Click Save")
        self._steps.setFixedHeight(80)
        lay.addWidget(self._steps)

        # ── Error log attachment ──
        self._attach_errors = QCheckBox(
            "Attach recent error log entries (recommended)")
        self._attach_errors.setChecked(True)
        lay.addWidget(self._attach_errors)

        # Preview of what will be attached
        self._log_preview = QPlainTextEdit()
        self._log_preview.setReadOnly(True)
        self._log_preview.setFixedHeight(70)
        self._log_preview.setStyleSheet(
            "QPlainTextEdit { background:#07080f; color:#445566; "
            "font-size:9pt; border:1px solid #1a1d2e; }")
        self._log_preview.setFont(QFont("Consolas", 8))
        lay.addWidget(self._log_preview)

        self._populate_log_preview()
        self._attach_errors.toggled.connect(
            lambda checked: self._log_preview.setVisible(checked))

        # ── Buttons ──
        btns = QDialogButtonBox()
        self._submit_btn = btns.addButton(
            "Submit Report", QDialogButtonBox.ButtonRole.AcceptRole)
        self._submit_btn.setStyleSheet(
            "QPushButton{background:#1a3040;border:1px solid #2a5060;"
            "color:#66ccee;padding:4px 14px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#1e4050;}")
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_submit)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _populate_log_preview(self):
        try:
            import main as _main
            recent = _main.error_buffer.get_recent()
        except Exception:
            recent = ""
        if recent:
            lines = recent.strip().splitlines()
            preview = "\n".join(lines[-8:])  # last 8 lines
            self._log_preview.setPlainText(preview)
        else:
            self._log_preview.setPlainText("(no errors logged this session)")

    def _on_severity_changed(self, sev: str):
        color = _SEVERITY_COLORS.get(sev, "#aaaacc")
        self._severity.setStyleSheet(
            f"QComboBox {{ color:{color}; background:#0a0b14; "
            f"border:1px solid {color}55; padding:4px; border-radius:3px; }}")

    def _on_submit(self):
        title = self._title.text().strip()
        if not title:
            QMessageBox.warning(self, "Required", "Please enter a title.")
            return

        description = self._desc.toPlainText().strip()
        steps       = self._steps.toPlainText().strip()
        severity    = self._severity.currentText()

        error_log = ""
        if self._attach_errors.isChecked():
            try:
                import main as _main
                error_log = _main.error_buffer.get_recent()
            except Exception:
                pass

        try:
            import importlib.metadata
            version = importlib.metadata.version("cnc-direct-editor")
        except Exception:
            version = "dev"

        bug_id = db.submit_bug_report(
            self._db_path, title, description, steps,
            severity, error_log, version)

        QMessageBox.information(
            self, "Report Submitted",
            f"Bug report #{bug_id} saved.\n\n"
            "Thank you — it will be reviewed and addressed in a future update.")
        self.accept()
