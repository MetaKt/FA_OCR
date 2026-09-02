"""Fixtures for src/regions.py -- the page span a bill covers, and the boxes that report it.

Everything here is synthetic and CPU-only: no OCR, no model, no server. That is not a shortcut,
it is the point. The webapp team's bug was never about reading a document; it was about throwing
away a page list we had already computed, so the fixtures are page lists.

The three checks their handover asks for in §6.2 are the last block: every candidate has at least
one region, its own `chunkPageIndex` appears in its regions, and the union covers the chunk.

    ../.venv/Scripts/python.exe eval/test_regions.py
"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

import certlink
import merge as M
import regions as R

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<54} got {got!r}" + ("" if ok else f"  want {want!r}"))


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def bill(doc=None, seller=None, total=None, date=None, vat=None, base=None, tax_id=None):
    return {"originalDocumentNumber": w(doc), "sellerName": w(seller),
            "originalTotal": w(total), "documentDate": w(date), "vat": w(vat),
            "amountBeforeVat": w(base), "sellerTaxId": w(tax_id),
            "withholdingTax": w(None), "withholdingTaxRate": w(None)}


def pages_of(candidate):
    return [r["chunkPageIndex"] for r in candidate.get("regions", [])]


print("span-01  an unmerged candidate owns its own page")
check("page_span", R.page_span({"chunkPageIndex": 2}), [2])
check("spans is still empty", M.spans({"chunkPageIndex": 2}), [])

print("\nspan-02  a candidate with no page at all owns nothing, and says so")
check("page_span", R.page_span({}), [])

print("\nspan-03  widen is idempotent and keeps the span sorted and unique")
c = {"chunkPageIndex": 1}
R.widen(c, 3)
R.widen(c, 2)
R.widen(c, 3)
check("span", R.page_span(c), [1, 2, 3])

print("\nbox-01  every box is the whole page, integers 0-1000")
check("whole_page", R.whole_page(4),
      {"chunkPageIndex": 4, "xMin": 0, "yMin": 0, "xMax": 1000, "yMax": 1000})

print("\nbox-02  attach_regions rebuilds from the span rather than trusting what came in")
c = {"chunkPageIndex": 0, "_pages": [0, 1], "regions": [R.whole_page(9)]}
R.attach_regions([c])
check("pages", pages_of(c), [0, 1])
check("evidence key present and empty", c["evidence"], [])

print("\nbox-03  no page means no regions key -- a loud schema failure, not a fabricated page 0")
c = {}
R.attach_regions([c])
check("regions absent", "regions" in c, False)

# --------------------------------------------------------------------------- the real shapes

print("\nmerge-01  a bill spanning pages 0-2 reports all three pages")
# A continuation page carries the document number and the seller and little else; the totals
# land on the last page. Two agreements and no conflict, which is what merge_pages requires.
out = M.merge_pages([(0, [bill("A1", "บริษัท ดีครับผม จำกัด", None, "2026-07-09")]),
                     (1, [bill("A1", "บริษัท ดีครับผม จำกัด")]),
                     (2, [bill("A1", "บริษัท ดีครับผม จำกัด", 5010.81)])])
check("one candidate", len(out), 1)
R.attach_regions(out)
check("pages", pages_of(out[0]), [0, 1, 2])
check("its own chunkPageIndex is inside", out[0]["chunkPageIndex"] in pages_of(out[0]), True)

print("\ncert-01  a folded certificate hands its page to the bill instead of vanishing")
# The receipt on page 0, its 50 ทวิ certificate on page 1. Only the certificate states the tax.
receipt = bill("047275", "บริษัท สยามสตีล กัลวาไนซิ่ง จำกัด", 1712.0, "2026-07-13", 112.0,
               base=1600.0, tax_id="0105634103494")   # checksum-valid, not TEAM
cert = bill(None, "ผู้ถูกหักภาษี ณ ที่จ่าย", None, None)
merged = M.merge_pages([(0, [receipt]), (1, [cert])])
check("two candidates before certlink", len(merged), 2)

transcripts = {
    0: "บริษัท สยามสตีล กัลวาไนซิ่ง จำกัด 1,600.00 112.00 1,712.00",
    1: ("หนังสือรับรองการหักภาษี ณ ที่จ่าย ตามมาตรา 50 ทวิ ผู้มีหน้าที่หักภาษี ณ ที่จ่าย "
        "เลขประจำตัวผู้เสียภาษีอากร 0107561000030 ผู้ถูกหักภาษี ณ ที่จ่าย 0105634103494 "
        "จำนวนเงินที่จ่าย 1,600.00 ภาษีที่หักและนำส่งไว้ 48.00"),
}
kept, report = certlink.apply_certificates(merged, transcripts)
check("one candidate after certlink", len(kept), 1)
check("the certificate attached", [r["attached"] for r in report], [True])
check("withholdingTax crossed over", kept[0]["withholdingTax"]["value"], 48.0)
R.attach_regions(kept)
check("the certificate's page came with it", pages_of(kept[0]), [0, 1])

print("\norphan-01  a page with no candidate joins the nearest preceding bill")
out = [{"chunkPageIndex": 0}, {"chunkPageIndex": 3}]
R.attach_orphans(out, 5)
R.attach_regions(out)
check("bill 0 takes pages 1, 2", pages_of(out[0]), [0, 1, 2])
check("bill 1 takes page 4", pages_of(out[1]), [3, 4])

print("\norphan-02  pages before the first bill go to the first bill")
out = [{"chunkPageIndex": 2}]
R.attach_orphans(out, 4)
R.attach_regions(out)
check("all four pages", pages_of(out[0]), [0, 1, 2, 3])

print("\norphan-03  a chunk with no candidate at all is left alone, not crashed")
check("empty in, empty out", R.attach_orphans([], 3), [])

print("\norphan-04  an owned page is never stolen, and an orphan follows the page before it")
out = [{"chunkPageIndex": 0, "_pages": [0, 2]}, {"chunkPageIndex": 1}]
R.attach_orphans(out, 4)
R.attach_regions(out)
check("bill 1 keeps its own page 1", pages_of(out[1]), [1])
check("orphan 3 follows page 2's owner", pages_of(out[0]), [0, 2, 3])

# --------------------------------------------------------------------------- their §6.2 checks

print("\ncontract-01  the three checks the webapp team run, on the real 5-page shape")
# The chunk in their handover: one bill on pages 0-2, another on pages 3-4.
out = [{"chunkPageIndex": 0, "_pages": [0, 1, 2]}, {"chunkPageIndex": 3, "_pages": [3, 4]}]
R.attach_orphans(out, 5)
R.attach_regions(out)
check("every candidate has at least one region",
      all(len(c["regions"]) >= 1 for c in out), True)
check("every candidate's own page is in its regions",
      all(c["chunkPageIndex"] in pages_of(c) for c in out), True)
covered = sorted({p for c in out for p in pages_of(c)})
check("the union covers the whole chunk", covered, [0, 1, 2, 3, 4])
check("no page is claimed twice", len(covered), sum(len(pages_of(c)) for c in out))
boxes = [r for c in out for r in c["regions"]]
check("every box is a non-empty integer box in 0-1000",
      all(isinstance(r[k], int) and 0 <= r[k] <= 1000 for r in boxes for k in
          ("xMin", "yMin", "xMax", "yMax"))
      and all(r["xMax"] > r["xMin"] and r["yMax"] > r["yMin"] for r in boxes), True)

print("\ninternal-01  the bookkeeping key never survives into the response")
out = [{"chunkPageIndex": 0, "_pages": [0, 1]}]
R.attach_regions(out)
M.strip_internal(out)
check("_pages gone", R.SPAN_KEY in out[0], False)
check("regions survive it", pages_of(out[0]), [0, 1])

print("\ndeclared-01  a key is sent only once their own file declares it")
# `relatedDocumentNumber` arrived as a required key in their v6 announcement on 2026-08-28, but
# the file itself did not. Sending it against their older file would fail
# `additionalProperties: false` and void the whole chunk; omitting it once the new file lands
# fails just as hard. So the answer is read from whichever file is actually on disk.
import json
import tempfile

import stage2_extract as s2

reduced = {"billCandidates": [{"originalTotal": {"value": 100.0, "confidence": 0.9},
                               "lineItems": []}]}

before = s2.assemble(reduced, page_index=0, transcript="x")["billCandidates"][0]
check("absent while their file does not declare it", "relatedDocumentNumber" in before, False)
check("and the response still validates", s2.validate({"billCandidates": [before]})[0], True)

schema = json.loads(s2.CONTRACT_SCHEMA.read_text(encoding="utf-8"))
item = schema["properties"]["billCandidates"]["items"]
item["properties"]["relatedDocumentNumber"] = item["properties"]["originalDocumentNumber"]
item["required"].append("relatedDocumentNumber")
patched = Path(tempfile.gettempdir()) / "v6-with-related.schema.json"
patched.write_text(json.dumps(schema), encoding="utf-8")

original, s2.CONTRACT_SCHEMA = s2.CONTRACT_SCHEMA, patched
try:
    after = s2.assemble(reduced, page_index=0, transcript="x")["billCandidates"][0]
    check("present once it does", after.get("relatedDocumentNumber"),
          {"value": None, "confidence": 0.0})
    check("and that response validates too", s2.validate({"billCandidates": [after]})[0], True)
finally:
    s2.CONTRACT_SCHEMA = original
    patched.unlink(missing_ok=True)

check("the cache follows the file back", "relatedDocumentNumber" in s2.declared_fields(), False)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
