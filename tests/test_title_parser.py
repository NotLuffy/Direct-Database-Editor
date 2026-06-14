"""
test_title_parser.py — regression ratchet for verifier.parse_title_specs().

Asserts the parser still produces the expected centerbore / outer-bore / thickness
for every title in tests/title_baseline.json (a corrected snapshot of ~6365 real
program titles — see gen_title_baseline.py for provenance).

The point of this test is that the failure count can only go DOWN, never up:
  - A title that currently parses correctly but stops → FAIL (a regression).
  - A "shop special" (intentionally non-conventional title that parses to None)
    that suddenly DOES parse → the shop-special guard fails, telling you to remove
    it from the fixture's null set. Either direction forces a deliberate update.

Runs standalone (`python tests/test_title_parser.py`, no pytest needed) and is also
collected by pytest (functions named test_*).
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import verifier as v

_FIXTURE = os.path.join(_HERE, "title_baseline.json")

# Acceptance windows (match gen_title_baseline.py)
TOL_MM = 0.05   # centerbore / outer-bore, in millimetres
TOL_IN = 0.02   # disc thickness, in inches

# Shop-special programs: intentionally non-conventional titles that the parser is
# NOT expected to understand (one-off holders, "13 TO 8.5" transition parts, etc.).
# They are stored in the fixture as [null, null, null] and must parse to None.
# This set may SHRINK as conventions expand — never grow.
KNOWN_SHOP_SPECIAL = {
    "13.0 13 TO 8.5;",
    "5.75$ IN DIA 2.25MM ID 1.50 XX",
    "7.0 IN DIA hub 71.5MM .75 HC",
    "7.5 IN STEP  CUSTOM",
    "8.5 IN  1.50 THK PART HOLDER",
    "8.5 OD PART HOLDER",
    "9.5IN$ 9.5 TO 7",
    "CUSTOM 20MM  HC SPACER 74MM HUB - 72MM WHL",
}


def _load():
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _check(title, cb, ob, th):
    """Return a list of mismatch strings for one title (empty == pass)."""
    specs = v.parse_title_specs(title)
    is_special = cb is None and ob is None and th is None
    if is_special:
        if specs is not None:
            return [f"{title!r}: expected shop-special (None) but parsed {specs['cb_mm']}/"
                    f"{specs['ob_mm']}/{specs['length_in']}"]
        return []
    if specs is None:
        return [f"{title!r}: parser returned None, expected cb={cb} ob={ob} th={th}"]
    out = []
    if cb is not None and abs((specs["cb_mm"] or -9999) - cb) > TOL_MM:
        out.append(f"{title!r}: CB got {specs['cb_mm']} want {cb}")
    if ob is not None and ob > 0 and (specs["ob_mm"] is None or abs(specs["ob_mm"] - ob) > TOL_MM):
        out.append(f"{title!r}: OB got {specs['ob_mm']} want {ob}")
    if th is not None and th > 0 and (specs["length_in"] is None or abs(specs["length_in"] - th) > TOL_IN):
        out.append(f"{title!r}: thickness got {specs['length_in']} want {th}")
    return out


def test_parser_matches_baseline():
    """Every fixture title parses to its expected CB / OB / thickness."""
    data = _load()
    failures = []
    for title, (cb, ob, th) in data.items():
        failures.extend(_check(title, cb, ob, th))
    assert not failures, (
        f"{len(failures)} title-parser regression(s):\n  "
        + "\n  ".join(failures[:60])
        + ("\n  ..." if len(failures) > 60 else "")
    )


def test_shop_special_set_unchanged():
    """The fixture's null (shop-special) set must exactly match KNOWN_SHOP_SPECIAL.

    If this fails because a title now parses, the parser improved — remove it from
    both the fixture and KNOWN_SHOP_SPECIAL (regenerate the fixture). If a NEW null
    appears, a title silently stopped parsing — that's a regression."""
    data = _load()
    fixture_nulls = {t for t, (cb, ob, th) in data.items()
                     if cb is None and ob is None and th is None}
    assert fixture_nulls == KNOWN_SHOP_SPECIAL, (
        "shop-special set drifted from the fixture:\n"
        f"  only in fixture: {sorted(fixture_nulls - KNOWN_SHOP_SPECIAL)}\n"
        f"  only in test:    {sorted(KNOWN_SHOP_SPECIAL - fixture_nulls)}"
    )


def _main():
    data = _load()
    failures = []
    for title, (cb, ob, th) in data.items():
        failures.extend(_check(title, cb, ob, th))
    nulls = {t for t, (cb, ob, th) in data.items()
             if cb is None and ob is None and th is None}
    drift = nulls ^ KNOWN_SHOP_SPECIAL

    print(f"titles checked      : {len(data)}")
    print(f"shop-specials (None): {len(nulls)}")
    print(f"parser regressions  : {len(failures)}")
    for f in failures[:60]:
        print("  -", f)
    if drift:
        print(f"shop-special drift  : {sorted(drift)}")
    ok = not failures and not drift
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
