"""What each page *is*, in the contract's `evidence[].role` vocabulary.

`regions` says which pages belong to a bill. `evidence` says what each of those pages proves,
and the webapp's finance rules are written against it -- "a bill with no receipt must have a
transfer slip" cannot be evaluated at all while every list is empty.

Read from the transcript rather than from the extracted `documentType` field, for the reason that
holds everywhere else in this pipeline: the heading is printed in fixed wording that survives OCR,
while the field is a model output that is null more often than it is wrong.

**A page may hold more than one role, and all of them are kept.** Thai receipts are routinely
headed ใบเสร็จรับเงิน/ใบกำกับภาษี, one paper serving as receipt and tax invoice at once, because
under Thai law it genuinely is both. 15 of the 58 real pages in the corpus carry both headings --
26%, so this is the ordinary case rather than a corner. Confirmed with the webapp team on
2026-09-02: emit every role detected, two entries rather than a choice between them.

Nothing is guessed. A heading we do not recognise produces no entry rather than `other`, because
"we looked and it is something else" and "we did not know" are different claims and only the
second is true.
"""
import re

import regions as R

# Printed heading -> the role it establishes. Several headings map to `receipt`: a toll ticket
# and a wage form are both receipts for money paid, whatever the paper calls itself.
HEADING_ROLES = (
    ("ใบเสร็จรับเงิน", "receipt"),
    ("ใบรับเงิน", "receipt"),
    ("ใบรับค่าผ่านทางพิเศษ", "receipt"),
    ("ใบกำกับภาษี", "tax_invoice"),
    ("บิลเงินสด", "cash_bill"),
)

# `CASH SALE` is the English of บิลเงินสด, and on its own it is a real heading -- three corpus
# pages are Chinese-Thai shop forms headed only `CASH SALE 現兑單`, with no Thai heading at all.
#
# But it is also printed inside the combined title block of ordinary receipts, which list every
# type the pre-printed form can serve as. A live page came back headed
#
#     ใบเสร็จรับเงิน / ใบกำกับภาษี OFFICIAL RECEIPT / TAX INVOICE CASH SALE
#
# and was tagged all three roles. That document is a receipt and a tax invoice; the words CASH
# SALE belong to the form's list of its own possible headings, and the cash in it is the payment
# method, ticked further down as `☑ Cash เงินสด`.
#
# So the English heading counts only when nothing else on the page claims to be a receipt or a
# tax invoice. No corpus page has CASH SALE beside a Thai receipt heading, so this costs nothing
# that currently works and removes the one wrong role that was observed.
ENGLISH_CASH_HEADINGS = ("CASH SALE", "CASH SALES")
_OUTRANKS_ENGLISH_CASH = ("ใบเสร็จรับเงิน", "ใบกำกับภาษี")

# Headings deliberately left unmapped, so that a page carrying one gets no evidence entry:
#
#   ใบส่งของ      a delivery note. Proof that goods arrived, not that money was paid.
#   ใบแจ้งหนี้     a billing note. It requests payment; it does not evidence one.
#
# Both would have to be `other`, and `other` asserts we classified the page. We did not.

# `เลขที่ใบกำกับภาษี` is a receipt citing the invoice number it settles -- the words appear, the
# document is something else. Seven of the twenty-one pages mentioning ใบกำกับภาษี are this form,
# so ignoring the distinction would mislabel a third of them. Removed before matching rather than
# matched around, because the label turns up mid-sentence as often as at a line start.
_LABEL_FORMS = re.compile(r"(?:เลขที่|เลขที|อ้างอิง|ตาม|หมายเลข)\s*ใบกำกับ(?:ภาษี)?")


def roles_for_page(transcript):
    """Every role the page's own headings establish, in a stable order. Possibly empty."""
    if not transcript:
        return []
    text = _LABEL_FORMS.sub(" ", transcript)
    roles = []
    for heading, role in HEADING_ROLES:
        if heading in text and role not in roles:
            roles.append(role)
    if ("cash_bill" not in roles
            and any(h in text for h in ENGLISH_CASH_HEADINGS)
            and not any(h in text for h in _OUTRANKS_ENGLISH_CASH)):
        roles.append("cash_bill")
    return roles


def apply_document_types(candidates, transcripts, page_field="chunkPageIndex"):
    """Tag every page of every bill with the roles its headings establish.

    Runs last among the evidence steps, after `regions.attach_regions` has settled which pages
    each bill owns -- a role is a statement about a page, so the page has to belong somewhere
    first. `add_evidence` is idempotent, so pages already tagged `transfer_slip` or `id_document`
    by the modules that recognised them keep those entries and simply gain any heading roles too.
    """
    tagged = 0
    for candidate in candidates or []:
        for region in candidate.get("regions") or []:
            page = region.get(page_field)
            for role in roles_for_page(transcripts.get(page)):
                R.add_evidence(candidate, role, page, page_field)
                tagged += 1
    return tagged
