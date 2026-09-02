"""Recognise a bank transfer slip, and attach it to the payment it evidences.

A slip is the proof that money actually left the account. FA staples one behind a ใบรับเงิน --
the wage form prints the requirement itself, "2) สลิปโอนเงินของธนาคารที่โอนให้ผู้รับเงิน" -- and
the webapp has a finance rule keyed on it: a bill with no receipt must have a transfer slip.
That rule cannot be evaluated while every `evidence` list is empty, which is why it currently
fires on every bill in the system.

**A slip is not like an ID card, and the difference is money.** A card states no amount, so a
candidate built from one is obviously empty and safe to drop. A slip states exactly the amount
that was paid, so stage 2 will happily turn it into a bill -- and that bill is the same money as
the receipt beside it. Folding blindly would double-count.

But dropping blindly is worse. R-SLIP-001 exists precisely because a payment sometimes has *no*
receipt, and then the slip is the only record there is. Deleting it would delete the payment.

So a slip is folded away **only when a bill in the chunk states the amount the slip paid**, which
is positive evidence that the two describe one payment. Anything else -- no match, or several --
leaves the slip standing as its own row for FA to judge, still tagged as a slip. That is the same
bias as certlink: a wrong attachment moves money silently, a missed one costs a reviewer seconds.

Verification note: the six real slips available (P06690 pages 192-197) are all Kasikorn K+
*bill-payment* slips paying M-Flow, not wage transfers. The markers below were chosen to survive
that -- the strong three are bank- and purpose-neutral, and `สำเร็จ` covers โอนเงินสำเร็จ and
ทำรายการสำเร็จ as well as the จ่ายบิลสำเร็จ actually seen. The bank list is broader than what
could be tested. Measured on 105 non-slip pages, every marker here has zero false positives; the
true-positive side is proven only for K+ bill payments.
"""
import re

import regions as R

# Verified 6/6 on the real slips and 0/105 on everything else. These are the wording of the slip
# itself rather than of any one bank: the transaction number, the QR verification caption, and
# the memo field.
SLIP_MARKERS = (
    "เลขที่รายการ",
    "สแกนตรวจสอบสลิป",
    "บันทึกช่วยจำ",
    # Covers จ่ายบิลสำเร็จ (seen), โอนเงินสำเร็จ and ทำรายการสำเร็จ (expected but untested).
    "สำเร็จ",
)

# Only ธ.กสิกรไทย could be verified positive; the rest are listed because a wage transfer may
# well come from another app. All of them score zero false positives on the corpus, so a wrong
# guess here costs nothing. Bare city-style names (`กรุงเทพ`) are deliberately absent -- that is
# a place as often as a bank.
BANK_MARKERS = (
    "ธ.กสิกรไทย", "ธนาคารกสิกรไทย", "K PLUS", "K+",
    "ธ.ไทยพาณิชย์", "ธนาคารไทยพาณิชย์", "SCB",
    "ธ.กรุงไทย", "ธนาคารกรุงไทย", "ธ.กรุงเทพ", "ธนาคารกรุงเทพ",
    "ธ.กรุงศรี", "ธนาคารกรุงศรีอยุธยา", "ธ.ทหารไทยธนชาต", "ttb",
    "ธนาคารออมสิน", "พร้อมเพย์", "PromptPay",
)

# Two, as in certlink and personlink. One is not enough: `สำเร็จ` alone is an ordinary Thai word.
MIN_MARKERS = 2

# `จำนวน: 90.00 บาท`. `ค่าธรรมเนียม` is matched separately and never as the amount -- a slip whose
# fee was read as its value would attach to the wrong bill or to none.
_AMOUNT = re.compile(r"จำนวน(?:เงิน)?\s*[:：]?\s*([\d,]+\.\d{2})")
_FEE = re.compile(r"ค่าธรรมเนียม\s*[:：]?\s*([\d,]+\.\d{2})")
_REFERENCE = re.compile(r"เลขที่รายการ\s*[:：]?\s*([A-Za-z0-9]{6,})")

# Satang tolerance, matching arith.
TOLERANCE = 0.05

# What a bill might call the figure a slip would have paid. `clearingAmount` first: on a wage
# receipt the bank moves the net after withholding, which is what the slip shows, while
# originalTotal is the gross before it.
PAID_FIELDS = ("clearingAmount", "originalTotal")


def is_transfer_slip(transcript):
    """True when the page is a bank's record of a completed payment.

    Note what is *not* a marker: the bare word สลิป. The wage receipt prints
    `สลิปโอนเงินของธนาคาร` in its list of required attachments, and eight pages in the corpus
    mention a slip without being one. Talking about a slip is not being a slip.
    """
    if not transcript:
        return False
    hits = sum(m in transcript for m in SLIP_MARKERS)
    hits += any(m in transcript for m in BANK_MARKERS)
    return hits >= MIN_MARKERS


def slip_amount(transcript):
    """What the slip says was paid, or None.

    The fee is excluded explicitly rather than by position. On every real slip it is printed
    directly under the amount and in the same format, so a regex that merely took the first or
    last money on the page would pick it up on some layout eventually.
    """
    fees = {m.group(1) for m in _FEE.finditer(transcript or "")}
    values = [float(m.group(1).replace(",", ""))
              for m in _AMOUNT.finditer(transcript or "") if m.group(1) not in fees]
    values = [v for v in values if v > 0]
    return values[0] if len(set(values)) == 1 else None


def slip_reference(transcript):
    """The bank's transaction number, or None. Unique per payment, so it identifies the slip."""
    match = _REFERENCE.search(transcript or "")
    return match.group(1) if match else None


def read_slip(transcript):
    """Everything the slip establishes on its own."""
    if not is_transfer_slip(transcript):
        return None
    return {"amount": slip_amount(transcript), "reference": slip_reference(transcript)}


def _value(candidate, field):
    wrapped = candidate.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


def _pays(candidate, amount):
    """Does this bill state the figure the slip moved?"""
    for field in PAID_FIELDS:
        stated = _value(candidate, field)
        if isinstance(stated, (int, float)) and abs(float(stated) - amount) <= TOLERANCE:
            return True
    return False


def apply_slips(candidates, transcripts, page_field="chunkPageIndex"):
    """Fold every transfer slip into the payment it evidences, and tag it either way.

    Returns `(candidates, report)`. Run after `merge_pages`, `certlink` and `personlink`, and
    before `regions.attach_orphans`.

    A slip is folded only when exactly one bill in the chunk states the amount it paid. With no
    match the slip stays as its own row -- it may be the only record of that payment -- and with
    several it also stays, because guessing which one would move money onto the wrong bill.
    """
    slip_pages = {p for p, t in transcripts.items() if is_transfer_slip(t)}
    if not slip_pages:
        return candidates, []

    bills = [c for c in candidates if c.get(page_field) not in slip_pages]
    # Decide everything first, then act. Two slips can match one bill: P06690 pages 195 and 196
    # are both 30.00 baht, carry different transaction references and different dates, and the
    # chunk holds a single 30.00 toll ticket. Attaching both would claim one payment had two
    # bank slips proving it, and would drop the second payment out of the chunk's total
    # altogether. Which of the two paid that ticket is not knowable from the amount.
    matches, report = {}, []
    for page in sorted(slip_pages):
        slip = read_slip(transcripts[page])
        entry = {"page": page, "slip": slip, "attached": False, "reason": None}
        report.append(entry)

        if slip["amount"] is None:
            entry["reason"] = "no amount could be read"
            continue

        hits = [c for c in bills if _pays(c, slip["amount"])]
        if len(hits) != 1:
            entry["reason"] = ("no bill states this amount" if not hits
                               else f"{len(hits)} bills state this amount")
            continue
        matches[page] = hits[0]

    # A bill claimed by more than one slip keeps none of them -- the same refusal certlink makes
    # when two purchases fit one certificate. Both slips then stand as their own rows, because
    # over-reporting a payment is something a reviewer sees and losing one is not.
    claims = {}
    for page, target in matches.items():
        claims.setdefault(id(target), []).append(page)

    by_page = {e["page"]: e for e in report}
    folded = set()
    for page, target in matches.items():
        rivals = claims[id(target)]
        if len(rivals) > 1:
            by_page[page]["reason"] = (f"{len(rivals)} slips match this bill "
                                       f"(pages {', '.join(str(p) for p in rivals)})")
            continue
        R.widen(target, page, page_field)
        R.add_evidence(target, "transfer_slip", page, page_field)
        folded.add(page)
        by_page[page]["attached"] = True

    # Only slips that found their payment are folded away. One left standing keeps its own row,
    # and is tagged so the webapp can still see what it is.
    kept = []
    for candidate in candidates:
        page = candidate.get(page_field)
        if page in folded:
            continue
        if page in slip_pages:
            R.add_evidence(candidate, "transfer_slip", page, page_field)
        kept.append(candidate)
    return kept, report
