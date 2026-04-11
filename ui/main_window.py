"""
CNC Direct Editor — Main window.
"""

import os
import re
import json
import shutil
import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGridLayout,
    QTableView, QHeaderView, QAbstractItemView, QLabel, QPushButton,
    QFileDialog, QMessageBox, QToolBar, QStatusBar, QMenu, QDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QInputDialog, QCheckBox,
    QTabWidget, QProgressDialog, QCalendarWidget, QScrollArea,
    QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, QSortFilterProxyModel, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QColor

import direct_database as db
import verifier
from direct_models import DirectFileTableModel, COLUMNS, COL_IDX, _PART_TYPE_FILTERS, VerifyStatusDelegate
from direct_scanner import IndexWorker
from ui.sidebar import DirectSidebar
from ui.filter_bar import FilterBar
from ui.scan_dialog import ScanProgressDialog
from ui.editor_panel import EditorPanel
from ui.dup_panel import DupPanel
from ui.diff_panel import DiffPanel

import verifier as _vfy

_APP_STYLE = """
QMainWindow, QWidget { background: #0d0e18; color: #ccccdd; }
QTableView {
    background: #0f1018; gridline-color: #1a1d2e;
    border: none; color: #ccccdd; font-size: 11px;
    selection-background-color: #1e2240;
}
QHeaderView::section {
    background: #1a1d2e; color: #8899bb;
    border: none; padding: 4px 6px; font-weight: bold; font-size: 11px;
}
QScrollBar:vertical {
    background: #0d0e18; width: 10px; border: none;
}
QScrollBar::handle:vertical {
    background: #2a2d45; border-radius: 4px; min-height: 20px;
}
QToolBar {
    background: #0d0e18; border-bottom: 1px solid #1a1d2e;
    spacing: 4px; padding: 3px 6px;
}
QStatusBar { background: #0a0b14; color: #555577; font-size: 11px; }
QLabel { color: #aaaacc; }
QPushButton {
    background: #1a2030; border: 1px solid #2a2d45;
    color: #aaaacc; padding: 4px 10px; border-radius: 3px; font-size: 11px;
}
QPushButton:hover { background: #1e2840; }
QMenu {
    background: #1a1d2e; color: #ccccdd; border: 1px solid #2a2d45;
}
QMenu::item:selected { background: #2a3055; }
"""


# ---------------------------------------------------------------------------
# Re-verify background worker
# ---------------------------------------------------------------------------

class _ReverifyWorker(QThread):
    progress = pyqtSignal(int, int)   # done, total
    finished = pyqtSignal(int)        # count updated

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from direct_scorer import score_file
        conn = db.get_connection(self.db_path)
        rows = conn.execute(
            "SELECT id, file_path, program_title, o_number FROM files "
            "WHERE last_seen IS NOT NULL ORDER BY id"
        ).fetchall()
        conn.close()

        total   = len(rows)
        updated = 0
        for i, row in enumerate(rows):
            if self._cancelled:
                break
            path = row["file_path"]
            if not os.path.exists(path):
                continue
            try:
                score, vstatus = score_file(path, row["program_title"] or "",
                                            o_number=row["o_number"] or "")
                conn2 = db.get_connection(self.db_path)
                with conn2:
                    conn2.execute(
                        "UPDATE files SET verify_score=?, verify_status=? WHERE id=?",
                        (score, vstatus, row["id"])
                    )
                conn2.close()
                updated += 1
            except Exception:
                pass
            if i % 50 == 0 or i == total - 1:
                self.progress.emit(i + 1, total)

        self.finished.emit(updated)


# ---------------------------------------------------------------------------
# Proxy model — applies free-text search + spec filters over DirectFileTableModel
# ---------------------------------------------------------------------------

class _DirectProxy(QSortFilterProxyModel):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filters: dict = {}

    def set_filters(self, filters: dict):
        self._filters = filters or {}
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent):
        model = self.sourceModel()
        rec = model.get_row_data(source_row)
        if rec is None:
            return True

        f = self._filters
        title = rec["program_title"] or ""

        # Free-text search
        q = (f.get("search") or "").upper()
        if q:
            haystack = " ".join([
                rec["o_number"] or "",
                rec["file_name"] or "",
                rec["program_title"] or "",
                rec["file_path"] or "",
                rec["notes"] or "",
            ]).upper()
            if q not in haystack:
                return False

        # Part type
        pt = f.get("part_type") or ""
        if pt and pt != "All":
            fn = _PART_TYPE_FILTERS.get(pt)
            if fn and not fn(title):
                return False

        # Round size
        rs = f.get("round_size")
        if rs and rs != "All":
            try:
                rs_val = float(rs)
                specs = _vfy.parse_title_specs(title)
                if specs is None or abs((specs.get("round_size_in") or 0) - rs_val) > 0.01:
                    return False
            except ValueError:
                pass

        # CB mm
        cb_str = f.get("cb_mm")
        if cb_str:
            try:
                cb_val = float(cb_str)
                specs = _vfy.parse_title_specs(title)
                if specs is None or specs.get("cb_mm") is None:
                    return False
                if abs(specs["cb_mm"] - cb_val) > 0.05:
                    return False
            except ValueError:
                pass

        # OB mm
        ob_str = f.get("ob_mm")
        if ob_str:
            try:
                ob_val = float(ob_str)
                specs = _vfy.parse_title_specs(title)
                if specs is None or specs.get("ob_mm") is None:
                    return False
                if abs(specs["ob_mm"] - ob_val) > 0.05:
                    return False
            except ValueError:
                pass

        # Thickness: list of labels e.g. ['1.250"', '31.8MM'] — OR logic
        th_list = f.get("thickness")
        if th_list:
            specs = _vfy.parse_title_specs(title)
            th_in = (specs or {}).get("length_in")
            if th_in is None:
                return False
            def _lbl_match(lbl: str, val_in: float) -> bool:
                if lbl.endswith("MM"):
                    return abs(val_in - float(lbl[:-2]) / 25.4) <= 0.1 / 25.4
                if lbl.endswith('"'):
                    return abs(val_in - float(lbl[:-1])) <= 0.002
                return False
            if not any(_lbl_match(lbl, th_in) for lbl in th_list):
                return False

        # Hub height
        hub_str = f.get("hub_height")
        if hub_str:
            specs = _vfy.parse_title_specs(title)
            hc = (specs or {}).get("hc_height_in")
            if hub_str == "none":
                if hc is not None:
                    return False
            else:
                try:
                    hub_val = float(hub_str)
                    if hc is None or abs(hc - hub_val) > 0.005:
                        return False
                except ValueError:
                    pass

        return True


# ---------------------------------------------------------------------------
# Import summary helper
# ---------------------------------------------------------------------------

def _show_import_summary(parent, imported: int, skipped: int,
                          imported_names: list, conflicts_resolved: int,
                          conflicts_skipped: int):
    """Show a popup summarising the result of an Import New operation."""
    lines = []
    if imported:
        lines.append(f"<b style='color:#88ee88'>{imported:,} new file(s) imported</b>")
        if imported_names:
            cap = 60
            names_shown = imported_names[:cap]
            names_html  = "".join(
                f"<br>&nbsp;&nbsp;<span style='font-family:Consolas;font-size:11px;"
                f"color:#aaccff'>{n}</span>"
                for n in names_shown
            )
            if len(imported_names) > cap:
                names_html += f"<br>&nbsp;&nbsp;<i>…and {len(imported_names)-cap:,} more</i>"
            lines.append(names_html)
    else:
        lines.append("<b style='color:#888888'>No new files found</b>")

    lines.append(f"<br><b style='color:#888888'>{skipped:,}</b> already in database (skipped)")

    if conflicts_resolved:
        lines.append(f"<b style='color:#ffcc44'>{conflicts_resolved:,}</b> conflict(s) resolved")
    if conflicts_skipped:
        lines.append(f"<b style='color:#ff8844'>{conflicts_skipped:,}</b> conflict(s) skipped")

    msg = QMessageBox(parent)
    msg.setWindowTitle("Import Complete")
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setText("<br>".join(lines))
    msg.exec()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class DirectMainWindow(QMainWindow):

    def __init__(self, exe_dir: str, parent=None):
        super().__init__(parent)
        self.exe_dir       = exe_dir
        self.config_path   = os.path.join(exe_dir, "direct_editor_config.json")
        self.db_path: str  = ""
        self.scan_folders: list[str] = []
        self._model          = None
        self._proxy          = _DirectProxy(self)
        self._worker         = None
        self._compare_pending: dict | None = None

        self.setWindowTitle("CNC Direct Editor")
        self.setMinimumSize(1200, 720)
        self.setStyleSheet(_APP_STYLE)

        self._build_ui()
        self._load_config()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Toolbar ──
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._folder_lbl = QLabel("  No folder selected")
        self._folder_lbl.setStyleSheet("color: #555577; font-size: 11px;")
        tb.addWidget(self._folder_lbl)

        tb.addSeparator()
        self._add_folder_btn = self._tb_btn("Open Database", "#0a1a2a", "#66ccff")
        self._add_folder_btn.clicked.connect(self._on_add_folder)
        tb.addWidget(self._add_folder_btn)

        self._clear_folders_btn = self._tb_btn("Disconnect", "#1a0a0a", "#ff6666")
        self._clear_folders_btn.clicked.connect(self._on_clear_folders)
        tb.addWidget(self._clear_folders_btn)

        self._rescan_btn = self._tb_btn("Rescan", "#0a1a0a", "#66dd66")
        self._rescan_btn.clicked.connect(self._on_rescan)
        self._rescan_btn.setEnabled(False)
        tb.addWidget(self._rescan_btn)

        self._import_new_btn = self._tb_btn("Import New", "#0a1a08", "#88ee66")
        self._import_new_btn.setToolTip(
            "Scan a folder and import only files not already in the database")
        self._import_new_btn.clicked.connect(self._on_import_new)
        self._import_new_btn.setEnabled(False)
        tb.addWidget(self._import_new_btn)

        self._reverify_btn = self._tb_btn("Re-Verify All", "#0a1a1a", "#44ddcc")
        self._reverify_btn.clicked.connect(self._on_reverify_all)
        self._reverify_btn.setEnabled(False)
        tb.addWidget(self._reverify_btn)

        self._empty_trash_btn = self._tb_btn("Empty Trash", "#2a0a0a", "#ff6655")
        self._empty_trash_btn.setToolTip(
            "Permanently delete all files with status 'delete' from disk and database")
        self._empty_trash_btn.clicked.connect(self._on_empty_trash)
        self._empty_trash_btn.setEnabled(False)
        tb.addWidget(self._empty_trash_btn)

        tb.addSeparator()

        self._filters_btn = self._tb_btn("Filters", "#0a0a1a", "#8899ff")
        self._filters_btn.setCheckable(True)
        self._filters_btn.toggled.connect(self._on_filters_toggle)
        tb.addWidget(self._filters_btn)

        tb.addSeparator()

        self._export_btn = self._tb_btn("Export XLSX", "#1a1a0a", "#ddcc44")
        self._export_btn.clicked.connect(self._on_export_csv)
        self._export_btn.setEnabled(False)
        tb.addWidget(self._export_btn)

        self._daily_report_btn = self._tb_btn("Daily Report", "#1a1a0a", "#ccaa44")
        self._daily_report_btn.setToolTip(
            "Generate an XLSX report of all files created on a chosen date")
        self._daily_report_btn.clicked.connect(self._on_daily_report)
        self._daily_report_btn.setEnabled(False)
        tb.addWidget(self._daily_report_btn)

        self._export_files_btn = self._tb_btn("Export Files", "#0a1a0a", "#55ee88")
        self._export_files_btn.clicked.connect(self._on_export_files)
        self._export_files_btn.setEnabled(False)
        tb.addWidget(self._export_files_btn)

        self._twopc_btn = self._tb_btn("2PC Match", "#0a1a1a", "#44ddcc")
        self._twopc_btn.clicked.connect(self._on_twopc)
        self._twopc_btn.setEnabled(False)
        tb.addWidget(self._twopc_btn)

        self._new_file_btn = self._tb_btn("New File", "#0a1a12", "#44ddaa")
        self._new_file_btn.setToolTip(
            "Create a new G-code file and add it directly to the database")
        self._new_file_btn.clicked.connect(self._on_new_file)
        self._new_file_btn.setEnabled(False)
        tb.addWidget(self._new_file_btn)

        self._settings_btn = self._tb_btn("Settings", "#0a0a1a", "#8899ff")
        self._settings_btn.clicked.connect(self._on_settings)
        tb.addWidget(self._settings_btn)

        # ── Central widget ──
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Filter bar (hidden by default)
        self._filter_bar = FilterBar()
        self._filter_bar.setVisible(False)
        self._filter_bar.filters_changed.connect(self._on_filters_changed)
        root.addWidget(self._filter_bar)

        # Main splitter: sidebar | content
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #1a1d2e; }")
        root.addWidget(splitter, stretch=1)

        # Sidebar
        self._sidebar = DirectSidebar()
        self._sidebar.filter_selected.connect(self._on_sidebar_filter)
        splitter.addWidget(self._sidebar)

        # Right pane: header + table
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self._table_header = QLabel("  Files")
        self._table_header.setStyleSheet(
            "color:#8899bb; font-size:12px; font-weight:bold; "
            "padding:4px 8px; background:#0d0e18; border-bottom:1px solid #1a1d2e;"
        )
        right_lay.addWidget(self._table_header)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._on_header_context_menu)
        # Sensible default widths
        hdr.resizeSection(0, 80)   # O-Number
        hdr.resizeSection(1, 130)  # File Name
        hdr.resizeSection(2, 55)   # Score
        hdr.resizeSection(3, 58)   # Lines
        hdr.resizeSection(4, 70)   # Status
        hdr.resizeSection(5, 65)   # Type
        hdr.resizeSection(6, 220)  # Title
        hdr.resizeSection(7, 100)  # Folder
        hdr.resizeSection(8, 35)   # Dup
        hdr.resizeSection(9, 260)  # Path
        hdr.resizeSection(10, 140) # Notes
        # Verify (last) stretches to fill

        # Colored token delegate for the Verify column
        self._verify_delegate = VerifyStatusDelegate(self._table)
        self._table.setItemDelegateForColumn(COL_IDX["verify_status"], self._verify_delegate)

        # Load verify panel when row selection changes
        self._table.clicked.connect(self._on_table_row_changed)
        self._table.selectionModel().currentChanged.connect(
            lambda cur, _prev: self._on_table_row_changed(cur))
        # Auto-compare when exactly 2 rows are selected (Ctrl+click)
        self._table.selectionModel().selectionChanged.connect(
            lambda _sel, _desel: self._on_selection_changed())

        # ── Vertical splitter: table (top) | panels (bottom) ──
        right_vsplit = QSplitter(Qt.Orientation.Vertical)
        right_vsplit.setHandleWidth(4)
        right_vsplit.setStyleSheet("QSplitter::handle { background: #1a1d2e; }")
        right_vsplit.addWidget(self._table)

        # Bottom tab panel
        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: #0d0e18; }
            QTabBar::tab { background: #1a1d2e; color: #888899; padding: 5px 14px;
                           font-size: 11px; border-radius: 3px 3px 0 0; margin-right: 2px; }
            QTabBar::tab:selected { background: #0d0e18; color: #cccccc; font-weight: bold; }
            QTabBar::tab:hover { background: #252840; color: #aaaacc; }
        """)

        self._editor_panel = EditorPanel(db_path="")
        self._editor_panel.file_saved.connect(self._on_file_saved)
        self._bottom_tabs.addTab(self._editor_panel, "Editor")

        self._diff_panel = DiffPanel()
        self._bottom_tabs.addTab(self._diff_panel, "Diff")

        self._dup_panel = DupPanel(db_path="")
        self._dup_panel.open_editor.connect(self._on_dup_open_editor)
        self._dup_panel.open_diff.connect(self._on_dup_open_diff)
        self._bottom_tabs.addTab(self._dup_panel, "Duplicates")

        from ui.verify_panel import VerifyPanel
        self._verify_panel = VerifyPanel()
        self._bottom_tabs.addTab(self._verify_panel, "Verify")

        from ui.tools_panel import ToolsPanel
        self._tools_panel = ToolsPanel()
        self._tools_panel.recheck_ranges.connect(self._on_recheck_ranges)
        self._tools_panel.auto_rename_range.connect(self._on_auto_rename_o_range)
        self._tools_panel.auto_resolve_dupes.connect(self._on_auto_resolve_dupes)
        self._tools_panel.open_batch_replace.connect(lambda: self._open_batch_replace())
        self._tools_panel.open_feed_audit.connect(lambda: self._open_feed_audit())
        self._tools_panel.open_new_progs_finder.connect(self._open_new_progs_finder)
        self._bottom_tabs.addTab(self._tools_panel, "Tools")

        right_vsplit.addWidget(self._bottom_tabs)
        right_vsplit.setSizes([500, 280])

        right_lay.addWidget(right_vsplit, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([190, 1010])

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("No folder selected — use Add Folder to begin.")

    def _on_header_context_menu(self, pos):
        """Right-click on column header → toggle column visibility."""
        hdr = self._table.horizontalHeader()
        menu = QMenu(self)
        menu.setTitle("Show/Hide Columns")
        from direct_models import COLUMNS
        for i, (_, label) in enumerate(COLUMNS):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(i))
            action.setData(i)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen and chosen.data() is not None:
            col = chosen.data()
            hdr.setSectionHidden(col, not hdr.isSectionHidden(col))
            self._save_config()

    def _tb_btn(self, text: str, bg: str, fg: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton {{ background:{bg}; border:1px solid {fg}33; "
            f"color:{fg}; padding:4px 10px; border-radius:3px; font-size:11px; }}"
            f"QPushButton:hover {{ background:{fg}22; }}"
            f"QPushButton:checked {{ background:{fg}33; border:1px solid {fg}; }}"
        )
        return btn

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _load_config(self):
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
            # Apply any verification limit overrides from config
            overrides = cfg.get("verify_overrides", {})
            if overrides:
                try:
                    import verifier
                    verifier.apply_overrides(overrides)
                except Exception:
                    pass
            folders = cfg.get("scan_folders", [])
            db_path = cfg.get("db_path", "")
            if db_path and os.path.exists(db_path) and folders:
                self.db_path      = db_path
                self.scan_folders = [f for f in folders if os.path.isdir(f)]
                self._on_workspace_ready()
            # Restore hidden columns (applies whether or not a DB was loaded)
            hdr = self._table.horizontalHeader()
            for col in cfg.get("hidden_columns", []):
                if 0 <= col < len(COLUMNS):
                    hdr.setSectionHidden(col, True)
        except Exception:
            pass

    def _save_config(self):
        try:
            hdr = self._table.horizontalHeader()
            hidden = [i for i in range(len(COLUMNS)) if hdr.isSectionHidden(i)]
            # Preserve existing verify_overrides if present
            verify_overrides = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path) as f:
                        existing = json.load(f)
                    verify_overrides = existing.get("verify_overrides", {})
                except Exception:
                    pass
            with open(self.config_path, "w") as f:
                cfg = {
                    "scan_folders":  self.scan_folders,
                    "db_path":       self.db_path,
                    "hidden_columns": hidden,
                }
                if verify_overrides:
                    cfg["verify_overrides"] = verify_overrides
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", "",
            QFileDialog.Option.ShowDirsOnly
        )
        if not folder:
            return
        folder = os.path.normpath(folder)

        # ── Case 1: No database loaded yet ────────────────────────────
        if not self.db_path:
            self._open_db_in_folder(folder)
            return

        # ── Case 2: DB already loaded — ask what the user wants ───────
        existing_db = os.path.join(folder, "condenser_direct.db")
        folder_name = os.path.basename(folder)

        if folder in self.scan_folders:
            QMessageBox.information(
                self, "Already Open",
                f'"{folder_name}" is already part of the current database.')
            return

        # Build a clear choice dialog
        msg = QMessageBox(self)
        msg.setWindowTitle("Open Database")
        msg.setIcon(QMessageBox.Icon.Question)

        if os.path.exists(existing_db):
            msg.setText(
                f'"{folder_name}" already has a database.\n\n'
                "What would you like to do?")
            open_btn   = msg.addButton("Open its Database",    QMessageBox.ButtonRole.AcceptRole)
            add_btn    = msg.addButton("Add to Current Scan",  QMessageBox.ButtonRole.ActionRole)
        else:
            msg.setText(
                f'"{folder_name}" has no database yet.\n\n'
                "What would you like to do?")
            open_btn   = msg.addButton("Create New Database Here", QMessageBox.ButtonRole.AcceptRole)
            add_btn    = msg.addButton("Add to Current Scan",       QMessageBox.ButtonRole.ActionRole)

        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked_text = msg.clickedButton().text() if msg.clickedButton() else ""
        if clicked_text in ("Open its Database", "Create New Database Here"):
            # Switch to this folder's database (disconnect current first)
            self.scan_folders = []
            self.db_path = ""
            self._model = None
            self._proxy.setSourceModel(None)
            self._sidebar.update_counts({}, {}, {}, {}, {})
            self._table_header.setText("  Files  (0)")
            self._open_db_in_folder(folder)
        elif clicked_text == "Add to Current Scan":
            # Add folder to current multi-folder scan
            self.scan_folders.append(folder)
            self._save_config()
            self._on_workspace_ready()
            self._on_rescan()

    def _open_db_in_folder(self, folder: str):
        """Connect to a folder's database, clearing stale data from other locations."""
        self.scan_folders = [folder]
        db_path = os.path.join(folder, "condenser_direct.db")
        db.init_schema(db_path)

        # If the DB has records from a different location, wipe it completely
        # and recreate from scratch — delete the file so there's no chance of
        # stale rows surviving (FK issues, swallowed exceptions, etc.)
        existing_folders = db.get_distinct_source_folders(db_path)
        stale = [f for f in existing_folders
                 if os.path.normcase(os.path.normpath(f)) !=
                    os.path.normcase(os.path.normpath(folder))]
        if stale:
            try:
                import os as _os
                if _os.path.exists(db_path):
                    _os.remove(db_path)
            except Exception:
                pass
            db.init_schema(db_path)  # recreate empty DB

        self.db_path = db_path
        self._save_config()
        self._on_workspace_ready()
        self._on_rescan()

    def _on_clear_folders(self):
        if not self.scan_folders:
            return
        reply = QMessageBox.question(
            self, "Clear Folders",
            "Remove all scan folders from this session?\n"
            "The database and files are not deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.scan_folders = []
        self.db_path = ""
        self._model = None
        self._proxy.setSourceModel(None)
        self._folder_lbl.setText("  No folder selected")
        self._rescan_btn.setEnabled(False)
        self._import_new_btn.setEnabled(False)
        self._empty_trash_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._export_files_btn.setEnabled(False)
        self._twopc_btn.setEnabled(False)
        self._reverify_btn.setEnabled(False)
        self._new_file_btn.setEnabled(False)
        self._daily_report_btn.setEnabled(False)
        self._sidebar.update_counts({}, {}, {}, {}, {})
        self._table_header.setText("  Files  (0)")
        self._status_bar.showMessage("Folders disconnected. Database and files are untouched.")
        self._save_config()

    def _on_workspace_ready(self):
        short = ", ".join(os.path.basename(f) for f in self.scan_folders)
        self._folder_lbl.setText(f"  {short}")
        self._rescan_btn.setEnabled(True)
        self._import_new_btn.setEnabled(True)
        self._reverify_btn.setEnabled(True)
        self._empty_trash_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._export_files_btn.setEnabled(True)
        self._twopc_btn.setEnabled(True)
        self._new_file_btn.setEnabled(True)
        self._daily_report_btn.setEnabled(True)

        # Propagate db_path to panels that need it
        self._editor_panel.db_path = self.db_path
        self._dup_panel.db_path    = self.db_path
        self._verify_panel.clear()

        if self._model is None:
            self._model = DirectFileTableModel(self.db_path,
                                               scope_folders=list(self.scan_folders))
            self._proxy.setSourceModel(self._model)
            self._model.row_count_changed.connect(self._on_row_count_changed)
        else:
            # Update scope whenever workspace changes (e.g. folder added)
            self._model.scope_folders = list(self.scan_folders)

        self._refresh_all()

    # ------------------------------------------------------------------
    # Rescan
    # ------------------------------------------------------------------

    def _on_rescan(self):
        if not self.scan_folders or not self.db_path:
            return

        dlg = ScanProgressDialog(self)
        worker = IndexWorker(self.db_path, self.scan_folders, self)
        worker.progress.connect(dlg.on_progress)
        worker.finished.connect(dlg.on_finished)
        worker.finished.connect(lambda *_: self._refresh_all())
        worker.error.connect(dlg.on_error)
        dlg.rejected.connect(worker.cancel)
        worker.start()
        dlg.exec()

    # ------------------------------------------------------------------
    # Import New Files (one-shot folder scan — new hashes only)
    # ------------------------------------------------------------------

    def _on_import_new(self):
        if not self.db_path:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Import From", "",
            QFileDialog.Option.ShowDirsOnly)
        if not folder:
            return
        folder = os.path.normpath(folder)

        from direct_scanner import ImportNewWorker

        self._import_new_btn.setEnabled(False)
        self._import_new_btn.setText("Importing…")

        dlg = QProgressDialog("Scanning for new files…", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Import New Files")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        worker = ImportNewWorker(self.db_path, folder, self)

        def _on_progress(done, total, msg):
            if total > 0:
                dlg.setMaximum(total)
                dlg.setValue(done)
            else:
                dlg.setMaximum(0)   # indeterminate
            dlg.setLabelText(msg)

        def _on_done(imported, skipped, conflicts, imported_names):
            dlg.close()
            self._import_new_btn.setEnabled(True)
            self._import_new_btn.setText("Import New")
            # Add the import folder to scope so imported files are visible
            if folder not in self.scan_folders:
                self.scan_folders.append(folder)
                self._save_config()
                if self._model is not None:
                    self._model.scope_folders = list(self.scan_folders)
            self._refresh_all()

            if conflicts:
                self._show_import_conflicts(conflicts, folder, imported, skipped,
                                            imported_names)
            else:
                _show_import_summary(self, imported, skipped, imported_names, 0, 0)

        def _on_error(msg):
            dlg.close()
            self._import_new_btn.setEnabled(True)
            self._import_new_btn.setText("Import New")
            QMessageBox.critical(self, "Import Error", msg)

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        dlg.canceled.connect(worker.cancel)
        worker.start()
        self._import_new_worker = worker

    def _show_import_conflicts(self, conflicts: list, folder: str,
                               already_imported: int, skipped: int,
                               imported_names: list = None):
        """Show the conflict review dialog; process any confirmed renames."""
        from ui.import_conflict_dialog import ImportConflictDialog
        from direct_scanner import commit_renamed_import, commit_renamed_existing

        cdlg = ImportConflictDialog(conflicts, folder, self.db_path, self)
        if cdlg.exec() != ImportConflictDialog.DialogCode.Accepted:
            _show_import_summary(self, already_imported, skipped,
                                 imported_names or [], 0, len(conflicts))
            return

        results   = cdlg.get_results()
        ok_count  = 0
        fail_msgs = []
        renamed_names = []

        for res in results:
            if res["action"] == "rename_new":
                ok = commit_renamed_import(
                    self.db_path, res["path"], res["new_o_number"], folder)
                label = f"{os.path.basename(res['path'])} → {res['new_o_number']}"
            else:  # rename_existing
                ok = commit_renamed_existing(
                    self.db_path,
                    res["existing_id"],
                    res["new_o_existing"],
                    res["new_file_path"],
                    folder,
                )
                label = (f"{os.path.basename(res['existing_path'])} → "
                         f"{res['new_o_existing']}")

            if ok:
                ok_count += 1
                renamed_names.append(label)
            else:
                fail_msgs.append(label)

        if fail_msgs:
            QMessageBox.warning(
                self, "Some Renames Failed",
                "The following could not be renamed (name already exists?):\n\n"
                + "\n".join(fail_msgs))

        skipped_conflicts = len(conflicts) - len(results)
        all_names = (imported_names or []) + renamed_names
        _show_import_summary(self, already_imported + ok_count, skipped,
                             all_names, ok_count, skipped_conflicts)
        self._refresh_all()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_all(self):
        if not self.db_path or self._model is None:
            return
        current_filters = self._filter_bar.current_filters() if self._filter_bar.isVisible() else {}
        db_filters = {
            "status":       current_filters.get("status"),
            "has_dup_flag": current_filters.get("has_dup_flag"),
            "score_min":    current_filters.get("score_min"),
            "score_max":    current_filters.get("score_max"),
        }
        self._model.refresh({k: v for k, v in db_filters.items() if v is not None})
        self._proxy.set_filters(current_filters)
        self._update_sidebar_counts()
        self._on_row_count_changed(self._proxy.rowCount())
        self._update_filter_spec_data()

    def _update_filter_spec_data(self):
        """Parse all DB titles and feed spec data to the filter bar dropdowns."""
        if not self.db_path:
            return
        try:
            rows = db.get_all_files(self.db_path)
        except Exception:
            return
        specs = []
        for r in rows:
            title = r["program_title"] or ""   # sqlite3.Row — use [] not .get()
            if not title:
                continue
            try:
                s = _vfy.parse_title_specs(title)
            except Exception:
                continue
            if s is None:
                continue
            th_in      = s.get("length_in")
            th_from_mm = bool(s.get("length_from_mm", False))
            hc_in      = s.get("hc_height_in")
            specs.append({
                "rs":         s.get("round_size_in"),
                "cb":         s.get("cb_mm"),
                "ob":         s.get("ob_mm"),
                "th":         th_in,       # always inches
                "th_from_mm": th_from_mm,  # True → was MM in title
                "hc_in":      hc_in,
            })
        self._filter_bar.set_spec_data(specs)

    def _update_sidebar_counts(self):
        if not self.db_path:
            return
        try:
            sf  = self.scan_folders or None
            sc  = db.get_status_counts(self.db_path,    scope_folders=sf)
            dc  = db.get_dup_group_counts(self.db_path, scope_folders=sf)
            sc2 = db.get_score_counts(self.db_path,     scope_folders=sf)
            vc  = db.get_verify_counts(self.db_path,    scope_folders=sf)
            ac  = db.get_attention_counts(self.db_path, scope_folders=sf)
            self._sidebar.update_counts(sc, dc, sc2, vc, ac)
        except Exception:
            pass

    def _on_table_row_changed(self, index):
        """Load the verify panel when the user clicks a row."""
        if self._model is None:
            return
        if not hasattr(self, "_verify_panel"):
            return
        source_idx = self._proxy.mapToSource(index)
        rec = self._model.get_row_data(source_idx.row())
        if rec is None:
            return
        rec = dict(rec)   # sqlite3.Row → dict so .get() works
        self._verify_panel.load(
            rec.get("file_path", ""),
            rec.get("program_title", "") or "",
            rec.get("o_number", "") or "",
        )
        self._bottom_tabs.setCurrentWidget(self._verify_panel)

    def _on_row_count_changed(self, n: int):
        self._table_header.setText(f"  Files  ({n:,})")
        self._status_bar.showMessage(
            f"{n:,} files shown  |  DB: {self.db_path}")

    # ------------------------------------------------------------------
    # Sidebar filter
    # ------------------------------------------------------------------

    def _on_sidebar_filter(self, key: str):
        self._filter_bar.reset()  # clear bar, then apply sidebar selection
        db_filters: dict = {}
        proxy_overrides: dict = {}

        if key == "all":
            pass
        elif key in ("active", "flagged", "review", "delete", "shop_special"):
            db_filters["status"] = key
        elif key == "attn_mismatch":
            db_filters["attention_filter"] = "onum_mismatch"
        elif key == "attn_no_gcode":
            db_filters["attention_filter"] = "no_gcode"
        elif key == "attn_no_eop":
            db_filters["attention_filter"] = "no_eop"
        elif key == "attn_range_conflict":
            db_filters["attention_filter"] = "range_conflict"
        elif key == "attn_folder_conflict":
            db_filters["attention_filter"] = "folder_conflict"
        elif key == "attn_shop_special":
            db_filters["attention_filter"] = "shop_special"
        elif key == "missing":
            # Show files with last_seen=NULL — handled in model via special query
            db_filters["missing"] = True
        elif key == "dup_all":
            db_filters["has_dup_flag"] = 1
            self._dup_panel.load_all_by_type(None)
            self._bottom_tabs.setCurrentWidget(self._dup_panel)
        elif key.startswith("dup_"):
            # Show files + load dup panel filtered by type
            db_filters["has_dup_flag"] = 1
            proxy_overrides["dup_type"] = key.replace("dup_", "")
            group_type = key.replace("dup_", "")
            self._dup_panel.load_all_by_type(group_type)
            self._bottom_tabs.setCurrentWidget(self._dup_panel)
        elif key == "score_7":
            db_filters["score_min"] = 7
            db_filters["score_max"] = 7
        elif key == "score_56":
            db_filters["score_min"] = 5
            db_filters["score_max"] = 6
        elif key == "score_34":
            db_filters["score_min"] = 3
            db_filters["score_max"] = 4
        elif key == "score_02":
            db_filters["score_min"] = 0
            db_filters["score_max"] = 2
        elif key == "recent_7d":
            db_filters["recent_days"] = 7
        elif key in ("verify_pass", "verify_fail", "verify_none"):
            vmap = {"verify_pass": "all_pass", "verify_fail": "has_fail",
                    "verify_none": "not_verified"}
            db_filters["verify_filter"] = vmap[key]

        if self._model:
            # Handle missing specially
            if db_filters.pop("missing", False):
                self.beginResetModel_missing()
            else:
                self._model.refresh(db_filters)
            self._proxy.set_filters(proxy_overrides)
            self._on_row_count_changed(self._proxy.rowCount())

    def beginResetModel_missing(self):
        """Reload only files with last_seen=NULL."""
        if not self.db_path or self._model is None:
            return
        import sqlite3
        conn = db.get_connection(self.db_path)
        rows = conn.execute(
            "SELECT * FROM files WHERE last_seen IS NULL ORDER BY o_number"
        ).fetchall()
        conn.close()
        self._model.beginResetModel()
        self._model._rows = list(rows)
        self._model.endResetModel()
        self._model.row_count_changed.emit(len(self._model._rows))

    # ------------------------------------------------------------------
    # Filter bar
    # ------------------------------------------------------------------

    def _on_filters_toggle(self, checked: bool):
        self._filter_bar.setVisible(checked)
        if not checked:
            self._filter_bar.reset()

    def _on_filters_changed(self, filters: dict):
        if self._model is None:
            return
        db_filters = {
            "status":       filters.get("status"),
            "has_dup_flag": filters.get("has_dup_flag"),
            "score_min":    filters.get("score_min"),
            "score_max":    filters.get("score_max"),
        }
        self._model.refresh({k: v for k, v in db_filters.items() if v is not None})
        self._proxy.set_filters(filters)
        self._on_row_count_changed(self._proxy.rowCount())

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _selected_records(self) -> list[dict]:
        rows = set(idx.row() for idx in self._table.selectedIndexes())
        result = []
        for proxy_row in rows:
            src_row = self._proxy.mapToSource(self._proxy.index(proxy_row, 0)).row()
            rec = self._model.get_row_data(src_row)
            if rec:
                result.append(dict(rec))
        return result

    def _on_context_menu(self, pos):
        recs = self._selected_records()
        if not recs:
            return

        menu = QMenu(self)
        single = len(recs) == 1
        rec = recs[0]

        if single:
            menu.addAction("Edit File",          lambda: self._action_edit(rec))
            menu.addAction("Open File Location", lambda: self._action_open_location(rec))
            menu.addAction("Rename File…",       lambda: self._action_rename(rec))
            menu.addAction("Write Chain Comment",lambda: self._action_chain_comment(rec))
            menu.addSeparator()
            menu.addAction("Compare with…",        lambda: self._action_compare(rec))
            menu.addAction("Show Duplicate Groups", lambda: self._action_show_dups(rec))
            menu.addAction("Revision History…",    lambda: self._action_revisions(rec))
            menu.addAction("View Toolpath…",        lambda: self._action_toolpath(rec))
            menu.addSeparator()
        elif len(recs) == 2:
            menu.addAction("Compare Selected",
                           lambda: self._on_selection_changed())
            menu.addSeparator()

        # Status sub-menu
        status_menu = menu.addMenu("Set Status")
        for st, label in [
            ("active",       "Active"),
            ("flagged",      "Flagged"),
            ("review",       "Review"),
            ("delete",       "Mark Delete"),
            ("shop_special", "Shop Special  (skip verify)"),
            ("verified",     "Verified"),
        ]:
            if st == "verified":
                status_menu.addSeparator()
                status_menu.addAction(label,
                    lambda: self._set_verified_multi(recs))
            else:
                status_menu.addAction(label, lambda s=st: self._set_status_multi(recs, s))

        menu.addSeparator()
        menu.addAction(
            f"Re-Verify {'File' if single else f'{len(recs)} Files'}",
            lambda: self._action_reverify_selected(recs))

        # Override Verify sub-menu (single file only)
        if single:
            override_menu = menu.addMenu("Override Verify")
            for token, label in [
                ("CB", "CB (Center Bore)"),
                ("OB", "OB (Outer Bore)"),
                ("DR", "DR (Drill Depth)"),
                ("OD", "OD (OD Turn)"),
                ("TZ", "TZ (Turning Z-Depth)"),
                ("PC", "PC (P-Code)"),
                ("HM", "HM (Home Position)"),
            ]:
                sub = override_menu.addMenu(label)
                sub.addAction("Override → PASS",
                    lambda t=token: self._apply_verify_override(rec, t, "PASS"))
                sub.addAction("Override → FAIL",
                    lambda t=token: self._apply_verify_override(rec, t, "FAIL"))
            override_menu.addSeparator()
            override_menu.addAction("Clear All Overrides",
                lambda: self._clear_verify_overrides(rec))

        if single:
            menu.addAction("Copy O-Number",   lambda: self._copy_onum(rec))
            menu.addAction("2PC Match…",      lambda: self._action_2pc(rec))
            menu.addAction("O# Map…",         lambda: self._action_onum_map())
            menu.addAction("Batch Replace…",  lambda: self._open_batch_replace(
                title_filter=rec.get("program_title", "")[:6]))
            menu.addAction("Feed Rate Audit…", lambda: self._open_feed_audit(
                file_ids=[r["id"] for r in recs]))
            menu.addSeparator()

        menu.addAction(
            f"Delete {'File' if single else f'{len(recs)} Files'}…",
            lambda: self._action_delete(recs)
        )

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Scope guard — prevents operating on files outside the open folder
    # ------------------------------------------------------------------

    def _path_in_scope(self, path: str) -> bool:
        """Return True if path lives inside one of the current scan folders."""
        if not self.scan_folders:
            return True  # no scope set — allow (shouldn't happen)
        norm = os.path.normcase(os.path.normpath(path))
        for folder in self.scan_folders:
            folder_norm = os.path.normcase(os.path.normpath(folder))
            if norm.startswith(folder_norm + os.sep) or norm == folder_norm:
                return True
        return False

    def _guard_scope(self, rec: dict) -> bool:
        """Show an error and return False if the file is outside the open folder."""
        path = rec.get("file_path", "")
        if self._path_in_scope(path):
            return True
        folder_list = "\n".join(f"  {f}" for f in self.scan_folders)
        QMessageBox.critical(
            self, "Wrong Folder — Operation Blocked",
            f"This file is NOT inside the currently open folder:\n\n"
            f"  {path}\n\n"
            f"Open folder(s):\n{folder_list}\n\n"
            f"Disconnect and open the correct folder before editing."
        )
        return False

    def _action_edit(self, rec: dict, scroll_to_line: int = 0):
        """Open file in the editor panel and switch to the Editor tab."""
        path = rec["file_path"]
        if not self._guard_scope(rec):
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "File Missing", f"Cannot find:\n{path}")
            return
        self._editor_panel.load_file(
            rec["id"], path,
            rec.get("o_number", ""),
            rec.get("verify_status", ""),
            rec.get("verify_score", 0),
            scroll_to_line=scroll_to_line,
        )
        self._bottom_tabs.setCurrentWidget(self._editor_panel)

    def _action_reverify_selected(self, recs: list[dict]):
        """Re-run verification on the selected files and update the DB."""
        from direct_scorer import (score_file, parse_overrides,
                                   apply_overrides_to_status)
        updated = 0
        conn = db.get_connection(self.db_path)
        with conn:
            for rec in recs:
                path  = rec.get("file_path", "")
                title = rec.get("program_title", "") or ""
                onum  = rec.get("o_number", "") or ""
                if not path or not os.path.isfile(path):
                    continue
                try:
                    score, vstatus = score_file(path, title, o_number=onum)
                except Exception:
                    continue
                # Re-apply any existing overrides
                notes = rec.get("notes", "") or ""
                overrides = parse_overrides(notes)
                if overrides:
                    score, vstatus = apply_overrides_to_status(vstatus, overrides)
                conn.execute(
                    "UPDATE files SET verify_score=?, verify_status=? WHERE id=?",
                    (score, vstatus, rec["id"]))
                updated += 1
        conn.close()
        self._status_bar.showMessage(
            f"Re-verified {updated} file(s).")
        # Refresh verify panel for the first selected file
        if recs and hasattr(self, "_verify_panel"):
            first = recs[0]
            self._verify_panel._current_path = ""   # force reload
            self._verify_panel.load(
                first.get("file_path", ""),
                first.get("program_title", "") or "",
                first.get("o_number", "") or "",
            )
            self._bottom_tabs.setCurrentWidget(self._verify_panel)
        self._refresh_all()

    def _action_open_location(self, rec: dict):
        if not self._guard_scope(rec):
            return
        import subprocess
        subprocess.Popen(f'explorer /select,"{rec["file_path"]}"', shell=True)

    def _action_rename(self, rec: dict):
        """Rename file on disk + rewrite internal O-number line + update DB."""
        if not self._guard_scope(rec):
            return
        old_path  = rec["file_path"]
        old_name  = rec["file_name"]
        folder    = os.path.dirname(old_path)

        new_name, ok = QInputDialog.getText(
            self, "Rename File",
            f"Current name: {old_name}\n\nNew O-number (e.g. O65200):",
            text=old_name
        )
        if not ok or not new_name.strip():
            return

        new_name  = new_name.strip()
        # Preserve extension
        _, ext    = os.path.splitext(old_name)
        new_fname = new_name if "." in new_name else new_name + ext
        new_path  = os.path.join(folder, new_fname)

        if os.path.exists(new_path) and new_path != old_path:
            QMessageBox.warning(self, "Name Conflict",
                                f"{new_fname} already exists in that folder.")
            return

        try:
            # Rewrite O-number line inside file before renaming
            self._rewrite_onum_line(old_path, new_name)
            os.rename(old_path, new_path)
            new_onum = re.match(r'(O\d{4,6})', new_name, re.IGNORECASE)
            db.update_file_path_and_name(
                self.db_path, rec["id"], new_path, new_fname,
                new_onum.group(1).upper() if new_onum else new_name.upper()
            )
            self._refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "Rename Failed", str(exc))

    def _rewrite_onum_line(self, path: str, new_onum: str):
        """Replace the first O-number line in file content with new_onum."""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "%" or not stripped:
                continue
            if re.match(r'^O\d{4,6}', stripped, re.IGNORECASE):
                # Preserve any comment after the O-number
                rest = re.sub(r'^O\d{4,6}(?:_\d+)?', "", stripped, flags=re.IGNORECASE)
                lines[i] = new_onum.upper() + rest + "\n"
                break
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def _action_chain_comment(self, rec: dict):
        """Write the file's full name (including _N suffix) into its header comment."""
        if not self._guard_scope(rec):
            return
        path  = rec["file_path"]
        fname = os.path.splitext(rec["file_name"])[0]  # e.g. O65123_1
        if not os.path.exists(path):
            QMessageBox.warning(self, "File Missing", f"Cannot find:\n{path}")
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped == "%" or not stripped:
                    continue
                if re.match(r'^O\d{4,6}', stripped, re.IGNORECASE):
                    # Replace line with full name and preserve existing comment if any
                    existing_comment = re.search(r'\(([^)]*)\)', stripped)
                    comment_body = existing_comment.group(1) if existing_comment else ""
                    # Prepend the filename to the comment
                    new_comment = fname.upper()
                    if comment_body and new_comment not in comment_body:
                        new_comment = f"{fname.upper()} — {comment_body}"
                    lines[i] = f"{rec['o_number']} ({new_comment})\n"
                    break

            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            # Re-analyze and update DB
            from direct_scorer import score_file
            import xxhash
            file_hash, line_count, title, derived = self._reanalyze(path)
            score, vstatus = score_file(path, title, o_number=rec["o_number"])
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
            db.update_file_after_edit(
                self.db_path, rec["id"], file_hash, line_count,
                title, derived, vstatus, score, rec["has_dup_flag"], mtime)
            self._refresh_all()
            QMessageBox.information(self, "Done",
                f"Chain comment written to {rec['file_name']}.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _reanalyze(self, path: str) -> tuple:
        """Hash + header for a file. Returns (hash, lines, title, derived)."""
        import xxhash
        from direct_scanner import _extract_header_info, _count_lines
        h = xxhash.xxh128()
        title = ""
        derived = ""
        header_found = False
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
                if not header_found:
                    title, derived, _internal_o, _has_gc = _extract_header_info(chunk)
                    header_found = True
        return h.hexdigest(), _count_lines(path), title, derived

    def _on_selection_changed(self):
        """Auto-compare when exactly 2 rows are selected (Ctrl+click)."""
        recs = self._selected_records()
        if len(recs) == 2:
            a, b = recs[0], recs[1]
            self._diff_panel.compare(
                a["file_path"], a["file_name"],
                b["file_path"], b["file_name"],
            )
            self._bottom_tabs.setCurrentWidget(self._diff_panel)
            self._status_bar.showMessage(
                f"Diff: {a['file_name']}  vs  {b['file_name']}")

    def _action_compare(self, rec: dict):
        """Two-click diff: first click sets File A, second fires diff panel."""
        if self._compare_pending is None:
            self._compare_pending = rec
            self._status_bar.showMessage(
                f"Compare: File A = {rec['file_name']}  — now select File B from table.")
            return
        a = self._compare_pending
        b = rec
        self._compare_pending = None
        if a["file_path"] == b["file_path"]:
            self._status_bar.showMessage("Compare cancelled: same file selected twice.")
            return
        self._diff_panel.compare(
            a["file_path"], a["file_name"],
            b["file_path"], b["file_name"],
        )
        self._bottom_tabs.setCurrentWidget(self._diff_panel)
        self._status_bar.showMessage(
            f"Diff: {a['file_name']}  vs  {b['file_name']}")

    def _action_show_dups(self, rec: dict):
        groups = db.get_dup_groups_for_file(self.db_path, rec["id"])
        if not groups:
            QMessageBox.information(self, "No Duplicates",
                f"{rec['file_name']} has no duplicate groups.")
            return
        self._dup_panel.load_file(rec["id"], rec["file_name"])
        self._bottom_tabs.setCurrentWidget(self._dup_panel)

    # ------------------------------------------------------------------
    # Panel signal handlers
    # ------------------------------------------------------------------

    def _on_file_saved(self, file_id: int):
        self._refresh_all()

    def _on_dup_open_editor(self, file_id: int, file_path: str,
                             o_number: str, verify_status: str, verify_score: int):
        self._editor_panel.load_file(file_id, file_path, o_number,
                                     verify_status, verify_score)
        self._bottom_tabs.setCurrentWidget(self._editor_panel)

    def _on_dup_open_diff(self, path_a: str, path_b: str,
                           name_a: str, name_b: str):
        self._diff_panel.compare(path_a, name_a, path_b, name_b)
        self._bottom_tabs.setCurrentWidget(self._diff_panel)

    def _set_status_multi(self, recs: list[dict], status: str):
        for rec in recs:
            db.update_file_status(self.db_path, rec["id"], status)
        self._refresh_all()

    def _set_verified_multi(self, recs: list[dict]):
        """Set status to 'verified' and stamp the G-code file with a VERIFIED comment."""
        import datetime
        now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
        for rec in recs:
            path = rec.get("file_path", "")
            # Write VERIFIED comment into G-code file
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                # Find insertion point: after the first O-line (title line)
                insert_idx = None
                verified_idx = None
                for i, ln in enumerate(lines):
                    s = ln.strip()
                    if not s or s == "%":
                        continue
                    # First code line (O-number line) — insert after it
                    if insert_idx is None:
                        insert_idx = i + 1
                    # Check if VERIFIED line already exists
                    if s.startswith("(VERIFIED") or s.startswith("( VERIFIED"):
                        verified_idx = i
                        break
                verified_line = f"(VERIFIED {now})\n"
                if verified_idx is not None:
                    lines[verified_idx] = verified_line
                elif insert_idx is not None:
                    lines.insert(insert_idx, verified_line)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.writelines(lines)
            except Exception:
                pass  # file write failure — still update DB status
            db.update_file_status(self.db_path, rec["id"], "verified")
        self._refresh_all()

    def _apply_verify_override(self, rec: dict, check_token: str, value: str):
        """Override a single verification check to PASS or FAIL."""
        from direct_scorer import (set_override_in_notes, parse_overrides,
                                   apply_overrides_to_status)
        notes = rec.get("notes", "") or ""
        new_notes = set_override_in_notes(notes, check_token, value)
        # Apply overrides to existing verify_status
        overrides = parse_overrides(new_notes)
        vstatus = rec.get("verify_status", "") or ""
        new_score, new_vstatus = apply_overrides_to_status(vstatus, overrides)
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute(
                "UPDATE files SET notes=?, verify_score=?, verify_status=? WHERE id=?",
                (new_notes, new_score, new_vstatus, rec["id"]))
        conn.close()
        self._refresh_all()

    def _clear_verify_overrides(self, rec: dict):
        """Remove all overrides and re-verify the file from scratch."""
        from direct_scorer import clear_overrides_in_notes, score_file
        notes = rec.get("notes", "") or ""
        new_notes = clear_overrides_in_notes(notes)
        # Re-verify from scratch (no overrides)
        path = rec.get("file_path", "")
        title = rec.get("program_title", "")
        onum = rec.get("o_number", "")
        score, vstatus = score_file(path, title, o_number=onum)
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute(
                "UPDATE files SET notes=?, verify_score=?, verify_status=? WHERE id=?",
                (new_notes, score, vstatus, rec["id"]))
        conn.close()
        self._refresh_all()

    def _copy_onum(self, rec: dict):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(rec["o_number"])

    def _action_2pc(self, rec: dict):
        try:
            from ui.twopc_match import TwoPCMatchDialog
            dlg = TwoPCMatchDialog(self.db_path, self,
                                   initial_onum=rec["o_number"])
            dlg.show()
        except Exception as exc:
            QMessageBox.warning(self, "2PC Match", str(exc))

    def _action_onum_map(self):
        try:
            from ui.onum_map import OnumMapDialog
            dlg = OnumMapDialog(self.db_path, self)
            dlg.show()
        except Exception as exc:
            QMessageBox.warning(self, "O# Map", str(exc))

    def _action_revisions(self, rec: dict):
        from ui.revision_history import RevisionHistoryDialog
        dlg = RevisionHistoryDialog(
            self.db_path, rec["id"], rec["file_path"],
            rec["file_name"], self)
        dlg.exec()

    def _action_toolpath(self, rec: dict):
        try:
            from ui.travel_viewer import TravelViewerDialog
            dlg = TravelViewerDialog(
                rec["file_path"],
                rec.get("o_number") or rec["file_name"],
                self,
            )
            dlg.show()
        except Exception as exc:
            QMessageBox.warning(self, "Toolpath Viewer", str(exc))

    def _on_empty_trash(self):
        """Permanently delete all status='delete' files from disk and DB."""
        if not self.db_path:
            return

        # Count how many are queued
        conn = db.get_connection(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE status='delete'"
        ).fetchone()[0]
        conn.close()

        if count == 0:
            QMessageBox.information(self, "Empty Trash",
                                    "No files are marked for deletion.")
            return

        reply = QMessageBox.warning(
            self, "Empty Trash",
            f"Permanently delete {count:,} file(s) from disk and the database?\n\n"
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted, missing = db.delete_trash_files(self.db_path)
        total = deleted + missing
        self._status_bar.showMessage(
            f"Empty Trash: {deleted:,} file(s) deleted from disk, "
            f"{missing:,} record(s) removed (files were already absent)."
        )
        QMessageBox.information(
            self, "Empty Trash Complete",
            f"{total:,} record(s) removed.\n"
            f"  {deleted:,} files deleted from disk.\n"
            f"  {missing:,} were already absent from disk."
        )
        self._refresh_all()

    def _action_delete(self, recs: list[dict]):
        names = ", ".join(r["file_name"] for r in recs[:5])
        if len(recs) > 5:
            names += f" … (+{len(recs)-5} more)"

        dlg = QDialog(self)
        dlg.setWindowTitle("Confirm Delete")
        dlg.setStyleSheet("QDialog{background:#1a0a0a;color:#ccccdd;}"
                          "QLabel{color:#ccccdd;} QLineEdit{background:#2a0a0a;"
                          "border:1px solid #ff4444;color:#ffaaaa;padding:4px;}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            f"You are about to permanently delete {len(recs)} file(s):\n{names}\n\n"
            f"Type  DELETE  to confirm:"
        ))
        edit = QLineEdit()
        lay.addWidget(edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        edit.textChanged.connect(
            lambda t: btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                t.strip() == "DELETE"))
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        errors = []
        for rec in recs:
            try:
                if os.path.exists(rec["file_path"]):
                    os.remove(rec["file_path"])
                db.delete_file_record(self.db_path, rec["id"])
            except Exception as exc:
                errors.append(f"{rec['file_name']}: {exc}")

        if errors:
            QMessageBox.warning(self, "Delete Errors", "\n".join(errors))
        self._refresh_all()

    # ------------------------------------------------------------------
    # Re-verify all
    # ------------------------------------------------------------------

    def _on_twopc(self):
        from ui.twopc_match import TwoPCMatchDialog
        try:
            dlg = TwoPCMatchDialog(self.db_path, self)
            dlg.show()
        except Exception as exc:
            QMessageBox.warning(self, "2PC Match", str(exc))

    def _open_batch_replace(self, find: str = "", title_filter: str = ""):
        from ui.batch_replace import BatchReplaceDialog
        dlg = BatchReplaceDialog(self.db_path, initial_find=find,
                                 initial_filter=title_filter, parent=self)
        dlg.exec()
        self._refresh_all()

    def _open_feed_audit(self, file_ids: list | None = None):
        from ui.feed_audit import FeedAuditDialog
        dlg = FeedAuditDialog(self.db_path, file_ids=file_ids, parent=self)
        dlg.open_in_editor.connect(self._audit_open_editor)
        dlg.show()

    def _open_new_progs_finder(self):
        from ui.new_programs_finder import NewProgsFinder, REPO_PATH, NEW_PROGS_PATH
        import os

        if not os.path.isdir(REPO_PATH):
            QMessageBox.warning(self, "New Programs Finder",
                                f"Repository folder not found:\n{REPO_PATH}")
            return
        if not os.path.isdir(NEW_PROGS_PATH):
            QMessageBox.warning(self, "New Programs Finder",
                                f"New Programs folder not found:\n{NEW_PROGS_PATH}")
            return

        prog = QProgressDialog("Scanning folders…", None, 0, 0, self)
        prog.setWindowTitle("New Programs Finder")
        prog.setMinimumWidth(380)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.show()

        self._new_progs_worker = NewProgsFinder(parent=self)
        self._new_progs_worker.progress.connect(prog.setLabelText)
        self._new_progs_worker.finished.connect(
            lambda copied, skipped, errors, p=prog: self._on_new_progs_done(
                copied, skipped, errors, p))
        self._new_progs_worker.start()

    def _on_new_progs_done(self, copied: int, skipped: int,
                           errors: list, prog: "QProgressDialog"):
        from ui.new_programs_finder import NEW_PROGS_PATH
        prog.close()
        new_folder = os.path.join(NEW_PROGS_PATH, "new")
        if copied == 0 and not errors:
            msg = "No new files found — everything in New Programs is already in Repository."
        else:
            lines = [f"{copied:,} file(s) copied to:\n{new_folder}"]
            if skipped:
                lines.append(f"{skipped:,} already existed — skipped.")
            if errors:
                lines.append(f"{len(errors):,} error(s):\n" + "\n".join(errors[:5]))
            msg = "\n".join(lines)
        QMessageBox.information(self, "New Programs Finder", msg)

    def _on_new_file(self):
        if not self.db_path or not self.scan_folders:
            return
        from ui.new_file_creator import NewFileCreatorDialog
        dlg = NewFileCreatorDialog(self.db_path, self.scan_folders, self)
        dlg.file_created.connect(lambda _path: self._refresh_all())
        dlg.exec()

    def _on_daily_report(self):
        if not self.db_path:
            return

        # Date picker dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Daily Report — Pick Date")
        dlg.setStyleSheet(
            "QDialog { background:#0d0e18; color:#ccccdd; }"
            "QLabel  { color:#aaaacc; font-size:11px; }"
            "QCalendarWidget { background:#0f1018; color:#ccccdd; }"
            "QCalendarWidget QToolButton { color:#ccccdd; background:#1a1d2e; }"
            "QCalendarWidget QMenu { background:#1a1d2e; color:#ccccdd; }"
            "QCalendarWidget QSpinBox { background:#1a1d2e; color:#ccccdd; }"
            "QCalendarWidget QAbstractItemView { background:#0f1018; color:#ccccdd;"
            "  selection-background-color:#2a3055; selection-color:#ffffff; }"
        )
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Select the date to report on:"))
        cal = QCalendarWidget()
        lay.addWidget(cal)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        gen_btn = QPushButton("Generate")
        gen_btn.setStyleSheet(
            "QPushButton { background:#0a2a0a; border:1px solid #44dd88;"
            " color:#44dd88; padding:5px 16px; border-radius:3px; }"
            "QPushButton:hover { background:#0e3812; }"
        )
        gen_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(gen_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        date_str = cal.selectedDate().toString("yyyy-MM-dd")

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Daily Report",
            f"Daily_Report_{date_str}.xlsx",
            "Excel Workbook (*.xlsx);;All files (*)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        from ui.export_xlsx import export_daily_report
        try:
            count = export_daily_report(self.db_path, path, date_str)
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, "Report Failed",
                f"Could not generate report:\n{exc}\n\n{traceback.format_exc()}")
            return

        if count == 0:
            QMessageBox.information(self, "Daily Report",
                f"No files were created on {date_str}.")
        else:
            QMessageBox.information(self, "Daily Report",
                f"Report saved to:\n{path}\n\n"
                f"{count:,} file(s) created on {date_str}.")

    def _audit_open_editor(self, file_path: str, line_no: int):
        """Open a file in the editor tab and scroll to a line number."""
        # Find the record in the DB to get o_number etc.
        all_files = db.get_all_files(self.db_path)
        rec = next((f for f in all_files if f["file_path"] == file_path), None)
        if rec:
            self._action_edit(rec, scroll_to_line=line_no)
        else:
            # Fallback: load directly (file not in DB)
            self._editor_panel.load_file(
                None, file_path,
                os.path.splitext(os.path.basename(file_path))[0],
                scroll_to_line=scroll_to_line)
            if hasattr(self, "_bottom_tabs"):
                self._bottom_tabs.setCurrentWidget(self._editor_panel)

    def _on_reverify_all(self):
        if not self.db_path:
            return
        self._reverify_btn.setEnabled(False)
        self._reverify_btn.setText("Verifying…")

        self._reverify_dlg = QProgressDialog(
            "Re-verifying files…", "Cancel", 0, 100, self)
        self._reverify_dlg.setWindowTitle("Re-Verify All")
        self._reverify_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._reverify_dlg.setMinimumDuration(0)
        self._reverify_dlg.setValue(0)

        worker = _ReverifyWorker(self.db_path, self)
        worker.progress.connect(self._on_reverify_progress)
        worker.finished.connect(self._on_reverify_done)
        self._reverify_dlg.canceled.connect(worker.cancel)
        worker.start()
        self._reverify_worker = worker

    def _on_reverify_progress(self, done: int, total: int):
        self._status_bar.showMessage(f"Re-verifying…  {done:,}/{total:,}")
        dlg = getattr(self, "_reverify_dlg", None)
        if dlg:
            pct = int(done * 100 / total) if total else 0
            dlg.setValue(pct)
            dlg.setLabelText(f"Re-verifying files…  {done:,} / {total:,}")

    def _on_reverify_done(self, updated: int):
        dlg = getattr(self, "_reverify_dlg", None)
        if dlg:
            dlg.close()
            self._reverify_dlg = None
        self._reverify_btn.setEnabled(True)
        self._reverify_btn.setText("Re-Verify All")
        self._status_bar.showMessage(f"Re-verify complete — {updated:,} files updated.")
        self._refresh_all()
        # Reload verify panel for the currently selected row (scores may have changed)
        if hasattr(self, "_verify_panel"):
            idx = self._table.selectionModel().currentIndex()
            if idx.isValid():
                self._verify_panel._current_path = ""   # force re-run
                self._on_table_row_changed(idx)

    # ------------------------------------------------------------------
    # Recheck Ranges — batch title/OD round-size consistency check
    # ------------------------------------------------------------------

    def _on_recheck_ranges(self):
        """Re-run check_o_range_title_only on every file and update has_range_conflict."""
        if not self.db_path:
            return

        from verifier import check_o_range_title_only
        import direct_database as _db

        conn = _db.get_connection(self.db_path)
        rows = conn.execute(
            "SELECT id, o_number, program_title, file_path, status FROM files"
        ).fetchall()

        updated = 0
        with conn:
            for row in rows:
                ok, msg = check_o_range_title_only(
                    row["program_title"] or "", row["o_number"] or "",
                    status=row["status"] or "")
                flag = 0 if ok else 1

                # Update flag and note
                existing = conn.execute(
                    "SELECT has_range_conflict, notes FROM files WHERE id=?",
                    (row["id"],)
                ).fetchone()
                if not existing:
                    continue

                old_flag  = existing["has_range_conflict"] or 0
                old_notes = existing["notes"] or ""

                # Strip old range-conflict tag
                import re as _re
                clean_notes = _re.sub(
                    r'\[RANGE CONFLICT\][^\n]*\n?', '', old_notes,
                    flags=_re.IGNORECASE
                ).strip()

                new_notes = clean_notes
                if flag and "[RANGE CONFLICT]" not in clean_notes:
                    new_notes = (f"[RANGE CONFLICT] {msg}\n" + clean_notes).strip()

                if flag != old_flag or new_notes != old_notes:
                    conn.execute(
                        "UPDATE files SET has_range_conflict=?, notes=? WHERE id=?",
                        (flag, new_notes, row["id"])
                    )
                    updated += 1

        conn.close()
        self._status_bar.showMessage(
            f"Range check complete — {updated:,} file(s) updated.")
        self._refresh_all()

    # ------------------------------------------------------------------
    # Auto-resolve same-O-number duplicates
    # ------------------------------------------------------------------

    def _on_auto_resolve_dupes(self):
        """
        For every dup group where all members share the same base O-number
        (regardless of file extension), keep the single best file and mark
        the rest as status='delete'.

        Best = highest verify_score; tiebreak = most line_count.
        """
        if not self.db_path:
            return

        import direct_database as _db

        conn = _db.get_connection(self.db_path)

        # Fetch every dup group with its member file details in one pass
        groups = conn.execute("SELECT id FROM dup_groups").fetchall()

        groups_resolved = 0
        files_marked    = 0

        with conn:
            for grp in groups:
                gid     = grp["id"]
                members = conn.execute("""
                    SELECT f.id, f.o_number, f.verify_score, f.line_count,
                           f.status, f.file_name
                    FROM files f
                    JOIN dup_group_members dgm ON f.id = dgm.file_id
                    WHERE dgm.group_id = ?
                """, (gid,)).fetchall()

                if len(members) < 2:
                    continue

                # Normalise O-numbers — strip extension, uppercase
                def _base_onum(fname, onum):
                    # Use the stored o_number which is already normalised
                    return (onum or "").upper().strip()

                o_numbers = {_base_onum(m["file_name"], m["o_number"])
                             for m in members}

                # Only auto-resolve when every member has the same O-number
                if len(o_numbers) != 1 or "" in o_numbers:
                    continue

                # Skip if any member is shop_special — leave those alone
                if any((m["status"] or "") == "shop_special" for m in members):
                    continue

                # Pick winner: best score, then most lines
                winner = max(
                    members,
                    key=lambda m: (m["verify_score"] or 0, m["line_count"] or 0)
                )

                losers = [m for m in members if m["id"] != winner["id"]]

                for loser in losers:
                    # Only mark if not already deleted/trashed
                    if (loser["status"] or "") not in ("delete", "shop_special"):
                        conn.execute(
                            "UPDATE files SET status='delete' WHERE id=?",
                            (loser["id"],)
                        )
                        files_marked += 1

                groups_resolved += 1

        conn.close()

        QMessageBox.information(
            self, "Auto-Resolve Complete",
            f"{groups_resolved} group(s) resolved.\n"
            f"{files_marked} file(s) marked for deletion.\n\n"
            f"Review them in the 'Mark Delete' filter, then use "
            f"'Empty Trash' to permanently remove."
        )
        self._refresh_all()

    # ------------------------------------------------------------------
    # Auto-rename files with wrong O-number range
    # ------------------------------------------------------------------

    def _on_auto_rename_o_range(self):
        """
        Find every file with has_range_conflict=1, determine the correct
        O-number range from its title, pick the lowest free O-number in
        that range, rename the file on disk, and clear the conflict flag.
        """
        if not self.db_path:
            return

        import re as _re
        from verifier import _o_range_for_round as _range_for

        conn = db.get_connection(self.db_path)

        conflicts = conn.execute(
            "SELECT id, o_number, file_name, file_path, program_title, status "
            "FROM files WHERE has_range_conflict = 1"
        ).fetchall()

        if not conflicts:
            conn.close()
            QMessageBox.information(self, "Auto-Rename O-Range",
                                    "No range conflicts found. Run Recheck Ranges first.")
            return

        # Build set of every O-number integer currently used in the DB
        used = set()
        for r in conn.execute("SELECT o_number FROM files").fetchall():
            try:
                used.add(int((r["o_number"] or "").lstrip("Oo")))
            except ValueError:
                pass

        renamed  = 0
        skipped  = 0
        skip_reasons: list[str] = []

        with conn:
            for f in conflicts:
                fid    = f["id"]
                status = (f["status"] or "").lower()
                title  = f["program_title"] or ""
                o_num  = f["o_number"] or ""
                path   = f["file_path"] or ""

                if status == "shop_special":
                    skipped += 1
                    skip_reasons.append(f"{o_num}: shop_special")
                    continue

                specs = _vfy.parse_title_specs(title)
                if not specs:
                    skipped += 1
                    skip_reasons.append(f"{o_num}: no parseable title")
                    continue

                rs = specs.get("round_size_in")
                if rs is None:
                    skipped += 1
                    skip_reasons.append(f"{o_num}: no round size in title")
                    continue

                range_result = _range_for(rs)
                if range_result is None:
                    skipped += 1
                    skip_reasons.append(f"{o_num}: round size {rs}\" not in range table")
                    continue

                _label, lo, hi = range_result

                # Find lowest free O-number in the target range
                new_val = None
                for candidate in range(lo, hi + 1):
                    if candidate not in used:
                        new_val = candidate
                        break

                if new_val is None:
                    skipped += 1
                    skip_reasons.append(f"{o_num}: target range {_label} is full")
                    continue

                # Build new names — preserve extension
                ext      = os.path.splitext(f["file_name"])[1]
                new_name = f"O{new_val:05d}{ext}"
                new_path = os.path.join(os.path.dirname(path), new_name)

                # Rename on disk
                try:
                    os.rename(path, new_path)
                except OSError as e:
                    skipped += 1
                    skip_reasons.append(f"{o_num}: disk rename failed ({e})")
                    continue

                # Clear conflict flag and note
                old_notes = (conn.execute(
                    "SELECT notes FROM files WHERE id=?", (fid,)
                ).fetchone() or {})
                clean_notes = _re.sub(
                    r'\[RANGE CONFLICT\][^\n]*\n?', '',
                    (old_notes["notes"] if old_notes else "") or "",
                    flags=_re.IGNORECASE
                ).strip()

                new_o = f"O{new_val:05d}"
                conn.execute(
                    "UPDATE files SET file_path=?, file_name=?, o_number=?, "
                    "has_range_conflict=0, notes=? WHERE id=?",
                    (new_path, new_name, new_o, clean_notes, fid)
                )

                used.add(new_val)   # reserve for this batch
                renamed += 1

        conn.close()

        msg = f"{renamed} file(s) renamed to correct O-number range.\n" \
              f"{skipped} file(s) skipped."
        if skip_reasons:
            msg += "\n\nSkipped:\n" + "\n".join(f"  • {r}" for r in skip_reasons[:20])
            if len(skip_reasons) > 20:
                msg += f"\n  … and {len(skip_reasons) - 20} more"

        QMessageBox.information(self, "Auto-Rename O-Range", msg)
        self._refresh_all()

    # ------------------------------------------------------------------
    # Export CSV
    # ------------------------------------------------------------------

    def _on_export_csv(self):
        if not self.db_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export XLSX", "",
            "Excel Workbook (*.xlsx);;All files (*)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        from ui.export_xlsx import export_workbook
        try:
            used, free = export_workbook(self.db_path, path,
                                         scan_folders=self.scan_folders or None)
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, "Export Failed",
                f"Could not write workbook:\n{exc}\n\n{traceback.format_exc()}")
            return

        QMessageBox.information(
            self, "Export Complete",
            f"Workbook saved to:\n{path}\n\n"
            f"{used:,} indexed file(s)  •  {free:,} free O-number(s) across all ranges\n\n"
            f"Sheets: All  +  one sheet per round size (5.75\" – 13.00\")"
        )

    # ------------------------------------------------------------------
    # Export Files — copy files into round-size sub-folders
    # ------------------------------------------------------------------

    def _on_export_files(self):
        if not self.db_path:
            return

        dest = QFileDialog.getExistingDirectory(
            self, "Choose Export Destination Folder", "")
        if not dest:
            return

        import shutil
        from verifier import parse_title_specs

        # Round-size buckets: label → (lo_in, hi_in)
        # These mirror _ROUND_TO_O_RANGE in verifier.py
        _BUCKETS = [
            ("5.75-6.50",  5.75,  6.50),
            ("7.00-8.50",  7.00,  8.50),
            ("9.50",       9.50,  9.50),
            ("10.00-13.00",10.00, 13.00),
        ]

        def _bucket_for(rs: float) -> str:
            for label, lo, hi in _BUCKETS:
                if lo - 0.01 <= rs <= hi + 0.01:
                    return label
            return "special"

        rows = db.get_all_files(self.db_path)
        if not rows:
            QMessageBox.information(self, "Export Files", "No files found in database.")
            return

        copied   = 0
        skipped  = 0
        errors   = []
        by_folder: dict[str, int] = {}

        for rec in rows:
            src = rec["file_path"]
            if not src or not os.path.isfile(src):
                skipped += 1
                continue

            title = rec.get("program_title") or ""
            specs = None
            try:
                specs = parse_title_specs(title)
            except Exception:
                pass

            if specs and specs.get("round_size_in") is not None:
                folder_name = _bucket_for(specs["round_size_in"])
            else:
                folder_name = "special"

            out_dir = os.path.join(dest, folder_name)
            os.makedirs(out_dir, exist_ok=True)

            fname    = os.path.basename(src)
            out_path = os.path.join(out_dir, fname)

            # If a file with this name already exists, append _A/_B/… suffix
            if os.path.exists(out_path):
                base, ext = os.path.splitext(fname)
                suffix_ord = ord('A')
                while os.path.exists(out_path) and suffix_ord <= ord('Z'):
                    out_path = os.path.join(out_dir, f"{base}_{chr(suffix_ord)}{ext}")
                    suffix_ord += 1

            try:
                shutil.copy2(src, out_path)
                copied += 1
                by_folder[folder_name] = by_folder.get(folder_name, 0) + 1
            except Exception as exc:
                errors.append(f"{fname}: {exc}")

        # Build summary
        lines = [f"Exported {copied:,} file(s) to:\n{dest}\n"]
        for fname in sorted(by_folder):
            lines.append(f"  {fname}/  — {by_folder[fname]:,} file(s)")
        if skipped:
            lines.append(f"\n{skipped} skipped (file missing on disk)")
        if errors:
            lines.append(f"\n{len(errors)} error(s):")
            lines.extend(f"  {e}" for e in errors[:10])
            if len(errors) > 10:
                lines.append(f"  … and {len(errors)-10} more")

        QMessageBox.information(self, "Export Files Complete", "\n".join(lines))

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _build_verify_limits_tab(self, parent_dialog: QDialog) -> tuple[QWidget, dict]:
        """Build the Verify Limits settings tab.

        Returns (tab_widget, control_dict) where control_dict maps setting keys
        to their QSpinBox/QDoubleSpinBox/etc controls for later retrieval.
        """
        import verifier

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setStyleSheet("QScrollArea{border:none;background:#0d0e18;}")
        scroll_widget = QWidget()
        scroll_layout = QGridLayout(scroll_widget)
        scroll_layout.setColumnStretch(0, 0)
        scroll_layout.setColumnStretch(1, 0)
        scroll_layout.setColumnStretch(2, 1)
        scroll_layout.setSpacing(8)

        row = 0
        controls = {}

        # Section 1: Tolerances
        title_label = QLabel("Tolerances")
        title_label.setStyleSheet("font-weight:bold;color:#88ccff;")
        scroll_layout.addWidget(title_label, row, 0, 1, 3)
        row += 1

        tolerance_fields = [
            ("TOLERANCE_IN", "CB/OB Bore (in)", 0.0010),
            ("DR_TOLERANCE_IN", "Drill Depth (in)", 0.0200),
            ("OD_TOLERANCE_IN", "OD Turn (in)", 0.0150),
            ("TZ_TOLERANCE", "Turning Z (in)", 0.0300),
            ("_F_MAX", "Max Feed Rate (in/rev)", 0.0200),
            ("_CB_F_MAX", "CB Finish Feed (HC 15MM+)", 0.0150),
        ]

        for key, label, orig_val in tolerance_fields:
            label_w = QLabel(label)
            orig_text = QLabel(f"Original: {orig_val:.4f}")
            orig_text.setStyleSheet("color:#666688;")

            spinbox = QDoubleSpinBox()
            spinbox.setRange(0.0001, 1.0)
            spinbox.setSingleStep(0.0001)
            spinbox.setDecimals(4)
            spinbox.setValue(getattr(verifier, key, orig_val))
            spinbox.setStyleSheet("background:#1a1d2e;border:1px solid #2a2d45;color:#ccccdd;padding:2px;")

            scroll_layout.addWidget(label_w, row, 0)
            scroll_layout.addWidget(orig_text, row, 1)
            scroll_layout.addWidget(spinbox, row, 2)
            controls[key] = spinbox
            row += 1

        row += 1  # Spacing

        # Section 2: Turning Z Table
        title_label = QLabel("Turning Z Limits (by disc thickness)")
        title_label.setStyleSheet("font-weight:bold;color:#88ccff;margin-top:8px;")
        scroll_layout.addWidget(title_label, row, 0, 1, 3)
        row += 1

        tz_header_th = QLabel("Thickness (in)")
        tz_header_th.setStyleSheet("font-weight:bold;color:#aaaacc;")
        tz_header_orig = QLabel("Original limit")
        tz_header_orig.setStyleSheet("font-weight:bold;color:#aaaacc;")
        tz_header_curr = QLabel("Current limit")
        tz_header_curr.setStyleSheet("font-weight:bold;color:#aaaacc;")
        scroll_layout.addWidget(tz_header_th, row, 0)
        scroll_layout.addWidget(tz_header_orig, row, 1)
        scroll_layout.addWidget(tz_header_curr, row, 2)
        row += 1

        for thickness in sorted(verifier._DEFAULTS.get("_TURNING_Z_TABLE", {}).keys()):
            orig_limit = verifier._DEFAULTS["_TURNING_Z_TABLE"][thickness]
            curr_limit = verifier._TURNING_Z_TABLE.get(thickness, orig_limit)

            th_label = QLabel(f"{thickness:.4f}\"")
            orig_label = QLabel(f"{orig_limit:.2f}\"")
            orig_label.setStyleSheet("color:#666688;")

            limit_spin = QDoubleSpinBox()
            limit_spin.setRange(-5.0, 0.0)
            limit_spin.setSingleStep(0.05)
            limit_spin.setDecimals(2)
            limit_spin.setValue(curr_limit)
            limit_spin.setStyleSheet("background:#1a1d2e;border:1px solid #2a2d45;color:#ccccdd;padding:2px;")

            key = f"TZ_{thickness:.4f}"
            controls[key] = limit_spin

            scroll_layout.addWidget(th_label, row, 0)
            scroll_layout.addWidget(orig_label, row, 1)
            scroll_layout.addWidget(limit_spin, row, 2)
            row += 1

        row += 1  # Spacing

        # Section 3: OD Table (just the key round sizes to avoid clutter)
        title_label = QLabel("OD Turn Finish (by round size)")
        title_label.setStyleSheet("font-weight:bold;color:#88ccff;margin-top:8px;")
        scroll_layout.addWidget(title_label, row, 0, 1, 3)
        row += 1

        od_header_rs = QLabel("Round Size (in)")
        od_header_rs.setStyleSheet("font-weight:bold;color:#aaaacc;")
        od_header_orig = QLabel("Original OD")
        od_header_orig.setStyleSheet("font-weight:bold;color:#aaaacc;")
        od_header_curr = QLabel("Current OD")
        od_header_curr.setStyleSheet("font-weight:bold;color:#aaaacc;")
        scroll_layout.addWidget(od_header_rs, row, 0)
        scroll_layout.addWidget(od_header_orig, row, 1)
        scroll_layout.addWidget(od_header_curr, row, 2)
        row += 1

        for round_size in sorted(verifier._DEFAULTS.get("_OD_TABLE", {}).keys()):
            orig_od = verifier._DEFAULTS["_OD_TABLE"][round_size]
            curr_od = verifier._OD_TABLE.get(round_size, orig_od)

            rs_label = QLabel(f"{round_size:.2f}\"")
            orig_label = QLabel(f"{orig_od:.3f}\"")
            orig_label.setStyleSheet("color:#666688;")

            od_spin = QDoubleSpinBox()
            od_spin.setRange(0.0, 20.0)
            od_spin.setSingleStep(0.001)
            od_spin.setDecimals(3)
            od_spin.setValue(curr_od)
            od_spin.setStyleSheet("background:#1a1d2e;border:1px solid #2a2d45;color:#ccccdd;padding:2px;")

            key = f"OD_{round_size:.2f}"
            controls[key] = od_spin

            scroll_layout.addWidget(rs_label, row, 0)
            scroll_layout.addWidget(orig_label, row, 1)
            scroll_layout.addWidget(od_spin, row, 2)
            row += 1

        scroll_layout.setRowStretch(row, 1)
        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        return tab, controls

    def _on_settings(self):
        if not self.db_path:
            QMessageBox.information(self, "Settings",
                "Add a folder first to access settings.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setMinimumWidth(620)
        dlg.setMinimumHeight(500)
        dlg.setStyleSheet(
            "QDialog{background:#0d0e18;color:#ccccdd;}"
            "QLabel{color:#aaaacc;} "
            "QLineEdit{background:#1a1d2e;border:1px solid #2a2d45;"
            "color:#ccccdd;padding:4px;border-radius:3px;}"
            "QCheckBox{color:#aaaacc;}"
            "QPushButton{background:#1a2030;border:1px solid #2a2d45;"
            "color:#aaaacc;padding:4px 12px;border-radius:3px;}"
            "QSpinBox,QDoubleSpinBox{background:#1a1d2e;border:1px solid #2a2d45;"
            "color:#ccccdd;padding:2px;border-radius:3px;}"
            "QTabWidget{border:none;} "
            "QTabBar::tab{background:#1a1d2e;color:#aaaacc;padding:6px 16px;border:1px solid #2a2d45;}"
            "QTabBar::tab:selected{background:#2a3050;color:#ccccdd;border-bottom:2px solid #5588ff;}"
        )

        tabs = QTabWidget()
        main_layout = QVBoxLayout(dlg)
        main_layout.addWidget(tabs)

        # --- Tab 1: General Settings ---
        general_tab = QWidget()
        form = QFormLayout(general_tab)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        settings = db.get_all_settings(self.db_path)

        auto_bak = QCheckBox("Create backup before editing")
        auto_bak.setChecked(settings.get("auto_backup_on_edit", "1") == "1")
        form.addRow("Auto-backup:", auto_bak)

        bak_ext = QLineEdit(settings.get("backup_extension", ".bak"))
        form.addRow("Backup extension:", bak_ext)

        bak_folder_row = QHBoxLayout()
        bak_folder_edit = QLineEdit(settings.get("backup_folder", ""))
        bak_folder_edit.setReadOnly(True)
        bak_folder_btn = QPushButton("Browse…")
        bak_folder_btn.clicked.connect(lambda: self._pick_backup_folder(bak_folder_edit))
        bak_folder_row.addWidget(bak_folder_edit)
        bak_folder_row.addWidget(bak_folder_btn)
        form.addRow("Backup folder:", bak_folder_row)

        new_progs_row = QHBoxLayout()
        new_progs_edit = QLineEdit(settings.get("new_programs_folder", ""))
        new_progs_edit.setReadOnly(True)
        new_progs_edit.setPlaceholderText("Not set — new files won't be copied")
        new_progs_btn = QPushButton("Browse…")
        new_progs_btn.clicked.connect(lambda: self._pick_backup_folder(new_progs_edit))
        new_progs_row.addWidget(new_progs_edit)
        new_progs_row.addWidget(new_progs_btn)
        form.addRow("New programs folder:", new_progs_row)

        allow_del = QCheckBox("Allow file deletion")
        allow_del.setChecked(settings.get("allow_delete", "1") == "1")
        form.addRow("Delete files:", allow_del)

        tabs.addTab(general_tab, "General")

        # --- Tab 2: Verify Limits ---
        verify_tab, verify_controls = self._build_verify_limits_tab(dlg)
        tabs.addTab(verify_tab, "Verify Limits")

        # --- Dialog buttons ---
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        main_layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Save general settings
        db.set_setting(self.db_path, "auto_backup_on_edit", "1" if auto_bak.isChecked() else "0")
        db.set_setting(self.db_path, "backup_extension", bak_ext.text().strip() or ".bak")
        db.set_setting(self.db_path, "backup_folder", bak_folder_edit.text().strip())
        db.set_setting(self.db_path, "new_programs_folder", new_progs_edit.text().strip())
        db.set_setting(self.db_path, "allow_delete", "1" if allow_del.isChecked() else "0")

        # Save verify limits (with confirmation)
        self._save_verify_limits(verify_controls)

    def _save_verify_limits(self, controls: dict):
        """Save verification limit overrides with confirmation dialog.

        Args:
            controls: Dict mapping setting keys to their QSpinBox/QDoubleSpinBox controls.
        """
        import verifier

        # Collect current values from controls and compare to defaults
        changes = {}
        change_list = []

        for key, ctrl in controls.items():
            curr_val = ctrl.value()

            # Get the original default value
            if key in verifier._DEFAULTS:
                orig_val = verifier._DEFAULTS[key]
            elif key.startswith("TZ_"):
                thickness = float(key[3:])
                orig_val = verifier._DEFAULTS.get("_TURNING_Z_TABLE", {}).get(thickness)
            elif key.startswith("OD_"):
                rs = float(key[3:])
                orig_val = verifier._DEFAULTS.get("_OD_TABLE", {}).get(rs)
            else:
                orig_val = None

            if orig_val is not None and curr_val != orig_val:
                changes[key] = curr_val
                change_list.append(f"  {key}: {orig_val:.4f} → {curr_val:.4f}")

        if not changes:
            # No changes, nothing to save
            return

        # Show confirmation dialog
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Verify Limits Changes")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("You are about to change the following verification limits:\n")
        msg.setInformativeText("\n".join(change_list))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if msg.exec() != QMessageBox.StandardButton.Ok:
            return

        # Load existing config, update verify_overrides, and save
        try:
            existing_cfg = {}
            if os.path.exists(self.config_path):
                with open(self.config_path) as f:
                    existing_cfg = json.load(f)
        except Exception:
            existing_cfg = {}

        # Merge new changes into verify_overrides
        verify_overrides = existing_cfg.get("verify_overrides", {})
        verify_overrides.update(changes)
        existing_cfg["verify_overrides"] = verify_overrides

        # Save updated config
        try:
            with open(self.config_path, "w") as f:
                json.dump(existing_cfg, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings: {e}")
            return

        # Apply overrides immediately without restart
        verifier.apply_overrides(verify_overrides)

        QMessageBox.information(self, "Success", "Verification limits updated. Changes take effect immediately.")

    def _pick_backup_folder(self, edit: QLineEdit):
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Folder", "",
                                                  QFileDialog.Option.ShowDirsOnly)
        if folder:
            edit.setText(os.path.normpath(folder))

    # QHBoxLayout is not a QWidget so we need a container
    # Override the form row helper to accept it
