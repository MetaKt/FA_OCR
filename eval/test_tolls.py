"""Fixtures for src/tolls.py -- a stapled set of expressway tickets as one ledger row.

Every transcript below is real, from P06690 pages 199-201 as the production stage-1 path read
them. They cover the three cases that matter: a page whose ticket is photocopied, a page with two
different tickets one of which is photocopied, and a page whose two tickets are taxed differently.

    ../.venv/Scripts/python.exe eval/test_tolls.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import regions as R
import tolls as T

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<56} got {got!r}" + ("" if ok else f"  want {want!r}"))


HEAD = ("EXAT การทางพิเศษแห่งประเทศไทย โทร 1543 เลขประจำตัวผู้เสียภาษีอากร 0994000165421\n"
        "ใบรับค่าผ่านทางพิเศษ\n")

# Page 200: three ticket images, two real tickets. 202607131735220214 is printed twice at 25.00.
P200 = HEAD + """Book No: 40702 Receipt Running No : 202607201730100213 Date Time 20/07/2026 20:01:59 Class I Baht(Vat Included) 20.00
Total 20.00 Payment For TOLLFARE
กรุณาทำสำเนาเพื่อนำไปใช้ในธุรกรรมต่อไป
""" + HEAD + """Book No: 66202 Receipt Running No : 202607131735220214 Date Time 13/07/2026 18:24:58 Class I Baht(Vat Included) 25.00
Total 25.00 Payment For TOLLFARE
""" + HEAD + """Book No: 66202 Receipt Running No : 202607131735220214 Date Time 13/07/2026 18:24:58 Class I Baht(Vat Included) 25.00
Total 25.00 Payment For TOLLFARE
"""

# Page 201: two different issuers on one page, one VAT-inclusive and one not.
P201 = HEAD + """Book No: 66201 Receipt Running No : 2026072661266208035
Date Time 26/07/2026 17:53:22 Class 3 Baht(Vat Included) 80.00

กรมทางหลวง โทร 1586
ใบรับค่าธรรมเนียมผ่านทางหลวงพิเศษ ทางหลวงพิเศษหมายเลข 9
Book No: 66201 Receipt Running No : 202607261620210215
Date Time 26/07/2026 17:53:22 Class 3 Baht(Non Vat) 35.00
Total 115.00 Payment For TOLLFARE
"""

# BEM prints a table instead of flat text, and labels the number `No.` rather than Receipt
# Running No. Real, from eval/spanpages/p049.txt.
BEM = """BEM
การทางพิเศษแห่งประเทศไทย เลขประจำตัวผู้เสียภาษี 039 4 00016542
บมจ. ทางด่วนและรถไฟฟ้ากรุงเทพ
ใบรับค่าผ่านทางพิเศษ ทางพิเศษศรีรัช
<table><tr><td>No.</td><td>24102202607080110173</td></tr><tr><td>Date Time</td><td>08/07/2026 07:08:46</td></tr><tr><td>Class</td><td>1</td></tr><tr><td>Baht(Vat Included)</td><td>50.00</td></tr></table>
"""

RECEIPT = "ใบเสร็จรับเงิน/ใบกำกับภาษี บริษัท ดีครับผม จำกัด รวมทั้งสิ้น 5,010.81"


def w(v, c=0.9):
    return {"value": v, "confidence": c}


def cand(page, total=None, index=0):
    return {"candidateIndex": index, "chunkPageIndex": page, "originalTotal": w(total),
            "clearingAmount": w(total), "amountBeforeVat": w(None, 0.0), "vat": w(None, 0.0),
            "vatRate": w(None, 0.0), "documentDate": w(None, 0.0),
            "originalDocumentNumber": w(None, 0.0), "documentBookNumber": w(None, 0.0),
            "sellerName": w("การทางพิเศษแห่งประเทศไทย"), "lineItems": [],
            "regions": [R.whole_page(page)]}


def v(c, f):
    return (c.get(f) or {}).get("value")


print("detect-01  toll pages are recognised, other documents are not")
check("EXAT page", T.is_toll_page(P200), True)
check("กรมทางหลวง page", T.is_toll_page(P201), True)
check("BEM table page", T.is_toll_page(BEM), True)
check("an ordinary receipt", T.is_toll_page(RECEIPT), False)
check("empty", T.is_toll_page(""), False)

print("\nparse-01  the photocopy is dropped -- 3 images, 2 tickets")
# The ticket prints `กรุณาทำสำเนา...` on itself, so duplicates are the norm, not the exception.
tickets = T.parse_tickets(P200)
check("two tickets", [t["ref"] for t in tickets],
      ["202607201730100213", "202607131735220214"])
check("amounts", [t["amount"] for t in tickets], [20.00, 25.00])
check("the page is worth 45, not 70", sum(t["amount"] for t in tickets), 45.00)
check("dates read per ticket", [t["date"] for t in tickets], ["2026-07-20", "2026-07-13"])
# parse_tickets removes the copies, so counting its output can never reveal them.
check("but three images were printed", T.ticket_images(P200), 3)
check("and one was a copy", T.ticket_images(P200) - len(tickets), 1)

print("\nparse-02  two issuers on one page, taxed differently")
tickets = T.parse_tickets(P201)
check("two tickets", len(tickets), 2)
check("amounts", [t["amount"] for t in tickets], [80.00, 35.00])
check("one carries VAT, the other does not", [t["vatIncluded"] for t in tickets], [True, False])
check("and they add to the printed Total", sum(t["amount"] for t in tickets), 115.00)

print("\nparse-03  the BEM table layout is read too")
tickets = T.parse_tickets(BEM)
check("one ticket", [(t["ref"], t["amount"]) for t in tickets],
      [("24102202607080110173", 50.00)])
check("its date", tickets[0]["date"], "2026-07-08")

print("\nparse-03b  the amount cell may carry its unit -- real, and it cost 75 baht")
# Verbatim from P06690 page 198 as stage 1 read it at 1300px. The first version of this module
# required the cell to hold the number alone, so both tickets were read, found to have no
# parseable amount, and dropped. The live run showed 190 where the pages are worth 265.
P198 = """BEM การทางพิเศษแห่งประเทศไทย
ใบรับค่าผ่านทางพิเศษ ทางพิเศษศรีรัช
<table><tr><td>No.</td><td>24202202607160610130</td></tr><tr><td>Date Time</td><td>16/07/2026 15:14:40</td></tr><tr><td>Baht(Vat Included)</td><td>25.00 บาท</td></tr></table>
<table><tr><td>No.</td><td>22603202607160610046</td></tr><tr><td>Date Time</td><td>16/07/2026 15:05:07</td></tr><tr><td>Baht(Vat Included)</td><td>50.00 บาท</td></tr></table>
"""
tickets = T.parse_tickets(P198)
check("both tickets read", [(t["ref"], t["amount"]) for t in tickets],
      [("24202202607160610130", 25.00), ("22603202607160610046", 50.00)])
check("worth 75", sum(t["amount"] for t in tickets), 75.00)

print("\nparse-04  a ticket with no readable amount is dropped, never guessed")
# A ticket contributing an unknown amount would make the sum quietly wrong.
check("number but no amount",
      T.parse_tickets(HEAD + "Receipt Running No : 202607201730100213 Date Time 20/07/2026"), [])
check("nothing at all", T.parse_tickets(RECEIPT), [])
check("empty", T.parse_tickets(""), [])

print("\ngate-01  nothing is merged unless FA said this is travel")
pages = {0: P200, 1: P201}
cands = [cand(0, 45.0), cand(1, 115.0, 1)]
check("no category at all", T.apply_tolls(list(cands), pages, None)[1], [])
check("a different category", T.apply_tolls(list(cands), pages, "5122100")[1], [])
check("travel, with its Thai label attached",
      T.applies("5223100 ค่าเดินทาง ในประเทศ"), True)
check("and with dashes", T.applies("5-223-100"), True)
# The account code is written down once. It used to be a constant in tolls.py as well, so FA
# renumbering travel would have needed two edits -- and doing only the JSON one would have looked
# sufficient while quietly switching the summing off.
import json
import stage2_extract as s2
check("the switch lives in category_rules.json, not in code",
      json.loads(s2.CATEGORY_RULES.read_text(encoding="utf-8"))["5223100"][T.TOLL_FLAG], True)
check("a category with the flag absent does not sum", T.applies("5222100"), False)

print("\nmerge-01  a run of toll pages becomes one row that sums the tickets")
out, report = T.apply_tolls([cand(0, 45.0), cand(1, 115.0, 1)], pages, "5223100")
check("one row", len(out), 1)
check("total is the sum of four distinct tickets", v(out[0], "originalTotal"), 160.00)
check("clearingAmount follows it", v(out[0], "clearingAmount"), 160.00)
# `regions` is built later by attach_regions, from the span this module sets.
check("spans both pages", R.page_span(out[0]), [0, 1])
R.attach_regions(out)
check("and attach_regions turns that into regions",
      [r["chunkPageIndex"] for r in out[0]["regions"]], [0, 1])
check("report", [(r["pages"], r["tickets"], r["total"], r["copiesDropped"]) for r in report],
      [([0, 1], 4, 160.00, 1)])

print("\nmerge-02  the three fields that cannot be merged are left empty")
check("no subtotal -- the run mixes VAT-inclusive and non-VAT tickets",
      v(out[0], "amountBeforeVat"), None)
check("no vat", v(out[0], "vat"), None)
check("no vatRate", v(out[0], "vatRate"), None)
check("no document number -- each ticket has its own", v(out[0], "originalDocumentNumber"), None)
check("no book number", v(out[0], "documentBookNumber"), None)
check("date is the earliest ticket, so the row sorts into the right period",
      v(out[0], "documentDate"), "2026-07-13")

print("\nmerge-03  every running number survives in lineItems")
# The only free-text field in the contract, and the reviewer's way back to a single ticket.
check("one line per ticket", len(out[0]["lineItems"]), 4)
check("descriptions", [v(li, "description") for li in out[0]["lineItems"]],
      ["TOLLFARE 202607201730100213", "TOLLFARE 202607131735220214",
       "TOLLFARE 2026072661266208035", "TOLLFARE 202607261620210215"])
check("amounts", [v(li, "amount") for li in out[0]["lineItems"]], [20.0, 25.0, 80.0, 35.0])
check("and they add up to the row", sum(v(li, "amount") for li in out[0]["lineItems"]), 160.00)

print("\nmerge-04  a gap in the pages is a different trip")
# Tolls at pages 0-1 and again at 4 are two journeys, not one.
pages = {0: P200, 1: P201, 4: BEM}
out, report = T.apply_tolls([cand(0, 45.0), cand(1, 115.0, 1), cand(4, 50.0, 2)],
                            pages, "5223100")
check("two rows", len(out), 2)
check("totals", sorted(v(c, "originalTotal") for c in out), [50.00, 160.00])
check("and their spans do not overlap",
      sorted(p for c in out for p in R.page_span(c)), [0, 1, 4])
check("runs", [r["pages"] for r in report], [[0, 1], [4]])

print("\nmerge-05  non-toll rows in the same chunk are untouched")
pages = {0: RECEIPT, 1: P200}
receipt_row = cand(0, 5010.81)
receipt_row["sellerName"] = w("บริษัท ดีครับผม จำกัด")
out, _ = T.apply_tolls([receipt_row, cand(1, 45.0, 1)], pages, "5223100")
check("both rows survive", len(out), 2)
check("the receipt is unchanged", v(out[0], "originalTotal"), 5010.81)
check("the toll row is summed", v(out[1], "originalTotal"), 45.00)

print("\nshape-01  chunks with nothing to merge come back untouched")
check("no toll pages", T.apply_tolls([cand(0)], {0: RECEIPT}, "5223100"), ([cand(0)], []))
check("no candidates", T.apply_tolls([], {0: P200}, "5223100"), ([], []))
check("a toll page nobody could read", T.apply_tolls([cand(0)], {0: HEAD}, "5223100")[1], [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
