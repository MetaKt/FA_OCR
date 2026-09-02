"""Fixtures for src/doctypes.py -- what each page is, in the contract's evidence vocabulary.

The measurement that shaped this module: of 58 real pages, 15 carry both ใบเสร็จรับเงิน and
ใบกำกับภาษี. That is 26%, so a page holding two roles is the ordinary case for Thai receipts, not
a corner. The webapp team confirmed on 2026-09-02 that every detected role is kept.

The other measurement: 7 of the 21 pages mentioning ใบกำกับภาษี only cite it, as
`เลขที่ใบกำกับภาษี`, the number of an invoice the receipt settles. Treating those as tax invoices
would mislabel a third of them.

    ../.venv/Scripts/python.exe eval/test_doctypes.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import doctypes as D
import regions as R

passed = failed = 0
ROLES = ("receipt", "tax_invoice", "cash_bill", "id_document", "transfer_slip",
         "exchange_rate_evidence", "approval_document", "other")


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<56} got {got!r}" + ("" if ok else f"  want {want!r}"))


print("map-01  one heading, one role")
check("ใบเสร็จรับเงิน", D.roles_for_page("ใบเสร็จรับเงิน เลขที่ 5"), ["receipt"])
check("ใบกำกับภาษี", D.roles_for_page("ใบกำกับภาษี/RECEIPT"), ["tax_invoice"])
check("บิลเงินสด", D.roles_for_page("บิลเงินสด ร้านค้า"), ["cash_bill"])
check("CASH SALE", D.roles_for_page("CASH SALE No. 8"), ["cash_bill"])
check("ใบรับเงิน (the wage form)", D.roles_for_page("# ใบรับเงิน"), ["receipt"])
check("ใบรับค่าผ่านทางพิเศษ (a toll ticket)",
      D.roles_for_page("การทางพิเศษแห่งประเทศไทย ใบรับค่าผ่านทางพิเศษ"), ["receipt"])

print("\nmap-02  a page headed both is both -- 26% of the corpus")
# Under Thai law one paper genuinely serves as receipt and tax invoice at once. Forcing a choice
# would throw away something the page states.
check("ใบเสร็จรับเงิน/ใบกำกับภาษี",
      D.roles_for_page("ใบเสร็จรับเงิน/ใบกำกับภาษี"), ["receipt", "tax_invoice"])
check("order is stable regardless of how they appear",
      D.roles_for_page("ใบกำกับภาษี ... ใบเสร็จรับเงิน"), ["receipt", "tax_invoice"])
check("and a role is never repeated",
      D.roles_for_page("บิลเงินสด CASH SALE บิลเงินสด"), ["cash_bill"])

print("\ncash-01  CASH SALE is a heading on its own, but not inside a receipt's title block")
# Found on the live endpoint, not in the fixtures. The first line below is verbatim from page 3
# of the 5-page file: the form lists every type it can serve as, and the cash in it is the
# payment method, ticked lower down as `☑ Cash เงินสด`. It is a receipt and a tax invoice.
combined = ("ใบเสร็จรับเงิน / ใบกำกับภาษี OFFICIAL RECEIPT / TAX INVOICE CASH SALE\n"
            "☑ Cash เงินสด 1,712 บาท (โอน)")
check("not also a cash bill", D.roles_for_page(combined), ["receipt", "tax_invoice"])
# Three corpus pages are Chinese-Thai shop forms headed only this way, with no Thai heading.
check("but CASH SALE alone still is one", D.roles_for_page("CASH SALE 現兑單"), ["cash_bill"])
check("and the Thai heading always wins, combined or not",
      D.roles_for_page("บิลเงินสด/ใบกำกับภาษี เลขที่ 2586"), ["tax_invoice", "cash_bill"])

print("\nlabel-01  citing an invoice number is not being an invoice")
# 7 of 21 real pages mentioning ใบกำกับภาษี are this. It is a receipt that settles an invoice.
check("เลขที่ใบกำกับภาษี alone", D.roles_for_page("เลขที่ใบกำกับภาษี IV-6801"), [])
check("on a receipt, only the receipt role survives",
      D.roles_for_page("ใบเสร็จรับเงิน เลขที่ใบกำกับภาษี IV-6801"), ["receipt"])
for prefix in ("เลขที่", "อ้างอิง", "ตาม", "หมายเลข"):
    check(f"{prefix}ใบกำกับภาษี", D.roles_for_page(f"{prefix}ใบกำกับภาษี 001"), [])
check("but a real heading beside a citation still counts",
      D.roles_for_page("ใบกำกับภาษี\nเลขที่ใบกำกับภาษี IV-1"), ["tax_invoice"])

print("\nblank-01  an unrecognised page gets no entry, never `other`")
# "We looked and it is something else" and "we did not know" are different claims.
check("empty", D.roles_for_page(""), [])
check("None", D.roles_for_page(None), [])
check("a delivery note is deliberately unmapped", D.roles_for_page("ใบส่งของ"), [])
check("so is a billing note", D.roles_for_page("ใบแจ้งหนี้"), [])
check("a withholding certificate has no role among the eight",
      D.roles_for_page("หนังสือรับรองการหักภาษี ณ ที่จ่าย 50 ทวิ"), [])
check("and an ID card is personlink's job, not this one",
      D.roles_for_page("บัตรประจำตัวประชาชน วันออกบัตร"), [])

print("\napply-01  every page a bill owns is tagged from its own transcript")
c = {"chunkPageIndex": 0, "regions": [R.whole_page(0), R.whole_page(1)]}
D.apply_document_types([c], {0: "ใบเสร็จรับเงิน/ใบกำกับภาษี", 1: "บิลเงินสด"})
check("roles follow the page they were found on",
      [(e["role"], e["regions"][0]["chunkPageIndex"]) for e in c["evidence"]],
      [("receipt", 0), ("tax_invoice", 0), ("cash_bill", 1)])

print("\napply-02  entries set by the other modules survive and are not duplicated")
# personlink and slips run earlier and tag pages they recognised. A second opinion about the
# same page must add to that, never replace it.
c = {"chunkPageIndex": 0, "regions": [R.whole_page(0), R.whole_page(1)]}
R.add_evidence(c, "transfer_slip", 1)
D.apply_document_types([c], {0: "ใบเสร็จรับเงิน", 1: "บิลเงินสด"})
D.apply_document_types([c], {0: "ใบเสร็จรับเงิน", 1: "บิลเงินสด"})     # a second pass
check("slip entry kept, headings added, nothing doubled",
      [(e["role"], e["regions"][0]["chunkPageIndex"]) for e in c["evidence"]],
      [("transfer_slip", 1), ("receipt", 0), ("cash_bill", 1)])

print("\napply-03  a page with no transcript, and a bill with no regions, are both safe")
c = {"chunkPageIndex": 0, "regions": [R.whole_page(0)]}
check("no transcript for that page", D.apply_document_types([c], {}), 0)
check("no evidence invented", c.get("evidence"), None)
check("no regions key at all", D.apply_document_types([{"chunkPageIndex": 0}], {0: "บิลเงินสด"}), 0)
check("empty inputs", (D.apply_document_types([], {}), D.apply_document_types(None, {})), (0, 0))

print("\ncontract-01  every role emitted is one the schema declares")
seen = set()
for text in ("ใบเสร็จรับเงิน/ใบกำกับภาษี", "บิลเงินสด", "CASH SALE", "ใบรับเงิน",
             "ใบรับค่าผ่านทางพิเศษ"):
    seen.update(D.roles_for_page(text))
check("roles used", sorted(seen), ["cash_bill", "receipt", "tax_invoice"])
check("all declared by the contract", seen <= set(ROLES), True)

print("\ncorpus-01  the real pages, if they are present")
corpus = [p for d in ("transcripts", "spanpages", "degenerate")
          for p in (HERE / d).glob("*.txt")]
if not corpus:
    print("  SKIP  eval/transcripts*/ absent (gitignored: real personal data).")
else:
    tagged = [D.roles_for_page(p.read_text(encoding="utf-8")) for p in corpus]
    both = sum(1 for r in tagged if r == ["receipt", "tax_invoice"])
    check("most pages get a role", sum(1 for r in tagged if r) >= len(corpus) * 0.8, True)
    check("the double-role case is common, not a corner", both >= 10, True)
    check("no role outside the contract's eight",
          all(role in ROLES for r in tagged for role in r), True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
