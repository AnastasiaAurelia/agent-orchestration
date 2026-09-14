"""Self-contained verification. Exits non-zero when calc.py is wrong."""
import sys

import calc

failures = []
if calc.add(2, 3) != 5:
    failures.append(f"add(2, 3) == {calc.add(2, 3)}, expected 5")
if calc.add(-1, 1) != 0:
    failures.append(f"add(-1, 1) == {calc.add(-1, 1)}, expected 0")
if calc.multiply(3, 4) != 12:
    failures.append(f"multiply(3, 4) == {calc.multiply(3, 4)}, expected 12")

if failures:
    for f in failures:
        print("FAIL", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
