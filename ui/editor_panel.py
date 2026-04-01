"""
CNC Direct Editor — In-place file editor panel.

Saves directly to the original file path.
Before overwriting, creates a versioned backup in:
  {backup_folder}/{file_name_no_ext}/{file_name}_{YYYY-MM-DD_HHMMSS}{ext}

Signals:
    file_saved(file_id)   — emitted after a successful save + DB update
"""

import os
import re
import shutil
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QMessageBox, QFileDialog, QSplitter, QFrame, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor

import direct_database as db
from direct_scorer import score_file, get_error_lines


# ---------------------------------------------------------------------------
# Minimal G-code syntax highlighter
# ---------------------------------------------------------------------------

class _GcodeHighlighter(QSyntaxHighlighter):

    def __init__(self, doc):
        super().__init__(doc)
        self._rules = []

        def rule(pattern, r, g, b, bold=False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(r, g, b))
            if bold:
                fmt.setFontWeight(700)
            self._rules.append((re.compile(pattern, re.IGNORECASE), fmt))

        rule(r'\(.*?\)',          100, 130, 180)          # comments — blue-grey
        rule(r'\b[GMT]\d+\.?\d*\b', 255, 180,  60, True) # M/G/T codes — amber
        rule(r'\b[XYZIJKF]-?[\d.]+\b', 100, 220, 150)    # coordinates — green
        rule(r'\bO\d{4,6}\b',    200, 150, 255, True)     # O-number — purple
        rule(r'%',               120, 120, 120)            # % delimiters

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ---------------------------------------------------------------------------
# Score badge widget
# ---------------------------------------------------------------------------

class _ScoreBadge(QLabel):

    _COLORS = {6: "#44dd88", 5: "#aadd44", 4: "#aadd44",
               3: "#ffaa33", 2: "#ffaa33", 1: "#ff5555", 0: "#ff5555"}

    def set_score(self, score: int, verify_status: str = ""):
        color = self._COLORS.get(score, "#888888")
        self.setText(f"  Score: {score}/6  ")
        self.setStyleSheet(
            f"color:{color}; font-weight:bold; font-size:13px; "
            f"border:1px solid {color}55; border-radius:4px; padding:2px 6px;"
        )
        self.setToolTip(verify_status)


# ---------------------------------------------------------------------------
# Editor panel
# ---------------------------------------------------------------------------

class EditorPanel(QWidget):

    file_saved = pyqtSignal(int)   # file_id

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path   = db_path
        self._file_id  = None
        self._file_path: str = ""
        self._o_number: str  = ""

        self.setStyleSheet("""
            QWidget    { background: #0d0e18; color: #ccccdd; }
            QLabel     { color: #aaaacc; font-size: 11px; }
            QPushButton {
                background: #1a2030; border: 1px solid #2a2d45;
                color: #aaaacc; padding: 3px 10px;
                border-radius: 3px; font-size: 11px;
            }
            QPushButton:hover { background: #1e2840; }
            QPushButton:disabled { color: #333355; border-color: #1a1d2e; }
            QPlainTextEdit {
                background: #0a0b14; color: #ccccdd;
                border: none; font-family: Consolas, monospace; font-size: 10pt;
                selection-background-color: #1e2a50;
            }
        """)
        self._build()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header strip ──
        header = QWidget()
        header.setStyleSheet("background:#0f1018; border-bottom:1px solid #1a1d2e;")
        header.setFixedHeight(36)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(8, 2, 8, 2)
        hlay.setSpacing(8)

        self._file_lbl = QLabel("No file open")
        self._file_lbl.setStyleSheet(
            "color:#88aacc; font-size:12px; font-weight:bold;")
        hlay.addWidget(self._file_lbl)

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color:#ffcc44; font-size:11px;")
        hlay.addWidget(self._dirty_lbl)

        hlay.addStretch()

        self._score_badge = _ScoreBadge()
        self._score_badge.setText("")
        hlay.addWidget(self._score_badge)

        self._verify_btn = QPushButton("Verify")
        self._verify_btn.setFixedWidth(70)
        self._verify_btn.clicked.connect(self._on_verify)
        self._verify_btn.setEnabled(False)
        hlay.addWidget(self._verify_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedWidth(70)
        self._save_btn.setStyleSheet(
            "QPushButton{background:#1a3a1a;border:1px solid #2a5a2a;"
            "color:#88dd88;padding:3px 10px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#255a25;}"
            "QPushButton:disabled{color:#333355;border-color:#1a1d2e;background:#0f1018;}"
        )
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        hlay.addWidget(self._save_btn)

        self._discard_btn = QPushButton("Discard")
        self._discard_btn.setFixedWidth(70)
        self._discard_btn.clicked.connect(self._on_discard)
        self._discard_btn.setEnabled(False)
        hlay.addWidget(self._discard_btn)

        self._revision_btn = QPushButton("Save as Rev…")
        self._revision_btn.setFixedWidth(95)
        self._revision_btn.setStyleSheet(
            "QPushButton{background:#1a1a3a;border:1px solid #3a3a7a;"
            "color:#8888dd;padding:3px 10px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#252560;}"
            "QPushButton:disabled{color:#333355;border-color:#1a1d2e;background:#0f1018;}"
        )
        self._revision_btn.clicked.connect(self._on_save_revision)
        self._revision_btn.setEnabled(False)
        hlay.addWidget(self._revision_btn)

        root.addWidget(header)

        # ── Verify tokens strip ──
        self._tokens_lbl = QLabel("")
        self._tokens_lbl.setStyleSheet(
            "background:#080910; color:#556688; font-size:10px; "
            "padding:2px 10px; border-bottom:1px solid #1a1d2e;"
        )
        self._tokens_lbl.setFixedHeight(20)
        root.addWidget(self._tokens_lbl)

        # ── Editor ──
        self._editor = QPlainTextEdit()
        self._editor.setFont(QFont("Consolas", 10))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = _GcodeHighlighter(self._editor.document())
        self._editor.document().modificationChanged.connect(self._on_dirty_changed)
        root.addWidget(self._editor, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, file_id: int, file_path: str, o_number: str,
                  verify_status: str = "", verify_score: int = 0,
                  scroll_to_line: int = 0):
        """Load a file into the editor."""
        if self._editor.document().isModified():
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Discard them and open the new file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Missing", f"Cannot find:\n{file_path}")
            return

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", str(exc))
            return

        self._file_id   = file_id
        self._file_path = file_path
        self._o_number  = o_number

        self._editor.setPlainText(content)
        self._editor.document().setModified(False)

        self._file_lbl.setText(os.path.basename(file_path))
        self._score_badge.set_score(verify_score, verify_status)
        self._tokens_lbl.setText(verify_status or "  Not verified")
        self._verify_btn.setEnabled(True)
        self._save_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)
        self._revision_btn.setEnabled(True)
        self._dirty_lbl.setText("")
        self._apply_error_highlights()

        if scroll_to_line > 0:
            block = self._editor.document().findBlockByLineNumber(scroll_to_line - 1)
            cursor = self._editor.textCursor()
            cursor.setPosition(block.position())
            self._editor.setTextCursor(cursor)
            self._editor.ensureCursorVisible()

    def has_unsaved_changes(self) -> bool:
        return self._editor.document().isModified()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_dirty_changed(self, modified: bool):
        self._dirty_lbl.setText("● unsaved" if modified else "")
        self._save_btn.setEnabled(modified and bool(self._file_path))
        self._discard_btn.setEnabled(modified)

    def _on_verify(self):
        if not self._file_path:
            return
        # Save to a temp string and verify from current on-disk content
        # (if dirty, inform user we're verifying saved version)
        if self._editor.document().isModified():
            QMessageBox.information(self, "Verify",
                "Showing verification for the last saved version.\n"
                "Save first to verify your current edits.")
        row = db.get_file_by_path(self.db_path, self._file_path)
        title = row["program_title"] if row else ""
        score, vstatus = score_file(self._file_path, title, o_number=self._o_number)
        self._score_badge.set_score(score, vstatus)
        self._tokens_lbl.setText(vstatus or "  No result")
        self._apply_error_highlights()

    def _on_save(self):
        if not self._file_path or not self._editor.document().isModified():
            return

        # ── Backup ──
        if self.db_path:
            auto_bak = db.get_setting(self.db_path, "auto_backup_on_edit", "1") == "1"
            if auto_bak:
                self._create_backup()

        # ── Write file ──
        content = self._editor.toPlainText()
        try:
            with open(self._file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return

        self._editor.document().setModified(False)

        # ── Update DB ──
        if self._file_id and self.db_path:
            self._update_db_after_save()

        self.file_saved.emit(self._file_id or 0)

    def _on_discard(self):
        if not self._editor.document().isModified():
            return
        reply = QMessageBox.question(
            self, "Discard Changes",
            "Discard all unsaved changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with open(self._file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self._editor.setPlainText(content)
            self._editor.document().setModified(False)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------
    # Save as Revision
    # ------------------------------------------------------------------

    def _on_save_revision(self):
        if not self._file_path or not self._file_id:
            return

        from PyQt6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QTextEdit as _QTE
        dlg = QDialog(self)
        dlg.setWindowTitle("Save as Revision")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(
            "QDialog{background:#0d0e18;color:#ccccdd;}"
            "QLabel{color:#aaaacc;} "
            "QLineEdit{background:#1a1d2e;border:1px solid #2a2d45;"
            "color:#ccccdd;padding:4px;border-radius:3px;}"
            "QTextEdit{background:#1a1d2e;border:1px solid #2a2d45;"
            "color:#ccccdd;padding:4px;border-radius:3px;}"
        )
        form = QFormLayout(dlg)
        form.setSpacing(8)
        form.setContentsMargins(14, 14, 14, 10)

        from PyQt6.QtWidgets import QLineEdit as _QLE
        label_edit = _QLE()
        label_edit.setPlaceholderText("e.g. Rev A, Before drill change, Approved 2026-03-27")
        form.addRow("Label:", label_edit)

        notes_edit = _QTE()
        notes_edit.setFixedHeight(72)
        notes_edit.setPlaceholderText("Optional notes…")
        form.addRow("Notes:", notes_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        label = label_edit.text().strip()
        if not label:
            QMessageBox.warning(self, "Label Required", "Enter a revision label.")
            return

        # Save current file state as backup
        backup_path = self._create_named_backup(label)
        if not backup_path:
            QMessageBox.warning(self, "Backup Failed",
                "Could not create backup file. Set a backup folder in Settings.")
            return

        import datetime
        db.save_revision(
            self.db_path, self._file_id, label,
            notes_edit.toPlainText().strip(),
            backup_path,
            datetime.datetime.now().isoformat()
        )
        QMessageBox.information(self, "Revision Saved",
            f"Revision '{label}' saved.\nBackup: {backup_path}")

    def _create_named_backup(self, label: str) -> str:
        """Create a backup copy for a named revision. Returns backup path or ''."""
        backup_folder = db.get_setting(self.db_path, "backup_folder", "") if self.db_path else ""
        if not backup_folder or not os.path.isdir(backup_folder):
            return ""
        import datetime, re as _re
        fname      = os.path.basename(self._file_path)
        name_noext = os.path.splitext(fname)[0]
        safe_label = _re.sub(r'[^\w\- ]', '_', label)[:40].strip()
        timestamp  = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ext        = os.path.splitext(fname)[1]
        sub_dir    = os.path.join(backup_folder, name_noext)
        os.makedirs(sub_dir, exist_ok=True)
        bak_path   = os.path.join(sub_dir, f"{name_noext}_{safe_label}_{timestamp}{ext}")
        try:
            shutil.copy2(self._file_path, bak_path)
            return bak_path
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Error highlighting
    # ------------------------------------------------------------------

    def _apply_error_highlights(self):
        """Highlight lines that have verification issues using ExtraSelections."""
        if not self._file_path or not os.path.exists(self._file_path):
            self._editor.setExtraSelections([])
            return

        row = db.get_file_by_path(self.db_path, self._file_path) if self.db_path else None
        title = row["program_title"] if row else ""

        try:
            error_lines = get_error_lines(self._file_path, title,
                                          o_number=self._o_number)
        except Exception:
            self._editor.setExtraSelections([])
            return

        doc = self._editor.document()
        selections = []

        # Two severity levels: context window = orange, direct violation = red
        _direct_keys = {"FR:", "Z:", "HM:"}

        for line_no, tooltip in error_lines.items():
            block = doc.findBlockByLineNumber(line_no - 1)  # 0-based
            if not block.isValid():
                continue

            is_direct = any(tooltip.startswith(k) for k in _direct_keys)
            bg = QColor("#2a0a0a") if is_direct else QColor("#1e1200")
            fg = QColor("#ff6666") if is_direct else QColor("#ffaa44")

            fmt = QTextCharFormat()
            fmt.setBackground(bg)
            fmt.setForeground(fg)
            fmt.setProperty(QTextCharFormat.Property.FullWidthSelection, True)

            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            sel.cursor = QTextCursor(block)
            sel.cursor.clearSelection()
            sel.tooltip = tooltip
            selections.append(sel)

        self._editor.setExtraSelections(selections)

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def _create_backup(self):
        backup_folder = db.get_setting(self.db_path, "backup_folder", "")
        if not backup_folder:
            # First use — ask user to pick
            folder = QFileDialog.getExistingDirectory(
                self, "Choose Backup Folder (one-time setup)",
                "", QFileDialog.Option.ShowDirsOnly
            )
            if not folder:
                return   # user skipped — still save without backup
            backup_folder = os.path.normpath(folder)
            db.set_setting(self.db_path, "backup_folder", backup_folder)

        ext       = db.get_setting(self.db_path, "backup_extension", ".bak")
        fname     = os.path.basename(self._file_path)
        name_noext, orig_ext = os.path.splitext(fname)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        bak_name  = f"{name_noext}_{timestamp}{orig_ext}"

        # Subfolder named after the file (created on first edit of that file)
        sub_dir = os.path.join(backup_folder, name_noext)
        os.makedirs(sub_dir, exist_ok=True)

        bak_path = os.path.join(sub_dir, bak_name)
        try:
            shutil.copy2(self._file_path, bak_path)
        except Exception as exc:
            QMessageBox.warning(self, "Backup Warning",
                f"Could not create backup:\n{exc}\n\nFile will still be saved.")

    # ------------------------------------------------------------------
    # DB update after save
    # ------------------------------------------------------------------

    def _update_db_after_save(self):
        try:
            from direct_scanner import _extract_header_info, _count_lines
            import xxhash
            h = xxhash.xxh128()
            title = ""
            derived = ""
            header_found = False
            with open(self._file_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
                    if not header_found:
                        title, derived, _internal_o, _has_gc = _extract_header_info(chunk)
                        header_found = True
            file_hash  = h.hexdigest()
            line_count = _count_lines(self._file_path)
            score, vstatus = score_file(self._file_path, title, o_number=self._o_number)
            mtime = datetime.datetime.fromtimestamp(
                os.path.getmtime(self._file_path)).isoformat()

            # Preserve existing has_dup_flag
            row = db.get_file_by_id(self.db_path, self._file_id)
            has_dup = row["has_dup_flag"] if row else 0

            db.update_file_after_edit(
                self.db_path, self._file_id,
                file_hash, line_count, title, derived,
                vstatus, score, has_dup, mtime
            )
            self._score_badge.set_score(score, vstatus)
            self._tokens_lbl.setText(vstatus or "  Not verified")
            self._apply_error_highlights()
        except Exception as exc:
            QMessageBox.warning(self, "DB Update Warning", str(exc))
