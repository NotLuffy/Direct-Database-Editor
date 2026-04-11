"""
CNC Direct Editor — Collapsible filter bar with cascading spec dropdowns.

CB, OB, Thickness, and Hub dropdowns are populated from actual DB titles and
update each other: selecting a round size narrows CB/OB/Thickness to only
values that exist for that round size, and vice versa.

Thickness supports multi-select (checkable menu) — multiple MM values can be
active simultaneously and are matched with OR logic.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QMenu
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import pyqtSignal

_STATUSES = ["All", "active", "flagged", "review", "delete"]

_PART_TYPES = ["All", "Standard", "HC — any", "HC — 15MM",
               "2PC", "LUG", "STUD", "STEP", "SPACER", "Steel Ring"]

_SCORE_OPTIONS = [
    ("All",    None,  None),
    ("6",      6,     6),
    ("4–5",    4,     5),
    ("2–3",    2,     3),
    ("0–1",    0,     1),
]

_STYLE = """
QWidget  { background: #0d0e18; }
QLabel   { color: #666688; font-size: 11px; }
QComboBox, QLineEdit {
    background: #1a1d2e; border: 1px solid #2a2d45;
    color: #ccccdd; padding: 2px 5px; border-radius: 3px;
    font-size: 11px;
}
QComboBox QAbstractItemView {
    background: #1a1d2e; color: #ccccdd;
    selection-background-color: #2a3055;
}
QPushButton {
    background: #1a1a2e; border: 1px solid #2a2d45;
    color: #556688; padding: 2px 8px; border-radius: 3px;
    font-size: 11px;
}
QPushButton:hover { background: #222240; color: #8899bb; }
"""

# ── spec key names stored in self._specs rows ─────────────────────────────────
_RS = "rs"    # round_size_in  (float, inches)
_CB = "cb"    # cb_mm          (float, mm)
_OB = "ob"    # ob_mm          (float, mm)
_TH = "th"    # length_in      (float, inches) stored as-is
_HC = "hc"    # hc_height_in   (float, inches), None = no hub


def _rs_key(v):  return round(v, 2)
def _cb_key(v):  return round(v, 1)
def _ob_key(v):  return round(v, 1)
def _hc_key(v):  return round(v * 1000)       # nearest-thou integer for bucketing


def _rs_label(k): return f"{k:.2f}"
def _cb_label(k): return f"{k:.1f}"
def _ob_label(k): return f"{k:.1f}"


def _th_display_label(th_in: float, from_mm: bool) -> str:
    """Format a thickness value in its original unit.
    from_mm=True  → '31.8MM'   (title specified mm, e.g. '32MM')
    from_mm=False → '1.250"'   (title specified inches, e.g. '1.25')
    """
    if from_mm:
        return f"{th_in * 25.4:.1f}MM"
    return f'{th_in:.3f}"'


def _th_label_to_inches(label: str) -> tuple[float, float]:
    """Parse a thickness label back to (value_in, tolerance_in).
    Inch labels use ±0.002", MM labels use ±0.1mm (≈±0.004").
    """
    if label.endswith("MM"):
        mm_val = float(label[:-2])
        return mm_val / 25.4, 0.1 / 25.4
    if label.endswith('"'):
        return float(label[:-1]), 0.002
    return 0.0, 0.002
def _hc_label(k):
    v = k / 1000.0
    if abs(v - 0.5906) < 0.002:
        return '15MM (0.591")'
    return f'{v:.3f}"'


class FilterBar(QWidget):

    filters_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        self.setFixedHeight(34)
        self._building = False
        # Each entry: {rs, cb, ob, th, hc_in}  (values may be None)
        self._specs: list[dict] = []
        # Set of MM label strings currently checked in the thickness menu
        self._thick_selections: set[str] = set()
        # All available MM thickness labels (for menu rebuild)
        self._thick_all_labels: list[str] = []
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        self._building = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(6)

        def lbl(text):
            l = QLabel(text)
            lay.addWidget(l)
            return l

        def combo(items, width=90, on_change=None):
            c = QComboBox()
            c.addItems(items)
            c.setFixedWidth(width)
            c.currentIndexChanged.connect(on_change or self._emit)
            lay.addWidget(c)
            return c

        lbl("Status:")
        self._status_combo = combo(_STATUSES, 90)

        lbl("Dup:")
        self._dup_combo = combo(["All", "Dups only", "No dups"], 90)

        lbl("Score:")
        self._score_combo = combo([o[0] for o in _SCORE_OPTIONS], 70)

        lbl("Round:")
        self._round_combo = combo(["All"], 76,
                                  on_change=self._on_spec_changed)

        lbl("CB mm:")
        self._cb_combo = combo(["All"], 76,
                               on_change=self._on_spec_changed)

        lbl("OB mm:")
        self._ob_combo = combo(["All"], 76,
                               on_change=self._on_spec_changed)

        # Thickness — multi-select button + checkable menu
        lbl("Thick:")
        self._thick_btn = QPushButton("All ▾")
        self._thick_btn.setFixedWidth(148)
        self._thick_btn.setStyleSheet(
            "QPushButton { background: #1a1d2e; border: 1px solid #2a2d45; "
            "color: #ccccdd; padding: 2px 5px; border-radius: 3px; "
            "font-size: 11px; text-align: left; }"
            "QPushButton:hover { background: #222240; }"
        )
        self._thick_menu = QMenu(self)
        self._thick_menu.setStyleSheet(
            "QMenu { background: #1a1d2e; color: #ccccdd; border: 1px solid #2a2d45; }"
            "QMenu::item:selected { background: #2a3055; }"
            "QMenu::item { padding: 3px 20px 3px 6px; font-size: 11px; }"
        )
        self._thick_btn.clicked.connect(self._show_thick_menu)
        lay.addWidget(self._thick_btn)

        lbl("Hub:")
        self._hub_combo = combo(["All", "No Hub"], 96,
                                on_change=self._on_spec_changed)

        lbl("Type:")
        self._type_combo = combo(_PART_TYPES, 100)

        lbl("Search:")
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("O-number, title, path, notes…")
        self._search_edit.setMinimumWidth(140)
        self._search_edit.textChanged.connect(self._emit)
        lay.addWidget(self._search_edit)

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(52)
        reset_btn.clicked.connect(self.reset)
        lay.addWidget(reset_btn)

        self._building = False

    # ------------------------------------------------------------------
    # Spec data — called by main window after DB load / rescan
    # ------------------------------------------------------------------

    def set_spec_data(self, specs: list[dict]):
        """
        Receive pre-parsed spec rows from the main window.
        Each row: {rs, cb, ob, th, hc_in}  (all may be None).
        """
        self._specs = [s for s in specs
                       if any(s.get(k) is not None
                              for k in (_RS, _CB, _OB, _TH, "hc_in"))]
        self._cascade()

    # ------------------------------------------------------------------
    # Cascade helpers
    # ------------------------------------------------------------------

    def _sel(self, combo: QComboBox):
        """Current selection text, or None for 'All'/'No Hub'."""
        t = combo.currentText()
        return None if (not t or t == "All") else t

    def _matching_specs(self, exclude: str | None = None) -> list[dict]:
        """Return spec rows that match all currently selected fields
        except the one named by exclude."""
        rs_sel  = self._sel(self._round_combo) if exclude != _RS else None
        cb_sel  = self._sel(self._cb_combo)    if exclude != _CB else None
        ob_sel  = self._sel(self._ob_combo)    if exclude != _OB else None
        # For cascade narrowing, use first checked thickness label (if any)
        th_label_sel = (next(iter(sorted(self._thick_selections)), None)
                        if exclude != _TH else None)
        hc_text = self._sel(self._hub_combo)   if exclude != _HC else None

        out = []
        for s in self._specs:
            if rs_sel is not None:
                sv = s.get(_RS)
                if sv is None or abs(_rs_key(sv) - float(rs_sel)) > 0.015:
                    continue
            if cb_sel is not None:
                sv = s.get(_CB)
                try:
                    if sv is None or abs(_cb_key(sv) - float(cb_sel)) > 0.05:
                        continue
                except (ValueError, TypeError):
                    continue
            if ob_sel is not None:
                sv = s.get(_OB)
                try:
                    if sv is None or abs(_ob_key(sv) - float(ob_sel)) > 0.05:
                        continue
                except (ValueError, TypeError):
                    continue
            if th_label_sel is not None:
                sv = s.get(_TH)
                if sv is None:
                    continue
                try:
                    target_in, tol = _th_label_to_inches(th_label_sel)
                    if abs(sv - target_in) > tol:
                        continue
                except (ValueError, TypeError):
                    continue
            if hc_text is not None:
                hc_in = s.get("hc_in")
                if hc_text == "No Hub":
                    if hc_in is not None:
                        continue
                else:
                    try:
                        raw = hc_text.split('"')[0].strip()
                        if '15MM' in raw:
                            target_thou = 591
                        else:
                            target_thou = round(float(raw) * 1000)
                        if hc_in is None or abs(_hc_key(hc_in) - target_thou) > 3:
                            continue
                    except (ValueError, TypeError):
                        continue
            out.append(s)
        return out

    def _populate_combo(self, combo: QComboBox, raw_vals: list,
                        label_fn, preserve: str | None,
                        fixed_top: list[str] | None = None):
        """Refill combo with fixed_top items + sorted unique labels,
        restoring the previous selection if it still exists."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All")
        if fixed_top:
            for item in fixed_top:
                combo.addItem(item)
        for v in raw_vals:
            combo.addItem(label_fn(v))
        all_items = ["All"] + (fixed_top or []) + [label_fn(v) for v in raw_vals]
        if preserve and preserve in all_items:
            combo.setCurrentText(preserve)
        combo.blockSignals(False)

    def _show_thick_menu(self):
        """Open the thickness checkable menu below the button."""
        self._thick_menu.exec(
            self._thick_btn.mapToGlobal(
                self._thick_btn.rect().bottomLeft()))

    def _rebuild_thick_menu(self, labels: list[str]):
        """Rebuild the thickness checkable menu, restoring checked state."""
        self._thick_menu.clear()
        for label in labels:
            action = QAction(label, self._thick_menu)
            action.setCheckable(True)
            action.setChecked(label in self._thick_selections)
            action.triggered.connect(lambda checked, lbl=label: self._on_thick_toggled(lbl, checked))
            self._thick_menu.addAction(action)
        self._thick_all_labels = list(labels)
        self._update_thick_btn_label()

    def _update_thick_btn_label(self):
        sel = sorted(self._thick_selections)
        if not sel:
            self._thick_btn.setText("All ▾")
        elif len(sel) == 1:
            self._thick_btn.setText(f"{sel[0]} ▾")
        else:
            self._thick_btn.setText(f"{len(sel)} selected ▾")

    def _on_thick_toggled(self, label: str, checked: bool):
        if checked:
            self._thick_selections.add(label)
        else:
            self._thick_selections.discard(label)
        self._update_thick_btn_label()
        self._cascade()
        self._emit()

    def _cascade(self):
        """Recompute available options for all five spec combos."""
        self._building = True

        rs_prev = self._sel(self._round_combo)
        cb_prev = self._sel(self._cb_combo)
        ob_prev = self._sel(self._ob_combo)
        hc_prev = self._sel(self._hub_combo)

        # Round size: specs matching current cb/ob/th/hc
        rs_pool = self._matching_specs(exclude=_RS)
        rs_vals = sorted({_rs_key(s[_RS]) for s in rs_pool if s.get(_RS)})
        self._populate_combo(self._round_combo, rs_vals, _rs_label, rs_prev)

        # CB: specs matching current rs/ob/th/hc
        cb_pool = self._matching_specs(exclude=_CB)
        cb_vals = sorted({_cb_key(s[_CB]) for s in cb_pool if s.get(_CB)})
        self._populate_combo(self._cb_combo, cb_vals, _cb_label, cb_prev)

        # OB: specs matching current rs/cb/th/hc
        ob_pool = self._matching_specs(exclude=_OB)
        ob_vals = sorted({_ob_key(s[_OB]) for s in ob_pool if s.get(_OB)})
        self._populate_combo(self._ob_combo, ob_vals, _ob_label, ob_prev)

        # Thickness: specs matching current rs/cb/ob/hc — rebuild checkable menu
        # Preserve original format: inch-specified → '1.250"', MM-specified → '31.8MM'
        th_pool = self._matching_specs(exclude=_TH)
        seen_th: set[str] = set()
        th_labels: list[str] = []
        for s in th_pool:
            th_in = s.get(_TH)
            if th_in is None:
                continue
            lbl = _th_display_label(th_in, s.get("th_from_mm", False))
            if lbl not in seen_th:
                seen_th.add(lbl)
                th_labels.append(lbl)
        # Sort: inches first (ascending), then MM (ascending)
        def _th_sort_key(lbl: str):
            if lbl.endswith('"'):
                return (0, float(lbl[:-1]))
            mm_val = float(lbl[:-2]) if lbl.endswith("MM") else 0.0
            return (1, mm_val)
        th_labels.sort(key=_th_sort_key)
        # Remove any checked selections that no longer appear in this pool
        self._thick_selections &= set(th_labels)
        self._rebuild_thick_menu(th_labels)

        # Hub: specs matching current rs/cb/ob/th — always show "No Hub" option
        hc_pool = self._matching_specs(exclude=_HC)
        hc_thou_vals = sorted({_hc_key(s["hc_in"])
                                for s in hc_pool if s.get("hc_in") is not None})
        self._populate_combo(self._hub_combo, hc_thou_vals, _hc_label,
                             hc_prev, fixed_top=["No Hub"])

        self._building = False

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_spec_changed(self):
        if self._building:
            return
        self._cascade()
        self._emit()

    def _emit(self):
        if self._building:
            return
        self.filters_changed.emit(self.current_filters())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_filters(self) -> dict:
        st_text = self._status_combo.currentText()
        status  = None if st_text == "All" else st_text

        dup_text = self._dup_combo.currentText()
        has_dup  = 1 if dup_text == "Dups only" else (0 if dup_text == "No dups" else None)

        sc_idx = self._score_combo.currentIndex()
        _, score_min, score_max = _SCORE_OPTIONS[sc_idx]

        pt_text   = self._type_combo.currentText()
        part_type = "" if pt_text == "All" else pt_text

        # Hub height: "All"→None, "No Hub"→"none", label→inch float string
        hc_text = self._sel(self._hub_combo)
        if hc_text is None:
            hub_height = None
        elif hc_text == "No Hub":
            hub_height = "none"
        else:
            try:
                if "15MM" in hc_text:
                    hub_height = str(round(15.0 / 25.4, 4))
                else:
                    hub_height = str(float(hc_text.replace('"', "").strip()))
            except ValueError:
                hub_height = None

        # Thickness: list of selected MM labels, or None if none selected
        thickness = sorted(self._thick_selections) if self._thick_selections else None

        return {
            "status":       status,
            "has_dup_flag": has_dup,
            "score_min":    score_min,
            "score_max":    score_max,
            "round_size":   self._sel(self._round_combo),
            "cb_mm":        self._sel(self._cb_combo),
            "ob_mm":        self._sel(self._ob_combo),
            "thickness":    thickness,   # list[str] | None  e.g. ["20.0MM", "25.4MM"]
            "hub_height":   hub_height,
            "part_type":    part_type,
            "search":       self._search_edit.text().strip(),
        }

    def reset(self):
        self._building = True
        self._status_combo.setCurrentIndex(0)
        self._dup_combo.setCurrentIndex(0)
        self._score_combo.setCurrentIndex(0)
        self._round_combo.setCurrentIndex(0)
        self._cb_combo.setCurrentIndex(0)
        self._ob_combo.setCurrentIndex(0)
        self._thick_selections.clear()
        self._update_thick_btn_label()
        self._hub_combo.setCurrentIndex(0)
        self._type_combo.setCurrentIndex(0)
        self._search_edit.clear()
        self._building = False
        self._cascade()   # re-populate all spec combos with the full unfiltered option sets
        self._emit()
