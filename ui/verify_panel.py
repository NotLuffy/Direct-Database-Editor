"""
CNC Direct Editor — Verify Results Panel.

Shown in the bottom tab strip when a file row is selected.
Calls verifier.verify_file() and displays each check as a
colored PASS / FAIL / NF row with found vs expected values,
plus the G-code context lines that were matched (or searched).
"""

import os
import html as _html
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QSizePolicy, QPushButton, QDialog, QTextBrowser,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

import verifier as _vfy

# ─────────────────────────────────────────────────────────────────────────────
# Background worker so the UI stays responsive while verifying
# ─────────────────────────────────────────────────────────────────────────────

class _VerifyWorker(QThread):
    completed = pyqtSignal(dict, str, str)   # result, file_path, o_number

    def __init__(self, path, title, o_number, parent=None):
        super().__init__(parent)
        self._path     = path
        self._title    = title
        self._o_number = o_number

    def run(self):
        try:
            result = _vfy.verify_file(self._path, self._title,
                                      o_number=self._o_number or None)
        except Exception as exc:
            result = {"error": str(exc)}
        self.completed.emit(result or {}, self._path, self._o_number)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PASS_BG  = "#0a2a14"
_FAIL_BG  = "#2a0a0a"
_NF_BG    = "#12131e"
_LOOSE_BG = "#2a1e00"

_PASS_FG  = "#44ee88"
_FAIL_FG  = "#ff5555"
_NF_FG    = "#555577"
_LOOSE_FG = "#ffaa33"

_MONO = QFont("Consolas", 10)
_BOLD = QFont("Consolas", 10)
_BOLD.setBold(True)
_SMALL = QFont("Consolas", 9)


def _badge(text: str, fg: str, bg: str, min_w: int = 64) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(_BOLD)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setMinimumWidth(min_w)
    lbl.setFixedHeight(22)
    lbl.setStyleSheet(
        f"color:{fg}; background:{bg}; border-radius:3px;"
        f" padding:1px 6px; font-size:11px; font-weight:bold;")
    return lbl


def _val_lbl(text: str, fg: str = "#aaaacc") -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(_MONO)
    lbl.setStyleSheet(f"color:{fg}; font-size:10px;")
    lbl.setWordWrap(True)
    return lbl


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#1e2038;")
    return f


def _ok_to_badge(ok) -> tuple[str, str, str]:
    """Return (text, fg, bg) for a True/False/None ok value."""
    if ok is True:
        return "PASS", _PASS_FG, _PASS_BG
    if ok is False:
        return "FAIL", _FAIL_FG, _FAIL_BG
    return "NF", _NF_FG, _NF_BG


def _inch(v) -> str:
    if v is None:
        return "—"
    return f'{v:+.4f}"'


def _mm(v) -> str:
    if v is None:
        return "—"
    return f"{v * 25.4:.3f} mm"


# ─────────────────────────────────────────────────────────────────────────────
# Panel
# ─────────────────────────────────────────────────────────────────────────────

class VerifyPanel(QWidget):
    """Displays pass/fail results for the selected file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #0d0e18; color: #ccccdd; }")
        self._worker: _VerifyWorker | None = None
        self._current_path = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar (HBox: label + legend button)
        hdr_widget = QWidget()
        hdr_widget.setFixedHeight(26)
        hdr_widget.setStyleSheet(
            "background:#12131e; border-bottom:1px solid #1e2038;")
        hdr_lay = QHBoxLayout(hdr_widget)
        hdr_lay.setContentsMargins(8, 0, 4, 0)
        hdr_lay.setSpacing(4)

        self._hdr = QLabel("  Select a file to see verification results.")
        self._hdr.setStyleSheet("color:#556688; font-size:11px; background:transparent;")
        hdr_lay.addWidget(self._hdr, stretch=1)

        legend_btn = QPushButton("Legend ?")
        legend_btn.setFixedHeight(20)
        legend_btn.setStyleSheet(
            "QPushButton { background:#1a2030; border:1px solid #2a3d55;"
            " color:#66aadd; border-radius:3px; font-size:10px;"
            " padding:1px 8px; }"
            "QPushButton:hover { background:#1e3448; color:#88ccff; }"
        )
        legend_btn.clicked.connect(self._show_legend)
        hdr_lay.addWidget(legend_btn)

        root.addWidget(hdr_widget)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:#0d0e18; }")
        self._content = QWidget()
        self._content.setStyleSheet("background:#0d0e18;")
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(8, 6, 8, 6)
        self._content_lay.setSpacing(4)
        self._content_lay.addStretch()
        scroll.setWidget(self._content)
        root.addWidget(scroll, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, file_path: str, title: str, o_number: str):
        """Start verifying file_path. Shows loading state immediately."""
        if not file_path or not os.path.isfile(file_path):
            self._show_message("File not found on disk.")
            return

        if not title:
            self._show_message("No program title — cannot verify.")
            return

        if self._current_path == file_path:
            return   # already showing this file

        self._current_path = file_path
        self._hdr.setText(
            f"  Verifying  {os.path.basename(file_path)} …")

        # Cancel any running worker
        if self._worker and self._worker.isRunning():
            self._worker.completed.disconnect()
            self._worker.quit()

        self._worker = _VerifyWorker(file_path, title, o_number, parent=self)
        self._worker.completed.connect(self._on_result)
        self._worker.start()

    def clear(self):
        self._current_path = ""
        self._show_message("Select a file to see verification results.")

    def _show_legend(self):
        """Show a non-modal dialog explaining every verify token."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Verification Legend")
        dlg.setMinimumSize(520, 420)
        dlg.setStyleSheet(
            "QDialog { background:#0d0e18; color:#ccccdd; }"
            "QTextBrowser { background:#0f1018; color:#ccccdd;"
            " border:1px solid #1e2038; font-size:11px; }"
        )
        lay = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml("""
<style>
  body { font-family: Consolas, monospace; font-size: 11px; color: #ccccdd; }
  h2   { color: #8899bb; font-size: 13px; margin: 10px 0 4px 0; }
  table { border-collapse: collapse; width: 100%%; }
  td, th { padding: 4px 8px; border-bottom: 1px solid #1e2038; text-align: left; }
  th { color: #8899bb; }
  .pass { color: #44ee88; font-weight: bold; }
  .fail { color: #ff5555; font-weight: bold; }
  .nf   { color: #555577; }
  .tok  { color: #66aadd; font-weight: bold; }
  .desc { color: #aaaacc; }
</style>

<h2>Scored Checks (6 total &mdash; each adds 1 to score when PASS)</h2>
<table>
  <tr><th>Token</th><th>Name</th><th>What It Checks</th></tr>
  <tr>
    <td class="tok">CB</td><td>Center Bore</td>
    <td class="desc">OP1 bore diameter (T303 X-value) matches title CB spec &plusmn;0.5mm</td>
  </tr>
  <tr>
    <td class="tok">OB</td><td>Outer Bore</td>
    <td class="desc">Second bore pass (HC/STEP/2PC hub bore) matches title OB spec &plusmn;0.5mm.
    Standard parts with no OB show NF.</td>
  </tr>
  <tr>
    <td class="tok">DR</td><td>Drill Depth</td>
    <td class="desc">T101 drill (G81/G83) Z-depth matches -(total_thickness + 0.15").
    15MM HC always expects Z-1.15". Dual drills checked on sum.</td>
  </tr>
  <tr>
    <td class="tok">OD</td><td>OD Turn</td>
    <td class="desc">Outside-diameter pass in OP1 and OP2 matches the OD table for the
    round size &plusmn;0.015" (e.g. 5.75&quot; round &rarr; 5.700&quot; OD).</td>
  </tr>
  <tr>
    <td class="tok">PC</td><td>P-Code</td>
    <td class="desc">G154 P## work-offset number matches the lookup table for the
    round size + total thickness (lathe 1 vs lathe 2/3).</td>
  </tr>
  <tr>
    <td class="tok">HM</td><td>Home Position</td>
    <td class="desc">G53 X-11 Z-## return-home move. Z must match total thickness:<br>
    &le;2.50&quot; &rarr; Z-13 &nbsp;|&nbsp; 2.75&ndash;3.75&quot; &rarr; Z-11 &nbsp;|&nbsp;
    4.0&ndash;5.0&quot; &rarr; Z-9 &nbsp;|&nbsp; &gt;5.0&quot; &rarr; NF</td>
  </tr>
</table>

<h2>Result Tokens</h2>
<table>
  <tr><th>Status</th><th>Meaning</th></tr>
  <tr><td class="pass">PASS</td><td class="desc">G-code value found and within tolerance</td></tr>
  <tr><td class="fail">FAIL</td><td class="desc">G-code value found but outside tolerance</td></tr>
  <tr><td class="nf">NF</td><td class="desc">Not Found &mdash; could not locate the G-code block (scores 0, not a penalty)</td></tr>
  <tr><td style="color:#ffaa33;font-weight:bold">LOOSE</td>
      <td class="desc">CB bore found but tolerance is wider than standard (hub bore variant)</td></tr>
</table>

<h2>2PC-Only Tokens (not scored)</h2>
<table>
  <tr><th>Token</th><th>Meaning</th></tr>
  <tr><td class="tok">RC:&lt;val&gt;</td><td class="desc">Recess X diameter found in G-code (for Piece B pairing)</td></tr>
  <tr><td class="tok">HB:&lt;val&gt;</td><td class="desc">Hub outside diameter found in G-code</td></tr>
  <tr><td class="tok">IH:&lt;val&gt;</td><td class="desc">Implicit hub height inferred from G-code (not stated in title)</td></tr>
</table>
""")
        lay.addWidget(browser)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.close)
        lay.addWidget(close_btn)
        dlg.show()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_result(self, result: dict, file_path: str, o_number: str):
        if file_path != self._current_path:
            return   # stale result

        self._hdr.setText(
            f"  {os.path.basename(file_path)}"
            + (f"  •  {o_number}" if o_number else ""))

        self._clear_content()

        if result.get("error"):
            self._add_row(_badge("ERROR", _FAIL_FG, _FAIL_BG),
                          _val_lbl(result["error"], _FAIL_FG))
            self._content_lay.addStretch()
            return

        specs = result.get("specs") or {}
        rs    = specs.get("round_size_in")
        cb_mm = specs.get("cb_mm")
        ob_mm = specs.get("ob_mm")
        th    = result.get("total_thickness")

        # ── Spec summary row ──────────────────────────────────────────────────
        spec_parts = []
        if rs:   spec_parts.append(f"Round {rs}\"")
        if cb_mm: spec_parts.append(f"CB {cb_mm:.2f}mm")
        if ob_mm: spec_parts.append(f"OB {ob_mm:.2f}mm")
        if th:   spec_parts.append(f"Thick {th:.3f}\"")
        if spec_parts:
            spec_lbl = QLabel("  " + "   •   ".join(spec_parts))
            spec_lbl.setStyleSheet(
                "color:#8899bb; font-size:10px; background:#12131e;"
                " padding:2px 6px; border-radius:3px;")
            self._content_lay.addWidget(spec_lbl)
            self._content_lay.addWidget(_sep())

        # ── CB ────────────────────────────────────────────────────────────────
        cb_ok = result.get("cb_ok")
        if cb_ok == "loose":
            cb_badge = _badge("LOOSE", _LOOSE_FG, _LOOSE_BG)
        else:
            txt, fg, bg = _ok_to_badge(cb_ok)
            cb_badge = _badge(txt, fg, bg)

        cb_found = result.get("cb_found_in")
        cb_exp   = result.get("cb_expected_in")
        cb_exp_max = result.get("cb_expected_max_in")
        cb_diff  = result.get("cb_diff_in")

        if cb_exp_max and cb_exp_max != cb_exp:
            cb_exp_str = f'{cb_exp:.4f}" – {cb_exp_max:.4f}"  ({cb_exp * 25.4:.3f}mm – {cb_exp_max * 25.4:.3f}mm)'
        elif cb_exp is not None:
            cb_exp_str = f'{cb_exp:.4f}"  ({cb_exp * 25.4:.3f}mm)'
        else:
            cb_exp_str = "—"

        cb_detail = f"Found: {_inch(cb_found)}  ({_mm(cb_found)})    Expected: {cb_exp_str}"
        if cb_diff is not None:
            cb_detail += f"    Diff: {cb_diff:+.4f}\""
        self._add_check_row("CB  (Center Bore)", cb_badge, cb_detail)
        self._add_context(result.get("cb_context") or [],
                          result.get("cb_context_hit_ln"))

        # ── OB ────────────────────────────────────────────────────────────────
        ob_ok  = result.get("ob_ok")
        txt, fg, bg = _ok_to_badge(ob_ok)
        ob_badge = _badge(txt, fg, bg)
        ob_found = result.get("ob_found_in")
        ob_exp   = result.get("ob_expected_in")
        ob_diff  = result.get("ob_diff_in")
        ob_detail = (f"Found: {_inch(ob_found)}  ({_mm(ob_found)})"
                     f"    Expected: {_inch(ob_exp)}  ({_mm(ob_exp)})")
        if ob_diff is not None:
            ob_detail += f"    Diff: {ob_diff:+.4f}\""
        self._add_check_row("OB  (Outer Bore / Pilot)", ob_badge, ob_detail)
        self._add_context(result.get("ob_context") or [],
                          result.get("ob_context_hit_ln"))

        # ── Drill ─────────────────────────────────────────────────────────────
        dr_ok   = result.get("dr_ok")
        txt, fg, bg = _ok_to_badge(dr_ok)
        dr_badge = _badge(txt, fg, bg)
        dr_depths   = result.get("dr_depths") or []
        dr_expected = result.get("dr_expected")
        dr_note     = result.get("dr_note") or ""
        found_str = "  ".join(f'{d:.4f}"' for d in dr_depths) or "—"
        if dr_expected is not None:
            exp_str = (f'{dr_expected:.4f}"'
                       if len(dr_depths) <= 1
                       else f"sum ≥ {dr_expected:.4f}\"")
        else:
            exp_str = "—"
        dr_detail = f"Found: {found_str}    Expected: {exp_str}"
        if dr_note:
            dr_detail += f"    ⚠ {dr_note}"
        self._add_check_row("DR  (Drill Depth)", dr_badge, dr_detail)
        self._add_context(result.get("dr_context") or [],
                          result.get("dr_context_hit_ln"))

        # ── OD Turn ───────────────────────────────────────────────────────────
        od_ok    = result.get("od_ok")
        txt, fg, bg = _ok_to_badge(od_ok)
        od_badge = _badge(txt, fg, bg)
        op1_od   = result.get("od_op1_found")
        op2_od   = result.get("od_op2_found")
        od_exp   = None
        if specs and rs:
            od_rs  = round(rs * 4) / 4
            od_exp = _vfy._OD_TABLE.get(od_rs)
        parts = []
        if op1_od is not None: parts.append(f"OP1: {op1_od:.4f}\"")
        if op2_od is not None: parts.append(f"OP2: {op2_od:.4f}\"")
        od_detail = ("Found: " + "  ".join(parts) if parts else "Found: —")
        if od_exp is not None:
            od_detail += f'    Expected: {od_exp:.4f}"'
        self._add_check_row("OD  (Outside Diameter Turn)", od_badge, od_detail)
        # Show OP1 context if available, else OP2
        od_ctx    = result.get("od_op1_context") or result.get("od_op2_context") or []
        od_hit_ln = result.get("od_op1_context_hit_ln") or result.get("od_op2_context_hit_ln")
        self._add_context(od_ctx, od_hit_ln)

        # ── P-Code ────────────────────────────────────────────────────────────
        pc_ok  = result.get("pcode_ok")
        txt, fg, bg = _ok_to_badge(pc_ok)
        pc_badge = _badge(txt, fg, bg)
        op1_p  = result.get("op1_p")
        op2_p  = result.get("op2_p")
        pc_exp = result.get("pcode_expected")
        pc_lathe = result.get("pcode_lathe") or ""
        found_str = f"P{op1_p}/P{op2_p}" if op1_p and op2_p else "—"
        exp_str   = (f"P{pc_exp[0]}/P{pc_exp[1]}" if pc_exp else "—")
        pc_detail = f"Found: {found_str}    Expected: {exp_str}"
        if pc_lathe:
            pc_detail += f"    ({pc_lathe})"
        if result.get("pcode_wrong_lathe"):
            wl = result.get("pcode_wrong_lathe_label") or ""
            pc_detail += f"    ⚠ Wrong lathe ({wl})"
        self._add_check_row("PC  (G154 P-Code)", pc_badge, pc_detail)
        pc_ctx    = result.get("pcode_op1_context") or result.get("pcode_op2_context") or []
        pc_hit_ln = result.get("pcode_op1_context_hit_ln") or result.get("pcode_op2_context_hit_ln")
        self._add_context(pc_ctx, pc_hit_ln)

        # ── Home Position ─────────────────────────────────────────────────────
        hm_ok  = result.get("home_ok")
        txt, fg, bg = _ok_to_badge(hm_ok)
        hm_badge = _badge(txt, fg, bg)
        hm_found = result.get("home_zs_found") or []
        hm_exp   = result.get("home_z_expected")
        found_str = "  ".join(f"Z{z:.4f}" for z in hm_found) or "—"
        hm_detail = (f"Found: {found_str}"
                     + (f"    Expected: ≥ Z{hm_exp:.4f}" if hm_exp is not None else ""))
        self._add_check_row("HM  (Home Position G53)", hm_badge, hm_detail)
        self._add_context(result.get("home_context") or [],
                          result.get("home_context_hit_ln"))

        self._content_lay.addStretch()

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _clear_content(self):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_check_row(self, label: str, badge: QWidget, detail: str):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 1, 0, 1)

        name_lbl = QLabel(label)
        name_lbl.setFont(_MONO)
        name_lbl.setFixedWidth(200)
        name_lbl.setStyleSheet("color:#8899cc; font-size:10px;")

        row.addWidget(badge)
        row.addWidget(name_lbl)
        row.addWidget(_val_lbl(detail), stretch=1)

        wrapper = QWidget()
        wrapper.setLayout(row)
        wrapper.setStyleSheet(
            f"background:{badge.styleSheet().split('background:')[1].split(';')[0]}22;"
            " border-radius:3px;")
        self._content_lay.addWidget(wrapper)

    def _add_context(self, ctx: list, hit_ln: int | None = None):
        """Render G-code context lines below the most recent check row."""
        if not ctx:
            return

        lines_html = []
        for ln_no, code in ctx:
            escaped = _html.escape(code or "")
            is_hit  = (hit_ln is not None and ln_no == hit_ln)
            if is_hit:
                line_html = (
                    f'<span style="color:#ddcc88;background:#181400;">'
                    f'&#x25B6;&nbsp;{ln_no:4d}&nbsp;&#x2502;&nbsp;{escaped}</span>')
            else:
                line_html = (
                    f'<span style="color:#445566;">'
                    f'&nbsp;&nbsp;{ln_no:4d}&nbsp;&#x2502;&nbsp;{escaped}</span>')
            lines_html.append(line_html)

        lbl = QLabel("<br>".join(lines_html))
        lbl.setFont(_SMALL)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(
            "background:#07080f; padding:3px 6px 3px 18px;"
            " border-left:2px solid #1e2240; margin:0 0 2px 8px;")
        lbl.setWordWrap(False)
        self._content_lay.addWidget(lbl)

    def _add_row(self, badge: QWidget, detail: QLabel):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(badge)
        row.addWidget(detail, stretch=1)
        wrapper = QWidget()
        wrapper.setLayout(row)
        self._content_lay.addWidget(wrapper)

    def _show_message(self, msg: str):
        self._hdr.setText("  Select a file to see verification results.")
        self._clear_content()
        lbl = QLabel(msg)
        lbl.setStyleSheet("color:#445566; font-size:11px; padding:12px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_lay.addStretch()
        self._content_lay.addWidget(lbl)
        self._content_lay.addStretch()
