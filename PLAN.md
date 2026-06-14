# CNC Direct Editor — Plan: Daily Report + Verify Legend

---

## Feature 1 — Daily New-Files Report (Export XLSX)

### Where files come from
`index_date` (ISO timestamp) is written when the New File Creator saves a file.
Query: `SELECT * FROM files WHERE date(index_date) = '<chosen_date>'`

### Entry point
New toolbar button **"Daily Report"** (enabled when DB loaded), calls `_on_daily_report()` in `main_window.py`.

### Date picker dialog (inline, no extra library)
- Small `QDialog` with a `QCalendarWidget`
- Pre-selects today
- "Generate" button → picks date → queries DB → exports

### DB query (`direct_database.py`)
New function:
```python
def get_files_by_index_date(db_path: str, date_str: str) -> list:
    """Return all files where date(index_date) = date_str (YYYY-MM-DD)."""
```

### Export (`ui/export_xlsx.py`)
New function `export_daily_report(db_path, out_path, date_str, scan_folders)`:
- Single sheet named after the date (e.g. `2026-04-08`)
- Same columns as the main export: O-Number, Title, Round Size, CB, OB, Thickness, Hub, Type, Notes, Verify Status, Fails
- Same sort: type → CB → thickness
- No FREE rows (this is a created-files report, not a range map)
- Same light theme / header style
- If 0 files found → show QMessageBox "No files created on <date>."

### main_window.py wiring
```python
def _on_daily_report(self):
    # 1. Open date-picker dialog
    # 2. Get chosen date string
    # 3. QFileDialog.getSaveFileName for xlsx path
    # 4. Call export_daily_report(...)
    # 5. Show success message
```

### Files touched
| File | Change |
|------|--------|
| `ui/main_window.py` | Toolbar button + `_on_daily_report()` + date-picker dialog |
| `direct_database.py` | `get_files_by_index_date()` |
| `ui/export_xlsx.py` | `export_daily_report()` |

---

## Feature 2 — Verify Legend Button

### What the abbreviations mean
| Token | Full name | What it checks |
|-------|-----------|----------------|
| CB | Center Bore | Bore diameter machined in OP1 matches title CB spec ±0.5mm |
| OB | Outer Bore | Second bore (HC/STEP/2PC) matches title OB spec ±0.5mm |
| DR | Drill | T101 drill depth matches total thickness + hub height + 0.15" |
| OD | OD Turn | Outside diameter pass in OP1/OP2 matches round-size OD table ±0.015" |
| PC | P-Code | G154 P## work offset matches thickness-based lookup table |
| HM | Home position | G53 X-11 Z-## home move matches expected Z for total thickness |
| PASS | — | Check found and value is within tolerance |
| FAIL | — | Check found but value is out of tolerance |
| NF | Not Found | Check could not locate the relevant G-code block |
| LOOSE | — | CB found but tolerance is wider than expected (HC hub bore) |
| RC | Recess | 2PC: recess X diameter found in G-code |
| HB | Hub OD | 2PC: hub OD diameter found in G-code |
| IH | Implicit Hub | 2PC: hub height inferred from G-code (not in title) |

### Where to add it
In `ui/verify_panel.py` — add a `QPushButton("Legend")` to the header bar (right-aligned).

### Legend dialog
`_show_legend()` method → creates a `QDialog` with a styled HTML `QLabel` or simple `QTableWidget` listing all tokens + descriptions. Dark theme. Non-modal (`show()` not `exec()`).

### Layout change to header bar
Currently `self._hdr` is a plain `QLabel`. Change to a `QHBoxLayout` strip:
- Left: file name label (current `self._hdr` content)
- Right: `QPushButton("Legend ?")` (small, always visible)

### Files touched
| File | Change |
|------|--------|
| `ui/verify_panel.py` | Header → HBox, Legend button, `_show_legend()` dialog |

---

## Implementation Order
1. `direct_database.py` — `get_files_by_index_date()`
2. `ui/export_xlsx.py` — `export_daily_report()`
3. `ui/main_window.py` — Daily Report button + date-picker + handler
4. `ui/verify_panel.py` — Legend button + dialog
