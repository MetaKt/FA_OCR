"""Fixtures for src/payee.py -- company, shop, or person.

Every seller name below is real, taken from saved responses this pipeline produced. Two of them
are errors the module exists to fix: a rice-porridge shop filed as `individual`, and a government
agency filed `individual` in one response and `company` in another with the same juristic tax id
both times.

    ../.venv/Scripts/python.exe eval/test_payee.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import payee as P
import stage2_extract as s2

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<56} got {got!r}" + ("" if ok else f"  want {want!r}"))


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def cand(name=None, tax_id=None, payee=None, index=0):
    return {"candidateIndex": index, "sellerName": w(name), "sellerTaxId": w(tax_id),
            "payeeType": w(payee)}


print("grammar-01  the model can finally say it")
check("three values", s2.PAYEE_TYPES, ["company", "shop", "individual"])

print("\nname-01  a registered business names itself")
for name in ("บริษัท ดีครับผม จำกัด", "บริษัท สยามสตีล กัลวาไนซึ่ง จำกัด",
             "หจก. สมชายการช่าง", "บมจ. ทางด่วนและรถไฟฟ้ากรุงเทพ",
             "TRIP.COM TRAVEL SINGAPORE PTE. LTD."):
    check(name[:40], P.from_name(name), "company")

print("\nname-02  a shop names itself too -- the case that had nowhere to go")
# Real, and currently filed as `individual` in a saved response.
check("ร้านข้าวต้มโกยาว", P.from_name("ร้านข้าวต้มโกยาว"), "shop")
check("ร้านวัสดุก่อสร้างพรชัย", P.from_name("ร้านวัสดุก่อสร้างพรชัย"), "shop")
check("only at the start -- 'ส่งของถึงร้านค้า' is not a shop's name",
      P.from_name("บจก เอบีซี ส่งของถึงร้านค้า"), "company")

print("\nname-03  a registered name outranks ร้าน, a personal title outranks both")
check("ร้านอาหารเอบีซี จำกัด is a company", P.from_name("ร้านอาหารเอบีซี จำกัด"), "company")
check("นาย สมชาย ร้านค้า is a person", P.from_name("นาย สมชาย ร้านค้า"), "individual")
check("นายปรีชา ขุนแก้ว", P.from_name("นายปรีชา ขุนแก้ว"), "individual")
check("นางสาว สมหญิง ใจดี", P.from_name("นางสาว สมหญิง ใจดี"), "individual")

print("\nname-04  a name that settles nothing settles nothing")
check("วิว การ์เดน รีสอร์ท", P.from_name("วิว การ์เดน รีสอร์ท"), None)
check("การทางพิเศษแห่งประเทศไทย", P.from_name("การทางพิเศษแห่งประเทศไทย"), None)
check("empty", P.from_name(""), None)
check("None", P.from_name(None), None)

print("\ntaxid-01  the first digit of a valid Thai id is evidence, not a guess")
check("0-prefixed is a juristic person", P.from_tax_id("0994000165421"), "company")
check("TEAM's own id", P.from_tax_id("0107561000030"), "company")
check("3-prefixed is a person", P.from_tax_id("3401000897819"), "individual")
check("spacing and dashes survive", P.from_tax_id("3 4010 00897 81 9"), "individual")

print("\ntaxid-02  a number that fails its check digit classifies nobody")
# Stage 1 invents plausible ids. Classifying a payee from a misread number is worse than not.
check("checksum failure", P.from_tax_id("0994000165422"), None)
check("twelve digits", P.from_tax_id("320100081719"), None)
check("empty", P.from_tax_id(None), None)
check("9-prefixed is neither", P.from_tax_id("9994000165421"), None)

print("\nclassify-01  the name is asked first, because the id cannot see a shop")
# A tax id says only whether the payee is a juristic person. The policy splits on what the payee
# is, so a registered shop stays a shop.
check("ร้าน with a juristic id is still a shop",
      P.classify("ร้านข้าวต้มโกยาว", "0994000165421"), "shop")
check("a nameless payee falls back to the id",
      P.classify(None, "0994000165421"), "company")
check("neither", P.classify("วิว การ์เดน รีสอร์ท", None), None)

print("\napply-01  the two real errors in saved responses")
shop = cand("ร้านข้าวต้มโกยาว", None, "individual", 0)
exat = cand("การทางพิเศษแห่งประเทศไทย", "0994000165421", "individual", 1)
report = P.apply_payee_types([shop, exat])
check("the shop is reclassified", shop["payeeType"]["value"], "shop")
check("the agency follows its juristic id", exat["payeeType"]["value"], "company")
check("both reported", [(r["was"], r["now"]) for r in report],
      [("individual", "shop"), ("individual", "company")])
check("and the new value is not passed off as certain",
      shop["payeeType"]["confidence"], P.DECIDED_CONFIDENCE)

print("\napply-02  a candidate the rules cannot settle is left exactly as it was")
# The guard corrects what it knows. It does not overwrite the model with a shrug.
c = cand("วิว การ์เดน รีสอร์ท", None, "company")
check("no report entry", P.apply_payee_types([c]), [])
check("untouched", c["payeeType"], {"value": "company", "confidence": 0.9})

print("\napply-03  agreeing with the model is not a change")
c = cand("บริษัท ดีครับผม จำกัด", "0105562192879", "company")
check("nothing reported", P.apply_payee_types([c]), [])
check("confidence not disturbed", c["payeeType"]["confidence"], 0.9)

print("\napply-04  a null payeeType is filled")
c = cand("ร้านข้าวต้มโกยาว", None, None)
P.apply_payee_types([c])
check("filled", c["payeeType"]["value"], "shop")

print("\napply-05  malformed input never raises")
check("empty list", P.apply_payee_types([]), [])
check("None", P.apply_payee_types(None), [])
check("no fields at all", P.apply_payee_types([{}]), [])

print("\ncontract-01  every value emitted is one the contract allows")
values = {P.classify(n, t) for n, t in
          (("ร้านข้าวต้มโกยาว", None), ("บริษัท ก จำกัด", None), ("นายปรีชา ขุนแก้ว", None),
           (None, "0994000165421"), ("วิว การ์เดน รีสอร์ท", None))}
check("no value outside the enum", values - {None} <= set(s2.PAYEE_TYPES), True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
