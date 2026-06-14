# XLSX Export — Plan (new indicators + tabs)

Goal: surface the new 2PC/STEP indicators in the Excel export so they can be
autofiltered, and add focused 2PC and STEP tabs.

Status: ✅ confirmed by you · 🔎 proposed (confirm) · ❓ open.

---

## 1. Where the values come from
All derived per row from `program_title` + `verify_status` tokens (already selected
in the export query):

| Indicator | Source |
|-----------|--------|
| CB (mm), OB (mm), Thickness, Hub, Round | `parse_title_specs` (as today) |
| Counterbore (mm) | title `step_mm`, else `STEP:` token |
| Step Depth (in) | `SD:` token |
| RC recess (in) | `RC:` token |
| HB hub OD (in) | `HB:` token (trailing `?` = variable) |
| IH implicit hub (in) | `IH:` token |
| Type | `_part_type(title, verify_status)` (already STEP/2PC-HC aware) |

🔎 Add a small `_tokens(vstatus)` helper in `export_xlsx.py` to pull RC/HB/IH/STEP/SD.

---

## 2. Enriched main sheets (All + per-round-size) ✅ columns

New column set (original + 5 new). Proposed order:

```
O-Number · Title · Round Size · Type · CB (mm) · OB (mm) · Counterbore (mm) ·
Hub · Thickness · Step Depth · RC (in) · HB (in) · IH (in) · Notes ·
Verify Status · Fails
```

- **OB stays the hub bore** (`ob_mm`); for STEP parts OB is correctly blank and the
  new **Counterbore** column carries the second bore (e.g. 107). This fixes today's
  "blank OB on step rows" without overloading OB.
- New columns are blank ("N/A") when not applicable.
- FREE rows: new columns show "FREE" like the others.
- Autofilter already spans all columns → every new column is filterable in Excel.

🔎 Per-round sheets get the same columns for consistency.

---

## 3. New dedicated tabs 🔎 (confirm)

Placed right after **All**, before the per-round tabs.

### 3a. `2PC` tab — pairing view (all 2PC programs)
```
O-Number · Round · Type · CB (mm) · OB (mm) · RC (in) · HB (in) · IH (in) ·
Thickness · Verify Status · Fails
```
- Rows: every program where Type ∈ {2PC, 2PC HC} (i.e. `_is_2pc`).
- Sort: Round → CB → RC, so recess/hub candidates of the same size sit together.
- No FREE rows.

### 3b. `STEP` tab — step view (all STEP programs)
```
O-Number · Round · Type · CB (mm) · Counterbore (mm) · Step Depth · Thickness ·
Verify Status · Fails
```
- Rows: every program classified STEP (`_part_type == "STEP"`, i.e. title keyword
  OR `STEP:` token).
- Sort: Round → CB → Counterbore.
- No FREE rows.

---

## 4. Sheet order
`All` → `2PC` → `STEP` → `5.75in` → `6.00in` → … → `13.00in`

---

## 5. Files touched
| File | Change |
|------|--------|
| `ui/export_xlsx.py` | `_tokens()` helper; extend `_HEADERS`/`_COL_WIDTHS` + `_build_row`; add `_write_2pc_sheet()` / `_write_step_sheet()`; wire into `export_workbook` |

The daily-report export (`export_daily_report`) reuses the same row builder, so it
inherits the new columns automatically.

---

## 6. Open questions
1. **Tabs** — confirm 2PC + STEP tabs (assumed since "Both" was chosen but the tab
   question wasn't answered). Want both? Just one? Different columns?
2. Column **order/labels** above OK, or reorder (e.g. keep original order and append
   the 5 new columns at the end)?
3. Should the **2PC tab** also include FREE/unused O-numbers, or used files only?
   (Proposed: used only.)
