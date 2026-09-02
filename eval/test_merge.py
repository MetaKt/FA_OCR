"""Phase 04 fixtures, built from hand-made candidates so the right answer is known exactly.

Plan 04 section 6 asks for these as documents. Synthetic candidates are better for the merge
rule itself: a document fixture tests OCR, extraction and merging at once, so a failure does not
say which one broke. These test the merge in isolation, which is what section 2 asks for.

    ../.venv/Scripts/python.exe eval/test_merge.py
"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

import merge as M

passed = failed = 0


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def bill(doc=None, seller=None, total=None, date=None, vat=None):
    return {"originalDocumentNumber": w(doc), "sellerName": w(seller),
            "originalTotal": w(total), "documentDate": w(date), "vat": w(vat)}


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got {got!r}" + ("" if ok else f"  want {want!r}"))


print("multi-01  three receipts on one page -> three candidates, none merged")
out = M.merge_pages([(0, [bill("A1", "ร้านหนึ่ง", 100), bill("B2", "ร้านสอง", 200),
                          bill("C3", "ร้านสาม", 300)])])
check("candidates", len(out), 3)
check("candidateIndex is 0,1,2", [c["candidateIndex"] for c in out], [0, 1, 2])

print("\nmulti-02  one receipt per page across five pages -> five candidates")
out = M.merge_pages([(i, [bill(f"D{i}", f"ร้าน{i}", 100 + i)]) for i in range(5)])
check("candidates", len(out), 5)
check("page indices", [c["chunkPageIndex"] for c in out], [0, 1, 2, 3, 4])

print("\nspan-01   หน้า 1/2 then หน้า 2/2 -> ONE candidate, fields combined")
first = bill("INV-77", "โรงแรมเอ", None, "2026-07-03")     # header, no total yet
second = bill(None, None, 4500.0, None, vat=294.39)         # continuation: totals only
out = M.merge_pages([(1, [first]), (2, [second])],
                    transcripts={1: "ใบแจ้งหนี้ หน้า 1/2", 2: "รวมทั้งสิ้น หน้า 2/2"})
check("candidates", len(out), 1)
check("seller kept from page 1", out[0]["sellerName"]["value"], "โรงแรมเอ")
check("total taken from page 2", out[0]["originalTotal"]["value"], 4500.0)
check("vat taken from page 2", out[0]["vat"]["value"], 294.39)
check("reported on the first page", out[0]["chunkPageIndex"], 1)
check("records both pages", M.spans(out[0]), [1, 2])

print("\nspan-02   same bill, no page marker, two identity fields agree")
out = M.merge_pages([(0, [bill("INV-88", "ร้านสี่", 900, "2026-07-09")]),
                     (1, [bill("INV-88", "ร้านสี่", None, None)])])
check("candidates", len(out), 1)

print("\nno-merge  a marker on both pages but the document numbers differ")
out = M.merge_pages([(0, [bill("INV-01", "ร้านห้า", 100)]),
                     (1, [bill("INV-02", "ร้านหก", 200)])],
                    transcripts={0: "หน้า 1/2", 1: "หน้า 2/2"})
check("a conflict beats the marker", len(out), 2)

print("\nno-merge  only one field agrees -- a shared date is a coincidence, not a bill")
out = M.merge_pages([(0, [bill("INV-03", "ร้านเจ็ด", 100, "2026-07-01")]),
                     (1, [bill("INV-04", None, 250, "2026-07-01")])])
check("one match is not enough", len(out), 2)

print("\nno-merge  two sparse pages that state almost nothing")
out = M.merge_pages([(0, [bill(None, None, None, None)]),
                     (1, [bill(None, None, None, None)])])
check("null == null is not agreement", len(out), 2)

print("\nno-merge  non-adjacent pages")
out = M.merge_pages([(0, [bill("INV-05", "ร้านแปด", 500, "2026-07-02")]),
                     (3, [bill("INV-05", "ร้านแปด", None, None)])])
check("pages 0 and 3 stay apart", len(out), 2)

print("\nposition  only the last candidate of a page can continue")
out = M.merge_pages([(0, [bill("INV-06", "ร้านเก้า", 100, "2026-07-04"),
                          bill("INV-07", "ร้านสิบ", 200, "2026-07-05")]),
                     (1, [bill("INV-06", "ร้านเก้า", None, None)])])
check("does not reach back past the page's last bill", len(out), 3)

print("\nempty-01  no bills anywhere")
check("stays empty", M.merge_pages([(0, []), (1, [])]), [])

print("\nwindow-01 spanning bill straddling a 3-page window boundary")
out = M.merge_pages([(2, [bill("INV-09", "ร้านสิบเอ็ด", None, "2026-07-06")]),
                     (3, [bill(None, None, 7800.0, None)])],
                    transcripts={2: "หน้า 1/2", 3: "หน้า 2/2"})
check("boundary is not special", len(out), 1)
check("total carried across", out[0]["originalTotal"]["value"], 7800.0)

print("\nmarkers")
check("หน้า 1/2", M.page_marker("ใบกำกับภาษี หน้า 1/2"), (1, 2))
check("หน้าที่ 2 จาก 3", M.page_marker("หน้าที่ 2 จาก 3"), (2, 3))
check("Page 1 of 2", M.page_marker("Page 1 of 2"), (1, 2))
check("2/2 alone is not a marker", M.page_marker("จำนวน 2/2 ชิ้น"), None)
check("implausible 5/2 rejected", M.page_marker("หน้า 5/2"), None)
check("no marker", M.page_marker("บิลเงินสด"), None)

print("\nstrip_internal  bookkeeping keys never reach the response")
out = M.merge_pages([(0, [bill("X", "ร้าน", None, "2026-07-01")]),
                     (1, [bill("X", "ร้าน", 50, None)])])
M.strip_internal(out)
check("no leading-underscore keys", [k for k in out[0] if k.startswith("_")], [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
