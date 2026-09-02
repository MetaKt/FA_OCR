"""Attach a copy of a national ID card to the ใบรับเงิน it was stapled behind.

Wage categories (5122100 ค่าแรง and its siblings) pay a person, not a shop, and the form itself
prints what has to be attached:

    หมายเหตุ : ให้แนบเอกสารดังต่อไปนี้ด้วยทุกครั้ง
    1) สำเนาบัตรประชาชนของผู้รับเงินพร้อมลงลายมือชื่อรับรองสำเนาถูกต้อง
    2) สลิปโอนเงินของธนาคารที่โอนให้ผู้รับเงิน

So the card is not a separate transaction. It is evidence for the payment on the page before it,
and it holds the one thing that page states badly: the payee's national ID, printed rather than
handwritten, and carrying a check digit that proves it was read correctly.

**This does not re-derive the pairing, and deliberately so.** On the one real pair we have -- pages
20 and 21 of P06690 -- the content cannot establish it: the receipt's only valid 13-digit number
is TEAM's own (the payee's was misread to twelve digits), and the two spellings of the name score
0.897, just under the threshold at which `certlink.names_agree` will call it a match. A rule
demanding two agreeing signals would refuse the only case it exists to handle.

What does establish it is the stapling order, which is FA's own convention and is what
`regions.attach_orphans` already acts on. This module accepts that pairing and spends its effort
on the part a human would do next: check the card against the receipt, and use it where the
receipt is silent.

Bias, following certlink: putting the wrong person's national ID on a payment record is much
worse than leaving the field null, so a conflict refuses outright and an uncertain fill is
labelled uncertain rather than hidden.
"""
import re

import certlink
import regions as R

# Headings that appear on the card and on nothing else in a clearing set. `เลขประจำตัวประชาชน` is
# deliberately absent: the ใบรับเงิน prints it too, as the label of the field the payee fills in,
# so including it would make every wage receipt look like an ID card. Measured on the real pair:
# 8 of these hit the card, 0 hit the receipt.
CARD_MARKERS = ("บัตรประจำตัวประชาชน", "Thai National ID Card", "วันออกบัตร", "วันหมดอายุ",
                "Date of Issue", "Date of Expiry", "เจ้าหน้าที่ออกบัตร", "ศาสนา")

# Two, for the same reason certlink requires two: one heading can survive on a page that is not
# a card, and a card that only produced one marker was not read well enough to trust anyway.
MIN_MARKERS = 2

# The number is printed spaced into groups -- `3 4010 00897 81 9` -- and stage 1 keeps the
# spacing. Hyphens appear on some copies.
#
# Spaces and tabs only, never a newline: a card page also prints a birth date, an issue date, an
# expiry date and a telephone number, and a separator that crossed lines could splice two of them
# into a thirteen-digit run belonging to nobody. The check digit rejects almost all of those, but
# "almost" is not the standard for writing a national ID onto someone's payment record.
_SPACED_ID = re.compile(r"(?<![\d\w])\d(?:[ \t-]*\d){12}(?![\d])")

_NAME_LABEL = re.compile(r"(?:ชื่อตัวและชื่อสกุล|ชื่อ-นามสกุล|ชื่อ–นามสกุล)\s*[:：]?\s*(.+)")

# Money fields. A card page that produced a candidate with none of these is a phantom row: stage 2
# recognised a person and filled in payeeType, but there is no payment on the page.
MONEY_FIELDS = ("originalTotal", "amountBeforeVat", "clearingAmount", "vat", "withholdingTax")


def is_id_card(transcript):
    """True when the page is a copy of a Thai national ID card."""
    return bool(transcript) and sum(m in transcript for m in CARD_MARKERS) >= MIN_MARKERS


def card_national_id(transcript):
    """The one checksum-valid national ID on the card, or None if it is not unambiguous.

    A Thai national ID carries the same mod-11 check digit as a tax id, which is what makes this
    safe to act on: a misread digit almost always fails the check rather than producing a
    different real person's number. Our own company id is excluded -- it can appear on a card
    page when the copy was made onto company letterhead.
    """
    found = []
    for raw in _SPACED_ID.findall(transcript or ""):
        digits = re.sub(r"[^0-9]", "", raw)
        if (digits != certlink.BUYER_TAX_ID and certlink.valid_thai_tax_id(digits)
                and digits not in found):
            found.append(digits)
    return found[0] if len(found) == 1 else None


def card_name(transcript):
    """The name printed on the card, or None.

    Only the Thai line is read. The card also prints `Name:` and `Last name:` separately in
    English, and joining those two is a guess about ordering that buys nothing -- the receipt
    states the name in Thai.
    """
    match = _NAME_LABEL.search(transcript or "")
    if not match:
        return None
    name = re.sub(r"\s+", " ", match.group(1)).strip()
    return name or None


def read_card(transcript):
    """Everything the card establishes on its own, without asking the model."""
    if not is_id_card(transcript):
        return None
    return {"nationalId": card_national_id(transcript), "name": card_name(transcript)}


def _value(candidate, field):
    wrapped = candidate.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


def is_phantom(candidate):
    """A candidate that names a person but reports no payment at all.

    An ID card is not a bill, and stage 2 knows that most of the time -- on the live two-page run
    it returned no candidate for the card page. But asked in isolation it returned one row with
    every field null except `payeeType`, and an empty row in FA's ledger is worse than no row:
    it has to be read before it can be dismissed.
    """
    return all(_value(candidate, f) is None for f in MONEY_FIELDS)


def _owner(page, candidates, page_field):
    """The bill this card was stapled behind: the nearest one starting before it.

    The same rule `regions.attach_orphans` applies to any page with no bill of its own, and it
    is the rule FA's stapling follows. Reimplemented rather than imported because this runs
    before `attach_orphans`, so that the card's page is already spoken for by the time regions
    are built.
    """
    before = [c for c in candidates
              if c.get(page_field) is not None and c.get(page_field) < page]
    return max(before, key=lambda c: c[page_field]) if before else None


def apply_cards(candidates, transcripts, page_field="chunkPageIndex"):
    """Fold every ID card in the chunk into the payment it evidences.

    Returns `(candidates, report)`. Run it after `merge_pages` and `certlink`, and **before**
    `regions.attach_orphans`, so the card's page travels with its bill rather than being adopted
    as a bare orphan.

    Three things happen, in decreasing order of confidence:

      the page joins the bill      always, when an owner exists
      a phantom row is dropped     always -- an ID card is not a ledger row
      sellerTaxId is filled in     only from a checksum-valid number, only when the bill has
                                   none of its own, and never when the names actively disagree
    """
    card_pages = {p for p, t in transcripts.items() if is_id_card(t)}
    if not card_pages:
        return candidates, []

    kept = [c for c in candidates
            if not (c.get(page_field) in card_pages and is_phantom(c))]
    bills = [c for c in kept if c.get(page_field) not in card_pages]
    report = []

    for page in sorted(card_pages):
        card = read_card(transcripts[page])
        target = _owner(page, bills, page_field)
        entry = {"page": page, "card": card, "attached": False, "filledTaxId": None,
                 "nameConflict": False}

        if target is None:
            # A card before any bill in the chunk. attach_orphans will give the page to the first
            # bill, which is the best available guess, but this module will not act on it.
            report.append(entry)
            continue

        R.widen(target, page, page_field)
        entry["attached"] = True

        same = certlink.names_agree(card["name"], _value(target, "sellerName"))
        if same is False:
            # Two different people. Say so and touch nothing: the likeliest cause is that the
            # stapling order is not what we assumed, and filling an ID here would put one
            # person's national ID on another person's payment.
            entry["nameConflict"] = True
            for field in ("sellerName", "sellerTaxId"):
                cell = target.get(field)
                if isinstance(cell, dict) and isinstance(cell.get("confidence"), (int, float)):
                    cell["confidence"] = min(cell["confidence"], 0.3)
            report.append(entry)
            continue

        existing = _value(target, "sellerTaxId")
        already_valid = (isinstance(existing, str)
                         and certlink.valid_thai_tax_id(re.sub(r"[^0-9]", "", existing)))
        if card["nationalId"] and not already_valid:
            # Confidence follows the evidence rather than the source. The number itself is
            # certain -- it is printed and it passes its check digit. What is less certain is
            # that this card belongs to this payment, and that rests on the names: agreeing
            # names make it solid, undecided names leave it an inference from stapling order.
            # 0.45 is below the review UI's 0.5 threshold, so the uncertain case is put in front
            # of a human rather than filed silently.
            target["sellerTaxId"] = {"value": card["nationalId"],
                                     "confidence": 0.9 if same is True else 0.45}
            entry["filledTaxId"] = card["nationalId"]
        report.append(entry)

    return kept, report
