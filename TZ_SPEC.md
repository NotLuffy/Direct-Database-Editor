# Turning Z-Depth (TZ) — Spec & Revision (working draft)

Goal: stop flagging valid **two-sided** turning (and thick parts the table can't
reach) as TZ failures, and define TZ for **MM-thick** and **MM-thick + HC** parts.

Status legend: ✅ confirmed by you · 🔎 inferred from code/your notes · ❓ open question.

---

## 1. How turning works (model)

✅ The OD is turned down from **both sides** — once in OP1 (pre-flip, T303) and once
in OP2 (post-flip, T303). Each side plunges to some Z depth; together the two cuts
must cover the **full part thickness** so no uncut band is left in the middle.

🔎 Today's table `_TURNING_Z_TABLE` encodes a balanced split: per-side limit ≈
`total/2 + 0.05` (e.g. 2.00" → −1.05 each; two sides = 2.10 = total + 0.10 overlap).

---

## 2. Current verifier behavior (what's there now)

`verifier.py` ~2009–2037:
1. **Table limit** — each side must satisfy `z ≥ table_limit − TZ_TOLERANCE`.
2. **0.75× rule** (parts < 4") — neither side deeper than `0.75 × total`.
3. **Hard cap** — no side deeper than **−4.15"**.
4. `tz_ok = True` unless a side fails. Sides are judged **independently**.

### Problems
- ❌ The table **stops at 4.00"** (`-2.05`). Parts thicker than ~4.3" → `tz_limit = None`
  → TZ shows **NF**, never verified.
- ❌ Independent per-side judging rejects a **legal asymmetric split** (one deep side +
  one shallow side) even when the two cuts correctly cover the part.
- ❓ The −4.15" hard cap and 0.75× rule don't match the turning cap you described (−3.25").

---

## 3. Proposed acceptance (primary OR secondary)

A part **passes TZ** if EITHER path passes:

### Primary (current, balanced split) ✅
Both sides within the table limit (`z ≥ table_limit − TZ_TOLERANCE`). Unchanged, so
no regression for normal parts.

### Secondary (asymmetric / thick two-sided split) 🔎 — NEW
Used **when the primary path fails or the table has no entry** (thick parts). Given the
deepest turning Z on each side, `d1 = |z_op1|`, `d2 = |z_op2|`, and `total` thickness:

1. **Per-side cap:** `max(d1, d2) ≤ 3.25 + TZ_TOLERANCE`. *(−3.25" is the absolute
   deepest any single side may turn.)*
2. **Full coverage:** `d1 + d2 ≥ total − TZ_TOLERANCE` — the two cuts meet/overlap, no
   uncut middle band.
3. **Not over-cut:** `d1 + d2 ≤ total + MAX_OVERLAP` where `MAX_OVERLAP ≈ 0.15"`
   (nominal overlap is **0.10"** for cleanup).

So the deep side is `min(needed, 3.25)` and the shallow side takes the remainder.

### Worked examples
| Total | Deep side | Shallow side | Sum | Path |
|------:|----------:|-------------:|----:|------|
| 2.00" | −1.05 | −1.05 | 2.10 | primary (table) |
| 3.50" | −2.75 (≈ total−0.75) | −0.90 | 3.65 | secondary |
| 6.00" | **−3.25** (capped) | **−2.85** (remainder) | 6.10 | secondary |

(6.00": one side hits the −3.25" cap, the other covers `6.10 − 3.25 = 2.85`, the extra
0.10 being the overlap that cleans the part.)

---

## 4. Open questions (please confirm before I code)

1. ❓ **Per-side cap = −3.25"** for turning, and it **replaces** the old −4.15" cap
   (which stays only for *drilling*)? Or do both caps apply?
2. ❓ **Shallow side "0.7 or 0.9":** do you want me to *enforce* a specific shallow
   value, or is it enough to verify **cap + full coverage** (so any split that covers
   the part with neither side past −3.25" passes)? What picks 0.7 vs 0.9 — thickness,
   lathe, or hub?
3. ❓ **Overlap:** nominal 0.10", max acceptable 0.15"? Is *too much* overlap (e.g.
   both sides cut deep, big sum) a fail, or only "uncut middle" matters?
4. ❓ **Single-op parts:** if a part is turned from only ONE side (no OP2 T303), does
   the secondary path apply, or must single-side parts stay within the table?
5. ❓ **0.75× rule:** keep it, or does the new cap+coverage logic replace it?

---

## 5. MM-thick & MM-thick + HC parts (needs your rules)

Today: parts whose thickness was parsed from millimetres set `length_from_mm = True`,
which **skips drill (DR) verification** entirely (verifier.py ~1946). TZ already has MM
entries in the table (10/12/13/15/17/20/22 MM, and MM+0.50"HC combos).

❓ To "teach how to test and score" these I need the expected rules:
1. **Drill depth (DR)** for an MM-thick disc — does the inch rule still hold
   (`Z ≈ −(thickness_in + 0.15)`), or a different formula for thin MM discs?
2. **MM + HC**: is total = `disc_mm_in + hc_height_in`, then the same DR / TZ / P-code
   rules as inch parts? Or special-cased?
3. Are thin MM discs **drilled at all**, or pre-drilled stock (DR = N/A, not NF)?
4. Worked example would lock it: pick one real MM title + its correct DR and TZ Z values.
