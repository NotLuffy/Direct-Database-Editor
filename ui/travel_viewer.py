"""
CNC Direct Editor — G-Code Travel / Toolpath Viewer.

PyQt6 port of the File Organizer travel viewer.
Embeds a matplotlib canvas for live toolpath rendering with
zoom, pan, side filters, flip visualisation, and playback.
"""

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("QtAgg")            # must be set before any pyplot import
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QPlainTextEdit, QFileDialog,
    QMessageBox, QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont, QTextCursor, QTextCharFormat, QColor

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Pure-Python G-code parser  (no Qt / tkinter dependency)
# ─────────────────────────────────────────────────────────────────────────────

class GCodeToolpathParser:
    """
    Walk a G-code file line by line and collect:
      rapid_moves / feed_moves    list of (x1,z1,x2,z2) segments
      tool_changes                list of (x,z,tool_str)
      line_coordinates            {1-based line_num: (x,z)}
      flip_line                   1-based line of the OP2/FLIP comment (or None)
      side1_deepest_z             deepest Z reached by T1xx before the flip
      gcode_lines                 raw list of strings (with newlines)
    """

    _FLIP_RE = re.compile(
        r"^\s*\(?\s*(FLIP|SIDE\s*2|OP2|OPERATION\s*2|SECOND\s*OP)\s*\)?",
        re.IGNORECASE,
    )
    _T_RE  = re.compile(r"\bT(\d+)\b")
    _G_RE  = re.compile(r"\b(G00|G01|G0|G1)\b", re.IGNORECASE)
    _X_RE  = re.compile(r"\bX([-+]?\d*\.?\d+)")
    _Z_RE  = re.compile(r"\bZ([-+]?\d*\.?\d+)")

    def parse_file(self, file_path: str) -> Dict:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception as exc:
            logger.error("Travel viewer cannot read %s: %s", file_path, exc)
            return self._empty()

        current_x      = 0.0
        current_z      = 0.0
        current_motion: Optional[str] = None
        current_tool:   Optional[str] = None

        rapid_moves:   List[Tuple] = []
        feed_moves:    List[Tuple] = []
        tool_changes:  List[Tuple] = []
        line_coords:   Dict[int, Tuple] = {}
        flip_line:     Optional[int]    = None
        deepest_z:     Optional[float]  = None

        all_x: List[float] = []
        all_z: List[float] = []

        for line_num, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("%") or line.startswith(";"):
                continue

            # Flip detection
            if flip_line is None and self._FLIP_RE.match(line):
                flip_line = line_num

            # Tool change
            t_m = self._T_RE.search(line)
            if t_m:
                current_tool = t_m.group(1)

            # Motion mode update
            g_m = self._G_RE.search(line)
            if g_m:
                code = g_m.group(1).upper()
                current_motion = "G00" if code in ("G00", "G0") else "G01"

            # Coordinate extraction
            x_m = self._X_RE.search(line)
            z_m = self._Z_RE.search(line)

            if x_m or z_m:
                prev_x, prev_z = current_x, current_z

                if x_m:
                    current_x = float(x_m.group(1))
                if z_m:
                    current_z = float(z_m.group(1))

                if current_motion == "G00":
                    rapid_moves.append((prev_x, prev_z, current_x, current_z))
                elif current_motion == "G01":
                    feed_moves.append((prev_x, prev_z, current_x, current_z))
                    # Track drill depth for Side 1 T1xx
                    if (flip_line is None
                            and current_tool
                            and current_tool.startswith("1")
                            and current_z < 0):
                        if deepest_z is None or current_z < deepest_z:
                            deepest_z = current_z

                line_coords[line_num] = (current_x, current_z)
                all_x.append(current_x)
                all_z.append(current_z)

                # Tool-change position marker (first occurrence per T)
                if t_m and current_tool:
                    tool_changes.append((current_x, current_z, current_tool))

        bounds = (
            {"x_min": min(all_x), "x_max": max(all_x),
             "z_min": min(all_z), "z_max": max(all_z)}
            if all_x
            else {"x_min": 0, "x_max": 0, "z_min": 0, "z_max": 0}
        )

        return {
            "rapid_moves":      rapid_moves,
            "feed_moves":       feed_moves,
            "tool_changes":     tool_changes,
            "line_coordinates": line_coords,
            "flip_line":        flip_line,
            "side1_deepest_z":  deepest_z,
            "gcode_lines":      lines,
            "bounds":           bounds,
        }

    @staticmethod
    def _empty() -> Dict:
        return {
            "rapid_moves": [], "feed_moves": [], "tool_changes": [],
            "line_coordinates": {}, "flip_line": None,
            "side1_deepest_z": None, "gcode_lines": [],
            "bounds": {"x_min": 0, "x_max": 0, "z_min": 0, "z_max": 0},
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Clickable QPlainTextEdit
# ─────────────────────────────────────────────────────────────────────────────

class _GCodeEdit(QPlainTextEdit):
    """Read-only G-code viewer that reports which line was clicked."""

    def __init__(self, on_line_clicked, parent=None):
        super().__init__(parent)
        self._on_line_clicked = on_line_clicked
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def mousePressEvent(self, event):          # noqa: N802
        super().mousePressEvent(event)
        cursor = self.cursorForPosition(event.pos())
        # block number is 0-based → displayed line = +1
        self._on_line_clicked(cursor.blockNumber() + 1)


# ─────────────────────────────────────────────────────────────────────────────
#  Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class TravelViewerDialog(QDialog):

    _BG   = "#0d0e18"
    _FG   = "#ccccdd"
    _GRID = "#1a1d2e"

    def __init__(self, file_path: str, program_number: str = "", parent=None):
        super().__init__(parent)
        self.file_path      = file_path
        self.program_number = program_number or os.path.basename(file_path)

        self.setWindowTitle(f"Toolpath Viewer — {self.program_number}")
        self.resize(1280, 740)
        self.setMinimumSize(800, 500)
        self.setStyleSheet(f"""
            QDialog    {{ background: {self._BG}; color: {self._FG}; }}
            QLabel     {{ color: #aaaacc; font-size: 11px; }}
            QPushButton {{
                background: #1a2030; border: 1px solid #2a2d45;
                color: #aaaacc; padding: 3px 10px;
                border-radius: 3px; font-size: 11px;
            }}
            QPushButton:hover   {{ background: #1e2840; }}
            QPushButton:checked {{ background: #007acc; border-color: #005a99; color: #ffffff; }}
            QPushButton:disabled {{ color: #333355; border-color: #1a1d2e; }}
        """)

        # State
        self.toolpath_data:          Dict = {}
        self.current_filter:         str  = "whole"
        self.flip_visualization:     bool = False
        self.current_playback_line:  int  = 0
        self.highlight_marker             = None
        self.panning:                bool = False
        self.pan_start:              Optional[Tuple] = None
        # Maps between displayed line (in G-code widget) and original file line
        self.displayed_to_original:  Dict[int, int] = {}
        self.original_to_displayed:  Dict[int, int] = {}

        self._build_ui()
        self._parse_and_render()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Top controls row ──────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        self._stats_label = QLabel("Parsing…")
        ctrl.addWidget(self._stats_label)
        ctrl.addStretch()

        # Filter buttons (checkable)
        self._btn_whole  = self._filter_btn("Whole", "whole")
        self._btn_side1  = self._filter_btn("Side 1", "side1")
        self._btn_side2  = self._filter_btn("Side 2", "side2")
        for b in (self._btn_whole, self._btn_side1, self._btn_side2):
            ctrl.addWidget(b)
        self._btn_whole.setChecked(True)

        self._flip_btn = QPushButton("Flip View")
        self._flip_btn.setCheckable(True)
        self._flip_btn.setEnabled(False)
        self._flip_btn.toggled.connect(self._toggle_flip)
        ctrl.addWidget(self._flip_btn)

        root.addLayout(ctrl)

        # ── Main splitter: plot | G-code ──────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.setStyleSheet("QSplitter::handle { background: #1a1d2e; }")

        # Left — matplotlib canvas
        self.fig = Figure(facecolor=self._BG, tight_layout=True)
        self.ax  = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.addWidget(self.canvas)

        # Right — G-code text
        self._gcode_edit = _GCodeEdit(self._on_gcode_line_clicked)
        self._gcode_edit.setStyleSheet(
            f"background: #0a0b14; color: {self._FG}; border: none;")
        splitter.addWidget(self._gcode_edit)

        splitter.setSizes([820, 420])
        root.addWidget(splitter, stretch=1)

        # ── Bottom controls row ───────────────────────────────────────────
        bot = QHBoxLayout()
        bot.setSpacing(6)

        # Playback
        for label, slot in (
            ("|◀", self._goto_first),
            ("◀",  self._goto_prev),
            ("▶",  self._goto_next),
            ("▶|", self._goto_last),
        ):
            btn = QPushButton(label)
            btn.setFixedWidth(32)
            btn.clicked.connect(slot)
            bot.addWidget(btn)

        self._play_label = QLabel("Line —")
        self._play_label.setMinimumWidth(100)
        bot.addWidget(self._play_label)
        bot.addStretch()

        export_btn = QPushButton("Export PNG…")
        export_btn.clicked.connect(self._export_png)
        bot.addWidget(export_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bot.addWidget(close_btn)

        root.addLayout(bot)

        # ── Matplotlib event connections ──────────────────────────────────
        self.canvas.mpl_connect("scroll_event",        self._on_scroll_zoom)
        self.canvas.mpl_connect("button_press_event",  self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event",self._on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("pick_event",          self._on_pick)

    def _filter_btn(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _, k=key: self._apply_filter(k))
        return btn

    # ── Parse + initial render ────────────────────────────────────────────────

    def _parse_and_render(self):
        parser = GCodeToolpathParser()
        self.toolpath_data = parser.parse_file(self.file_path)

        # Stats label
        td = self.toolpath_data
        has_flip = td["flip_line"] is not None
        n_rapid  = len(td["rapid_moves"])
        n_feed   = len(td["feed_moves"])
        n_tools  = len(set(t[2] for t in td["tool_changes"]))
        flip_txt = f"  |  Flip @ line {td['flip_line']}" if has_flip else "  |  No flip detected"
        self._stats_label.setText(
            f"{self.program_number}   Rapid: {n_rapid}   Feed: {n_feed}"
            f"   Tools: {n_tools}{flip_txt}"
        )

        # Enable/disable flip button
        self._flip_btn.setEnabled(
            has_flip and self.current_filter in ("whole", "side2"))

        # Enable/disable Side 2 button
        self._btn_side2.setEnabled(has_flip)

        # Initialise playback
        coords = td["line_coordinates"]
        if coords:
            self.current_playback_line = min(coords.keys())

        self._update_gcode_display()
        self._replot()

    # ── Filter / flip ─────────────────────────────────────────────────────────

    def _apply_filter(self, key: str):
        self.current_filter = key
        # Update button checked states
        self._btn_whole.setChecked(key == "whole")
        self._btn_side1.setChecked(key == "side1")
        self._btn_side2.setChecked(key == "side2")

        # Flip button only valid for whole / side2
        can_flip = (key in ("whole", "side2")
                    and self.toolpath_data.get("flip_line") is not None)
        self._flip_btn.setEnabled(can_flip)
        if not can_flip:
            self.flip_visualization = False
            self._flip_btn.setChecked(False)

        self._update_gcode_display()
        self._replot()

    def _toggle_flip(self, checked: bool):
        self.flip_visualization = checked
        self._replot()

    # ── G-code display ────────────────────────────────────────────────────────

    def _update_gcode_display(self):
        td        = self.toolpath_data
        flip_line = td["flip_line"]
        g_lines   = td["gcode_lines"]

        self.displayed_to_original = {}
        self.original_to_displayed = {}
        disp = 1
        parts: List[str] = []

        for orig, raw in enumerate(g_lines, 1):
            show = False
            if self.current_filter == "whole":
                show = True
            elif self.current_filter == "side1":
                show = not (flip_line and orig >= flip_line)
            elif self.current_filter == "side2":
                show = bool(flip_line and orig >= flip_line)

            if show:
                parts.append(raw if raw.endswith("\n") else raw + "\n")
                self.displayed_to_original[disp] = orig
                self.original_to_displayed[orig] = disp
                disp += 1

        self._gcode_edit.setPlainText("".join(parts))

    # ── Plot ──────────────────────────────────────────────────────────────────

    def _replot(self):
        self.ax.clear()
        self.highlight_marker = None

        fd = self._filtered_data()

        # Rapid moves
        rapid_done = False
        for i, (x1, z1, x2, z2) in enumerate(fd["rapid_moves"]):
            lbl = "Rapid (G00)" if not rapid_done else ""
            ln, = self.ax.plot(
                [z1, z2], [x1, x2],
                color="#FF8C00", linestyle="-", linewidth=1.0,
                alpha=0.5, label=lbl, picker=True, pickradius=5,
            )
            if i < len(fd["rapid_lines"]):
                ln.set_gid(str(fd["rapid_lines"][i][0]))
            rapid_done = True

        # Feed moves
        feed1_done = feed2_done = False
        for i, (x1, z1, x2, z2) in enumerate(fd["feed_moves"]):
            is_s2 = (i < len(fd["feed_lines"]) and fd["feed_lines"][i][1])
            if is_s2 and self.flip_visualization:
                color = "#7FFF00"
                lbl   = "Feed Side 2 (G01)" if not feed2_done else ""
                feed2_done = True
            else:
                color = "#569CD6"
                lbl   = "Feed (G01)" if not feed1_done else ""
                feed1_done = True
            ln, = self.ax.plot(
                [z1, z2], [x1, x2],
                color=color, linestyle="-", linewidth=2.0,
                label=lbl, picker=True, pickradius=5,
            )
            if i < len(fd["feed_lines"]):
                ln.set_gid(str(fd["feed_lines"][i][0]))

        # Tool changes
        seen_tools = set()
        for x, z, tool in fd["tool_changes"]:
            lbl = f"T{tool}" if tool not in seen_tools else ""
            self.ax.plot(z, x, "o", color="#CE9178",
                         markersize=8, label=lbl, zorder=5)
            self.ax.annotate(f"T{tool}", (z, x),
                             xytext=(5, 5), textcoords="offset points",
                             color="#CE9178", fontsize=9, fontweight="bold")
            seen_tools.add(tool)

        # Axes styling
        self.ax.set_facecolor(self._BG)
        self.ax.set_xlabel("Z — Length (inches)", color=self._FG, fontsize=11)
        self.ax.set_ylabel("X — Radius (inches)", color=self._FG, fontsize=11)
        self.ax.tick_params(colors=self._FG, labelsize=9)
        self.ax.grid(True, color=self._GRID, linestyle=":", alpha=0.5, linewidth=0.7)
        for spine in self.ax.spines.values():
            spine.set_edgecolor(self._GRID)

        # Title
        title = f"Toolpath: {self.program_number}"
        flip_line = self.toolpath_data["flip_line"]
        if self.current_filter == "side1":
            title += "  (Side 1)"
        elif self.current_filter == "side2":
            suffix = " — Flipped" if self.flip_visualization else ""
            title += f"  (Side 2{suffix})"
        elif self.flip_visualization and flip_line:
            title += "  (Side 1 normal + Side 2 flipped)"
        self.ax.set_title(title, color=self._FG, fontsize=13, fontweight="bold", pad=14)

        # Legend
        handles, labels = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(
                loc="upper right",
                facecolor="#1a1d2e", edgecolor=self._GRID,
                labelcolor=self._FG, fontsize=9,
            ).get_frame().set_alpha(0.9)

        # Bounds
        if fd["rapid_moves"] or fd["feed_moves"]:
            b = fd["bounds"]
            xr = max(b["x_max"] - b["x_min"], 0.01)
            zr = max(b["z_max"] - b["z_min"], 0.01)
            p  = 0.08
            self.ax.set_xlim(b["z_min"] - zr * p, b["z_max"] + zr * p)
            self.ax.set_ylim(b["x_min"] - xr * p, b["x_max"] + xr * p)

        self.ax.set_aspect("equal", adjustable="datalim")
        self.canvas.draw()

    def _filtered_data(self) -> Dict:
        """Return moves/tool-changes filtered to current_filter with optional flip."""
        td        = self.toolpath_data
        flip_line = td["flip_line"]
        coords    = td["line_coordinates"]

        rapid_out, rapid_lines = [], []
        feed_out,  feed_lines  = [], []
        tools_out              = []

        for ln, (x, z) in coords.items():
            is_s2 = bool(flip_line and ln >= flip_line)

            if self.current_filter == "side1" and is_s2:
                continue
            if self.current_filter == "side2" and not is_s2:
                continue

            z_ref = td["side1_deepest_z"] or 0

            for mv in td["rapid_moves"]:
                if abs(mv[2] - x) < 0.001 and abs(mv[3] - z) < 0.001:
                    m = self._flip_move(mv, z_ref) if (self.flip_visualization and is_s2) else mv
                    if m not in rapid_out:
                        rapid_out.append(m)
                        rapid_lines.append((ln, is_s2))

            for mv in td["feed_moves"]:
                if abs(mv[2] - x) < 0.001 and abs(mv[3] - z) < 0.001:
                    m = self._flip_move(mv, z_ref) if (self.flip_visualization and is_s2) else mv
                    if m not in feed_out:
                        feed_out.append(m)
                        feed_lines.append((ln, is_s2))

        for x, z, tool in td["tool_changes"]:
            for ln, (cx, cz) in coords.items():
                if abs(cx - x) < 0.001 and abs(cz - z) < 0.001:
                    is_s2 = bool(flip_line and ln >= flip_line)
                    if self.current_filter == "side1" and is_s2:
                        continue
                    if self.current_filter == "side2" and not is_s2:
                        continue
                    if self.flip_visualization and is_s2:
                        z_ref = td["side1_deepest_z"] or 0
                        _, z_flip = self._flip_coord(x, z, z_ref)
                        tools_out.append((x, z_flip, tool))
                    else:
                        tools_out.append((x, z, tool))
                    break

        all_x = [v for m in rapid_out + feed_out for v in (m[0], m[2])]
        all_z = [v for m in rapid_out + feed_out for v in (m[1], m[3])]
        bounds = (
            {"x_min": min(all_x), "x_max": max(all_x),
             "z_min": min(all_z), "z_max": max(all_z)}
            if all_x else {"x_min": 0, "x_max": 0, "z_min": 0, "z_max": 0}
        )

        return {
            "rapid_moves":  rapid_out,  "rapid_lines":  rapid_lines,
            "feed_moves":   feed_out,   "feed_lines":   feed_lines,
            "tool_changes": tools_out,  "bounds":       bounds,
        }

    # ── Flip helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _flip_move(mv: Tuple, z_ref: float) -> Tuple:
        x1, z1, x2, z2 = mv
        return (x1, -(z1 - z_ref), x2, -(z2 - z_ref))

    @staticmethod
    def _flip_coord(x: float, z: float, z_ref: float) -> Tuple:
        return (x, -(z - z_ref))

    # ── Highlight helpers ─────────────────────────────────────────────────────

    def _highlight_position(self, x: float, z: float, line_num: int):
        if self.highlight_marker:
            self.highlight_marker.remove()
        self.highlight_marker = self.ax.plot(
            z, x, "o",
            color="#ff0000", markersize=12,
            markeredgewidth=2, markeredgecolor="#ffffff",
            zorder=10, label=f"Line {line_num}",
        )[0]
        handles, _ = self.ax.get_legend_handles_labels()
        if handles:
            self.ax.legend(
                loc="upper right",
                facecolor="#1a1d2e", edgecolor=self._GRID,
                labelcolor=self._FG, fontsize=9,
            ).get_frame().set_alpha(0.9)
        self.canvas.draw()

    def _highlight_gcode_line(self, displayed_line: int):
        """Scroll and highlight a displayed line in the G-code editor."""
        doc    = self._gcode_edit.document()
        block  = doc.findBlockByNumber(displayed_line - 1)
        cursor = QTextCursor(block)

        # Clear previous selection highlight
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#1e2840"))
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        self._gcode_edit.setTextCursor(cursor)
        self._gcode_edit.ensureCursorVisible()

    # ── matplotlib event handlers ─────────────────────────────────────────────

    def _on_pick(self, event):
        """Click on a plot line/point → sync G-code view."""
        artist = event.artist
        gid    = artist.get_gid()
        if not gid:
            return
        try:
            orig_line = int(gid)
        except ValueError:
            return

        disp_line = self.original_to_displayed.get(orig_line)
        if disp_line:
            self._highlight_gcode_line(disp_line)

        # Highlight marker
        td = self.toolpath_data
        if orig_line in td["line_coordinates"]:
            x, z = td["line_coordinates"][orig_line]
            flip_line = td["flip_line"]
            if (self.flip_visualization and flip_line
                    and orig_line >= flip_line):
                z_ref = td["side1_deepest_z"] or 0
                x, z  = self._flip_coord(x, z, z_ref)
            self._highlight_position(x, z, orig_line)
            self._update_play_label(orig_line)

    def _on_scroll_zoom(self, event):
        if event.inaxes != self.ax:
            return
        factor  = 0.8 if event.button == "up" else 1.2
        xlim    = self.ax.get_xlim()
        ylim    = self.ax.get_ylim()
        xd, yd  = event.xdata, event.ydata
        nw      = (xlim[1] - xlim[0]) * factor
        nh      = (ylim[1] - ylim[0]) * factor
        rx      = (xlim[1] - xd) / (xlim[1] - xlim[0])
        ry      = (ylim[1] - yd) / (ylim[1] - ylim[0])
        self.ax.set_xlim(xd - nw * (1 - rx), xd + nw * rx)
        self.ax.set_ylim(yd - nh * (1 - ry), yd + nh * ry)
        self.canvas.draw()

    def _on_mouse_press(self, event):
        if event.button == 3 and event.inaxes == self.ax:
            self.panning  = True
            self.pan_start = (event.xdata, event.ydata)

    def _on_mouse_release(self, event):
        if event.button == 3:
            self.panning   = False
            self.pan_start = None

    def _on_mouse_move(self, event):
        if self.panning and event.inaxes == self.ax and self.pan_start:
            dx = self.pan_start[0] - event.xdata
            dy = self.pan_start[1] - event.ydata
            xl = self.ax.get_xlim()
            yl = self.ax.get_ylim()
            self.ax.set_xlim(xl[0] + dx, xl[1] + dx)
            self.ax.set_ylim(yl[0] + dy, yl[1] + dy)
            self.canvas.draw()

    # ── G-code click handler ──────────────────────────────────────────────────

    def _on_gcode_line_clicked(self, displayed_line: int):
        orig_line = self.displayed_to_original.get(displayed_line)
        if orig_line is None:
            return
        td = self.toolpath_data
        if orig_line in td["line_coordinates"]:
            x, z      = td["line_coordinates"][orig_line]
            flip_line = td["flip_line"]
            if (self.flip_visualization and flip_line
                    and orig_line >= flip_line):
                z_ref = td["side1_deepest_z"] or 0
                x, z  = self._flip_coord(x, z, z_ref)
            self._highlight_position(x, z, orig_line)
            self.current_playback_line = orig_line
            self._update_play_label(orig_line)
        else:
            # Remove existing marker
            if self.highlight_marker:
                self.highlight_marker.remove()
                self.highlight_marker = None
                self.canvas.draw()

    # ── Playback controls ─────────────────────────────────────────────────────

    def _goto_first(self):
        coords = self.toolpath_data["line_coordinates"]
        if not coords:
            return
        self.current_playback_line = min(coords.keys())
        self._sync_playback()

    def _goto_last(self):
        coords = self.toolpath_data["line_coordinates"]
        if not coords:
            return
        self.current_playback_line = max(coords.keys())
        self._sync_playback()

    def _goto_next(self):
        coords = self.toolpath_data["line_coordinates"]
        if not coords:
            return
        nums = sorted(coords.keys())
        for n in nums:
            if n > self.current_playback_line:
                self.current_playback_line = n
                self._sync_playback()
                return
        self.current_playback_line = nums[0]
        self._sync_playback()

    def _goto_prev(self):
        coords = self.toolpath_data["line_coordinates"]
        if not coords:
            return
        nums = sorted(coords.keys())
        for n in reversed(nums):
            if n < self.current_playback_line:
                self.current_playback_line = n
                self._sync_playback()
                return
        self.current_playback_line = nums[-1]
        self._sync_playback()

    def _sync_playback(self):
        orig = self.current_playback_line
        td   = self.toolpath_data

        disp = self.original_to_displayed.get(orig)
        if disp:
            self._highlight_gcode_line(disp)

        if orig in td["line_coordinates"]:
            x, z      = td["line_coordinates"][orig]
            flip_line = td["flip_line"]
            if (self.flip_visualization and flip_line and orig >= flip_line):
                z_ref = td["side1_deepest_z"] or 0
                x, z  = self._flip_coord(x, z, z_ref)
            self._highlight_position(x, z, orig)

        self._update_play_label(orig)

    def _update_play_label(self, orig_line: int):
        coords = self.toolpath_data["line_coordinates"]
        total  = max(coords.keys()) if coords else 1
        self._play_label.setText(f"Line {orig_line} / {total}")

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_png(self):
        default = f"{self.program_number}_toolpath.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Toolpath as PNG", default,
            "PNG Image (*.png);;All Files (*.*)"
        )
        if not path:
            return
        try:
            self.fig.savefig(
                path, dpi=300,
                facecolor=self._BG, edgecolor="none", bbox_inches="tight",
            )
            QMessageBox.information(self, "Export Complete",
                                    f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
