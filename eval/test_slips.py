"""Fixtures for src/slips.py -- the bank transfer slip and the payment it evidences.

SLIP below is a real transcript, page 192 of the P06690 clearing set, produced by the production
stage-1 path. The six real slips are all Kasikorn K+ *bill-payment* slips, so the true-positive
side of this is proven only for that form; the false-positive side is measured against all 105
other real pages in the corpus.

The case that matters most is fold-02: a slip states the same money as the receipt beside it, so
attaching it wrongly does not lose a page, it invents a payment.

    ../.venv/Scripts/python.exe eval/test_slips.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import regions as R
import slips as S

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<54} got {got!r}" + ("" if ok else f"  want {want!r}"))


# Real, page 192. Note the fee sits directly under the amount in the same format.
SLIP = """จ่ายบิลสำเร็จ 3 ก.ค. 69 19:32 น.

K+

นาย สมเกียรติ จ
ธ.กสิกรไทย xxx-x-x2565-x

DEPARTMENT OF HIGHWAY, M- FLOW PAYMENT
326070300000027839

เลขที่รายการ: 016184193208APM12413

จำนวน: 90.00 บาท
ค่าธรรมเนียม: 0.00 บาท

<figure>
A QR code is displayed on the right side of the document, with the text "สแกนตรวจสอบสลิป" (Scan to check slip) below it.
</figure>

บันทึกช่วยจำ: M Flow"""

# The wage receipt. It *mentions* a slip in its attachment note, which must not make it one.
RECEIPT = """# ใบรับเงิน
ชื่อ-นามสกุล: นาย ปรีดา ขนแก้ว
รายการรับเงิน ค่าจ้างแรงงานติดตั้งเครื่องมือ 24,226.80 726.80 23,500.00
หมายเหตุ : ให้แนบเอกสารดังต่อไปนี้ด้วยทุกครั้ง
1) สำเนาบัตรประชาชนของผู้รับเงินพร้อมลงลายมือชื่อรับรองสำเนาถูกต้อง
2) สลิปโอนเงินของธนาคารที่โอนให้ผู้รับเงิน"""


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def bill(page=0, total=24226.80, clearing=23500.00):
    return {"chunkPageIndex": page, "originalTotal": w(total), "clearingAmount": w(clearing),
            "sellerName": w("นาย ปรีดา ขนแก้ว")}


def slip_candidate(page=1, total=23500.00):
    """What stage 2 makes of a slip page: a bill, for the money that was transferred."""
    return {"chunkPageIndex": page, "originalTotal": w(total), "clearingAmount": w(total),
            "sellerName": w(None, 0.0)}


def pages_of(c):
    return R.page_span(c)


def roles_of(c):
    return [(e["role"], e["regions"][0]["chunkPageIndex"]) for e in c.get("evidence", [])]


print("detect-01  a real slip is recognised; documents that merely mention one are not")
check("the real slip", S.is_transfer_slip(SLIP), True)
check("the wage receipt that asks for a slip", S.is_transfer_slip(RECEIPT), False)
check("empty", S.is_transfer_slip(""), False)
check("None", S.is_transfer_slip(None), False)
check("one marker alone is not enough", S.is_transfer_slip("รายการนี้สำเร็จแล้ว"), False)

print("\ndetect-02  the markers are the slip's wording, not one bank's")
# Only K+ slips could be tested. A slip with none of the K+ vocabulary must still register.
generic = "โอนเงินสำเร็จ 5 ส.ค. 69\nธนาคารไทยพาณิชย์\nเลขที่รายการ: 20260805X99\nจำนวน: 23,500.00 บาท"
check("an SCB transfer with no K+ wording", S.is_transfer_slip(generic), True)
check("and its amount is read", S.slip_amount(generic), 23500.00)

print("\nread-01  the amount is the amount, never the fee")
# Both are money, both are labelled, and the fee is printed directly below on every real slip.
check("amount", S.slip_amount(SLIP), 90.00)
check("reference", S.slip_reference(SLIP), "016184193208APM12413")
check("a non-zero fee is still not the amount",
      S.slip_amount(SLIP.replace("ค่าธรรมเนียม: 0.00", "ค่าธรรมเนียม: 15.00")), 90.00)

print("\nread-02  an unreadable slip yields None rather than a guess")
check("no amount printed", S.slip_amount("เลขที่รายการ: X สแกนตรวจสอบสลิป"), None)
check("two different amounts is ambiguous",
      S.slip_amount("จำนวน: 90.00 บาท จำนวนเงิน: 120.00 บาท"), None)
check("no reference", S.slip_reference(SLIP.replace("เลขที่รายการ:", "xx:")), None)

print("\nfold-01  a slip whose amount matches a bill is folded into it")
b = bill(page=0)
kept, report = S.apply_slips([b], {0: RECEIPT, 1: SLIP.replace("90.00", "23,500.00")})
check("one candidate", len(kept), 1)
check("the slip's page joined the bill", pages_of(kept[0]), [0, 1])
check("tagged as a transfer slip", roles_of(kept[0]), [("transfer_slip", 1)])
check("attached", report[0]["attached"], True)

print("\nfold-02  the slip's own row is dropped -- it is the same money, not more money")
# This is the failure that costs real baht: 23,500 paid once, reported twice.
paid = SLIP.replace("90.00", "23,500.00")
kept, _ = S.apply_slips([bill(page=0), slip_candidate(page=1)], {0: RECEIPT, 1: paid})
check("one row, not two", len(kept), 1)
check("and the money is counted once", _v := kept[0]["clearingAmount"]["value"], 23500.00)

print("\nstand-01  a slip that matches nothing keeps its row -- it may be the only record")
# R-SLIP-001 exists because a payment sometimes has no receipt at all. Dropping the slip then
# would delete the payment, which is the one unrecoverable error.
kept, report = S.apply_slips([bill(page=0), slip_candidate(page=1, total=90.00)],
                             {0: RECEIPT, 1: SLIP})
check("both rows survive", len(kept), 2)
check("not attached", report[0]["attached"], False)
check("and the reason is recorded", report[0]["reason"], "no bill states this amount")
check("the standing slip is still tagged", roles_of(kept[1]), [("transfer_slip", 1)])

print("\nstand-02  two bills for the same amount is ambiguous, so nothing is moved")
paid = SLIP.replace("90.00", "23,500.00")
a, b = bill(page=0), bill(page=1)
kept, report = S.apply_slips([a, b], {0: RECEIPT, 1: RECEIPT, 2: paid})
check("nothing folded", report[0]["attached"], False)
check("reason", report[0]["reason"], "2 bills state this amount")
check("neither bill took the page", (pages_of(kept[0]), pages_of(kept[1])), ([0], [1]))

print("\nstand-03  two slips for the same amount cannot both have paid one bill")
# Found by the live run, not by these fixtures. P06690 pages 195 and 196 are both 30.00 with
# different transaction references and different dates, and the chunk holds one 30.00 toll
# ticket. Attaching both claimed that one payment had two slips proving it, and dropped 30 baht
# out of the chunk total.
paid = SLIP.replace("90.00", "30.00")
one = bill(page=0, total=30.00, clearing=30.00)
kept, report = S.apply_slips([one, slip_candidate(2, 30.0), slip_candidate(3, 30.0)],
                             {0: RECEIPT, 2: paid, 3: paid})
check("neither folded", [r["attached"] for r in report], [False, False])
check("and the reason names the rival", "2 slips match this bill" in report[0]["reason"], True)
check("the bill did not take either page", pages_of(kept[0]), [0])
check("both slips still stand, so no payment is lost", len(kept), 3)

print("\nmatch-01  the net is what the bank moves, so clearingAmount matches before the gross")
# A wage receipt: gross 24,226.80, withholding 726.80, net 23,500.00. The slip shows the net.
b = bill(page=0, total=24226.80, clearing=23500.00)
kept, report = S.apply_slips([b], {0: RECEIPT, 1: SLIP.replace("90.00", "23,500.00")})
check("matched on the net", report[0]["attached"], True)
b = bill(page=0, total=24226.80, clearing=None)
kept, report = S.apply_slips([b], {0: RECEIPT, 1: SLIP.replace("90.00", "24,226.80")})
check("and on the gross when there is no net", report[0]["attached"], True)

print("\nevidence-01  entries are shaped as the contract declares and never duplicated")
b = bill(page=0)
paid = SLIP.replace("90.00", "23,500.00")
S.apply_slips([b], {0: RECEIPT, 1: paid})
S.apply_slips([b], {0: RECEIPT, 1: paid})            # a second pass must not add it twice
check("one entry", len(b["evidence"]), 1)
entry = b["evidence"][0]
check("role is one of the eight", entry["role"] in
      ("receipt", "tax_invoice", "cash_bill", "id_document", "transfer_slip",
       "exchange_rate_evidence", "approval_document", "other"), True)
check("regions is non-empty", len(entry["regions"]) >= 1, True)
check("and every box is the whole page", entry["regions"][0],
      {"chunkPageIndex": 1, "xMin": 0, "yMin": 0, "xMax": 1000, "yMax": 1000})

print("\nevidence-02  add_evidence records different roles and pages separately")
c = {}
R.add_evidence(c, "transfer_slip", 3)
R.add_evidence(c, "transfer_slip", 3)
R.add_evidence(c, "id_document", 3)
R.add_evidence(c, "transfer_slip", 4)
check("three entries", [(e["role"], e["regions"][0]["chunkPageIndex"]) for e in c["evidence"]],
      [("transfer_slip", 3), ("id_document", 3), ("transfer_slip", 4)])

print("\nshape-01  chunks with no slip are returned untouched")
bills = [bill(page=0)]
check("no slip pages", S.apply_slips(bills, {0: RECEIPT}), (bills, []))
check("no transcripts", S.apply_slips(bills, {}), (bills, []))
check("no candidates", S.apply_slips([], {0: SLIP}), ([], [{
    "page": 0, "slip": {"amount": 90.0, "reference": "016184193208APM12413"},
    "attached": False, "reason": "no bill states this amount"}]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
