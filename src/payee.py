"""Who was paid: a juristic person, or a natural one.

This field picks the withholding return. **ภ.ง.ด.3** is filed for a payment to a natural person,
**ภ.ง.ด.53** for one to a juristic person, and they are separate account codes in FA's chart
(2153600 and 2153700). That is the whole job, and the split is binary.

**`shop` was removed 2026-09-03, owner's decision, after one day in the enum.** A ร้าน is not a
third legal category -- unregistered it is a natural person and files ภ.ง.ด.3, registered as
หจก./บริษัท it is juristic and files ภ.ง.ด.53. So the value cut across the split this field
exists to make, and told that a payee was a `shop` FA still could not pick a form. The two claims
supporting it did not survive checking: a cited "Policy 88/2568" appears in no document we hold,
and the webapp team's round-3 list never reached us and their schema file still does not declare
`payeeType` at all. `ร้านข้าวต้มโกยาว` moving `individual` -> `shop` was recorded here as a
genuine correction; for an unregistered porridge shop, `individual` was very likely right.

Deterministic, and deliberately so. F15 measured guards 6-for-6 against prompt rules 0-for-6 on
this pipeline. One real error visible in saved responses:

    การทางพิเศษแห่งประเทศไทย            `individual` in one response, `company` in another,
                                       with the same 0-prefixed juristic tax id both times

Read from the resolved `sellerName` and `sellerTaxId` rather than from the page. The page is no
use here: บริษัท appears on 45 of 54 real pages because TEAM is the buyer on every one of them,
so a raw-text rule would call every document a company.

The first digit of a Thai 13-digit id is the evidence that settles most cases. `0` is issued to a
juristic person -- a company, a partnership, a government body. `1`-`8` is a natural person's
national id, which doubles as their tax id. That is a fact about the number, not a guess about
the document.
"""
import re

import certlink

# A registered business says so in its name. `จำกัด` covers บริษัท…จำกัด and หจก., and the English
# forms appear on the bilingual invoices in the corpus.
COMPANY_MARKERS = ("บริษัท", "บจก", "หจก", "ห้างหุ้นส่วน", "บมจ", "จำกัด", "มหาชน",
                   "CO.,LTD", "CO., LTD", "COMPANY LIMITED", "PUBLIC COMPANY", "PTE")

# A government body or state enterprise: a juristic person, so `company`, so ภ.ง.ด.53.
#
# Added 2026-09-03 after the golden re-score, where `กรมทางหลวง` came back as `shop`. `shop` is
# gone now, but this list is not: without it a state name matches nothing, `classify` returns
# None, and the model's own answer stands unchecked on a payee whose form is not in doubt.
#
# Anchored at the start, unlike COMPANY_MARKERS, because these words are ordinary Thai nouns
# elsewhere in a name. `ร้านค้าสวัสดิการกรมทางหลวง` is a welfare shop run inside a department and
# its registration, not the department's, decides its form -- so it must fall through to the tax
# id rather than being called juristic by the `กรม` buried in its name. A substring test would
# decide it wrongly and confidently.
#
# Deliberately not here: bare `การ` (it prefixes ordinary Thai nouns -- การเดินทาง), and bare
# `สำนักงาน` (สำนักงานบัญชี / สำนักงานทนายความ are private practices, and a sole practitioner is
# `individual`). A name this list does not match keeps today's behaviour exactly.
STATE_PREFIXES = ("กรม", "กระทรวง", "องค์การ", "เทศบาล", "มหาวิทยาลัย", "โรงเรียน", "โรงพยาบาล",
                  "การทางพิเศษ", "การไฟฟ้า", "การประปา", "การรถไฟ", "การท่าเรือ",
                  "ธนาคารแห่งประเทศไทย", "สำนักงานเขต",
                  "DEPARTMENT OF", "MINISTRY OF", "EXPRESSWAY AUTHORITY")

# Thai personal titles. A seller called นาย is a person however the rest of the line reads.
PERSON_PREFIXES = ("นาย", "นาง", "นางสาว", "น.ส.", "ด.ช.", "ด.ญ.", "MR.", "MRS.", "MS.")

_SPACES = re.compile(r"\s+")


def _clean(value):
    return _SPACES.sub("", str(value or "")).upper() or None


def from_tax_id(tax_id):
    """`company` or `individual` from the first digit of a valid Thai id, else None.

    The check digit has to pass first. Stage 1 invents plausible ids -- DT05 page 2 carries a
    corrupted echo of our own -- and classifying a payee from a number that was misread is worse
    than not classifying them.
    """
    digits = re.sub(r"[^0-9]", "", str(tax_id or ""))
    if not certlink.valid_thai_tax_id(digits):
        return None
    if digits[0] == "0":
        return "company"
    return "individual" if digits[0] in "12345678" else None


def from_name(name):
    """`company`, `individual`, or None, from what the seller calls itself."""
    text = _clean(name)
    if not text:
        return None
    # A person's title outranks everything: `นาย สมชาย ร้านค้า` is a man, not his shop.
    if any(text.startswith(_clean(p)) for p in PERSON_PREFIXES):
        return "individual"
    # A registered form makes the payee juristic, whatever else the name says: `ร้านอาหารเอบีซี
    # จำกัด` files ภ.ง.ด.53.
    if any(m.replace(" ", "").upper() in text for m in COMPANY_MARKERS):
        return "company"
    # A state body is juristic too, so it answers `company` for the same reason. Its position here
    # is not load-bearing: it returns the same value as the branch above it, and a name can only
    # start with one thing, so it cannot race the start-anchored branch above.
    if any(text.startswith(_clean(p)) for p in STATE_PREFIXES):
        return "company"
    # A bare `ร้าน…` deliberately settles nothing. Registration decides the form, and the name
    # does not state it -- `ร้านข้าวต้มโกยาว` could be either. The tax id below is the evidence
    # that can answer, and when there is none the model's guess is better than ours.
    return None


def classify(name, tax_id):
    """The payee type, or None when nothing on the document settles it.

    The name is still asked first, though the reason changed when `shop` left. It is no longer
    that the name is more expressive -- with two values the id's first digit answers the question
    outright. It is that the id on the page is not reliably the *payee's*: a ใบรับเงิน routinely
    carries TEAM's own 13 digits as the payer, and `personlink` exists because the payee's own
    number on that form is misread often enough to need a stapled ID card to fix it. A name
    beginning นาย is the payee's own words about themselves.

    So the order is: what the payee calls itself, then a number that passed its check digit. A
    name that states nothing -- `ร้าน…` on its own -- falls through to the id on purpose.
    """
    return from_name(name) or from_tax_id(tax_id)


def _value(candidate, field):
    wrapped = candidate.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


# Set when the guard supplies or replaces the model's answer. Not 1.0: the rules above are sound
# but the name they read is still OCR, and R5 forbids a flat confidence across a response.
DECIDED_CONFIDENCE = 0.9


def apply_payee_types(candidates):
    """Fill or correct `payeeType` from the seller's own name and id. Returns a report.

    Corrects rather than only filling gaps. Until 2026-09-02 the grammar had no `shop` in it, so
    every shop in the system is currently filed as something else and leaving the model's answer
    alone would preserve the error this exists to fix. A candidate the rules cannot settle keeps
    whatever the model said, untouched.
    """
    report = []
    for candidate in candidates or []:
        decided = classify(_value(candidate, "sellerName"), _value(candidate, "sellerTaxId"))
        if decided is None:
            continue
        before = _value(candidate, "payeeType")
        if before == decided:
            continue
        candidate["payeeType"] = {"value": decided, "confidence": DECIDED_CONFIDENCE}
        report.append({"candidateIndex": candidate.get("candidateIndex"),
                       "sellerName": _value(candidate, "sellerName"),
                       "was": before, "now": decided})
    return report
