"""
Side-by-side diff panel.
Shows two files with colored line-by-line differences.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QTextEdit, QSplitter, QFrame, QScrollBar,
    QStackedWidget, QPushButton, QPlainTextEdit, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont, QFontMetrics

import diff_engine as engine
import verifier


# Color scheme for diff lines
BG_EQUAL   = QColor("#1e1e2e")
BG_INSERT  = QColor("#1a3a1a")
BG_DELETE  = QColor("#3a1a1a")
BG_REPLACE = QColor("#3a3010")
BG_EMPTY   = QColor("#141420")

FG_EQUAL   = QColor("#cccccc")
FG_INSERT  = QColor("#88dd88")
FG_DELETE  = QColor("#dd8888")
FG_REPLACE = QColor("#ddcc66")
FG_EMPTY   = QColor("#333355")

LINE_NO_FG = QColor("#555577")


class DiffTextEdit(QTextEdit):
    """Read-only text widget for one side of the diff."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e;
                color: #cccccc;
                border: none;
                padding: 4px;
            }
        """)


class DiffPanel(QWidget):
    """
    Side-by-side diff viewer with integrated G-code editor.
    Call compare(path_a, name_a, path_b, name_b) to load a comparison.
    Call show_editor(path, name, title) to open the in-app editor.
    """

    # Emitted when the user saves a file in the editor: (file_path, working_name, title)
    file_saved = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editor_path  = None
        self._editor_name  = None
        self._editor_title = None
        self._editor_dirty = False
        self._verify_path  = None
        self._verify_name  = None
        self._verify_title = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar (shared across all views)
        header_bar = QWidget()
        header_bar.setStyleSheet("QWidget { background: #12131f; border-bottom: 1px solid #2a2d45; }")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(10, 4, 10, 4)
        header_layout.setSpacing(8)

        self._header = QLabel("Select two files to compare")
        self._header.setStyleSheet("color: #8888aa; font-size: 12px; background: transparent; border: none;")
        header_layout.addWidget(self._header, stretch=1)

        btn_style = """
            QPushButton {
                background: #1a2040; color: #88aaff; border: 1px solid #2a3060;
                padding: 3px 12px; font-size: 11px; border-radius: 3px;
            }
            QPushButton:hover { background: #253070; }
            QPushButton:pressed { background: #1a2050; }
        """

        self._edit_btn = QPushButton("Edit File")
        self._edit_btn.setStyleSheet(btn_style)
        self._edit_btn.setVisible(False)
        self._edit_btn.clicked.connect(self._on_edit_clicked)
        header_layout.addWidget(self._edit_btn)

        layout.addWidget(header_bar)

        # Stats bar
        self._stats = QLabel("")
        self._stats.setStyleSheet("""
            QLabel {
                background: #0f1020;
                color: #aaaacc;
                padding: 4px 10px;
                font-size: 11px;
                border-bottom: 1px solid #1a1d35;
            }
        """)
        self._stats.setVisible(False)
        layout.addWidget(self._stats)

        # Stacked widget: index 0 = diff/verify view, index 1 = editor view
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # --- Page 0: Diff / Verify view ---
        diff_page = QWidget()
        diff_layout = QVBoxLayout(diff_page)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.setStyleSheet("QSplitter::handle { background: #2a2d45; }")

        self._left_label = QLabel()
        self._right_label = QLabel()
        for lbl in (self._left_label, self._right_label):
            lbl.setStyleSheet("""
                QLabel {
                    background: #12131f;
                    color: #9999bb;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-style: italic;
                    border-bottom: 1px solid #1a1d35;
                }
            """)

        left_container = QWidget()
        left_layout_inner = QVBoxLayout(left_container)
        left_layout_inner.setContentsMargins(0, 0, 0, 0)
        left_layout_inner.setSpacing(0)
        left_layout_inner.addWidget(self._left_label)
        self._left_edit = DiffTextEdit()
        left_layout_inner.addWidget(self._left_edit)

        right_container = QWidget()
        right_layout_inner = QVBoxLayout(right_container)
        right_layout_inner.setContentsMargins(0, 0, 0, 0)
        right_layout_inner.setSpacing(0)
        right_layout_inner.addWidget(self._right_label)
        self._right_edit = DiffTextEdit()
        right_layout_inner.addWidget(self._right_edit)

        splitter.addWidget(left_container)
        splitter.addWidget(right_container)
        diff_layout.addWidget(splitter)

        # Sync scrolling
        self._left_edit.verticalScrollBar().valueChanged.connect(
            self._right_edit.verticalScrollBar().setValue
        )
        self._right_edit.verticalScrollBar().valueChanged.connect(
            self._left_edit.verticalScrollBar().setValue
        )

        self._stack.addWidget(diff_page)  # index 0

        # --- Page 1: Editor view ---
        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        # Editor toolbar
        editor_toolbar = QWidget()
        editor_toolbar.setStyleSheet("QWidget { background: #0f1020; border-bottom: 1px solid #2a2d45; }")
        et_layout = QHBoxLayout(editor_toolbar)
        et_layout.setContentsMargins(10, 4, 10, 4)
        et_layout.setSpacing(8)

        self._editor_status = QLabel("")
        self._editor_status.setStyleSheet("color: #aaaacc; font-size: 11px; background: transparent; border: none;")
        et_layout.addWidget(self._editor_status, stretch=1)

        save_btn_style = """
            QPushButton {
                background: #1a3a1a; color: #88dd88; border: 1px solid #2a5a2a;
                padding: 4px 16px; font-size: 11px; border-radius: 3px; font-weight: bold;
            }
            QPushButton:hover { background: #255a25; }
            QPushButton:pressed { background: #1a3a1a; }
        """
        close_btn_style = """
            QPushButton {
                background: #2a1a1a; color: #dd8888; border: 1px solid #4a2a2a;
                padding: 4px 12px; font-size: 11px; border-radius: 3px;
            }
            QPushButton:hover { background: #3a2525; }
        """

        self._save_btn = QPushButton("Save && Re-verify")
        self._save_btn.setStyleSheet(save_btn_style)
        self._save_btn.clicked.connect(self._on_save)
        et_layout.addWidget(self._save_btn)

        self._close_editor_btn = QPushButton("Close Editor")
        self._close_editor_btn.setStyleSheet(close_btn_style)
        self._close_editor_btn.clicked.connect(self._on_close_editor)
        et_layout.addWidget(self._close_editor_btn)

        editor_layout.addWidget(editor_toolbar)

        # The actual text editor
        self._code_editor = QPlainTextEdit()
        self._code_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._code_editor.setFont(font)
        self._code_editor.setStyleSheet("""
            QPlainTextEdit {
                background: #1e1e2e;
                color: #cccccc;
                border: none;
                padding: 4px;
                selection-background-color: #3a3d5a;
            }
        """)
        self._code_editor.textChanged.connect(self._on_editor_text_changed)
        editor_layout.addWidget(self._code_editor)

        self._stack.addWidget(editor_page)  # index 1

    # ------------------------------------------------------------------
    # Editor methods
    # ------------------------------------------------------------------

    def show_editor(self, path: str, working_name: str, title: str):
        """Open the in-app G-code editor for the given file."""
        self._editor_path  = path
        self._editor_name  = working_name
        self._editor_title = title
        self._editor_dirty = False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open", f"Error reading file:\n{exc}")
            return
        self._code_editor.blockSignals(True)
        self._code_editor.setPlainText(content)
        self._code_editor.blockSignals(False)
        self._header.setText(f"Editing: {working_name}")
        self._edit_btn.setVisible(False)
        self._stats.setVisible(False)
        self._editor_status.setText(f"{title}")
        self._stack.setCurrentIndex(1)

    def _on_edit_clicked(self):
        """Switch from verify view to editor for the currently-viewed file."""
        if self._verify_path:
            self.show_editor(self._verify_path, self._verify_name, self._verify_title)

    def _on_editor_text_changed(self):
        self._editor_dirty = True
        self._editor_status.setText(f"{self._editor_title}  [modified]")

    def _on_save(self):
        """Save editor content to disk and emit file_saved signal."""
        if not self._editor_path:
            return
        content = self._code_editor.toPlainText()
        try:
            with open(self._editor_path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save:\n{exc}")
            return
        # Re-read title from saved content
        import re as _re
        for ln in content.splitlines():
            s = ln.strip()
            if not s or s == "%":
                continue
            m = _re.match(r'^O\d{4,6}\s*\(([^)]*)\)', s, _re.IGNORECASE)
            if m:
                self._editor_title = m.group(1).strip()
                self._verify_title = self._editor_title
            break
        self._editor_dirty = False
        self._editor_status.setText(f"{self._editor_title}  [saved — re-verifying]")
        self.file_saved.emit(self._editor_path, self._editor_name, self._editor_title)

    def _on_close_editor(self):
        """Close editor and return to verify/diff view."""
        if self._editor_dirty:
            reply = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved changes. Close without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return
        self._stack.setCurrentIndex(0)
        self._editor_dirty = False
        # Re-show verify if we had one loaded
        if self._verify_path:
            self.show_verify(self._verify_path, self._verify_name, self._verify_title)

    def is_editor_open(self) -> bool:
        return self._stack.currentIndex() == 1

    # ------------------------------------------------------------------
    # Diff / Verify methods
    # ------------------------------------------------------------------

    def clear(self):
        self._left_edit.clear()
        self._right_edit.clear()
        self._header.setText("Select two files to compare")
        self._stats.setVisible(False)
        self._edit_btn.setVisible(False)
        self._left_label.setText("")
        self._right_label.setText("")
        self._verify_path  = None
        self._verify_name  = None
        self._verify_title = None
        self._stack.setCurrentIndex(0)

    def show_verify(self, path: str, working_name: str, title: str):
        """Run dimension verification and display results for a single file."""
        self._verify_path  = path
        self._verify_name  = working_name
        self._verify_title = title
        self._stack.setCurrentIndex(0)
        self._header.setText(f"Dimension Verify: {working_name}")
        self._edit_btn.setVisible(True)
        self._stats.setVisible(False)
        self._left_label.setText("  Verification Results")
        self._right_label.setText("  G-Code Context")
        self._left_edit.clear()
        self._right_edit.clear()

        # Extract base O-number from working_name (e.g. "O57006_A.nc" → "O57006")
        import re as _re
        _om = _re.match(r'(O\d{4,6})', working_name, _re.IGNORECASE)
        expected_onum = _om.group(1).upper() if _om else None
        result = verifier.verify_file(path, title, o_number=expected_onum)

        if "error" in result:
            self._left_edit.setPlainText(f"Cannot verify:\n{result['error']}")
            return

        specs      = result["specs"]
        is_step    = specs.get("is_step", False)
        cb_found   = result.get("cb_found_in")
        cb2_found  = result.get("cb2_found_in")
        ob_found   = result.get("ob_found_in")
        cb_ok      = result.get("cb_ok")
        cb2_ok     = result.get("cb2_ok")
        ob_ok      = result.get("ob_ok")
        step_ok    = result.get("step_ok")
        cb_diff    = result.get("cb_diff_in")
        cb2_diff   = result.get("cb2_diff_in")
        ob_diff    = result.get("ob_diff_in")
        step_diff  = result.get("step_diff_in")

        def mm_in(mm):
            return f"{mm:.3f} mm  ({mm / 25.4:.5f}\")"

        def pass_fail(ok, diff_in):
            if ok is None:
                return "NOT FOUND"
            tol = verifier.TOLERANCE_IN
            if ok:
                return f"PASS  (diff {diff_in:+.5f}\"  tol ±{tol}\")"
            return f"FAIL  (diff {diff_in:+.5f}\"  tol ±{tol}\")"

        lines = []
        lines.append("=" * 52)
        lines.append(f"  TITLE   : {title}")
        lines.append(f"  ROUND   : {specs['round_size_in']}\"")
        internal_onum = result.get("internal_o_number")
        o_match = result.get("o_match")
        if expected_onum or internal_onum:
            exp_str = expected_onum or "?"
            int_str = internal_onum or "NOT FOUND"
            if o_match is True:
                lines.append(f"  O-NUMBER: {int_str}  ✓ matches filename")
            elif o_match is False:
                lines.append(f"  O-NUMBER: file={int_str}  MISMATCH — expected {exp_str}")
            else:
                lines.append(f"  O-NUMBER: {int_str}")
        lines.append("")
        cb_label = "  COUNTERBORE (CB)" if is_step else "  CENTER BORE (CB)"
        lines.append(cb_label)
        cb_str = f'{cb_found:.5f}"' if cb_found is not None else 'NOT FOUND'
        cb_offset_mm = 0.2 if abs(specs['cb_mm'] - 116.7) < 0.01 else 0.1
        lines.append(f"    Nominal   : {mm_in(specs['cb_mm'])}")
        lines.append(f"    Expected  : {mm_in(specs['cb_mm'] + cb_offset_mm)}  (+{cb_offset_mm} mm)")
        cb_note = "(marked CB — counterbore)" if is_step else "(marked CB / hub ID)"
        lines.append(f"    In file   : {cb_str}  {cb_note}")
        lines.append(f"    Status    : {pass_fail(cb_ok, cb_diff)}")
        if not is_step and cb2_found is not None:
            cb2_str = f'{cb2_found:.5f}"'
            lines.append(f"    Actual bore: {cb2_str}  (next smaller X after marker)")
            if cb2_ok is True:
                lines.append(f"    LOOSE PASS: {pass_fail(cb2_ok, cb2_diff)}")
            else:
                lines.append(f"    Bore check: {pass_fail(cb2_ok, cb2_diff)}")
        lines.append("")

        if is_step and specs.get("step_mm"):
            lines.append("  STEP BORE (center bore)")
            step_found_str = f'{cb2_found:.5f}"' if cb2_found is not None else 'NOT FOUND'
            lines.append(f"    Nominal   : {mm_in(specs['step_mm'])}")
            lines.append(f"    Expected  : {mm_in(specs['step_mm'] + 0.1)}  (+0.1 mm)")
            lines.append(f"    In file   : {step_found_str}  (next smaller X after counterbore)")
            lines.append(f"    Status    : {pass_fail(step_ok, step_diff)}")
            lines.append("")
        elif specs["ob_mm"]:
            lines.append("  OUTER BORE / OB")
            ob_str = f'{ob_found:.5f}"' if ob_found is not None else 'NOT FOUND'
            lines.append(f"    Nominal   : {mm_in(specs['ob_mm'])}")
            lines.append(f"    Expected  : {mm_in(specs['ob_mm'] - 0.1)}  (-0.1 mm)")
            lines.append(f"    In file   : {ob_str}")
            lines.append(f"    Status    : {pass_fail(ob_ok, ob_diff)}")
            lines.append("")

        # P-code / part thickness section
        total_thk  = result.get("total_thickness")
        op1        = result.get("op1_p")
        op2        = result.get("op2_p")
        uses_g5x   = result.get("uses_g5x", False)
        show_pc    = (total_thk and total_thk >= 0.75) or op1 is not None or uses_g5x
        if show_pc:
            round_size = specs.get("round_size_in", 0.0)
            lathe_lbl  = verifier._lathe_label_for_round(round_size)
            pc_table   = verifier._pc_table_for_round(round_size)
            exp_pair   = pc_table.get(round(total_thk * 4) / 4) if total_thk else None
            lines.append("  PART THICKNESS / P-CODE")
            if total_thk:
                lines.append(f"    Total thickness  : {total_thk:.3f}\"  (disc + hub)")
                lines.append(f"    Lathe (by round) : Lathe {lathe_lbl}  ({round_size}\" round)")
            else:
                lines.append(f"    Total thickness  : unknown (no disc length in title)")
                lines.append(f"    Lathe (by round) : Lathe {lathe_lbl}  ({round_size}\" round)")
            if uses_g5x:
                lines.append("    Work offsets     : G54/G55 (no P-codes)")
                lines.append("    Status           : N/A")
            elif op1 is not None:
                lines.append(f"    Found P-codes    : OP1=P{op1}  OP2=P{op2 if op2 else '?'}")
                if exp_pair:
                    lines.append(f"    Expected P-codes : P{exp_pair[0]}/P{exp_pair[1]}")
                    if result.get("pcode_ok"):
                        lines.append(f"    Status           : PASS  (Lathe {result.get('pcode_lathe')})")
                    else:
                        impl = result.get("pcode_implied")
                        impl_str = f"  → implies {impl:.2f}\"" if impl else ""
                        lines.append(f"    Status           : FAIL  found P{op1}/P{op2}{impl_str}")
                else:
                    lines.append("    Status           : cannot verify (thickness unknown)")
            else:
                lines.append("    Found P-codes    : NOT FOUND")
                lines.append("    Status           : NOT FOUND")
            lines.append("")

        # Home tool position section
        home_zs  = result.get("home_zs_found") or []
        home_exp = result.get("home_z_expected")
        if total_thk and total_thk >= 0.75:
            lines.append(f"  HOME TOOL POSITION (G53 Z — {len(home_zs)} found)")
            if home_exp is not None:
                lines.append(f"    Max negative Z   : {int(home_exp)}.  (Z must be ≥ {int(home_exp)})")
                if home_zs:
                    z_strs = "  ".join(f"Z{int(z)}." for z in home_zs)
                    lines.append(f"    Found            : {z_strs}")
                    bad = [z for z in home_zs if z < home_exp]
                    if bad:
                        lines.append(f"    Violations       : {', '.join(f'Z{int(z)}.' for z in bad)}")
                    lines.append(f"    Status           : {'PASS' if result.get('home_ok') else 'FAIL'}")
                else:
                    lines.append("    Found            : NOT FOUND")
            else:
                lines.append(f"    Max negative Z   : unknown  (>{total_thk:.2f}\" — no data)")
                if home_zs:
                    z_strs = "  ".join(f"Z{int(z)}." for z in home_zs)
                    lines.append(f"    Found            : {z_strs}")
            lines.append("")

        # Drill depth section
        dr_depths  = result.get("dr_depths") or []
        dr_exp     = result.get("dr_expected")
        total_thk  = result.get("total_thickness")
        if dr_exp is not None:
            is_thick   = total_thk is not None and total_thk > 4.0
            is_15mm_hc = dr_exp == -1.15
            lines.append("  DRILL DEPTH (T101)")
            if is_15mm_hc:
                lines.append("    Mode             : 15MM HC — always Z-1.15\"")
                lines.append(f"    Expected Z       : {dr_exp:.3f}\"")
            elif is_thick:
                lines.append(f"    Mode             : THICK ({total_thk:.3f}\") — dual drill expected")
                lines.append(f"    Max per pass     : 4.150\"")
                lines.append(f"    Min depth sum    : {dr_exp:.3f}\"")
            else:
                lines.append(f"    Expected Z       : {dr_exp:.3f}\"")
            if dr_depths and is_thick:
                abs_d = [abs(d) for d in dr_depths[:2]]
                depth_sum = sum(abs_d)
                for k, d in enumerate(dr_depths[:2], 1):
                    ok_k = abs(d) <= 4.15 + verifier.DR_TOLERANCE_IN
                    lines.append(f"    Drill {k} Z        : {d:.3f}\"  ({'ok' if ok_k else 'EXCEEDS 4.15'})")
                lines.append(f"    Sum of depths    : {depth_sum:.3f}\"")
                lines.append(f"    Status           : {'PASS' if result.get('dr_ok') else 'FAIL'}")
                if result.get("dr_note"):
                    lines.append(f"    Note             : {result['dr_note']}")
            elif dr_depths:
                diff_dr = dr_depths[0] - dr_exp
                lines.append(f"    Found Z          : {dr_depths[0]:.3f}\"")
                lines.append(f"    Status           : {'PASS' if result.get('dr_ok') else 'FAIL'}  (diff {diff_dr:+.3f}\")")
            else:
                lines.append("    Found Z          : NOT FOUND  (no T101 G81/G83)")
            lines.append("")

        # OD turn-down section
        od_exp      = result.get("od_expected")
        od_op1      = result.get("od_op1_found")
        od_op2      = result.get("od_op2_found")
        od_op1_ok   = result.get("od_op1_ok")
        od_op2_ok   = result.get("od_op2_ok")
        if od_exp is not None:
            lines.append(f"  OD TURN-DOWN ({specs['round_size_in']}\" round → expected X{od_exp:.3f}\")")
            def _od_line(label, val, ok):
                if val is None:
                    return f"    {label:<18}: NOT FOUND"
                diff = val - od_exp
                st = "PASS" if ok else "FAIL"
                return f"    {label:<18}: {val:.3f}\"  {st}  (diff {diff:+.3f}\")"
            lines.append(_od_line("OP1 (pre-flip)", od_op1, od_op1_ok))
            lines.append(_od_line("OP2 (post-flip)", od_op2, od_op2_ok))
            overall = result.get("od_ok")
            if overall is True:
                lines.append("    Overall          : PASS")
            elif overall is False:
                lines.append("    Overall          : FAIL")
            else:
                lines.append("    Overall          : NOT FOUND")
            lines.append("")

        # Rough bore section
        rb_approach   = result.get("rb_approach_x")
        rb_pass_xs    = result.get("rb_pass_xs") or []
        rb_start_ok   = result.get("rb_start_ok")
        rb_steps_ok   = result.get("rb_steps_ok")
        rb_max_step   = result.get("rb_max_step")
        rb_violations = result.get("rb_violations") or []
        rb_skip_cb    = result.get("rb_skip_cb", False)
        has_rb        = rb_approach is not None or bool(rb_pass_xs)
        lines.append("  ROUGH BORE CHECK (T121 — approach X and step increments)")
        if not has_rb:
            lines.append("    Status           : NOT FOUND  (no T121 bore passes detected)")
        else:
            if rb_approach is not None:
                if rb_skip_cb:
                    start_note = f"  (CB < 58mm — start check N/A)"
                elif rb_start_ok is True:
                    start_note = "  PASS  (< 2.4\")"
                else:
                    start_note = "  FAIL  (≥ 2.4\" — too large)"
                lines.append(f"    Approach X       : {rb_approach:.4f}\"{start_note}")
            if rb_pass_xs:
                xs_str = "  ".join(f"X{x:.3f}" for x in rb_pass_xs)
                lines.append(f"    Bore passes      : {xs_str}")
                if len(rb_pass_xs) >= 2:
                    steps = [rb_pass_xs[i+1] - rb_pass_xs[i] for i in range(len(rb_pass_xs)-1)]
                    steps_str = "  ".join(f"{s:+.3f}" for s in steps)
                    lines.append(f"    Increments       : {steps_str}")
            if rb_max_step is not None:
                lines.append(f"    Max step         : {rb_max_step:.3f}\"  (limit: {verifier._RB_STEP_LIMIT:.1f}\")")
            for x1, x2, step in rb_violations:
                lines.append(f"    Violation        : X{x1:.3f} → X{x2:.3f}  (step {step:.3f}\")")
            if rb_start_ok is False or rb_steps_ok is False:
                lines.append("    Status           : FAIL")
            elif rb_start_ok is True or rb_steps_ok is True:
                lines.append("    Status           : PASS")
            else:
                lines.append("    Status           : NOT FOUND")
        lines.append("")

        # Feed rate section
        fr_ok         = result.get("fr_ok")
        fr_max        = result.get("fr_max", 0.0)
        fr_violations = result.get("fr_violations") or []
        lines.append(f"  FEED RATE CHECK (F ≤ {verifier._F_MAX})")
        if fr_ok is None:
            lines.append("    Status           : NOT FOUND  (no F values in program)")
        elif fr_ok:
            lines.append(f"    Max F found      : F{fr_max}")
            lines.append("    Status           : PASS")
        else:
            lines.append(f"    Max F found      : F{fr_max}  (EXCEEDS F{verifier._F_MAX})")
            lines.append(f"    Violations       : {len(fr_violations)} line(s)")
            for line_idx, fv in fr_violations[:10]:
                lines.append(f"      Line {line_idx + 1:<6}: F{fv}")
            if len(fr_violations) > 10:
                lines.append(f"      ... and {len(fr_violations) - 10} more")
            lines.append("    Status           : FAIL")
        lines.append("")

        # Integer coordinate (decimal) check section
        int_coord_hits = result.get("int_coord_hits") or []
        int_coord_ok   = result.get("int_coord_ok", True)
        if not int_coord_ok or int_coord_hits:
            lines.append(f"  DECIMAL COORD CHECK (X/Z must have decimal point)")
            if int_coord_ok:
                lines.append("    Status           : PASS")
            else:
                lines.append(f"    Violations       : {len(int_coord_hits)} line(s)")
                for line_no, line_text in int_coord_hits[:10]:
                    lines.append(f"      Line {line_no:<6}: {line_text.strip()}")
                if len(int_coord_hits) > 10:
                    lines.append(f"      ... and {len(int_coord_hits) - 10} more")
                lines.append("    Status           : FAIL  (integer X/Z — missing decimal point)")
            lines.append("")

        lines.append(f"  FLIP found: {'Yes' if result['flip_found'] else 'No'}")
        lines.append("=" * 52)

        # How-to hints for anything not found
        hints = []
        if cb_found is None:
            hints.append("")
            hints.append("HOW TO MARK CB IN G-CODE")
            hints.append("  CB is located BEFORE the (FLIP) comment,")
            hints.append("  inside the T121 bore tool block.")
            hints.append("  Add (X IS CB) on the line with the X bore move:")
            hints.append("")
            hints.append("    T121 (BORE TOOL)")
            hints.append("    G01 X4.3307 F0.008 (X IS CB)")
            hints.append("")
            hints.append("  Accepted comment forms:")
            hints.append("    (X IS CB)  (X IS C.B.)  (X IS ID)  (X IS CENTER BORE)")
            hints.append("")
            hints.append("  Fallback: if no (X IS CB) comment is present,")
            hints.append("  the largest X value in the T121 block is used.")

        if is_step and specs.get("step_mm") and cb2_found is None:
            hints.append("")
            hints.append("HOW TO FIND STEP BORE")
            hints.append("  The step bore (center bore) is NOT marked — it is")
            hints.append("  found automatically as the next smaller X value")
            hints.append("  after the (X IS CB) counterbore line, before any")
            hints.append("  G00 rapid move.  Example:")
            hints.append("")
            hints.append("    G01 X4.3307 F0.008 (X IS CB)   ← counterbore")
            hints.append("    X2.9331                         ← center bore (found here)")
            hints.append("    X2.7                            ← roughing step (ignored)")

        if not is_step and specs["ob_mm"] and ob_found is None:
            hints.append("")
            hints.append("HOW TO MARK OB IN G-CODE")
            hints.append("  OB is located AFTER the (FLIP) comment,")
            hints.append("  in the turning section (outer diameter pass).")
            hints.append("  Add (X IS OB) on the line with the X turn move:")
            hints.append("")
            hints.append("    G01 X3.3543 (X IS OB)")
            hints.append("")
            hints.append("  Accepted comment forms:")
            hints.append("    (X IS OB)  (X IS OD)  (X IS O.B.)  (X IS O.D.)")
            hints.append("    (X IS HUB)")

        if not result["flip_found"] and (cb_found is None or (specs["ob_mm"] and ob_found is None)):
            hints.append("")
            hints.append("NOTE: No (FLIP) comment found in file.")
            hints.append("  CB is searched across the whole file.")
            hints.append("  OB cannot be located without (FLIP).")

        if hints:
            lines.append("")
            lines.extend(hints)

        # Color the text based on pass/fail
        from PyQt6.QtGui import QTextCharFormat, QTextCursor, QColor
        doc = self._left_edit.document()
        cursor = QTextCursor(doc)
        for ln in lines:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#1e1e2e"))
            if "PASS" in ln:
                fmt.setForeground(QColor("#66dd66"))
            elif "FAIL" in ln:
                fmt.setForeground(QColor("#ff4444"))
            elif "NOT FOUND" in ln:
                fmt.setForeground(QColor("#ffaa44"))
            elif ("CENTER BORE" in ln or "OUTER BORE" in ln or "COUNTERBORE" in ln
                  or "STEP BORE" in ln or "DRILL DEPTH" in ln or "OD TURN-DOWN" in ln
                  or "PART THICKNESS" in ln or "HOME TOOL" in ln or "ROUGH BORE" in ln
                  or "DECIMAL COORD" in ln):
                fmt.setForeground(QColor("#88aaff"))
            elif ln.startswith("HOW TO"):
                fmt.setForeground(QColor("#ffdd88"))
                fmt.setFontWeight(700)
            elif ln.startswith("NOTE:"):
                fmt.setForeground(QColor("#ff9955"))
            elif ln.startswith("="):
                fmt.setForeground(QColor("#444466"))
            elif ln.strip().startswith("(X IS") or ln.strip().startswith("G0") or ln.strip().startswith("T12"):
                fmt.setForeground(QColor("#88ccff"))
            else:
                fmt.setForeground(QColor("#cccccc"))
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(ln + "\n", fmt)

        # Right panel: G-code context
        ctx_lines = []
        if result["cb_context"]:
            cb_ctx_label = "--- CB context (counterbore line) ---" if is_step else "--- CB context (marked line) ---"
            ctx_lines.append(cb_ctx_label)
            for no, text in result["cb_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")
        elif cb_found is None:
            ctx_lines.append("--- CB: no T121 block or (X IS CB) marker found ---")
            ctx_lines.append("")
            ctx_lines.append("  Verifier searched BEFORE (FLIP) for:")
            ctx_lines.append("    T0121 or T121  — bore tool call")
            ctx_lines.append("    X value        — diameter move")
            ctx_lines.append("    (X IS CB)      — optional explicit marker")
            ctx_lines.append("")

        if is_step and result.get("cb2_context"):
            ctx_lines.append("--- STEP BORE context (center bore — next smaller X) ---")
            for no, text in result["cb2_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")
        elif not is_step and result.get("cb2_context"):
            ctx_lines.append("--- CB2 context (actual bore — next smaller X) ---")
            for no, text in result["cb2_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")

        if not is_step and result["ob_context"]:
            ctx_lines.append("--- OB context (lines around detected X value) ---")
            for no, text in result["ob_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
        elif not is_step and specs["ob_mm"] and ob_found is None:
            ctx_lines.append("--- OB: no (X IS OB) marker found ---")
            ctx_lines.append("")
            ctx_lines.append("  Verifier searched for:")
            ctx_lines.append("    X value + (X IS OB) / (X IS OD) / (X IS HUB) / (X CB)")
            ctx_lines.append("    Unlike CB, OB has no fallback — marker required.")
            ctx_lines.append("")

        if result.get("dr_context"):
            ctx_lines.append("--- DRILL context (T101 G81/G83 line) ---")
            for no, text in result["dr_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")
        elif result.get("dr_expected") is not None and not result.get("dr_depths"):
            ctx_lines.append("--- DRILL: no T101 G81/G83 Z found ---")
            ctx_lines.append("")

        if result.get("od_op1_context"):
            ctx_lines.append("--- OD TURN OP1 context (T303 pre-flip) ---")
            for no, text in result["od_op1_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")
        if result.get("od_op2_context"):
            ctx_lines.append("--- OD TURN OP2 context (T303 post-flip) ---")
            for no, text in result["od_op2_context"]:
                ctx_lines.append(f"{no:>5}  {text}")
            ctx_lines.append("")
        if (result.get("od_expected") is not None
                and result.get("od_op1_found") is None
                and result.get("od_op2_found") is None):
            ctx_lines.append("--- OD TURN: no T303 Z-cut found in either op ---")
            ctx_lines.append("")

        dc_hits = result.get("int_coord_hits") or []
        if dc_hits:
            ctx_lines.append("--- DECIMAL CHECK: integer X/Z coordinates ---")
            for line_no, line_text in dc_hits:
                ctx_lines.append(f"{line_no:>5}  {line_text}")
            ctx_lines.append("")

        self._right_edit.setPlainText("\n".join(ctx_lines))

    def compare(self, path_a: str, name_a: str, path_b: str, name_b: str,
                title_a: str = "", title_b: str = ""):
        """Load and render a diff between two files."""
        self._verify_path = None
        self._verify_name = None
        self._verify_title = None
        self._edit_btn.setVisible(False)
        self._stack.setCurrentIndex(0)
        self._header.setText(f"Comparing: {name_a}  vs  {name_b}")

        # Build per-side labels showing filename + program title
        def side_label(name, title):
            if title:
                return f"  {name}   —   ( {title} )"
            return f"  {name}"

        self._left_label.setText(side_label(name_a, title_a))
        self._right_label.setText(side_label(name_b, title_b))

        diff_lines = engine.compute_diff(path_a, path_b)
        similarity = engine.similarity_percent(path_a, path_b)

        added = sum(1 for d in diff_lines if d.kind == "insert")
        removed = sum(1 for d in diff_lines if d.kind == "delete")
        changed = sum(1 for d in diff_lines if d.kind == "replace")

        # Title relationship note
        title_note = ""
        if title_a and title_b:
            from utils import title_similarity
            sim = title_similarity(title_a, title_b)
            if sim == "identical":
                title_note = "  |  Titles: SAME PART"
            elif sim == "same_numerics":
                title_note = "  |  Titles: SAME SPEC (formatting differs)"
            elif sim == "similar":
                title_note = "  |  Titles: SIMILAR PART"
            else:
                title_note = "  |  Titles: DIFFERENT PARTS"

        self._stats.setText(
            f"Similarity: {similarity}%   |   "
            f"+{added} added   -{removed} removed   ~{changed} changed"
            f"{title_note}"
        )
        self._stats.setVisible(True)

        self._render(diff_lines)

    def _render(self, diff_lines: list):
        left_cursor = QTextCursor(self._left_edit.document())
        right_cursor = QTextCursor(self._right_edit.document())

        self._left_edit.clear()
        self._right_edit.clear()

        left_cursor.movePosition(QTextCursor.MoveOperation.Start)
        right_cursor.movePosition(QTextCursor.MoveOperation.Start)

        for dl in diff_lines:
            # Choose colors
            if dl.kind == "equal":
                bg_l, fg_l = BG_EQUAL, FG_EQUAL
                bg_r, fg_r = BG_EQUAL, FG_EQUAL
            elif dl.kind == "insert":
                bg_l, fg_l = BG_EMPTY, FG_EMPTY
                bg_r, fg_r = BG_INSERT, FG_INSERT
            elif dl.kind == "delete":
                bg_l, fg_l = BG_DELETE, FG_DELETE
                bg_r, fg_r = BG_EMPTY, FG_EMPTY
            elif dl.kind == "replace":
                bg_l, fg_l = BG_REPLACE, FG_REPLACE
                bg_r, fg_r = BG_REPLACE, FG_REPLACE
            else:
                bg_l, fg_l = BG_EMPTY, FG_EMPTY
                bg_r, fg_r = BG_EMPTY, FG_EMPTY

            # Line number fmt
            ln_fmt = QTextCharFormat()
            ln_fmt.setForeground(LINE_NO_FG)
            ln_fmt.setBackground(QColor("#12131f"))

            # Content fmt
            def make_fmt(bg, fg):
                fmt = QTextCharFormat()
                fmt.setBackground(bg)
                fmt.setForeground(fg)
                return fmt

            # Left side
            ln_str_l = f"{dl.line_no_left:>5} " if dl.line_no_left else "      "
            self._append_line(left_cursor, ln_str_l, dl.text_left, ln_fmt, make_fmt(bg_l, fg_l))

            # Right side
            ln_str_r = f"{dl.line_no_right:>5} " if dl.line_no_right else "      "
            self._append_line(right_cursor, ln_str_r, dl.text_right, ln_fmt, make_fmt(bg_r, fg_r))

        # Scroll both to top
        self._left_edit.verticalScrollBar().setValue(0)
        self._right_edit.verticalScrollBar().setValue(0)

    def _append_line(self, cursor: QTextCursor, ln_str: str, content: str,
                     ln_fmt: QTextCharFormat, content_fmt: QTextCharFormat):
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(ln_str, ln_fmt)
        # Pad content to fill line
        cursor.insertText(content + "\n", content_fmt)
