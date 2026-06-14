"""
CNC Direct Editor — Collapsible filter bar with cascading spec dropdowns.

CB, OB, Thickness, and Hub dropdowns are populated from actual DB titles and
update each other: selecting a round size narrows CB/OB/Thickness to only
values that exist for that round size, and vice versa.

Thickness supports multi-select (checkable menu) — multiple MM values can be
active simultaneously and are matched with OR logic.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QMenu
)
from PyQt6.QtGui import QAction, QStandardItemModel, QStandardItem
from PyQt6.QtCore import pyqtSignal, QEvent, Qt, QSortFilterProxyModel

_STATUSES = ["All", "active", "flagged", "review", "delete"]

_PART_TYPES = ["All", "Standard", "HC — any", "HC — 15MM",
               "2PC", "2PC HC", "LUG", "STUD", "STEP", "SPACER", "Steel Ring"]

# 2PC piece role (from verify tokens): Recess = has RC, Hub = 0.25" mating hub
# (HB/IH, not HC), HC = 0.50" hub / 2PC HC.  Helps pull up one half of a pair.
_TWOPC_ROLES = ["All", "Recess", "Hub", "HC"]

_SCORE_OPTIONS = [
    ("All",    None,  None),
    ("8",      8,     8),
    ("6–7",    6,     7),
    ("4–5",    4,     5),
    ("0–3",    0,     3),
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
QComboBox QLineEdit {
    background: #1a1d2e; border: none; color: #ccccdd;
    padding: 0px 2px; font-size: 11px;
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
def _cb_key(v):  return round(v, 2)   # 2 decimal places so 66.56 stays distinct from 66.5
def _ob_key(v):  return round(v, 2)
def _hc_key(v):  return round(v * 1000)       # nearest-thou integer for bucketing


def _rs_label(k): return f"{k:.2f}"
def _cb_label(k): return f"{k:g}"    # :g drops trailing zeros: 66.56→"66.56", 66.0→"66"
def _ob_label(k): return f"{k:g}"


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


class _AlwaysShowFirstProxy(QSortFilterProxyModel):
    """Proxy that always shows row 0 ('All') regardless of filter text."""
    def filterAcceptsRow(self, source_row, source_parent):
        if source_row == 0:
            return True
        return super().filterAcceptsRow(source_row, source_parent)


class FilterableComboBox(QComboBox):
    """Editable combo whose dropdown narrows as the user types.
    Uses a proxy model so the line edit text is never disturbed by filtering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = QStandardItemModel(self)
        self._proxy = _AlwaysShowFirstProxy(self)
        self._proxy.setSourceModel(self._src)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self._proxy)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(25)
        self.setCompleter(None)
        self.lineEdit().setPlaceholderText("All")
        self.lineEdit().installEventFilter(self)
        self.lineEdit().textEdited.connect(self._on_text_edited)

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if not self.view().isVisible():
                self.showPopup()
        return super().eventFilter(obj, event)

    def set_all_items(self, items: list[str], preserve: str | None = None):
        """Rebuild the full item list, reset the filter, and optionally restore a selection."""
        self._src.clear()
        for label in items:
            self._src.appendRow(QStandardItem(label))
        self._proxy.setFilterFixedString("")
        # Clear any typed text (shows placeholder "All")
        self.blockSignals(True)
        self.lineEdit().blockSignals(True)
        self.lineEdit().clear()
        self.lineEdit().blockSignals(False)
        self.blockSignals(False)
        # Re-select preserved value if it's still in the list
        if preserve and preserve != "All":
            for i in range(self._src.rowCount()):
                if self._src.item(i).text() == preserve:
                    proxy_row = self._proxy.mapFromSource(self._src.index(i, 0)).row()
                    self.blockSignals(True)
                    self.lineEdit().blockSignals(True)
                    self.setCurrentIndex(proxy_row)
                    self.lineEdit().setText(preserve)
                    self.lineEdit().blockSignals(False)
                    self.blockSignals(False)
                    break

    def _on_text_edited(self, text: str):
        # Block signals so Qt's model-reset logic can't overwrite the line edit
        self.blockSignals(True)
        self.lineEdit().blockSignals(True)
        self._proxy.setFilterFixedString(text)
        # Restore typed text (Qt may have clobbered it when currentIndex changed)
        self.lineEdit().setText(text)
        self.lineEdit().setCursorPosition(len(text))
        self.lineEdit().blockSignals(False)
        self.blockSignals(False)
        if not self.view().isVisible():
            self.showPopup()

    def currentText(self) -> str:
        t = super().currentText().strip()
        return t if t else "All"


class FilterBar(QWidget):

    filters_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        self.setFixedHeight(62)   # two rows
        self._building = False
        # Each entry: {rs, cb, ob, th, hc_in}  (values may be None)
        self._specs: list[dict] = []
        # Set of MM label strings currently checked in the thickness menu
        self._thick_selections: set[str] = set()
        # All available MM thickness labels (for menu rebuild)
        self._thick_all_labels: list[str] = []
        # Cached 2PC token value lists (for repopulating after reset)
        self._rc_values_cache: list = []
        self._step_values_cache: list = []
        self._cbore_values_cache: list = []
        self._hb_values_cache: list = []
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        self._building = True
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 2)
        root.setSpacing(3)
        row1 = QHBoxLayout(); row1.setSpacing(6)
        row2 = QHBoxLayout(); row2.setSpacing(6)
        root.addLayout(row1)
        root.addLayout(row2)

        def lbl(text, row):
            l = QLabel(text)
            row.addWidget(l)
            return l

        def combo(items, row, width=90, on_change=None):
            c = QComboBox()
            c.addItems(items)
            c.setFixedWidth(width)
            c.currentIndexChanged.connect(on_change or self._emit)
            row.addWidget(c)
            return c

        def filterable(row, width=78, on_change=None):
            c = FilterableComboBox()
            c.setFixedWidth(width)
            c.set_all_items(["All"])
            cb = on_change or self._emit
            c.currentIndexChanged.connect(cb)
            c.lineEdit().editingFinished.connect(cb)
            row.addWidget(c)
            return c

        # ── Row 1 — general + title-spec filters ─────────────────────────────
        lbl("Status:", row1)
        self._status_combo = combo(_STATUSES, row1, 90)

        lbl("Dup:", row1)
        self._dup_combo = combo(["All", "Dups only", "No dups"], row1, 90)

        lbl("Score:", row1)
        self._score_combo = combo([o[0] for o in _SCORE_OPTIONS], row1, 70)

        lbl("Type:", row1)
        self._type_combo = combo(_PART_TYPES, row1, 100)

        lbl("Round:", row1)
        self._round_combo = combo(["All"], row1, 76, on_change=self._on_spec_changed)

        lbl("CB mm:", row1)
        self._cb_combo = filterable(row1, 84, self._on_spec_changed)

        lbl("OB mm:", row1)
        self._ob_combo = filterable(row1, 84, self._on_spec_changed)

        # Thickness — multi-select button + checkable menu
        lbl("Thick:", row1)
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
        row1.addWidget(self._thick_btn)

        lbl("Hub:", row1)
        self._hub_combo = combo(["All", "No Hub"], row1, 96,
                                on_change=self._on_spec_changed)
        row1.addStretch()

        # ── Row 2 — 2PC / step value filters + search ────────────────────────
        # 2PC pairing group: role + recess + hub together
        lbl("2PC:", row2)
        self._twopc_combo = combo(_TWOPC_ROLES, row2, 84)

        lbl("RC:", row2)          # recess value (RC token, inches)
        self._rc_combo = filterable(row2, 78)

        lbl("HB:", row2)          # hub OD (HB token, inches)
        self._hb_combo = filterable(row2, 78)

        row2.addSpacing(16)
        # STEP group: depth + counterbore
        lbl("Step:", row2)        # step depth (SD token, inches)
        self._step_combo = combo(["All"], row2, 74)

        lbl("CBore:", row2)       # counterbore value (mm) — step_mm / STEP token
        self._cbore_combo = filterable(row2, 78)

        row2.addSpacing(16)
        lbl("Search:", row2)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("O-number, title, path, notes…")
        self._search_edit.setMinimumWidth(160)
        self._search_edit.textChanged.connect(self._emit)
        row2.addWidget(self._search_edit, stretch=1)

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(52)
        reset_btn.clicked.connect(self.reset)
        row2.addWidget(reset_btn)

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

    def set_twopc_values(self, rc_values: list, step_depths: list,
                         cbore_values: list = None, hb_values: list = None):
        """Populate the RC / Step / Counterbore / HB dropdowns from verify-token
        values present in the data.  RC, Step, HB are inches; Counterbore is mm."""
        cbore_values = cbore_values or []
        hb_values    = hb_values or []
        self._rc_values_cache    = list(rc_values)
        self._step_values_cache  = list(step_depths)
        self._cbore_values_cache = list(cbore_values)
        self._hb_values_cache    = list(hb_values)

        rc_prev = self._sel(self._rc_combo)
        rc_items = ["All"] + [f'{v:.3f}"'
                              for v in sorted({round(x, 3) for x in rc_values})]
        self._rc_combo.set_all_items(rc_items, preserve=rc_prev)

        cb_prev = self._sel(self._cbore_combo)
        cbore_items = ["All"] + [f"{v:g}"
                                 for v in sorted({round(x, 1) for x in cbore_values})]
        self._cbore_combo.set_all_items(cbore_items, preserve=cb_prev)

        hb_prev = self._sel(self._hb_combo)
        hb_items = ["All"] + [f'{v:.3f}"'
                             for v in sorted({round(x, 3) for x in hb_values})]
        self._hb_combo.set_all_items(hb_items, preserve=hb_prev)

        self._step_combo.blockSignals(True)
        step_prev = self._step_combo.currentText()
        self._step_combo.clear()
        step_items = ["All"] + [f'{v:.3f}"'
                                for v in sorted({round(x, 3) for x in step_depths})]
        for it in step_items:
            self._step_combo.addItem(it)
        if step_prev in step_items:
            self._step_combo.setCurrentText(step_prev)
        self._step_combo.blockSignals(False)

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
                    if sv is None or abs(_cb_key(sv) - float(cb_sel)) > 0.005:
                        continue
                except (ValueError, TypeError):
                    continue
            if ob_sel is not None:
                sv = s.get(_OB)
                try:
                    if sv is None or abs(_ob_key(sv) - float(ob_sel)) > 0.005:
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
        all_items = ["All"] + (fixed_top or []) + [label_fn(v) for v in raw_vals]
        if isinstance(combo, FilterableComboBox):
            combo.set_all_items(all_items, preserve=preserve)
            return
        combo.blockSignals(True)
        combo.clear()
        for item in all_items:
            combo.addItem(item)
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

        tp_text     = self._twopc_combo.currentText()
        twopc_role  = "" if tp_text == "All" else tp_text

        def _val(text):
            if not text or text == "All":
                return None
            try:
                return float(text.replace('"', "").strip())
            except ValueError:
                return None
        rc_value    = _val(self._sel(self._rc_combo))
        step_depth  = _val(self._step_combo.currentText())
        cbore_value = _val(self._sel(self._cbore_combo))   # mm
        hb_value    = _val(self._sel(self._hb_combo))      # inches

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
            "twopc_role":   twopc_role,
            "rc_value":     rc_value,     # recess diameter (in) | None
            "step_depth":   step_depth,   # step depth (in) | None
            "cbore_value":  cbore_value,  # counterbore diameter (mm) | None
            "hb_value":     hb_value,     # hub OD (in) | None
            "search":       self._search_edit.text().strip(),
        }

    def reset(self):
        self._building = True
        self._status_combo.setCurrentIndex(0)
        self._dup_combo.setCurrentIndex(0)
        self._score_combo.setCurrentIndex(0)
        self._round_combo.setCurrentIndex(0)
        self._cb_combo.set_all_items(getattr(self._cb_combo, "_all_items", ["All"]))
        self._ob_combo.set_all_items(getattr(self._ob_combo, "_all_items", ["All"]))
        self._thick_selections.clear()
        self._update_thick_btn_label()
        self._hub_combo.setCurrentIndex(0)
        self._type_combo.setCurrentIndex(0)
        self._twopc_combo.setCurrentIndex(0)
        self._step_combo.setCurrentIndex(0)
        # Clear the typeable value combos so set_twopc_values doesn't preserve them
        for c in (self._rc_combo, self._cbore_combo, self._hb_combo):
            c.lineEdit().clear()
        self._search_edit.clear()
        self._building = False
        # Repopulate RC/Step/CBore/HB value lists from cache (selections now cleared)
        self.set_twopc_values(self._rc_values_cache, self._step_values_cache,
                              self._cbore_values_cache, self._hb_values_cache)
        self._cascade()   # re-populate all spec combos with the full unfiltered option sets
        self._emit()
