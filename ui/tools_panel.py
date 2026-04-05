"""
CNC Direct Editor — Tools / Maintenance Panel.

Shown as a tab in the bottom strip. Houses infrequently-used operations
that were previously cluttering the toolbar.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt


class ToolsPanel(QWidget):
    # Signals fired when the user clicks each action button
    recheck_ranges          = pyqtSignal()
    auto_rename_range       = pyqtSignal()
    auto_resolve_dupes      = pyqtSignal()
    open_batch_replace      = pyqtSignal()
    open_feed_audit         = pyqtSignal()
    open_new_progs_finder   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #0d0e18; color: #ccccdd; }")
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0d0e18; }")

        inner = QWidget()
        inner.setStyleSheet("background: #0d0e18;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)

        # ── Database Maintenance ─────────────────────────────────────────
        lay.addWidget(self._section_hdr("Database Maintenance"))

        lay.addWidget(self._tool_btn(
            "Recheck Ranges",
            "Re-run the O-number range check on every file and update "
            "the Range Conflict flag using the current range table.",
            self.recheck_ranges,
        ))
        lay.addWidget(self._tool_btn(
            "Auto-Rename Wrong O-Range",
            "Find all files flagged as Range Conflict and rename them to "
            "the lowest free O-number in the correct range for their disc size. "
            "Skips shop-special and files with no parseable title.",
            self.auto_rename_range,
        ))
        lay.addWidget(self._tool_btn(
            "Auto-Resolve Duplicates",
            "For each duplicate group where all members share the same O-number "
            "(regardless of extension), keep the highest-scoring file "
            "(best verify score, then most lines) and mark the rest for deletion.",
            self.auto_resolve_dupes,
        ))

        lay.addSpacing(12)

        # ── Analysis Tools ───────────────────────────────────────────────
        lay.addWidget(self._section_hdr("Analysis Tools"))

        lay.addWidget(self._tool_btn(
            "Batch Replace",
            "Find and replace text patterns across multiple G-code programs "
            "at once. Useful for updating tool numbers, feed rates, or comments.",
            self.open_batch_replace,
        ))
        lay.addWidget(self._tool_btn(
            "Feed Rate Audit",
            "Scan selected files for feed rate violations. "
            "Reports lines that exceed the configured maximum.",
            self.open_feed_audit,
        ))

        lay.addSpacing(12)

        # ── File Management ───────────────────────────────────────────────
        lay.addWidget(self._section_hdr("File Management"))

        lay.addWidget(self._tool_btn(
            "New Programs Finder",
            "Scan New Programs subfolders and copy any file whose O-number is not "
            "already in the Repository into New Programs\\new.  "
            "Extension-agnostic: O10101.txt, O10101.NC, and O10101 all count as the same O-number.",
            self.open_new_progs_finder,
        ))

        lay.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

    # ------------------------------------------------------------------
    def _section_hdr(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color:#445577; font-size:10px; font-weight:bold; "
            "letter-spacing:1px; padding:4px 0 2px 0;")
        return lbl

    def _tool_btn(self, title: str, description: str,
                  signal: pyqtSignal) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background:#0f1018; border:1px solid #1e2038; "
            "border-radius:5px; }"
            "QFrame:hover { border-color:#2a3055; }"
        )
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)

        # Text block
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color:#aabbdd; font-size:12px; font-weight:bold; "
            "background:transparent; border:none;")
        text_col.addWidget(title_lbl)

        desc_lbl = QLabel(description)
        desc_lbl.setStyleSheet(
            "color:#445566; font-size:10px; background:transparent; border:none;")
        desc_lbl.setWordWrap(True)
        text_col.addWidget(desc_lbl)

        lay.addLayout(text_col, stretch=1)

        # Run button
        btn = QPushButton("Run")
        btn.setFixedWidth(64)
        btn.setFixedHeight(32)
        btn.setStyleSheet(
            "QPushButton { background:#1a2a3a; border:1px solid #2a3d55; "
            "color:#66aadd; border-radius:4px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#1e3448; border-color:#3a5577; color:#88ccff; }"
            "QPushButton:pressed { background:#162030; }"
        )
        btn.clicked.connect(signal.emit)
        lay.addWidget(btn)

        return frame
