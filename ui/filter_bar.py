"""
CNC Direct Editor — Collapsible filter bar with cascading spec dropdowns.

CB, OB, Thickness, and Hub dropdowns are populated from actual DB titles and
update each other: selecting a round size narrows CB/OB/Thickness to only
values that exist for that round size, and vice versa.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton
)
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
_TH = "th"    # length_in      stored as nearest-mm integer (th_mm)
_HC = "hc"    # hc_height_in   (float, inches), None = no hub


def _rs_key(v):  return round(v, 2)
def _cb_key(v):  return round(v, 1)
def _ob_key(v):  return round(v, 1)
def _th_key(v):  return round(v, 3)         # inches, 3 decimal places
def _hc_key(v):  return round(v * 1000)     # nearest-thou integer for bucketing


def _rs_label(k): return f"{k:.2f}"
def _cb_label(k): return f"{k:.1f}"
def _ob_label(k): return f"{k:.1f}"
def _th_label(k): return f'{k:.3f}"'
def _hc_label(k):
    v = k / 1000.0
    # Special display for common values
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
        # Each entry: {rs, cb, ob, th_mm, hc_in}  (values may be None)
        self._specs: list[dict] = []
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

        lbl("Thick:")
        self._thick_combo = combo(["All"], 76,
                                  on_change=self._on_spec_changed)

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
        Each row: {rs, cb, ob, th_mm, hc_in}  (all may be None).
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
        th_sel  = self._sel(self._thick_combo) if exclude != _TH else None
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
            if th_sel is not None:
                sv = s.get(_TH)
                try:
                    if sv is None or abs(_th_key(sv) - float(th_sel.replace('"', "").strip())) > 0.002:
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
                        target_thou = int(hc_text.split('"')[0].strip().replace('15MM (0.591)', '591'))
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

    def _cascade(self):
        """Recompute available options for all five spec combos."""
        self._building = True

        rs_prev = self._sel(self._round_combo)
        cb_prev = self._sel(self._cb_combo)
        ob_prev = self._sel(self._ob_combo)
        th_prev = self._sel(self._thick_combo)
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

        # Thickness: specs matching current rs/cb/ob/hc
        th_pool = self._matching_specs(exclude=_TH)
        th_vals = sorted({_th_key(s[_TH]) for s in th_pool if s.get(_TH)})
        self._populate_combo(self._thick_combo, th_vals, _th_label, th_prev)

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
            # Parse back the inch value from the label (e.g. '0.591"' → '0.591')
            try:
                if "15MM" in hc_text:
                    hub_height = str(round(15.0 / 25.4, 4))
                else:
                    hub_height = str(float(hc_text.replace('"', "").strip()))
            except ValueError:
                hub_height = None

        return {
            "status":       status,
            "has_dup_flag": has_dup,
            "score_min":    score_min,
            "score_max":    score_max,
            "round_size":   self._sel(self._round_combo),
            "cb_mm":        self._sel(self._cb_combo),
            "ob_mm":        self._sel(self._ob_combo),
            "thickness":    self._sel(self._thick_combo),   # e.g. '0.750"' or None
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
        self._thick_combo.setCurrentIndex(0)
        self._hub_combo.setCurrentIndex(0)
        self._type_combo.setCurrentIndex(0)
        self._search_edit.clear()
        self._building = False
        self._emit()
