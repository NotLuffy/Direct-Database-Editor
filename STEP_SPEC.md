# STEP Parts — Detection Spec (working draft)

Problem: STEP titles look **identical** to HC titles (e.g. `7.5 IN 110/107MM STEP 1.5`
vs an HC `7.5 IN 110/107MM 1.5 HC`), and **not all STEP titles contain "STEP"**. So we
can't classify reliably from the title — we must read the G-code.

Status legend: ✅ confirmed by you · 🔎 inferred from code/data · ❓ open question.

---

## 1. What a STEP is (vs HC)

✅ A STEP has **two concentric bores cut from the same side** (OP1 / T121, pre-flip):
1. a **center bore (CB)** = the **larger, first** title value, cut to a shallow
   **step depth**, then
2. the tool steps **inward** to a **smaller counter bore** which is then cut
   **deeper / through** the rest of the part.

✅ This is a **counterbore + centerbore**, both internal, one side.
✅ HC is different: its second bore is the **hub bore on the flip side (OP2)** with a
hub protrusion — there is never a same-side step-down to a smaller diameter.

### Worked example — O76313  `7.5 IN 110/107MM STEP 1.5`
OP1 / T121 block (after the rough-bore passes):
```
X4.534
G01 Z0
G01 X4.334 Z-0.1 F0.015 (X IS CB)   ← CB  = 4.334" = 110.08mm  (first value 110)
Z-0.43                              ← step depth = 0.43"
X4.2165                            ← counterbore = 4.2165" = 107.1mm (second value 107)
Z-1.65                             ← smaller bore continues through
X3.8
G00 Z0.2
```
So `110` = CB (X4.334), `107` = counterbore (X4.2165). Both are internal bores.

---

## 2. G-code detection signature ✅ (implemented)

**Candidate gate (title):** only run on a title with **two bore values (CB/counterbore)
and NO "HC"**. An "HC" in the title means a hub part → trust the title, skip detection.
This is what eliminates HC false positives.

Within the **pre-flip T121 block**:
1. track the deepest Z the **CB diameter** (`≈ first title value / 25.4`) is cut to —
   the **shelf depth** `d`;
2. find a feed move **inward** to the **counterbore** whose X **matches the second
   title value** (`≈ second value / 25.4`, within ~1 mm) and is **smaller** than CB,
   cut **deeper than the shelf** `d` (shelf within 0.20–1.85").

→ STEP. Report `step_cb_mm` (counterbore) and `step_depth_in = d`.

**Why match the second title value?** An ordinary bore's *breakthrough-relief* pass also
plunges, retracts inward and goes deeper — but it retracts to an arbitrary drill
clearance diameter, **not a named title dimension**. Requiring the inward bore to equal
the second title value (e.g. O76313's 107) rejects those. (Validated: 0 false positives
across 1245 HC titles; 56 keyword-less steps correctly found, e.g. `90/74MM B/C 6MM DEEP`.)

🔎 Conversions: diameter mm = X_inch × 25.4 (HAAS lathe X is a diameter).

Implemented in `verifier._find_step_bore()`; result keys `step_detected`,
`step_cb_mm`, `step_depth_in`; persisted as a `STEP:<mm>` token by `direct_scorer`.

---

## 3. Step-depth vs thickness rule ✅ (upper-bound check)

✅ The step must **leave ≥ ~0.75" of material**: `step_depth ≤ thickness − 0.75`.
✅ The largest step depth ever used is **~1.78"** (very thick parts).
✅ It is an **upper bound, not a fixed value** — O76313 is 1.5" thick yet only steps
0.43" (well under the 0.75" allowed).

🔎 Depths appear as rough/finish **pairs** ~0.03" apart, e.g.
`0.40/0.43, 0.75/0.78, 1.00/1.03, 1.25/1.28, …` (finish is the deeper of each pair).

❓ Confirm the exact rule for verification: flag FAIL when
`step_depth > thickness − 0.75 (+ small tol)`? And is "leave 0.75" exact, or ~0.72
(2.00" thick → 1.28" depth leaves 0.72")?

---

## 4. How this plugs into the app

| Concern | Today | Proposed change |
|---------|-------|-----------------|
| `parse_title_specs` | second value → `ob_mm` (hub) unless title has "STEP", then → `step_mm` | unchanged (title-only); geometry decides in the verifier |
| Verifier | only title `\bSTEP\b` drives step handling | add `_find_step_bore()` G-code detector → sets `is_step` even with no keyword; outputs `step_cb_mm`, `step_depth_in` |
| Classification (`_part_type`, Type column) | STEP only if title has "STEP" | STEP when geometry detected, regardless of keyword |
| CB/OB columns (grid + export) | shows OB for these (wrong) | show CB (110) + counterbore as OB/step (107), not a hub |
| Verify tokens/score | CB + OB(hub) checks | CB + STEP(counterbore) check; optional step-depth check (§3) |

❓ **Precedence:** when the title says HC but the G-code shows the step pattern (or vice
versa), which wins? (Proposal: **geometry wins** — same "tokens over keywords" rule we
chose for 2PC.)

---

## 5. Open questions
1. Confirm the §3 depth rule (`thickness − 0.75`, tolerance) — and whether to make it a
   scored FAIL or just an informational token.
2. **Scope of this change** — pick one:
   - (a) **Detector only**: fix classification + CB/OB columns (Type shows STEP, columns
     show CB + counterbore). Lowest risk.
   - (b) Detector **+ verification**: also verify the counterbore dimension and the
     step-depth rule, emitting tokens/score.
3. Geometry-vs-title precedence (§4) — geometry wins? 
4. Any STEP programs with **3+ steps** (multiple counterbores) we must handle, or always
   exactly one CB + one counterbore?
