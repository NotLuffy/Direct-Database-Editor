"""
verifier.py — Verifies CB, OB, and STEP dimensions in HAAS lathe G-code programs.

Title format:  ROUND_SIZE[IN]  CB_MM[/OB_MM]  [LENGTH_IN]
  CB (center bore): nominal mm. G-code should have CB_MM + 0.1 mm, in inches.
  OB (outer bore):  nominal mm. G-code should have OB_MM - 0.1 mm, in inches.
  Tolerance on inch value: ±0.001"

STEP parts:  ROUND_SIZE  COUNTERBORE_MM/CENTERBORE_MM  STEP  [depth]
  Both bores are cut in the same bore operation (counterbore first, then step
  down to the smaller center bore).  Both get +0.1 mm offset (both are internal).
  The counterbore is found via the (X IS CB) marker; the center bore is the next
  smaller X value in the same bore block.

Tolerance exceptions:
  1. CB titled 116.7mm → code is written as 116.9mm (+0.2 offset instead of +0.1).
  2. Steel ring parts (title contains "STEEL RING" or "STL RING") → CB in code
     is 0.1–0.4mm more than title spec.  Acceptance range: [cb_mm+0.1, cb_mm+0.4]
     with ±TOLERANCE_IN on each end.  cb_expected_in stores the low end of range;
     cb_expected_max_in stores the high end (present only for steel ring parts).

G-code structure:
  Ops are split at (FLIP PART). CB is typically before, OB after — but
  explicit markers are searched across the whole file.

  CB markers (X taken from same line):
    (X IS CB), (X IS ID), (X IS C.B.), (X IS CENTER BORE)
    Fallback: largest X in the T121 bore tool block (before FLIP PART).
    Ignored:  (X IS HOLDER), (X IS HB) — these are retract/secondary features.

  OB markers (X taken from same line):
    (X IS OB), (X IS OD), (X IS HUB), (X CB) [typo form]
"""

import re

_MM_TO_IN      = 1.0 / 25.4
TOLERANCE_IN   = 0.001   # ±0.001" acceptance window (CB / OB)
DR_TOLERANCE_IN = 0.020  # ±0.020" acceptance window (drill depth)
OD_TOLERANCE_IN = 0.015  # ±0.015" acceptance window (OD turn-down)

# CB special offsets
_CB_116_7_ACTUAL_MM = 116.9   # 116.7mm title → 116.9mm in code (+0.2 instead of +0.1)

# Steel ring detection — STEEL RING, STL RING, HCS-1/HCS-2, and STEEL S-N (grade designations)
_STEEL_RING_RE = re.compile(
    r'\b(?:STEEL|STL)[\s._-]*RING\b'    # STEEL RING / STL RING
    r'|\bHCS-\d+\b'                      # HCS-1, HCS-2
    r'|\bSTEEL\s+S-\d+\b',              # STEEL S-1, STEEL S-2
    re.IGNORECASE,
)

# --- Regexes ---

# Title examples:
#   7.5IN  107/131MM STUD 0.75  --2PC       (with IN)
#   7IN DIA 77.8MM ID 1.25 --2PC LUG        (with DIA)
#   8.5IN$ DIA 116.7-124.1 2.0 HC           (dash separator, $ typo)
#   6.0 IN 72.6/66.56MM .75HC               (space before IN, no-space HC value)
#   13.0 124/220MM 2.0 HC 1.5               (no IN on round size)
#   5.75 70.3MM/70.3MM 15MM HC              (no IN, MM after each value)
#   13.0 116.7IN/220MM 4.0 HC               (IN after CB value, typo)
#   7.5 LUG 116.7MM .5                      (LUG instead of DIA)
#   6.00$ DIA 66.1/72.56MM 15MM HC          ($ typo on round size, no IN)
#   13.0 220CB 10MM SPACER                  (MM spacer thickness, no HC)
#   8.5 116.7MM HCS-1 1.5                   (HCS-1/HCS-2 = steel ring)
#   7.0 107MM --HCXX 1.0                    (HCXX = hub-centric = bare HC 0.50")
#   5.75$ IN DIA 58.1MM ID 1.00             ($ THEN IN THEN DIA — very common)
#   9.5IN* DIA 124.9MM ID 1.5               (* after IN — asterisk typo)
#   9.5IND - 133.35MM ID 1.50               (D after IN, dash separator before CB)
#   6.25$ IN 67.1INNER 1.0 2PC              (INNER keyword = ID)
#   8IN DIA 2.76"/121.3MM 1.5 -HCXX        (inch-mark before / in OB separator)
_TITLE_SPEC_RE = re.compile(
    r'(\d+(?:\.\d+)?)'                              # group 1: round size
    r'(?:\s*(?:IN[$*D]?|[$*"]))?'                   # optional attached unit: IN, IN$, IN*, IND, $, *, "
    r'(?:\s+(?:IN\$?|DIA\b|LUG\b|HC\b))*'            # optional spaced keywords (repeatable): IN, DIA, LUG, HC
    r'\s*(?:-\s*)?'                                  # optional dash separator (e.g. "IND - 133MM")
    r'(\d+(?:\.\d+)?)'                              # group 2: CB mm (required)
    r'(?:[A-Za-z"]*\s*[/\-]\s*(\d+(?:\.\d+)?))?'  # group 3: optional OB (allow " before /)
    r'(?:\s*MM?\b)?'                                # optional MM or M unit
    r'(?:\s+(?:ID|INNER)\b)?'                       # optional ID/INNER keyword
    r'(?:\s+(\d*\.?\d+))?',                         # group 4: optional length
    re.IGNORECASE,
)

_FLIP_RE      = re.compile(r'\(\s*FLIP\s*PART\s*\)',                       re.IGNORECASE)
# Tool codes: (?!\d) instead of \b so T121X, T121( etc. all match but T1210 does not
_T121_RE      = re.compile(r'\bT0?121(?!\d)',                              re.IGNORECASE)
_T303_RE      = re.compile(r'\bT0?303(?!\d)',                              re.IGNORECASE)
_TOOL_CHG_RE  = re.compile(r'\bT\d{3,4}(?!\d)',                           re.IGNORECASE)
# X/Z coordinate addresses: (?<![A-Z]) rejects X/Z inside words (e.g. MAX, FIZZ)
# but allows them after digits (e.g. G54X2.3, P17X0., G83Z-4.15)
_X_RE         = re.compile(r'(?<![A-Z])X(-?\d+(?:\.\d+)?)',               re.IGNORECASE)
_Z_RE         = re.compile(r'(?<![A-Z])Z(-?\d+(?:\.\d+)?)',               re.IGNORECASE)
# G-codes: (?!\d) instead of \b to allow G53X, G00Z, G81Z etc. without space
_G53_RE       = re.compile(r'\bG53(?!\d)',                                 re.IGNORECASE)
_G00_LINE_RE  = re.compile(r'\bG0+(?!\d)',                                 re.IGNORECASE)  # G0/G00 rapid
_FEED_RE      = re.compile(r'\bG0*[123](?!\d)',                            re.IGNORECASE)  # G01/G02/G03
_CB_CMT_RE = re.compile(
    r'\(\s*X\s+IS\s+(?:C\.?B\.?|I\.?D\.?|CENTER\s*BORE)\s*\)',            re.IGNORECASE)
_OB_CMT_RE = re.compile(
    r'\(\s*X\s+IS\s+(?:O\.?B\.?|O\.?D\.?|HUB)\s*\)'
    r'|\(\s*X\s+CB\s*\)',                                                   re.IGNORECASE)

# 2PC recess / hub VARIABLE comment: "(VARIABLE)" or "(X IS VARIABLE)"
_VARIABLE_RE = re.compile(r'\(\s*(?:X\s+IS\s+)?VARIABLE\s*\)', re.IGNORECASE)

# Chamfer-into-CB pattern: G01 X{bore_diam} Z{-small} F{feed}
# After roughing passes the tool cuts a small chamfer from face into the bore.
# Z depth ≤ 0.35" distinguishes this from a full boring pass.
# G01/X/Z/F use (?!\d) or no leading boundary to handle no-space formatting
_CHAMFER_CB_RE = re.compile(
    r'\bG0*1(?!\d)[^(]*X(-?\d+(?:\.\d+)?)[^(]*Z(-\d+(?:\.\d+)?)[^(]*F\d',
    re.IGNORECASE)
_CHAMFER_Z_MAX_IN = 0.35   # larger Z depths are boring passes, not chamfers

# Rough bore pass checker constants
_RB_START_LIMIT = 2.3   # approach X must be < 2.4 to be "good"
_RB_FLAG_X      = 2.4   # flag if approach X >= this (if CB >= 58mm)
_RB_STEP_LIMIT  = 0.3   # max X increment between consecutive bore passes (inches)
_RB_CB_SKIP_MM  = 58.0  # skip start check when CB < this (going straight to CB)
_RB_DEEP_X      = 6.8   # passes at or beyond this X must have decreasing Z depth

# HC height in title: "HC 1", "HC 1.5", "HC .75", "HC1.5" (no space) — hub height in inches
# \d*\.?\d+ handles both "1.5" and ".75" (leading dot); \s* allows no-space like "HC1.5"
_HC_HEIGHT_RE  = re.compile(r'\bHC\s*(\d*\.?\d+)',  re.IGNORECASE)
# 15MM HC: special drill-depth case — always Z-1.15" regardless of part thickness
# \s* allows "15MMHC" with no spaces
_HC_15MM_RE    = re.compile(r'\b15\s*MM\s*HC\b',    re.IGNORECASE)
# Hub-centric variants: HCX, HCXX, -HCX, --HCXX, ---HCXX → treated as bare HC (0.50")
_HC_CENTRIC_RE = re.compile(r'-*HCX+\b',            re.IGNORECASE)
# Bare "HC" with no value following → standard hub 0.50"
# Also catches NNNN HC (value BEFORE HC, where value is disc thickness not HC height)
_HC_BARE_RE    = re.compile(r'(?:\b|\d)HC\b',        re.IGNORECASE)
# MM disc thickness: "10MM SPACER", "12MMSPACER", "10MM" standalone (disc ≤ 50mm)
# Requires SPACER keyword OR small value not followed by HC/ID/slash (avoids CB/OB confusion)
_SPACER_MM_RE  = re.compile(r'(\d+(?:\.\d+)?)\s*MM\s*SPACER\b', re.IGNORECASE)
_DISC_MM_RE    = re.compile(r'(?<!\d)(\d+(?:\.\d+)?)\s*MM\b(?!\s*(?:HC|ID|/|-))', re.IGNORECASE)
# Thickness after STUD or LUG label: "STUD 0.75", "107STUD 0.75", "LUG 1.25"
# No leading \b: digits may precede STUD/LUG (e.g. "107STUD") with no word boundary.
_STUD_LUG_THICK_RE = re.compile(r'(?<![A-Za-z])(?:STUD|LUG)\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Thickness after B/C (bore/counterbore) label, optional trailing dash: "B/C 1.25-", "B/C 2.0"
_BC_THICK_RE       = re.compile(r'\bB/C\b\s*(\d*\.?\d+)-?', re.IGNORECASE)
# Explicit THK suffix: "3.0 THK", "2.75"THK", "2.0 THK"
_THK_SUFFIX_RE     = re.compile(r'(\d*\.?\d+)["\s]*THK\b', re.IGNORECASE)
# Inch-value spacer without MM: "2.0 SPACER", ".75 SPACER", "1.5 SPACER"
_SPACER_IN_RE      = re.compile(r'(\d*\.\d+|\d+\.\d*)\s+SPACER\b', re.IGNORECASE)
# Disc thickness stated BEFORE HC keyword (no-space or space): "1.5HC", "2.0HC", "1.5 HC .5"
# Requires a decimal point (so bare integers like 15 in "15MM HC" won't accidentally fire).
_PRE_HC_THICK_RE   = re.compile(r'(?<!\d)(\d+\.\d*|\d*\.\d+)\s*HC\b', re.IGNORECASE)
# Disc thickness in MM before bare HC: "40MM HC", "25MM HC" (not 15MM HC — that's a hub height)
_PRE_HC_MM_RE      = re.compile(r'(?<!\d)(\d+(?:\.\d+)?)\s*MM\s+HC\b', re.IGNORECASE)
# Thickness letter codes: A=1.00", B=1.25", C=1.50", D=1.75", E=2.00", F=2.25", G=2.50"
# Matches a standalone letter (space/boundary on both sides) after the CB portion of the title.
_THICK_LETTER_MAP = {'A': 1.00, 'B': 1.25, 'C': 1.50, 'D': 1.75,
                     'E': 2.00, 'F': 2.25, 'G': 2.50}
_THICK_LETTER_RE  = re.compile(r'(?:^|\s)([A-G])(?:\s|$)', re.IGNORECASE)
# Thickness after ID or ID-OD keyword: "B/C ID 2.0", "SPECIAL ID 1.5", "ID-OD 3.5"
_ID_SUFFIX_THICK_RE = re.compile(r'\bID(?:-OD)?\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Thickness after OD keyword: "OD 2.5 XX", "OD 3.75- XX"
_OD_SUFFIX_THICK_RE = re.compile(r'\bOD\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Thickness after STEP keyword (three sub-cases handled in code):
#   "STEP-2.6  3.0"    → dash+step-depth then disc thickness
#   "STEP 0.40 DEEP 2.00" → DEEP keyword separates step-depth from disc thickness
#   "STEP 2.5"         → direct value, only when no DEEP in title
_STEP_DASH_THICK_RE  = re.compile(r'\bSTEP\s*-\d+(?:\.\d+)?\s+(\d*\.?\d+)', re.IGNORECASE)
_STEP_DIRECT_THICK_RE = re.compile(r'\bSTEP\b\s+(\d*\.?\d+)', re.IGNORECASE)
_STEP_DEEP_THICK_RE  = re.compile(r'\bDEEP\b\s+(\d*\.?\d+)', re.IGNORECASE)
# INNER keyword attached to CB value (no space): "67.1INNER .75", "61INNER 1.0"
# No leading \b because digits precede INNER with no word boundary.
_INNER_THICK_RE     = re.compile(r'(?<![A-Za-z])INNER\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Dash separator between bore MM and thickness: "87.1MM - 1.25", "74.4 MM - 2.00"
# Leading \b omitted: digits precede MM with no word boundary (e.g. "87.1MM").
_DASH_SEP_THICK_RE  = re.compile(r'(?<![A-Za-z])MM?\b\s*-\s*(\d+\.\d+)', re.IGNORECASE)
# OB stated in inches blocking group 4: "77.8MM/4.875IN 1.25", "116.7/5.125IN 3.0"
_OB_IN_THICK_RE     = re.compile(r'/\d+(?:\.\d+)?\s*IN\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Trailing letter suffix on OB then thickness: "131.7L 1.5" (L = lathe suffix)
_TRAILING_L_THICK_RE = re.compile(r'\d+(?:\.\d+)?L\b\s+(\d*\.?\d+)', re.IGNORECASE)
# Bracket expression then thickness: "[1/2 STEP] 1.50"
_AFTER_BRACKET_THICK_RE = re.compile(r'\]\s+(\d*\.?\d+)', re.IGNORECASE)

# Feed rate (F word): captures decimal value.  F0.008, F.02, F0.02, F.1 etc.
# (?<![A-Z]) prevents matching inside words (e.g. "OFFSET") but allows after G01
_F_RE         = re.compile(r'(?<![A-Z])F(\d*\.?\d+)', re.IGNORECASE)
_F_MAX        = 0.02   # maximum allowed feed rate (inches/rev)

# Integer coordinate check: X or Z value with NO decimal point (e.g. X3, Z-1).
# Z0 is exempt — common retract value.  All other integer X/Z are flagged.
# (?<![A-Z]) prevents matching inside words; (?![\d.]) ensures no trailing digit/decimal.
_INT_COORD_RE  = re.compile(r'(?<![A-Z])[XZ]-?\d+(?![\d.])', re.IGNORECASE)
_INLINE_CMT_RE = re.compile(r'\(.*?\)')

# Drill tool — (?!\d) instead of \b to handle no-space formatting like G83Z-4.15
_T101_RE = re.compile(r'\bT0?101(?!\d)', re.IGNORECASE)
_G81_RE  = re.compile(r'\bG81(?!\d)',    re.IGNORECASE)  # standard drilling cycle
_G83_RE  = re.compile(r'\bG83(?!\d)',    re.IGNORECASE)  # peck drilling cycle
_M00_RE  = re.compile(r'\bM0*0(?!\d)',   re.IGNORECASE)  # M00/M0 program stop (flip)
_M30_RE  = re.compile(r'\bM0*30(?!\d)',  re.IGNORECASE)  # M30 end of program

# OD turn-down table: round size (in) → standard finish OD X value (in)
_OD_TABLE = {
     5.75:  5.700,
     6.00:  5.950,
     6.25:  6.200,
     6.50:  6.450,
     7.00:  6.945,
     7.50:  7.445,
     8.00:  7.945,
     8.50:  8.440,
     9.50:  9.450,
    10.25: 10.170,
    10.50: 10.450,
    13.00: 12.903,
}

# ---------------------------------------------------------------------------
# P-code tables: total part thickness (in) → (OP1 G154 P#, OP2 G154 P#)
# ---------------------------------------------------------------------------
# Lathe 1: thin MM registrations (below 0.75")
#   ~10mm disc (no HC)      → P1/P2
#   ~12/13/15mm disc (no HC)→ P5/P6  (P3/P4 also valid for 12mm — see _LATHE1_ALT_PC)
# Lathe 1: standard inch registrations 0.75"–8.00" in 0.25" steps; P13/P14 base
_LATHE1_PC  = {
    0.25: ( 1,  2),   # ~10mm disc no HC
    0.50: ( 5,  6),   # ~12/13/15mm disc no HC
    **{round(0.75 + i * 0.25, 2): (13 + i * 2, 14 + i * 2) for i in range(30)}
}
# Alternate Lathe 1 P-codes for specific MM sizes:
#   12mm disc → P3/P4 (dedicated, alternate to P5/P6)
#   17mm disc → P7/P8 (dedicated, alternate to P13/P14 at 0.75")
_LATHE1_ALT_PC = {0.50: (3, 4), 0.75: (7, 8)}

# Lathe 2/3: thin MM registrations
#   ~12/13/15mm disc (no HC) → P1/P2
#   ~17/20/22mm disc (no HC) → P3/P4
# Lathe 2/3: standard inch registrations 1.00"–8.00" in 0.25" steps; P5/P6 base
_LATHE23_PC = {
    0.50: (1, 2),   # ~12/13/15mm disc no HC
    0.75: (3, 4),   # ~17/20/22mm disc no HC
    **{round(1.00 + i * 0.25, 2): ( 5 + i * 2,  6 + i * 2) for i in range(29)}
}
# Reverse maps: (op1, op2) → thickness (for reporting which thickness a P-pair implies)
_LATHE1_PC_REV  = {v: k for k, v in _LATHE1_PC.items()}
_LATHE23_PC_REV = {v: k for k, v in _LATHE23_PC.items()}

# G154 work offset P-code reader — no trailing \b: handles "G154 P29X0." (no space before next word)
_G154_RE = re.compile(r'\bG154\s*P(\d+)', re.IGNORECASE)
# G54/G55 direct work-offset detection (no P-codes in these programs)
_G5X_RE  = re.compile(r'\bG5[45](?!\d)', re.IGNORECASE)
# G53 home/retract — matches any G53 line with a negative Z value.
# Does NOT restrict to specific X value; collects all home Z positions in the file.
_HOME_G53_RE = re.compile(
    r'\bG53(?!\d)[^;\n(]*Z(-\d+(?:\.\d+)?)',
    re.IGNORECASE)


def _home_z_for_thickness(t: float) -> int | None:
    """Return expected G53 Z home value based on total part thickness (inches).
    Returns None when thickness > 5.0" (no data available for those parts)."""
    if t <= 2.50:
        return -13
    if t <= 3.75:
        return -11
    if t <= 5.00:
        return -9
    return None   # > 5.0" — no data


def _lathe_label_for_round(rs: float) -> str:
    """Return the lathe designation label based on round size (inches)."""
    if rs <= 6.50:
        return "1"
    if rs <= 8.50:
        return "2/3"
    return "2"


def _pc_table_for_round(rs: float) -> dict:
    """Return the correct P-code table for a given round size."""
    if rs <= 6.50:
        return _LATHE1_PC
    return _LATHE23_PC


def parse_title_specs(title: str) -> dict | None:
    """
    Return parsed spec dict or None if title has no recognisable CB/OB.

    Rules:
      - Round size <= 15.0  → in inches (7.5, 13.0, etc.)
      - CB/OB values > 15.0 → in mm (bore diameters are always well above 15 mm)
      - Values <= 15 after CB are part thickness, not bore dims — ignored
      - OB is only present when a /OB or -OB follows CB (hub parts with HC label)
      - 2PC parts have no hub/OB — CB only
    """
    if not title:
        return None
    # Strip punctuation decorators used in some titles (e.g. "!!!142/220MM!!!")
    title = re.sub(r'[!?]+', ' ', title)
    # Normalize trailing dot on integers with no decimal digits ("121." → "121", "110." → "110")
    # Avoids confusing the regex when a bare dot appears before a dash or space.
    title = re.sub(r'\b(\d+)\.(?!\d)', r'\1', title)
    m = _TITLE_SPEC_RE.search(title)
    if not m:
        return None

    round_size_in = float(m.group(1))
    cb_mm         = float(m.group(2))

    # Round size must be ≤ 15" (reasonable max for these wheel parts)
    if round_size_in > 15.0:
        return None
    # CB must be ≥ 15 mm — if < 15 the regex matched a disc thickness or tiny value, not a bore.
    # Handle two alternative title formats where thickness comes first:
    #   Format A:  "2.77/72.56MM"  → thickness_in=2.77, cb_mm=72.56  (OB slot has real CB)
    #   Format B:  "2.75IN/2.75INmm" → thickness_in=2.75, cb_mm=2.75×25.4=69.85
    _thickness_override_in = None   # disc thickness extracted from these formats
    _format_c_ob_mm = None          # OB converted from inches in Format C
    if cb_mm < 15.0:
        ob_candidate = float(m.group(3)) if m.group(3) else None
        if ob_candidate is not None and ob_candidate > 15.0:
            # Format A: OB slot held the real CB; first value was disc thickness
            _thickness_override_in = cb_mm
            cb_mm = ob_candidate
            ob_candidate = None   # no separate OB in this layout
        else:
            # Format B: look for an explicit NNN.NIN value to convert (skip round size)
            _in_vals = re.findall(r'(\d+(?:\.\d+)?)\s*IN\b', title, re.IGNORECASE)
            _cb_from_in = None
            for _v in _in_vals:
                _vf = float(_v)
                if abs(_vf - round_size_in) > 0.01 and _vf > 0.5:
                    _cb_from_in = round(_vf * 25.4, 3)
                    # Only treat as disc-thickness override when the inch value is NOT
                    # immediately followed by IN in the title (e.g. "5.3IN" = bore label,
                    # not a disc thickness). A bare inch value like "2.75/220MM" is OK.
                    _in_pos = title.find(_v)
                    _after_in_val = title[_in_pos + len(_v):_in_pos + len(_v) + 3]
                    if not re.match(r'\s*IN\b', _after_in_val, re.IGNORECASE):
                        _thickness_override_in = _vf
                    break
            if _cb_from_in is not None and _cb_from_in > 15.0:
                cb_mm = _cb_from_in
                ob_candidate = None
            elif 0.4 < cb_mm <= 6.5:
                # Format C: CB is a bare inch value (e.g. "8IN DIA 4.56 2.0 STEEL HCS-1")
                # Require a bore-context signal to avoid mistaking disc thickness for inch bore
                _inch_signal = bool(re.search(
                    r'\bDIA\b|\bID\b|\bINNER\b|\bHCS-\d|\d"', title, re.IGNORECASE))
                # Skip if CB has an explicit MM suffix (truly is in mm, e.g. 2.25MM)
                _cb_suffix = title[m.end(2):m.end(2) + 4]
                _has_mm = bool(re.match(r'\s*MM?\b', _cb_suffix, re.IGNORECASE))
                if _inch_signal and not _has_mm:
                    if ob_candidate is not None and 0.4 < ob_candidate <= 6.5:
                        _format_c_ob_mm = round(ob_candidate * 25.4, 3)
                    cb_mm = round(cb_mm * 25.4, 3)
                else:
                    return None
            else:
                return None
        if cb_mm < 15.0:
            return None

    if _format_c_ob_mm is not None:
        ob_mm = _format_c_ob_mm
    else:
        ob_mm = float(m.group(3)) if m.group(3) and _thickness_override_in is None else None
        # OB must also be > 15 mm; otherwise it's a hub-height value.
        # Exception: if the OB value is small but followed by "IN" it is in inches — convert.
        # e.g. "77.8MM/4.875IN 1.25" → OB = 4.875" = 123.8mm (hub bore in inches)
        if ob_mm is not None and ob_mm <= 15.0:
            ob_pos = m.end(3)
            if ob_pos <= len(title) and re.match(r'\s*IN\b', title[ob_pos:], re.IGNORECASE):
                ob_mm = round(ob_mm * 25.4, 3)   # convert inch hub bore to mm
            else:
                ob_mm = None

    # STEP parts: ###/### is counterbore/centerbore (both internal bores), not hub OB
    is_step = bool(re.search(r'\bSTEP\b', title, re.IGNORECASE))
    step_mm = None
    if is_step and ob_mm is not None:
        step_mm = ob_mm
        ob_mm   = None

    is_steel_ring = bool(_STEEL_RING_RE.search(title))  # includes HCS-1/HCS-2

    # HC height detection (order matters — most specific first):
    #   1. 15MM HC (or 15MMHC) → 15mm in inches, special drill depth rule
    #   2. HC<value> or HC <value> → explicit inch height (e.g. HC1.5, HC .75)
    #   3. <value>HC — value written BEFORE the HC label (e.g. 1.5HC, 1.00HC)
    #   4. HCX/HCXX/--HCXX etc. → hub-centric = standard bare HC (0.50")
    #   5. Bare HC → standard hub 0.50"
    if _HC_15MM_RE.search(title):
        hc_height_in = round(15.0 * _MM_TO_IN, 4)   # ≈ 0.5906"
    elif (hc_m := _HC_HEIGHT_RE.search(title)):
        hc_height_in = float(hc_m.group(1))
    elif _HC_CENTRIC_RE.search(title):
        hc_height_in = 0.50                          # HCX/HCXX = hub-centric = standard hub
    elif _HC_BARE_RE.search(title):
        hc_height_in = 0.50                          # bare "HC" / "1.5HC" → standard hub 0.50"
    else:
        hc_height_in = None

    # Disc thickness (length_in):
    # Primary: if we extracted thickness from an alternative title format, use it directly
    if _thickness_override_in is not None:
        raw_len = _thickness_override_in
    else:
        # Normal path: main regex group(4) — plain decimal inch value in title
        # Guard: don't use "15" when it came from "15MM HC"
        raw_len_str = m.group(4)
        if _HC_15MM_RE.search(title) and raw_len_str == "15":
            raw_len_str = None
        raw_len = float(raw_len_str) if raw_len_str else None
        # Guard: disc thickness must be ≤ 10" — larger values from group 4 are bore dimensions
        # (e.g. "ID 71.5 OD 1.50" → group4="71.5" is an OD bore, not thickness)
        if raw_len is not None and raw_len > 10.0:
            raw_len = None

    # Track whether disc thickness was parsed from an MM value (not a plain inch decimal).
    # When True, drill depth verification is skipped (insufficient data for MM-thickness parts).
    length_from_mm = False

    # If group 4 captured an integer that is the numerator of a fraction (e.g. "7/8 THK"),
    # convert the fraction to a decimal inch value.
    # e.g. "7/8 HC" → group4="7", after="/8 HC" → 7/8 = 0.875"
    if (raw_len is not None and _thickness_override_in is None
            and m.group(4) and '.' not in m.group(4)):
        after_g4 = title[m.end(4):]
        frac_m = re.match(r'/(\d+)', after_g4)
        if frac_m:
            denom = float(frac_m.group(1))
            if denom > 0 and raw_len < denom:   # proper fraction: numerator < denominator
                raw_len = round(raw_len / denom, 4)

    # If group 4 captured a small number immediately followed by MM or a bare HC (no MM),
    # it is an MM value mis-read as inches — convert it.
    # e.g. "12MM HC" → group4="12", after="MM HC" → 12/25.4=0.4724"
    #      "15HC"    → group4="15", after="HC"    → 15/25.4=0.5906"
    #      "20MM-HC" → group4="20", after="MM-HC" → 20/25.4=0.7874"
    #      "10MM"    → group4="10", after="MM..."  → 10/25.4=0.3937"
    # Only fires when: value is 5–80 (plausible mm range) AND has no decimal point
    # (decimal values like "1.5 HC" are valid inch measurements, not mm).
    if (raw_len is not None and _thickness_override_in is None
            and m.group(4) and '.' not in m.group(4)
            and 5.0 <= raw_len <= 80.0):
        after_g4 = title[m.end(4):]
        # Followed by MM (with optional dash/space before HC): "MM HC", "MM-HC", "MM THK", "MM"
        if re.match(r'\s*MM?\b', after_g4, re.IGNORECASE):
            raw_len = round(raw_len / 25.4, 4)
            length_from_mm = True
        # Followed directly by bare HC (no MM), e.g. "15HC", "10HC"
        elif re.match(r'\s*HC\b', after_g4, re.IGNORECASE):
            raw_len = round(raw_len / 25.4, 4)
            length_from_mm = True

    # Fallback 1: explicit MM SPACER ("10MM SPACER", "12MMSPACER")
    if raw_len is None:
        sm = _SPACER_MM_RE.search(title)
        if sm:
            mm_val = float(sm.group(1))
            if mm_val < 50.0:    # sanity: disc spacer won't be > ~2"
                raw_len = round(mm_val * _MM_TO_IN, 4)
                length_from_mm = True

    # Fallback 2: standalone small MM value not claimed by HC/CB/OB
    if raw_len is None:
        for mm_m in _DISC_MM_RE.finditer(title):
            mm_val = float(mm_m.group(1))
            if 5.0 <= mm_val <= 50.0:
                raw_len = round(mm_val * _MM_TO_IN, 4)
                length_from_mm = True
                break

    # Fallback 3: thickness letter codes (A=1.00", B=1.25", C=1.50", …, G=2.50")
    # Searches the substring after the CB match to avoid round-size/CB digits.
    if raw_len is None:
        after_cb = title[m.start(2):]
        lm = _THICK_LETTER_RE.search(after_cb)
        if lm:
            raw_len = _THICK_LETTER_MAP.get(lm.group(1).upper())

    # Fallback 4: thickness after STUD or LUG label ("STUD 0.75", "LUG 1.25")
    if raw_len is None:
        sl = _STUD_LUG_THICK_RE.search(title)
        if sl:
            v = float(sl.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 5: thickness after B/C label, optional trailing dash ("B/C 1.25-", "B/C 2.0")
    if raw_len is None:
        bc = _BC_THICK_RE.search(title)
        if bc:
            v = float(bc.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 6: explicit THK suffix ("3.0 THK", "2.0 THK XX")
    if raw_len is None:
        tk = _THK_SUFFIX_RE.search(title)
        if tk:
            v = float(tk.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 7: inch-value SPACER suffix without MM ("2.0 SPACER", ".75 SPACER")
    if raw_len is None:
        sp = _SPACER_IN_RE.search(title)
        if sp:
            v = float(sp.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 8: disc thickness (inch) stated before HC keyword ("1.5 HC .5", "2.0HC", "1.00 HC")
    # Covers cases where an IN-suffixed OB or no-space HC blocks group4.
    if raw_len is None and _HC_BARE_RE.search(title) and not _HC_15MM_RE.search(title):
        ph = _PRE_HC_THICK_RE.search(title)
        if ph:
            v = float(ph.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 9: disc thickness in MM before bare HC ("40MM HC", "25MM HC", "15MM HC")
    if raw_len is None:
        phm = _PRE_HC_MM_RE.search(title)
        if phm:
            mm_val = float(phm.group(1))
            if 5.0 <= mm_val <= 100.0:
                raw_len = round(mm_val * _MM_TO_IN, 4)
                length_from_mm = True

    # Fallback 10: thickness after ID or ID-OD keyword
    # Covers: "B/C ID 2.0", "STEP 0.40 DEEP ID 4.00", "SPECIAL ID 1.5", "ID-OD 3.5"
    if raw_len is None:
        im = _ID_SUFFIX_THICK_RE.search(title)
        if im:
            v = float(im.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 11: thickness after OD keyword ("OD 2.5 XX", "OD 3.75-")
    if raw_len is None:
        om = _OD_SUFFIX_THICK_RE.search(title)
        if om:
            v = float(om.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 12: STEP-related thickness (three sub-cases, checked in priority order)
    if raw_len is None and re.search(r'\bSTEP\b', title, re.IGNORECASE):
        # Sub-case A: "STEP N.NN DEEP N.NN" — thickness is AFTER the DEEP keyword
        sdm = _STEP_DEEP_THICK_RE.search(title)
        if sdm:
            v = float(sdm.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v
        # Sub-case B: "STEP-N.NN N.NN" — dash prefix on step-depth, then disc thickness
        if raw_len is None:
            sdb = _STEP_DASH_THICK_RE.search(title)
            if sdb:
                v = float(sdb.group(1))
                if 0.1 <= v <= 10.0:
                    raw_len = v
        # Sub-case C: "STEP N.NN" — direct value, only when no DEEP in title
        if raw_len is None and not re.search(r'\bDEEP\b', title, re.IGNORECASE):
            sdc = _STEP_DIRECT_THICK_RE.search(title)
            if sdc:
                v = float(sdc.group(1))
                if 0.1 <= v <= 10.0:
                    raw_len = v

    # Fallback 13: INNER keyword with no leading space attached to CB ("67.1INNER .75")
    if raw_len is None:
        inn = _INNER_THICK_RE.search(title)
        if inn:
            v = float(inn.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 14: dash separator between bore spec and thickness ("87.1MM - 1.25")
    if raw_len is None:
        dsm = _DASH_SEP_THICK_RE.search(title)
        if dsm:
            v = float(dsm.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 15: OB in inches blocks group 4 — thickness follows the "IN" unit
    # e.g. "77.8MM/4.875IN 1.25", "116.7/5.125IN 3.0", "116.7/124.9IN 3.0"
    if raw_len is None:
        obi = _OB_IN_THICK_RE.search(title)
        if obi:
            v = float(obi.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 16: letter suffix on OB value blocks group 4 ("131.7L 1.5", "124.1L 3.0")
    if raw_len is None:
        trl = _TRAILING_L_THICK_RE.search(title)
        if trl:
            v = float(trl.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    # Fallback 17: thickness after bracket expression ("[1/2 STEP] 1.50")
    if raw_len is None:
        brk = _AFTER_BRACKET_THICK_RE.search(title)
        if brk:
            v = float(brk.group(1))
            if 0.1 <= v <= 10.0:
                raw_len = v

    return {
        "round_size_in": round_size_in,
        "cb_mm":         cb_mm,
        "ob_mm":         ob_mm,
        "step_mm":       step_mm,
        "is_step":       is_step,
        "is_steel_ring": is_steel_ring,
        "hc_height_in":  hc_height_in,
        "length_in":     raw_len,
        "length_from_mm": length_from_mm,
    }


def _to_in(mm: float) -> float:
    """Convert mm to inches, rounded to 3 decimal places."""
    return round(mm * _MM_TO_IN, 3)


def _find_od_in_block(block_lines: list, line_offset: int = 0) -> tuple:
    """Find the OD turn-down X in a T303 block.

    Looks for the first G01 Z<-0.05" move and returns the modal X at that point
    (which is the OD approach position — the finished OD diameter).

    Returns (absolute_line_idx, x_value) or (None, None).
    line_offset: added to local index to produce the absolute file line number.
    """
    in_t303   = False
    modal_x   = None
    is_feed   = False
    for i, ln in enumerate(block_lines):
        s = ln.strip()
        if _T303_RE.search(s):
            in_t303 = True; modal_x = None; is_feed = False; continue
        if not in_t303:
            continue
        if _TOOL_CHG_RE.search(s) and not _T303_RE.search(s):
            break
        if _G53_RE.search(s):
            continue
        xm = _X_RE.search(s)
        if xm:
            xv = abs(float(xm.group(1)))
            if xv > 1.0:
                modal_x = xv
        if _G00_LINE_RE.search(s):
            is_feed = False
            continue
        if _FEED_RE.search(s):
            is_feed = True
        if is_feed and modal_x is not None:
            zm = _Z_RE.search(s)
            if zm and float(zm.group(1)) < -0.05:
                return line_offset + i, modal_x
    return None, None


_INTERNAL_ONUM_RE = re.compile(r'^(O\d{4,6})\b', re.IGNORECASE)


def _read_internal_onum(path: str) -> str | None:
    """Return the O-number string from the first code line of a G-code file, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                s = ln.strip()
                if not s or s == "%":
                    continue
                m = _INTERNAL_ONUM_RE.match(s)
                return m.group(1).upper() if m else None
    except Exception:
        pass
    return None


def _find_rough_bore(pre_flip_lines: list, cb_mm: float) -> dict:
    """Analyze the T121 pre-flip bore block for rough bore pass checks.

    Finds the approach X (from G154/G54 positioning line or first G00 X in the
    T121 block), then records the X and Z position at every G01 Z-negative bore pass.

    Returns dict with keys:
      rb_approach_x     — X value from positioning line (float or None)
      rb_pass_xs        — list of X values at each Z-plunge bore pass
      rb_pass_zs        — list of Z depths (negative floats) at each bore pass
      rb_start_ok       — True if approach_x < _RB_FLAG_X; None if skip or NF
      rb_steps_ok       — True if all consecutive increments ≤ _RB_STEP_LIMIT; None if <2 passes
      rb_max_step       — largest step found (float or None)
      rb_violations     — list of (x1, x2, step) tuples exceeding step limit
      rb_skip_cb        — True if CB < 58mm (start check not applicable)
      rb_deep_ok        — True if all passes beyond X6.8 have decreasing Z depth; None if N/A
      rb_deep_violations — list of (x_prev, z_prev, x_curr, z_curr) tuples where Z didn't decrease
    """
    in_t121    = False
    approach_x = None
    modal_x    = None
    in_feed    = False
    pass_xs    = []   # X value recorded at each Z-plunge bore pass
    pass_zs    = []   # Z depth at each bore pass (negative)
    pass_lns   = []   # 1-based file line number for each bore pass

    for file_idx, ln in enumerate(pre_flip_lines):
        s = ln.strip()
        line_no = file_idx + 1   # 1-based file line number
        if _T121_RE.search(s):
            in_t121 = True
            modal_x = None
            in_feed = False
            continue
        if not in_t121:
            continue
        if _TOOL_CHG_RE.search(s) and not _T121_RE.search(s):
            break
        if _G53_RE.search(s):
            continue

        # Get approach X from G154/G54/G55 positioning line (first one after T121)
        if approach_x is None and (_G154_RE.search(s) or _G5X_RE.search(s)):
            xm = _X_RE.search(s)
            if xm:
                approach_x = abs(float(xm.group(1)))
                modal_x    = approach_x

        # Track modal X (any non-G53 line with X > 0.3 updates modal)
        xm = _X_RE.search(s)
        if xm:
            xv = abs(float(xm.group(1)))
            if xv > 0.3:
                modal_x = xv
                # Fall back: first G00 X in block as approach if G154/G54 had no X
                if approach_x is None and _G00_LINE_RE.search(s):
                    approach_x = xv

        # Track feed/rapid mode
        if _G00_LINE_RE.search(s):
            in_feed = False
        if _FEED_RE.search(s):
            in_feed = True

        # Bore pass = feed move with Z < -0.05" (pure Z-plunge or combined X+Z)
        if in_feed:
            zm = _Z_RE.search(s)
            if zm and float(zm.group(1)) < -0.05:
                pass_z = float(zm.group(1))
                # X on the same line takes priority over modal
                x_on_line = None
                xm2 = _X_RE.search(s)
                if xm2:
                    x2 = abs(float(xm2.group(1)))
                    if x2 > 0.3:
                        x_on_line = x2
                pass_x = x_on_line if x_on_line is not None else modal_x
                if pass_x is not None:
                    # Deduplicate: don't record same X twice in a row
                    if not pass_xs or abs(pass_xs[-1] - pass_x) > 0.001:
                        pass_xs.append(pass_x)
                        pass_zs.append(pass_z)
                        pass_lns.append(line_no)

    # Fall back: use first pass X as approach if no G154/G54 line had an X
    if approach_x is None and pass_xs:
        approach_x = pass_xs[0]

    skip_cb = cb_mm < _RB_CB_SKIP_MM

    # Start check (not applicable when CB < 58mm — tool goes straight to finish bore)
    if skip_cb:
        rb_start_ok = None
    elif approach_x is not None:
        rb_start_ok = approach_x < _RB_FLAG_X   # must be < 2.4"
    else:
        rb_start_ok = None   # not found

    # Step check (requires at least 2 passes)
    rb_steps_ok   = None
    rb_max_step   = None
    rb_violations = []
    if len(pass_xs) >= 2:
        steps = [pass_xs[i + 1] - pass_xs[i] for i in range(len(pass_xs) - 1)]
        rb_max_step   = max(steps)
        rb_violations = [
            (pass_xs[i], pass_xs[i + 1], steps[i])
            for i in range(len(steps))
            if steps[i] > _RB_STEP_LIMIT + 1e-9   # epsilon handles floating-point (e.g. 2.6-2.3=0.3000...027)
        ]
        rb_steps_ok = len(rb_violations) == 0

    # Deep-pass check: passes at or beyond X6.8 must have strictly decreasing Z depth.
    # Once the bore has widened past _RB_DEEP_X the Z plunge must get shallower each pass
    # to avoid overloading the tool.
    # Violations: (x_prev, z_prev, x_curr, z_curr, line_no_1based_of_curr_pass)
    rb_deep_violations = []
    rb_deep_ok         = None
    deep_idxs = [i for i in range(len(pass_xs)) if pass_xs[i] >= _RB_DEEP_X]
    if len(deep_idxs) >= 2:
        for k in range(1, len(deep_idxs)):
            pi, ci = deep_idxs[k - 1], deep_idxs[k]
            x_prev, z_prev = pass_xs[pi], pass_zs[pi]
            x_curr, z_curr = pass_xs[ci], pass_zs[ci]
            ln_curr = pass_lns[ci]
            if abs(z_curr) >= abs(z_prev) - 0.001:
                rb_deep_violations.append((x_prev, z_prev, x_curr, z_curr, ln_curr))
        rb_deep_ok = len(rb_deep_violations) == 0
    elif len(deep_idxs) == 1:
        ci = deep_idxs[0]
        pre_idxs = [i for i in range(len(pass_xs)) if pass_xs[i] < _RB_DEEP_X]
        if pre_idxs:
            pi = pre_idxs[-1]
            x_prev, z_prev = pass_xs[pi], pass_zs[pi]
            x_curr, z_curr = pass_xs[ci], pass_zs[ci]
            ln_curr = pass_lns[ci]
            if abs(z_curr) >= abs(z_prev) - 0.001:
                rb_deep_violations.append((x_prev, z_prev, x_curr, z_curr, ln_curr))
            rb_deep_ok = len(rb_deep_violations) == 0

    return {
        "rb_approach_x":      approach_x,
        "rb_pass_xs":         pass_xs,
        "rb_pass_zs":         pass_zs,
        "rb_pass_lns":        pass_lns,
        "rb_start_ok":        rb_start_ok,
        "rb_steps_ok":        rb_steps_ok,
        "rb_max_step":        rb_max_step,
        "rb_violations":      rb_violations,
        "rb_skip_cb":         skip_cb,
        "rb_deep_ok":         rb_deep_ok,
        "rb_deep_violations": rb_deep_violations,
    }


_IH_Z_MIN    = 0.18   # shallowest Z considered a hub face (not just OD chamfer)
_IH_Z_MAX    = 0.65   # deepest Z for hub-height range (0.65 covers 15MM HC at 0.5906")
_RC_Z_MIN    = 0.27   # shallowest Z for recess depth detection
_RC_Z_MAX    = 0.56   # deepest Z for recess depth (covers large-part 0.50–0.55" recess)


def _find_2pc_hub_op2(post_flip_lines: list) -> tuple:
    """Detect hub height, hub OD, and variable flag for 2PC parts on OP2.

    Some 2PC programs machine a hub on OP2 (T303) that the title doesn't
    explicitly state.  This hub protrudes and slots into the recess of the
    mating piece (Piece A).

    Strategy:
      - Scan T303 post-flip block for G01 feed moves with Z depth in
        [_IH_Z_MIN, _IH_Z_MAX].  Mode Z = hub height.
      - Hub OD = minimum X at the hub-height Z (finish passes are smallest X).
      - VARIABLE flag set when (VARIABLE) or (X IS VARIABLE) comment is found
        in the T303 block (indicates the hub OD X is a placeholder to be set).

    Hub height ranges:
      - Standard 2PC (round ≤ 10.25"): hub height ~0.22–0.27"
      - Large 2PC    (round > 10.25"):  hub height ~0.50"
    Both are covered by [_IH_Z_MIN=0.18, _IH_Z_MAX=0.56].

    Returns (hub_height_in, hub_od_in, hub_is_variable).
    Any component may be None if not detected.
    """
    from collections import Counter

    in_t303         = False
    modal_feed      = False
    modal_z         = 0.0
    modal_x         = None
    hub_is_variable = False
    z_x_hits: list[tuple[float, float]] = []  # (z_abs, x_val) pairs

    for ln in post_flip_lines:
        s = ln.strip()
        if _VARIABLE_RE.search(s):
            hub_is_variable = True
        if _T303_RE.search(s):
            in_t303 = True; modal_feed = False; modal_z = 0.0; modal_x = None; continue
        if not in_t303:
            continue
        if _TOOL_CHG_RE.search(s) and not _T303_RE.search(s):
            break
        if _G53_RE.search(s):
            continue
        if _G00_LINE_RE.search(s):
            modal_feed = False; continue
        if _FEED_RE.search(s):
            modal_feed = True
        xm = _X_RE.search(s)
        if xm:
            xv = abs(float(xm.group(1)))
            if xv > 0.5:
                modal_x = xv
        zm = _Z_RE.search(s)
        if zm:
            modal_z = float(zm.group(1))
        if modal_feed and modal_z < 0 and modal_x is not None:
            z_abs = abs(modal_z)
            if _IH_Z_MIN <= z_abs <= _IH_Z_MAX:
                z_x_hits.append((round(z_abs, 3), modal_x))

    if not z_x_hits:
        return None, None, hub_is_variable

    # Hub height = mode of Z values (finish passes repeat most)
    z_vals = [z for z, x in z_x_hits]
    counts = Counter(z_vals)
    max_cnt = max(counts.values())
    candidates = sorted(z for z, c in counts.items() if c == max_cnt)
    hub_height_in = candidates[0]

    # Hub OD = minimum X at hub-height Z (finish passes land at smallest X)
    hub_xs = [x for z, x in z_x_hits if z == hub_height_in]
    hub_od_in = round(min(hub_xs), 4) if hub_xs else None

    return hub_height_in, hub_od_in, hub_is_variable


def _find_2pc_recess(pre_flip_lines: list) -> tuple:
    """Detect the recess (counterbore) cut in T121 OP1 for 2PC Piece A parts.

    Sequence in the G-code:
      1. Rough bore passes (deep Z, not in RC range)
      2. G00 Z0.2          — retract to safe Z
      3. G00 X<start>      — rapid to chamfer entry diameter
      4. G01 Z0.0          — feed to face
      5. G01 X<rc> Z-<d>   — chamfer cut: BOTH X and Z on same line
                             X<rc> = recess diameter  (RC)
                             Z-<d> = recess depth, in [_RC_Z_MIN, _RC_Z_MAX]

    Primary detection: G01 line with BOTH X and Z where Z ∈ [RC_Z_MIN, RC_Z_MAX].
    The X on *that line* is the RC — analogous to the major CB diameter in STEP.

    Fallback: Z-only plunge in range immediately after a combined X+Z move
    (modal X at that point = RC).  Kept for variant programs.

    When multiple candidates exist, the outermost (largest X) is the recess edge.

    Returns (recess_x_in, recess_z_in) — both positive — or (None, None).
    """
    in_t121     = False
    modal_feed  = False
    modal_x     = None
    prev_was_xz = False
    candidates: list[tuple[float, float]] = []   # (x_in, z_in)

    for ln in pre_flip_lines:
        s = ln.strip()
        if _T121_RE.search(s):
            in_t121 = True; modal_feed = False
            modal_x = None; prev_was_xz = False
            continue
        if not in_t121:
            continue
        if _TOOL_CHG_RE.search(s) and not _T121_RE.search(s):
            break
        if _G53_RE.search(s):
            continue
        if _G00_LINE_RE.search(s):
            modal_feed  = False
            prev_was_xz = False
            xm = _X_RE.search(s)
            if xm:
                xv = abs(float(xm.group(1)))
                if xv > 0.5:
                    modal_x = xv
            continue
        if _FEED_RE.search(s):
            modal_feed = True

        xm = _X_RE.search(s)
        if xm:
            xv = abs(float(xm.group(1)))
            if xv > 0.5:
                modal_x = xv

        if modal_feed:
            zm = _Z_RE.search(s)
            if zm:
                z_abs = abs(float(zm.group(1)))
                if xm is not None:
                    # PRIMARY: combined X+Z chamfer — X on this line IS the RC
                    if _RC_Z_MIN <= z_abs <= _RC_Z_MAX:
                        candidates.append((abs(float(xm.group(1))), z_abs))
                    prev_was_xz = True
                else:
                    # FALLBACK: Z-only plunge right after a chamfer move
                    if _RC_Z_MIN <= z_abs <= _RC_Z_MAX and prev_was_xz and modal_x is not None:
                        candidates.append((modal_x, z_abs))
                    prev_was_xz = False

    if not candidates:
        return None, None
    # Outermost X = recess edge (largest X wins when multiple candidates exist)
    best = max(candidates, key=lambda c: c[0])
    return round(best[0], 4), round(best[1], 3)


def _check_tool_homes(lines: list) -> dict:
    """Verify that G53 tool-home moves appear at every required position.

    Required positions:
      • Before the first T### call in the program
      • Before any T### where the TOOL NUMBER changes
        (same tool / different offset — T101→T121 — does NOT require a home)
      • Before each M00 (program stop for part flip)
      • Before M30 (end of program)

    The G53 before an M00 also satisfies the requirement for the first tool
    of the next operation section (after the flip), so only one G53 is needed
    to cover both the flip stop and the start of OP2.

    Returns:
        th_ok         — True/False, or None when no T-codes are present
        th_violations — list of (line_idx, description) for each missing home
    """
    _TCALL_RE = re.compile(r'\b(T\d{3,4})(?!\d)', re.IGNORECASE)

    def _tnum(code: str) -> int:
        """Tool number = leading digits of T code, dropping last 2 (the offset)."""
        digits = re.sub(r'\D', '', code)
        return int(digits[:-2]) if len(digits) > 2 else int(digits)

    # Build ordered event list: (line_idx, type, data)
    events = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s == "%" or s.startswith("("):
            continue
        if _G53_RE.search(s):
            events.append((i, "G53", None))
        tm = _TCALL_RE.search(s)
        if tm:
            events.append((i, "TOOL", tm.group(1)))
        if _M00_RE.search(s):
            events.append((i, "M00", None))
        if _M30_RE.search(s):
            events.append((i, "M30", None))

    g53_avail    = False
    current_tnum = None
    has_tools    = False
    violations   = []

    for line_idx, etype, edata in events:
        if etype == "G53":
            g53_avail = True

        elif etype == "TOOL":
            has_tools = True
            new_num   = _tnum(edata)
            if current_tnum is None or new_num != current_tnum:
                if not g53_avail:
                    desc = (
                        f"No home before first tool {edata.upper()} (line {line_idx + 1})"
                        if current_tnum is None
                        else f"No home before tool change →{edata.upper()} (line {line_idx + 1})"
                    )
                    violations.append((line_idx, desc))
                g53_avail    = False   # consume — reset requirement for next segment
                current_tnum = new_num
            # Same tool number (offset-only change): no home required, preserve g53_avail

        elif etype == "M00":
            if not g53_avail:
                violations.append((line_idx,
                    f"No home before M00 program stop (line {line_idx + 1})"))
            # Do NOT consume: G53 before M00 also covers start of next operation section

        elif etype == "M30":
            if not g53_avail:
                violations.append((line_idx,
                    f"No home before M30 end of program (line {line_idx + 1})"))
            g53_avail = False

    return {
        "th_ok":        (len(violations) == 0) if has_tools else None,
        "th_violations": violations,
    }


def _check_integer_coords(lines: list) -> list:
    """Return list of (1-based line_no, stripped_line) for any X or Z coordinate
    that has no decimal point (e.g. X3, Z-1).  Z0 is exempt (common retract value).
    Comments are stripped before checking."""
    hits = []
    for i, ln in enumerate(lines):
        s = _INLINE_CMT_RE.sub('', ln).strip()
        if not s or s == '%':
            continue
        for m in _INT_COORD_RE.finditer(s):
            token = m.group(0)
            # Z0 (with or without leading minus) is an accepted retract — skip it
            if token.upper() in ('Z0', 'Z-0'):
                continue
            hits.append((i + 1, ln.rstrip()))
            break  # one hit per line is enough
    return hits


def verify_file(path: str, title: str, o_number: str = None) -> dict:
    """
    Read the G-code file and verify CB/OB against title specs.

    Returns dict with:
      specs           — parsed title values
      flip_found      — bool
      internal_o_number — O-number found on first code line of the file
      o_match         — True/False/None (None if o_number param not provided)
      cb_found_in     — X value found in file (inches) or None
      ob_found_in     — X value found in file (inches) or None
      cb_expected_in  — expected inch value
      ob_expected_in  — expected inch value or None
      cb_ok / ob_ok   — bool (within TOLERANCE_IN)
      cb_diff_in      — signed difference (found - expected) or None
      ob_diff_in      — signed difference or None
      cb_context      — list of (line_no, line_text) around the CB hit
      ob_context      — list of (line_no, line_text) around the OB hit
      error           — str if something went wrong, else absent
    """
    specs = parse_title_specs(title)
    if not specs:
        return {"error": "Title does not contain parseable CB/OB specs"}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return {"error": f"Cannot read file: {exc}"}

    # --- Extract internal O-number from first code line ---
    internal_o_number = None
    for ln in lines:
        s = ln.strip()
        if not s or s == "%":
            continue
        m = _INTERNAL_ONUM_RE.match(s)
        if m:
            internal_o_number = m.group(1).upper()
        break   # only check first non-empty non-% line

    o_match = None
    if o_number and internal_o_number:
        o_match = (internal_o_number.upper() == o_number.upper())

    # --- Split at (FLIP PART) ---
    flip_idx = None
    for i, ln in enumerate(lines):
        if _FLIP_RE.search(ln):
            flip_idx = i
            break

    pre_flip  = lines[:flip_idx] if flip_idx is not None else lines
    post_flip = lines[flip_idx + 1:] if flip_idx is not None else []

    # --- Find G154 work-offset P-codes (OP1 = first in pre-flip, OP2 = first in post-flip) ---
    # If no G154 is used at all and G54/G55 appears → program uses direct work offsets (no P-codes).
    op1_p, op2_p = None, None
    op1_p_hit, op2_p_hit = None, None
    for i, ln in enumerate(pre_flip):
        m = _G154_RE.search(ln)
        if m:
            op1_p = int(m.group(1))
            op1_p_hit = i
            break
    _post_flip_start = (flip_idx + 1) if flip_idx is not None else len(lines)
    for i, ln in enumerate(post_flip):
        m = _G154_RE.search(ln)
        if m:
            op2_p = int(m.group(1))
            op2_p_hit = _post_flip_start + i
            break

    # Detect G54/G55 usage when no G154 was found (mutually exclusive offset systems)
    uses_g5x = False
    if op1_p is None:
        uses_g5x = any(_G5X_RE.search(ln) for ln in lines)

    # --- Find ALL G53 home/retract positions (negative Z) ---
    home_zs_found = []
    home_hit = None
    for i, ln in enumerate(lines):
        m = _HOME_G53_RE.search(ln)
        if m:
            home_zs_found.append(float(m.group(1)))
            if home_hit is None:
                home_hit = i

    # --- Find CB ---
    # Primary: search entire file for explicit CB marker; take X from that line.
    # Fallback: largest X value in the T121 bore tool block (before FLIP PART).
    cb_found_in   = None
    cb_hit_line   = None
    cb_from_marker = False   # True only when (X IS CB) marker was found

    for i, ln in enumerate(lines):
        s = ln.strip()
        if _CB_CMT_RE.search(s):
            xm = _X_RE.search(s)
            if xm:
                cb_found_in    = abs(float(xm.group(1)))
                cb_hit_line    = i
                cb_from_marker = True
            break

    if cb_found_in is None:
        in_t121      = False
        modal_rapid  = True   # default before first explicit G01
        modal_z_t121 = 0.0    # track Z depth; only accept X when Z < 0 (cutting, not face)
        t121_xs      = []     # (line_idx, x_val)
        for i, ln in enumerate(pre_flip):
            s = ln.strip()
            if _T121_RE.search(s):
                in_t121      = True
                modal_rapid  = True
                modal_z_t121 = 0.0
                continue
            if not in_t121:
                continue
            # Stop when the next tool change (non-T121) is encountered
            if _TOOL_CHG_RE.search(s) and not _T121_RE.search(s):
                break
            if _G53_RE.search(s):           # skip machine-coord home lines
                continue
            # Update Z modal (do this before G00 continue so G00 Z lines are tracked)
            zm = _Z_RE.search(s)
            if zm:
                modal_z_t121 = float(zm.group(1))
            # Track modal: G00/G0 → rapid; G01/G02/G03 → feed
            if _G00_LINE_RE.search(s):
                modal_rapid = True
                continue                     # skip the G00 line itself
            if _FEED_RE.search(s):
                modal_rapid = False
            # Skip X values while in rapid modal — those are approach/clearance positions
            if modal_rapid:
                continue
            # Skip X values at Z ≥ 0 — those are face/chamfer-start positions, not bore cuts
            if modal_z_t121 >= 0:
                continue
            xm = _X_RE.search(s)
            if xm:
                t121_xs.append((i, abs(float(xm.group(1)))))
        if t121_xs:
            cb_hit_line, cb_found_in = max(t121_xs, key=lambda p: p[1])

    # Fallback 2: chamfer-into-CB move (G01 X{bore} Z-{small} F{feed}).
    # A small diagonal cut from the face into the bore leaves an X mark at the bore
    # diameter.  Take the largest such X in the pre-flip section (avoids any smaller
    # finishing chamfers deeper in the part).  G53 lines are never chamfers.
    if cb_found_in is None:
        chamfer_hits = []
        for i, ln in enumerate(pre_flip):
            s = ln.strip()
            if _G53_RE.search(s):
                continue
            cm = _CHAMFER_CB_RE.search(s)
            if cm:
                x_val = abs(float(cm.group(1)))
                z_val = abs(float(cm.group(2)))
                if z_val <= _CHAMFER_Z_MAX_IN and x_val > 0.5:
                    chamfer_hits.append((i, x_val))
        if chamfer_hits:
            cb_hit_line, cb_found_in = max(chamfer_hits, key=lambda p: p[1])

    # --- Find CB secondary (actual bore after hub-ring ID mark) ---
    # Some files mark the hub-ring inner diameter as (X IS CB), then step to a
    # smaller X for the actual bearing bore.  If the primary CB fails, we scan
    # up to 20 lines past the marker for the first smaller X that appears in a
    # feed move (before any G00 rapid/retract).  If that value passes it is
    # reported as a loose pass (CB:LOOSE).
    _G00_RE = re.compile(r'^\s*G0+\b', re.IGNORECASE)
    cb2_found_in = None
    cb2_hit_line = None
    if cb_from_marker and cb_found_in is not None and cb_hit_line is not None:
        for i in range(cb_hit_line + 1, min(len(lines), cb_hit_line + 20)):
            s = lines[i].strip()
            if not s:
                continue
            if _G00_RE.match(s) or _T121_RE.search(s) or _FLIP_RE.search(s):
                break
            xm = _X_RE.search(s)
            if xm:
                x2 = abs(float(xm.group(1)))
                if x2 < cb_found_in:
                    cb2_found_in = x2
                    cb2_hit_line = i
                    break

    # --- Find OB ---
    # Search entire file for explicit OB marker; take X from that line.
    # Recognised: (X IS OB), (X IS OD), (X IS HUB), (X CB)
    ob_found_in  = None
    ob_hit_line  = None

    for i, ln in enumerate(lines):
        s = ln.strip()
        if _OB_CMT_RE.search(s):
            if _G00_LINE_RE.search(s):
                # Marker is on a rapid approach line — find actual G01 cutting X nearby.
                for j in range(i + 1, min(len(lines), i + 10)):
                    sj = lines[j].strip()
                    if _G53_RE.search(sj) or _TOOL_CHG_RE.search(sj):
                        break
                    if _FEED_RE.search(sj):
                        xm = _X_RE.search(sj)
                        if xm:
                            ob_found_in = abs(float(xm.group(1)))
                            ob_hit_line = j
                        break
            else:
                xm = _X_RE.search(s)
                if xm:
                    ob_found_in = abs(float(xm.group(1)))
                    ob_hit_line = i
            break

    # --- OB Fallback: T303 post-flip hub-facing pattern ---
    # When no explicit OB marker is found, scan the T303 tool block after (FLIP PART).
    # Hub-facing passes make rough cuts from OD inward, then a finish cut to hub OD.
    # The minimum G01 X value at Z depth < -0.1" within ±0.5" of expected OB is the hub OD.
    # If HC height is known (e.g. "HC 1" → 1.0"), passes run from Z-0.1 to Z-hc_height;
    # the chamfer starts at Z-(hc_height + 0.05) — exclude that zone to avoid chamfer X.
    if ob_found_in is None and specs.get("ob_mm") and flip_idx is not None:
        _ob_exp      = _to_in(specs["ob_mm"] - 0.1)
        hc_h         = specs.get("hc_height_in")   # None if not stated in title
        z_min        = -(hc_h + 0.02) if hc_h else None  # deepest valid hub pass
        in_t303      = False
        modal_feed   = False
        modal_z      = 0.0
        ob_cands     = []
        for j, ln in enumerate(lines[flip_idx + 1:]):
            s = ln.strip()
            if _T303_RE.search(s):
                in_t303    = True
                modal_feed = False
                modal_z    = 0.0
                continue
            if not in_t303:
                continue
            if _TOOL_CHG_RE.search(s) and not _T303_RE.search(s):
                break
            if _G53_RE.search(s):
                continue
            if _G00_LINE_RE.search(s):
                modal_feed = False
                continue
            if _FEED_RE.search(s):
                modal_feed = True
            zm = _Z_RE.search(s)
            if zm:
                modal_z = float(zm.group(1))
            # Accept this line if: feed modal, below face (-0.1"), and not in chamfer zone
            in_hub_z = modal_z < -0.1 and (z_min is None or modal_z >= z_min)
            if modal_feed and in_hub_z:
                xm = _X_RE.search(s)
                if xm:
                    x_val = abs(float(xm.group(1)))
                    if abs(x_val - _ob_exp) <= 0.5:
                        ob_cands.append((flip_idx + 1 + j, x_val))
        if ob_cands:
            ob_hit_line, ob_found_in = min(ob_cands, key=lambda p: p[1])

    # --- Find ALL Drill depths (T101 G83/G81/G01 Z) — may be 2 ops for thick parts ---
    # Primary: G83/G81 canned cycle Z depth.
    # Fallback: if no canned cycle in a T101 block, use the most-negative G01 Z.
    # dr_cycle_types tracks how each depth was found: 'G83', 'G81', or 'G01'.
    dr_depths      = []   # list of Z depths found (negative values)
    dr_hits        = []   # corresponding line indices
    dr_cycle_types = []   # 'G83' | 'G81' | 'G01' for each entry in dr_depths
    in_t101        = False
    t101_g01_z     = None
    t101_g01_hit   = None
    t101_found_cycle = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if _T101_RE.search(s):
            # Flush previous block's G01 fallback if no canned cycle was found
            if in_t101 and not t101_found_cycle and t101_g01_z is not None:
                dr_depths.append(t101_g01_z)
                dr_hits.append(t101_g01_hit)
                dr_cycle_types.append("G01")
            in_t101 = True
            t101_g01_z = None
            t101_g01_hit = None
            t101_found_cycle = False
            continue
        if not in_t101:
            continue
        if _TOOL_CHG_RE.search(s) and not _T101_RE.search(s):
            # End of T101 block — flush G01 fallback if needed
            if not t101_found_cycle and t101_g01_z is not None:
                dr_depths.append(t101_g01_z)
                dr_hits.append(t101_g01_hit)
                dr_cycle_types.append("G01")
            in_t101 = False
            t101_g01_z = None
            t101_g01_hit = None
            t101_found_cycle = False
            continue
        if _G83_RE.search(s):
            zm = _Z_RE.search(s)
            if zm:
                dr_depths.append(float(zm.group(1)))
                dr_hits.append(i)
                dr_cycle_types.append("G83")
                t101_found_cycle = True
                in_t101 = False
        elif _G81_RE.search(s):
            zm = _Z_RE.search(s)
            if zm:
                dr_depths.append(float(zm.group(1)))
                dr_hits.append(i)
                dr_cycle_types.append("G81")
                t101_found_cycle = True
                in_t101 = False
        elif _FEED_RE.search(s) and not t101_found_cycle:
            # G01 fallback: track the most-negative Z in the T101 block
            zm = _Z_RE.search(s)
            if zm:
                z_val = float(zm.group(1))
                if z_val < -0.05 and (t101_g01_z is None or z_val < t101_g01_z):
                    t101_g01_z = z_val
                    t101_g01_hit = i
    # Flush last T101 block if file ended while still in one
    if in_t101 and not t101_found_cycle and t101_g01_z is not None:
        dr_depths.append(t101_g01_z)
        dr_hits.append(t101_g01_hit)
        dr_cycle_types.append("G01")

    # --- Rough bore pass check (T121 pre-flip approach X and step increments) ---
    _rb = _find_rough_bore(pre_flip, specs["cb_mm"])

    # --- Find OD turn-down in BOTH OP1 (pre-flip) and OP2 (post-flip) T303 blocks ---
    _od_rs      = round(specs["round_size_in"] * 4) / 4   # nearest 0.25"
    od_expected = _OD_TABLE.get(_od_rs)
    od_op1_hit, od_op1_found = _find_od_in_block(pre_flip) if od_expected is not None else (None, None)
    od_op2_hit, od_op2_found = (
        _find_od_in_block(post_flip, flip_idx + 1)
        if od_expected is not None and flip_idx is not None
        else (None, None)
    )

    # --- Expected values ---
    cb_mm = specs["cb_mm"]

    # Exception 1: 116.7mm CB is physically cut as 116.9mm (+0.2 offset)
    if abs(cb_mm - 116.7) < 0.001:
        cb_expected_in = _to_in(_CB_116_7_ACTUAL_MM)
    else:
        cb_expected_in = _to_in(cb_mm + 0.1)

    # Exception 2: steel ring CB is anywhere from +0.1 to +0.4mm above title spec
    cb_expected_max_in = _to_in(cb_mm + 0.4) if specs.get("is_steel_ring") else None

    ob_expected_in   = _to_in(specs["ob_mm"] - 0.1) if specs["ob_mm"] else None
    step_expected_in = _to_in(specs["step_mm"] + 0.1) if specs.get("step_mm") else None

    # --- Context snippets (5 lines either side) ---
    def context(hit_line, source_lines=lines, window=5):
        if hit_line is None:
            return []
        start = max(0, hit_line - window)
        end   = min(len(source_lines), hit_line + window + 1)
        return [(start + k + 1, source_lines[start + k].rstrip())
                for k in range(end - start)]

    # --- Total thickness (disc + hub) used for drill, P-codes, home position ---
    disc_thickness = specs.get("length_in")
    hc_height_in   = specs.get("hc_height_in")     # None for non-HC parts

    # Implicit hub detection: 2PC parts sometimes machine a ~0.22–0.27" hub on
    # OP2 that the title doesn't mention.  Detect it from the T303 post-flip Z
    # depths so P-code and home-Z checks use the TRUE total thickness.
    # Also detects hub OD (for pairing with Piece A recess) and VARIABLE flag.
    is_2pc = bool(re.search(r'-*2PC\b', title, re.IGNORECASE))
    implicit_hub_in = None
    hub_od_in       = None
    hub_is_variable = False
    recess_x_in     = None
    recess_z_in     = None
    if is_2pc and flip_idx is not None:
        # HC 2PC: hub height comes from title (hc_height_in), NOT from a T303
        # protrusion.  Calling _find_2pc_hub_op2 on these files incorrectly
        # picks up OD-turn passes (X ≈ 6.9") whose Z depth happens to fall in
        # the hub-height range, producing a bogus HB token.  Skip it entirely.
        if hc_height_in is None:
            implicit_hub_in, hub_od_in, hub_is_variable = _find_2pc_hub_op2(post_flip)
        recess_x_in, recess_z_in = _find_2pc_recess(pre_flip)

    effective_hub   = hc_height_in if hc_height_in is not None else (implicit_hub_in or 0.0)
    total_thickness = (
        (disc_thickness + effective_hub) if disc_thickness is not None else None
    )

    # --- Drill depth verification ---
    # 15MM HC: always -1.15" regardless of total thickness.
    # Single op (total ≤ 4.0"): expected = -(total + 0.15"), each Z never > 4.15".
    # Dual op  (total > 4.0"):  each pass ≤ 4.15", sum of |depths| ≥ total + 0.15".
    # MM-thickness parts: skip drill verification (insufficient data — show NF only).
    _DR_MAX_PASS = 4.15   # maximum individual drill depth (never deeper than this)
    dr_ok        = None
    dr_expected  = None   # for single: target Z; for dual: minimum sum of depths
    dr_note      = None
    _skip_dr = specs.get("length_from_mm", False)   # MM-thickness parts: no DR check yet
    if total_thickness and not _HC_15MM_RE.search(title) and not _skip_dr:
        if total_thickness <= 4.0:
            dr_expected = round(-(total_thickness + 0.15), 3)
            if dr_depths:
                dr_ok = abs(dr_depths[0] - dr_expected) <= DR_TOLERANCE_IN
        else:
            dr_expected = round(total_thickness + 0.15, 3)   # minimum required sum
            if len(dr_depths) >= 2:
                abs_depths = [abs(d) for d in dr_depths[:2]]
                each_ok    = all(d <= _DR_MAX_PASS + DR_TOLERANCE_IN for d in abs_depths)
                sum_ok     = sum(abs_depths) >= dr_expected - DR_TOLERANCE_IN
                dr_ok      = each_ok and sum_ok
                if not each_ok:
                    dr_note = "One or more drill passes exceed max depth of 4.15\""
            elif len(dr_depths) == 1:
                dr_ok   = False
                dr_note = f"Thick part ({total_thickness}\"): only 1 drill found, expected 2"
    elif _HC_15MM_RE.search(title):
        dr_expected = -1.15
        if dr_depths:
            dr_ok = abs(dr_depths[0] - dr_expected) <= DR_TOLERANCE_IN

    # --- G83 peck-cycle requirement (total thickness > 2.50") ---
    # G83 is always acceptable.  G81 or G01 fallback is only acceptable at ≤ 2.50".
    _G83_THRESHOLD = 2.50
    dr_g83_note = None
    if total_thickness and total_thickness > _G83_THRESHOLD and dr_cycle_types:
        bad = [t for t in dr_cycle_types if t != "G83"]
        if bad:
            cycle_str = "/".join(sorted(set(bad)))
            dr_g83_note = f"Part > 2.50\" total — G83 peck required ({cycle_str} found)"
            dr_ok = False
            dr_note = (dr_note + "  " if dr_note else "") + dr_g83_note

    # --- OD turn-down verification (OP1 and OP2 checked separately) ---
    od_op1_ok = None if od_op1_found is None else (
        abs(od_op1_found - od_expected) <= OD_TOLERANCE_IN
    )
    od_op2_ok = None if od_op2_found is None else (
        abs(od_op2_found - od_expected) <= OD_TOLERANCE_IN
    )
    # Overall: PASS if all found values match; FAIL if any found value mismatches
    _od_found_any  = od_op1_found is not None or od_op2_found is not None
    _od_found_fail = od_op1_ok is False or od_op2_ok is False
    od_ok = (not _od_found_fail) if _od_found_any else None

    # --- P-code verification (lathe determined by round size, thickness = total) ---
    pcode_ok       = None
    pcode_lathe    = None
    pcode_expected = None
    pcode_implied  = None
    round_size     = specs.get("round_size_in", 0.0)
    if total_thickness and total_thickness >= 0.20:
        t_key    = round(total_thickness * 4) / 4
        pc_table = _pc_table_for_round(round_size)
        exp_pair = pc_table.get(t_key)
        if op1_p is not None and op2_p is not None:
            pair = (op1_p, op2_p)
            if exp_pair and pair == exp_pair:
                pcode_ok       = True
                pcode_lathe    = _lathe_label_for_round(round_size)
                pcode_expected = exp_pair
            else:
                # Also check alternate Lathe 1 P-codes (12mm→P3/P4, 17mm→P7/P8)
                alt_pair = _LATHE1_ALT_PC.get(t_key) if pc_table is _LATHE1_PC else None
                if alt_pair and pair == alt_pair:
                    pcode_ok       = True
                    pcode_lathe    = _lathe_label_for_round(round_size)
                    pcode_expected = alt_pair
                else:
                    pcode_ok       = False
                    pcode_expected = exp_pair
                    rev = {v: k for k, v in pc_table.items()}
                    pcode_implied  = rev.get(pair)

    # --- Cross-lathe P-code check: if FAIL, see if codes belong to the other lathe ---
    # e.g. a 7.5" part programmed with Lathe 1 P-codes → PC:WARN instead of PC:FAIL
    pcode_wrong_lathe           = False
    pcode_wrong_lathe_label     = None
    pcode_wrong_lathe_thickness = None
    if pcode_ok is False and op1_p is not None and op2_p is not None:
        # Check the table that was NOT used for the primary check
        if round_size <= 6.50:
            other_table = _LATHE23_PC
            other_label = "2/3"
        else:
            other_table = _LATHE1_PC
            other_label = "1"
        other_rev = {v: k for k, v in other_table.items()}
        # Also include alternate Lathe 1 MM P-codes in wrong-lathe detection
        if other_label == "1":
            other_rev = {**{v: k for k, v in _LATHE1_ALT_PC.items()}, **other_rev}
        wrong_thk = other_rev.get((op1_p, op2_p))
        if wrong_thk is not None:
            pcode_wrong_lathe           = True
            pcode_wrong_lathe_label     = other_label
            pcode_wrong_lathe_thickness = wrong_thk

    # --- Home position verification (uses total thickness) ---
    home_ok    = None
    home_z_exp = None
    if total_thickness and total_thickness >= 0.20 and home_zs_found:
        home_z_exp = _home_z_for_thickness(total_thickness)
        if home_z_exp is not None:
            # Each home Z must be >= expected (closer to 0 is OK; more negative than limit fails)
            home_ok = all(z >= home_z_exp for z in home_zs_found)
        # If home_z_exp is None (>5.0"), home_ok stays None (NF — no expected value)

    # --- Feed rate check: no F value should exceed F_MAX (0.02 in/rev) ---
    fr_violations = []   # list of (line_index, f_value)
    fr_max_found  = 0.0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("(") or s == "%":
            continue
        for fm in _F_RE.finditer(s):
            fval = float(fm.group(1))
            if fval > fr_max_found:
                fr_max_found = fval
            if fval > _F_MAX + 1e-9:
                fr_violations.append((i, fval))
    fr_ok = len(fr_violations) == 0 if fr_max_found > 0 else None  # None = no F found

    # --- Z depth limit: no non-G53 line should have Z < -4.15 ---
    _Z_DEEP_LIMIT = -4.15
    z_deep_violations = []   # list of (line_index, z_value)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("(") or s == "%":
            continue
        if _G53_RE.search(s):
            continue  # G53 machine-coord moves are exempt
        zm = _Z_RE.search(s)
        if zm:
            z_val = float(zm.group(1))
            if z_val < _Z_DEEP_LIMIT - 1e-9:
                z_deep_violations.append((i, z_val))

    # --- Integer coordinate check (X/Z values missing decimal point) ---
    int_coord_hits = _check_integer_coords(lines)

    # --- Tool home position check ---
    _th = _check_tool_homes(lines)

    result = {
        "specs":               specs,
        "flip_found":          flip_idx is not None,
        "internal_o_number":   internal_o_number,
        "o_match":             o_match,
        "total_thickness":     total_thickness,
        "implicit_hub_in":     implicit_hub_in,   # detected hub height for 2PC parts (or None)
        "hub_od_in":           hub_od_in,         # detected hub OD for 2PC Piece B (or None)
        "hub_is_variable":     hub_is_variable,   # True if (VARIABLE) comment on hub OD line
        "recess_x_in":         recess_x_in,       # detected recess diameter for 2PC Piece A (or None)
        "recess_z_in":         recess_z_in,       # detected recess depth for 2PC Piece A (or None)
        "cb_found_in":         cb_found_in,
        "cb_from_marker":      cb_from_marker,
        "cb2_found_in":        cb2_found_in,
        "ob_found_in":         ob_found_in,
        "cb_expected_in":      cb_expected_in,
        "cb_expected_max_in":  cb_expected_max_in,
        "ob_expected_in":      ob_expected_in,
        "step_expected_in":    step_expected_in,
        "cb_context":          context(cb_hit_line),
        "cb_context_hit_ln":   cb_hit_line + 1 if cb_hit_line is not None else None,
        "cb2_context":         context(cb2_hit_line),
        "ob_context":          context(ob_hit_line),
        "ob_context_hit_ln":   ob_hit_line + 1 if ob_hit_line is not None else None,
        # P-code
        "op1_p":               op1_p,
        "op2_p":               op2_p,
        "uses_g5x":            uses_g5x,
        "pcode_ok":                    pcode_ok,
        "pcode_lathe":                 pcode_lathe,
        "pcode_expected":              pcode_expected,
        "pcode_implied":               pcode_implied,
        "pcode_wrong_lathe":           pcode_wrong_lathe,
        "pcode_wrong_lathe_label":     pcode_wrong_lathe_label,
        "pcode_wrong_lathe_thickness": pcode_wrong_lathe_thickness,
        "pcode_op1_context":           context(op1_p_hit),
        "pcode_op1_context_hit_ln":    op1_p_hit + 1 if op1_p_hit is not None else None,
        "pcode_op2_context":           context(op2_p_hit),
        "pcode_op2_context_hit_ln":    op2_p_hit + 1 if op2_p_hit is not None else None,
        # Home position
        "home_zs_found":       home_zs_found,
        "home_z_found":        home_zs_found[0] if home_zs_found else None,  # backward compat
        "home_z_expected":     home_z_exp,
        "home_ok":             home_ok,
        "home_context":        context(home_hit),
        "home_context_hit_ln": home_hit + 1 if home_hit is not None else None,
        # Drill depth
        "dr_depths":           dr_depths,
        "dr_cycle_types":      dr_cycle_types,
        "dr_expected":         dr_expected,
        "dr_ok":               dr_ok,
        "dr_note":             dr_note,
        "dr_g83_note":         dr_g83_note,
        "dr_context":          context(dr_hits[0]) if dr_hits else [],
        "dr_context_hit_ln":   dr_hits[0] + 1 if dr_hits else None,
        # Rough bore
        "rb_approach_x":       _rb["rb_approach_x"],
        "rb_pass_xs":          _rb["rb_pass_xs"],
        "rb_pass_zs":          _rb["rb_pass_zs"],
        "rb_pass_lns":         _rb["rb_pass_lns"],
        "rb_start_ok":         _rb["rb_start_ok"],
        "rb_steps_ok":         _rb["rb_steps_ok"],
        "rb_max_step":         _rb["rb_max_step"],
        "rb_violations":       _rb["rb_violations"],
        "rb_skip_cb":          _rb["rb_skip_cb"],
        "rb_deep_ok":          _rb["rb_deep_ok"],
        "rb_deep_violations":  _rb["rb_deep_violations"],
        # OD turn-down
        "od_expected":         od_expected,
        "od_op1_found":        od_op1_found,
        "od_op2_found":        od_op2_found,
        "od_op1_ok":           od_op1_ok,
        "od_op2_ok":           od_op2_ok,
        "od_ok":               od_ok,
        "od_op1_context":         context(od_op1_hit),
        "od_op1_context_hit_ln":  od_op1_hit + 1 if od_op1_hit is not None else None,
        "od_op2_context":         context(od_op2_hit),
        "od_op2_context_hit_ln":  od_op2_hit + 1 if od_op2_hit is not None else None,
        # Feed rate
        "fr_ok":               fr_ok,
        "fr_max":              fr_max_found,
        "fr_violations":       fr_violations,
        "fr_context":          context(fr_violations[0][0]) if fr_violations else [],
        # Z depth limit (non-G53 lines must not exceed Z-4.15)
        "z_deep_ok":           len(z_deep_violations) == 0,
        "z_deep_violations":   z_deep_violations,
        "z_deep_context":      context(z_deep_violations[0][0]) if z_deep_violations else [],
        # Tool home positions
        "th_ok":               _th["th_ok"],
        "th_violations":       _th["th_violations"],
        "th_context":          context(_th["th_violations"][0][0]) if _th["th_violations"] else [],
        # Integer coordinate check
        "int_coord_ok":        len(int_coord_hits) == 0,
        "int_coord_hits":      int_coord_hits,   # list of (line_no, line_text)
    }

    if cb_found_in is not None:
        diff = cb_found_in - cb_expected_in
        result["cb_diff_in"] = diff
        if cb_expected_max_in is not None:
            # Steel ring: accept found anywhere in [cb_expected_in, cb_expected_max_in] ±tolerance
            result["cb_ok"] = (
                cb_found_in >= cb_expected_in - TOLERANCE_IN and
                cb_found_in <= cb_expected_max_in + TOLERANCE_IN
            )
        else:
            result["cb_ok"] = abs(diff) <= TOLERANCE_IN

    if cb2_found_in is not None:
        # For STEP parts: cb2 is the center bore — check against step_expected_in.
        # For non-STEP parts: cb2 is the actual bore when primary marked a hub ring ID.
        if step_expected_in is not None:
            diff = cb2_found_in - step_expected_in
            result["step_diff_in"] = diff
            result["step_ok"]      = abs(diff) <= TOLERANCE_IN
        else:
            diff = cb2_found_in - cb_expected_in
            result["cb2_diff_in"] = diff
            result["cb2_ok"]      = abs(diff) <= TOLERANCE_IN

    if ob_found_in is not None and ob_expected_in is not None:
        diff = ob_found_in - ob_expected_in
        if abs(diff) > 0.5:
            # Marker is for a different feature (e.g., wheel OD turn, not hub bore).
            # Discard so it shows as OB:NF rather than a spurious fail.
            result["ob_found_in"] = None
            result["ob_context"]  = []
        else:
            result["ob_diff_in"] = diff
            result["ob_ok"]      = abs(diff) <= TOLERANCE_IN

    return result


# ---------------------------------------------------------------------------
# Round-size consistency check  (used by import conflict dialog)
# ---------------------------------------------------------------------------

# Maps round size (inches) to an O-number range label and (lo, hi)
_ROUND_TO_O_RANGE = [
    # (round_size_lo, round_size_hi, label, o_number_lo, o_number_hi)
    (5.75,  5.75,  "O50000 – O59999", 50000, 59999),
    (6.00,  6.00,  "O60000 – O62499", 60000, 62499),
    (6.25,  6.25,  "O62500 – O64999", 62500, 64999),
    (6.50,  6.50,  "O65000 – O69999", 65000, 69999),
    (7.00,  7.00,  "O70000 – O74999", 70000, 74999),
    (7.50,  7.50,  "O75000 – O79999", 75000, 79999),
    (8.00,  8.00,  "O80000 – O84999", 80000, 84999),
    (8.50,  8.50,  "O85000 – O89999", 85000, 89999),
    (9.50,  9.50,  "O90000 – O99999", 90000, 99999),
    (10.25, 10.50, "O10000 – O10999", 10000, 10999),
    (13.00, 13.00, "O13000 – O13999", 13000, 13999),
]

_FIX_LATER_RANGE = ("O30000 – O39999", 30000, 39999)


def _round_from_od(od_found: float) -> float | None:
    """Back-calculate the nominal round size from an OD turn measurement."""
    best_rs  = None
    best_diff = 99.0
    for rs, od_exp in _OD_TABLE.items():
        d = abs(od_found - od_exp)
        if d < best_diff:
            best_diff = d
            best_rs   = rs
    return best_rs if best_diff < 0.05 else None


def _o_range_for_round(rs: float) -> tuple | None:
    """Return (label, lo, hi) for the given round size, or None."""
    for lo, hi, label, olo, ohi in _ROUND_TO_O_RANGE:
        if lo - 0.01 <= rs <= hi + 0.01:
            return label, olo, ohi
    return None


def check_file_round_size(path: str, title: str) -> dict:
    """
    Analyse a G-code file for round-size consistency.

    Returns dict with keys:
      title_round   float|None  — round size parsed from title
      od_round      float|None  — round size inferred from OD turn in file
      od_found      float|None  — actual OD diameter found in T303 block
      consistent    bool        — True if title and OD agree (or one is missing)
      conflict_msg  str         — human-readable description of any conflict
      suggested_range  tuple|None  — (label, lo, hi) from title, or OD if title absent
      fix_later_range  tuple       — always _FIX_LATER_RANGE
    """
    # ── 1. Title round size ────────────────────────────────────────────────
    title_round = None
    if title:
        specs = parse_title_specs(title)
        if specs:
            title_round = specs.get("round_size_in")

    # ── 2. OD from file ───────────────────────────────────────────────────
    od_found  = None
    od_round  = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        _, od_val = _find_od_in_block(lines)
        if od_val is not None:
            od_found = od_val
            od_round = _round_from_od(od_found)
    except Exception:
        pass

    # ── 3. Consistency check ───────────────────────────────────────────────
    consistent   = True
    conflict_msg = ""

    if title_round is not None and od_round is not None:
        if abs(title_round - od_round) > 0.13:   # allow ¼" tolerance
            consistent   = False
            conflict_msg = (
                f"Title says {title_round}\u2033 round but OD turn "
                f"({od_found:.3f}\u2033) matches {od_round}\u2033 round."
            )

    # ── 4. Suggested range ────────────────────────────────────────────────
    ref_round = title_round if title_round is not None else od_round
    suggested_range = _o_range_for_round(ref_round) if ref_round else None

    return {
        "title_round":     title_round,
        "od_round":        od_round,
        "od_found":        od_found,
        "consistent":      consistent,
        "conflict_msg":    conflict_msg,
        "suggested_range": suggested_range,
        "fix_later_range": _FIX_LATER_RANGE,
    }


def check_o_range_title_only(title: str, o_number: str,
                             status: str = "") -> tuple[bool, str]:
    """
    Fast title-only range check — no file read required.

    Parses the round size from *title*, looks up the expected O-number range,
    and verifies that *o_number* falls within that range.

    Files with status='shop_special' are always exempt.

    Returns (consistent, message).
      consistent=True  — no conflict detected (or not enough info to decide)
      consistent=False — mismatch confirmed; message explains the conflict
    """
    if (status or "").lower() == "shop_special":
        return True, ""

    if not title or not o_number:
        return True, ""

    specs = parse_title_specs(title)
    if not specs:
        return True, ""

    title_round = specs.get("round_size_in")
    if title_round is None:
        return True, ""

    expected = _o_range_for_round(title_round)
    if expected is None:
        return True, ""   # round size outside our table — can't validate

    label, lo, hi = expected
    try:
        o_val = int(o_number.lstrip("Oo"))
    except (ValueError, AttributeError):
        return True, ""

    if lo <= o_val <= hi:
        return True, ""

    # Identify which range the O-number IS in
    actual_label = "an unrecognised range"
    for rlo, rhi, rlabel, rolo, rohi in _ROUND_TO_O_RANGE:
        if rolo <= o_val <= rohi:
            actual_label = rlabel
            break

    msg = (
        f"{title_round}\u2033 round should be in {label}, "
        f"but {o_number} is in {actual_label}."
    )
    return False, msg
