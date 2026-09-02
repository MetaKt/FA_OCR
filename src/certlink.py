"""Attach a withholding-tax certificate to the bill it belongs to.

A ใบเสร็จ and its หนังสือรับรองการหักภาษี ณ ที่จ่าย are two documents describing one payment,
and FA wants one ledger row. `merge.py` cannot do this: it joins pages of a single bill, keys on
adjacency, and refuses any pair whose document numbers disagree -- but a certificate carries its
own WHT number, sits pages away from its receipt, and never repeats the invoice number (0 of 3
real samples did).

Everything here is deterministic. Stage 2 reads certificates unreliably -- on p090 it lost the
tax entirely and the page was then dropped for having no amount -- so the numbers are recovered
from the transcript instead, where the form's fixed legal shape makes them recoverable and where
the arithmetic proves itself.

Bias, following merge.py: a wrong attachment silently moves money onto the wrong bill, while a
missed one costs a reviewer a few seconds. So every rule needs positive evidence, and an
ambiguous certificate is emitted as its own row for FA rather than guessed at.
"""
import re
from difflib import SequenceMatcher

import regions as R

BUYER_TAX_ID = "0107561000030"

# Thai withholding rates in ordinary use. A pair of amounts whose ratio is not one of these is
# not a base/tax pair, which is what lets the parser validate itself instead of trusting a model.
LEGAL_RATES = (1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 15.0)

CERT_MARKERS = ("หนังสือรับรองการหักภาษี", "ผู้ถูกหักภาษี", "50 ทวิ", "ผู้มีหน้าที่หักภาษี")

_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")
_ID = re.compile(r"\b\d{13}\b")


def is_certificate(transcript):
    """True when the page is a withholding-tax certificate.

    Two independent headings are required. An ordinary receipt often prints a หัก ณ ที่จ่าย line
    and must not qualify -- verified against p084, p088 and p120.
    """
    return bool(transcript) and sum(m in transcript for m in CERT_MARKERS) >= 2


def valid_thai_tax_id(digits):
    """Mod-11 check digit, the same one printed on every Thai tax id.

    Stage 1 invents plausible ids: DT05 page 2 carries `0107685000030`, a corrupted echo of our
    own id that appears nowhere on the paper. It fails this check; the two real ids pass.
    """
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(d) * (13 - i) for i, d in enumerate(digits[:12]))
    return (11 - total % 11) % 10 == int(digits[12])


def counterparty_tax_id(transcript):
    """The seller's tax id as printed on the certificate, or None if it is not unambiguous.

    Not read from a field: stage 2 filed this id under `buyerTaxId` on DT05 and under
    `sellerTaxId` on the other two, so the field name cannot be trusted. Ours is excluded
    because TEAM is the withholder on every certificate in a clearing set.
    """
    found = []
    for d in _ID.findall(transcript or ""):
        if d != BUYER_TAX_ID and valid_thai_tax_id(d) and d not in found:
            found.append(d)
    return found[0] if len(found) == 1 else None


def base_and_tax(transcript):
    """(base, tax, rate) when the certificate covers exactly one payment, else None.

    The right pair proves itself -- the tax is a legal percentage of the base -- so this needs
    no knowledge of where on the form the numbers sit. Several distinct bases mean the
    certificate covers several payments; splitting that across bills would be guesswork, so it
    returns None and the certificate goes to a human.
    """
    amounts = []
    for raw in _AMOUNT.findall(transcript or ""):
        value = float(raw.replace(",", ""))
        if value > 0 and value not in amounts:
            amounts.append(value)

    pairs = []
    for base in amounts:
        for tax in amounts:
            if tax >= base:
                continue
            rate = tax / base * 100
            if any(abs(rate - r) < 0.01 for r in LEGAL_RATES):
                pair = (base, tax, round(rate, 2))
                if pair not in pairs:
                    pairs.append(pair)

    if not pairs or len({p[0] for p in pairs}) > 1:
        return None
    return pairs[0]


def read_certificate(transcript):
    """Everything we can establish about a certificate page without asking the model."""
    if not is_certificate(transcript):
        return None
    figures = base_and_tax(transcript)
    return {"taxId": counterparty_tax_id(transcript),
            "base": figures[0] if figures else None,
            "tax": figures[1] if figures else None,
            "rate": figures[2] if figures else None}


def _value(candidate, field):
    wrapped = candidate.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


def _money_equal(a, b):
    return a is not None and b is not None and abs(float(a) - float(b)) < 0.01


def _identity(candidate):
    """What makes two candidates the same purchase rather than two purchases.

    FA staples the invoice, the receipt and the customer copy together -- p084/p085 are one
    purchase, p087/p088/p089 another -- so a certificate legitimately matches several
    candidates. Collapsing them here keeps that from reading as ambiguity.
    """
    return (_value(candidate, "sellerTaxId"),
            _value(candidate, "amountBeforeVat"),
            _value(candidate, "documentDate"))


def _pages_of(candidate, page_field):
    """Every page this candidate was assembled from, including any merge_pages folded in."""
    pages = list(candidate.get("_pages") or [])
    own = candidate.get(page_field)
    if own is not None and own not in pages:
        pages.append(own)
    return pages


def tax_ids_for(candidate, transcripts, page_field="chunkPageIndex"):
    """Every valid non-TEAM tax id available for this bill, from the field and from the page.

    The extracted field is not enough. On DT05 the seller's id is printed plainly in the
    letterhead and appears in the transcript, but `resolve_parties` left `sellerTaxId` null, so
    a field-only match found nothing and the certificate went unattached. Reading the page as
    well is the same move the certificate side already makes, and for the same reason: which
    party a number belongs to is decided badly, but the digits themselves are read reliably.
    """
    ids = []
    field = _value(candidate, "sellerTaxId")
    if isinstance(field, str) and valid_thai_tax_id(field) and field != BUYER_TAX_ID:
        ids.append(field)
    for page in _pages_of(candidate, page_field):
        for d in _ID.findall(transcripts.get(page) or ""):
            if d != BUYER_TAX_ID and valid_thai_tax_id(d) and d not in ids:
                ids.append(d)
    return ids


_BRANCH_SUFFIX = re.compile(r"[\(\s]*(สำนักงานใหญ่|สาขาที่\s*\d+|สาขา\s*\d+)[\)\s]*$")


def _norm_name(value):
    """A company name reduced to what two documents can be expected to agree on.

    The same vendor prints its name with and without `(สำนักงานใหญ่)`, with and without an
    English translation after it, and with inconsistent spacing. None of that is a difference
    of identity.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    name = re.sub(r"\s+", " ", value).strip()
    while True:
        stripped = _BRANCH_SUFFIX.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    name = re.sub(r"\s+", "", name).lower()
    return name or None


def names_agree(a, b):
    """True, False, or None when the two names are too close to call.

    Three-valued on purpose. Exact comparison is wrong here: the same vendor is transcribed
    `อินเตอร์เนชันแนล` on the certificate and `อินเตอร์เนชั่นแนล` on the receipt -- one character,
    pure OCR noise -- and treating that as a conflict refused a correct attachment on p086.

    So a near match counts as agreement, a clearly different name counts as a conflict, and the
    band between them counts as nothing. Names are the weakest of the four signals and are never
    the sole basis for attaching, so leaving the middle undecided costs little.
    """
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return None                                  # not stated, so neither agree nor conflict
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 8 and longer.startswith(shorter):
        return True                                  # one carries a translation or a branch
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.90:
        return True
    if ratio < 0.60:
        return False
    return None


# The form's own headings, which stage 2 returns as `sellerName` when it cannot find a real one.
# A label is not a name, and treating it as one manufactures a conflict with every bill.
_FORM_LABELS = ("ผู้ถูกหักภาษี", "ผู้มีหน้าที่หักภาษี", "หนังสือรับรอง", "ผู้จ่ายเงิน")


def cert_seller_name(cert_candidate):
    """The vendor named on the certificate, or None if stage 2 returned a form heading."""
    value = _value(cert_candidate or {}, "sellerName")
    if not isinstance(value, str) or any(l in value for l in _FORM_LABELS):
        return None
    return value


def compare(cert, cert_candidate, bill, bill_tax_ids=()):
    """(agreements, conflicts) across tax id, base amount, date and seller name.

    Each signal is three-valued. Both sides state it and match -> agreement. Both state it and
    differ -> conflict. Either side silent -> neither, because stage 1 drops fields routinely and
    a missing value is not evidence of anything.

    Any conflict is disqualifying. A certificate whose base disagrees with the bill's subtotal is
    not that bill's certificate -- withholding is computed on that subtotal -- and agreeing on
    the other signals would not make it so. The same holds for a tax id naming a different vendor.
    """
    agree = conflict = 0

    if cert.get("taxId") is not None and bill_tax_ids:
        agree, conflict = ((agree + 1, conflict) if cert["taxId"] in bill_tax_ids
                           else (agree, conflict + 1))

    base, subtotal = cert.get("base"), _value(bill, "amountBeforeVat")
    if base is not None and subtotal is not None:
        agree, conflict = ((agree + 1, conflict) if _money_equal(base, subtotal)
                           else (agree, conflict + 1))

    left, right = _value(cert_candidate or {}, "documentDate"), _value(bill, "documentDate")
    if left and right:
        agree, conflict = (agree + 1, conflict) if left == right else (agree, conflict + 1)

    same = names_agree(cert_seller_name(cert_candidate), _value(bill, "sellerName"))
    if same is not None:
        agree, conflict = (agree + 1, conflict) if same else (agree, conflict + 1)

    return agree, conflict


def find_target(cert, candidates, transcripts=None, page_field="chunkPageIndex",
                cert_candidate=None):
    """The one candidate a certificate belongs to, or None.

    **Two of four must agree and none may conflict**: counterparty tax id, base amount, document
    date, seller name.

    One signal is never enough. A tax id matches every bill from that vendor in the chunk -- the
    owner's objection, and a real one. An amount matches any coincidence of round numbers. A date
    matches half a clearing set. Two independent agreements is the threshold, and any conflict
    refuses outright however many others agree.

    Four signals rather than the two this started with, because no signal is reliably present:
    on DT05 the serving path transcribed the certificate page with **no 13-digit number at all**,
    five runs out of five, while reading the amounts perfectly every time. Requiring the tax id
    would lose that case; requiring any fixed pair would lose another.

    If two genuinely different purchases fit, nothing is attached.
    """
    if not cert or cert.get("base") is None:
        return None

    transcripts = transcripts or {}
    hits = []
    for candidate in candidates:
        ids = tax_ids_for(candidate, transcripts, page_field)
        agree, conflict = compare(cert, cert_candidate, candidate, ids)
        if conflict == 0 and agree >= 2:
            hits.append(candidate)

    if not hits or len({_identity(c) for c in hits}) > 1:
        return None            # nothing fits, or two genuinely different purchases do
    return hits[0]


def apply_certificates(candidates, transcripts, page_field="chunkPageIndex"):
    """Fold every certificate in the chunk into the bill it belongs to.

    Returns the candidate list with certificate pages either merged away or left standing as
    their own row. `candidates` is the merged output of `merge.merge_pages`; `transcripts` maps
    page index to that page's text.

    Only `withholdingTax` and `withholdingTaxRate` cross over. Everything else a certificate
    produced was wrong on all three real samples -- p086 reported the payment base as
    `originalTotal` and the tax as `vat` -- so this is a whitelist, not a gap fill.
    """
    cert_pages = {p for p, t in transcripts.items() if is_certificate(t)}
    if not cert_pages:
        return candidates, []

    bills = [c for c in candidates if c.get(page_field) not in cert_pages]
    report = []

    for page in sorted(cert_pages):
        cert = read_certificate(transcripts[page])
        # The certificate's own date and seller name come from its extracted candidate rather
        # than the transcript: stage 2 read both correctly on all three real samples, and
        # parsing a date off the form means guessing whether the year is Buddhist.
        on_page = next((c for c in candidates if c.get(page_field) == page), None)
        target = find_target(cert, bills, transcripts, page_field, cert_candidate=on_page)
        if target is None:
            report.append({"page": page, "attached": False, "cert": cert})
            continue
        if _value(target, "withholdingTax") in (None, 0, 0.0):
            target["withholdingTax"] = {"value": cert["tax"], "confidence": 0.95}
            target["withholdingTaxRate"] = {"value": cert["rate"], "confidence": 0.95}
        # The certificate's own row disappears below. Its page has to move to the bill or the
        # scan is lost from the evidence bundle -- and a stapled 50 ทวิ is the page FA most need
        # to see, since it is the only document that states the tax withheld.
        R.widen(target, page, page_field)
        report.append({"page": page, "attached": True, "cert": cert,
                       "target_page": target.get(page_field)})

    attached = {r["page"] for r in report if r["attached"]}
    kept = [c for c in candidates
            if c.get(page_field) not in attached]
    for index, candidate in enumerate(kept):
        candidate["candidateIndex"] = index
    return kept, report
