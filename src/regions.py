"""Which scans belong to which bill.

`regions` left the contract on 2026-08-11 and came back on 2026-08-28. Nothing changed about
what we can measure -- Typhoon returns no coordinates, so every box here is the whole page --
but the purpose changed. The box was originally for click-to-highlight, which a whole-page box
cannot do, and that is why dropping it looked free. It is in fact the only place the contract can
carry *this bill covers pages 3, 4 and 5*: `chunkPageIndex` names the first page and nothing
else. So a three-page bill reached the reviewer as one scan with two hidden, and a withholding
certificate stapled behind a receipt vanished every time. That is the bug this module closes.

Every box is `0,0,1000,1000` on purpose. It claims exactly the precision we have and no more.
The webapp team call this their level 2 and it needs no localisation ability at all -- only an
honest answer to "which pages", which the merge already knows.

Nothing here calls a model. Given the same candidates it returns the same boxes every time.
"""

PAGE_FIELD = "chunkPageIndex"

# merge.py's bookkeeping key: every page a joined candidate absorbed. Written by merge.py and
# certlink.py, read here, and stripped by merge.strip_internal before the response goes out --
# their validator is `additionalProperties: false`, so one stray key fails the whole chunk.
# It lives here rather than in merge.py because certlink.py writes it too and merge.py imports
# certlink.py, so merge.py cannot be the shared home without a cycle.
SPAN_KEY = "_pages"

FULL_PAGE_BOX = {"xMin": 0, "yMin": 0, "xMax": 1000, "yMax": 1000}


def whole_page(page_index, page_field=PAGE_FIELD):
    """The one box we can honestly draw."""
    return {page_field: page_index, **FULL_PAGE_BOX}


def page_span(candidate, page_field=PAGE_FIELD):
    """Every page this candidate owns, ascending and unique.

    Its own page if it never merged. `merge.spans` answers "did this join?" and returns [] when
    it did not; this answers "what does it own", which is never empty for a real candidate.
    """
    pages = candidate.get(SPAN_KEY)
    if pages:
        return sorted(set(pages))
    start = candidate.get(page_field)
    return [] if start is None else [start]


def widen(candidate, page, page_field=PAGE_FIELD):
    """Record that `candidate` also owns `page`. The only writer of SPAN_KEY."""
    candidate[SPAN_KEY] = sorted(set(page_span(candidate, page_field)) | {page})
    return candidate


def add_evidence(candidate, role, page, page_field=PAGE_FIELD):
    """Record that one page of this bill plays a named role in the contract's `evidence` list.

    The webapp's finance rules are written against these roles -- "a bill with no receipt must
    have a transfer slip" cannot be evaluated at all while every `evidence` list is empty, which
    is why R-SLIP-001 currently fires on every bill in the system.

    Only roles we can establish deterministically are ever added. A page we cannot classify gets
    no entry rather than `other`, because "we looked and it is something else" and "we did not
    know" are different claims and only the second one is true.

    Idempotent: the same page in the same role is recorded once, however many times the pipeline
    passes over it.
    """
    entries = candidate.setdefault("evidence", [])
    for entry in entries:
        if entry.get("role") == role and any(r.get(page_field) == page
                                             for r in entry.get("regions", [])):
            return candidate
    entries.append({"role": role, "regions": [whole_page(page, page_field)]})
    return candidate


def attach_orphans(candidates, page_count, page_field=PAGE_FIELD):
    """Give every page in the chunk an owner, so no scan is dropped from the evidence bundle.

    A page produces no candidate when stage 2 found no money on it: a blank reverse, a signature
    sheet, a delivery note stapled behind the receipt. Left unowned it never reaches the
    reviewer, which is the same failure regions came back to fix.

    Nearest **preceding** bill, because an attachment follows the document it belongs to. Pages
    before the first bill go to the first bill -- there is nothing earlier to hold them.

    This is the only guess in this module, and it is the safe direction: a wrongly attached page
    puts one extra scan on a reviewer's screen, where the alternative hides one. FA recheck every
    row by hand, so a visible extra costs seconds and an invisible loss costs a correction.
    """
    if not candidates or not page_count:
        return candidates

    owner = {}
    for index, candidate in enumerate(candidates):
        for page in page_span(candidate, page_field):
            owner.setdefault(page, index)

    held_by = None
    for page in range(page_count):
        if page in owner:
            held_by = owner[page]
            continue
        widen(candidates[held_by if held_by is not None else 0], page, page_field)
    return candidates


def attach_regions(candidates, page_field=PAGE_FIELD):
    """One whole-page box per page owned, plus the `evidence` key. Call last.

    Authoritative: it rebuilds `regions` from the span rather than trusting whatever the pages
    carried in, so a merge or a folded certificate cannot leave a stale box behind.

    `evidence` ships empty on purpose. We can say which pages a bill covers; we cannot yet say
    which of their eight roles each scan plays. An empty array is the contract's way of saying
    exactly that, where a guessed `receipt` would be a claim we cannot support.

    A candidate with no page at all gets no `regions` key, which fails their schema loudly on the
    way out. That cannot happen -- merge_pages stamps the page on every candidate -- and if it
    ever does, a 500 is the right answer rather than a fabricated page 0.
    """
    for candidate in candidates:
        span = page_span(candidate, page_field)
        if span:
            candidate["regions"] = [whole_page(page, page_field) for page in span]
        candidate.setdefault("evidence", [])
    return candidates
