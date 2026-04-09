"""
CNC Direct Editor — New File Creator dialog.

Lets the user:
  1. Pick a round size → O-number range
  2. Select a free O-number (first 200) or type one manually + Check
  3. Choose which scan folder to save the file into
  4. Write G-code in an embedded editor
  5. Verify the code (runs score_file on a temp copy)
  6. Save & Add — writes file to disk and inserts record into the DB
"""

import os
import re
import datetime
import tempfile

import xxhash

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QLineEdit, QPushButton,
    QPlainTextEdit, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont, QColor, QPalette

import direct_database as db
from direct_models import VerifyStatusDelegate   # noqa: F401 (imported for type hints elsewhere)

# ---------------------------------------------------------------------------
# Round-size → O-range table
# ---------------------------------------------------------------------------
_ROUND_TO_O_RANGE = [
    (5.75,  50000, 59999),
    (6.00,  60000, 62499),
    (6.25,  62500, 64999),
    (6.50,  65000, 69999),
    (7.00,  70000, 74999),
    (7.50,  75000, 79999),
    (8.00,  80000, 84999),
    (8.50,  85000, 89999),
    (9.50,  90000, 99999),
    (10.25, 10000, 10999),
    (13.00, 13000, 13999),
]

_O_RE = re.compile(r'^O(\d{4,6})$', re.IGNORECASE)

_FREE_CAP = 200   # max free O-numbers shown in dropdown

_STYLE = """
QDialog { background: #0d0e18; color: #ccccdd; }
QLabel  { color: #aaaacc; font-size: 11px; }
QComboBox, QLineEdit {
    background: #1a1d2e; border: 1px solid #2a2d45;
    color: #ccccdd; padding: 3px 6px; border-radius: 3px; font-size: 11px;
}
QComboBox QAbstractItemView {
    background: #1a1d2e; color: #ccccdd;
    selection-background-color: #2a3055;
}
QPushButton {
    background: #1a2030; border: 1px solid #2a2d45;
    color: #aaaacc; padding: 4px 10px; border-radius: 3px; font-size: 11px;
}
QPushButton:hover { background: #1e2840; }
QPushButton:disabled { color: #445566; border-color: #1a1d2e; }
QPlainTextEdit {
    background: #0f1018; border: 1px solid #2a2d45;
    color: #ccccdd; font-family: Consolas, Courier New, monospace;
    font-size: 11px; border-radius: 3px;
}
"""


class NewFileCreatorDialog(QDialog):

    file_created = pyqtSignal(str)   # emits file_path on successful save

    def __init__(self, db_path: str, scan_folders: list[str], parent=None):
        super().__init__(parent)
        self.db_path      = db_path
        self.scan_folders = scan_folders
        self._verified    = False   # True once Verify passes

        self.setWindowTitle("New File Creator")
        self.setMinimumSize(680, 600)
        self.setStyleSheet(_STYLE)
        self._build()
        self._on_round_changed()   # populate free O-numbers for default round size

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 12, 14, 12)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Round size ──────────────────────────────────────────────────
        self._round_combo = QComboBox()
        for rs, o_min, o_max in _ROUND_TO_O_RANGE:
            self._round_combo.addItem(
                f'{rs:.2f}"  (O{o_min:05d}–O{o_max:05d})',
                userData=(rs, o_min, o_max))
        self._round_combo.currentIndexChanged.connect(self._on_round_changed)
        form.addRow("Round Size:", self._round_combo)

        # ── Free O-number dropdown ───────────────────────────────────────
        o_row = QHBoxLayout()
        self._onum_combo = QComboBox()
        self._onum_combo.setMinimumWidth(140)
        self._onum_combo.currentIndexChanged.connect(self._on_onum_combo_changed)
        o_row.addWidget(self._onum_combo)

        o_row.addWidget(QLabel("  or type:"))
        self._onum_edit = QLineEdit()
        self._onum_edit.setPlaceholderText("O65200")
        self._onum_edit.setFixedWidth(90)
        self._onum_edit.textChanged.connect(self._on_onum_typed)
        o_row.addWidget(self._onum_edit)

        self._check_btn = QPushButton("Check")
        self._check_btn.setFixedWidth(60)
        self._check_btn.clicked.connect(self._on_check)
        o_row.addWidget(self._check_btn)

        self._onum_status = QLabel("")
        self._onum_status.setFixedWidth(200)
        o_row.addWidget(self._onum_status)
        o_row.addStretch()
        form.addRow("O-Number:", o_row)

        # ── Target folder ────────────────────────────────────────────────
        self._folder_combo = QComboBox()
        for f in self.scan_folders:
            self._folder_combo.addItem(os.path.basename(f), userData=f)
        form.addRow("Save To:", self._folder_combo)

        root.addLayout(form)

        # ── G-code editor ────────────────────────────────────────────────
        root.addWidget(QLabel("G-Code:"))
        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText(
            "% \nO##### (TITLE)\n...\nM30\n%")
        self._editor.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._editor.textChanged.connect(self._on_code_changed)
        root.addWidget(self._editor, stretch=1)

        # ── Verify result label ──────────────────────────────────────────
        self._verify_lbl = QLabel("")
        self._verify_lbl.setWordWrap(True)
        self._verify_lbl.setStyleSheet("font-size: 11px; color: #667788;")
        root.addWidget(self._verify_lbl)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._verify_btn = QPushButton("Verify")
        self._verify_btn.setStyleSheet(
            "QPushButton { background:#0a1a1a; border:1px solid #44ddcc; "
            "color:#44ddcc; padding:5px 16px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#0e2424; }"
        )
        self._verify_btn.clicked.connect(self._on_verify)
        btn_row.addWidget(self._verify_btn)

        self._save_btn = QPushButton("Save && Add")
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            "QPushButton { background:#0a2a0a; border:1px solid #44dd88; "
            "color:#44dd88; padding:5px 16px; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { background:#0e3812; }"
            "QPushButton:disabled { color:#334433; border-color:#1a2a1a; }"
        )
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_range(self) -> tuple[float, int, int]:
        return self._round_combo.currentData()

    def _used_o_ints(self) -> set[int]:
        """Return set of O-number integers already in the DB."""
        try:
            conn = db.get_connection(self.db_path)
            rows = conn.execute(
                "SELECT o_number FROM files WHERE o_number IS NOT NULL"
            ).fetchall()
            conn.close()
            result = set()
            for r in rows:
                m = _O_RE.match(r["o_number"] or "")
                if m:
                    result.add(int(m.group(1)))
            return result
        except Exception:
            return set()

    def _active_o_number(self) -> str:
        """Return the O-number to use: typed value takes priority over combo."""
        typed = self._onum_edit.text().strip().upper()
        if typed:
            return typed if typed.startswith("O") else f"O{typed}"
        return self._onum_combo.currentData() or ""

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_round_changed(self):
        data = self._round_combo.currentData()
        if not data:
            return
        _, o_min, o_max = data
        used = self._used_o_ints()
        free = [o for o in range(o_min, o_max + 1) if o not in used]

        self._onum_combo.blockSignals(True)
        self._onum_combo.clear()
        for o in free[:_FREE_CAP]:
            label = f"O{o:05d}"
            self._onum_combo.addItem(label, userData=label)
        if not free:
            self._onum_combo.addItem("(no free O-numbers)", userData="")
        self._onum_combo.blockSignals(False)
        self._onum_edit.clear()
        self._onum_status.setText("")
        self._verified = False
        self._save_btn.setEnabled(False)

    def _on_onum_combo_changed(self):
        self._onum_edit.clear()
        self._onum_status.setText("")
        self._verified = False
        self._save_btn.setEnabled(False)

    def _on_onum_typed(self, text: str):
        self._onum_status.setText("")
        self._verified = False
        self._save_btn.setEnabled(False)

    def _on_code_changed(self):
        self._verified = False
        self._save_btn.setEnabled(False)
        self._verify_lbl.setText("")

    def _on_check(self):
        typed = self._onum_edit.text().strip().upper()
        if not typed:
            self._onum_status.setText("Type an O-number first.")
            return
        onum = typed if typed.startswith("O") else f"O{typed}"
        m = _O_RE.match(onum)
        if not m:
            self._onum_status.setStyleSheet("color:#ff6666; font-size:11px;")
            self._onum_status.setText("Invalid format (O + 4–6 digits)")
            return

        o_int = int(m.group(1))
        _, o_min, o_max = self._current_range()
        if not (o_min <= o_int <= o_max):
            self._onum_status.setStyleSheet("color:#ffaa44; font-size:11px;")
            self._onum_status.setText(
                f"Out of range for this round size (O{o_min:05d}–O{o_max:05d})")
            return

        used = self._used_o_ints()
        if o_int in used:
            self._onum_status.setStyleSheet("color:#ff6666; font-size:11px;")
            self._onum_status.setText(f"{onum} is already in use")
        else:
            self._onum_status.setStyleSheet("color:#44dd88; font-size:11px;")
            self._onum_status.setText(f"{onum} is free ✓")

    def _on_verify(self):
        from direct_scorer import score_file
        code = self._editor.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "No Code", "Enter G-code before verifying.")
            return

        onum = self._active_o_number()
        if not onum:
            QMessageBox.warning(self, "No O-Number", "Select or enter an O-number first.")
            return

        # Extract program title from code (first O-number line comment)
        title = ""
        for line in code.splitlines():
            m = re.match(r'^O\d{4,6}\s*\(([^)]*)\)', line.strip(), re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                break

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".nc", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(code)
                tmp_path = tmp.name

            score, vstatus = score_file(tmp_path, title, o_number=onum)
        except Exception as exc:
            self._verify_lbl.setStyleSheet("color:#ff6666; font-size:11px;")
            self._verify_lbl.setText(f"Verify error: {exc}")
            return
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        tokens = vstatus.split() if vstatus else []
        has_fail = any(t.upper().endswith(":FAIL") for t in tokens)
        color = "#ff6666" if has_fail else "#44dd88"
        self._verify_lbl.setStyleSheet(f"color:{color}; font-size:11px;")
        self._verify_lbl.setText(
            f"Score: {score}/6    {vstatus or '(no status)'}")
        self._verified = True
        self._save_btn.setEnabled(True)

    def _on_save(self):
        onum = self._active_o_number()
        if not onum:
            QMessageBox.warning(self, "No O-Number", "Select or enter an O-number first.")
            return

        # Validate O-number format
        if not _O_RE.match(onum):
            QMessageBox.warning(self, "Invalid O-Number",
                                f"'{onum}' is not a valid O-number.")
            return

        # Target folder
        folder = self._folder_combo.currentData()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "No Folder",
                                "Select a valid target folder.")
            return

        # Check for existing file on disk
        file_path = os.path.join(folder, onum)
        if os.path.exists(file_path):
            reply = QMessageBox.question(
                self, "File Exists",
                f"{onum} already exists in that folder. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Write file
        code = self._editor.toPlainText()
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as exc:
            QMessageBox.critical(self, "Write Error", str(exc))
            return

        # Hash + line count
        try:
            h = xxhash.xxh128()
            line_count = 0
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
            file_hash = h.hexdigest()
        except Exception as exc:
            QMessageBox.critical(self, "Hash Error", str(exc))
            return

        # Extract title from code
        title = ""
        for line in code.splitlines():
            m = re.match(r'^O\d{4,6}\s*\(([^)]*)\)', line.strip(), re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                break

        # Re-verify on actual file for accurate status
        try:
            from direct_scorer import score_file
            score, vstatus = score_file(file_path, title, o_number=onum)
        except Exception:
            score, vstatus = 0, ""

        now = datetime.datetime.now().isoformat()
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()

        created_note = f"[NEW FILE] Created {now[:10]} {now[11:16]}"

        record = {
            "file_path":         file_path,
            "file_name":         onum,
            "o_number":          onum.upper(),
            "o_suffix":          None,
            "file_hash":         file_hash,
            "line_count":        line_count,
            "program_title":     title,
            "derived_from":      "",
            "source_folder":     folder,
            "status":            "active",
            "verify_status":     vstatus,
            "verify_score":      score,
            "has_dup_flag":      0,
            "created_via":       "new_file_creator",
            "notes":             created_note,
            "last_seen":         now,
            "last_modified":     mtime,
            "index_date":        now,
        }

        try:
            conn = db.get_connection(self.db_path)
            with conn:
                db.upsert_file(self.db_path, conn, record)
            conn.close()
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
            return

        self.file_created.emit(file_path)
        QMessageBox.information(
            self, "File Created",
            f"{onum} saved to:\n{file_path}\n\nScore: {score}/6"
        )
        self.accept()
