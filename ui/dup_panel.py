"""
CNC Direct Editor — Duplicate group detail panel.

Shows all members of a duplicate group side-by-side with scores,
paths, and recommended-keep highlighting.
Signals:
    open_editor(file_id, file_path, o_number, verify_status, verify_score)
    open_diff(path_a, path_b, name_a, name_b)
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import direct_database as db

_SCORE_COLORS = {
    7: "#44dd88", 6: "#aadd44", 5: "#aadd44",
    4: "#ffaa33", 3: "#ffaa33", 2: "#ff5555", 1: "#ff5555", 0: "#ff5555",
}
_TYPE_LABELS = {
    "exact":         "Exact Copies (same content)",
    "name_conflict": "Name Conflict (same O-number, different content)",
    "backup_chain":  "Backup Chain",
    "derived":       "Derived Copy",
    "title_match":   "Title Match (same title, different O-number)",
}


class _MemberCard(QFrame):
    """Card showing one duplicate group member."""

    edit_clicked    = pyqtSignal(dict)
    compare_clicked = pyqtSignal(dict)

    def __init__(self, rec: dict, is_recommended: bool, parent=None):
        super().__init__(parent)
        self._rec = rec
        self.setFrameShape(QFrame.Shape.Box)
        border_color = "#44dd88" if is_recommended else "#2a2d45"
        self.setStyleSheet(
            f"QFrame {{ background:#0f1018; border:2px solid {border_color}; "
            f"border-radius:6px; }}"
            f"QLabel {{ border:none; background:transparent; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        # ── Header row ──
        hdr = QHBoxLayout()
        name_lbl = QLabel(rec["file_name"])
        name_lbl.setStyleSheet("color:#ccccff; font-size:12px; font-weight:bold;")
        hdr.addWidget(name_lbl)

        if is_recommended:
            star = QLabel("  ★ Recommended Keep")
            star.setStyleSheet("color:#44dd88; font-size:11px; font-weight:bold;")
            hdr.addWidget(star)

        hdr.addStretch()

        score = rec.get("verify_score", 0)
        score_lbl = QLabel(f"Score: {score}/7")
        color = _SCORE_COLORS.get(score, "#888888")
        score_lbl.setStyleSheet(
            f"color:{color}; font-weight:bold; font-size:12px; "
            f"border:1px solid {color}55; border-radius:4px; padding:1px 6px;"
        )
        hdr.addWidget(score_lbl)
        lay.addLayout(hdr)

        # ── Path ──
        path_lbl = QLabel(rec["file_path"])
        path_lbl.setStyleSheet("color:#666688; font-size:10px;")
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl)

        # ── Title + verify tokens ──
        if rec.get("program_title"):
            title_lbl = QLabel(f"Title: {rec['program_title']}")
            title_lbl.setStyleSheet("color:#8899bb; font-size:10px;")
            lay.addWidget(title_lbl)

        if rec.get("verify_status"):
            tok_lbl = QLabel(rec["verify_status"])
            tok_lbl.setStyleSheet("color:#556688; font-size:10px; font-family:Consolas;")
            lay.addWidget(tok_lbl)

        # ── Hash + lines ──
        meta_lbl = QLabel(
            f"Hash: {(rec.get('file_hash') or '')[:16]}…   "
            f"Lines: {rec.get('line_count', 0)}"
        )
        meta_lbl.setStyleSheet("color:#444466; font-size:10px;")
        lay.addWidget(meta_lbl)

        # ── Status tag ──
        st = rec.get("status", "active")
        st_colors = {"active":"#aaaacc","flagged":"#ffcc44","review":"#88dd44","delete":"#ff6666"}
        st_lbl = QLabel(st.upper())
        st_lbl.setStyleSheet(
            f"color:{st_colors.get(st,'#aaaacc')}; font-size:10px; font-weight:bold;")
        lay.addWidget(st_lbl)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(70)
        edit_btn.setStyleSheet(
            "QPushButton{background:#1a2030;border:1px solid #2a3040;"
            "color:#aaaacc;padding:3px 8px;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#222a40;}"
        )
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._rec))
        btn_row.addWidget(edit_btn)

        cmp_btn = QPushButton("Compare…")
        cmp_btn.setFixedWidth(80)
        cmp_btn.setStyleSheet(edit_btn.styleSheet())
        cmp_btn.clicked.connect(lambda: self.compare_clicked.emit(self._rec))
        btn_row.addWidget(cmp_btn)

        btn_row.addStretch()
        lay.addLayout(btn_row)


class DupPanel(QWidget):
    """
    Shows duplicate group members for a selected file.
    Launched from sidebar dup categories or right-click "Show Duplicates".
    """

    open_editor  = pyqtSignal(int, str, str, str, int)
    open_diff    = pyqtSignal(str, str, str, str)

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path  = db_path
        self._groups: list = []
        self._compare_first: dict | None = None

        self.setStyleSheet("""
            QWidget { background: #0a0b14; color: #ccccdd; }
            QLabel  { color: #aaaacc; font-size: 11px; }
            QComboBox {
                background: #1a1d2e; border: 1px solid #2a2d45;
                color: #ccccdd; padding: 3px 6px; border-radius: 3px; font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background: #1a1d2e; color: #ccccdd;
                selection-background-color: #2a3055;
            }
            QPushButton {
                background: #1a2030; border: 1px solid #2a2d45;
                color: #aaaacc; padding: 3px 8px; border-radius: 3px; font-size: 11px;
            }
            QPushButton:hover { background: #1e2840; }
        """)

        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──
        hdr = QWidget()
        hdr.setStyleSheet("background:#0f1018; border-bottom:1px solid #1a1d2e;")
        hdr.setFixedHeight(36)
        hlay = QHBoxLayout(hdr)
        hlay.setContentsMargins(8, 2, 8, 2)
        hlay.setSpacing(8)

        self._title_lbl = QLabel("Duplicate Groups")
        self._title_lbl.setStyleSheet(
            "color:#88aacc; font-size:12px; font-weight:bold;")
        hlay.addWidget(self._title_lbl)
        hlay.addStretch()

        self._group_combo = QComboBox()
        self._group_combo.setFixedWidth(300)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        hlay.addWidget(self._group_combo)

        root.addWidget(hdr)

        # ── Scroll area for cards ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0a0b14; }")

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet("background: #0a0b14;")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(10, 10, 10, 10)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()

        scroll.setWidget(self._cards_widget)
        root.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, file_id: int, file_name: str):
        """Load all dup groups for a given file."""
        self._groups = db.get_dup_groups_for_file(self.db_path, file_id)
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for g in self._groups:
            label = (
                f"{_TYPE_LABELS.get(g['group_type'], g['group_type'])}  "
                f"({g['member_count']} files)"
            )
            self._group_combo.addItem(label, g["id"])
        self._group_combo.blockSignals(False)

        self._title_lbl.setText(f"Duplicate Groups — {file_name}  ({len(self._groups)})")

        if self._groups:
            self._group_combo.setCurrentIndex(0)
            self._on_group_changed(0)
        else:
            self._clear_cards()

    def load_all_by_type(self, group_type: str | None = None):
        """Load all dup groups of a given type (for sidebar navigation)."""
        groups = db.get_all_dup_groups(self.db_path, group_type)
        self._groups = list(groups)
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for g in self._groups:
            onum = f"  O={g['o_number']}" if g["o_number"] else ""
            label = (
                f"{_TYPE_LABELS.get(g['group_type'], g['group_type'])}{onum}  "
                f"({g['member_count']} files)"
            )
            self._group_combo.addItem(label, g["id"])
        self._group_combo.blockSignals(False)

        type_label = _TYPE_LABELS.get(group_type, "All Duplicates") if group_type else "All Duplicates"
        self._title_lbl.setText(f"{type_label}  ({len(self._groups)} groups)")

        if self._groups:
            self._group_combo.setCurrentIndex(0)
            self._on_group_changed(0)
        else:
            self._clear_cards()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_group_changed(self, idx: int):
        if idx < 0 or idx >= len(self._groups):
            self._clear_cards()
            return
        group_id = self._group_combo.itemData(idx)
        members  = db.get_files_in_dup_group(self.db_path, group_id)
        group    = self._groups[idx]
        rec_id   = group["recommended_id"]
        self._populate_cards(members, rec_id)

    def _clear_cards(self):
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_cards(self, members, recommended_id):
        self._clear_cards()
        self._compare_first = None

        for rec in members:
            rec = dict(rec)
            is_rec = (rec["id"] == recommended_id)
            card = _MemberCard(rec, is_rec)
            card.edit_clicked.connect(self._on_edit)
            card.compare_clicked.connect(self._on_compare_pick)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

    def _on_edit(self, rec: dict):
        self.open_editor.emit(
            rec["id"], rec["file_path"], rec["o_number"],
            rec.get("verify_status", ""), rec.get("verify_score", 0)
        )

    def _on_compare_pick(self, rec: dict):
        """First click picks File A; second click picks File B and fires diff."""
        if self._compare_first is None:
            self._compare_first = rec
            return
        a = self._compare_first
        b = rec
        self._compare_first = None
        if a["file_path"] == b["file_path"]:
            return
        self.open_diff.emit(
            a["file_path"], b["file_path"],
            a["file_name"], b["file_name"]
        )
