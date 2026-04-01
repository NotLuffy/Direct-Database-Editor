"""
2PC Match dialog.
Find compatible 2-piece CNC program pairs.

Features:
  - Recess | 0.25 Hub tab per side, independent filters, live narrowing
  - Gap column fills in when a row is selected (RC - HB - 0.003")
  - Show All Pairs button: every compatible pair across the loaded data
  - Launched from right-click menu with the selected file pre-loaded
"""

import re

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QSplitter, QAbstractItemView, QFrame, QTextEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

import direct_database as db
from verifier import parse_title_specs

_PAIR_TOL   = 0.005
_HUB_OFFSET = 0.003
_CB_TOL_MM  = 1.0   # CB/OB filter tolerance in mm (tight — 74.4 won't match 73.1)
_THICK_TOL  = 0.15

_ROUND_SIZES = ["All", "5.75", "6.00", "6.25", "6.50", "7.00", "7.50",
                "8.00", "8.50", "9.50", "10.25", "10.50", "13.00"]

# Letter/MM thickness notation used on orders
# Each letter = standalone height (disc + hub for hub piece, disc for recess piece)
_LETTER_TO_IN = {
    "A": 1.00, "B": 1.25, "C": 1.50, "D": 1.75,
    "E": 2.00, "F": 2.25, "G": 2.50, "H": 2.75,
}
# Reverse map for display labels (inch → label)
_IN_TO_LABEL = {v: k for k, v in _LETTER_TO_IN.items()}
_IN_TO_LABEL[0.75] = "20mm"   # 0.75" disc = "20mm" on orders

_THICK_LABEL_RE = re.compile(r'^([A-Ha-h])$|^(\d+(?:\.\d+)?)\s*[Mm][Mm]$')

def _parse_thick_filter(text: str):
    """Accept A–H letter, MM value (20mm or bare integer ≥5 treated as mm), or inch float."""
    s = text.strip()
    if not s:
        return None
    su = s.upper()
    if su in _LETTER_TO_IN:
        return _LETTER_TO_IN[su]
    # Explicit MM suffix: "20mm", "20 MM"
    m = re.match(r'^(\d+(?:\.\d+)?)\s*[Mm][Mm]$', s)
    if m:
        return round(float(m.group(1)) / 25.4, 4)
    # Bare whole number ≥ 5 → treat as mm (20 → 20mm, not 20")
    m2 = re.match(r'^(\d+)$', s)
    if m2:
        val = int(m2.group(1))
        if val >= 5:
            return round(val / 25.4, 4)
    # Decimal or small integer → inches
    try:
        return float(s.rstrip('"'))
    except ValueError:
        return None

def _thick_label(tt) -> str:
    """Return the order-notation label for a given total_thick value, e.g. 'A' or '20mm'."""
    if tt is None:
        return ""
    for in_val, label in _IN_TO_LABEL.items():
        if abs(tt - in_val) < 0.04:
            return label
    return ""

_TOKEN_RE = re.compile(r'\b(RC|HB|IH):(\d+\.\d+)"(\?)?', re.IGNORECASE)

_BG_ROW     = QColor("#12141f")
_BG_ROW_ALT = QColor("#0f1018")
_BG_MATCH   = QColor("#103010")
_FG_NORMAL  = QColor("#ccccdd")
_FG_RC      = QColor("#44ddff")
_FG_HB      = QColor("#ffaa44")
_FG_HB50    = QColor("#cc88ff")
_FG_GAP     = QColor("#66ff88")

# col indices
_COL_ONUM  = 0
_COL_VAL   = 1
_COL_GAP   = 2
_COL_CB    = 3
_COL_OB    = 4
_COL_THICK = 5
_COL_HC    = 6
_COL_STAT  = 7

_COLS = ["O-Number", "RC/HB (in)", "Gap (in)", "CB (mm)", "OB (mm)",
         "Thick (in)", "HC (in)", "Status"]

_TAB_ACTIVE_RC   = ("background:#0d2a3a; border:1px solid #44ddff; color:#44ddff;"
                    "font-weight:bold; padding:4px 14px; border-radius:3px;")
_TAB_ACTIVE_HB   = ("background:#2a1a00; border:1px solid #ffaa44; color:#ffaa44;"
                    "font-weight:bold; padding:4px 14px; border-radius:3px;")
_TAB_ACTIVE_HB50 = ("background:#1e0a2a; border:1px solid #cc88ff; color:#cc88ff;"
                    "font-weight:bold; padding:4px 14px; border-radius:3px;")
_TAB_INACTIVE    = ("background:#111224; border:1px solid #2a2d45; color:#555577;"
                    "padding:4px 14px; border-radius:3px;")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_tokens(vstatus: str) -> dict:
    out = {}
    for m in _TOKEN_RE.finditer(vstatus or ""):
        k = m.group(1).upper()
        out[k] = float(m.group(2))
        if k == "HB":
            out["HB_VAR"] = bool(m.group(3))
    return out


def _fmt_thick(rec: dict) -> str:
    tt   = rec.get("total_thick")
    if tt is None:
        return "Thickness: —"
    disc = rec.get("length_in")
    hc   = rec.get("hc")
    hh   = rec.get("hub_height")
    lbl  = _thick_label(tt)
    lbl_s = f" [{lbl}]" if lbl else ""
    extra = hc or hh
    if extra:
        return (f"Thickness: {tt:.3f}\"{lbl_s}"
                f"  ({disc:.3f}\" disc + {extra:.3f}\" {'HC' if hc else 'hub'})")
    return f"Thickness: {tt:.3f}\"{lbl_s}"


def _total_thick(length_in, extra_in) -> float | None:
    """Total assembled thickness: disc + (HC height or hub height)."""
    if length_in is None:
        return None
    return round(length_in + (extra_in or 0.0), 4)


def _safe_float(text: str):
    try:
        return float(text.strip()) if text.strip() else None
    except ValueError:
        return None


def _are_compatible(rc: float, hb: float) -> bool:
    """RC must be >= HB (hub can't exceed recess), and gap within tolerance."""
    if rc < hb:
        return False
    return abs((hb + _HUB_OFFSET) - rc) <= _PAIR_TOL


def _gap_str(rc: float, hb: float) -> str:
    g = rc - hb - _HUB_OFFSET
    return f"{g:+.4f}\""


def _build_records(all_files: list) -> tuple[list, list]:
    """Return (pieces_a, pieces_b) — non-trash 2PC files with RC or HB tokens."""
    pieces_a, pieces_b = [], []
    for _row in all_files:
        f = dict(_row)
        if f.get("status") in ("trash", "delete"):
            continue
        title   = f.get("program_title") or ""
        vstatus = f.get("verify_status") or ""
        if not re.search(r'-*2PC\b', title, re.IGNORECASE):
            continue
        specs  = parse_title_specs(title)
        tokens = _parse_tokens(vstatus)
        length = specs["length_in"]    if specs else None
        hc     = specs["hc_height_in"] if specs else None
        ih     = tokens.get("IH")   # detected hub height (0.25" typical)
        base = {
            "o_number":   f.get("o_number") or f.get("working_name") or "",
            "title":      title,
            "status":     f.get("status") or "",
            "rc":         tokens.get("RC"),
            "hb":         tokens.get("HB"),
            "hb_var":     tokens.get("HB_VAR", False),
            "round_size": specs["round_size_in"] if specs else None,
            "cb_mm":      specs["cb_mm"]       if specs else None,
            "ob_mm":      specs["ob_mm"]       if specs else None,
            "length_in":  length,
            "hc":         hc,
            "hub_height": ih,
        }
        if base["rc"] is not None:
            r = dict(base)
            # Recess piece total = disc + HC height (HC hub adds to total height)
            r["total_thick"] = _total_thick(length, hc)
            pieces_a.append(r)
        if base["hb"] is not None:
            r = dict(base)
            # Hub piece total = disc + detected hub height (IH token, typically 0.25")
            r["total_thick"] = _total_thick(length, ih)
            pieces_b.append(r)
    return pieces_a, pieces_b


# ──────────────────────────────────────────────────────────────────────────────
# Pairs dialog (all compatible pairs in one table)
# ──────────────────────────────────────────────────────────────────────────────

class _PairsDialog(QDialog):
    """Shows every compatible RC/HB pair across all loaded files."""

    _PCOLS = ["Round", "Piece A (Recess)", "RC (in)", "Piece B (Hub)", "HB (in)",
              "Gap (in)", "CB (mm)", "Thickness"]

    def __init__(self, raw_a: list, raw_b: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("All Compatible 2PC Pairs")
        self.setMinimumSize(900, 520)
        self.setStyleSheet("""
            QDialog, QWidget { background: #0d0e18; color: #ccccdd; }
            QLabel { color: #aaaacc; }
            QTableWidget { background: #0f1018; gridline-color: #1a1d2e;
                           border: 1px solid #2a2d45; }
            QHeaderView::section { background: #1a1d2e; color: #8899bb;
                                   border: none; padding: 4px 6px; font-weight: bold; }
            QPushButton { background: #1a2a3a; border: 1px solid #2a3a4a;
                          color: #44ddff; padding: 5px 12px; border-radius: 4px; }
            QPushButton:hover { background: #223344; }
        """)

        pairs = self._build_pairs(raw_a, raw_b)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 8)

        lbl = QLabel(f"{len(pairs)} compatible pair{'s' if len(pairs) != 1 else ''} found  "
                     "(Gap = RC − HB − 0.003\",  ideal = 0.000\")")
        lbl.setStyleSheet("color:#888899; font-size:11px;")
        root.addWidget(lbl)

        t = QTableWidget(len(pairs), len(self._PCOLS))
        t.setHorizontalHeaderLabels(self._PCOLS)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        hdr = t.horizontalHeader()
        for i in range(len(self._PCOLS) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        t.setStyleSheet("QTableWidget { alternate-background-color: #0f1018; }")

        for row, (a, b) in enumerate(pairs):
            gap = a["rc"] - b["hb"] - _HUB_OFFSET
            cb_s  = f"{a['cb_mm']:.1f}"       if a["cb_mm"]           else "—"
            th_s  = f"{a['total_thick']:.3f}\"" if a.get("total_thick") else "—"
            rs_s  = f"{a['round_size']:.2f}\"" if a["round_size"] else "?"
            cells = [
                rs_s, a["o_number"],
                f"{a['rc']:.4f}\"",
                b["o_number"],
                f"{b['hb']:.4f}\"" + (" ?" if b.get("hb_var") else ""),
                f"{gap:+.4f}\"",
                cb_s, th_s,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setForeground(_FG_RC)
                elif col == 4:
                    item.setForeground(_FG_HB)
                elif col == 5:
                    item.setForeground(_FG_GAP)
                else:
                    item.setForeground(_FG_NORMAL)
                t.setItem(row, col, item)

        root.addWidget(t, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _build_pairs(self, raw_a: list, raw_b: list) -> list:
        pairs = []
        for a in raw_a:
            rc = a.get("rc")
            rs = a.get("round_size")
            if rc is None:
                continue
            for b in raw_b:
                hb = b.get("hb")
                rs2 = b.get("round_size")
                if hb is None:
                    continue
                rs_ok = (rs is None or rs2 is None or abs(rs - rs2) < 0.01)
                if rs_ok and _are_compatible(rc, hb):
                    pairs.append((a, b))
        # Sort: round size → RC value → gap (closest fit first within same RC)
        pairs.sort(key=lambda p: (
            p[0].get("round_size") or 0,
            p[0].get("rc") or 0,
            abs(p[0]["rc"] - p[1]["hb"] - _HUB_OFFSET),
        ))
        return pairs


# ──────────────────────────────────────────────────────────────────────────────
# Side panel
# ──────────────────────────────────────────────────────────────────────────────

class _SidePanel(QWidget):

    def __init__(self, changed_cb, selected_cb, parent=None):
        super().__init__(parent)
        self._changed_cb  = changed_cb
        self._selected_cb = selected_cb
        self._mode = "rc"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._btn_rc   = QPushButton("Recess")
        self._btn_rc.setFixedHeight(28)
        self._btn_rc.clicked.connect(lambda: self._set_mode("rc"))
        self._btn_hb   = QPushButton("0.25\" Hub")
        self._btn_hb.setFixedHeight(28)
        self._btn_hb.clicked.connect(lambda: self._set_mode("hb"))
        self._btn_hb50 = QPushButton("0.50\" Hub+RC")
        self._btn_hb50.setFixedHeight(28)
        self._btn_hb50.clicked.connect(lambda: self._set_mode("hb50"))
        tab_row.addWidget(self._btn_rc)
        tab_row.addWidget(self._btn_hb)
        tab_row.addWidget(self._btn_hb50)
        tab_row.addStretch()
        layout.addLayout(tab_row)

        self._header = QLabel()
        self._header.setStyleSheet("font-weight:bold; font-size:12px; padding:2px 2px;")
        layout.addWidget(self._header)

        filt_row = QHBoxLayout()
        filt_row.setSpacing(6)
        filt_row.addWidget(QLabel("CB (mm):"))
        self._cb_edit = QLineEdit()
        self._cb_edit.setPlaceholderText("e.g. 107")
        self._cb_edit.setFixedWidth(78)
        self._cb_edit.textChanged.connect(self._changed_cb)
        filt_row.addWidget(self._cb_edit)
        filt_row.addWidget(QLabel("OB (mm):"))
        self._ob_edit = QLineEdit()
        self._ob_edit.setPlaceholderText("e.g. 131")
        self._ob_edit.setFixedWidth(78)
        self._ob_edit.textChanged.connect(self._changed_cb)
        filt_row.addWidget(self._ob_edit)
        filt_row.addWidget(QLabel("Thick:"))
        self._thick_edit = QLineEdit()
        self._thick_edit.setPlaceholderText("A  20mm  1.00")
        self._thick_edit.setFixedWidth(68)
        self._thick_edit.textChanged.connect(self._changed_cb)
        filt_row.addWidget(self._thick_edit)
        filt_row.addWidget(QLabel("HC:"))
        self._hc_combo = QComboBox()
        self._hc_combo.addItems(["Any", "HC only", "No HC"])
        self._hc_combo.setFixedWidth(80)
        self._hc_combo.currentTextChanged.connect(self._changed_cb)
        filt_row.addWidget(self._hc_combo)
        filt_row.addStretch()
        layout.addLayout(filt_row)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        for i in range(len(_COLS) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._selected_cb)
        layout.addWidget(self._table, stretch=1)

        self._apply_style("rc")

    def _apply_style(self, mode: str):
        self._mode = mode
        self._btn_rc.setStyleSheet(_TAB_INACTIVE)
        self._btn_hb.setStyleSheet(_TAB_INACTIVE)
        self._btn_hb50.setStyleSheet(_TAB_INACTIVE)
        if mode == "rc":
            self._btn_rc.setStyleSheet(_TAB_ACTIVE_RC)
            self._header.setText("Piece A — Recess (RC)")
            self._header.setStyleSheet(
                "color:#44ddff; font-weight:bold; font-size:12px; padding:2px 2px;")
        elif mode == "hb50":
            self._btn_hb50.setStyleSheet(_TAB_ACTIVE_HB50)
            self._header.setText("Piece B — 0.50\" Hub + Recess (Combo)")
            self._header.setStyleSheet(
                "color:#cc88ff; font-weight:bold; font-size:12px; padding:2px 2px;")
        else:
            self._btn_hb.setStyleSheet(_TAB_ACTIVE_HB)
            self._header.setText("Piece B — 0.25\" Hub (HB)")
            self._header.setStyleSheet(
                "color:#ffaa44; font-weight:bold; font-size:12px; padding:2px 2px;")

    def _set_mode(self, mode: str):
        self._apply_style(mode)
        self._changed_cb()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def table(self) -> QTableWidget:
        return self._table

    def filter_values(self) -> dict:
        return {
            "cb":    _safe_float(self._cb_edit.text()),
            "ob":    _safe_float(self._ob_edit.text()),
            "thick": _parse_thick_filter(self._thick_edit.text()),
            "hc":    self._hc_combo.currentText(),
        }

    def set_thick(self, text: str):
        """Programmatically set the Thick filter without losing signal."""
        self._thick_edit.blockSignals(True)
        self._thick_edit.setText(text)
        self._thick_edit.blockSignals(False)
        self._changed_cb()

    def set_count(self, n: int):
        if self._mode == "rc":
            label_type, prefix = "Recess (RC)", "Piece A"
        elif self._mode == "hb50":
            label_type, prefix = "0.50\" Hub + Recess (Combo)", "Piece B"
        else:
            label_type, prefix = "0.25\" Hub (HB)", "Piece B"
        self._header.setText(
            f"{prefix} — {label_type}   {n} file{'s' if n != 1 else ''}")

    def row_bg(self, row: int, color: QColor):
        for col in range(self._table.columnCount()):
            item = self._table.item(row, col)
            if item:
                item.setBackground(color)

    def set_gap(self, row: int, text: str):
        item = self._table.item(row, _COL_GAP)
        if item:
            item.setText(text)
            item.setForeground(_FG_GAP if text != "—" else _FG_NORMAL)

    def reset_bg(self):
        for row in range(self._table.rowCount()):
            self.row_bg(row, _BG_ROW if row % 2 == 0 else _BG_ROW_ALT)
            self.set_gap(row, "—")


# ──────────────────────────────────────────────────────────────────────────────
# Main dialog
# ──────────────────────────────────────────────────────────────────────────────

class TwoPCMatchDialog(QDialog):

    def __init__(self, db_path: str, parent=None,
                 initial_onum: str = None,
                 initial_rs:   str = None,
                 initial_mode: str = "rc"):
        """
        initial_onum: O-number to pre-select after loading (e.g. "O70015")
        initial_rs:   round-size string to pre-select (e.g. "7.50")
        initial_mode: "rc" or "hb" — which tab the left panel starts on
        """
        super().__init__(parent)
        self.db_path       = db_path
        self._raw_a:  list = []
        self._raw_b:  list = []
        self._rs_val       = None
        self._init_onum    = initial_onum
        self._init_rs      = initial_rs
        self._init_mode    = initial_mode or "rc"

        self.setWindowTitle("2PC Match — Find Compatible Pairs")
        self.setMinimumSize(1180, 720)
        self.setStyleSheet("""
            QDialog, QWidget { background: #0d0e18; color: #ccccdd; }
            QLabel  { color: #aaaacc; }
            QLineEdit, QComboBox {
                background: #1a1d2e; border: 1px solid #2a2d45;
                color: #ccccdd; padding: 3px 5px; border-radius: 3px;
            }
            QTableWidget {
                background: #0f1018; gridline-color: #1a1d2e;
                border: 1px solid #2a2d45; color: #ccccdd;
            }
            QHeaderView::section {
                background: #1a1d2e; color: #8899bb;
                border: none; padding: 4px 6px; font-weight: bold;
            }
            QPushButton {
                background: #1a2a3a; border: 1px solid #2a3a4a;
                color: #aaaacc; padding: 4px 12px; border-radius: 3px;
            }
            QPushButton:hover { background: #223344; }
        """)
        self._build_ui()
        self._load_all()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 8)

        # Shared top bar
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel("Round Size (both sides):"))
        self._rs_combo = QComboBox()
        self._rs_combo.addItems(_ROUND_SIZES)
        self._rs_combo.setFixedWidth(88)
        self._rs_combo.currentTextChanged.connect(self._on_rs_changed)
        top.addWidget(self._rs_combo)

        top.addSpacing(10)
        pairs_btn = QPushButton("Show All Pairs")
        pairs_btn.setStyleSheet(
            "QPushButton { background:#1a2a1a; border:1px solid #336633; color:#66ff88; "
            "padding:4px 12px; border-radius:3px; }"
            "QPushButton:hover { background:#223322; }"
        )
        pairs_btn.clicked.connect(self._on_show_pairs)
        top.addWidget(pairs_btn)

        top.addStretch()
        root.addLayout(top)

        # Order combo notation row
        combo_row = QHBoxLayout()
        combo_row.setSpacing(8)
        combo_lbl = QLabel("Order Combo:")
        combo_lbl.setStyleSheet("color:#aaaacc; font-weight:bold;")
        combo_row.addWidget(combo_lbl)
        self._combo_edit = QLineEdit()
        self._combo_edit.setPlaceholderText("e.g.  A+20mm   B+A   C+B")
        self._combo_edit.setFixedWidth(180)
        self._combo_edit.setStyleSheet(
            "QLineEdit { background:#1a1d2e; border:1px solid #44ddff; "
            "color:#44ddff; padding:3px 5px; border-radius:3px; }"
        )
        self._combo_edit.textChanged.connect(self._on_combo_changed)
        combo_row.addWidget(self._combo_edit)
        combo_note = QLabel(
            "First = hub piece (0.25\" hub),  Second = recess piece.  "
            "Fills Thick filter on both sides.  Letters A–H or MM (20mm=0.75\").  "
            "Gap = RC − HB − 0.003\"  (ideal 0.000\")."
        )
        combo_note.setStyleSheet("color: #555577; font-size: 10px;")
        combo_row.addWidget(combo_note)
        combo_row.addStretch()
        root.addLayout(combo_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a2d45;")
        root.addWidget(sep)

        # Two side panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("QSplitter::handle { background: #2a2d45; }")

        self._left  = _SidePanel(self._refilter_left,  self._on_left_selected)
        self._right = _SidePanel(self._refilter_right, self._on_right_selected)
        self._right._apply_style("hb")

        splitter.addWidget(self._left)
        splitter.addWidget(self._right)
        splitter.setSizes([590, 590])
        root.addWidget(splitter, stretch=1)

        # Details panel
        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        detail_splitter.setHandleWidth(4)
        detail_splitter.setStyleSheet("QSplitter::handle { background: #2a2d45; }")
        detail_splitter.setMaximumHeight(120)

        sel_w = QWidget()
        sel_w.setStyleSheet("QWidget { background:#0a0b14; border:1px solid #1e2030; border-radius:3px; }")
        sel_l = QVBoxLayout(sel_w)
        sel_l.setContentsMargins(6, 4, 6, 4)
        sel_l.setSpacing(2)
        lbl_sel = QLabel("Selected")
        lbl_sel.setStyleSheet("color:#555577; font-size:10px; border:none;")
        sel_l.addWidget(lbl_sel)
        self._detail_sel = QTextEdit()
        self._detail_sel.setReadOnly(True)
        self._detail_sel.setStyleSheet(
            "QTextEdit { background:transparent; border:none; color:#ccccdd; font-size:11px; }")
        self._detail_sel.setPlaceholderText("Select a file to see its specs here.")
        sel_l.addWidget(self._detail_sel, stretch=1)

        match_w = QWidget()
        match_w.setStyleSheet("QWidget { background:#0a140a; border:1px solid #1e301e; border-radius:3px; }")
        match_l = QVBoxLayout(match_w)
        match_l.setContentsMargins(6, 4, 6, 4)
        match_l.setSpacing(2)
        lbl_match = QLabel("Compatible Matches")
        lbl_match.setStyleSheet("color:#336633; font-size:10px; border:none;")
        match_l.addWidget(lbl_match)
        self._detail_matches = QTextEdit()
        self._detail_matches.setReadOnly(True)
        self._detail_matches.setStyleSheet(
            "QTextEdit { background:transparent; border:none; color:#66ff88; font-size:11px; }")
        self._detail_matches.setPlaceholderText("Compatible matches will appear here.")
        match_l.addWidget(self._detail_matches, stretch=1)

        detail_splitter.addWidget(sel_w)
        detail_splitter.addWidget(match_w)
        detail_splitter.setSizes([590, 590])
        root.addWidget(detail_splitter)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.setStyleSheet(
            "QPushButton { background:#1a2a3a; border:1px solid #2a3a4a; "
            "color:#44ddff; padding:5px 12px; border-radius:4px; }"
        )
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _load_all(self):
        all_files = db.get_all_files(self.db_path)
        self._raw_a, self._raw_b = _build_records(all_files)

        # Apply initial round size if provided
        if self._init_rs:
            idx = self._rs_combo.findText(self._init_rs)
            if idx >= 0:
                self._rs_combo.setCurrentIndex(idx)
            else:
                self._on_rs_changed(self._rs_combo.currentText())
        else:
            self._on_rs_changed(self._rs_combo.currentText())

        # Pre-select initial file if provided
        if self._init_onum:
            self._left._apply_style(self._init_mode)
            self._refilter_left()
            self._auto_select(self._left, self._init_onum)

    def _on_rs_changed(self, rs_text: str):
        self._rs_val = None if rs_text == "All" else _safe_float(rs_text)
        self._refilter_left()
        self._refilter_right()

    def _on_combo_changed(self, text: str):
        """Parse 'A+20mm' style notation → set Thick filter on both sides.
        First term = hub piece (left), second term = recess piece (right)."""
        parts = [p.strip() for p in text.split("+") if p.strip()]
        hub_text    = parts[0] if len(parts) >= 1 else ""
        recess_text = parts[1] if len(parts) >= 2 else ""
        # Left side should be hub (HB), right side recess (RC) — switch tabs if needed
        if hub_text and self._left.mode != "hb":
            self._left._set_mode("hb")
        if recess_text and self._right.mode != "rc":
            self._right._set_mode("rc")
        self._left.set_thick(hub_text)
        self._right.set_thick(recess_text)

    def _on_show_pairs(self):
        dlg = _PairsDialog(self._raw_a, self._raw_b, self)
        dlg.exec()

    def _auto_select(self, panel: _SidePanel, onum: str):
        """Select the row matching onum in panel and trigger highlight."""
        t = panel.table
        for row in range(t.rowCount()):
            item = t.item(row, _COL_ONUM)
            if item and item.text().upper() == onum.upper():
                t.selectRow(row)
                t.scrollToItem(item)
                break

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _source(self, mode: str) -> list:
        if mode == "hb50":
            # Combo pieces: have an RC token AND provide a 0.50" hub.
            # Two cases:
            #   (a) Explicit HB ≈ 0.50 token (old-style detected hub OD)
            #   (b) HC 2PC: hub height comes from title HC spec or IH token
            #       (no HB token — hub is the HC itself at 0.50")
            def _is_hb50(r):
                hb = r.get("hb")
                if hb is not None and abs(hb - 0.50) < 0.03:
                    return True
                hc = r.get("hc")
                if hc is not None and abs(hc - 0.50) < 0.06:
                    return True
                ih = r.get("hub_height")
                if ih is not None and abs(ih - 0.50) < 0.06:
                    return True
                return False
            return [r for r in self._raw_a if _is_hb50(r)]
        return self._raw_a if mode == "rc" else self._raw_b

    def _passes(self, rec: dict, fv: dict) -> bool:
        rs = self._rs_val
        if rs is not None:
            if rec["round_size"] is None or abs(rec["round_size"] - rs) > 0.01:
                return False
        if fv["cb"] is not None:
            if rec["cb_mm"] is None or abs(rec["cb_mm"] - fv["cb"]) > _CB_TOL_MM:
                return False
        if fv["ob"] is not None:
            if rec["ob_mm"] is None or abs(rec["ob_mm"] - fv["ob"]) > _CB_TOL_MM:
                return False
        if fv["thick"] is not None:
            tt = rec.get("total_thick")
            # Only hide if thickness IS known and doesn't match.
            # Unknown thickness (None) passes through — user verifies manually.
            if tt is not None and abs(tt - fv["thick"]) > _THICK_TOL:
                return False
        if fv["hc"] == "HC only" and rec["hc"] is None:
            return False
        if fv["hc"] == "No HC" and rec["hc"] is not None:
            return False
        return True

    def _mode_vk_fg(self, mode: str):
        """Return (val_key, foreground_color) for a given panel mode."""
        if mode == "rc":
            return "rc", _FG_RC
        if mode == "hb50":
            return "rc", _FG_HB50   # display RC value; it's what matches the incoming 0.25" HB
        return "hb", _FG_HB

    def _refilter_left(self):
        fv   = self._left.filter_values()
        mode = self._left.mode
        vk, fg = self._mode_vk_fg(mode)
        rows = [r for r in self._source(mode) if self._passes(r, fv)]
        cb_target = fv["cb"]
        rows.sort(key=lambda r: (
            abs((r["cb_mm"] or 0) - cb_target) if cb_target and r["cb_mm"] else 0,
            r[vk] or 0,
        ))
        self._fill(self._left.table, rows, vk, fg)
        self._left.set_count(len(rows))

    def _refilter_right(self):
        fv   = self._right.filter_values()
        mode = self._right.mode
        vk, fg = self._mode_vk_fg(mode)
        rows = [r for r in self._source(mode) if self._passes(r, fv)]
        cb_target = fv["cb"]
        rows.sort(key=lambda r: (
            abs((r["cb_mm"] or 0) - cb_target) if cb_target and r["cb_mm"] else 0,
            r[vk] or 0,
        ))
        self._fill(self._right.table, rows, vk, fg)
        self._right.set_count(len(rows))

    # ------------------------------------------------------------------
    # Table fill
    # ------------------------------------------------------------------

    def _fill(self, table: QTableWidget, records: list, val_key: str, val_fg: QColor):
        table.blockSignals(True)
        table.setRowCount(0)
        for rec in records:
            row = table.rowCount()
            table.insertRow(row)
            val   = rec[val_key]
            var_f = " ?" if val_key == "hb" and rec.get("hb_var") else ""
            vs    = f"{val:.4f}\"{var_f}" if val is not None else "—"
            tt    = rec.get("total_thick")
            lbl   = _thick_label(tt)
            th_s  = (f"{tt:.3f}\" [{lbl}]" if lbl else f"{tt:.3f}\"") if tt is not None else "? (not in title)"
            cells = [
                rec["o_number"], vs, "—",
                f"{rec['cb_mm']:.1f}"  if rec["cb_mm"]  else "—",
                f"{rec['ob_mm']:.1f}"  if rec["ob_mm"]  else "—",
                th_s,
                f"{rec['hc']:.3f}\""   if rec["hc"]     else "—",
                rec["status"].upper(),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, rec)
                if col == _COL_VAL:
                    item.setForeground(val_fg)
                    item.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
                else:
                    item.setForeground(_FG_NORMAL)
                table.setItem(row, col, item)
            bg = _BG_ROW if row % 2 == 0 else _BG_ROW_ALT
            for col in range(table.columnCount()):
                itm = table.item(row, col)
                if itm:
                    itm.setBackground(bg)
        table.blockSignals(False)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_left_selected(self):
        self._handle_selection(self._left, self._right)

    def _on_right_selected(self):
        self._handle_selection(self._right, self._left)

    def _handle_selection(self, src_panel: _SidePanel, dst_panel: _SidePanel):
        sel = src_panel.table.selectedItems()
        if not sel:
            dst_panel.reset_bg()
            self._detail_sel.clear()
            self._detail_matches.clear()
            return

        rec  = src_panel.table.item(sel[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        mode = src_panel.mode
        # hb50 pieces display/match via their RC value (the recess that accepts the incoming hub)
        val  = rec.get("rc" if mode in ("rc", "hb50") else "hb")
        rs   = rec.get("round_size")

        self._detail_sel.setPlainText(self._fmt_selected(rec, mode))

        if val is None:
            token_name = "RC" if mode in ("rc", "hb50") else "HB"
            self._detail_matches.setPlainText(
                f"No {token_name} token — run Verify Dimensions first.")
            dst_panel.reset_bg()
            return

        dst_mode = dst_panel.mode
        src_is_rc = mode in ("rc", "hb50")
        matches  = self._highlight_dst(dst_panel, dst_mode, val, rs, src_is_rc=src_is_rc)
        self._detail_matches.setPlainText(
            self._fmt_matches(matches, mode, val, dst_mode))

        if matches:
            first_onum = matches[0][0]["o_number"]
            for row in range(dst_panel.table.rowCount()):
                item0 = dst_panel.table.item(row, 0)
                if item0 and item0.text() == first_onum:
                    dst_panel.table.scrollToItem(item0)
                    break

    def _highlight_dst(self, dst_panel: _SidePanel, dst_mode: str,
                       ref_val: float, ref_rs, src_is_rc: bool) -> list:
        """Highlight compatible rows.  Returns list of (rec, gap_float) sorted closest-first."""
        table = dst_panel.table
        table.blockSignals(True)
        pending = []   # (abs_gap, gap_float, row, r) for compatible rows

        for row in range(table.rowCount()):
            item0 = table.item(row, 0)
            if not item0:
                continue
            r    = item0.data(Qt.ItemDataRole.UserRole)
            dval = r.get("rc" if dst_mode in ("rc", "hb50") else "hb")
            rs2  = r.get("round_size")
            rs_ok = (ref_rs is None or rs2 is None or abs(ref_rs - rs2) < 0.01)

            compat     = False
            gap_str    = "—"
            gap_float  = None

            if dval is not None and rs_ok:
                if src_is_rc and dst_mode == "hb":
                    # Left RC → right 0.25" HB
                    compat = _are_compatible(ref_val, dval)
                    if compat:
                        gap_float = ref_val - dval - _HUB_OFFSET
                elif not src_is_rc and dst_mode in ("rc", "hb50"):
                    # Left 0.25" HB → right Recess or right 0.50" Hub+RC (match left HB vs right RC)
                    compat = _are_compatible(dval, ref_val)
                    if compat:
                        gap_float = dval - ref_val - _HUB_OFFSET
                # else: incompatible same-type pairing — no highlight

            if compat and gap_float is not None:
                gap_str = f"{gap_float:+.4f}\""
                pending.append((abs(gap_float), gap_float, row, r))

            dst_panel.set_gap(row, gap_str)
            if not compat:
                dst_panel.row_bg(row, _BG_ROW if row % 2 == 0 else _BG_ROW_ALT)

        # Sort by closeness (smallest gap deviation from ideal = 0.000")
        pending.sort(key=lambda x: x[0])

        matched = []
        for rank, (_, gf, row, r) in enumerate(pending):
            # Best match slightly brighter; subsequent matches slightly dimmer
            if rank == 0:
                bg = QColor("#104518")   # brightest — closest fit
            elif rank == 1:
                bg = QColor("#0d3a14")
            else:
                bg = QColor("#0a2d10")   # dimmer — larger gap
            dst_panel.row_bg(row, bg)
            matched.append((r, gf))

        table.blockSignals(False)
        return matched

    # ------------------------------------------------------------------
    # Detail text formatters
    # ------------------------------------------------------------------

    def _fmt_selected(self, rec: dict, mode: str) -> str:
        if mode == "hb50":
            rc_val = rec.get("rc")
            hc_val = rec.get("hc") or rec.get("hub_height")
            rc_s   = f"RC={rc_val:.4f}\"" if rc_val else "no RC token"
            hc_s2  = f"HC={hc_val:.3f}\"" if hc_val else "no HC"
            lines = [
                f"{rec['o_number']}   [{rc_s}  {hc_s2}]",
                f"Title:     {rec['title'] or '—'}",
                f"Round:     {rec['round_size']:.2f}\"" if rec['round_size'] else "Round:     —",
                f"CB:        {rec['cb_mm']:.1f} mm"     if rec['cb_mm']    else "CB:        —",
                f"OB:        {rec['ob_mm']:.1f} mm"     if rec['ob_mm']    else "OB:        —",
                _fmt_thick(rec),
                "Hub:       0.50\" HC (provides hub to mate with 0.25\" HB piece)",
            ]
            return "\n".join(lines)
        token = "RC" if mode == "rc" else "HB"
        val   = rec.get("rc" if mode == "rc" else "hb")
        var_s = " (variable)" if mode == "hb" and rec.get("hb_var") else ""
        lines = [
            (f"{rec['o_number']}   [{token}={val:.4f}\"{var_s}]" if val
             else f"{rec['o_number']}   [no {token} token]"),
            f"Title:     {rec['title'] or '—'}",
            f"Round:     {rec['round_size']:.2f}\"" if rec['round_size'] else "Round:     —",
            f"CB:        {rec['cb_mm']:.1f} mm"     if rec['cb_mm']    else "CB:        —",
            f"OB:        {rec['ob_mm']:.1f} mm"     if rec['ob_mm']    else "OB:        —",
            _fmt_thick(rec),
            f"HC:        {rec['hc']:.3f}\""          if rec['hc']       else "HC:        —",
        ]
        return "\n".join(lines)

    def _fmt_matches(self, matches: list, src_mode: str, ref_val: float, dst_mode: str) -> str:
        """matches = list of (rec, gap_float), already sorted closest-first."""
        dst_token = "RC" if dst_mode == "rc" else "HB"
        if not matches:
            token  = "HB" if src_mode == "rc" else "RC"
            expect = (ref_val - _HUB_OFFSET) if src_mode == "rc" else (ref_val + _HUB_OFFSET)
            return (f"No compatible {token} matches found.\n"
                    f"Expected {token} ≈ {expect:.4f}\" ± {_PAIR_TOL:.3f}\"")

        lines = [f"{len(matches)} compatible {dst_token} match{'es' if len(matches) != 1 else ''}"
                 f" — sorted closest first:\n"]
        for rank, (r, gap_float) in enumerate(matches):
            dval  = r.get("rc" if dst_mode == "rc" else "hb")
            var_s = " (variable)" if dst_mode == "hb" and r.get("hb_var") else ""
            cb_s  = f"  CB={r['cb_mm']:.1f}mm"       if r['cb_mm']    else ""
            tt    = r.get("total_thick")
            th_s  = f"  Thick={tt:.3f}\""             if tt is not None else ""
            hc_s  = f"  HC={r['hc']:.3f}\""           if r['hc']       else ""
            rank_s = "★ BEST" if rank == 0 else f"  #{rank + 1}"
            lines.append(
                f"{rank_s}  {r['o_number']}   {dst_token}={dval:.4f}\"{var_s}"
                f"   gap={gap_float:+.4f}\"{cb_s}{th_s}{hc_s}"
            )
        return "\n".join(lines)
