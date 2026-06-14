"""
test_verifier_checks.py — characterization tests for the core verifier checks.

Builds one realistic synthetic part that passes every check, then perturbs a single
value at a time to confirm each check actually discriminates pass from fail:

    CB  center bore        OB  outer bore         DR  drill depth
    OD  OD turn-down       PC  P-code offsets      HM  home Z
    RB  rough bore steps   TZ  turning Z-depth

Same approach as test_tz_depth.py. Runs standalone (`python tests/test_verifier_checks.py`)
and under pytest.

The reference part is:
    "13.0 124/220MM 2.0 HC .5"  (round 13.0", CB 124mm, OB 220mm, disc 2.0" + 0.5" hub)
    -> total thickness 2.5"
    -> CB X4.8858  OB X8.6575  OD X12.901  drill Z-2.65  home Z-13  P17/P18  TZ -1.30/side
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import verifier as v

TITLE = "13.0 124/220MM 2.0 HC .5"

# Reference (passing) values
CB_X, OB_X, OD_X = 4.8858, 8.6575, 12.901


def _rough_passes(approach, target, z, rough_f, step):
    """Stepping bore passes from approach diameter out to just under the CB."""
    out = [f"G00 X{approach} Z0.1"]
    x = approach
    while x < target - step:
        x = round(x + step, 4)
        out.append(f"G01 X{x} Z{z} F{rough_f}")
    return out


def build(cb_x=CB_X, ob_x=OB_X, od_x=OD_X, drill="-2.65", drill_cycle="G83",
          home="-13.0", tz1="-1.30", tz2="-1.30", p1=17, p2=18,
          rough_step=0.30, rough_f="0.020", finish_f="0.015"):
    """Write a synthetic part to a temp file and return its path.

    Defaults produce a part that passes every check; pass a kwarg to perturb one
    value and drive a specific check to FAIL."""
    L = ["O12345", f"({TITLE})", "(OP1)", f"G53 Z{home}"]
    L += ["T101 (DRILL)", f"{drill_cycle} Z{drill} F0.008"]
    L += ["T121 (BORE)", f"G154 P{p1} X2.3"]
    L += _rough_passes(2.3, cb_x, "-1.9", rough_f, rough_step)
    L += [f"G01 X{cb_x} Z-1.9 F{finish_f} (X IS CB)"]
    L += ["T303 (OD TURN)", f"G00 X{od_x} Z0.1", f"G01 Z{tz1} F0.01"]
    L += ["(FLIP PART)", "(OP2)", f"G53 Z{home}"]
    L += ["T121 (BORE OP2)", f"G154 P{p2} X8.0",
          f"G00 X{ob_x} Z0.1", f"G01 X{ob_x} Z-0.5 F0.012 (X IS OB)"]
    L += ["T303 (OD TURN OP2)", f"G00 X{od_x} Z0.1", f"G01 Z{tz2} F0.01", "M30"]
    path = os.path.join(tempfile.gettempdir(), "verifier_check_part.nc")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


def _verify(**kw):
    return v.verify_file(build(**kw), TITLE)


_SCORED = ["cb_ok", "ob_ok", "dr_ok", "rb_ok", "od_ok", "tz_ok", "pcode_ok", "home_ok"]


def test_reference_part_passes_all():
    r = _verify()
    failed = [k for k in _SCORED if r.get(k) is not True]
    assert not failed, f"reference part should pass every check, got non-True: {failed}"


# (check key, kwarg perturbation that should make it FAIL)
_FAIL_CASES = [
    ("cb_ok",    dict(cb_x=4.90)),       # +0.014" off the CB target
    ("ob_ok",    dict(ob_x=8.70)),       # +0.04" off the OB target
    ("dr_ok",    dict(drill="-2.00")),   # 0.65" too shallow
    ("od_ok",    dict(od_x=12.96)),      # +0.06" off the OD turn target
    ("pcode_ok", dict(p1=99)),           # wrong OP1 P-code
    ("home_ok",  dict(home="-15.0")),    # home Z deeper than the -13 limit for a 2.5" part
    ("rb_ok",    dict(rough_step=0.60)), # bore passes step >0.3"
    ("tz_ok",    dict(tz1="-3.50")),     # one side past the -3.25" cap
]


def test_each_check_can_fail():
    failures = []
    for key, kw in _FAIL_CASES:
        got = _verify(**kw).get(key)
        if got is not False:
            failures.append(f"{key} with {kw}: expected False, got {got}")
    assert not failures, "checks that did not fail as expected:\n  " + "\n  ".join(failures)


def _main():
    r = _verify()
    bad = 0
    print("reference part:")
    for k in _SCORED:
        ok = r.get(k) is True
        bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {k} = {r.get(k)}")
    print("perturbations (each should FAIL its check):")
    for key, kw in _FAIL_CASES:
        got = _verify(**kw).get(key)
        ok = got is False
        bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {key} -> {got}  ({kw})")
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(_main())
