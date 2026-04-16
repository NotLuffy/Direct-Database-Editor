"""
CNC Direct Editor — Order Sheet Search parser and scorer.

parse_order_row(text)          — parse a tab-separated I–M row from the order sheet
score_title_match(params, title) — score a program title against parsed order params
"""

import re
import verifier as _vfy

# ---------------------------------------------------------------------------
# Tolerances for order-sheet search (wider than machining verifier tolerances)
# ---------------------------------------------------------------------------
_TOL_ROUND_IN  = 0.01    # round size: exact within 0.01"
_TOL_CB_MM     = 1.5     # CB: ±1.5mm
_TOL_OB_MM     = 2.0     # OB: ±2.0mm
_TOL_DISC_IN   = 0.06    # disc thickness: ±0.06" (~1.5mm)
_TOL_HC_IN     = 0.10    # HC height: ±0.10" (order sheets often round)

# Minimum score to include a result (0–100)
MIN_SCORE = 20

# ---------------------------------------------------------------------------
# Column M thickness parser
# ---------------------------------------------------------------------------

def _parse_thickness(raw: str) -> dict | None:
    """
    Parse column M thickness cell. Returns dict with keys:
        disc_in   — disc (rotor) thickness in inches
        hc_in     — hub-centric height in inches, or None
        is_2pc    — True if this is a two-piece part
    Returns None on parse failure.
    """
    s = raw.strip()
    if not s:
        return None

    _MM_TO_IN = 1.0 / 25.4

    def _f(v: str) -> float:
        """Convert a value like '1.75', '.50', '7/8', '1 7/8' to float (strip quotes)."""
        v = v.strip().strip('"')
        # Mixed number: "1 7/8"
        mixed = re.match(r'^(\d+)\s+(\d+)/(\d+)$', v)
        if mixed:
            return float(mixed.group(1)) + float(mixed.group(2)) / float(mixed.group(3))
        # Pure fraction: "7/8"
        frac = re.match(r'^(\d+)/(\d+)$', v)
        if frac:
            return float(frac.group(1)) / float(frac.group(2))
        return float(v)

    def _inch(v: str, is_mm: bool) -> float:
        """Parse a numeric string and convert from mm if flagged."""
        val = _f(v)
        return round(val * _MM_TO_IN, 4) if is_mm else val

    # Numeric token: decimal, fraction (7/8), or mixed (1 7/8)
    _NUM = r'(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)'

    # ── MM-only value: "19MM", "19.5MM" — bare mm thickness, no hub ──────────
    m = re.match(r'^(\d+\.?\d*)\s*MM$', s, re.IGNORECASE)
    if m:
        return {"disc_in": round(float(m.group(1)) * _MM_TO_IN, 4),
                "hc_in": None, "is_2pc": False}

    # ── MM+HC: "19MM+0.50"HUB" or "19MM+12MMHUB" ─────────────────────────────
    m = re.match(
        r'^(\d+\.?\d*)\s*MM\s*\+\s*(\d*\.?\d+)\s*(MM)?"?\s*HUB',
        s, re.IGNORECASE)
    if m:
        disc  = round(float(m.group(1)) * _MM_TO_IN, 4)
        hc_is_mm = bool(m.group(3))
        hc    = _inch(m.group(2), hc_is_mm)
        return {"disc_in": disc, "hc_in": hc, "is_2pc": False}

    # Pattern A: "1.75"+.50"HUB", "7/8"+.50"HUB" — HC disc with HUB keyword
    m = re.match(
        rf'^({_NUM})"?\s*\+\s*({_NUM})"?\s*HUB',
        s, re.IGNORECASE)
    if m:
        return {"disc_in": _f(m.group(1)), "hc_in": _f(m.group(2)), "is_2pc": False}

    # Pattern B: "1.50"+0.50"(B+A)", "(B+C)", "(A+B)" etc — 2PC with HC, any letter pair
    m = re.match(
        rf'^({_NUM})"?\s*\+\s*({_NUM})"?\s*\([A-Z]\+[A-Z]\)',
        s, re.IGNORECASE)
    if m:
        disc = _f(m.group(1)); hc = _f(m.group(2))
        return {"disc_in": disc, "hc_in": hc, "is_2pc": True,
                "piece_a_in": disc, "piece_b_in": hc}

    # Pattern C: "1.25" (20mm+20mm)" — 2PC no HC (individual mm thicknesses)
    m = re.match(
        rf'^({_NUM})"?\s*\(([\d.]+)\s*mm\s*\+\s*([\d.]+)\s*mm\s*\)',
        s, re.IGNORECASE)
    if m:
        piece_a = round(float(m.group(2)) * _MM_TO_IN, 4)
        piece_b = round(float(m.group(3)) * _MM_TO_IN, 4)
        return {"disc_in": _f(m.group(1)), "hc_in": None, "is_2pc": True,
                "piece_a_in": piece_a, "piece_b_in": piece_b}

    # Pattern D: "1.25"+0.50"", "7/8"+.50"" — HC disc, no HUB, no bracket
    m = re.match(
        rf'^({_NUM})"?\s*\+\s*({_NUM})"?$',
        s, re.IGNORECASE)
    if m:
        return {"disc_in": _f(m.group(1)), "hc_in": _f(m.group(2)), "is_2pc": False}

    # Pattern E: plain value "1.00"", "7/8"", "1 7/8""
    m = re.match(rf'^({_NUM})"?$', s)
    if m:
        return {"disc_in": _f(m.group(1)), "hc_in": None, "is_2pc": False}

    return None


# ---------------------------------------------------------------------------
# Column K CB parser
# ---------------------------------------------------------------------------

def _parse_cb(raw: str) -> dict | None:
    """
    Parse column K center-bore cell. Returns dict with keys:
        cb_mm       — primary (outer) CB in mm
        is_step     — True if two CB values (step part)
        step_cb_mm  — inner CB in mm for STEP parts, else None
    Returns None on parse failure.
    """
    s = raw.strip()
    if not s:
        return None

    # Extract just the leading numeric portion from each part
    # e.g. "74 (.40 DEEP STEP)" → 74.0, "110" → 110.0
    def _leading_float(text: str) -> float:
        m = re.match(r'[\s]*(\d+\.?\d*)', text)
        if not m:
            raise ValueError(f"No number in: {text!r}")
        return float(m.group(1))

    parts = [p.strip() for p in s.split("/")]
    try:
        cb_mm = _leading_float(parts[0])
        if len(parts) >= 2:
            step_cb = _leading_float(parts[1])
            return {"cb_mm": cb_mm, "is_step": True, "step_cb_mm": step_cb}
        return {"cb_mm": cb_mm, "is_step": False, "step_cb_mm": None}
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Main row parser
# ---------------------------------------------------------------------------

def parse_order_row(text: str) -> dict | None:
    """
    Parse a tab-separated row from order sheet columns I–M.

    Column I: round size (e.g. "7", "9.5")
    Column J: bolt pattern (e.g. "5550-5450-A", "8170-8200-DH") — H suffix = hub hint
    Column K: CB in mm (e.g. "87.1", "125/115" for STEP)
    Column L: OB/hub diameter in mm, or empty
    Column M: thickness (e.g. "1.00"", "1.75"+.50"HUB")

    Returns dict or None on failure.
    """
    try:
        cols = text.strip().split("\t")
        # Also try comma-separated if no tabs found (paste from some apps)
        if len(cols) < 5:
            cols = text.strip().split(",")
        if len(cols) < 5:
            return None

        col_i = cols[0].strip()
        col_j = cols[1].strip()
        col_k = cols[2].strip()
        col_l = cols[3].strip()
        col_m = cols[4].strip()

        # I — round size
        round_in = float(col_i)

        # J — bolt pattern: H suffix = hub hint, SR = steel ring, 2PC anywhere = two-piece
        bolt_parts = col_j.split("-")
        bolt_has_hub_hint = bool(bolt_parts) and bolt_parts[-1].upper().endswith("H")
        is_steel_ring = bool(re.search(r'\bSR\b', col_j, re.IGNORECASE))
        col_j_is_2pc  = bool(re.search(r'2\s*PC', col_j, re.IGNORECASE))

        # K — CB
        cb_data = _parse_cb(col_k)
        if cb_data is None:
            return None

        # L — OB (optional)
        ob_mm = float(col_l) if col_l else None

        # M — thickness
        th_data = _parse_thickness(col_m)
        if th_data is None:
            return None

        # If column J says 2PC, override whatever column M parsed
        if col_j_is_2pc:
            th_data["is_2pc"] = True

        # Steel rings: ignore hub height and OB — search on round, CB, disc only
        if is_steel_ring:
            ob_mm            = None
            th_data["hc_in"] = None

        # 2PC pairing:
        #   HC 2PC  → col K = A piece CB, col L = B piece hub bore CB, hc_in = hub height
        #   Std 2PC → col K may have step (A/B CBs via /), no HC, no OB needed
        is_hc_2pc = th_data["is_2pc"] and th_data.get("hc_in") is not None
        if th_data["is_2pc"] and not is_hc_2pc:
            ob_mm            = None
            th_data["hc_in"] = None

        # For HC 2PC: ob_mm (col L) is the hub bore CB of the B piece — keep it
        hub_cb_mm = ob_mm if is_hc_2pc else None
        if is_hc_2pc:
            ob_mm = None   # not an OB for scoring purposes

        return {
            "round_in":          round_in,
            "bolt_has_hub_hint": bolt_has_hub_hint,
            "is_steel_ring":     is_steel_ring,
            "cb_mm":             cb_data["cb_mm"],
            "is_step":           cb_data["is_step"],
            "step_cb_mm":        cb_data["step_cb_mm"],
            "ob_mm":             ob_mm,
            "disc_in":           th_data["disc_in"],
            "hc_in":             th_data["hc_in"],
            "is_2pc":            th_data["is_2pc"],
            "is_hc_2pc":         is_hc_2pc,
            # HC 2PC: hub bore CB for the B (hub/hat) piece, from col L
            "hub_cb_mm":         hub_cb_mm,
            # Individual piece thicknesses for standard 2PC pairing
            "piece_a_in":        th_data.get("piece_a_in"),
            "piece_b_in":        th_data.get("piece_b_in"),
        }
    except (ValueError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_title_match(params: dict, title: str) -> tuple[int, list[str]]:
    """
    Score a program title against parsed order params.

    Returns (score_0_to_100, matched_fields_list).
    matched_fields lists what passed tolerance (e.g. ["Round 9.5 ✓", "CB 125.0 ✓"]).
    score < MIN_SCORE → treat as no match.
    """
    if not title:
        return 0, []

    specs = _vfy.parse_title_specs(title)
    if specs is None:
        return 0, []

    raw       = 0
    matched   = []
    missed    = []

    # ── Round size (30 pts) ──────────────────────────────────────────────────
    t_round = specs.get("round_size_in")
    if t_round is not None and abs(t_round - params["round_in"]) <= _TOL_ROUND_IN:
        raw += 30
        matched.append(f'Round {params["round_in"]}" ✓')
    else:
        missed.append(f'Round {params["round_in"]}" ✗'
                      + (f' (title: {t_round}")' if t_round else ''))
        # Round size is so discriminating that if it doesn't match at all,
        # cap the total score to avoid noise in results
        return 0, []

    # ── CB (25 pts) ──────────────────────────────────────────────────────────
    t_cb = specs.get("cb_mm")
    p_cb = params["cb_mm"]
    if t_cb is not None and abs(t_cb - p_cb) <= _TOL_CB_MM:
        raw += 25
        matched.append(f"CB {p_cb:.1f}mm ✓")
    else:
        missed.append(f"CB {p_cb:.1f}mm ✗" + (f" (title: {t_cb:.1f}mm)" if t_cb else ""))

    # ── Disc thickness (15 pts) ──────────────────────────────────────────────
    t_len = specs.get("length_in")
    p_len = params["disc_in"]
    if t_len is not None and abs(t_len - p_len) <= _TOL_DISC_IN:
        raw += 15
        matched.append(f'Disc {p_len}" ✓')
    else:
        missed.append(f'Disc {p_len}" ✗' + (f' (title: {t_len:.3f}")' if t_len else ""))

    # ── Part type (15 pts) ───────────────────────────────────────────────────
    p_is_step       = params["is_step"]
    p_is_2pc        = params["is_2pc"]
    p_is_steel_ring = params.get("is_steel_ring", False)
    p_has_hc        = params["hc_in"] is not None

    _STEEL_RE = re.compile(
        r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-?\d*\b|\bSTEEL\s+S-\d+\b', re.IGNORECASE)
    t_is_step       = bool(re.search(r'\bSTEP\b', title, re.IGNORECASE))
    t_is_2pc        = bool(re.search(r'-*2\s*PC\b', title, re.IGNORECASE))
    t_is_steel_ring = bool(_STEEL_RE.search(title))
    t_has_hc        = specs.get("hc_height_in") is not None

    type_score = 0
    type_label = ""

    if p_is_steel_ring and t_is_steel_ring:
        type_score = 15; type_label = "Steel Ring ✓"
    elif p_is_steel_ring and not t_is_steel_ring:
        missed.append("Steel Ring ✗")
    elif not p_is_steel_ring and t_is_steel_ring:
        # Order is not SR but title is — penalize hard
        missed.append("Type ✗ (title is Steel Ring)")
    elif p_is_step and t_is_step:
        type_score = 15; type_label = "STEP ✓"
    elif p_is_2pc and t_is_2pc:
        type_score = 15; type_label = "2PC ✓"
    elif p_has_hc and t_has_hc and not p_is_2pc and not t_is_2pc:
        type_score = 15; type_label = "HC ✓"
    elif not p_has_hc and not p_is_2pc and not p_is_step and \
         not t_has_hc and not t_is_2pc and not t_is_step:
        type_score = 15; type_label = "STD ✓"
    else:
        # Partial: at least both have or both lack a hub
        if p_has_hc == t_has_hc:
            type_score = 7; type_label = "Type ~"
        else:
            missed.append("Type ✗")

    raw += type_score
    if type_label:
        matched.append(type_label)

    # ── OB (10 pts, only when order specifies OB) ────────────────────────────
    has_ob_field = params["ob_mm"] is not None
    max_ob = 10 if has_ob_field else 0

    if has_ob_field:
        t_ob = specs.get("ob_mm") or specs.get("step_mm")
        p_ob = params["ob_mm"]
        if t_ob is not None and abs(t_ob - p_ob) <= _TOL_OB_MM:
            raw += 10
            matched.append(f"OB {p_ob:.1f}mm ✓")
        else:
            missed.append(f"OB {p_ob:.1f}mm ✗" + (f" (title: {t_ob:.1f}mm)" if t_ob else ""))

    # ── HC height (5 pts, only when order specifies HC) ──────────────────────
    has_hc_field = params["hc_in"] is not None
    max_hc = 5 if has_hc_field else 0

    if has_hc_field:
        t_hc = specs.get("hc_height_in")
        p_hc = params["hc_in"]
        if t_hc is not None and abs(t_hc - p_hc) <= _TOL_HC_IN:
            raw += 5
            matched.append(f'HC {p_hc:.3f}" ✓')
        else:
            missed.append(f'HC {p_hc:.3f}" ✗' + (f' (title: {t_hc:.3f}")' if t_hc else ""))

    # ── Normalize to 0–100 ───────────────────────────────────────────────────
    max_possible = 30 + 25 + 15 + 15 + max_ob + max_hc   # 85–100 depending on part type
    score = round(raw * 100 / max_possible) if max_possible else 0

    # Append missed fields for tooltip context
    all_fields = matched + missed
    return score, all_fields


# ---------------------------------------------------------------------------
# 2PC pair scoring
# ---------------------------------------------------------------------------

def _score_2pc_piece(round_in: float, cb_mm: float, thickness_in: float | None,
                     title: str, require_hc: bool = False,
                     forbid_hc: bool = False) -> tuple[int, list[str]]:
    """
    Score a single title as one half of a 2PC pair.

    require_hc  — title must have HC (B/hub piece in HC 2PC)
    forbid_hc   — title must NOT have HC (A/ring piece in HC 2PC)
    thickness_in — None to skip thickness check (used when per-piece thickness unknown)
    """
    if not title:
        return 0, []
    specs = _vfy.parse_title_specs(title)
    if specs is None:
        return 0, []

    # Must be a 2PC title
    if not re.search(r'-*2\s*PC\b', title, re.IGNORECASE):
        return 0, []

    # Check HC presence two ways: parsed specs AND raw title keyword
    # Raw check catches HC patterns that parse_title_specs might not fully parse
    _HC_RAW = re.compile(
        r'\bHC\b|\bHCX+\b|\b\d+\s*MM\s*HC\b|\bHC\s*[\d.]+',
        re.IGNORECASE)
    t_has_hc = (specs.get("hc_height_in") is not None
                or bool(_HC_RAW.search(title)))

    if require_hc and not t_has_hc:
        return 0, []
    if forbid_hc and t_has_hc:
        return 0, []

    raw = 0; matched = []; missed = []

    # Round size (40 pts)
    t_round = specs.get("round_size_in")
    if t_round is not None and abs(t_round - round_in) <= _TOL_ROUND_IN:
        raw += 40; matched.append(f'Round {round_in}" ✓')
    else:
        return 0, []   # wrong round size — not a candidate

    # CB (35 pts) — hard gate for 2PC: if CB doesn't match at all, not a valid candidate.
    # Use tighter tolerance (1.0mm) than general search to avoid wrong-CB suggestions.
    _2PC_CB_TOL = 1.0   # ±1.0mm for 2PC CB matching (tighter than _TOL_CB_MM=1.5)
    t_cb = specs.get("cb_mm")
    if t_cb is not None and abs(t_cb - cb_mm) <= _2PC_CB_TOL:
        raw += 35; matched.append(f'CB {cb_mm:.1f}mm ✓')
    elif t_cb is not None and abs(t_cb - cb_mm) <= _TOL_CB_MM:
        # Within loose tolerance — score partial but do not hard-gate
        raw += 15; missed.append(f'CB {cb_mm:.1f}mm ~ (title: {t_cb:.1f}mm, off {abs(t_cb-cb_mm):.1f}mm)')
    else:
        # CB too far off — reject this candidate entirely
        return 0, []

    # Thickness (25 pts) — skip when thickness_in is None
    if thickness_in is not None:
        t_len = specs.get("length_in")
        if t_len is not None and abs(t_len - thickness_in) <= _TOL_DISC_IN:
            raw += 25; matched.append(f'Thick {thickness_in:.3f}" ✓')
        else:
            missed.append(f'Thick {thickness_in:.3f}" ✗' + (f' (title: {t_len:.3f}")' if t_len else ''))

    return min(raw, 100), matched + missed


def find_2pc_pairs(params: dict, db_path: str) -> list[tuple]:
    """
    For a 2PC order row, find pairs of files (ring + bell/hat) that fit together.

    Uses:
      - params["cb_mm"]      as the ring piece CB
      - params["step_cb_mm"] as the bell/hat CB (if present), else same as cb_mm
      - params["piece_a_in"] / params["piece_b_in"] for individual piece thicknesses
        (falls back to disc_in if not available)
      - params["round_in"]   for both pieces

    Returns list of (pair_score, ring_id, ring_o, ring_name, ring_title,
                                 hat_id,  hat_o,  hat_name,  hat_title,
                                 ring_fields, hat_fields)
    sorted by pair_score descending.
    """
    import direct_database as db_mod

    round_in   = params["round_in"]
    is_hc_2pc  = params.get("is_hc_2pc", False)

    if is_hc_2pc:
        # HC 2PC notation: "1.50"+.50"HUB (B+A)"
        #   B = hub piece (HC):    CB = col L (hub bore), disc = disc_in / 2, has HC
        #   A = flat ring piece:   CB = col K,            disc = disc_in / 2, no HC
        #   The total disc (1.50") is split equally; each piece machined to half.
        piece_disc   = params["disc_in"] / 2.0   # e.g. 1.50/2 = 0.75"
        cb_a         = params["cb_mm"]            # A piece CB (col K) e.g. 106.1
        cb_b         = params.get("hub_cb_mm")    # B piece hub bore (col L) e.g. 71.5
        thick_a      = piece_disc                 # e.g. 0.75"
        thick_b      = piece_disc                 # e.g. 0.75"
        require_hc_b = True    # B piece must have HC in title
        forbid_hc_a  = True    # A piece must NOT have HC in title
    else:
        # Standard 2PC: step CBs from col K (A/B), individual thicknesses from col M
        cb_a         = params["cb_mm"]
        cb_b         = params.get("step_cb_mm") or params["cb_mm"]
        thick_a      = params.get("piece_a_in") or params["disc_in"]
        thick_b      = params.get("piece_b_in") or params["disc_in"]
        require_hc_b = False
        forbid_hc_a  = False

    conn = db_mod.get_connection(db_path)
    rows = conn.execute(
        "SELECT id, o_number, file_name, program_title, verify_status "
        "FROM files "
        "WHERE program_title IS NOT NULL AND program_title != '' "
        "  AND (program_title LIKE '%2PC%' OR program_title LIKE '%2 PC%') "
        "ORDER BY o_number"
    ).fetchall()
    conn.close()

    # Deduplicate by o_number, keep first occurrence
    seen = set(); unique_rows = []
    for r in rows:
        key = (r["o_number"] or "").upper() or str(r["id"])
        if key not in seen:
            seen.add(key); unique_rows.append(r)

    def _file_has_hub(title: str, vstatus: str) -> bool:
        """
        Return True if the file has a hub bore — checked three ways:
        1. HC keyword in title
        2. parse_title_specs returns hc_height_in
        3. verify_status contains OB:PASS or OB:FAIL (OB:NF = no hub)
        """
        if re.search(r'\bHC\b|\bHCX+\b|\b\d+\s*MM\s*HC\b|\bHC\s*[\d.]',
                     title, re.IGNORECASE):
            return True
        specs = _vfy.parse_title_specs(title)
        if specs and specs.get("hc_height_in") is not None:
            return True
        if vstatus:
            toks = vstatus.upper().split()
            for tok in toks:
                if tok.startswith("OB:") and not tok.startswith("OB:NF"):
                    return True
        return False

    # Score every unique 2PC file as ring candidate and as hat candidate
    ring_candidates = []
    hat_candidates  = []

    for row in unique_rows:
        title   = row["program_title"] or ""
        vstatus = row["verify_status"]  or ""

        if not re.search(r'-*2\s*PC\b', title, re.IGNORECASE):
            continue

        has_hub = _file_has_hub(title, vstatus)

        # A piece / ring: for HC 2PC must NOT have hub
        if forbid_hc_a and has_hub:
            pass  # skip — file has a hub, can't be the ring piece
        else:
            s_ring, f_ring = _score_2pc_piece(
                round_in, cb_a, thick_a, title, forbid_hc=False)
            if s_ring >= MIN_SCORE:
                ring_candidates.append((s_ring, row["id"], row["o_number"] or "",
                                        row["file_name"] or "", title, f_ring))

        # B piece / hub: for HC 2PC must have hub
        if cb_b is not None:
            if require_hc_b and not has_hub:
                pass  # skip — file has no hub, can't be the hub piece
            else:
                s_hat, f_hat = _score_2pc_piece(
                    round_in, cb_b, thick_b, title, require_hc=False)
                if s_hat >= MIN_SCORE:
                    hat_candidates.append((s_hat, row["id"], row["o_number"] or "",
                                           row["file_name"] or "", title, f_hat))

    def _b_recess_mm(title: str) -> float | None:
        """
        B piece (hub): recess cut on first op (STEP-like bore).
        The A piece ring must fit INTO this diameter.
        Stored as step_mm (STEP inner bore) or ob_mm in parse_title_specs.
        """
        specs = _vfy.parse_title_specs(title)
        if not specs:
            return None
        v = specs.get("step_mm") or specs.get("ob_mm")
        return float(v) if v is not None else None

    def _a_ring_mm(title: str) -> float | None:
        """
        A piece (ring): the protruding ring OD that slides into B's recess.
        Stored as ob_mm in parse_title_specs.
        """
        specs = _vfy.parse_title_specs(title)
        if not specs:
            return None
        v = specs.get("ob_mm")
        return float(v) if v is not None else None

    # Ideal clearance: recess should be larger than ring by ~0.003" = ~0.076mm
    _FIT_CLEARANCE_MM = 0.076
    _FIT_TOL_MM       = 0.5    # allow up to 0.5mm over ideal clearance

    # Pair every ring candidate with every hat candidate (different files)
    pairs = []
    for ring in ring_candidates:
        for hat in hat_candidates:
            if ring[1] == hat[1]:   # same file — skip self-pairing
                continue

            # For HC 2PC: verify A piece ring fits into B piece recess
            if is_hc_2pc:
                ring_title = ring[4]   # A piece
                hat_title  = hat[4]    # B piece (has HC + recess)
                recess_d   = _b_recess_mm(hat_title)   # B piece recess diameter
                ring_d     = _a_ring_mm(ring_title)    # A piece ring OD

                fit_note = ""
                if recess_d is not None and ring_d is not None:
                    clearance = recess_d - ring_d
                    if clearance < -_FIT_TOL_MM:
                        # Ring OD is bigger than recess — won't fit, skip pair
                        continue
                    fit_ok = clearance >= _FIT_CLEARANCE_MM - _FIT_TOL_MM
                    status = "OK" if fit_ok else "TIGHT"
                    fit_note = (f"Fit: ring {ring_d:.2f}mm → recess {recess_d:.2f}mm "
                                f"({clearance:+.3f}mm) [{status}]")
                elif recess_d is not None:
                    fit_note = f"B recess {recess_d:.2f}mm (A ring OD not found in title)"
                elif ring_d is not None:
                    fit_note = f"A ring OD {ring_d:.2f}mm (B recess not found in title)"

                ring_fields = list(ring[5]) + ([fit_note] if fit_note else [])
                hat_fields  = list(hat[5])
            else:
                ring_fields = ring[5]
                hat_fields  = hat[5]

            pair_score = min(ring[0], hat[0])
            pairs.append((pair_score,
                          ring[1], ring[2], ring[3], ring[4],
                          hat[1],  hat[2],  hat[3],  hat[4],
                          ring_fields, hat_fields))

    # Deduplicate: keep best pair for each (ring_id, hat_id) combination
    best_pairs: dict[tuple, tuple] = {}
    for p in pairs:
        key = (min(p[1], p[5]), max(p[1], p[5]))
        if key not in best_pairs or p[0] > best_pairs[key][0]:
            best_pairs[key] = p

    return sorted(best_pairs.values(), key=lambda x: x[0], reverse=True)[:20]
