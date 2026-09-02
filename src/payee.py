"""Who was paid: a registered company, a shop, or a person.

Policy 88/2568 requires a different evidence set for a ร้านค้า than for a นิติบุคคล, so folding
shops into `company` loses a distinction FA's rules are built on. The contract has carried three
values since the webapp team's round-3 list of 2026-08-19; ours carried two until 2026-09-02,
because that document never reached us.

Deterministic, and deliberately so. F15 measured guards 6-for-6 against prompt rules 0-for-6 on
this pipeline, and the model could not emit `shop` at all until the grammar changed, which means
every shop already in the system is filed under something else. Two real errors visible in saved
responses:

    ร้านข้าวต้มโกยาว                   filed as `individual`  -- a rice-porridge shop
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

# A shop names itself. Only at the start: `ร้าน` inside a longer phrase is often descriptive
# ("ส่งของถึงร้าน"), while a name that opens with it is the trader's own.
SHOP_PREFIXES = ("ร้าน",)

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
    """`company`, `shop`, `individual`, or None, from what the seller calls itself."""
    text = _clean(name)
    if not text:
        return None
    # A person's title outranks everything: `นาย สมชาย ร้านค้า` is a man, not a shop.
    if any(text.startswith(_clean(p)) for p in PERSON_PREFIXES):
        return "individual"
    # A registered name outranks `ร้าน`, because `ร้านอาหารเอบีซี จำกัด` really is a company.
    if any(m.replace(" ", "").upper() in text for m in COMPANY_MARKERS):
        return "company"
    if any(text.startswith(_clean(p)) for p in SHOP_PREFIXES):
        return "shop"
    return None


def classify(name, tax_id):
    """The payee type, or None when nothing on the document settles it.

    The name is asked first. It is the more specific evidence: a tax id says only whether the
    payee is a juristic person, which cannot tell a shop from a company, while the name can.
    A registered shop keeps `shop` even though its id begins with 0, because the policy splits
    on what the payee *is*, not on whether it happens to be registered for tax.
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
