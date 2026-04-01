"""
CNC Direct Editor — Table model and filter proxy.
"""

import re
from PyQt6.QtCore import (
    QAbstractTableModel, QSortFilterProxyModel,
    Qt, QModelIndex, pyqtSignal, QRect
)
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QApplication

import direct_database as db
import verifier as _verifier

# ---------------------------------------------------------------------------
# Part-type filter helpers
# ---------------------------------------------------------------------------
_PT = re.IGNORECASE

_2PC_RE = re.compile(r'-*2\s*PC\b', _PT)
_STL_RE = re.compile(r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-?\d*\b', _PT)


def _has_hub(title: str) -> bool:
    """True when parse_title_specs returns a hub height (any HC variant)."""
    s = _verifier.parse_title_specs(title)
    return s is not None and s.get("hc_height_in") is not None


def _is_2pc(title: str) -> bool:
    return bool(_2PC_RE.search(title))


def _is_steel_ring(title: str) -> bool:
    return bool(_STL_RE.search(title))


_PART_TYPE_FILTERS: dict = {
    # Standard = single-piece disc, no hub of any kind (title-based)
    "Standard":    lambda t: not _has_hub(t) and not _is_2pc(t),
    "HC — any":    lambda t: _has_hub(t),
    "HC — 15MM":   lambda t: bool(re.search(
                       r'\b15\s*MM\s*HC\b|\bHC\s*15\s*MM\b', t, _PT)),
    # 2PC: "--2PC", "-2PC", "2PC" anywhere in title
    "2PC":         lambda t: _is_2pc(t),
    "LUG":         lambda t: bool(re.search(r'\bLUG\b',    t, _PT)),
    "STUD":        lambda t: bool(re.search(r'\bSTUD\b',   t, _PT)),
    "STEP":        lambda t: bool(re.search(r'\bSTEP\b',   t, _PT)),
    "SPACER":      lambda t: bool(re.search(r'\bSPACER\b', t, _PT)),
    # Steel Ring: STEEL RING, STL RING, HCS-1, HCS-2, bare HCS
    "Steel Ring":  lambda t: _is_steel_ring(t),
}

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------
COLUMNS = [
    ("o_number",      "O-Number"),
    ("file_name",     "File Name"),
    ("verify_score",  "Score"),
    ("status",        "Status"),
    ("part_type",     "Type"),
    ("program_title", "Title"),
    ("source_folder", "Folder"),
    ("has_dup_flag",  "Dup"),
    ("file_path",     "Path"),
    ("notes",         "Notes"),
    ("verify_status", "Verify"),
]
COL_IDX = {name: i for i, (name, _) in enumerate(COLUMNS)}

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
_BG = {
    "active":       QColor("#12141f"),
    "flagged":      QColor("#2a1a00"),
    "review":       QColor("#1a2a00"),
    "delete":       QColor("#2a0a0a"),
    "shop_special": QColor("#001a2a"),
}
_FG = {
    "active":       QColor("#ccccdd"),
    "flagged":      QColor("#ffcc44"),
    "review":       QColor("#88dd44"),
    "delete":       QColor("#ff6666"),
    "shop_special": QColor("#66ccff"),
}
_STATUS_LABELS = {
    "active":       "ACTIVE",
    "flagged":      "FLAGGED",
    "review":       "REVIEW",
    "delete":       "DELETE",
    "shop_special": "SHOP SPECIAL",
}

def _score_color(score: int) -> QColor:
    if score == 6:     return QColor("#44dd88")
    if score >= 4:     return QColor("#aadd44")
    if score >= 2:     return QColor("#ffaa33")
    return QColor("#ff5555")


_PT = re.IGNORECASE

def _part_type(title: str) -> str:
    """Derive a short part-type label from the program title."""
    if not title:
        return "STD"
    if re.search(r'\b15\s*MM\s*HC\b', title, _PT):
        return "15MM HC"
    if re.search(r'-*2\s*PC\b', title, _PT):
        return "2PC"
    if re.search(r'\bSTEP\b', title, _PT):
        return "STEP"
    if re.search(r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-\d+\b', title, _PT):
        return "STEEL"
    if re.search(r'\bSPACER\b', title, _PT):
        return "SPACER"
    if re.search(r'\bLUG\b', title, _PT):
        return "LUG"
    if re.search(r'\bSTUD\b', title, _PT):
        return "STUD"
    if re.search(r'\bHC\b', title, _PT):
        return "HC"
    return "STD"

_TYPE_COLORS = {
    "STD":    QColor("#778899"),   # steel blue-gray
    "HC":     QColor("#cc88ff"),   # purple
    "15MM HC":QColor("#ff88ff"),   # pink-purple
    "2PC":    QColor("#44ddcc"),   # teal
    "STEP":   QColor("#ffaa44"),   # orange
    "STEEL":  QColor("#ff6688"),   # rose
    "SPACER": QColor("#66ccff"),   # light blue
    "LUG":    QColor("#ddcc44"),   # yellow
    "STUD":   QColor("#ddcc44"),   # yellow
}

_DUP_COLOR  = QColor("#ff9944")
_FONT_BOLD  = QFont("Consolas", 9, QFont.Weight.Bold)
_FONT_SCORE = QFont("Consolas", 9, QFont.Weight.Bold)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DirectFileTableModel(QAbstractTableModel):

    row_count_changed = pyqtSignal(int)

    def __init__(self, db_path: str, scope_folders: list | None = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.scope_folders = scope_folders  # only show files from these folders
        self._rows: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, filters: dict | None = None):
        """Reload from DB applying optional filters dict."""
        self.beginResetModel()
        rows = db.get_all_files(
            self.db_path,
            status           = (filters or {}).get("status"),
            has_dup_flag     = (filters or {}).get("has_dup_flag"),
            score_min        = (filters or {}).get("score_min"),
            score_max        = (filters or {}).get("score_max"),
            source_folder    = (filters or {}).get("source_folder"),
            recent_days      = (filters or {}).get("recent_days"),
            verify_filter    = (filters or {}).get("verify_filter"),
            scope_folders    = self.scope_folders,
            attention_filter = (filters or {}).get("attention_filter"),
        )
        self._rows = list(rows)
        self.endResetModel()
        self.row_count_changed.emit(len(self._rows))

    def get_row_data(self, row: int):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def get_file_id(self, row: int) -> int | None:
        r = self.get_row_data(row)
        return r["id"] if r else None

    # ------------------------------------------------------------------
    # QAbstractTableModel interface
    # ------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None

        row  = self._rows[index.row()]
        col  = index.column()
        key  = COLUMNS[col][0]
        val  = row[key] if key in row.keys() else None
        st   = row["status"] or "active"

        # part_type is virtual — derive from program_title
        if key == "part_type":
            pt = _part_type(row["program_title"] or "" if "program_title" in row.keys() else "")
            if role == Qt.ItemDataRole.DisplayRole:
                return pt
            if role == Qt.ItemDataRole.ForegroundRole:
                return _TYPE_COLORS.get(pt, QColor("#778899"))
            if role == Qt.ItemDataRole.FontRole:
                return _FONT_BOLD
            if role == Qt.ItemDataRole.BackgroundRole:
                return _BG.get(st, QColor("#12141f"))
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if key == "status":
                return _STATUS_LABELS.get(st, st.upper())
            if key == "verify_score":
                return f"{val}/6" if val is not None else "—"
            if key == "has_dup_flag":
                return "[DUP]" if val else ""
            if key == "source_folder":
                import os
                return os.path.basename(val) if val else "—"
            if key == "file_path":
                return val or ""
            if key == "notes":
                # Show first 80 chars, no newlines
                n = (val or "").replace("\n", " ")
                return n[:80] + ("…" if len(n) > 80 else "")
            return str(val) if val is not None else ""

        if role == Qt.ItemDataRole.ForegroundRole:
            if key == "verify_score":
                return _score_color(val or 0)
            if key == "has_dup_flag" and val:
                return _DUP_COLOR
            if key == "status":
                return _FG.get(st, QColor("#ccccdd"))
            return _FG.get(st, QColor("#ccccdd"))

        if role == Qt.ItemDataRole.BackgroundRole:
            return _BG.get(st, QColor("#12141f"))

        if role == Qt.ItemDataRole.FontRole:
            if key in ("o_number", "verify_score", "has_dup_flag"):
                return _FONT_BOLD
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            if key == "file_path":
                return val
            if key == "source_folder":
                return val
            if key == "notes":
                return val
            if key == "verify_status":
                return val
            return None

        if role == Qt.ItemDataRole.UserRole:
            return dict(row)

        return None


# ---------------------------------------------------------------------------
# Verify-status column delegate — colored PASS/FAIL/NF tokens
# ---------------------------------------------------------------------------

_TOK_PASS_COLOR = QColor("#44dd88")   # green
_TOK_FAIL_COLOR = QColor("#ff5555")   # red
_TOK_NF_COLOR   = QColor("#445566")   # muted blue-gray
_TOK_2PC_COLOR  = QColor("#66aaff")   # blue — RC/HB/IH tokens
_TOK_FONT       = QFont("Consolas", 9)


def _token_color(tok: str) -> QColor:
    tu = tok.upper()
    if tu.endswith(":PASS"):
        return _TOK_PASS_COLOR
    if tu.endswith(":FAIL"):
        return _TOK_FAIL_COLOR
    if tu.startswith(("RC:", "HB:", "IH:")):
        return _TOK_2PC_COLOR
    return _TOK_NF_COLOR


class VerifyStatusDelegate(QStyledItemDelegate):
    """Renders each token in a verify_status string with its own color."""

    _PAD  = 4   # horizontal padding between tokens (px)
    _LPAD = 4   # left padding inside cell

    def paint(self, painter: QPainter, option, index):
        # Draw background (handles selection highlight too)
        painter.save()
        style = QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        tokens = text.split()

        fm   = painter.fontMetrics()
        x    = option.rect.left() + self._LPAD
        y    = option.rect.top() + (option.rect.height() - fm.height()) // 2 + fm.ascent()

        painter.setFont(_TOK_FONT)
        for tok in tokens:
            color = _token_color(tok)
            # Dim everything when the row is selected so it stays readable
            if option.state & QStyle.StateFlag.State_Selected:
                color = color.lighter(130)
            painter.setPen(color)
            painter.drawText(x, y, tok)
            x += fm.horizontalAdvance(tok) + self._PAD

        painter.restore()

    def sizeHint(self, option, index):           # noqa: N802
        text   = index.data(Qt.ItemDataRole.DisplayRole) or ""
        tokens = text.split()
        fm     = option.fontMetrics
        w = self._LPAD + sum(fm.horizontalAdvance(t) + self._PAD for t in tokens)
        return QRect(0, 0, max(w, 80), option.rect.height()).size()
