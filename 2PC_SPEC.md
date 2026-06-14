# 2PC Parts — Spec & Classification (working draft)

Goal: a shared, written understanding of two-piece (2PC) parts so we can (1) classify
them correctly into separate filters and (2) search/match them reliably.

Status legend used below:
- ✅ **Confirmed** — stated directly by you.
- 🔎 **Inferred** — derived from the code/data, needs your sign-off.
- ❓ **Open question** — genuine ambiguity I could not resolve; please decide.

---

## 1. Physical model

A 2PC assembly is **two pieces** that mate. Each piece is one program (with OP1 front
and OP2 after the flip). The mating works by a **male hub** seating into a **female
recess**:

```
   HUB PIECE (male)                 RECESS PIECE (female)
   ┌───────────────┐                ┌───────────────┐
   │      ███       │  hub height    │     ▼▼▼       │  recess depth
   │   ███████      │  (IH)          │   ▼▼▼▼▼▼▼      │  (recess_z)
   └───────────────┘                └───────────────┘
        hub OD = HB        →  drops into  →   recess dia = RC
   pair rule (code today):  RC ≈ HB + 0.003"
```

🔎 Some pieces have **both** features (a hub on one operation *and* a recess on the
other), which lets parts stack — this is the 2PC HC case described in §3.

---

## 2. Measured features (what the verifier already detects)

| Token | Source (verifier.py) | Meaning | Detected range |
|-------|----------------------|---------|----------------|
| `RC:` | `_find_2pc_recess()` (T121/OP1) | recess **diameter** | depth gate `_RC_Z_MIN..MAX` = **0.27–0.56"** |
| `HB:` | `_find_2pc_hub_op2()` (T303/OP2) | hub **OD** | — |
| `IH:` | `_find_2pc_hub_op2()` (T303/OP2) | hub **height** | `_IH_Z_MIN..MAX` = **0.18–0.56"** |
| `length_in` | `parse_title_specs` | body/disc thickness from title | — |
| `hc_height_in` | `parse_title_specs` | HC hub height from title | ≈ 0.50" |

Also available: `recess_z_in` (the actual recess depth value, not just presence) and
`hub_is_variable` (the `?` flag on HB).

❓ **Gap:** today the scorer emits `RC:` but **not** the recess *depth* value, and the
classification can't see depth. Several rules below need the **recess depth** (0.30–0.33"
vs deeper) and the **hub height** (0.20–0.25" vs 0.50"). We likely need to surface
`recess_z_in` (and keep using `IH`) for classification. 🔎

---

## 3. Part-type taxonomy (the filters you want)

You asked to **separate** these (today "2PC HC" leaks into the "2PC" filter).
Final filter set (6) — `2PC HC` and `2PC STUD HC` are **the same thing**:

`2PC` · `2PC HC` (= STUD HC) · `2PC STUD` · `2PC LUG` · `2PC RING LUG`

> ❓ If you actually want `2PC HC` and `2PC STUD HC` as **two distinct** filters,
> tell me what separates them — otherwise they are merged as above.

### 3.1 `2PC STUD HC`  (= what we call "2PC HC" today)
✅ One operation has a **0.50" HC hub**; the other operation has a **0.30–0.33" recess**.
✅ A 2PC HC is *also* a stud → this is the same thing as `2PC STUD HC`.
🔎 Detection: `hc_height_in ≈ 0.50` (or `IH ≈ 0.50`) **AND** a recess with depth ≈ 0.30–0.33".

### 3.2 `2PC STUD`  (non-HC stud plate)
✅ Hub height **0.20–0.25"**.
✅ Body thickness typically **0.75"** or **0.55"** (the 0.55 is written in the title).
✅ CB is called out in the title; the hub is **not** in the title.
🔎 Detection: `IH ≈ 0.20–0.25` AND `length_in ≈ 0.55 or 0.75` AND no HC.

### 3.3 `2PC LUG`  (recess / female piece)
✅ **Any** thickness.
✅ Has a **0.30–0.33" depth step (recess)**.
✅ Does **not** contain HC.
🔎 Detection: recess present with depth ≈ 0.30–0.33" AND no HC AND `IH` not ≈ 0.50.

### 3.4 `2PC RING LUG`
✅ A 2PC with a **0.20–0.25" hub** that fits **into** 2PC HC parts.
✅ **Overlap with `2PC STUD` is intentional and resolved by body thickness:**
   - body **0.55"** + 0.20–0.25" hub → **`2PC STUD` only** (never ring lug).
   - body **0.75"** + 0.20–0.25" hub → **both** `2PC STUD` *and* `2PC RING LUG`
     (the file appears in both filters).
✅ Ring lug gets its **own filter** (see §3 list).
🔎 Because filters can overlap, part-type **filters are independent predicates**
   (a file may pass several). The single **"Type" column** label still uses the
   precedence in §4 to pick one primary label.

### 3.5 Plain `2PC`
🔎 Anything 2PC that is none of the above (e.g. a hub piece with a 0.20–0.25" hub that
isn't a stud, or a recess piece that isn't a lug). The `2PC` filter must **exclude**
everything classified as HC/STUD/LUG so the lists don't overlap.

---

## 4. Classification logic

✅ **Tokens win over title keywords.** Use measured RC/HB/IH/recess-depth as the
primary signal; the title `LUG`/`STUD`/`HC` keyword is only a fallback when tokens are
missing (e.g. the file was never verified).

### 4a. "Type" column — single primary label (precedence, top wins)
All require `2PC` in the title. Evaluated in order:

| # | Result | Rule 🔎 |
|---|--------|---------|
| 1 | `2PC HC` (= 2PC STUD HC) | HC hub ≈0.50" (`IH`≈0.50, or `hc_height_in`≈0.50 fallback) **and** recess depth 0.30–0.33" |
| 2 | `2PC LUG` | recess depth 0.30–0.33", **no** HC hub |
| 3 | `2PC STUD` | hub `IH`≈0.20–0.25" **and** body ≈0.55" or 0.75", no HC |
| 4 | `2PC RING LUG` | hub `IH`≈0.20–0.25", **not** a 0.55" stud (i.e. body 0.75"+ or no stud match) |
| 5 | `2PC` | none of the above |

### 4b. Filters — independent, may overlap (§3.4)
Each filter is its own predicate, so one file can pass several:
- `2PC STUD`: `IH`≈0.20–0.25" + body ≈0.55"/0.75", no HC.
- `2PC RING LUG`: `IH`≈0.20–0.25" + body ≈0.75"+ (excludes 0.55"), no HC.
- `2PC LUG`: recess 0.30–0.33", no HC.
- `2PC HC`: HC hub ≈0.50" + recess 0.30–0.33".
- `2PC` (plain): is 2PC **and not** any of the above.

❓ **Unverified files** (no RC/HB/IH tokens): fall back to title keyword
(`LUG`/`STUD`/`HC`). If even that is absent → plain `2PC`. Confirm acceptable, or
should the app offer to auto-verify on demand?

---

## 5. Search / find improvements (your asks)

1. **Fix filter overlap** — `2PC` filter must NOT include `2PC HC`/`2PC STUD HC`.
   (Today `_PART_TYPE_FILTERS["2PC"]` = `_is_2pc(t)` which catches HC too.)
2. **New filters** — `2PC STUD`, `2PC STUD HC`, `2PC LUG` (and ring lug per §3.4 ❓).
3. **Grid columns / sort for `RC` and `HB`** — show the recess & hub-OD token values
   as sortable/filterable columns in the main file grid.
4. **2PC Match dialog improvements** — ❓ which specifically? (e.g. add LUG/STUD/HC
   type filter to each side, show recess-depth & hub-height, flag ring-lug-fits-HC pairs).

---

## 6. Files this will touch (once rules are locked)

| File | Change |
|------|--------|
| `verifier.py` | surface `recess_z_in`; ensure IH/recess depth available for classify |
| `direct_scorer.py` | (maybe) emit recess depth token |
| `direct_models.py` | `_part_type()` precedence + new labels + colors; split `_PART_TYPE_FILTERS` |
| `ui/filter_bar.py` | add new part-type entries |
| `ui/main_window.py` | RC/HB grid columns + filter wiring; `_PART_TYPE_FILTERS` use |
| `ui/twopc_match.py` | type filters / display per §5.4 |

---

## 7. Open questions — remaining
Resolved: ring lug vs stud (§3.4, by body 0.55 vs 0.75), tokens-win (§4),
ring lug is its own 6th filter (§3).

Still open:
1. **`2PC HC` vs `2PC STUD HC`** — merged as one (§3). Split only if you give a rule.
2. **Unverified 2PC files** — title-keyword fallback OK, or auto-verify on demand? (§4b)
3. **2PC Match dialog** — exactly which improvements (§5.4)?
4. Confirm numeric gates: hub **0.20–0.25"**, HC hub **≈0.50"**, recess **0.30–0.33"**.
