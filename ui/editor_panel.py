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
import tempfile

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QMessageBox, QFileDialog, QSplitter, QFrame,
    QTextEdit, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect
from PyQt6.QtGui import QFont, QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QPainter

import direct_database as db
import verifier as _vfy
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
# Line-number gutter (painted outside the document — not selectable)
# ---------------------------------------------------------------------------

class _LineNumberArea(QWidget):
    """Gutter widget that displays line numbers for a _CodeEditor."""

    def __init__(self, editor: "_CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint(event)


class _CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with a non-selectable line-number gutter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self._update_line_area_width()

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_area_width(self, _count=0):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_area(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(),
                                   self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_area_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(),
                  self.line_number_area_width(), cr.height()))

    def line_number_area_paint(self, event):
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor("#0a0b14"))

        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        painter.setFont(QFont("Consolas", 9))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor("#3a3d55"))
                painter.drawText(
                    0, top,
                    self._line_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(block_num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

        painter.end()


# ---------------------------------------------------------------------------
# Score badge widget
# ---------------------------------------------------------------------------

class _ScoreBadge(QLabel):

    _COLORS = {7: "#44dd88", 6: "#aadd44", 5: "#aadd44",
               4: "#ffaa33", 3: "#ffaa33", 2: "#ff5555", 1: "#ff5555", 0: "#ff5555"}

    def set_score(self, score: int, verify_status: str = ""):
        color = self._COLORS.get(score, "#888888")
        self.setText(f"  Score: {score}/7  ")
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

        self._save_verify_btn = QPushButton("Save + Verify")
        self._save_verify_btn.setFixedWidth(100)
        self._save_verify_btn.setStyleSheet(
            "QPushButton{background:#1a2a3a;border:1px solid #2a4a6a;"
            "color:#66aadd;padding:3px 10px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#1e3a50;}"
            "QPushButton:disabled{color:#333355;border-color:#1a1d2e;background:#0f1018;}"
        )
        self._save_verify_btn.clicked.connect(self._on_save_and_verify)
        self._save_verify_btn.setEnabled(False)
        hlay.addWidget(self._save_verify_btn)

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

        # ── Splitter: Editor (left) | Verify Results (right) ──
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setStyleSheet(
            "QSplitter::handle{background:#1a1d2e; width:3px;}")

        # Left: code editor (with line-number gutter)
        self._editor = _CodeEditor()
        self._editor.setFont(QFont("Consolas", 10))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = _GcodeHighlighter(self._editor.document())
        self._editor.document().modificationChanged.connect(self._on_dirty_changed)
        self._splitter.addWidget(self._editor)

        # Right: verify results panel
        self._verify_pane = QWidget()
        self._verify_pane.setStyleSheet("background:#0d0e18;")
        vp_layout = QVBoxLayout(self._verify_pane)
        vp_layout.setContentsMargins(0, 0, 0, 0)
        vp_layout.setSpacing(0)

        vp_hdr = QLabel("  Verification Results")
        vp_hdr.setStyleSheet(
            "background:#0f1018; color:#88aacc; font-size:11px; "
            "font-weight:bold; padding:4px 8px; border-bottom:1px solid #1a1d2e;")
        vp_hdr.setFixedHeight(28)
        vp_layout.addWidget(vp_hdr)

        self._verify_scroll = QScrollArea()
        self._verify_scroll.setWidgetResizable(True)
        self._verify_scroll.setStyleSheet(
            "QScrollArea{border:none; background:#0d0e18;}"
            "QScrollBar:vertical{background:#0a0b14;width:8px;}"
            "QScrollBar::handle:vertical{background:#2a2d45;border-radius:4px;}"
        )
        self._verify_content = QWidget()
        self._verify_content_lay = QVBoxLayout(self._verify_content)
        self._verify_content_lay.setContentsMargins(6, 6, 6, 6)
        self._verify_content_lay.setSpacing(4)
        self._verify_content_lay.addStretch()
        self._verify_scroll.setWidget(self._verify_content)
        vp_layout.addWidget(self._verify_scroll, stretch=1)

        self._splitter.addWidget(self._verify_pane)

        # Default sizes: 65% editor, 35% verify
        self._splitter.setSizes([650, 350])
        # Start with verify panel hidden until first verify
        self._verify_pane.hide()

        root.addWidget(self._splitter, stretch=1)

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
        self._save_verify_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)
        self._revision_btn.setEnabled(True)
        self._dirty_lbl.setText("")
        self._apply_error_highlights()

        # Auto-run verification and show side panel
        try:
            row = db.get_file_by_path(self.db_path, file_path) if self.db_path else None
            title = row["program_title"] if row else ""
            result = _vfy.verify_file(file_path, title,
                                       o_number=o_number or None)
            self._populate_verify_panel(result or {})
        except Exception:
            pass  # non-fatal — panel just stays empty

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
        self._save_verify_btn.setEnabled(modified and bool(self._file_path))
        self._discard_btn.setEnabled(modified)

    def _on_verify(self):
        """Verify the current editor text (writes to temp file if unsaved)."""
        if not self._file_path:
            return
        result, score, vstatus = self._run_verify_on_editor_text()
        self._score_badge.set_score(score, vstatus)
        self._tokens_lbl.setText(vstatus or "  No result")
        self._apply_error_highlights()
        self._populate_verify_panel(result)

    def _on_save_and_verify(self):
        """Save the file then immediately verify and show results."""
        self._on_save()
        if not self._editor.document().isModified():
            # Save succeeded — run verify on saved file
            self._on_verify()

    def _run_verify_on_editor_text(self):
        """Run verification on current editor content. Returns (result, score, vstatus)."""
        row = db.get_file_by_path(self.db_path, self._file_path) if self.db_path else None
        title = row["program_title"] if row else ""

        if self._editor.document().isModified():
            # Write editor text to a temp file for verification
            content = self._editor.toPlainText()
            # Re-extract title from the edited content
            lines = content.split("\n")
            for ln in lines[:5]:
                m = re.match(r'^O\d{4,6}\s*\((.+?)\)', ln.strip(), re.IGNORECASE)
                if m:
                    title = m.group(1).strip()
                    break
            try:
                fd, tmp = tempfile.mkstemp(suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                result = _vfy.verify_file(tmp, title,
                                          o_number=self._o_number or None)
                score, vstatus = score_file(tmp, title, o_number=self._o_number)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        else:
            result = _vfy.verify_file(self._file_path, title,
                                      o_number=self._o_number or None)
            score, vstatus = score_file(self._file_path, title,
                                        o_number=self._o_number)
        return result or {}, score, vstatus

    def _on_save(self):
        if not self._file_path or not self._editor.document().isModified():
            return

        # ── Backup (non-fatal — never block save) ──
        try:
            if self.db_path:
                auto_bak = db.get_setting(self.db_path, "auto_backup_on_edit", "1") == "1"
                if auto_bak:
                    self._create_backup()
        except Exception as exc:
            QMessageBox.warning(self, "Backup Warning",
                f"Could not create backup (file will still be saved):\n{exc}")

        # ── Write file ──
        content = self._editor.toPlainText()
        try:
            with open(self._file_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return

        self._editor.document().setModified(False)

        # ── Update DB + re-verify ──
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
                QMessageBox.warning(self, "No Backup Created",
                    "No backup folder was chosen.\n\n"
                    "The file will be saved, but without a backup copy.\n"
                    "You can set a backup folder in Settings at any time.")
                return
            backup_folder = os.path.normpath(folder)
            db.set_setting(self.db_path, "backup_folder", backup_folder)
        elif not os.path.isdir(backup_folder):
            QMessageBox.warning(self, "Backup Folder Missing",
                f"Your backup folder no longer exists:\n{backup_folder}\n\n"
                "The file will be saved without a backup.\n"
                "Please update your backup folder in Settings.")
            return

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
            # Auto-populate side panel after save
            result = _vfy.verify_file(self._file_path, title,
                                       o_number=self._o_number or None)
            self._populate_verify_panel(result or {})
        except Exception as exc:
            QMessageBox.warning(self, "DB Update Warning", str(exc))

    # ------------------------------------------------------------------
    # Side-panel verify results
    # ------------------------------------------------------------------

    def _clear_verify_panel(self):
        lay = self._verify_content_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _vp_label(self, text: str, fg: str = "#aaaacc",
                  bg: str = "transparent", bold: bool = False,
                  size: int = 10) -> QLabel:
        lbl = QLabel(text)
        weight = "bold" if bold else "normal"
        lbl.setStyleSheet(
            f"color:{fg}; background:{bg}; font-size:{size}px; "
            f"font-weight:{weight}; padding:2px 6px; font-family:Consolas;")
        lbl.setWordWrap(True)
        return lbl

    def _vp_badge(self, ok) -> QLabel:
        if ok is True:
            return self._vp_label("PASS", "#44ee88", "#0a2a14", bold=True)
        if ok is False:
            return self._vp_label("FAIL", "#ff5555", "#2a0a0a", bold=True)
        if ok == "loose":
            return self._vp_label("LOOSE", "#ffaa33", "#2a1e00", bold=True)
        return self._vp_label("N/F", "#555577", "#12131e", bold=True)

    def _vp_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1a1d2e;")
        sep.setFixedHeight(1)
        return sep

    def _vp_check_row(self, label: str, ok, detail: str,
                      go_to_line: int | None = None):
        """Add one check row to the verify side panel."""
        lay = self._verify_content_lay
        # Badge + label row
        row_w = QWidget()
        row_w.setStyleSheet("background:transparent;")
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 2, 0, 0)
        row_lay.setSpacing(6)
        row_lay.addWidget(self._vp_badge(ok))
        row_lay.addWidget(self._vp_label(label, "#ccccdd", bold=True, size=11))
        row_lay.addStretch()
        if go_to_line is not None and go_to_line > 0:
            go_btn = QPushButton(f"Ln {go_to_line}")
            go_btn.setFixedWidth(60)
            go_btn.setStyleSheet(
                "QPushButton{background:#1a1d2e;border:1px solid #2a2d45;"
                "color:#6688aa;padding:1px 4px;border-radius:2px;font-size:9px;}"
                "QPushButton:hover{background:#252840;}")
            go_btn.clicked.connect(lambda checked, ln=go_to_line: self._goto_line(ln))
            row_lay.addWidget(go_btn)
        lay.addWidget(row_w)
        # Detail text
        if detail:
            lay.addWidget(self._vp_label(detail, "#8899bb", size=9))
        lay.addWidget(self._vp_separator())

    def _goto_line(self, line_no: int):
        block = self._editor.document().findBlockByLineNumber(line_no - 1)
        if block.isValid():
            cursor = self._editor.textCursor()
            cursor.setPosition(block.position())
            self._editor.setTextCursor(cursor)
            self._editor.ensureCursorVisible()

    def _populate_verify_panel(self, result: dict):
        """Fill the side verify panel with check results."""
        self._clear_verify_panel()
        lay = self._verify_content_lay

        if not result or result.get("error"):
            msg = result.get("error", "No verification result") if result else "No result"
            lay.addWidget(self._vp_label(msg, "#ff5555"))
            lay.addStretch()
            self._verify_pane.show()
            return

        specs = result.get("specs") or {}
        rs    = specs.get("round_size_in")
        cb_mm = specs.get("cb_mm")
        ob_mm = specs.get("ob_mm")
        th    = result.get("total_thickness")

        # Spec summary
        parts = []
        if rs:    parts.append(f"Round {rs}\"")
        if cb_mm: parts.append(f"CB {cb_mm:.2f}mm")
        if ob_mm: parts.append(f"OB {ob_mm:.2f}mm")
        if th:    parts.append(f"Thick {th:.3f}\"")
        if parts:
            lay.addWidget(self._vp_label("   ".join(parts), "#8899bb",
                                         "#12131e", size=10))
            lay.addWidget(self._vp_separator())

        def _inch(v):
            return f'{v:.4f}"' if v is not None else "—"

        # ── CB ──
        cb_ok = result.get("cb_ok")
        cb_found = result.get("cb_found_in")
        cb_exp = result.get("cb_expected_in")
        cb_diff = result.get("cb_diff_in")
        cb_detail = f"Found: {_inch(cb_found)}  Expected: {_inch(cb_exp)}"
        if cb_diff is not None:
            cb_detail += f"  Diff: {cb_diff:+.4f}\""
        cb_f_ok = result.get("cb_f_ok")
        if cb_f_ok is not None:
            f_status = "OK" if cb_f_ok else "HIGH"
            cb_detail += f"\nFinish feed: F{result.get('cb_f_found', '?'):.3f} (max F{result.get('cb_f_expected', 0.015):.3f}) [{f_status}]"
        # Rough bore F-value check (underbore passes must use F0.02, finish must use F0.015)
        rb_rough_f_ok = result.get("rb_rough_f_ok")
        rb_finish_f_ok = result.get("rb_finish_f_ok")
        rb_finish_f_found = result.get("rb_finish_f_found")
        if rb_rough_f_ok is not None:
            status = "OK" if rb_rough_f_ok else "FAIL"
            cb_detail += f"\nRough passes F: [{status}] (must be ≤F0.020)"
        if rb_finish_f_ok is not None and cb_f_ok is None:
            # Only show if not already shown via cb_f_ok above
            f_str = f"F{rb_finish_f_found:.3f}" if rb_finish_f_found is not None else "F?"
            status = "OK" if rb_finish_f_ok else "HIGH"
            cb_detail += f"\nFinish feed: {f_str} (max F0.015) [{status}]"
        cb_ln = result.get("cb_context_hit_ln")
        self._vp_check_row("CB  (Center Bore)", cb_ok, cb_detail, cb_ln)

        # ── OB ──
        ob_ok = result.get("ob_ok")
        ob_found = result.get("ob_found_in")
        ob_exp = result.get("ob_expected_in")
        ob_diff = result.get("ob_diff_in")
        ob_detail = f"Found: {_inch(ob_found)}  Expected: {_inch(ob_exp)}"
        if ob_diff is not None:
            ob_detail += f"  Diff: {ob_diff:+.4f}\""
        ob_ln = result.get("ob_context_hit_ln")
        self._vp_check_row("OB  (Outer Bore)", ob_ok, ob_detail, ob_ln)

        # ── DR ──
        dr_ok = result.get("dr_ok")
        dr_depths = result.get("dr_depths") or []
        dr_expected = result.get("dr_expected")
        found_str = "  ".join(f'{d:.4f}"' for d in dr_depths) or "—"
        if dr_expected is not None:
            exp_str = (f'{dr_expected:.4f}"'
                       if len(dr_depths) <= 1
                       else f"sum >= {dr_expected:.4f}\"")
        else:
            exp_str = "—"
        dr_detail = f"Found: {found_str}\nExpected: {exp_str}"
        dr_note = result.get("dr_note")
        if dr_note:
            dr_detail += f"\n{dr_note}"
        dr_ln = result.get("dr_context_hit_ln")
        self._vp_check_row("DR  (Drill Depth)", dr_ok, dr_detail, dr_ln)

        # ── OD ──
        od_ok = result.get("od_ok")
        op1_od = result.get("od_op1_found")
        op2_od = result.get("od_op2_found")
        od_exp = None
        if rs:
            od_rs = round(rs * 4) / 4
            od_exp = _vfy._OD_TABLE.get(od_rs)
        od_parts = []
        if op1_od is not None: od_parts.append(f"OP1: {op1_od:.4f}\"")
        if op2_od is not None: od_parts.append(f"OP2: {op2_od:.4f}\"")
        od_detail = "Found: " + ("  ".join(od_parts) if od_parts else "—")
        if od_exp is not None:
            od_detail += f"\nExpected: {od_exp:.4f}\""
        od_ln = result.get("od_op1_context_hit_ln") or result.get("od_op2_context_hit_ln")
        self._vp_check_row("OD  (OD Turn)", od_ok, od_detail, od_ln)

        # ── TZ ──
        tz_ok = result.get("tz_ok")
        tz_op1 = result.get("tz_op1_z")
        tz_op2 = result.get("tz_op2_z")
        tz_lim = result.get("tz_limit")
        tz_parts = []
        if tz_op1 is not None: tz_parts.append(f"OP1: Z{tz_op1:.4f}")
        if tz_op2 is not None: tz_parts.append(f"OP2: Z{tz_op2:.4f}")
        tz_detail = "Found: " + ("  ".join(tz_parts) if tz_parts else "—")
        if tz_lim is not None:
            tz_detail += f"\nLimit: Z{tz_lim:.4f}"
        tz_note = result.get("tz_note")
        if tz_note:
            tz_detail += f"\n{tz_note}"
        tz_ln = result.get("tz_context_hit_ln")
        self._vp_check_row("TZ  (Turning Z)", tz_ok, tz_detail, tz_ln)

        # ── PC ──
        pc_ok = result.get("pcode_ok")
        op1_p = result.get("op1_p")
        op2_p = result.get("op2_p")
        pc_exp = result.get("pcode_expected")
        found_str = f"P{op1_p}/P{op2_p}" if op1_p and op2_p else "—"
        exp_str = f"P{pc_exp[0]}/P{pc_exp[1]}" if pc_exp else "—"
        pc_detail = f"Found: {found_str}  Expected: {exp_str}"
        pc_lathe = result.get("pcode_lathe") or ""
        if pc_lathe:
            pc_detail += f"  ({pc_lathe})"
        pc_ln = result.get("pcode_op1_context_hit_ln") or result.get("pcode_op2_context_hit_ln")
        self._vp_check_row("PC  (P-Code)", pc_ok, pc_detail, pc_ln)

        # ── HM ──
        hm_ok = result.get("home_ok")
        hm_found = result.get("home_zs_found") or []
        hm_exp = result.get("home_z_expected")
        found_str = "  ".join(f"Z{z:.4f}" for z in hm_found) or "—"
        hm_detail = f"Found: {found_str}"
        if hm_exp is not None:
            hm_detail += f"\nExpected: >= Z{hm_exp:.4f}"
        hm_ln = result.get("home_context_hit_ln")
        self._vp_check_row("HM  (Home Position)", hm_ok, hm_detail, hm_ln)

        lay.addStretch()
        self._verify_pane.show()
