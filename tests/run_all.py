"""
run_all.py — run every test module in tests/ without pytest.

Each test_*.py exposes a `_main()` that returns 0 on pass, non-zero on fail.
Usage:  python tests/run_all.py
"""

import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_MODULES = [
    "test_title_parser",
    "test_tz_depth",
    "test_verifier_checks",
]


def main():
    rc = 0
    for name in _MODULES:
        print(f"\n========== {name} ==========")
        mod = importlib.import_module(name)
        rc |= mod._main()
    print("\n==============================")
    print("OVERALL:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
