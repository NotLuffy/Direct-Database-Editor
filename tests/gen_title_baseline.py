"""
gen_title_baseline.py — (re)generate tests/title_baseline.json from _baseline.json.

`_baseline.json` is an untracked dev snapshot mapping
    program_title -> [cb_mm, ob_mm, thickness_in]
captured from an OLDER parser.  It is mostly correct (~6300/6365 titles) but has
one confirmed-wrong family: titles where the centerbore is written in inches before
the slash, e.g.

    "13.0 5.25IN/220MM 1.5 HC .5"

The old snapshot recorded cb=220 / thickness=5.25 (it put the OB into the CB slot
and the inch CB value into the thickness slot).  The CORRECT reading — confirmed by
the shop — is:
    5.25" is the centerbore  -> 133.35 mm  (cut +0.1mm = 133.45 -> X5.2539")
    220   is the outer bore  -> ob = 220 mm
    1.5   is the disc thickness
The current parser already produces this.  So for exactly this family we trust the
parser and write the corrected values into the fixture; everything else is copied
from the snapshot unchanged.

This generator is provenance only.  `_baseline.json` is gitignored, so the COMMITTED
source of truth is the produced tests/title_baseline.json.  Re-run only when the
snapshot is refreshed.
"""

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import verifier as v

_SNAPSHOT = os.path.join(_ROOT, "_baseline.json")
_FIXTURE = os.path.join(_HERE, "title_baseline.json")

# An inch value (<= 12") written immediately before the '/' separator — the parser
# converts these to mm (value * 25.4).  Used to detect the confirmed inch-CB family.
_INCH_BEFORE_SLASH = re.compile(r'(\d+(?:\.\d+)?)\s*(?:IN\b|")\s*/', re.IGNORECASE)

_TOL_MM = 0.05
_TOL_IN = 0.02


def _disagrees(specs, cb, ob, th):
    if specs is None:
        return cb is not None or ob is not None or th is not None
    if cb is not None and abs((specs["cb_mm"] or -9999) - cb) > _TOL_MM:
        return True
    if ob is not None and ob > 0 and (specs["ob_mm"] is None or abs(specs["ob_mm"] - ob) > _TOL_MM):
        return True
    if th is not None and th > 0 and (specs["length_in"] is None or abs(specs["length_in"] - th) > _TOL_IN):
        return True
    return False


def _is_inch_cb_family(title, specs):
    """True when the parser converted a plausible inch CB (<=12") before the slash."""
    if not specs or not specs["cb_mm"]:
        return False
    for m in _INCH_BEFORE_SLASH.finditer(title):
        iv = float(m.group(1))
        if iv <= 12 and abs(specs["cb_mm"] - iv * 25.4) < 0.05:
            return True
    return False


def main():
    snapshot = json.load(open(_SNAPSHOT, encoding="utf-8"))
    corrected = {}
    n_trusted = 0
    for title, (cb, ob, th) in snapshot.items():
        specs = v.parse_title_specs(title)
        if _disagrees(specs, cb, ob, th) and _is_inch_cb_family(title, specs):
            corrected[title] = [specs["cb_mm"], specs["ob_mm"], specs["length_in"]]
            n_trusted += 1
        else:
            corrected[title] = [cb, ob, th]
    json.dump(corrected, open(_FIXTURE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"wrote {_FIXTURE}: {len(corrected)} titles "
          f"({n_trusted} corrected to parser values for the inch-CB family)")


if __name__ == "__main__":
    main()
