"""
CNC Direct Editor — Order Sheet Search panel.

Paste a tab-separated row from the order sheet (columns I-M) and
find matching CNC program files by parsed specs.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QListWidget, QListWidgetItem, QFrame, QSizePolicy,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont, QColor

import direct_database as db
from order_search_parser import (parse_order_row, score_title_match,
                                  find_2pc_pairs, MIN_SCORE)


# ---------------------------------------------------------------------------
# Background search worker
# ---------------------------------------------------------------------------

class _SearchWorker(QThread):
    # is_2pc=False: list of (score, id, o_number, file_name, title, fields)
    # is_2pc=True:  list of (pair_score, ring_id, ring_o, ring_name, ring_title,
    #                                    hat_id,  hat_o,  hat_name,  hat_title,
    #                                    ring_fields, hat_fields)
    results_ready = pyqtSignal(list, bool)
    error         = pyqtSignal(str)

    def __init__(self, db_path: str, params: dict, parent=None):
        super().__init__(parent)
        self._db_path   = db_path
        self._params    = params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._params.get("is_2pc"):
                pairs = find_2pc_pairs(self._params, self._db_path)
                self.results_ready.emit(pairs, True)
                return

            conn = db.get_connection(self._db_path)
            rows = conn.execute(
                "SELECT id, o_number, file_name, program_title "
                "FROM files "
                "WHERE program_title IS NOT NULL AND program_title != '' "
                "ORDER BY o_number"
            ).fetchall()
            conn.close()

            # Score all rows, deduplicate by o_number keeping best score
            best: dict[str, tuple] = {}
            for row in rows:
                if self._cancelled:
                    return
                score, fields = score_title_match(self._params, row["program_title"])
                if score < MIN_SCORE:
                    continue
                key = (row["o_number"] or "").upper() or str(row["id"])
                entry = (score, row["id"], row["o_number"] or "",
                         row["file_name"] or "", row["program_title"] or "", fields)
                if key not in best or score > best[key][0]:
                    best[key] = entry

            results = sorted(best.values(), key=lambda x: x[0], reverse=True)
            self.results_ready.emit(results[:30], False)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Result list item
# ---------------------------------------------------------------------------

_SCORE_COLORS = [
    (80, "#44dd88"),
    (60, "#aadd44"),
    (40, "#ffaa33"),
    ( 0, "#ff6655"),
]


def _score_color(score: int) -> str:
    for threshold, color in _SCORE_COLORS:
        if score >= threshold:
            return color
    return "#ff6655"


class _ResultItem(QListWidgetItem):
    def __init__(self, score: int, file_id: int, o_number: str,
                 file_name: str, title: str, fields: list[str]):
        super().__init__()
        self.file_id  = file_id
        self.score    = score
        self.o_number = o_number

        color = _score_color(score)

        passed = [f for f in fields if "✓" in f]
        failed = [f for f in fields if "✗" in f or "(title:" in f]
        passed_short = "  ".join(f.replace(" ✓", "").replace(" ~", "~") for f in passed)

        # Main line: score + o-number + filename
        self.setText(f"{score:3d}%  {o_number}  {file_name}")
        self.setForeground(QColor(color))
        self.setFont(QFont("Consolas", 10))

        # Tooltip: full title + field breakdown
        tip  = f"Title: {title}\n"
        if passed: tip += "Matched: " + "  |  ".join(passed) + "\n"
        if failed: tip += "Missed:  " + "  |  ".join(failed)
        self.setToolTip(tip.strip())

        # Sub-item: matched fields in dim text (not selectable, not a file entry)
        self._sub = QListWidgetItem(f"     {passed_short}")
        self._sub.setForeground(QColor("#445566"))
        self._sub.setFont(QFont("Consolas", 9))
        self._sub.setFlags(Qt.ItemFlag.NoItemFlags)


# ---------------------------------------------------------------------------
# Order Search Panel
# ---------------------------------------------------------------------------

class OrderSearchPanel(QWidget):

    go_to_file = pyqtSignal(int)   # file_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_path = ""
        self._worker: _SearchWorker | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._run_search)

        self.setMinimumWidth(240)
        self.setMaximumWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QWidget   { background:#0d0e18; color:#ccccdd; }
            QLabel    { color:#aaaacc; font-size:11px; }
            QPushButton {
                background:#1a2030; border:1px solid #2a2d45;
                color:#aaaacc; padding:3px 8px;
                border-radius:3px; font-size:11px;
            }
            QPushButton:hover  { background:#1e2840; }
            QPushButton:disabled { color:#333355; border-color:#1a1d2e; }
            QPlainTextEdit {
                background:#0a0b14; color:#ccccdd;
                border:1px solid #2a2d45;
                font-family:Consolas; font-size:10pt;
            }
            QListWidget {
                background:#080910; color:#ccccdd;
                border:1px solid #1a1d2e;
                font-family:Consolas; font-size:10pt;
            }
            QListWidget::item:selected {
                background:#1a2840; color:#ccddff;
            }
        """)
        self._build()

    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── Header ──
        hdr_row = QHBoxLayout()
        hdr_lbl = QLabel("Order Sheet Search")
        hdr_lbl.setStyleSheet(
            "color:#88aacc; font-size:12px; font-weight:bold;")
        hdr_row.addWidget(hdr_lbl)
        hdr_row.addStretch()
        root.addLayout(hdr_row)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1a1d2e;")
        root.addWidget(sep)

        # ── Paste area ──
        paste_lbl = QLabel("Paste columns I–M from order sheet:")
        root.addWidget(paste_lbl)

        self._paste_box = QPlainTextEdit()
        self._paste_box.setFixedHeight(68)
        self._paste_box.setPlaceholderText(
            "Round  BoltPattern  CB_mm  OB_mm  Thickness\n"
            "e.g. 9.5  8170-8200-DH  125  142  1.75\"+.50\"HUB\n"
            "or   7    5550-5450-A   87.1       1.00\""
        )
        self._paste_box.textChanged.connect(self._on_text_changed)
        root.addWidget(self._paste_box)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._search_btn = QPushButton("Search")
        self._search_btn.setStyleSheet(
            "QPushButton{background:#1a3040;border:1px solid #2a5060;"
            "color:#66ccee;padding:3px 10px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#1e4050;}"
            "QPushButton:disabled{color:#333355;border-color:#1a1d2e;background:#0f1018;}")
        self._search_btn.clicked.connect(self._run_search)
        btn_row.addWidget(self._search_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Status ──
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#556688; font-size:10px;")
        root.addWidget(self._status_lbl)

        # ── Results list ──
        results_lbl = QLabel("Results  (double-click to go to file):")
        root.addWidget(results_lbl)

        self._results = QListWidget()
        self._results.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._results.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self._results, stretch=1)

        # ── Parse hint ──
        self._hint_lbl = QLabel("")
        self._hint_lbl.setStyleSheet("color:#664444; font-size:9px;")
        self._hint_lbl.setWordWrap(True)
        root.addWidget(self._hint_lbl)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_db_path(self, db_path: str):
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_text_changed(self):
        self._debounce.stop()
        self._debounce.start(600)

    def _on_clear(self):
        self._paste_box.clear()
        self._results.clear()
        self._status_lbl.setText("")
        self._hint_lbl.setText("")

    def _on_item_double_clicked(self, item: QListWidgetItem):
        if isinstance(item, _ResultItem):
            self.go_to_file.emit(item.file_id)

    def _run_search(self):
        self._debounce.stop()
        text = self._paste_box.toPlainText().strip()
        if not text:
            self._results.clear()
            self._status_lbl.setText("")
            self._hint_lbl.setText("")
            return

        if not self._db_path:
            self._status_lbl.setStyleSheet("color:#ff6655; font-size:10px;")
            self._status_lbl.setText("No workspace open.")
            return

        params = parse_order_row(text)
        if params is None:
            self._hint_lbl.setText(
                "Could not parse row. Expected 5 tab-separated values:\n"
                "Round  BoltPattern  CB_mm  OB_mm  Thickness")
            self._status_lbl.setText("")
            return

        self._hint_lbl.setText("")
        self._status_lbl.setStyleSheet("color:#556688; font-size:10px;")
        self._status_lbl.setText("Searching…")
        self._search_btn.setEnabled(False)

        # Cancel previous worker
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.results_ready.disconnect()
            self._worker.error.disconnect()

        self._worker = _SearchWorker(self._db_path, params, self)
        self._worker.results_ready.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, results: list, is_2pc: bool):
        self._search_btn.setEnabled(True)
        self._results.clear()

        if not results:
            self._status_lbl.setStyleSheet("color:#aa8844; font-size:10px;")
            self._status_lbl.setText("No matches found.")
            return

        if is_2pc:
            self._render_2pc_pairs(results)
        else:
            self._render_single_results(results)

    def _render_single_results(self, results: list):
        exact   = sum(1 for r in results if r[0] >= 80)
        close   = sum(1 for r in results if 60 <= r[0] < 80)
        partial = sum(1 for r in results if r[0] < 60)
        parts = []
        if exact:   parts.append(f"{exact} exact")
        if close:   parts.append(f"{close} close")
        if partial: parts.append(f"{partial} partial")
        self._status_lbl.setStyleSheet("color:#44aa66; font-size:10px;")
        self._status_lbl.setText(f"{len(results)} results — " + ", ".join(parts))

        for score, file_id, o_number, file_name, title, fields in results:
            item = _ResultItem(score, file_id, o_number, file_name, title, fields)
            self._results.addItem(item)
            if item._sub.text().strip():
                self._results.addItem(item._sub)

    def _render_2pc_pairs(self, pairs: list):
        self._status_lbl.setStyleSheet("color:#44ccdd; font-size:10px;")
        self._status_lbl.setText(f"{len(pairs)} matching pair(s) found — double-click to go to file")

        for (pair_score,
             ring_id, ring_o, ring_name, ring_title,
             hat_id,  hat_o,  hat_name,  hat_title,
             ring_fields, hat_fields) in pairs:

            color = _score_color(pair_score)

            # ── Pair header ──────────────────────────────────────────────
            hdr = QListWidgetItem(f"── {pair_score}% Pair ──────────────────────")
            hdr.setForeground(QColor(color))
            hdr.setFont(QFont("Consolas", 9))
            hdr.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results.addItem(hdr)

            # ── Ring piece ───────────────────────────────────────────────
            ring_passed = [f.replace(" ✓", "") for f in ring_fields if "✓" in f]
            ring_item = _ResultItem(pair_score, ring_id, ring_o, ring_name,
                                    ring_title, ring_fields)
            ring_item.setText(f"  RING  {ring_o}  {ring_name}")
            ring_item.setForeground(QColor("#66ddaa"))
            self._results.addItem(ring_item)

            ring_sub = QListWidgetItem("     " + "  ".join(ring_passed))
            ring_sub.setForeground(QColor("#336655"))
            ring_sub.setFont(QFont("Consolas", 9))
            ring_sub.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results.addItem(ring_sub)

            # ── Hat/bell piece ───────────────────────────────────────────
            hat_passed = [f.replace(" ✓", "") for f in hat_fields if "✓" in f]
            hat_item = _ResultItem(pair_score, hat_id, hat_o, hat_name,
                                   hat_title, hat_fields)
            hat_item.setText(f"  HAT   {hat_o}  {hat_name}")
            hat_item.setForeground(QColor("#66aadd"))
            self._results.addItem(hat_item)

            hat_sub = QListWidgetItem("     " + "  ".join(hat_passed))
            hat_sub.setForeground(QColor("#335566"))
            hat_sub.setFont(QFont("Consolas", 9))
            hat_sub.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results.addItem(hat_sub)

            # ── Spacer ───────────────────────────────────────────────────
            spacer = QListWidgetItem("")
            spacer.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results.addItem(spacer)

    def _on_error(self, msg: str):
        self._search_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("color:#ff5555; font-size:10px;")
        self._status_lbl.setText(f"Error: {msg}")
