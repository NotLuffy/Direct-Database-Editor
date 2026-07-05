"""
CNC Direct Editor — Order Sheet Search parser and scorer.

parse_order_row(text)            — parse a tab-separated I–M row from the order sheet
score_title_match(params, title) — score a program title against parsed order params
describe_params(params)          — one-line human summary of what was parsed
find_2pc_pairs(params, db_path)  — ring/hat pair search for 2PC orders

Column semantics (order sheet I–M):
    I — round size in inches
    J — bolt pattern + thickness code:  <pattern>[-<pattern>]-<thick><H?>
        thickness letter A = 1.00", +0.25" per letter (B=1.25" … H=2.75", I=3.00",
        J=3.25"); a TRAILING H after another thickness token means "has hub"
        (CH = 1.50"+hub, 10H = 10mm+hub, 1/2H = 0.50"+hub).  Flags: SR = steel
        ring only, (2PC) = two-piece only, (1pc) = anything that is not 2PC.
    K — center bore in mm; "CB/counterbore (depth)" = STEP part (non-2PC rows),
        or the two piece CBs for 2PC rows
    L — OB / hub diameter in mm.  A value here (without SR) means HC part.
    M — thickness: disc + hub height ("1.50"+.50"HUB").  Authoritative for hub
        height, but prone to hand-typed errors — cross-checked against J.
"""

import re
import verifier as _vfy

_MM_TO_IN = 1.0 / 25.4

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

# Steel-ring title detector (shared by scorer and 2PC pairing)
_STEEL_RE = re.compile(
    r'\b(?:STEEL|STL)[\s._-]*RING\b|\bHCS-?\d*\b|\bSTL\b|\bSTEEL\s+S-\d+\b',
    re.IGNORECASE)

# Numeric token: decimal, fraction (7/8), or mixed (1 7/8)
_NUM = r'(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)'


def _to_float(v: str) -> float:
    """Convert '1.75', '.50', '7/8' or '1 7/8' to float (strips quote marks)."""
    v = v.strip().strip('"')
    mixed = re.match(r'^(\d+)\s+(\d+)/(\d+)$', v)
    if mixed:
        return float(mixed.group(1)) + float(mixed.group(2)) / float(mixed.group(3))
    frac = re.match(r'^(\d+)/(\d+)$', v)
    if frac:
        return float(frac.group(1)) / float(frac.group(2))
    return float(v)


def _inch(v: str, is_mm) -> float:
    """Parse a numeric string, converting from mm when is_mm is truthy."""
    val = _to_float(v)
    return round(val * _MM_TO_IN, 4) if is_mm else val


def _letter_in(ch: str) -> float:
    """Column-J thickness letter: A=1.00", +0.25" per letter (H=2.75", J=3.25")."""
    return 1.00 + 0.25 * (ord(ch.upper()) - ord('A'))


# ---------------------------------------------------------------------------
# Column M thickness parser
# ---------------------------------------------------------------------------

# Hand-typed noise that appears inside M and never carries spec data
_M_NOISE_RE = re.compile(
    r'\(\s*CR\s*\)'              # "(CR)" annotation
    r'|-\s*CR\b'                 # "-CR" suffix
    r'|\bTOP\s*PLATE\s*ONLY\b'
    r'|\bBOTTOM\b'
    r'|\bSLIP[-\s]?ON\b'
    r'|\(\s*CUT\s*-[^)]*\)',     # "(CUT - 1.50")" note — NOT the (CUT A+A) pair form
    re.IGNORECASE)

# 2PC piece pair: "(A+B)", "(CUT A+A)", "(A+20MM)", "(20MM+20MM)"
_PAIR_RE = re.compile(
    r'\(\s*(?:CUT\s*)?([A-J]|\d+\.?\d*\s*MM)\s*\+\s*([A-J]|\d+\.?\d*\s*MM)\s*\)',
    re.IGNORECASE)

# disc + hub height with HUB keyword: "1.50"+.50"HUB", "10MM+3/8"HUB", "1/2H+.50"HUB"
_M_HUB_RE = re.compile(
    rf'^({_NUM})\s*(MM)?\s*"?\s*H?\s*\+\s*({_NUM})\s*(MM)?\s*"?\s*HUB')
# disc + hub height, HUB keyword lost: "2.50" + 0.50"", "1.25"+.50"
_M_PLUS_RE = re.compile(
    rf'^({_NUM})\s*(MM)?\s*"?\s*\+\s*({_NUM})\s*(MM)?\s*"?$')
# plain thickness: "1.00"", "19MM", "1 7/8"", "1.50"
_M_PLAIN_RE = re.compile(rf'^({_NUM})\s*(MM)?\s*"?$')
# J-style mm+hub token typed into M: "10H"
_M_MMH_RE = re.compile(r'^(\d+\.?\d*)\s*(?:MM)?\s*H$')


def _normalize_m(raw: str) -> str:
    """Uppercase and repair the common hand-typed errors seen in column M."""
    s = raw.strip().upper()
    for q in '“”„':
        s = s.replace(q, '"')
    s = s.replace("’", '"').replace("'", '"')        # apostrophe used as inch mark
    s = re.sub(r'[{}:`]', '', s)                     # shift-slips: .50{"HUB  .50:HUB
    s = re.sub(r'\+\s*,\s*', '+.', s)                # "+,50"  → "+.50"
    s = re.sub(r'\+{2,}', '+', s)                    # "++.50" → "+.50"
    s = re.sub(r'\.{2,}', '.', s)                    # "+..50" → "+.50"
    s = re.sub(r'\+\s*\.\s+(?=\d)', '+.', s)         # "+. 50" → "+.50"
    s = re.sub(r'(\d)\s*M{1,3}\b"?', r'\1MM', s)     # 19M / 19MMM / 15MM" → 15MM
    s = re.sub(r'\bHUBB+\b', 'HUB', s)
    s = re.sub(r'\bSTEEL\s*RING\b', 'HUB', s)        # "+0.50" STEEL RING" = ring height
    s = _M_NOISE_RE.sub(' ', s)
    return ' '.join(s.split())


def _pair_piece_in(tok: str) -> float | None:
    """Finished piece thickness for one half of a (X+Y) pair.

    mm values are finished thickness; stock LETTERS are blank sizes (finished
    size is thinner by an unknown cut) → None so thickness isn't score-gated."""
    tok = tok.strip().upper()
    m = re.fullmatch(r'(\d+\.?\d*)\s*MM', tok)
    if m:
        return round(float(m.group(1)) * _MM_TO_IN, 4)
    return None


def _parse_thickness(raw: str) -> dict | None:
    """
    Parse column M thickness cell. Returns dict with keys:
        disc_in     — disc thickness in inches (total for 2PC)
        hc_in       — hub-centric height in inches, or None
        is_2pc      — True if a (X+Y) piece pair is present
        piece_a_in / piece_b_in — individual 2PC piece thicknesses when known
        assumed_hub — True when a hub is implied but its height was guessed
    Returns None on parse failure.
    """
    s = _normalize_m(raw)
    if not s or s == '?':
        return None

    pair = _PAIR_RE.search(s)
    s_main = _PAIR_RE.sub(' ', s, count=1) if pair else s
    # any parenthetical left after pair extraction is an aside, e.g. "(30.48MM)"
    s_main = ' '.join(re.sub(r'\([^)]*\)', ' ', s_main).split())
    if not s_main:
        return None

    disc = hc = None
    assumed_hub = False

    m = _M_MMH_RE.match(s_main)                      # "10H" — mm disc, hub implied
    if m:
        disc = round(float(m.group(1)) * _MM_TO_IN, 4)
        hc   = 0.50
        assumed_hub = True
    if disc is None:
        m = _M_HUB_RE.match(s_main)
        if m:
            disc = _inch(m.group(1), m.group(2))
            hc   = _inch(m.group(3), m.group(4))
    if disc is None:
        m = _M_PLUS_RE.match(s_main)
        if m:
            disc = _inch(m.group(1), m.group(2))
            hc   = _inch(m.group(3), m.group(4))
    if disc is None:
        m = _M_PLAIN_RE.match(s_main)
        if m:
            disc = _inch(m.group(1), m.group(2))
    if disc is None:
        return None

    piece_a = piece_b = None
    if pair:
        piece_a = _pair_piece_in(pair.group(1))
        piece_b = _pair_piece_in(pair.group(2))
        # letter + mm mix: the letter piece finishes at total − mm piece
        if piece_a is None and piece_b is not None and disc - piece_b > 0.1:
            piece_a = round(disc - piece_b, 4)
        elif piece_b is None and piece_a is not None and disc - piece_a > 0.1:
            piece_b = round(disc - piece_a, 4)

    return {"disc_in": disc, "hc_in": hc, "is_2pc": bool(pair),
            "piece_a_in": piece_a, "piece_b_in": piece_b,
            "assumed_hub": assumed_hub}


# ---------------------------------------------------------------------------
# Column K CB parser
# ---------------------------------------------------------------------------

def _leading_float(text: str) -> float | None:
    m = re.match(r'\s*(\d+\.?\d*)', text)
    return float(m.group(1)) if m else None


def _parse_cb(raw: str) -> dict | None:
    """
    Parse column K center-bore cell. Returns dict with keys:
        cb_mm         — primary (outer) CB in mm
        is_step       — True if two bore values (STEP part / 2PC piece CBs)
        step_cb_mm    — inner counterbore in mm, else None
        step_depth_in — step depth in inches when noted, e.g. "110/74 (.40 STEP)"
    Returns None on parse failure.
    """
    s = raw.strip().upper()
    if not s or s == '?':
        return None

    parts = s.split("/", 1)
    cb_mm = _leading_float(parts[0])
    if cb_mm is None:
        return None

    if len(parts) == 2:
        second = _leading_float(parts[1])
        # Both sides must look like bore diameters (mm) — guards against a
        # fraction thickness typed into K such as '3 1/16"'
        if second is not None and cb_mm >= 20 and second >= 20:
            tail = parts[1][re.match(r'\s*\d+\.?\d*', parts[1]).end():]
            depth = None
            mnum = re.search(r'(\d*\.?\d+)', tail)
            if mnum:
                depth = float(mnum.group(1))
                if depth >= 3:          # "75 STEP" means .75" — decimal dropped
                    depth /= 100.0
            return {"cb_mm": cb_mm, "is_step": True,
                    "step_cb_mm": second, "step_depth_in": depth}

    return {"cb_mm": cb_mm, "is_step": False,
            "step_cb_mm": None, "step_depth_in": None}


# ---------------------------------------------------------------------------
# Column J parser
# ---------------------------------------------------------------------------

_J_FRAC_DENOMS = {2, 3, 4, 8, 16}   # sane thickness fractions (rejects "131/108")


def _parse_j(col_j: str) -> dict:
    """
    Parse column J: flags (SR / 2PC / 1PC) and thickness tokens.

    Token forms:  letter code C / CH / E9 (A=1.00" +0.25"/letter, trailing H=hub),
    mm+hub 10H / 10.6H, fraction 1/2H / 3/4", explicit inches 1.50", mm 19MM, HC.
    Bare bolt-pattern numbers (5450, 84.1, 131/108, -2 suffix) are ignored.

    When TWO thickness values appear ("AH-1.50""), the first is the disc and the
    trailing one is the hub height (matches M "1.00"+1.50"HUB").  A bare H after
    another thickness token is a hub marker ("1/2-H"); bare H alone is 2.75".
    """
    up = col_j.upper()
    is_sr  = bool(re.search(r'\bSR\b', up))
    is_2pc = bool(re.search(r'2\s*PC', up))
    is_1pc = bool(re.search(r'1\s*PC', up))

    core = re.sub(r'\([^)]*\)', ' ', up)      # drop (SPACERS)/(2PC)/(1pc)
    core = re.sub(r'\bSR\b', ' ', core)

    vals: list[float] = []
    has_hub = False
    tokens = [t for t in re.split(r'[\s-]+', core) if t]
    for tok in tokens[1:]:                    # tokens[0] is always the bolt pattern
        val = None
        hub = None
        if tok == 'H' and vals:                           # detached hub marker: "1/2-H"
            has_hub = True
            continue
        m = re.fullmatch(r'([A-K])\d?(H)?', tok)
        if m:                                             # C / CH / E9 / bare H (=2.75")
            val = _letter_in(m.group(1))
            hub = bool(m.group(2))
        elif re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:MM)?H', tok):   # 10H / 10.6H — mm + hub
            mmv = float(re.match(r'\d+(?:\.\d+)?', tok).group(0))
            if mmv <= 40:                     # 96H etc. is a bore, not a thickness
                val = round(mmv * _MM_TO_IN, 4)
                hub = True
        elif re.fullmatch(r'(\d+(?:\.\d+)?)MM', tok):            # 19MM
            val = round(float(tok[:-2]) * _MM_TO_IN, 4)
            hub = False
        elif tok == 'HC':
            has_hub = True
        else:
            m = re.fullmatch(r'((?:\d+\s+)?\d+/\d+)"?(H)?', tok)  # 1/2H / 3/4"
            if m:
                num, den = m.group(1).rsplit('/', 1)
                if int(den) in _J_FRAC_DENOMS:
                    val = _to_float(m.group(1))
                    hub = bool(m.group(2))
            else:
                m = re.fullmatch(r'(\d*\.\d+|\d+)"', tok)         # 1.50" (quote required)
                if m:
                    val = float(m.group(1))
                    hub = False
        if val is not None:
            vals.append(val)
            if hub:
                has_hub = True

    disc_in = vals[0] if vals else None
    hub_in  = vals[-1] if len(vals) >= 2 else None   # "AH-1.50"" → hub height 1.50"

    return {"is_sr": is_sr, "is_2pc": is_2pc, "is_1pc": is_1pc,
            "disc_in": disc_in, "hub_in": hub_in, "has_hub": has_hub}


# ---------------------------------------------------------------------------
# Main row parser
# ---------------------------------------------------------------------------

def parse_order_row(text: str) -> dict | None:
    """
    Parse a tab-separated row from order sheet columns I–M.
    Returns dict (see keys below) or None on failure.

    Robust to hand-typed errors: M typos are normalized, and when M is
    unreadable the thickness falls back to the column-J code; when J and M
    disagree both values are accepted for matching.  Any repair is recorded
    in the returned "warnings" list.
    """
    try:
        cols = text.strip().split("\t")
        if len(cols) < 4:
            cols = text.strip().split(",")
        if len(cols) == 4:
            cols = cols[:3] + [""] + cols[3:]    # L omitted (flat part)
        if len(cols) < 5:
            return None

        col_i, col_j, col_k, col_l, col_m = (c.strip() for c in cols[:5])

        m = re.match(r'(\d+(?:\.\d+)?)', col_i)
        if not m:
            return None
        round_in = float(m.group(1))

        j = _parse_j(col_j)
        cb_data = _parse_cb(col_k)
        if cb_data is None:
            return None
        m = re.match(r'(\d+(?:\.\d+)?)', col_l)
        ob_mm = float(m.group(1)) if m else None

        warnings: list[str] = []
        alt_disc_in = None
        th = _parse_thickness(col_m)
        if th is None:
            if j["disc_in"] is None:
                return None
            hub = j["has_hub"] or ob_mm is not None
            hc  = (j["hub_in"] or 0.50) if hub else None
            th = {"disc_in": j["disc_in"], "hc_in": hc,
                  "is_2pc": False, "piece_a_in": None, "piece_b_in": None}
            warnings.append(
                f'Thickness "{col_m}" unreadable — using {j["disc_in"]:g}" from '
                f'bolt-pattern code' + (f' + {hc:g}" hub' if hc else ''))
        else:
            if th.pop("assumed_hub", False):
                if j["hub_in"] is not None:
                    th["hc_in"] = j["hub_in"]
                else:
                    warnings.append('Hub height not given in thickness — assumed 0.50"')
            if j["disc_in"] is not None and abs(j["disc_in"] - th["disc_in"]) > 0.07:
                alt_disc_in = j["disc_in"]
                warnings.append(
                    f'J says {j["disc_in"]:g}" but M says {th["disc_in"]:g}" '
                    f'— using M, matching either (check for typo)')
            if (j["hub_in"] is not None and th.get("hc_in") is not None
                    and abs(j["hub_in"] - th["hc_in"]) > 0.07):
                warnings.append(
                    f'J hub {j["hub_in"]:g}" ≠ M hub {th["hc_in"]:g}" '
                    f'— using M (check for typo)')

        is_2pc = bool(th.get("is_2pc")) or j["is_2pc"]
        if j["is_1pc"] and is_2pc:
            is_2pc = False
            warnings.append('J says 1PC but thickness looks 2PC — treating as 1PC')
        is_steel_ring = j["is_sr"]

        # Steel rings: ignore hub height and OB — search on round, CB, disc only
        if is_steel_ring:
            ob_mm            = None
            th["hc_in"]      = None

        # 2PC pairing:
        #   HC 2PC  → col K = A piece CB, col L = B piece hub bore CB, hc_in = hub height
        #   Std 2PC → col K may carry both piece CBs via /, no HC, no OB needed
        is_hc_2pc = is_2pc and th.get("hc_in") is not None
        if is_2pc and not is_hc_2pc:
            ob_mm            = None
            th["hc_in"]      = None
        hub_cb_mm = ob_mm if is_hc_2pc else None
        if is_hc_2pc:
            ob_mm = None   # not an OB for scoring purposes

        # Part type — drives the hard search filters:
        #   SR  → steel-ring files only
        #   2PC → 2PC files only (ring/hat pairing)
        #   HC  → hub files only (OB in L, hub height from M)
        #   STD → flat parts: never 2PC, never HC (explicit "1pc" lands here too)
        if is_steel_ring:
            part_type = "SR"
        elif is_2pc:
            part_type = "2PC"
        elif ob_mm is not None or th.get("hc_in") is not None:
            part_type = "HC"
            if th.get("hc_in") is None:
                warnings.append('OB given but no hub height in M — hub height unchecked')
            elif ob_mm is None:
                warnings.append('Hub in M but no OB value in column L')
        else:
            part_type = "STD"
            if j["has_hub"]:
                warnings.append('J suggests a hub but L and M do not — treating as flat')

        return {
            "round_in":          round_in,
            "part_type":         part_type,
            "bolt_has_hub_hint": j["has_hub"],
            "is_steel_ring":     is_steel_ring,
            "is_1pc":            j["is_1pc"],
            "cb_mm":             cb_data["cb_mm"],
            "is_step":           cb_data["is_step"],
            "step_cb_mm":        cb_data["step_cb_mm"],
            "step_depth_in":     cb_data["step_depth_in"],
            "ob_mm":             ob_mm,
            "disc_in":           th["disc_in"],
            "alt_disc_in":       alt_disc_in,
            "hc_in":             th["hc_in"],
            "is_2pc":            is_2pc,
            "is_hc_2pc":         is_hc_2pc,
            # HC 2PC: hub bore CB for the B (hub/hat) piece, from col L
            "hub_cb_mm":         hub_cb_mm,
            # Individual piece thicknesses for standard 2PC pairing
            "piece_a_in":        th.get("piece_a_in"),
            "piece_b_in":        th.get("piece_b_in"),
            "warnings":          warnings,
        }
    except (ValueError, IndexError, TypeError, AttributeError):
        return None


def describe_params(p: dict) -> str:
    """One-line human summary of a parsed order (shown above its results)."""
    bits = [p["part_type"], f'{p["round_in"]:g}" rnd', f'CB {p["cb_mm"]:g}mm']
    if p["is_step"] and not p["is_2pc"]:
        s = f'STEP {p["step_cb_mm"]:g}mm'
        if p.get("step_depth_in"):
            s += f' × {p["step_depth_in"]:.2f}" deep'
        bits.append(s)
    if p["is_2pc"] and p.get("step_cb_mm"):
        bits.append(f'piece CBs {p["cb_mm"]:g}/{p["step_cb_mm"]:g}mm')
    if p.get("ob_mm") is not None:
        bits.append(f'OB {p["ob_mm"]:g}mm')
    if p.get("hub_cb_mm") is not None:
        bits.append(f'hub CB {p["hub_cb_mm"]:g}mm')
    d = f'{p["disc_in"]:g}"'
    if p.get("hc_in") is not None:
        d += f' + {p["hc_in"]:g}" hub'
    bits.append(d)
    return "  ·  ".join(bits)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_title_match(params: dict, title: str) -> tuple[int, list[str]]:
    """
    Score a program title against parsed order params.

    Part type is a HARD filter: SR orders only see steel-ring files, HC orders
    only hub files, STD/1pc orders never see 2PC / HC / steel-ring files, and
    STEP orders only STEP files.  Within the allowed type, closeness is scored.

    Returns (score_0_to_100, matched_fields_list).
    """
    if not title:
        return 0, []

    specs = _vfy.parse_title_specs(title)
    if specs is None:
        return 0, []

    p_type    = params.get("part_type", "STD")
    p_is_step = bool(params.get("is_step")) and not params.get("is_2pc")

    t_is_step = bool(specs.get("is_step")) or bool(re.search(r'\bSTEP\b', title, re.IGNORECASE))
    t_is_2pc  = bool(re.search(r'-*2\s*PC\b', title, re.IGNORECASE))
    t_is_sr   = bool(specs.get("is_steel_ring")) or bool(_STEEL_RE.search(title))
    t_has_hc  = specs.get("hc_height_in") is not None

    # ── Hard part-type gates ─────────────────────────────────────────────────
    if p_type == "SR":
        if not t_is_sr:
            return 0, []
    elif t_is_sr:
        return 0, []
    if t_is_2pc != (p_type == "2PC"):
        return 0, []
    if p_type == "HC" and not t_has_hc:
        return 0, []
    if p_type == "STD" and t_has_hc:
        return 0, []
    if p_type in ("STD", "HC") and p_is_step != t_is_step:
        return 0, []

    raw     = 0
    matched = []
    missed  = []

    # ── Round size (30 pts, hard gate) ───────────────────────────────────────
    t_round = specs.get("round_size_in")
    if t_round is not None and abs(t_round - params["round_in"]) <= _TOL_ROUND_IN:
        raw += 30
        matched.append(f'Round {params["round_in"]}" ✓')
    else:
        return 0, []

    # ── CB (25 pts) ──────────────────────────────────────────────────────────
    t_cb = specs.get("cb_mm")
    p_cb = params["cb_mm"]
    if t_cb is not None and abs(t_cb - p_cb) <= _TOL_CB_MM:
        raw += 25
        matched.append(f"CB {p_cb:.1f}mm ✓")
    else:
        missed.append(f"CB {p_cb:.1f}mm ✗" + (f" (title: {t_cb:.1f}mm)" if t_cb else ""))

    # ── Disc thickness (15 pts) — J/M conflict accepts either value ─────────
    t_len = specs.get("length_in")
    cands = [params["disc_in"]]
    if params.get("alt_disc_in") is not None:
        cands.append(params["alt_disc_in"])
    if t_len is not None and any(abs(t_len - c) <= _TOL_DISC_IN for c in cands):
        raw += 15
        matched.append(f'Disc {params["disc_in"]}" ✓')
    else:
        missed.append(f'Disc {params["disc_in"]}" ✗'
                      + (f' (title: {t_len:.3f}")' if t_len else ""))

    # ── Part type (15 pts — gates above guarantee the match) ─────────────────
    raw += 15
    matched.append({"SR": "Steel Ring ✓", "HC": "HC ✓",
                    "2PC": "2PC ✓", "STD": "STD ✓"}[p_type]
                   if not p_is_step else "STEP ✓")

    # ── STEP counterbore (15 pts, only for step orders) ──────────────────────
    max_step = 15 if (p_is_step and params.get("step_cb_mm") is not None) else 0
    if max_step:
        t_step = specs.get("step_mm")
        p_step = params["step_cb_mm"]
        if t_step is not None and abs(t_step - p_step) <= _TOL_CB_MM:
            raw += 15
            matched.append(f"Counterbore {p_step:.1f}mm ✓")
        else:
            missed.append(f"Counterbore {p_step:.1f}mm ✗"
                          + (f" (title: {t_step:.1f}mm)" if t_step else ""))

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
    max_possible = 30 + 25 + 15 + 15 + max_step + max_ob + max_hc
    score = round(raw * 100 / max_possible) if max_possible else 0

    return score, matched + missed


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

    # Must be a 2PC title, and never a steel ring
    if not re.search(r'-*2\s*PC\b', title, re.IGNORECASE):
        return 0, []
    if _STEEL_RE.search(title):
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


def find_2pc_pairs(params: dict, db_path: str,
                   scope_folders: list | None = None) -> list[tuple]:
    """
    For a 2PC order row, find pairs of files (ring + bell/hat) that fit together.

    Uses:
      - params["cb_mm"]      as the ring piece CB
      - params["step_cb_mm"] as the bell/hat CB (if present), else same as cb_mm
      - params["piece_a_in"] / params["piece_b_in"] for individual piece thicknesses
        (thickness check is skipped for a piece whose finished size is unknown,
        e.g. stock-letter pairs like "(A+B)")
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
        # Standard 2PC: piece CBs from col K (A/B via /), thicknesses from col M
        # when the pair notation gives finished sizes (mm); stock letters → None
        cb_a         = params["cb_mm"]
        cb_b         = params.get("step_cb_mm") or params["cb_mm"]
        thick_a      = params.get("piece_a_in")
        thick_b      = params.get("piece_b_in")
        require_hc_b = False
        forbid_hc_a  = False

    conn = db_mod.get_connection(db_path)
    sql = ("SELECT id, o_number, file_name, program_title, verify_status "
           "FROM files "
           "WHERE program_title IS NOT NULL AND program_title != '' "
           "  AND (program_title LIKE '%2PC%' OR program_title LIKE '%2 PC%') ")
    args: list = []
    if scope_folders:
        sql += ("AND source_folder IN ("
                + ",".join("?" * len(scope_folders)) + ") ")
        args = list(scope_folders)
    rows = conn.execute(sql + "ORDER BY o_number", args).fetchall()
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
