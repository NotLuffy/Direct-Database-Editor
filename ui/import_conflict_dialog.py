"""
Import Conflict Dialog — shown when ImportNewWorker finds files whose
O-number already exists in the DB but with a different program title.

User chooses:
  • Skip — leave both files as-is
  • Rename New File — assign a new O-number to the incoming file before importing
  • Rename Existing File — reassign the existing DB file to a new O-number,
                           then import the new file under the original O-number

When Rename is selected the dialog checks round-size consistency between the
program title and the OD turn tool value.  If they conflict the user is warned
and offered a "Save as Fix Later" button which forces the O-number into the
O30000–O39999 range.
"""

import os
import re
import direct_database as db
from verifier import check_file_round_size
from direct_scorer import score_file

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox,
    QHeaderView, QAbstractItemView, QMessageBox, QWidget, QFrame,
    QSplitter, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor


_STYLE = """
QDialog { background: #0d0e1a; color: #ccccdd; }
QLabel  { color: #aaaacc; }
QTableWidget {
    background: #10121e; color: #ccccdd;
    gridline-color: #1e2038;
    selection-background-color: #1e2850;
}
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section {
    background: #151728; color: #8899cc;
    padding: 4px 6px; border: none;
    border-bottom: 1px solid #2a2d45;
    font-size: 11px;
}
QComboBox {
    background: #181a2e; color: #ccccdd;
    border: 1px solid #2a2d45; border-radius: 3px;
    padding: 2px 6px; font-size: 11px; min-width: 80px;
}
QComboBox:disabled { color: #444466; border-color: #1e2035; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: #181a2e; color: #ccccdd;
    selection-background-color: #223366;
}
QPushButton {
    background: #1a2030; border: 1px solid #2a2d45;
    color: #aaaacc; padding: 5px 14px; border-radius: 3px; font-size: 11px;
}
QPushButton:hover { background: #1e2840; }
QPushButton#import_btn { background: #0a2a1a; border-color: #44cc88; color: #44cc88; }
QPushButton#import_btn:hover { background: #0e3a22; }
QPushButton#cancel_btn { background: #1a0a0a; border-color: #cc4444; color: #cc4444; }
QPushButton#fixlater_btn { background: #2a1a00; border-color: #ffaa33; color: #ffaa33; }
QPushButton#fixlater_btn:hover { background: #3a2a00; }
QFrame#warn_bar { background: #2a1800; border: 1px solid #aa6600; border-radius: 3px; }
"""

_ACT_SKIP     = "Skip"
_ACT_NEW      = "Rename New File"
_ACT_EXISTING = "Rename Existing File"

# Standard O-number ranges in dropdown
_RANGES = [
    ("O30000 – O39999 [Fix Later]", 30000, 39999),
    ("O60000 – O69999",             60000, 69999),
    ("O70000 – O79999",             70000, 79999),
    ("O80000 – O89999",             80000, 89999),
    ("O90000 – O99999",             90000, 99999),
]
_FIX_LATER_IDX = 0   # index of the fix-later row in _RANGES

_MAX_AVAIL = 300

# HAAS reserved O-number ranges — never assign these
_HAAS_RESERVED = [
    (0,    999,   "O0000–O0999  (HAAS reserved — system programs)"),
    (8000, 9999,  "O8000–O9999  (HAAS reserved — macro/probe)"),
]

def _is_haas_reserved(o_val: int) -> str | None:
    """Return warning string if o_val is in a HAAS reserved range, else None."""
    for lo, hi, msg in _HAAS_RESERVED:
        if lo <= o_val <= hi:
            return msg
    return None


def _o_int(o_str: str) -> int:
    m = re.match(r'O(\d+)', o_str.upper())
    return int(m.group(1)) if m else 0


def _score_label(score: int) -> tuple[str, str]:
    """Return (label, hex_color) for a verify score."""
    if score >= 6:
        return f"★ {score}/6", "#44dd88"
    if score >= 4:
        return f"★ {score}/6", "#aadd44"
    if score >= 2:
        return f"★ {score}/6", "#ffaa33"
    return f"★ {score}/6", "#ff5555"


def _get_score(path: str, title: str, o_number: str) -> int:
    try:
        score, _ = score_file(path, title, o_number=o_number)
        return score
    except Exception:
        return -1


def _range_idx_for_round(rs: float | None) -> int:
    """Return the _RANGES index whose lo–hi bracket contains rs, or -1."""
    if rs is None:
        return -1
    for i, (_, lo, hi) in enumerate(_RANGES):
        if lo <= round(rs * 1000) / 1000 * 1000 <= hi:
            return i
    # numeric search with tolerance
    for i, (_, lo, hi) in enumerate(_RANGES):
        if lo / 10000 - 0.5 <= rs <= hi / 10000 + 0.5:
            return i
    return -1


def _range_idx_for_o(o_val: int) -> int:
    for i, (_, lo, hi) in enumerate(_RANGES):
        if lo <= o_val <= hi:
            return i
    return -1


class ImportConflictDialog(QDialog):
    """
    Shows a table of O-number conflicts.
    Call `.get_results()` after exec() to retrieve action dicts.
    """

    def __init__(self, conflicts: list, folder: str, db_path: str, parent=None):
        super().__init__(parent)
        self._conflicts  = conflicts
        self._folder     = folder
        self._db_path    = db_path
        self._used       = db.get_used_o_numbers(db_path) if db_path else set()
        self._results    = []
        # Cache: row_idx → check_file_round_size result for the "rename" target
        self._round_cache: dict[int, dict] = {}

        # Pre-scan all directories touched by these conflicts so that files
        # existing on disk but not yet indexed are also treated as "used".
        self._disk_o_ints: set[int] = self._scan_conflict_dirs()

        self.setWindowTitle("Import Conflicts — Review Required")
        self.setMinimumSize(1340, 540)
        self.setStyleSheet(_STYLE)
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        hdr = QLabel(
            f"<b>{len(self._conflicts)}</b> file(s) share an O-number with a different title.<br>"
            "Choose which file to rename, pick a range, then select an available O-number."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color: #ffcc88; font-size: 12px; padding-bottom: 2px;")
        lay.addWidget(hdr)

        # Warning bar (hidden until a conflict is detected)
        self._warn_frame = QFrame()
        self._warn_frame.setObjectName("warn_bar")
        self._warn_frame.setVisible(False)
        wl = QHBoxLayout(self._warn_frame)
        wl.setContentsMargins(8, 6, 8, 6)
        self._warn_lbl = QLabel("")
        self._warn_lbl.setStyleSheet("color: #ffcc44; font-size: 11px;")
        self._warn_lbl.setWordWrap(True)
        wl.addWidget(self._warn_lbl, 1)
        self._fix_later_btn = QPushButton("Save as Fix Later  (O30000–O39999)")
        self._fix_later_btn.setObjectName("fixlater_btn")
        self._fix_later_btn.setFixedWidth(230)
        self._fix_later_btn.clicked.connect(self._on_fix_later)
        wl.addWidget(self._fix_later_btn)
        lay.addWidget(self._warn_frame)

        # Table
        cols = ["O-Number", "Existing File  (in DB)", "Score",
                "New Incoming File", "Score", "Action", "Range",
                "Available O-Number", "Compare"]
        self._table = QTableWidget(len(self._conflicts), len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.itemSelectionChanged.connect(self._on_row_selected)

        hv = self._table.horizontalHeader()
        hv.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hv.setStretchLastSection(True)

        mono = QFont("Consolas", 9)

        self._action_combos = []
        self._range_combos  = []
        self._avail_combos  = []
        self._active_row    = None   # row currently shown in warn bar

        for row_idx, conflict in enumerate(self._conflicts):
            o_num = conflict["o_number"]
            ex_t  = conflict["existing_title"]
            ex_f  = os.path.basename(conflict["existing_path"])
            nw_t  = conflict["new_title"]
            nw_f  = os.path.basename(conflict["path"])

            def _item(text, color=None):
                it = QTableWidgetItem(text)
                it.setFont(mono)
                if color:
                    it.setForeground(QColor(color))
                it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                return it

            # ── Scores (computed now) ─────────────────────────────────────
            ex_score = _get_score(conflict["existing_path"], ex_t, o_num)
            nw_score = _get_score(conflict["path"],          nw_t, o_num)

            self._table.setItem(row_idx, 0, _item(o_num, "#88aaff"))
            self._table.setItem(row_idx, 1, _item(f"{ex_t}\n{ex_f}", "#ffaa66"))

            ex_lbl, ex_col = _score_label(ex_score) if ex_score >= 0 else ("—", "#555577")
            sc_item_ex = _item(ex_lbl, ex_col)
            sc_item_ex.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 2, sc_item_ex)

            self._table.setItem(row_idx, 3, _item(f"{nw_t}\n{nw_f}", "#88ee88"))

            nw_lbl, nw_col = _score_label(nw_score) if nw_score >= 0 else ("—", "#555577")
            sc_item_nw = _item(nw_lbl, nw_col)
            sc_item_nw.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 4, sc_item_nw)

            # ── Action ──────────────────────────────────────────────────
            act_cb = QComboBox()
            # "Rename Existing" is only available when the existing file is
            # already in the DB (existing_id != None).  Same-batch collisions
            # (two files with the same O-number in one import folder, neither
            # yet in the DB) only allow renaming the incoming file.
            if conflict.get("existing_id") is not None:
                act_cb.addItems([_ACT_SKIP, _ACT_NEW, _ACT_EXISTING])
            else:
                act_cb.addItems([_ACT_SKIP, _ACT_NEW])
            act_cb.currentIndexChanged.connect(
                lambda _, r=row_idx: self._on_action_changed(r))
            self._table.setCellWidget(row_idx, 5, act_cb)
            self._action_combos.append(act_cb)

            # ── Range ────────────────────────────────────────────────────
            rng_cb = QComboBox()
            rng_cb.setEnabled(False)
            for label, lo, hi in _RANGES:
                rng_cb.addItem(label, (lo, hi))
            # Pre-select range matching conflict O-number
            o_val = _o_int(o_num)
            idx   = _range_idx_for_o(o_val)
            if idx >= 0:
                rng_cb.setCurrentIndex(idx)
            rng_cb.currentIndexChanged.connect(
                lambda _, r=row_idx: self._on_range_changed(r))
            self._table.setCellWidget(row_idx, 6, rng_cb)
            self._range_combos.append(rng_cb)

            # ── Available O-Number ────────────────────────────────────────
            avail_cb = QComboBox()
            avail_cb.setEnabled(False)
            avail_cb.setFont(mono)
            self._table.setCellWidget(row_idx, 7, avail_cb)
            self._avail_combos.append(avail_cb)

            # ── Compare button ────────────────────────────────────────────
            cmp_btn = QPushButton("Compare")
            cmp_btn.setFixedWidth(72)
            cmp_btn.setStyleSheet(
                "QPushButton { background:#1a1a3a; border:1px solid #4466aa;"
                " color:#88aaff; font-size:10px; padding:3px 6px; border-radius:3px; }"
                "QPushButton:hover { background:#22285a; }")
            cmp_btn.clicked.connect(lambda _, r=row_idx: self._on_compare(r))
            self._table.setCellWidget(row_idx, 8, cmp_btn)

        for r in range(len(self._conflicts)):
            self._table.setRowHeight(r, 46)
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 230)
        self._table.setColumnWidth(2, 58)
        self._table.setColumnWidth(3, 230)
        self._table.setColumnWidth(4, 58)
        self._table.setColumnWidth(5, 155)
        self._table.setColumnWidth(6, 170)
        self._table.setColumnWidth(7, 110)
        self._table.setColumnWidth(8, 76)
        lay.addWidget(self._table)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        skip_all = QPushButton("Skip All")
        skip_all.clicked.connect(self._skip_all)
        btn_row.addWidget(skip_all)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancel_btn")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        ok = QPushButton("Import Selected")
        ok.setObjectName("import_btn")
        ok.clicked.connect(self._on_confirm)
        btn_row.addWidget(ok)

        lay.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Round-size check helpers
    # ------------------------------------------------------------------

    def _get_round_check(self, row_idx: int) -> dict | None:
        """Return (cached) round-size check for the file being renamed in this row."""
        if row_idx in self._round_cache:
            return self._round_cache[row_idx]

        conflict = self._conflicts[row_idx]
        act      = self._action_combos[row_idx].currentText()

        if act == _ACT_NEW:
            path  = conflict["path"]
            title = conflict["new_title"]
        elif act == _ACT_EXISTING:
            path  = conflict["existing_path"]
            title = conflict["existing_title"]
        else:
            return None

        try:
            chk = check_file_round_size(path, title)
        except Exception:
            chk = None

        self._round_cache[row_idx] = chk
        return chk

    def _apply_suggested_range(self, row_idx: int, chk: dict):
        """Set range combo to the suggested range from the round-size check."""
        suggested = chk.get("suggested_range")  # (label, lo, hi) or None
        if suggested is None:
            return
        _, lo, hi = suggested
        for i, (_, rlo, rhi) in enumerate(_RANGES):
            if rlo == lo and rhi == hi:
                self._range_combos[row_idx].blockSignals(True)
                self._range_combos[row_idx].setCurrentIndex(i)
                self._range_combos[row_idx].blockSignals(False)
                self._populate_avail(row_idx)
                break

    def _update_warn_bar(self, row_idx: int | None):
        """Show/hide warning bar for the currently active row."""
        self._active_row = row_idx
        if row_idx is None:
            self._warn_frame.setVisible(False)
            return

        chk = self._get_round_check(row_idx)
        if chk is None or chk.get("consistent", True):
            self._warn_frame.setVisible(False)
            return

        msg = chk["conflict_msg"]
        act = self._action_combos[row_idx].currentText()
        file_lbl = (os.path.basename(self._conflicts[row_idx]["path"])
                    if act == _ACT_NEW
                    else os.path.basename(self._conflicts[row_idx]["existing_path"]))
        self._warn_lbl.setText(
            f"⚠  <b>{file_lbl}</b>: {msg}  —  "
            f"Range auto-set to match OD tool. "
            f"If the title is wrong, use \"Save as Fix Later\" to flag it for correction."
        )
        self._warn_frame.setVisible(True)

    # ------------------------------------------------------------------
    # Available O-numbers
    # ------------------------------------------------------------------

    def _scan_conflict_dirs(self) -> set[int]:
        """
        Scan every directory that appears in the conflict list (both the
        incoming file's folder and the existing file's folder) and collect
        the O-number integers of every O-number file found there.
        This catches files that exist on disk but are not yet indexed in the DB.
        """
        dirs_to_scan: set[str] = set()
        for c in self._conflicts:
            dirs_to_scan.add(os.path.dirname(c["path"]))
            dirs_to_scan.add(os.path.dirname(c.get("existing_path", "")))
        if self._folder:
            dirs_to_scan.add(self._folder)
        dirs_to_scan.discard("")

        found: set[int] = set()
        for d in dirs_to_scan:
            try:
                for fname in os.listdir(d):
                    base = os.path.splitext(fname)[0]
                    m = re.match(r'^O(\d{4,6})(?:_\d+)?$', base, re.IGNORECASE)
                    if m:
                        found.add(int(m.group(1)))
            except OSError:
                pass
        return found

    @staticmethod
    def _to_used_ints(used: set) -> set[int]:
        """Convert a set of O-number strings to a set of ints (format-agnostic)."""
        result = set()
        for o in used:
            m = re.match(r'O?(\d+)', o.strip(), re.IGNORECASE)
            if m:
                result.add(int(m.group(1)))
        return result

    def _available_in_range(self, used_ints: set[int], lo: int, hi: int) -> list[str]:
        result = []
        for n in range(lo, hi + 1):
            if n not in used_ints:
                result.append(f"O{n:05d}")
                if len(result) >= _MAX_AVAIL:
                    break
        return result

    def _populate_avail(self, row_idx: int):
        rng_cb   = self._range_combos[row_idx]
        avail_cb = self._avail_combos[row_idx]
        lo, hi   = rng_cb.currentData()
        avail_cb.clear()

        # Refresh both DB state and disk scan (stays pure — only updated here)
        if self._db_path:
            self._used = db.get_used_o_numbers(self._db_path)
        self._disk_o_ints = self._scan_conflict_dirs()

        # Build a LOCAL used set for the dropdown: DB + disk + sibling-row selections.
        # We do NOT modify self._used here so confirm-time validation stays clean.
        local_used = set(self._used)
        for i, (act_cb, av_cb) in enumerate(
                zip(self._action_combos, self._avail_combos)):
            if i == row_idx or act_cb.currentText() == _ACT_SKIP:
                continue
            sel = av_cb.currentText().strip().upper()
            if re.match(r'^O\d{4,6}$', sel):
                local_used.add(sel)

        # Merge disk-found O-numbers (files on disk not yet in DB)
        local_ints = self._to_used_ints(local_used) | self._disk_o_ints

        nums = self._available_in_range(local_ints, lo, hi)
        if nums:
            avail_cb.addItems(nums)
        else:
            avail_cb.addItem("(none available)")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_action_changed(self, row_idx: int):
        act    = self._action_combos[row_idx].currentText()
        active = (act != _ACT_SKIP)
        self._range_combos[row_idx].setEnabled(active)
        self._avail_combos[row_idx].setEnabled(active)

        # Invalidate cache when action changes (different file may be targeted)
        self._round_cache.pop(row_idx, None)

        if active:
            self._populate_avail(row_idx)
            # Run round-size check and auto-set range
            chk = self._get_round_check(row_idx)
            if chk:
                if not chk["consistent"] and chk.get("od_round") is not None:
                    # OD takes priority — auto-select that range
                    od_rs  = chk["od_round"]
                    od_idx = -1
                    for i, (_, lo, hi) in enumerate(_RANGES):
                        # Convert round size (inches) to O-range: multiply by 10000
                        # e.g. 6.0 → 60000, 7.0 → 70000
                        o_lo = round(od_rs * 10000)
                        if lo <= o_lo <= hi:
                            od_idx = i
                            break
                    if od_idx >= 0:
                        self._range_combos[row_idx].blockSignals(True)
                        self._range_combos[row_idx].setCurrentIndex(od_idx)
                        self._range_combos[row_idx].blockSignals(False)
                        self._populate_avail(row_idx)
                elif chk.get("suggested_range"):
                    self._apply_suggested_range(row_idx, chk)

            # Update warn bar if this row is the selected one
            sel = self._table.currentRow()
            if sel == row_idx or self._active_row == row_idx:
                self._update_warn_bar(row_idx)
        else:
            if self._active_row == row_idx:
                self._update_warn_bar(None)

    def _on_range_changed(self, row_idx: int):
        if self._range_combos[row_idx].isEnabled():
            self._populate_avail(row_idx)

    def _on_row_selected(self):
        rows = {i.row() for i in self._table.selectedItems()}
        if not rows:
            self._update_warn_bar(None)
            return
        row_idx = min(rows)
        act = self._action_combos[row_idx].currentText()
        if act != _ACT_SKIP:
            self._update_warn_bar(row_idx)
        else:
            self._update_warn_bar(None)

    def _on_fix_later(self):
        """Force the active row's range to O30000–O39999."""
        row_idx = self._active_row
        if row_idx is None:
            return
        self._range_combos[row_idx].blockSignals(True)
        self._range_combos[row_idx].setCurrentIndex(_FIX_LATER_IDX)
        self._range_combos[row_idx].blockSignals(False)
        self._populate_avail(row_idx)
        self._warn_frame.setVisible(False)

    def _skip_all(self):
        for cb in self._action_combos:
            cb.setCurrentIndex(0)

    def _on_compare(self, row_idx: int):
        """Open a side-by-side diff dialog for the two conflicting files."""
        from ui.diff_panel import DiffPanel
        conflict = self._conflicts[row_idx]
        path_a   = conflict["existing_path"]
        path_b   = conflict["path"]
        name_a   = os.path.basename(path_a)
        name_b   = os.path.basename(path_b)
        title_a  = conflict["existing_title"]
        title_b  = conflict["new_title"]

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Compare  {name_a}  vs  {name_b}")
        dlg.setMinimumSize(1300, 700)
        dlg.setStyleSheet("QDialog { background: #0d0e1a; }")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(6, 6, 6, 6)

        panel = DiffPanel(parent=dlg)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel.compare(path_a, name_a, path_b, name_b,
                      title_a=title_a, title_b=title_b)
        lay.addWidget(panel)

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(dlg.accept)
        br = QHBoxLayout()
        br.addStretch()
        br.addWidget(close_btn)
        lay.addLayout(br)

        dlg.exec()

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------

    def _on_confirm(self):
        errors  = []
        results = []

        # Fresh DB query → integer set for format-agnostic validation.
        # Also includes files found on disk but not yet indexed.
        # We accumulate confirmed picks into this set row-by-row so that two
        # rows can't claim the same number even within the same dialog session.
        if self._db_path:
            confirm_used = db.get_used_o_numbers(self._db_path)
        else:
            confirm_used = set(self._used)
        confirm_ints = self._to_used_ints(confirm_used) | self._disk_o_ints

        for row_idx, conflict in enumerate(self._conflicts):
            act = self._action_combos[row_idx].currentText()
            if act == _ACT_SKIP:
                continue

            new_o = self._avail_combos[row_idx].currentText().strip().upper()
            if not new_o or not re.match(r'^O\d{4,6}$', new_o):
                errors.append(f"Row {row_idx + 1}: No valid O-number selected.")
                continue
            if _o_int(new_o) in confirm_ints:
                errors.append(f"Row {row_idx + 1}: {new_o} is already in use.")
                continue
            reserved_msg = _is_haas_reserved(_o_int(new_o))
            if reserved_msg:
                errors.append(
                    f"Row {row_idx + 1}: {new_o} is a HAAS reserved range.\n"
                    f"  {reserved_msg}\n  Choose a different O-number.")
                continue

            # Warn if inconsistent and user hasn't switched to fix-later range
            chk = self._get_round_check(row_idx)
            if chk and not chk["consistent"]:
                _, __ = self._range_combos[row_idx].currentData()
                if not (30000 <= _o_int(new_o) <= 39999):
                    reply = QMessageBox.question(
                        self,
                        "Round-Size Conflict",
                        f"Row {row_idx + 1}: {chk['conflict_msg']}\n\n"
                        f"Proceed with {new_o} anyway?\n"
                        f"(Choose No to go back and use 'Save as Fix Later' instead.)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return

            if act == _ACT_NEW:
                results.append({
                    "action":       "rename_new",
                    "path":         conflict["path"],
                    "new_o_number": new_o,
                })
            else:
                results.append({
                    "action":          "rename_existing",
                    "existing_id":     conflict["existing_id"],
                    "existing_path":   conflict["existing_path"],
                    "new_o_existing":  new_o,
                    "new_file_path":   conflict["path"],
                })

            confirm_ints.add(_o_int(new_o))   # reserve for subsequent rows

        if errors:
            QMessageBox.warning(self, "Validation", "\n".join(errors))
            return

        self._results = results
        self.accept()

    # ------------------------------------------------------------------

    def get_results(self) -> list:
        return self._results
