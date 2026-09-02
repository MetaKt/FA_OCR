"""Does the money on this bill add up? Deterministic, no model, no network.

A Thai bill states more numbers than it has degrees of freedom, and that redundancy is free
error detection we were not using. Two identities hold on every document FA sends:

    amountBeforeVat + vat = originalTotal
    vat = amountBeforeVat x 7%          -- Thailand has exactly one VAT rate
    withholdingTax = amountBeforeVat x one of seven legal rates

So an OCR digit error usually breaks an equation rather than hiding. Measured on the ใบรับเงิน in
eval/transcripts/adv-p20-ubrapngern.txt: stage 1 read the gross as 24,826.80 when the paper says
24,226.80. Nothing about "24,826.80" looks wrong on its own -- but 726.80 / 24,826.80 is 2.93%,
which is not a rate that exists in Thai law, while 726.80 / 24,226.80 is exactly 3%. One
multiplication finds what no amount of reading the number again would.

**This flags, it does not repair.** When an equation fails we know the set is wrong, not which
member of it is -- the gross could be misread, or the withholding tax could be. Lowering
confidence puts the field in front of the reviewer, who has the paper. Rewriting it would put a
confident wrong number in front of them instead, and a plausible wrong number is worse than a
flagged one (the same reasoning as relatedDocumentNumber in stage2_extract).

Two checks that look obvious are deliberately absent:

  clearingAmount   means different things on different documents. On the real bills measured
                   live it equals originalTotal (5010.81 and 1712.00 both); on a ใบรับเงิน the
                   category rule defines it as the net after withholding. There is no single
                   identity to test, so testing one would fire on correct bills.
  vat == 0         is a real value, not a missing one -- กรมทางหลวง toll tickets print
                   "Baht(Non Vat)" and page 199 came back total=30, before=30, vat=0. Correct.
"""

# Thailand's VAT rate. One rate, no exemptions expressible in these fields -- a zero-rated or
# exempt document reports vat = 0, which is checked for separately rather than divided by.
VAT_RATE = 0.07

# The withholding rates that exist in Thai law. A computed rate outside this set means one of the
# two numbers behind it is misread; that is the entire finding.
WHT_RATES = (0.01, 0.015, 0.02, 0.03, 0.05, 0.10, 0.15)

# Satang rounding, plus a hair for large invoices where the source itself rounded each line.
# Deliberately tight: the errors worth catching are digits, and a digit is worth at least 0.01
# of the column it sits in -- never 5 satang.
def tolerance(base):
    return 0.05 + abs(base or 0.0) * 1e-4


def _v(candidate, field):
    """The value of one contract field, or None. Every field is {"value", "confidence"}."""
    cell = candidate.get(field)
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    return value if isinstance(value, (int, float)) else None


def check(candidate):
    """Return a list of findings. Empty means every equation this bill supports held.

    A finding is (code, message, [fields to distrust]). Fields that are null are not evidence of
    anything, so a bill that states only a total -- a toll ticket -- produces no findings rather
    than three.
    """
    total = _v(candidate, "originalTotal")
    base = _v(candidate, "amountBeforeVat")
    vat = _v(candidate, "vat")
    wht = _v(candidate, "withholdingTax")
    out = []

    if base is not None and vat is not None and total is not None:
        if abs(base + vat - total) > tolerance(total):
            out.append(("VAT_SUM",
                        f"amountBeforeVat {base:,.2f} + vat {vat:,.2f} = {base + vat:,.2f}, "
                        f"but originalTotal is {total:,.2f}",
                        ["amountBeforeVat", "vat", "originalTotal"]))

    # vat == 0 is a non-VAT document stating so. Only a non-zero VAT claims to be 7% of something.
    if base is not None and vat is not None and vat != 0:
        expected = round(base * VAT_RATE, 2)
        if abs(vat - expected) > tolerance(base):
            rate = (vat / base * 100) if base else float("inf")
            out.append(("VAT_RATE",
                        f"vat {vat:,.2f} on amountBeforeVat {base:,.2f} is {rate:.2f}%, "
                        f"not 7% (7% would be {expected:,.2f})",
                        ["vat", "amountBeforeVat"]))

    # Thai withholding is computed on the pre-VAT subtotal, never on the VAT-inclusive total.
    if base is not None and wht is not None and wht != 0:
        near = min(WHT_RATES, key=lambda r: abs(wht - round(base * r, 2)))
        if abs(wht - round(base * near, 2)) > tolerance(base):
            rate = (wht / base * 100) if base else float("inf")
            out.append(("WHT_RATE",
                        f"withholdingTax {wht:,.2f} on amountBeforeVat {base:,.2f} is "
                        f"{rate:.2f}%, which is not a legal rate "
                        f"({', '.join(f'{r * 100:g}%' for r in WHT_RATES)})",
                        ["withholdingTax", "amountBeforeVat"]))
    return out


# What a distrusted field's confidence is capped at. Not zero: the number is probably still
# roughly right and blanking it would lose the reviewer their starting point. Low enough that a
# webapp sorting or thresholding on confidence surfaces it.
SUSPECT_CONFIDENCE = 0.3


def apply(candidates):
    """Lower the confidence of every field caught in a failing equation. Returns a report.

    Confidence is only ever lowered, never raised -- a field the model was already unsure about
    does not become more trustworthy by being arithmetically consistent.
    """
    report = []
    for candidate in candidates or []:
        findings = check(candidate)
        for code, message, fields in findings:
            for field in fields:
                cell = candidate.get(field)
                if isinstance(cell, dict) and isinstance(cell.get("confidence"), (int, float)):
                    cell["confidence"] = min(cell["confidence"], SUSPECT_CONFIDENCE)
        if findings:
            report.append({"candidateIndex": candidate.get("candidateIndex"),
                           "chunkPageIndex": candidate.get("chunkPageIndex"),
                           "findings": [(c, m) for c, m, _ in findings]})
    return report
