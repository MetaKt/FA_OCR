"""Is a stage-1 transcript usable, or did the model come apart on this page?

Typhoon fails on some pages without ever raising: it returns a confident, well-formed answer
that contains none of the page. Stage 2 then reads that answer, finds no bill, and the page is
silently worth zero baht. Page 198 of P06690 cost 120 baht exactly this way.

Two shapes have been seen:

  the @ burst   at target_dim 1800 the decoder collapses into "@@@@@@@" (ocr_pipeline
                .DEFAULT_TARGET_DIM records the measurement). Caught since day one by counting
                "@", and that check is kept here unchanged.

  the loop      the model latches onto something repetitive on the page and re-emits it until
                the context runs out. Page 198 is a punch-card toll ticket with a calendar
                printed round its border -- JAN..DEC down the sides, 1..31 across -- and the
                output was 9,893 characters of the EXAT letterhead 34 times over, no amounts, no
                ticket numbers, and not a single "@". Perfectly readable Thai. The old check saw
                nothing wrong with it.

What separates a loop from a dense page is not how long it is or how often one phrase appears --
a real receipt repeats its own letterhead too. It is how much of the transcript is *distinct*.
Measured over the 94 transcripts in eval/transcripts*, the worst genuine page keeps 71% of its
segments unique and compresses to 24%; page 198 keeps 4% and compresses to 3%. The thresholds
below sit in the middle of that gap, and both must agree, because the two metrics are wrong in
different directions: unique-ratio is blind to a loop that varies slightly, zlib is fooled by any
long document. Requiring both costs a false negative now and then and buys near-zero false
positives, which is the trade we want -- see `serving/pipeline.py` for what a positive triggers.
"""
import re
import zlib
from collections import Counter

_TAG = re.compile(r"<[^>]+>")
_SPLIT = re.compile(r"<[^>]+>|\n")
_WS = re.compile(r"\s+")

# Below this a transcript is too short to have said anything, whatever it looks like.
MIN_CHARS = 80
# The @ burst. Unchanged from the check this module replaces.
MAX_AT_SIGNS = 10

# Fewer segments than this and the ratio is noise: a three-line toll ticket scores 1.00 by having
# nothing to repeat. Every real page measured has 3-24 segments; page 198 has 133.
MIN_SEGMENTS = 12
# Distinct segments / total segments. 0.04 broken, 0.71 worst genuine.
MAX_REPEAT_RATIO = 0.35
# zlib size / raw size. 0.03 broken, 0.24 worst genuine.
MAX_COMPRESSION = 0.12

# Segments shorter than this are punctuation and stray digits, not content.
#
# Lowered from 12 to 8 on 2026-09-03. Page 118 of P06690 is a handwritten บิลเงินสด worth
# 3,514 baht on which the model emitted the table's *header* row about fifty times instead of the
# one filled row: `<td>รายการ<br/>DESCRIPTION<br/>貨名</td><td>หน่วยละ<br/>UNIT PRICE...`. Every
# repeated piece is a column label of 6-11 characters, so a floor of 12 discarded all of them,
# left 9 long segments that were genuinely distinct, and scored the page 1.00. At 8 the same page
# yields 218 segments at 0.06 and the existing conjunction condemns it.
#
# `collapse_empty_rows` cannot help here -- the repeated cells are not empty, they are full of
# column headings -- and the page is not saved by `repeat_penalty` either, which is what F13 added
# for exactly this failure shape.
#
# Measured before changing: at 8 the catch set grows by precisely this one page across 138 corpus
# pages, with zero false positives there or on the 47 golden transcripts. The re-read earns its
# keep -- at 1500 px the page produces `514`, at 1300 and 2000 px it produces `3,514`, so the
# guard recovers 3,000 baht that was being lost silently at confidence 0.95 with no VAT or
# withholding line for `arith` to check it against.
MIN_SEGMENT_CHARS = 8


def segments(text):
    """The text between the tags AND between the lines, with the scraps dropped.

    Splitting on tags is what catches page 198: the v1.5 prompt asks for HTML and a looped page
    arrives as one enormous line with no newline in it at all, so newlines alone would see one
    segment and score it 1.00.

    Splitting on newlines *as well* was added 2026-09-03, after transcribing all 138 corpus
    pages. Page 13 is the counter-example to tags-only: 10,306 characters in which one footer
    line -- ใบเสร็จรับเงินและใบกำกับภาษีฉบับนี้จัดทำขึ้นโดย... -- is emitted 54 times, separated
    by newlines rather than tags. Tag-splitting saw 15 segments, every one distinct, scored it
    1.00 and cleared it. Splitting on both gives 192 segments at 0.19, and the existing
    conjunction condemns it without any threshold moving.

    The two delimiters catch opposite failures, so the split has to be on both. Neither ordering
    nor precedence matters -- an empty piece between two adjacent delimiters is dropped by the
    length filter either way.
    """
    parts = (_WS.sub(" ", part).strip() for part in _SPLIT.split(text or ""))
    return [part for part in parts if len(part) >= MIN_SEGMENT_CHARS]


def score(text):
    """(repeat_ratio, compression, segment_count). Cheap, pure, and safe on empty input."""
    segs = segments(text)
    ratio = len(Counter(segs)) / len(segs) if segs else 1.0
    raw = (text or "").encode("utf-8")
    compression = len(zlib.compress(raw, 6)) / len(raw) if raw else 1.0
    return ratio, compression, len(segs)


def verdict(text):
    """None if the transcript is usable, otherwise a short reason for the log.

    A reason string rather than a bool because these three failures want different responses and
    the caller cannot tell them apart afterwards: a blank page is genuinely blank and retrying it
    wastes 50 seconds, while a loop is a page we have not read yet and is worth another go.
    """
    if not text or len(text) < MIN_CHARS:
        return "empty"
    if text.count("@") > MAX_AT_SIGNS:
        return "at_burst"
    ratio, compression, count = score(text)
    if count >= MIN_SEGMENTS and ratio <= MAX_REPEAT_RATIO and compression <= MAX_COMPRESSION:
        return f"loop(unique={ratio:.2f} compressed={compression:.2f} segments={count})"
    return None


def worth_rereading(reason):
    """Is this failure one a second attempt could fix?

    Only the loop. An empty answer usually means the page really is blank -- a separator sheet, a
    back side -- and the @ burst is a resolution fault we already avoid by not using 1800.
    """
    return bool(reason) and reason.startswith("loop")
