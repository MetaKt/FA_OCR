"""Phase 04 — join the candidates of a bill that runs across more than one page.

Pages are read independently, so a hotel folio with its line items on page 2 and its totals on
page 3 arrives as two candidates. R4 makes bill boundaries the model's job, and two candidates
for one bill means two wrong ledger rows however well each field was read.

`regions` left the contract on 2026-08-11, so the geometric half of the original design -- "the
regions overlap on the shared page" -- has nothing to stand on. What is left is the page index
and the field values, which is what this module uses. `regions` came back on 2026-08-28 carrying
whole-page boxes only (src/regions.py), so it still offers this module no geometry to merge on:
the decision below is unchanged, and what changed is that the result is now reported honestly.

**The bias is deliberately against merging.** R12 sends ambiguity to the human as two candidates.
A false merge destroys a bill: the second page's total silently replaces the first's, and nothing
downstream can tell. A false split costs a reviewer ten seconds. So every rule here needs positive
evidence, and a page that simply looks similar to the one before it is not evidence.
"""
import re

import regions as R
from certlink import names_agree

# Enough to tell two bills apart. Deliberately not sellerAddress or the buyer block: the buyer is
# TEAM on every page in a clearing set, so buyer agreement is not evidence of anything.
IDENTITY_FIELDS = ("originalDocumentNumber", "sellerName", "originalTotal", "documentDate")

# Compared as text, one character apart is as different as two unrelated companies. That is wrong
# for a name and right for everything else here: a document number or a total that differs by a
# character IS a different bill, while a seller name that differs by a character is the same
# vendor read twice. `names_agree` is three-valued for that reason -- shared with certlink.py so
# the two files cannot drift apart, which is exactly how this bug survived in both of them.
FUZZY_FIELDS = ("sellerName",)

# A page that says so itself. Far stronger than field agreement, and the only signal available
# when a continuation page carries totals and nothing else.
#   หน้า 1/2 · หน้าที่ 2 จาก 2 · Page 1 of 3 · 1/2
_CONTINUATION = re.compile(
    r"(?:หน้า(?:ที่)?|page)\s*(\d{1,2})\s*(?:/|of|จาก)\s*(\d{1,2})", re.I)


def page_marker(transcript):
    """(page, of) if the page states its own position in a multi-page document, else None.

    Only pairs where 1 <= page <= of and of > 1 count. `1/2` also appears as a fraction, a date
    fragment and a lot number, so an implausible pair is treated as noise rather than a marker.
    """
    if not transcript:
        return None
    for m in _CONTINUATION.finditer(transcript):
        page, of = int(m.group(1)), int(m.group(2))
        if of > 1 and 1 <= page <= of and of <= 20:
            return page, of
    return None


def _value(candidate, field):
    wrapped = candidate.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


def _norm(field, value):
    if value is None:
        return None
    if field in ("originalTotal",):
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value).strip().lower()
    return re.sub(r"\s+", "", str(value)).lower() or None


def _verdict(field, a, b):
    """True, False, or None for one identity field: agrees, conflicts, or says nothing.

    None covers both "one side is silent" and, for a name, "too close to call". Page 1 of the
    ดีครับผม bill prints `บริษัท ดีครับผม จำกัด` and page 2 prints the same name with
    `DEEKRUBPHOM CO.,LTD.` after it. Compared literally that is a conflict, and a conflict refuses
    the merge outright however many other fields agree -- so a bill whose two pages agree on both
    the total and the date was being split in two.
    """
    left, right = _value(a, field), _value(b, field)
    if field in FUZZY_FIELDS:
        return names_agree(left, right)
    left, right = _norm(field, left), _norm(field, right)
    if left is None or right is None:
        return None
    return left == right


def agreement(a, b):
    """How many identity fields both state and agree on.

    Both must state it. Two nulls are not agreement -- a continuation page states almost nothing,
    so counting nulls would make every pair of sparse pages look like the same bill.
    """
    return sum(_verdict(field, a, b) is True for field in IDENTITY_FIELDS)


def conflicts(a, b):
    """Identity fields where both state a value and the values genuinely differ.

    One conflict is enough to refuse a merge even when a page marker says otherwise: two
    different document numbers on consecutive pages are two bills that happen to be stapled
    together, which is the normal shape of a clearing set.
    """
    return sum(_verdict(field, a, b) is False for field in IDENTITY_FIELDS)


def same_bill(a, b, marker_a=None, marker_b=None):
    """Should these two candidates on adjacent pages be one bill?

    Two independent routes, both requiring positive evidence and neither tolerating a conflict:

    1. **The pages say so.** `หน้า 1/2` followed by `หน้า 2/2` is the document telling us
       directly, and it is the only thing that works when the continuation page carries a total
       and nothing else.
    2. **Two identity fields agree.** The threshold is two rather than one because a single match
       is routinely a coincidence -- many receipts in one clearing set share a date, and round
       totals like 500.00 repeat.
    """
    if conflicts(a, b):
        return False
    if marker_a and marker_b:
        page_a, of_a = marker_a
        page_b, of_b = marker_b
        if of_a == of_b and page_b == page_a + 1:
            return True
    return agreement(a, b) >= 2


def _fill(target, source):
    """Take values the target is missing. Never overwrite one it already has.

    The first page of a bill carries the seller and the document number; the last carries the
    totals. Filling gaps assembles the whole bill. Overwriting would let a continuation page's
    subtotal replace the real total, which is exactly the silent corruption a false merge causes.
    """
    for field, wrapped in source.items():
        if field in ("candidateIndex", "chunkPageIndex"):
            continue
        if not isinstance(wrapped, dict) or "value" not in wrapped:
            continue
        if wrapped.get("value") is None:
            continue
        current = target.get(field)
        if isinstance(current, dict) and current.get("value") is None:
            target[field] = dict(wrapped)
    return target


def merge_pages(per_page, transcripts=None, page_field="chunkPageIndex"):
    """[(page_index, [candidate, ...]), ...] -> one flat list, spanning bills joined.

    `transcripts` maps page_index -> that page's text, used only for the หน้า 1/2 markers. Omit
    it and merging falls back to field agreement alone, which is the honest behaviour when the
    text is not available rather than a reason to fail.

    Only the **last** candidate of a page is considered against the **first** of the next: a bill
    continues off the bottom of a page onto the top of the following one. A receipt in the middle
    of a page is complete by definition.

    `candidateIndex` is assigned last, sequentially in page order, so it never repeats and never
    depends on how many merges happened.
    """
    transcripts = transcripts or {}
    merged = []
    previous_page = None

    for page_index, candidates in sorted(per_page, key=lambda x: x[0]):
        for position, candidate in enumerate(candidates):
            joined = False
            if (merged and position == 0 and previous_page is not None
                    and page_index == previous_page + 1):
                tail = merged[-1]
                if tail.get("_lastOnPage") and same_bill(
                        tail, candidate,
                        page_marker(transcripts.get(previous_page)),
                        page_marker(transcripts.get(page_index))):
                    _fill(tail, candidate)
                    R.widen(tail, page_index, page_field)
                    joined = True
            if not joined:
                candidate = dict(candidate)
                candidate[page_field] = page_index
                merged.append(candidate)
            merged[-1]["_lastOnPage"] = (position == len(candidates) - 1)
        previous_page = page_index

    for index, candidate in enumerate(merged):
        candidate.pop("_lastOnPage", None)
        candidate["candidateIndex"] = index
    return merged


def spans(candidate):
    """Which pages a merged candidate came from. [] if it never spanned.

    For "which pages does this candidate own", which is never empty, use `regions.page_span`.
    """
    return candidate.get(R.SPAN_KEY, [])


def strip_internal(candidates):
    """Drop the bookkeeping keys before the response goes out.

    Their validator is strict -- `additionalProperties: false` -- so a stray `_pages` would fail
    the whole chunk, not just the field.
    """
    for candidate in candidates:
        candidate.pop(R.SPAN_KEY, None)
        candidate.pop("_lastOnPage", None)
    return candidates
