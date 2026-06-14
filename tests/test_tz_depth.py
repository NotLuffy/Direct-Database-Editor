"""
test_tz_depth.py — turning Z-depth (TZ) acceptance logic.

Covers the two acceptance paths defined in TZ_SPEC.md:
  - Primary   : balanced split, each side within the table limit.
  - Secondary : asymmetric two-sided split for thick parts the table can't reach
                (per-side cap −3.25" + OP1/OP2 must cover the full thickness).

Builds tiny synthetic G-code files and asserts verify_file()'s tz_ok. Runs
standalone (`python tests/test_tz_depth.py`) and under pytest.
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import verifier as v


def _gcode(title, op1_z, op2_z, two_sided=True):
    body = ["O12345", "(" + title + ")", "T303 (OD TURN OP1)", "G00 X13.0 Z0.1"]
    if op1_z is not None:
        body.append(f"G01 Z{op1_z} F0.01")
    body.append("G00 Z0.5")
    if two_sided:
        body += ["(FLIP PART)", "T303 (OD TURN OP2)", "G00 X13.0 Z0.1"]
        if op2_z is not None:
            body.append(f"G01 Z{op2_z} F0.01")
        body.append("G00 Z0.5")
    body.append("M30")
    path = os.path.join(tempfile.gettempdir(), "tz_test.nc")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")
    return path


def _tz_ok(title, op1_z, op2_z, two_sided=True):
    return v.verify_file(_gcode(title, op1_z, op2_z, two_sided), title)["tz_ok"]


# (name, title, op1_z, op2_z, two_sided, expected_tz_ok)
_CASES = [
    ("balanced split passes (primary)",      "13.0 124/220MM 2.0", -1.05, -1.05, True,  True),
    ("asymmetric 3.25/2.85 passes (sec)",    "13.0 124/220MM 6.0", -3.25, -2.85, True,  True),
    ("uncut middle band fails",              "13.0 124/220MM 6.0", -3.25, -2.00, True,  False),
    ("side past -3.25 cap fails",            "13.0 124/220MM 6.0", -3.50, -2.85, True,  False),
    ("single side over table fails",         "13.0 124/220MM 2.0", -1.50, None,  False, False),
]


def test_tz_acceptance_paths():
    failures = []
    for name, title, a, b, two, expect in _CASES:
        got = _tz_ok(title, a, b, two)
        if got is not expect:
            failures.append(f"{name}: tz_ok={got} expected {expect}")
    assert not failures, "TZ regressions:\n  " + "\n  ".join(failures)


def _main():
    bad = 0
    for name, title, a, b, two, expect in _CASES:
        got = _tz_ok(title, a, b, two)
        ok = got is expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:34} tz_ok={got} (want {expect})")
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(_main())
