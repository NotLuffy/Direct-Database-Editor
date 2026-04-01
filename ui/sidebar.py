"""
CNC Direct Editor — Navigation sidebar.
"""

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont


_STYLE = """
QTreeWidget {
    background: #0f1018;
    border: none;
    color: #aaaacc;
    font-size: 12px;
    outline: none;
}
QTreeWidget::item {
    padding: 4px 8px;
    border-radius: 3px;
}
QTreeWidget::item:selected {
    background: #1e2240;
    color: #ccccff;
}
QTreeWidget::item:hover:!selected {
    background: #181a2a;
}
QTreeWidget::branch {
    background: #0f1018;
}
"""

_SECTION_FONT = QFont()
_SECTION_FONT.setBold(True)
_SECTION_FONT.setPointSize(9)

_ITEM_FONT = QFont()
_ITEM_FONT.setPointSize(10)

_COUNT_COLOR = QColor("#555577")
_SCORE_COLORS = {
    "6":   QColor("#44dd88"),
    "4-5": QColor("#aadd44"),
    "2-3": QColor("#ffaa33"),
    "0-1": QColor("#ff5555"),
}


def _section(label: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem([label])
    item.setFont(0, _SECTION_FONT)
    item.setForeground(0, QColor("#555577"))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
    item.setExpanded(True)
    return item


def _leaf(label: str, key: str, count: int = 0) -> QTreeWidgetItem:
    text = f"{label}  {count}" if count else label
    item = QTreeWidgetItem([text])
    item.setFont(0, _ITEM_FONT)
    item.setData(0, Qt.ItemDataRole.UserRole, key)
    return item


_VERIFY_COLORS = {
    "verify_pass": QColor("#44dd88"),
    "verify_fail": QColor("#ff5555"),
    "verify_none": QColor("#555577"),
}

_ATTENTION_COLORS = {
    "attn_mismatch":      QColor("#ff9944"),   # orange — O# mismatch
    "attn_no_gcode":      QColor("#ff5555"),   # red    — no G/M codes
    "attn_no_eop":        QColor("#ffcc44"),   # yellow — no end-of-program
    "attn_range_conflict":QColor("#ff66aa"),   # pink   — wrong O-number range
    "attn_folder_conflict":QColor("#ff7722"),  # deep orange — same-folder O# conflict
    "attn_shop_special":  QColor("#66ccff"),   # blue   — shop special
}


class DirectSidebar(QTreeWidget):
    """
    Navigation sidebar.  Emits filter_selected(key) when user clicks an item.
    key values:
        "all" | "active" | "flagged" | "review" | "delete" | "shop_special" | "missing"
        "dup_all" | "dup_exact" | "dup_conflict" | "dup_chain" | "dup_derived" | "dup_title"
        "score_6" | "score_45" | "score_23" | "score_01"
        "verify_pass" | "verify_fail" | "verify_none"
        "attn_mismatch" | "attn_no_gcode" | "attn_shop_special"
        "recent_7d"
    """

    filter_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(170)
        self.setMaximumWidth(240)
        self.itemClicked.connect(self._on_clicked)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._build()

    # ------------------------------------------------------------------
    # Build tree
    # ------------------------------------------------------------------

    def _build(self):
        self.clear()
        self._items = {}

        # ── FILES ──
        sec_files = _section("  FILES")
        self.addTopLevelItem(sec_files)
        for key, label in [
            ("all",          "All Files"),
            ("active",       "Active"),
            ("flagged",      "Flagged"),
            ("review",       "Review"),
            ("delete",       "Mark Delete"),
            ("shop_special", "Shop Special"),
            ("missing",      "Missing"),
        ]:
            item = _leaf(label, key)
            if key == "shop_special":
                item.setForeground(0, QColor("#66ccff"))
            sec_files.addChild(item)
            self._items[key] = item

        # ── NEEDS ATTENTION ──
        sec_attn = _section("  NEEDS ATTENTION")
        self.addTopLevelItem(sec_attn)
        for key, label, color_key in [
            ("attn_mismatch",       "O# Mismatch",        "attn_mismatch"),
            ("attn_no_gcode",       "No G-Code",           "attn_no_gcode"),
            ("attn_no_eop",         "No End-of-Program",   "attn_no_eop"),
            ("attn_range_conflict", "Wrong O Range",       "attn_range_conflict"),
            ("attn_folder_conflict","Same-Folder Conflict","attn_folder_conflict"),
            ("attn_shop_special",   "Shop Special",        "attn_shop_special"),
        ]:
            item = _leaf(label, key)
            item.setForeground(0, _ATTENTION_COLORS[color_key])
            sec_attn.addChild(item)
            self._items[key] = item

        # ── DUPLICATES ──
        sec_dups = _section("  DUPLICATES")
        self.addTopLevelItem(sec_dups)
        for key, label in [
            ("dup_all",      "All Duplicates"),
            ("dup_exact",    "Exact Copies"),
            ("dup_conflict", "Name Conflicts"),
            ("dup_chain",    "Backup Chains"),
            ("dup_derived",  "Derived Copies"),
            ("dup_title",    "Title Matches"),
        ]:
            item = _leaf(label, key)
            sec_dups.addChild(item)
            self._items[key] = item

        # ── BY SCORE ──
        sec_score = _section("  BY SCORE")
        self.addTopLevelItem(sec_score)
        for key, label, color in [
            ("score_6",  "Score 6  Perfect", "6"),
            ("score_45", "Score 4–5",        "4-5"),
            ("score_23", "Score 2–3",        "2-3"),
            ("score_01", "Score 0–1",        "0-1"),
        ]:
            item = _leaf(label, key)
            item.setForeground(0, _SCORE_COLORS[color])
            sec_score.addChild(item)
            self._items[key] = item

        # ── BY VERIFY RESULT ──
        sec_verify = _section("  BY VERIFY")
        self.addTopLevelItem(sec_verify)
        for key, label, color_key in [
            ("verify_pass", "All Pass",      "verify_pass"),
            ("verify_fail", "Has Failures",  "verify_fail"),
            ("verify_none", "Not Verified",  "verify_none"),
        ]:
            item = _leaf(label, key)
            item.setForeground(0, _VERIFY_COLORS[color_key])
            sec_verify.addChild(item)
            self._items[key] = item

        # ── RECENT ──
        sec_recent = _section("  RECENT (7 DAYS)")
        self.addTopLevelItem(sec_recent)
        item = _leaf("Recently Edited", "recent_7d")
        item.setForeground(0, QColor("#8899bb"))
        sec_recent.addChild(item)
        self._items["recent_7d"] = item

        self.expandAll()

    # ------------------------------------------------------------------
    # Update counts
    # ------------------------------------------------------------------

    def update_counts(self, status_counts: dict, dup_counts: dict, score_counts: dict,
                      verify_counts: dict | None = None,
                      attention_counts: dict | None = None):
        def _set(key, n):
            item = self._items.get(key)
            if not item:
                return
            label_map = {
                "all":              "All Files",
                "active":           "Active",
                "flagged":          "Flagged",
                "review":           "Review",
                "delete":           "Mark Delete",
                "shop_special":     "Shop Special",
                "missing":          "Missing",
                "dup_all":          "All Duplicates",
                "dup_exact":        "Exact Copies",
                "dup_conflict":     "Name Conflicts",
                "dup_chain":        "Backup Chains",
                "dup_derived":      "Derived Copies",
                "dup_title":        "Title Matches",
                "score_6":          "Score 6  Perfect",
                "score_45":         "Score 4–5",
                "score_23":         "Score 2–3",
                "score_01":         "Score 0–1",
                "verify_pass":      "All Pass",
                "verify_fail":      "Has Failures",
                "verify_none":      "Not Verified",
                "attn_mismatch":        "O# Mismatch",
                "attn_no_gcode":        "No G-Code",
                "attn_no_eop":          "No End-of-Program",
                "attn_range_conflict":  "Wrong O Range",
                "attn_folder_conflict": "Same-Folder Conflict",
                "attn_shop_special":    "Shop Special",
            }
            base = label_map.get(key, key)
            item.setText(0, f"{base}  {n}" if n else base)
            # Keep attention items at their theme color when they have entries
            if key in _ATTENTION_COLORS and n:
                item.setForeground(0, _ATTENTION_COLORS[key])

        _set("all",          status_counts.get("total", 0))
        _set("active",       status_counts.get("active", 0))
        _set("flagged",      status_counts.get("flagged", 0))
        _set("review",       status_counts.get("review", 0))
        _set("delete",       status_counts.get("delete", 0))
        _set("shop_special", status_counts.get("shop_special", 0))
        _set("missing",      status_counts.get("missing", 0))

        _set("dup_all",      dup_counts.get("total", 0))
        _set("dup_exact",    dup_counts.get("exact", 0))
        _set("dup_conflict", dup_counts.get("name_conflict", 0))
        _set("dup_chain",    dup_counts.get("backup_chain", 0))
        _set("dup_derived",  dup_counts.get("derived", 0))
        _set("dup_title",    dup_counts.get("title_match", 0))

        _set("score_6",  score_counts.get("6", 0))
        _set("score_45", score_counts.get("4-5", 0))
        _set("score_23", score_counts.get("2-3", 0))
        _set("score_01", score_counts.get("0-1", 0))

        vc = verify_counts or {}
        _set("verify_pass", vc.get("all_pass", 0))
        _set("verify_fail", vc.get("has_fail", 0))
        _set("verify_none", vc.get("not_verified", 0))

        ac = attention_counts or {}
        _set("attn_mismatch",       ac.get("onum_mismatch",   0))
        _set("attn_no_gcode",       ac.get("no_gcode",        0))
        _set("attn_no_eop",         ac.get("no_eop",          0))
        _set("attn_range_conflict", ac.get("range_conflict",  0))
        _set("attn_folder_conflict",ac.get("folder_conflict", 0))
        _set("attn_shop_special",   status_counts.get("shop_special", 0))

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_clicked(self, item: QTreeWidgetItem, _col: int):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key:
            self.filter_selected.emit(key)

    def select_key(self, key: str):
        item = self._items.get(key)
        if item:
            self.setCurrentItem(item)
