"""Fixtures for src/payee.py -- a juristic payee, or a natural one.

Every seller name below is real, taken from saved responses this pipeline produced. One of them is
the error the module exists to fix: a government agency filed `individual` in one response and
`company` in another, with the same juristic tax id both times.

`shop` was a third value for one day, 2026-09-02 to 2026-09-03. The fixtures that asserted it are
rewritten rather than deleted, because what they now assert is the opposite answer to the same
question, and the reasoning is worth being able to read back.

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


print("grammar-01  two values, because the field picks between two withholding returns")
# ภ.ง.ด.3 for a natural person, ภ.ง.ด.53 for a juristic one. `shop` lived here for one day
# (2026-09-02 to 2026-09-03) and was removed: a ร้าน is one of these two depending on
# registration, so the value cut across the split instead of extending it.
check("two values", s2.PAYEE_TYPES, ["company", "individual"])

print("\nname-01  a registered business names itself")
for name in ("บริษัท ดีครับผม จำกัด", "บริษัท สยามสตีล กัลวาไนซึ่ง จำกัด",
             "หจก. สมชายการช่าง", "บมจ. ทางด่วนและรถไฟฟ้ากรุงเทพ",
             "TRIP.COM TRAVEL SINGAPORE PTE. LTD."):
    check(name[:40], P.from_name(name), "company")

print("\nname-02  a bare ร้าน settles nothing, on purpose")
# The name does not say whether the trader is registered, and registration is what picks the
# return: unregistered files ภ.ง.ด.3, registered as หจก./บริษัท files ภ.ง.ด.53. So this defers to
# the tax id rather than guessing. These two asserted "shop" for one day.
check("ร้านข้าวต้มโกยาว undecided", P.from_name("ร้านข้าวต้มโกยาว"), None)
check("ร้านวัสดุก่อสร้างพรชัย undecided", P.from_name("ร้านวัสดุก่อสร้างพรชัย"), None)

print("\nname-03  a registered form decides it, a personal title outranks that")
check("ร้านอาหารเอบีซี จำกัด is juristic", P.from_name("ร้านอาหารเอบีซี จำกัด"), "company")
check("a company marker anywhere in the name counts",
      P.from_name("บจก เอบีซี ส่งของถึงร้านค้า"), "company")
check("นาย สมชาย ร้านค้า is a person", P.from_name("นาย สมชาย ร้านค้า"), "individual")
check("นายปรีชา ขุนแก้ว", P.from_name("นายปรีชา ขุนแก้ว"), "individual")
check("นางสาว สมหญิง ใจดี", P.from_name("นางสาว สมหญิง ใจดี"), "individual")

print("\nname-04  a name that settles nothing settles nothing")
check("วิว การ์เดน รีสอร์ท", P.from_name("วิว การ์เดน รีสอร์ท"), None)
# `การทางพิเศษแห่งประเทศไทย` used to assert None here, and that was right while the name lists
# held only companies, shops and people -- it is none of the three, and its id carried the case.
# STATE_PREFIXES answers it by name now, so it moved to state-01 rather than being deleted.
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

print("\nclassify-01  the name is asked first, then an id that passed its check digit")
# Name first, but not because it is more expressive -- with two values the id's first digit
# answers outright. It is that the id on the page is often not the payee's: a ใบรับเงิน routinely
# carries TEAM's own 13 digits as the payer, which is why personlink exists.
check("ร้าน with a juristic id is juristic",
      P.classify("ร้านข้าวต้มโกยาว", "0994000165421"), "company")
check("the same ร้าน with a personal id is a person",
      P.classify("ร้านข้าวต้มโกยาว", "3401000897819"), "individual")
check("and with no id at all it stays undecided",
      P.classify("ร้านข้าวต้มโกยาว", None), None)
check("a nameless payee falls back to the id",
      P.classify(None, "0994000165421"), "company")
check("neither", P.classify("วิว การ์เดน รีสอร์ท", None), None)

print("\napply-01  the real error in saved responses")
# `ร้านข้าวต้มโกยาว` was filed `individual`, and this file once called that an error worth fixing
# and moved it to `shop`. Under two values `individual` is very likely correct for an
# unregistered porridge shop, so the guard now has nothing to say about it and leaves it.
porridge = cand("ร้านข้าวต้มโกยาว", None, "individual", 0)
exat = cand("การทางพิเศษแห่งประเทศไทย", "0994000165421", "individual", 1)
report = P.apply_payee_types([porridge, exat])
check("the shop keeps the model's answer", porridge["payeeType"]["value"], "individual")
check("the agency follows its juristic id", exat["payeeType"]["value"], "company")
check("only the agency is reported", [(r["was"], r["now"]) for r in report],
      [("individual", "company")])
check("and the new value is not passed off as certain",
      exat["payeeType"]["confidence"], P.DECIDED_CONFIDENCE)

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
c = cand("กรมทางหลวง", None, None)
P.apply_payee_types([c])
check("filled", c["payeeType"]["value"], "company")

print("\napply-05  malformed input never raises")
check("empty list", P.apply_payee_types([]), [])
check("None", P.apply_payee_types(None), [])
check("no fields at all", P.apply_payee_types([{}]), [])

print("\nstate-01  a government body is a juristic person, so ภ.ง.ด.53, so `company`")
# Found by the golden re-score 2026-09-03, when `กรมทางหลวง` came back `shop`: nothing matched a
# state name, `classify` returned None, and the model's own answer stood unchecked. `shop` is gone
# now, but the fall-through it exposed is real and this list is what closes it.
for name in ("กรมทางหลวง", "กรมสรรพากร", "การทางพิเศษแห่งประเทศไทย", "กระทรวงการคลัง",
             "องค์การขนส่งมวลชนกรุงเทพ", "มหาวิทยาลัยเกษตรศาสตร์", "โรงพยาบาลรามาธิบดี",
             "เทศบาลนครเชียงใหม่", "การไฟฟ้านครหลวง", "สำนักงานเขตบึงกุ่ม"):
    check(f"{name}", P.classify(name, None), "company")

print("\nstate-02  the id agrees where the document carries one")
# EXAT's id is checksum-valid and 0-prefixed, so name and number reach `company` independently.
check("การทางพิเศษ by name alone", P.from_name("การทางพิเศษแห่งประเทศไทย"), "company")
check("and by its id alone", P.from_tax_id("0994000165421"), "company")

print("\nstate-03  anchored at the start, because these are ordinary Thai nouns mid-name")
check("a private hospital company is juristic via จำกัด",
      P.classify("บริษัท โรงพยาบาลกรุงเทพ จำกัด (มหาชน)", None), "company")
# A welfare shop run inside a department: its own registration picks its form, not the
# department's. A substring test would see `กรม` and answer `company` confidently and wrongly.
check("a welfare shop inside a department falls through to its id",
      P.classify("ร้านค้าสวัสดิการกรมทางหลวง", None), None)

print("\nstate-04  a person's title still outranks everything")
check("นาย before a state word", P.classify("นายสมชาย กรมเกษตร", None), "individual")

print("\nstate-05  names deliberately left unmatched keep the old behaviour")
# `สำนักงานบัญชี`/`สำนักงานทนายความ` are private practices and a sole practitioner is a person, so
# bare `สำนักงาน` is not a marker. `การ` prefixes ordinary Thai nouns. Both must stay undecided.
for name in ("สำนักงานบัญชีเอบีซี", "สำนักงานทนายความสมชาย", "การเดินทางสบายใจ",
             "วิว การ์เดน รีสอร์ท"):
    check(f"{name} undecided", P.classify(name, None), None)

print("\nstate-06  the new list cannot change an answer the old rules already had")
# Every marker returns `company`, and both branches that could reach it first -- person titles and
# COMPANY_MARKERS -- are checked above it. So no name that was decided before is decided
# differently now; the list can only fill Nones.
before = {"บริษัท ก จำกัด": "company", "นายปรีชา ขุนแก้ว": "individual",
          "หจก. ข ขนส่ง": "company"}
check("previously-decided names unchanged",
      {n: P.classify(n, None) for n in before} == before, True)

print("\napply-06  the state rule corrects the model, not just fills a gap")
c = cand("กรมทางหลวง", None, "individual")
report = P.apply_payee_types([c])
check("corrected", c["payeeType"]["value"], "company")
check("and reported", [(r["was"], r["now"]) for r in report], [("individual", "company")])

print("\ncontract-01  every value emitted is one the contract allows")
values = {P.classify(n, t) for n, t in
          (("ร้านข้าวต้มโกยาว", None), ("บริษัท ก จำกัด", None), ("นายปรีชา ขุนแก้ว", None),
           (None, "0994000165421"), ("วิว การ์เดน รีสอร์ท", None), ("กรมทางหลวง", None),
           ("มหาวิทยาลัยเกษตรศาสตร์", None), ("DEPARTMENT OF HIGHWAYS", None))}
check("no value outside the enum", values - {None} <= set(s2.PAYEE_TYPES), True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
