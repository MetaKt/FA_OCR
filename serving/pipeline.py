"""Request bytes -> validated contract response. No filesystem, no globals, no notebook.

This is the same two-stage pipeline the accuracy number was measured on, imported rather than
reimplemented: `src/ocr_pipeline.py` for the image half, `src/stage2_extract.py` for the fields.
If those change, this changes with them, which is the point -- a serving copy that drifts from
the evaluated code makes the evaluation a work of fiction.
"""
import contextlib
import logging
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src")]

import arith
import certlink
import degeneracy
import doctypes
import merge as M
import personlink
import slips
import regions as R
import stage2_extract as s2
from ocr_pipeline import build_prompt, collapse_empty_rows, load_pages, ocr_page

import config

# Same logger app.py configures, so a page-level event lands in the same stream as the request
# line it belongs to. This is the whole point of the degeneracy check: a page stage 1 could not
# read used to be indistinguishable in the log from a page with no bill on it, and the only way
# anyone found out was FA noticing 120 baht missing from a toll set.
log = logging.getLogger("adv-clear")


class Terminal(Exception):
    """The request cannot succeed and retrying it will not help. Maps to 4xx."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Retryable(Exception):
    """Something transient. Maps to 5xx with retryable: true."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Deadline:
    """Wall clock from request receipt, not from inference start.

    Rasterising a 40 MB PDF is not free and it is on our clock -- their lease starts when they
    call us, so ours has to as well.
    """

    def __init__(self, seconds):
        self.started = time.perf_counter()
        self.limit = seconds

    @property
    def elapsed(self):
        return time.perf_counter() - self.started

    @property
    def remaining(self):
        return self.limit - self.elapsed

    def check(self, doing):
        if self.remaining <= 0:
            raise Retryable("DEADLINE_EXCEEDED",
                            f"exceeded {self.limit}s while {doing}")


# One page costs roughly 50s at 1500px on this hardware. Starting a model call with less than
# this left cannot succeed, and starting it anyway is what produced the misleading error below.
MIN_STEP_S = 15.0


def _step_timeout(deadline, doing):
    """How long one model call may take, or a clean deadline breach if there is no time to try.

    Without this floor the call was made with `max(5.0, remaining)`, so once the deadline was
    spent the model got a 5-second timeout, httpx raised ReadTimeout, and app.py reported it as
    `503 MODEL_UNAVAILABLE` -- telling the caller their model backend is broken when the truth is
    that they sent more pages than fit in the deadline. Nobody can act on that error. This
    reports `504 DEADLINE_EXCEEDED` instead, which names the page reached so the caller knows how
    much smaller the chunk needs to be.
    """
    if deadline.remaining < MIN_STEP_S:
        raise Retryable("DEADLINE_EXCEEDED", f"exceeded {deadline.limit}s while {doing}")
    return deadline.remaining


@contextlib.contextmanager
def _deadline_aware(deadline, doing):
    """A timeout that lands after the clock ran out is a deadline breach, not a backend fault.

    The floor above prevents most of these, but a call started with time left can still overrun
    it. Reclassifying here keeps the caller's retry logic honest: a deadline breach means send
    fewer pages, while a backend fault means the model server is down, and the two need opposite
    responses.
    """
    try:
        yield
    except httpx.TimeoutException as exc:
        if deadline.remaining <= 0:
            raise Retryable("DEADLINE_EXCEEDED",
                            f"exceeded {deadline.limit}s while {doing}") from exc
        raise


def rasterise(body, target_dim, deadline):
    """Bytes -> [(page_number, image)]. Never touches disk (R18)."""
    try:
        pages = list(load_pages(body, target_dim))
    except Exception as exc:
        # A file we cannot open is terminal. Retrying a corrupt or encrypted PDF three times at
        # 60/300/900s burns 21 minutes of their queue on something that will never parse.
        raise Terminal("UNREADABLE_DOCUMENT",
                       f"could not read the document: {type(exc).__name__}") from exc
    if not pages:
        raise Terminal("EMPTY_DOCUMENT", "the document has no pages")
    deadline.check("rasterising")
    return pages


def _read(image, deadline, doing, seed):
    """One stage-1 call, under the deadline. Pulled out because a looped page gets read twice."""
    deadline.check(doing)
    with _deadline_aware(deadline, doing):
        return ocr_page(image, build_prompt(figure_language="English"),
                        model=config.STAGE1_MODEL, base_url=config.MODEL_BASE_URL,
                        seed=seed, timeout=_step_timeout(deadline, doing))


def extract_page(image, page_index, deadline, seed=42, page_count=None, rerender=None,
                 category=None):
    """One page -> that page's bill candidates. Raises Retryable on a deadline breach.

    `page_count` only shapes the error message, but it is the part the caller acts on: "exceeded
    240s while reading page 5 of 20" says send fewer pages, where a bare timeout says nothing.

    `rerender(page_index, target_dim) -> image` is how a looped page gets a second chance. Pass
    None to skip the retry; the page is then reported bill-free exactly as it was before.

    `category` is the account code FA picked before uploading, straight from the request header.
    It selects extra stage-2 rules; None means the shared prompt, which is what every request
    got before 2026-09-01.
    """
    where = f"page {page_index + 1}" + (f" of {page_count}" if page_count else "")
    doing = f"reading {where}"
    text = _read(image, deadline, doing, seed)
    reason = degeneracy.verdict(text)

    # Stage 1 failing produces a confident-looking answer rather than an error, so a page it came
    # apart on is silently worth zero baht. Two kinds are separated here: one we can do something
    # about, and one we cannot. A loop means the page has not been read yet and is worth another
    # go at a different size (config.RETRY_TARGET_DIMS explains why size and not seed). A blank
    # or an "@" burst is not, so nothing is spent on it.
    if degeneracy.worth_rereading(reason) and rerender is not None:
        log.warning("stage1 degenerate page=%d %s -- re-reading at %s",
                    page_index, reason, ",".join(str(d) for d in config.RETRY_TARGET_DIMS))
        for dim in config.RETRY_TARGET_DIMS:
            if deadline.remaining < MIN_STEP_S:
                log.warning("stage1 page=%d retry abandoned, %.0fs left", page_index,
                            deadline.remaining)
                break
            retry = _read(rerender(page_index, dim), deadline, f"re-{doing} at {dim}px", seed)
            if degeneracy.verdict(retry) is None:
                log.info("stage1 page=%d recovered at %dpx, %d chars", page_index, dim, len(retry))
                text, reason = retry, None
                break

    if reason:
        # Feeding a broken transcript to stage 2 is worse than dropping the page: it will happily
        # turn letterhead into a phantom bill with no amount on it.
        #
        # A blank page is ordinary -- separator sheets and blank reverse sides are everywhere in
        # a 200-page set -- so it is logged at info. Anything else means a page nobody read, and
        # the reviewer looking at the webapp cannot tell that apart from a page with no bill on
        # it, so it is a warning.
        level = log.info if reason == "empty" else log.warning
        level("stage1 page=%d unusable (%s) -- reported bill-free", page_index, reason)
        return [], text or ""

    text = collapse_empty_rows(text)
    doing = f"extracting {where}"
    deadline.check(doing)
    with _deadline_aware(deadline, doing):
        reduced = s2.extract(text, config.STAGE2_MODEL, {"think": False},
                             timeout=_step_timeout(deadline, doing), category=category)
    return s2.assemble(reduced, page_index=page_index, transcript=text)["billCandidates"], text


def run(body, deadline, target_dim=None, seed=42, category=None):
    """The whole job. Returns (response, page_count).

    Pages are read independently, then `merge.merge_pages` joins a bill that runs across a page
    break -- R4 says the model owns bill boundaries, and two candidates for one bill means two
    wrong ledger rows. The merge is biased against joining: see src/merge.py.

    `candidateIndex` is assigned inside merge_pages, after everything is collected, so it is
    sequential across the whole chunk rather than restarting at 0 on every page.
    """
    pages = rasterise(body, target_dim or config.TARGET_DIM, deadline)

    def rerender(page_index, dim):
        """The one page, again, at a different size. Only ever called on a looped page.

        This rasterises the pages before it as well, because load_pages is a generator over the
        document. That is a tenth of a second each and it reuses the code every other page went
        through, which is worth more here than the saving -- the expensive part of a re-read is
        the 50 GPU-seconds after it, not the drawing.
        """
        for number, image in load_pages(body, dim):
            if number - 1 == page_index:
                return image
        raise Retryable("PAGE_MISSING", f"page {page_index + 1} vanished on re-render")

    per_page, transcripts = [], {}
    for number, image in pages:
        page_index = number - 1
        candidates, text = extract_page(image, page_index, deadline, seed, len(pages),
                                        rerender, category)
        per_page.append((page_index, candidates))
        transcripts[page_index] = text

    merged = M.merge_pages(per_page, transcripts)
    # A ใบเสร็จ and its หนังสือรับรองการหักภาษี ณ ที่จ่าย are one payment on two documents, and FA
    # wants one ledger row. merge_pages cannot join them -- it keys on adjacency and refuses any
    # pair whose document numbers disagree, and a certificate carries its own WHT number, sits
    # pages away from its receipt, and never repeats the invoice number. See src/certlink.py.
    merged, _ = certlink.apply_certificates(merged, transcripts)
    # A copy of the payee's national ID card, stapled behind the ใบรับเงิน it belongs to. It is
    # evidence for that payment, not a payment of its own, and it holds the one thing the wage
    # form states badly: the payee's ID, printed and check-digited rather than handwritten.
    # Before attach_orphans, so the card's page travels with its bill. See src/personlink.py.
    merged, cards = personlink.apply_cards(merged, transcripts)
    for row in cards:
        if row["nameConflict"]:
            log.warning("personlink page=%d name on the card does not match the bill "
                        "-- nothing filled, both fields distrusted", row["page"])
        elif row["filledTaxId"]:
            log.info("personlink page=%d sellerTaxId taken from the ID card", row["page"])
        elif not row["attached"]:
            log.warning("personlink page=%d ID card with no bill before it in this chunk",
                        row["page"])
    # Only now is the page span final -- merge_pages joins continuations and certlink folds a
    # certificate's page into its bill. `regions` is the contract's only way to say a bill covers
    # more than one scan; without it the reviewer sees the first page and the rest are hidden.
    # The bank's record that the money actually moved. Unlike an ID card a slip states an
    # amount, so it becomes a bill in its own right if left alone -- the same money as the
    # receipt beside it, counted twice. It is folded away only when a bill states the amount it
    # paid; otherwise it stays, because a payment with no receipt has nothing else to prove it.
    merged, paid = slips.apply_slips(merged, transcripts)
    for row in paid:
        if row["attached"]:
            log.info("transfer slip page=%d folded into the payment it evidences", row["page"])
        else:
            log.warning("transfer slip page=%d left standing: %s", row["page"], row["reason"])
    R.attach_orphans(merged, len(pages))
    R.attach_regions(merged)
    # What each of those pages is, in the contract's evidence vocabulary. Last of the evidence
    # steps: a role is a statement about a page, so which bill owns the page has to be settled
    # first. Adds to whatever personlink and slips already tagged; a Thai receipt headed
    # ใบเสร็จรับเงิน/ใบกำกับภาษี gets both roles, agreed with the webapp team 2026-09-02.
    doctypes.apply_document_types(merged, transcripts)
    # Last, on the final numbers: a bill's own arithmetic is redundant, so an OCR digit error
    # usually breaks an equation instead of hiding. This only lowers confidence on the fields
    # caught in a failing equation -- it never rewrites a number, because a failing equation says
    # the set is wrong, not which member of it is. See src/arith.py.
    for row in arith.apply(merged):
        for code, message in row["findings"]:
            log.warning("arith page=%s candidate=%s %s: %s",
                        row["chunkPageIndex"], row["candidateIndex"], code, message)
    merged = M.strip_internal(merged)
    return {"billCandidates": merged}, len(pages)


def run_validated(body, deadline, target_dim=None, category=None):
    """`run`, but never returns a body we have not validated against their schema.

    Constrained decoding makes invalid output rare, not impossible -- the grammar cannot enforce
    numeric ranges or minItems (phase 02 section 3). One internal retry with a different seed,
    because the failure is usually a sampling accident rather than a systematic one. Returning
    an invalid body costs them the entire chunk, so a slow 500 beats a fast lie.
    """
    for attempt, seed in enumerate((42, 1337)):
        response, n_pages = run(body, deadline, target_dim, seed=seed, category=category)
        ok, err = s2.validate(response)
        if ok:
            return response, n_pages, attempt
        if config.DEBUG_DUMP_INVALID:
            _dump(response, err)
        if deadline.remaining <= 0:
            break
    raise Retryable("SCHEMA_VALIDATION_FAILED",
                    "model output did not satisfy the contract schema after a retry")


def _dump(response, err):
    """Debug aid for invalid output. This writes document content -- see config.DEBUG_DUMP_INVALID."""
    import json
    import uuid
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (config.DEBUG_DIR / f"{uuid.uuid4().hex}.json").write_text(
        json.dumps({"error": str(err), "response": response}, ensure_ascii=False, indent=2),
        encoding="utf-8")
