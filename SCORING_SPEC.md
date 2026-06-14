# Scoring Revision — Plan

Two problems with today's scoring:
1. Every part is scored out of a **fixed /8**, and checks that **don't apply** show as
   `NF` (e.g. a Standard disc shows `OB:NF` even though it has no hub) — so those parts
   can never reach a full score.
2. **STEP** parts have no OB but do have a **counterbore** and a **step depth** that
   aren't checked at all.

Status: ✅ confirmed by you · 🔎 proposed · ❓ needs your input.

---

## 1. Applicability-aware scoring ✅

Introduce a third state per check: **N/A** (does not apply to this part type) —
distinct from **NF** (should be there but not found = a real miss).

- Verifier returns `None` today for both "not found" and "not applicable". Add an
  explicit **not-applicable** signal per check (per part type).
- Score becomes **passed / applicable** (dynamic denominator), e.g. a Standard disc
  with no hub is scored out of 7, not 8 — `OB` shows **N/A**, not `NF`.
- Display: `OB:N/A` (greyed, like NF but clearly "doesn't apply"), and it is **not**
  counted in the denominator.

🔎 Token: render applicable-but-missing as `:NF` (real miss, hurts nothing today but
flags incompleteness) and non-applicable as `:N/A` (excluded from score).

### Applicability matrix (which checks count per type)
✅ = scored · — = N/A · ? = needs your confirmation

| Type        | CB | OB | CBORE | SD | DR | RB | OD | TZ | PC | HM |
|-------------|----|----|-------|----|----|----|----|----|----|----|
| Standard    | ✅ | —  | —     | —  | ✅ | ✅ | ✅ | ?  | ✅ | ✅ |
| HC          | ✅ | ✅ | —     | —  | ✅ | ✅ | ✅ | ?  | ✅ | ✅ |
| STEP        | ✅ | —  | ✅    | ✅ | ✅ | ✅ | ✅ | ?  | ✅ | ✅ |
| 2PC (recess)| ✅ | —  | —     | —  | ?  | ?  | ✅ | ?  | ?  | ✅ |
| 2PC (hub)   | ✅ | ?  | —     | —  | ?  | ?  | ✅ | ?  | ?  | ✅ |
| SPACER      | ✅ | —  | —     | —  | ?  | ?  | ?  | ?  | ?  | ?  |
| STEEL ring  | ?  | ?  | —     | —  | ?  | ?  | ?  | ?  | ?  | ?  |

❓ Please fill the `?` cells (especially 2PC pieces, SPACER, STEEL) — I only have
firm rules for **Standard** (no OB) and **STEP** (no OB; add CBORE + SD).

---

## 2. STEP-specific checks ✅

### 2a. Counterbore (`CBORE`)
✅ Mirrors the **CB** rule: title counterbore + **0.1 mm** is the cut size.
- e.g. title counterbore 110 mm → code bore should be **110.1 mm** (X = 4.3346").
- Tolerance: same window as CB (`TOLERANCE_IN`, ±0.001" normal).
- Source: the counterbore X already found by `_find_step_bore` (`step_cb_mm`).
- Token: `CBORE:PASS/FAIL/NF`.

### 2b. Step depth (`SD` score)
✅ Verify the detected step depth against the **comment** that calls it out.
- Find a comment like `(0.40 deep step)` → nominal depth **0.40"**.
- Code Z is always nominal **+ 0.03"** → expected **0.43"** (z-0.43).
- PASS when detected depth ≈ comment_depth + 0.03" (small tol, e.g. ±0.005").
- If no depth comment is found → `NF` (can't verify) — ❓ or should missing comment
  be a FAIL (i.e. the comment is required)?
- Token: `SDOK:PASS/FAIL/NF` (separate from the existing informational `SD:<value>`).

🔎 Regex for the comment: `\(\s*([\d.]+)\s*deep\s*step\s*\)` (case-insensitive);
also accept `STEP <n> DEEP` variants — ❓ what phrasings appear in your files?

---

## 3. Sequenced roadmap (the areas you picked)

1. **Scoring framework** (§1) — applicability/N-A + dynamic denominator. *Underpins the
   rest; touches `verifier.py` (per-type applicability) + `direct_scorer.py` (denominator)
   + `direct_models.py` (score display `X/N`).*
2. **STEP checks** (§2) — CBORE + SD scoring, OB → N/A for steps.
3. **Full 2PC taxonomy** — classify + filter 2PC STUD / LUG / RING LUG / STUD HC, and
   stop 2PC HC leaking into 2PC (see `2PC_SPEC.md`).
4. **2PC Match upgrade** — per-side type filters, recess-depth/hub-height display,
   ring-lug-fits-HC pairing.

Dependencies: #1 → #2 (STEP swaps OB for CBORE/SD). #3 → #4 (Match uses the taxonomy).

---

## 4. Decisions (locked)
- ✅ **Dynamic denominator**: score = passed / applicable → displayed `X/N`.
- ✅ **Depth comment optional**: missing `(... deep step)` comment → `SDOK:NF` (no penalty).
- ✅ **Build order**: scoring framework + STEP checks first.
- 🔎 **Non-breaking rollout**: only types with defined N/A rules change now —
  **Standard** (OB → N/A) and **STEP** (OB → N/A, add CBORE + SDOK). All other types
  keep their current applicable set until their matrix row is confirmed, so no regression.

## 4b. Integration finding (read before building)
The verifier **already** has partial STEP logic that must be reconciled (not duplicated):
- `step_expected_in = _to_in(specs["step_mm"] + 0.1)` (verifier.py ~1875) — the +0.1mm rule
  for the counterbore already exists.
- `result["step_ok"]` (~2277–2280) compares an older `cb2_found_in` to `step_expected_in`,
  but `step_ok` is **not** in `_SCORE_KEYS`, so it's computed and ignored.
- My new `_find_step_bore` produces `step_cb_mm` / `step_depth_in` via a separate path.

Plan: drive the CBORE check from the title counterbore (`step_mm`/`ob_mm`) + 0.1mm vs the
detected counterbore, fold the existing `step_ok` into the scored **CBORE** key, and add
the new **SDOK** depth key. Avoid two competing step-counterbore results.

### Exact change set (build #1)
1. `verifier.py`: add `_step_depth_comment(lines)`; compute `result["cbore_ok"]`
   (title counterbore +0.1mm vs detected, ±TOLERANCE_IN) and `result["sd_score_ok"]`
   (comment depth +0.03" vs detected, ±0.005"); keep existing `step_ok` as the fallback
   source for CBORE.
2. `direct_scorer.py`: per-part **applicable** key set — drop `ob_ok` for STD & STEP,
   add `cbore_ok`+`sd_score_ok` for STEP; omit non-applicable tokens entirely (no
   `OB:N/A` clutter); add CBORE/SDOK labels.
3. `direct_models.py`: score display `X/N` (N = scored tokens in verify_status) and
   color by **ratio** instead of `/8`.
   - STD applicability uses keyword exclusion (not hub/2pc/step/steel/spacer/lug/stud) so
     SPACER/LUG/STUD scoring is unchanged this pass.

## 5. Still open (not blocking the first build)
1. Fill the applicability matrix `?` cells for 2PC / SPACER / STEEL (§1).
2. Depth-comment phrasings beyond `(0.40 deep step)` (§2b).
3. Grid score-color thresholds currently key off `/8`; with `X/N` they should key off the
   **ratio** (e.g. ≥0.95 green). Will adjust when wiring the display.
