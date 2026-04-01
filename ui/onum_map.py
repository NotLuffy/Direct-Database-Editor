"""
O-Number Map dialog.
Shows used / available O-numbers by round size and across the full O00001-O99999 space.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QTableView, QAbstractItemView, QLabel, QLineEdit, QPushButton,
    QHeaderView, QComboBox
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
)
from PyQt6.QtGui import QColor, QFont

import direct_database as db
from utils import O_NUMBER_RANGES


# Status colours (used in the table rows)
_STATUS_BG = {
    "active":  QColor("#1a1d2e"),
    "flagged": QColor("#2a2200"),
    "review":  QColor("#1a2a00"),
    "delete":  QColor("#2a1212"),
}
_AVAIL_BG   = QColor("#0e0f18")
_AVAIL_FG   = QColor("#333355")
_STATUS_FG  = {
    "active":  QColor("#aaaacc"),
    "flagged": QColor("#ffcc44"),
    "review":  QColor("#88dd44"),
    "delete":  QColor("#ff6666"),
}


class _OnumModel(QAbstractTableModel):
    """
    Virtual table model for O-number ranges.
    Rows = every integer in [lo, hi]; columns = O#, Status, Program Title, Verify.
    """

    COLS = ["O-Number", "Status", "Program Title", "Verify Status"]

    def __init__(self, lo: int, hi: int, used: dict, parent=None):
        super().__init__(parent)
        self._lo   = lo
        self._hi   = hi
        self._used = used   # {int o_num: {status, program_title, verify_status, proven}}
        self._nums = list(range(lo, hi + 1))

    def rowCount(self, parent=QModelIndex()):
        return len(self._nums)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.COLS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        n    = self._nums[index.row()]
        info = self._used.get(n)
        col  = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return f"O{n:05d}"
            if info is None:
                return "" if col > 0 else f"O{n:05d}"
            if col == 1:
                proven = "★ " if info.get("proven") else ""
                return proven + info.get("status", "").upper()
            if col == 2:
                return info.get("program_title") or ""
            if col == 3:
                vs = info.get("verify_status") or ""
                # Condense to FAIL tokens or PASS
                tokens = vs.split()
                fails  = [t for t in tokens if ":FAIL" in t]
                warns  = [t for t in tokens if ":WARN" in t]
                if fails:   return " ".join(fails + warns)
                if warns:   return " ".join(warns)
                if any(":PASS" in t for t in tokens): return "PASS"
                return vs
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if info is None:
                return _AVAIL_FG
            vs = info.get("verify_status") or ""
            if col == 3:
                if "FAIL" in vs:  return QColor("#ff6666")
                if "WARN" in vs:  return QColor("#ffaa44")
                if "PASS" in vs:  return QColor("#66dd66")
            return _STATUS_FG.get(info.get("status", ""), QColor("#cccccc"))

        if role == Qt.ItemDataRole.BackgroundRole:
            if info is None:
                return _AVAIL_BG
            return _STATUS_BG.get(info.get("status", ""), QColor("#1a1d2e"))

        if role == Qt.ItemDataRole.UserRole:
            return n   # raw O-number integer for navigation

        return None


def _make_tab(lo: int, hi: int, used: dict) -> QWidget:
    """Build a single round-size tab widget."""
    w   = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(4)

    # Stats bar
    total  = hi - lo + 1
    n_used = sum(1 for n in range(lo, hi + 1) if n in used)
    stats  = QLabel(
        f"Range: O{lo:05d} – O{hi:05d}   |   "
        f"Used: {n_used:,}   Available: {total - n_used:,}   Total: {total:,}"
    )
    stats.setStyleSheet("color: #888899; font-size: 11px;")
    lay.addWidget(stats)

    # Filter row
    filter_row = QHBoxLayout()
    filter_row.setSpacing(6)

    search = QLineEdit()
    search.setPlaceholderText("Search O# or title…")
    search.setStyleSheet(
        "QLineEdit { background:#1a1a2a; color:#cccccc; border:1px solid #444466;"
        " border-radius:3px; padding:2px 6px; font-size:11px; }"
    )
    filter_row.addWidget(search)

    show_combo = QComboBox()
    show_combo.addItems(["All", "Used only", "Available only"])
    show_combo.setStyleSheet(
        "QComboBox { background:#1a1a2a; color:#cccccc; border:1px solid #444466;"
        " border-radius:3px; padding:2px 6px; font-size:11px; }"
        "QComboBox QAbstractItemView { background:#1a1a2a; color:#cccccc;"
        " selection-background-color:#333355; }"
    )
    filter_row.addWidget(show_combo)
    lay.addLayout(filter_row)

    # Table
    model = _OnumModel(lo, hi, used)
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setFilterKeyColumn(-1)   # search all columns
    proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    table = QTableView()
    table.setModel(proxy)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(22)
    table.setShowGrid(False)
    table.setSortingEnabled(True)
    table.horizontalHeader().setStretchLastSection(True)
    table.setColumnWidth(0, 90)
    table.setColumnWidth(1, 110)
    table.setColumnWidth(2, 280)
    table.setStyleSheet("""
        QTableView {
            background: #0e0f18; color: #cccccc;
            border: none; font-size: 11px;
            selection-background-color: #2d3250;
        }
        QTableView QHeaderView::section {
            background: #12131f; color: #8888aa; font-size: 11px;
            border: none; padding: 4px;
        }
    """)
    font = QFont("Consolas", 10)
    font.setStyleHint(QFont.StyleHint.Monospace)
    table.setFont(font)
    lay.addWidget(table)

    # Wire search
    def _on_search(txt):
        proxy.setFilterFixedString(txt)

    def _on_show(idx):
        # Rebuild model with filtered number list
        if idx == 0:
            nums = list(range(lo, hi + 1))
        elif idx == 1:
            nums = [n for n in range(lo, hi + 1) if n in used]
        else:
            nums = [n for n in range(lo, hi + 1) if n not in used]
        new_model = _OnumModel.__new__(_OnumModel)
        _OnumModel.__init__(new_model, lo, hi, used)
        new_model._nums = nums
        proxy.setSourceModel(new_model)

    search.textChanged.connect(_on_search)
    show_combo.currentIndexChanged.connect(_on_show)

    return w


class OnumMapDialog(QDialog):
    """O-Number Map — used/available by round size and across full range."""

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("O-Number Map")
        self.resize(900, 620)
        self.setStyleSheet("""
            QDialog    { background: #12131f; color: #cccccc; }
            QTabWidget::pane { border: none; background: #12131f; }
            QTabBar::tab { background: #1a1d2e; color: #888899; padding: 6px 14px;
                           font-size: 11px; border-radius: 3px 3px 0 0; margin-right: 2px; }
            QTabBar::tab:selected { background: #2d3250; color: #cccccc; font-weight: bold; }
            QTabBar::tab:hover    { background: #252840; color: #aaaacc; }
        """)

        # Load all used O-numbers from DB
        conn = db.get_connection(db_path)
        rows = conn.execute(
            "SELECT o_number, status, program_title, verify_status, proven "
            "FROM files WHERE o_number IS NOT NULL"
        ).fetchall()
        conn.close()

        # Build lookup: int → dict
        used: dict = {}
        for r in rows:
            try:
                n = int(r["o_number"][1:])   # "O57286" → 57286
            except (ValueError, IndexError, TypeError):
                continue
            used[n] = {
                "status":        r["status"],
                "program_title": r["program_title"],
                "verify_status": r["verify_status"],
                "proven":        r["proven"],
            }

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        lay.addWidget(tabs)

        # Per-round-size tabs
        for rs, (lo, hi) in sorted(O_NUMBER_RANGES.items()):
            label = f"{rs}\""
            tabs.addTab(_make_tab(lo, hi, used), label)

        # "All O-Numbers" tab — only used O-numbers by default, full range searchable
        all_used_nums = sorted(used.keys())
        all_tab = self._build_all_tab(used, all_used_nums)
        tabs.addTab(all_tab, "All O#")

    def _build_all_tab(self, used: dict, all_used_nums: list) -> QWidget:
        """All O-Numbers tab: shows used O-numbers with option to show full range."""
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        stats = QLabel(
            f"O00001 – O99999   |   "
            f"Used: {len(used):,}   Available: {99999 - len(used):,}   "
            f"Total range: 99,999"
        )
        stats.setStyleSheet("color: #888899; font-size: 11px;")
        lay.addWidget(stats)

        note = QLabel(
            "Showing used O-numbers by default.  "
            "Use 'Show all' to display the full range (99,999 rows — may be slow)."
        )
        note.setStyleSheet("color: #555577; font-size: 10px;")
        lay.addWidget(note)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        search = QLineEdit()
        search.setPlaceholderText("Jump to O-number or search title…")
        search.setStyleSheet(
            "QLineEdit { background:#1a1a2a; color:#cccccc; border:1px solid #444466;"
            " border-radius:3px; padding:2px 6px; font-size:11px; }"
        )
        ctrl.addWidget(search)

        show_all_btn = QPushButton("Show all 99,999")
        show_all_btn.setStyleSheet(
            "QPushButton { background:#2a1a2a; color:#cc88ff; border:1px solid #553355;"
            " border-radius:3px; padding:3px 12px; font-size:11px; }"
            "QPushButton:hover { background:#3a2a3a; }"
        )
        ctrl.addWidget(show_all_btn)
        lay.addLayout(ctrl)

        # Model starts with used-only
        model = _OnumModel(1, 99999, used)
        model._nums = all_used_nums[:]   # override to show only used

        proxy = QSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.setFilterKeyColumn(-1)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        table = QTableView()
        table.setModel(proxy)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(22)
        table.setShowGrid(False)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 90)
        table.setColumnWidth(1, 110)
        table.setColumnWidth(2, 280)
        table.setStyleSheet("""
            QTableView {
                background: #0e0f18; color: #cccccc;
                border: none; font-size: 11px;
                selection-background-color: #2d3250;
            }
            QTableView QHeaderView::section {
                background: #12131f; color: #8888aa; font-size: 11px;
                border: none; padding: 4px;
            }
        """)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        table.setFont(font)
        lay.addWidget(table)

        def _on_search(txt):
            proxy.setFilterFixedString(txt)

        def _on_show_all():
            note.setText("Loading full range — scroll to browse 99,999 rows.")
            model._nums = list(range(1, 100000))
            model.layoutChanged.emit()
            show_all_btn.setEnabled(False)
            show_all_btn.setText("Showing all 99,999")

        search.textChanged.connect(_on_search)
        show_all_btn.clicked.connect(_on_show_all)

        return w
