"""
test_order_search.py — characterization tests for the order-sheet search parser.

Covers real formats (and real typos) sampled from the BRONSON MANUFACTURING
order sheets: column M thickness variants, column K step notation, column J
thickness codes/flags, full-row parsing, and the hard part-type gates in
score_title_match.

Run directly:  python tests/test_order_search.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import order_search_parser as osp

_FAILS = []


def _check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _FAILS.append(label)


def _close(a, b, tol=0.002):
    return a is not None and b is not None and abs(a - b) <= tol


MM = 1.0 / 25.4


# ---------------------------------------------------------------------------
# Column M — thickness (disc_in, hc_in, is_2pc); None entries mean "don't care"
# ---------------------------------------------------------------------------

_M_CASES = [
    # clean formats
    ('2.00"+.50"HUB',            2.00, 0.50, False),
    ('10MM+.50"HUB',          10 * MM, 0.50, False),
    ('11MM+3/8"HUB',          11 * MM, 0.375, False),
    ('1/2"+.50"HUB',             0.50, 0.50, False),
    ('19MM',                  19 * MM, None, False),
    ('1.00"',                    1.00, None, False),
    ('1 7/8"',                   1.875, None, False),
    ('19MM + 0.50" HUB',      19 * MM, 0.50, False),
    ('1.50"+24MM"HUB',           1.50, 24 * MM, False),
    # hand-typed errors (all real rows)
    ('1.50"+.50{"HUB',           1.50, 0.50, False),
    ('2.00+.50HUB',              2.00, 0.50, False),
    ('1.25"+,50"HUB',            1.25, 0.50, False),
    ('1/2"++.50"HUB',            0.50, 0.50, False),
    ('3.00"+..50"HUB',           3.00, 0.50, False),
    ('2.00"+.50:HUB',            2.00, 0.50, False),
    ('15MMM+.50"HUB',         15 * MM, 0.50, False),
    ('19M+.50"HUB',           19 * MM, 0.50, False),
    ('15MM"+.50"HUB',         15 * MM, 0.50, False),
    ('10.6MM"+.50"HUB',     10.6 * MM, 0.50, False),
    ('1/2"+.50"HUBB',            0.50, 0.50, False),
    ('2.00" +. 50" STEEL RING',  2.00, 0.50, False),
    ("3/4'",                     0.75, None, False),
    ('2.50" + 0.50"',            2.50, 0.50, False),
    ('2.50"+.50',                2.50, 0.50, False),
    ('1.50 BOTTOM',              1.50, None, False),
    ('16MM (CR)',             16 * MM, None, False),
    ('1.2" (30.48MM)-CR',        1.20, None, False),
    ('1.00" + .50" HUB (CUT - 1.50")', 1.00, 0.50, False),
    # 2PC piece pairs
    ('2.00" (A+B)',              2.00, None, True),
    ('1.75" (CUT A+A)',          1.75, None, True),
    ('1.50" (A+20MM)',           1.50, None, True),
    ('1.25" (20MM+20MM)',        1.25, None, True),
    ('2.00"+.50"HUB (B+C)',      2.00, 0.50, True),
]


def _test_m():
    print("column M thickness:")
    for raw, disc, hc, is_2pc in _M_CASES:
        got = osp._parse_thickness(raw)
        if got is None:
            _check(repr(raw), False, "returned None")
            continue
        ok = (_close(got["disc_in"], disc)
              and (hc is None) == (got["hc_in"] is None)
              and (hc is None or _close(got["hc_in"], hc))
              and got["is_2pc"] == is_2pc)
        _check(repr(raw), ok, f"got {got}")
    _check("'?' -> None", osp._parse_thickness("?") is None)

    # mixed letter+mm pair: mm piece exact, letter piece = total - mm
    got = osp._parse_thickness('1.50" (A+20MM)')
    _check("(A+20MM) pieces", _close(got["piece_a_in"], 1.50 - 20 * MM)
           and _close(got["piece_b_in"], 20 * MM), f"got {got}")
    # letter-only pair: finished sizes unknown -> None (skip thickness gate)
    got = osp._parse_thickness('2.00" (A+B)')
    _check("(A+B) pieces unknown",
           got["piece_a_in"] is None and got["piece_b_in"] is None, f"got {got}")


# ---------------------------------------------------------------------------
# Column K — CB / STEP (cb, step_cb, depth); all real rows
# ---------------------------------------------------------------------------

_K_CASES = [
    ('106.1',                       106.1, None,  None),
    ('110/74 (.40 DEEP STEP)',      110.0, 74.0,  0.40),
    ('131/74 (75 STEP)',            131.0, 74.0,  0.75),
    ('110 / 107 (STEP 0.4)',        110.0, 107.0, 0.40),
    ('110/74(0.75DEEP)',            110.0, 74.0,  0.75),
    ('131/74 (CONFIRM STEPDOWN)',   131.0, 74.0,  None),
    ('88.7 (CR)/74 (0.50 DEEP)',    88.7,  74.0,  0.50),
    ('106/74 STEP .5" DEEP',        106.0, 74.0,  0.50),
    ('110/74',                      110.0, 74.0,  None),
]


def _test_k():
    print("column K center bore / step:")
    for raw, cb, step_cb, depth in _K_CASES:
        got = osp._parse_cb(raw)
        if got is None:
            _check(repr(raw), False, "returned None")
            continue
        ok = (_close(got["cb_mm"], cb, 0.01)
              and (step_cb is None) == (not got["is_step"])
              and (step_cb is None or _close(got["step_cb_mm"], step_cb, 0.01))
              and (depth is None) == (got["step_depth_in"] is None)
              and (depth is None or _close(got["step_depth_in"], depth, 0.001)))
        _check(repr(raw), ok, f"got {got}")
    got = osp._parse_cb('3 1/16"')     # thickness typed into K — must NOT be a step
    _check("'3 1/16\"' not a step", got is not None and not got["is_step"], f"got {got}")
    _check("'?' -> None", osp._parse_cb("?") is None)


# ---------------------------------------------------------------------------
# Column J — flags and thickness token
# ---------------------------------------------------------------------------

_J_CASES = [
    # (col_j, is_sr, is_2pc, is_1pc, disc_in, has_hub)
    ('6550-1/2H (Spacers)',       False, False, False, 0.50,   True),
    ('8650-8650-CH-SR',           True,  False, False, 1.50,   True),
    ('5550-6550-CH (2PC)',        False, True,  False, 1.50,   True),
    ('3112-4156-E (1pc)',         False, False, True,  2.00,   False),
    ('6550-6550-DH',              False, False, False, 1.75,   True),
    ('6450-5450-H (2PC)',         False, True,  False, 2.75,   False),
    ('8170-8200-IH',              False, False, False, 3.00,   True),
    ('8650-11H (SPACERS)',        False, False, False, 11 * MM, True),
    ('5475-10.6H',                False, False, False, 10.6 * MM, True),
    ('10225-17MM',                False, False, False, 17 * MM, False),
    ('10225-3/4" (SPACERS)',      False, False, False, 0.75,   False),
    ('8650/8170-10MM (SPACERS)',  False, False, False, 10 * MM, False),
    ('5130-8650-E9-84.1 (2PC)',   False, True,  False, 2.00,   False),
    ('4156-6550-E7-131/108-2 (2PC)', False, True, False, 2.00, False),
]


def _test_j():
    print("column J bolt pattern / thickness code:")
    for raw, sr, pc2, pc1, disc, hub in _J_CASES:
        got = osp._parse_j(raw)
        ok = (got["is_sr"] == sr and got["is_2pc"] == pc2 and got["is_1pc"] == pc1
              and ((disc is None and got["disc_in"] is None)
                   or _close(got["disc_in"], disc))
              and got["has_hub"] == hub)
        _check(repr(raw), ok, f"got {got}")

    # two values: first = disc, trailing = hub height ("AH-1.50"" = 1.00"+1.50"HUB)
    got = osp._parse_j('8650-8200-AH-1.50"')
    _check("'AH-1.50\"' disc+hub height", _close(got["disc_in"], 1.00)
           and _close(got["hub_in"], 1.50) and got["has_hub"], f"got {got}")
    got = osp._parse_j('8650-10285-7/8H-1.50"')
    _check("'7/8H-1.50\"' disc+hub height", _close(got["disc_in"], 0.875)
           and _close(got["hub_in"], 1.50), f"got {got}")
    # detached H after a value is a hub marker, not the 2.75" letter
    got = osp._parse_j('8180-1/2-H(SPACER)')
    _check("'1/2-H' hub marker", _close(got["disc_in"], 0.50) and got["has_hub"],
           f"got {got}")
    # K letter = 3.50"
    got = osp._parse_j('8170-8210-KH-1.50"')
    _check("'KH' = 3.50\"", _close(got["disc_in"], 3.50), f"got {got}")
    # 96H is a bore diameter, not a thickness token
    got = osp._parse_j('5450-5450-96H-8')
    _check("'96H' ignored", got["disc_in"] is None, f"got {got}")


# ---------------------------------------------------------------------------
# Full rows — part type determination and fallbacks
# ---------------------------------------------------------------------------

def _test_rows():
    print("full rows:")

    p = osp.parse_order_row('6.5\t6550-1/2H (Spacers)\t106.1\t106\t.50"+.50" Hub')
    _check("HC example row", p is not None and p["part_type"] == "HC"
           and _close(p["cb_mm"], 106.1, 0.01) and _close(p["ob_mm"], 106.0, 0.01)
           and _close(p["disc_in"], 0.50) and _close(p["hc_in"], 0.50)
           and not p["warnings"], f"got {p}")

    p = osp.parse_order_row('8\t8650-8650-CH-SR\t121.3\t121.3\t1.50"+.50"HUB')
    _check("SR row ignores hub/OB", p is not None and p["part_type"] == "SR"
           and p["ob_mm"] is None and p["hc_in"] is None
           and _close(p["disc_in"], 1.50), f"got {p}")

    p = osp.parse_order_row('7.5\t3112-4156-E (1pc)\t57.1\t\t2.00"')
    _check("1pc row is STD", p is not None and p["part_type"] == "STD"
           and p["is_1pc"] and not p["is_2pc"], f"got {p}")

    p = osp.parse_order_row('7\t4137-6550-C (2PC)\t131/74\t\t1.50" (A+20MM)')
    _check("2PC row piece CBs", p is not None and p["part_type"] == "2PC"
           and p["is_2pc"] and _close(p["step_cb_mm"], 74.0, 0.01), f"got {p}")

    p = osp.parse_order_row('9.5\t8170-8200-E\t131/74 (.75 STEP)\t\t2.00"')
    _check("STEP row", p is not None and p["is_step"] and not p["is_2pc"]
           and _close(p["step_depth_in"], 0.75, 0.001), f"got {p}")

    # M unreadable -> thickness from J code, hub assumed, warning emitted
    p = osp.parse_order_row('6.5\t6550-10H (SPACERS)\t78.1\t78.1\t?')
    _check("M fallback to J", p is not None and _close(p["disc_in"], 10 * MM)
           and _close(p["hc_in"], 0.50) and p["warnings"], f"got {p}")

    # J/M thickness conflict -> warning + both values accepted
    p = osp.parse_order_row('7\t6550-6550-CH\t106.1\t95\t1.75"+.50"HUB')
    _check("J/M conflict flagged", p is not None and p["warnings"]
           and _close(p["alt_disc_in"], 1.50), f"got {p}")

    # 4 columns (L omitted entirely) still parses
    p = osp.parse_order_row('10.25\t10225-A (SPACERS)\t170.1\t1.0"')
    _check("4-column row (no L)", p is not None and p["part_type"] == "STD"
           and _close(p["disc_in"], 1.0), f"got {p}")


# ---------------------------------------------------------------------------
# Scoring — hard part-type gates
# ---------------------------------------------------------------------------

_T_HC    = '6.5IN DIA 106.1/106 .5 HC .5'
_T_SR    = '8IN DIA 121.3 1.5 STL HCS-1'
_T_PLAIN = '8IN DIA 121.3MM ID 1.5 THK XX'
_T_STD   = '7.5IN DIA 57.1MM ID 2.0 THK XX'
_T_2PC   = '7.5IN DIA 57.1 2PC 2.0 LUG'
_T_STEP  = '9.5IN DIA 131/74MM STEP 0.75 DEEP ID 2.0 XX'
_T_FLAT9 = '9.5IN DIA 131MM ID 2.0 THK XX'


def _test_scoring():
    print("scoring gates:")

    hc = osp.parse_order_row('6.5\t6550-1/2H (Spacers)\t106.1\t106\t.50"+.50" Hub')
    s, _ = osp.score_title_match(hc, _T_HC)
    _check("HC order vs HC title scores high", s >= 80, f"score {s}")
    s, _ = osp.score_title_match(hc, '6.5IN DIA 106.1MM ID .5 THK XX')
    _check("HC order rejects flat title", s == 0, f"score {s}")

    sr = osp.parse_order_row('8\t8650-8650-CH-SR\t121.3\t121.3\t1.50"+.50"HUB')
    s, _ = osp.score_title_match(sr, _T_SR)
    _check("SR order vs steel-ring title", s >= 80, f"score {s}")
    s, _ = osp.score_title_match(sr, _T_PLAIN)
    _check("SR order rejects non-SR title", s == 0, f"score {s}")

    std = osp.parse_order_row('7.5\t3112-4156-E (1pc)\t57.1\t\t2.00"')
    s, _ = osp.score_title_match(std, _T_STD)
    _check("1pc order vs flat title", s >= 80, f"score {s}")
    s, _ = osp.score_title_match(std, _T_2PC)
    _check("1pc order rejects 2PC title", s == 0, f"score {s}")

    stp = osp.parse_order_row('9.5\t8170-8200-E\t131/74 (.75 STEP)\t\t2.00"')
    s, f = osp.score_title_match(stp, _T_STEP)
    _check("STEP order vs STEP title", s >= 80, f"score {s} fields {f}")
    s, _ = osp.score_title_match(stp, _T_FLAT9)
    _check("STEP order rejects flat title", s == 0, f"score {s}")
    flat9 = osp.parse_order_row('9.5\t8170-8200-E\t131\t\t2.00"')
    s, _ = osp.score_title_match(flat9, _T_STEP)
    _check("flat order rejects STEP title", s == 0, f"score {s}")

    # Thickness is a hard gate: same CB/OB/type but wrong disc must be rejected
    hc10 = osp.parse_order_row('8.5\t8180-10H (SPACERS)\t124.1\t124.1\t10MM+.50"HUB)')
    s, _ = osp.score_title_match(hc10, '8.5IN DIA 124.1/124MM 10MM HC')
    _check("10MM HC order vs 10MM title", s >= 80, f"score {s}")
    s, _ = osp.score_title_match(hc10, '8.5IN 124.1/124.1MM ID 1.5--HC')
    _check("10MM HC order rejects 1.5\" title", s == 0, f"score {s}")

    # Hub height is a hard gate: 1.00" hub order rejects .50" hub files
    hub1 = osp.parse_order_row(
        '10.25\t10225-10225-EH-1.00"\t170.1\t170 (6.58HLB)\t2.00"+1.00"HUB')
    s, _ = osp.score_title_match(hub1, '10.25IN DIA 170.1/170 2.0 HC 1.0')
    _check("1.00\" hub order vs 1.0 hub title", s >= 90, f"score {s}")
    s, _ = osp.score_title_match(hub1, '10.25IN DIA 170.1/170 2.0 HC .5')
    _check("1.00\" hub order rejects .5 hub title", s == 0, f"score {s}")

    # J/M conflict: title matching the J thickness still gets disc credit
    con = osp.parse_order_row('8\t8170-8170-C\t121.3\t\t1.75"')
    s_m, f_m = osp.score_title_match(con, _T_PLAIN)   # title is 1.5 = J value
    _check("conflict accepts J thickness", any('Disc' in x and '✓' in x for x in f_m),
           f"fields {f_m}")


def _main():
    global _FAILS
    _FAILS = []
    _test_m()
    _test_k()
    _test_j()
    _test_rows()
    _test_scoring()
    print("RESULT:", "PASS" if not _FAILS else f"FAIL ({len(_FAILS)}: {_FAILS})")
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    raise SystemExit(_main())
