"""
CNC Direct Editor — Table model and filter proxy.
"""

import re
from functools import lru_cache
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
_STL_RE = re.compile(r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-?\d*\b|\bSTEEL\s+S-\d+\b', _PT)


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
    "Standard":    lambda t: not _has_hub(t) and not _is_2pc(t)
                             and not re.search(r'\bSTEP\b', t, _PT),
    "HC — any":    lambda t: _has_hub(t),
    "HC — 15MM":   lambda t: bool(re.search(
                       r'\b15\s*MM\s*HC\b|\bHC\s*15\s*MM\b', t, _PT)),
    # 2PC: "--2PC", "-2PC", "2PC" anywhere in title (with hub = 2PC HC)
    "2PC":         lambda t: _is_2pc(t),
    "2PC HC":      lambda t: _is_2pc(t) and _has_hub(t),
    "LUG":         lambda t: bool(re.search(r'\bLUG\b',    t, _PT)),
    "STUD":        lambda t: bool(re.search(r'\bSTUD\b',   t, _PT)),
    # STEP: title-based — re.search is case-insensitive, word boundary on both sides
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
    ("line_count",    "Lines"),
    ("status",        "Status"),
    ("part_type",     "Type"),
    # Title-derived spec identifiers (virtual columns, parsed from program_title)
    ("spec_round",    "Round"),
    ("spec_thick",    "Thick"),
    ("spec_cb",       "CB"),
    ("spec_ob",       "OB"),
    ("spec_hub",      "Hub"),
    ("program_title", "Title"),
    ("source_folder", "Folder"),
    ("has_dup_flag",  "Dup"),
    ("file_path",     "Path"),
    ("notes",         "Notes"),
    ("verify_status", "Verify"),
]
COL_IDX = {name: i for i, (name, _) in enumerate(COLUMNS)}

# Virtual spec columns whose values are parsed from the title, not stored in the DB
_SPEC_COLS = ("spec_round", "spec_thick", "spec_cb", "spec_ob", "spec_hub")

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
    if score >= 8:     return QColor("#44dd88")
    if score >= 5:     return QColor("#aadd44")
    if score >= 3:     return QColor("#ffaa33")
    return QColor("#ff5555")


# Scored-token states in verify_status (PASS/FAIL/NF, optional override *).
# RC/HB/IH/STEP/SD informational tokens end in a value, so they are excluded.
_SCORED_TOK_RE = re.compile(r':(PASS|FAIL|NF)\*?\b')


def _score_xn(vstatus: str) -> tuple[int, int]:
    """(passed, applicable) parsed from a verify_status string — the dynamic
    denominator. Non-applicable checks were omitted by the scorer, so they
    simply aren't present here."""
    states = _SCORED_TOK_RE.findall(vstatus or "")
    return sum(1 for s in states if s == "PASS"), len(states)


def _score_ratio_color(passed: int, total: int) -> QColor:
    if not total:
        return QColor("#778899")
    r = passed / total
    if r >= 0.95:  return QColor("#44dd88")
    if r >= 0.62:  return QColor("#aadd44")
    if r >= 0.37:  return QColor("#ffaa33")
    return QColor("#ff5555")


_PT = re.IGNORECASE

def _part_type(title: str, vstatus: str = "") -> str:
    """Derive a short part-type label from the program title.

    vstatus: verify_status string from DB — used to detect 2PC hub presence
    when it is not explicitly stated in the title (IH: token = implicit hub).
    Priority order: STEP > 2PC HC > 2PC > 15MM HC > STEEL > SPACER > LUG > STUD > HC > STD
    """
    if not title:
        return "STD"

    # STEP takes top priority — some STEP programs also have HC or 2PC in the title.
    # Geometry wins: a STEP: token (same-side counterbore detected in the G-code)
    # classifies as STEP even when the title carries no "STEP" keyword (or says HC).
    if re.search(r'\bSTEP\b', title, _PT) or re.search(r'\bSTEP:\d', vstatus):
        return "STEP"

    # 2PC with hub detection
    if re.search(r'-*2\s*PC\b', title, _PT):
        # Check title for HC keyword (explicit — trust the title)
        hub_in_title = _has_hub(title)
        # Check verify_status for implicit hub token (IH:N.NNN").
        # Only classify as "2PC HC" when BOTH:
        #   1. IH ≥ 0.40" — true 0.50" HC hub (not small 0.22" mating hub)
        #   2. RC: token present — the 0.30" recess was also cut on OP1
        # A program with IH ≈ 0.22" is a regular 2PC mating hub, not a 2PC HC.
        hub_in_vstatus = False
        ih_m = re.search(r'\bIH:(\d+\.\d+)"?', vstatus)
        if ih_m:
            ih_val = float(ih_m.group(1))
            has_rc = bool(re.search(r'\bRC:\d+\.\d+', vstatus))
            # True 2PC HC: hub ≥ 0.40" (≈ standard 0.50" HC) AND recess detected
            if ih_val >= 0.40 and has_rc:
                hub_in_vstatus = True
        if hub_in_title or hub_in_vstatus:
            return "2PC HC"
        return "2PC"

    if re.search(r'\b15\s*MM\s*HC\b', title, _PT):
        return "15MM HC"
    if re.search(r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-?\d*\b|\bSTEEL\s+S-\d+\b', title, _PT):
        return "STEEL"
    if re.search(r'\bSPACER\b', title, _PT):
        return "SPACER"
    if re.search(r'\bLUG\b', title, _PT):
        return "LUG"
    if re.search(r'\bSTUD\b', title, _PT):
        return "STUD"
    specs = _verifier.parse_title_specs(title)
    if specs is not None and specs.get("hc_height_in") is not None:
        return "HC"
    return "STD"

@lru_cache(maxsize=8192)
def _spec_display(title: str, vstatus: str = "") -> dict:
    """Parsed title spec formatted for the grid's identifier columns.

    Returns a dict keyed by the virtual column names in _SPEC_COLS, each a
    display string ("" when not applicable). Cached by (title, vstatus) —
    parse_title_specs is regex-heavy and data() is called per cell on repaint.

    vstatus carries the verify tokens; a STEP: token (same-side counterbore
    detected in the G-code) means the second bore is a counterbore, not a hub —
    so it shows in the OB column and the Hub column is blanked.
    """
    if not title:
        return {}
    try:
        sp = _verifier.parse_title_specs(title) or {}
    except Exception:
        return {}

    rs = sp.get("round_size_in")
    cb = sp.get("cb_mm")
    ob = sp.get("ob_mm")
    th = sp.get("length_in")
    hc = sp.get("hc_height_in")

    # STEP: geometry (STEP: token) wins over the title; fall back to "STEP" keyword
    # and the parsed step_mm. The second bore is then a counterbore, not a hub.
    step_m  = re.search(r'\bSTEP:(\d+(?:\.\d+)?)', vstatus or "")
    is_step = bool(step_m) or bool(re.search(r'\bSTEP\b', title, _PT))
    if step_m:
        second = float(step_m.group(1))      # counterbore mm from the G-code
    elif ob is not None:
        second = ob
    else:
        second = sp.get("step_mm")           # title-parsed counterbore (STEP titles)

    # Thickness: mirror export_xlsx — MM when parsed from an mm value, else inches
    if th is None:
        th_disp = ""
    elif sp.get("length_from_mm"):
        mm = th * 25.4
        th_disp = f"{round(mm):.0f}MM" if abs(mm - round(mm)) < 0.1 else f"{mm:.1f}MM"
    else:
        th_disp = f'{th:.3f}"'

    # Hub: a STEP has no hub; otherwise 15MM HC gets a friendly label, else inches
    if is_step or hc is None:
        hub_disp = ""
    elif abs(hc * 25.4 - 15.0) < 0.15:
        hub_disp = "15MM"
    else:
        hub_disp = f'{hc:.3f}"'

    return {
        "spec_round": f'{rs:.2f}"' if rs else "",
        "spec_thick": th_disp,
        "spec_cb":    f"{cb:.1f}" if cb is not None else "",
        "spec_ob":    f"{second:.1f}" if second is not None else "",
        "spec_hub":   hub_disp,
    }


_TYPE_COLORS = {
    "STD":    QColor("#778899"),   # steel blue-gray
    "HC":     QColor("#cc88ff"),   # purple
    "15MM HC":QColor("#ff88ff"),   # pink-purple
    "2PC":    QColor("#44ddcc"),   # teal
    "2PC HC": QColor("#44ffaa"),   # green-teal (2PC with hub)
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

        # part_type is virtual — derive from program_title + verify_status
        if key == "part_type":
            _title   = row["program_title"] or "" if "program_title" in row.keys() else ""
            _vstatus = row["verify_status"]  or "" if "verify_status"  in row.keys() else ""
            pt = _part_type(_title, _vstatus)
            if role == Qt.ItemDataRole.DisplayRole:
                return pt
            if role == Qt.ItemDataRole.ForegroundRole:
                return _TYPE_COLORS.get(pt, QColor("#778899"))
            if role == Qt.ItemDataRole.FontRole:
                return _FONT_BOLD
            if role == Qt.ItemDataRole.BackgroundRole:
                return _BG.get(st, QColor("#12141f"))
            return None

        # verify_score shown as X/N (N = applicable checks present in verify_status)
        if key == "verify_score":
            _vs = row["verify_status"] or "" if "verify_status" in row.keys() else ""
            n_pass, n_app = _score_xn(_vs)
            if role == Qt.ItemDataRole.DisplayRole:
                return f"{n_pass}/{n_app}" if n_app else "—"
            if role == Qt.ItemDataRole.ForegroundRole:
                return _score_ratio_color(n_pass, n_app)
            if role == Qt.ItemDataRole.FontRole:
                return _FONT_SCORE
            if role == Qt.ItemDataRole.BackgroundRole:
                return _BG.get(st, QColor("#12141f"))
            return None

        # spec_* are virtual — parsed from the title (round/thickness/CB/OB/hub)
        if key in _SPEC_COLS:
            _title = row["program_title"] or "" if "program_title" in row.keys() else ""
            _vstatus = row["verify_status"] or "" if "verify_status" in row.keys() else ""
            if role == Qt.ItemDataRole.DisplayRole:
                return _spec_display(_title, _vstatus).get(key) or "—"
            if role == Qt.ItemDataRole.ForegroundRole:
                return _FG.get(st, QColor("#ccccdd"))
            if role == Qt.ItemDataRole.BackgroundRole:
                return _BG.get(st, QColor("#12141f"))
            if role == Qt.ItemDataRole.FontRole:
                return _FONT_BOLD if key in ("spec_round", "spec_cb") else None
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if key == "status":
                return _STATUS_LABELS.get(st, st.upper())
            if key == "verify_score":
                return f"{val}/8" if val is not None else "—"
            if key == "line_count":
                return str(val) if val else "—"
            if key == "has_dup_flag":
                return "[DUP]" if val else ""
            if key == "source_folder":
                import os
                file_path = row["file_path"] if "file_path" in row.keys() else ""
                if file_path and val:
                    rel = os.path.relpath(os.path.dirname(file_path), val)
                    if rel and rel != ".":
                        return rel
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

    _PAD  = 10  # horizontal padding between tokens (px)
    _LPAD = 4   # left padding inside cell

    def paint(self, painter: QPainter, option, index):
        # Draw background (handles selection highlight too)
        painter.save()
        style = QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        tokens = text.split()

        painter.setFont(_TOK_FONT)
        fm   = painter.fontMetrics()
        x    = option.rect.left() + self._LPAD
        y    = option.rect.top() + (option.rect.height() - fm.height()) // 2 + fm.ascent()

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
        from PyQt6.QtGui import QFontMetrics
        text   = index.data(Qt.ItemDataRole.DisplayRole) or ""
        tokens = text.split()
        fm     = QFontMetrics(_TOK_FONT)
        w = self._LPAD + sum(fm.horizontalAdvance(t) + self._PAD for t in tokens)
        return QRect(0, 0, max(w, 80), option.rect.height()).size()
