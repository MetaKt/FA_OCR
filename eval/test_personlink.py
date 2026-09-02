"""Fixtures for src/personlink.py -- the ID card stapled behind a ใบรับเงิน.

The two transcripts used here are real: pages 20 and 21 of the P06690 clearing set, a wage
receipt and the copy of the payee's national ID card. They are the reason the module exists and
the reason it does not try to re-derive the pairing from content -- on this pair the content
cannot establish it.

    ../.venv/Scripts/python.exe eval/test_personlink.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import certlink
import personlink as P
import regions as R

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<54} got {got!r}" + ("" if ok else f"  want {want!r}"))


CARD = """บัตรประจำตัวประชาชน Thai National ID Card
เลขประจำตัวประชาชน / Identification Number: 3 4010 00897 81 9
ชื่อตัวและชื่อสกุล: นาย ปรีชา ขุนแก้ว
เกิดวันที่ / Date of Birth: 11 ม.ค. 2522
ศาสนา: พุทธ
วันออกบัตร / Date of Issue: 15 ม.ค. 2563
วันหมดอายุ / Date of Expiry: 10 ม.ค. 2572
เจ้าหน้าที่ออกบัตร
สำเนาถูกต้อง"""

# The wage receipt. Note it prints เลขประจำตัวประชาชน too -- that label is why the card markers
# deliberately exclude it -- and the only 13-digit number on it is TEAM's own.
RECEIPT = """# ใบรับเงิน
## 1. ข้อมูลผู้รับเงิน (บุคคลธรรมดา)
ชื่อ-นามสกุล: นาย ปรีดา ขนแก้ว เลขประจำตัวประชาชน 320100081719
## 2. ข้อมูลผู้จ่ายเงิน (บริษัท)
เลขประจำตัวผู้เสียภาษีอากร: 0107561000030
หมายเหตุ : ให้แนบเอกสารดังต่อไปนี้ด้วยทุกครั้ง
1) สำเนาบัตรประชาชนของผู้รับเงินพร้อมลงลายมือชื่อรับรองสำเนาถูกต้อง
2) สลิปโอนเงินของธนาคารที่โอนให้ผู้รับเงิน"""


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def wage_bill(page=0, name="นาย ปรีดา ขนแก้ว", tax_id=None, total=24226.80):
    return {"chunkPageIndex": page, "sellerName": w(name), "sellerTaxId": w(tax_id, 0.0),
            "payeeType": w("individual"), "originalTotal": w(total),
            "amountBeforeVat": w(total), "clearingAmount": w(23500.0),
            "vat": w(None, 0.0), "withholdingTax": w(726.80)}


def phantom(page=1):
    return {"chunkPageIndex": page, "sellerName": w(None, 0.0), "sellerTaxId": w(None, 0.0),
            "payeeType": w("individual"), "originalTotal": w(None, 0.0),
            "amountBeforeVat": w(None, 0.0), "clearingAmount": w(None, 0.0),
            "vat": w(None, 0.0), "withholdingTax": w(None, 0.0)}


def pages_of(candidate):
    return R.page_span(candidate)


print("detect-01  the card is recognised and the receipt is not")
# 8 markers vs 0 on the real pair. The receipt says สำเนาบัตรประชาชน in its attachment note,
# which must not be enough to make it look like a card.
check("card", P.is_id_card(CARD), True)
check("receipt", P.is_id_card(RECEIPT), False)
check("empty", P.is_id_card(""), False)
check("None", P.is_id_card(None), False)
check("one marker alone is not enough", P.is_id_card("ศาสนา: พุทธ"), False)

print("\nread-01  the number survives the spacing it is printed with")
check("3 4010 00897 81 9", P.card_national_id(CARD), "3401000897819")
check("and it passes its check digit", certlink.valid_thai_tax_id("3401000897819"), True)
check("name", P.card_name(CARD), "นาย ปรีชา ขุนแก้ว")

print("\nread-02  a misread number is refused rather than filled in")
# The receipt's own attempt at this number lost a digit. Anything that fails the check digit is
# not evidence of anything.
check("12 digits", P.card_national_id(CARD.replace("3 4010 00897 81 9", "3 4010 0897 81 9")), None)
check("checksum failure", P.card_national_id(CARD.replace("81 9", "81 8")), None)
check("two valid ids is ambiguous, not a pick",
      P.card_national_id(CARD + " 1 1010 12345 67 7"), None)
check("digits are never spliced across a line break",
      P.card_national_id("บัตรประจำตัวประชาชน ศาสนา\n3 4010 00897 81\n9 111 2222 333"), None)
check("our own id is never the payee", P.card_national_id(
    "บัตรประจำตัวประชาชน วันออกบัตร 0107561000030"), None)

print("\nfold-01  the real pair: the card joins the bill and gives it the payee's ID")
bill = wage_bill(page=0)
kept, report = P.apply_cards([bill], {0: RECEIPT, 1: CARD})
check("one candidate", len(kept), 1)
check("the card's page came with it", pages_of(kept[0]), [0, 1])
check("attached", report[0]["attached"], True)
check("sellerTaxId filled from the card", kept[0]["sellerTaxId"]["value"], "3401000897819")

print("\nfold-02  the fill is labelled by how sure the pairing is, not by how sure the digits are")
# The names here score 0.897 -- just under certlink's match threshold, so `names_agree` returns
# None. The number is certain; that it belongs to this payment is an inference from the stapling
# order. 0.45 is below the review UI's 0.5 cut, so a human sees it.
check("undecided names -> flagged for review", kept[0]["sellerTaxId"]["confidence"], 0.45)
bill = wage_bill(page=0, name="นาย ปรีชา ขุนแก้ว")          # names match exactly
kept, _ = P.apply_cards([bill], {0: RECEIPT, 1: CARD})
check("agreeing names -> confident", kept[0]["sellerTaxId"]["confidence"], 0.9)

print("\nfold-03  a bill that already states a valid ID is left alone")
bill = wage_bill(page=0, tax_id="1101401234566")   # checksum-valid, a different person
kept, report = P.apply_cards([bill], {0: RECEIPT, 1: CARD})
check("not overwritten", kept[0]["sellerTaxId"]["value"], "1101401234566")
check("and nothing was reported as filled", report[0]["filledTaxId"], None)
check("but the page still joined", pages_of(kept[0]), [0, 1])

print("\nfold-04  a bill whose stated ID failed its check digit is corrected from the card")
# This is the ใบรับเงิน case: stage 1 read the payee's ID as twelve digits.
bill = wage_bill(page=0, tax_id="320100081719")
kept, _ = P.apply_cards([bill], {0: RECEIPT, 1: CARD})
check("replaced", kept[0]["sellerTaxId"]["value"], "3401000897819")

print("\nconflict-01  two different people means touch nothing")
# The likeliest cause is that the stapling order is not what we assumed. Filling here would put
# one person's national ID on another person's payment.
bill = wage_bill(page=0, name="นางสาว สมหญิง ใจดี")
kept, report = P.apply_cards([bill], {0: RECEIPT, 1: CARD})
check("conflict reported", report[0]["nameConflict"], True)
check("no ID written", kept[0]["sellerTaxId"]["value"], None)
check("name distrusted", kept[0]["sellerName"]["confidence"], 0.3)
check("the page still joins -- it is still evidence for this payment", pages_of(kept[0]), [0, 1])

print("\nphantom-01  an ID card never becomes a ledger row")
kept, _ = P.apply_cards([wage_bill(page=0), phantom(page=1)], {0: RECEIPT, 1: CARD})
check("the empty row is gone", len(kept), 1)
check("and its page went to the bill", pages_of(kept[0]), [0, 1])

print("\nphantom-02  a card page carrying a real payment is not dropped")
# Defensive: if a page is both, the money wins. Losing a payment is the one unrecoverable error.
real = wage_bill(page=1, total=500.0)
kept, _ = P.apply_cards([wage_bill(page=0), real], {0: RECEIPT, 1: CARD})
check("both kept", len(kept), 2)

print("\nshape-01  chunks with nothing to do are returned untouched")
bills = [wage_bill(page=0)]
check("no card pages", P.apply_cards(bills, {0: RECEIPT}), (bills, []))
check("no transcripts", P.apply_cards(bills, {}), (bills, []))
# A loose card is still worth reporting: it means a chunk boundary split a pair, which is
# the caller's problem to notice.
kept, report = P.apply_cards([], {0: CARD})
check("no candidates at all", kept, [])
check("but the loose card is reported", (len(report), report[0]["attached"]), (1, False))

print("\nshape-02  a card before any bill is left for attach_orphans, not guessed at")
kept, report = P.apply_cards([wage_bill(page=1)], {0: CARD, 1: RECEIPT})
check("not attached", report[0]["attached"], False)
check("nothing filled", report[0]["filledTaxId"], None)
check("the bill is untouched", pages_of(kept[0]), [1])

print("\nmulti-01  each card goes to the bill it was stapled behind")
a, b = wage_bill(page=0, name="นาย ปรีชา ขุนแก้ว"), wage_bill(page=2, name="นาง สมศรี มีสุข")
kept, report = P.apply_cards([a, b], {0: RECEIPT, 1: CARD, 2: RECEIPT, 3: CARD})
check("first bill takes page 1", pages_of(kept[0]), [0, 1])
check("second bill takes page 3", pages_of(kept[1]), [2, 3])
check("the mismatched second pair is refused, not filled",
      [r["filledTaxId"] for r in report], ["3401000897819", None])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
