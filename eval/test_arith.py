"""Fixtures for src/arith.py -- the money equations a Thai bill has to satisfy.

The corpus here is not synthetic. Every "clean" case below is a candidate this pipeline actually
returned on a real document, taken from the saved live responses, and every one of them must pass
untouched: a guard that fires on a correct bill trains the reviewer to ignore it.

The two failing cases are also real -- the ใบรับเงิน whose gross lost a digit, and the toll
ticket that put 80 baht of VAT on a 35 baht base.

    ../.venv/Scripts/python.exe eval/test_arith.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import arith

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<56} got {got!r}" + ("" if ok else f"  want {want!r}"))


def bill(total=None, base=None, vat=None, wht=None, conf=0.9):
    def cell(v):
        return {"value": v, "confidence": conf}
    return {"originalTotal": cell(total), "amountBeforeVat": cell(base),
            "vat": cell(vat), "withholdingTax": cell(wht)}


def codes(candidate):
    return [c for c, _, _ in arith.check(candidate)]


print("clean-01  the two real bills from the live 5-page response")
# 4683.00 + 327.81 = 5010.81, vat is exactly 7%, wht is exactly 3%.
check("5010.81 bill", codes(bill(5010.81, 4683.00, 327.81, 140.49)), [])
check("1712.00 bill", codes(bill(1712.00, 1600.00, 112.00, 48.00)), [])

print("\nclean-02  a document that states only a total says nothing to contradict")
# Six of the seven toll candidates look like this. No base, no vat -- no equations, no findings.
check("toll ticket, total only", codes(bill(25.00)), [])
check("nothing at all", codes(bill()), [])

print("\nclean-03  vat = 0 is a non-VAT document, not a missing number")
# กรมทางหลวง prints "Baht(Non Vat)". Page 199 came back exactly like this and is correct.
check("total 30, base 30, vat 0", codes(bill(30.00, 30.00, 0.00)), [])

print("\nclean-04  satang rounding does not trip the tolerance")
check("7% of 1234.57 rounded", codes(bill(1321.00, 1234.57, 86.42)), [])

print("\nfail-01  the ใบรับเงิน digit error -- what this guard was built for")
# Paper says gross 24,226.80; stage 1 read 24,826.80. 726.80 / 24,826.80 is 2.93%, and there is
# no 2.93% withholding rate in Thai law. The number looks perfectly ordinary on its own.
got = arith.check(bill(24826.80, 24826.80, None, 726.80))
check("caught", [c for c, _, _ in got], ["WHT_RATE"])
print(f"        {got[0][1]}")
check("the true reading passes", codes(bill(24226.80, 24226.80, None, 726.80)), [])

print("\nfail-02  the toll ticket with 80 baht of VAT on a 35 baht base")
# Real: live-toll-2026-09-01.json candidate 6. The note already calls this "ยอดถูก ช่องผิด";
# now it is detected instead of noticed by a human.
got = arith.check(bill(115.00, 35.00, 80.00))
check("caught", [c for c, _, _ in got], ["VAT_RATE"])
print(f"        {got[0][1]}")

print("\nfail-03  a broken sum is caught even when both rates look plausible")
check("base + vat != total", [c for c, _, _ in arith.check(bill(2000.00, 1000.00, 70.00))],
      ["VAT_SUM"])

print("\nfail-04  withholding taken on the VAT-inclusive total instead of the subtotal")
# A real and easy mistake: 3% of 5010.81 is 150.32, but the law says 3% of 4683.00 = 140.49.
check("caught", [c for c, _, _ in arith.check(bill(5010.81, 4683.00, 327.81, 150.32))],
      ["WHT_RATE"])

print("\napply-01  a finding lowers confidence on exactly the fields it names")
c = bill(24826.80, 24826.80, None, 726.80, conf=0.95)
c["candidateIndex"] = 0
report = arith.apply([c])
check("one candidate reported", len(report), 1)
check("withholdingTax distrusted", c["withholdingTax"]["confidence"], arith.SUSPECT_CONFIDENCE)
check("amountBeforeVat distrusted", c["amountBeforeVat"]["confidence"], arith.SUSPECT_CONFIDENCE)
check("originalTotal untouched -- the equation did not involve it",
      c["originalTotal"]["confidence"], 0.95)

print("\napply-02  confidence is only ever lowered, never raised")
c = bill(5010.81, 4683.00, 327.81, 140.49, conf=0.10)
check("clean bill keeps its low confidence", (arith.apply([c]), c["vat"]["confidence"]), ([], 0.10))

print("\napply-03  a clean chunk produces an empty report and touches nothing")
cands = [bill(5010.81, 4683.00, 327.81, 140.49), bill(25.00), bill(30.00, 30.00, 0.00)]
check("no findings", arith.apply(cands), [])
check("confidences intact", [c["originalTotal"]["confidence"] for c in cands], [0.9, 0.9, 0.9])

print("\nshape-01  malformed input never raises")
check("no cells at all", arith.check({}), [])
check("value is a string", arith.check({"originalTotal": {"value": "x", "confidence": 1}}), [])
check("cell is not a dict", arith.check({"vat": None, "amountBeforeVat": 5}), [])
check("apply on empty", arith.apply([]), [])
check("apply on None", arith.apply(None), [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
