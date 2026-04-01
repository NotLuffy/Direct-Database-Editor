"""
CNC Direct Editor — Scan progress dialog.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout
)
from PyQt6.QtCore import Qt


_STYLE = """
QDialog   { background: #0d0e18; color: #ccccdd; }
QLabel    { color: #aaaacc; font-size: 12px; }
QProgressBar {
    background: #1a1d2e; border: 1px solid #2a2d45;
    border-radius: 4px; height: 18px; text-align: center;
    color: #ccccdd; font-size: 11px;
}
QProgressBar::chunk { background: #335599; border-radius: 3px; }
QPushButton {
    background: #1a2a3a; border: 1px solid #2a3a4a;
    color: #aaaacc; padding: 5px 20px; border-radius: 4px;
}
QPushButton:hover { background: #223344; }
"""


class ScanProgressDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indexing Files…")
        self.setMinimumWidth(460)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint
        )
        self.setStyleSheet(_STYLE)
        self._cancelled = False

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(20, 16, 20, 14)

        self._status_lbl = QLabel("Preparing…")
        lay.addWidget(self._status_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        lay.addWidget(self._bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn = QPushButton("Cancel")
        self._btn.setFixedWidth(90)
        self._btn.clicked.connect(self._on_btn)
        btn_row.addWidget(self._btn)
        lay.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def on_progress(self, current: int, total: int, message: str):
        self._status_lbl.setText(message)
        if current == -1:
            self._bar.setRange(0, 0)   # indeterminate
        else:
            self._bar.setRange(0, max(total, 1))
            self._bar.setValue(current)

    def on_finished(self, found: int, new: int, changed: int,
                    removed: int, dups: int):
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._status_lbl.setText(
            f"Done — {found:,} files indexed  |  "
            f"{new:,} new  |  {changed:,} changed  |  "
            f"{removed:,} missing  |  {dups:,} dup groups"
        )
        self._btn.setText("Done")
        self._btn.setStyleSheet(
            "QPushButton { background:#1a3a1a; border:1px solid #2a5a2a; "
            "color:#88dd88; padding:5px 20px; border-radius:4px; }"
        )

    def on_error(self, message: str):
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._status_lbl.setText(f"Error: {message[:500]}")
        self._btn.setText("Close")

    def _on_btn(self):
        if self._btn.text() in ("Done", "Close"):
            self.accept()
        else:
            self._cancelled = True
            self._btn.setEnabled(False)
            self._status_lbl.setText("Cancelling…")
            self.cancelled = True
            self.reject()

    @property
    def was_cancelled(self) -> bool:
        return self._cancelled
