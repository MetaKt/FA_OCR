"""Sum a stapled set of expressway tickets into one ledger row.

FA staples a trip's toll tickets onto one or more sheets and wants one line in the ledger, the
same way a bill spanning several pages became one row. Agreed with FA for
`5223100 ค่าเดินทาง ในประเทศ`.

Two things make this safe to do deterministically:

  every ticket prints its own running number   so the photocopies can be dropped exactly
  every ticket prints its own amount           so the total is added, never inferred

The photocopies are not incidental. The ticket itself says
`กรุณาทำสำเนาเพื่อนำไปใช้ในธุรกรรมต่อไป` -- please make a copy for your next transaction -- so a
page routinely carries each ticket twice. Page 200 of P06690 holds three ticket images and two
real tickets: `202607131735220214` appears twice, at 25.00 both times. Counting the images gives
70 baht where the page is worth 45. Deduplicating by running number is therefore required, not an
optimisation, and it is the one part of this module that would be worth having even without the
summing.

**Three fields are deliberately left empty on a merged row**, because the tickets disagree and no
single value is honest:

  documentDate            page 200 alone carries tickets dated 13/07 and 20/07. The earliest is
                          used, so the row sorts into the period the trip began.
  amountBeforeVat, vat    page 201 carries one EXAT ticket at `Baht(Vat Included) 80.00` and one
                          กรมทางหลวง ticket at `Baht(Non Vat) 35.00`. There is no subtotal and no
                          tax figure that describes both, so both stay null.
  originalDocumentNumber  each ticket has its own. Picking one would look like a real reference
                          to a document that does not cover the row. Null, with every running
                          number kept in `lineItems[].description`.

These were sent to FA on 2026-08-31 and are still unanswered; each is a single constant here.

**Gated on the category.** Nothing is merged unless the request carried
`x-category-id: 5223100`. A clearing set filed under another code keeps one row per ticket, which
is what it had before this module existed.
"""
import re

import regions as R

# Account code this applies to. FA picks it before uploading and the webapp sends it since
# 2026-09-01; without the header nothing here runs.
TRAVEL_CATEGORY = "5223100"

# Who issues expressway tickets. EXAT's own tax id is checksum-valid and is the strongest of
# these, but stage 1 drops or mangles it on some scans, so the printed names carry the detection.
TOLL_MARKERS = ("ใบรับค่าผ่านทางพิเศษ", "ใบรับค่าธรรมเนียมผ่านทางหลวงพิเศษ",
                "การทางพิเศษแห่งประเทศไทย", "กรมทางหลวง", "TOLLFARE", "EXAT",
                "ทางด่วนและรถไฟฟ้ากรุงเทพ")
EXAT_TAX_ID = "0994000165421"

# One marker is enough here, unlike certlink or slips. These phrases are the issuer's own name or
# the form's title; nothing else in a clearing set says ใบรับค่าผ่านทางพิเศษ.
MIN_MARKERS = 1

# Two layouts, both real. EXAT and กรมทางหลวง print flat text with `Receipt Running No :`; BEM
# prints an HTML table whose first row is `No.`. The number is the ticket's identity in both.
_RUNNING_FLAT = re.compile(r"Receipt\s+Running\s+No\s*[:：]?\s*(\d{6,})", re.I)
_RUNNING_TABLE = re.compile(r"<td>\s*No\.?\s*</td>\s*<td>\s*(\d{6,})\s*</td>", re.I)

# `Baht(Vat Included) 80.00` and `<td>Baht(Vat Included)</td><td>50.00</td>`. The wording of the
# bracket also says whether the ticket carries VAT, which is why a merged row cannot state one.
_AMOUNT_FLAT = re.compile(r"Baht\s*\(\s*(Vat Included|Non Vat)\s*\)\s*([\d,]+\.\d{2})", re.I)
# The BEM table writes the unit inside the cell -- `<td>25.00 บาท</td>` -- while EXAT's flat
# layout does not. Requiring a bare number here cost the two tickets on P06690 page 198: they were
# read, found to have no parseable amount, and dropped by the rule below. Correct behaviour from a
# pattern that was too strict, and worth 75 baht.
_AMOUNT_TABLE = re.compile(
    r"<td>\s*Baht\s*\(\s*(Vat Included|Non Vat)\s*\)\s*</td>\s*"
    r"<td>\s*([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)?\s*</td>",
    re.I)

_DATE_FLAT = re.compile(r"Date\s*Time\s*(\d{2})/(\d{2})/(\d{4})", re.I)
_DATE_TABLE = re.compile(r"<td>\s*Date\s*Time\s*</td>\s*<td>\s*(\d{2})/(\d{2})/(\d{4})", re.I)


def is_toll_page(transcript):
    """True when the page carries expressway tickets."""
    if not transcript:
        return False
    hits = sum(m in transcript for m in TOLL_MARKERS)
    hits += EXAT_TAX_ID.replace(" ", "") in re.sub(r"\s+", "", transcript)
    return hits >= MIN_MARKERS


def _iso(day, month, year):
    """DD/MM/YYYY as printed -> YYYY-MM-DD. Toll tickets print the Gregorian year already."""
    return f"{year}-{month}-{day}"


def parse_tickets(transcript):
    """Every distinct ticket on the page, in the order printed, photocopies removed.

    Identity is the running number. Two images with the same number are the same ticket however
    many times the page repeats them; two different numbers are two journeys even when the amount
    and the second match, which happens on a busy day.

    A ticket whose number was read but whose amount was not is dropped rather than guessed at:
    the page total exists to be added up, and a ticket contributing an unknown amount would make
    the sum quietly wrong.
    """
    text = transcript or ""
    refs = [(m.start(), m.group(1)) for m in _RUNNING_FLAT.finditer(text)]
    refs += [(m.start(), m.group(1)) for m in _RUNNING_TABLE.finditer(text)]
    amounts = [(m.start(), m.group(1).lower(), float(m.group(2).replace(",", "")))
               for m in _AMOUNT_FLAT.finditer(text)]
    amounts += [(m.start(), m.group(1).lower(), float(m.group(2).replace(",", "")))
                for m in _AMOUNT_TABLE.finditer(text)]
    dates = [(m.start(), _iso(*m.groups())) for m in _DATE_FLAT.finditer(text)]
    dates += [(m.start(), _iso(*m.groups())) for m in _DATE_TABLE.finditer(text)]
    refs.sort()
    amounts.sort()
    dates.sort()

    def after(items, position, limit):
        """The first item printed after this ticket's number and before the next one."""
        return next((v for at, *v in items if position < at < limit), None)

    tickets, seen = [], set()
    for index, (position, ref) in enumerate(refs):
        limit = refs[index + 1][0] if index + 1 < len(refs) else len(text)
        money = after(amounts, position, limit)
        if money is None or ref in seen:
            continue
        seen.add(ref)
        stamp = after(dates, position, limit)
        tickets.append({"ref": ref, "amount": money[1], "vatIncluded": money[0] == "vat included",
                        "date": stamp[0] if stamp else None})
    return tickets


def ticket_images(transcript):
    """How many ticket images the page carries, photocopies included.

    `parse_tickets` removes the duplicates, so counting its result can never show how many were
    removed. This counts what was printed, which is what makes the drop visible in the log --
    and a page where images far outnumber tickets is worth a reviewer's eye.
    """
    text = transcript or ""
    return len(_RUNNING_FLAT.findall(text)) + len(_RUNNING_TABLE.findall(text))


def _runs(pages):
    """Consecutive page numbers grouped together. A gap starts a new trip."""
    groups = []
    for page in sorted(pages):
        if groups and page == groups[-1][-1] + 1:
            groups[-1].append(page)
        else:
            groups.append([page])
    return groups


def _cell(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def _line_item(ticket):
    """One ticket as a line item. Its running number is the only place the detail survives."""
    return {"description": _cell(f"TOLLFARE {ticket['ref']}"),
            "quantity": _cell(None, 1.0), "unit": _cell(None, 1.0),
            "unitPrice": _cell(None, 1.0), "discount": _cell(None, 1.0),
            "amount": _cell(ticket["amount"])}


def applies(category):
    """Does this request's category ask for toll rows to be summed?"""
    import stage2_extract as s2
    return category is not None and s2.category_key(category) == TRAVEL_CATEGORY


def apply_tolls(candidates, transcripts, category, page_field="chunkPageIndex"):
    """Merge each run of consecutive toll pages into one candidate. Returns (candidates, report).

    Runs after `slips` and **before** `regions.attach_orphans`, the same slot the other folding
    steps use. Setting `_pages` and letting `attach_regions` rebuild `regions` afterwards is what
    keeps the page invariants true: a toll page adopted by a neighbouring bill cannot end up
    claimed by that bill and by the merged row at once.
    """
    if not applies(category):
        return candidates, []

    toll_pages = {p for p, t in transcripts.items() if is_toll_page(t)}
    if not toll_pages:
        return candidates, []

    report, merged_out, consumed = [], [], set()
    for run in _runs(toll_pages):
        tickets, seen = [], set()
        for page in run:
            for ticket in parse_tickets(transcripts[page]):
                if ticket["ref"] not in seen:
                    seen.add(ticket["ref"])
                    tickets.append(ticket)
        if not tickets:
            # Every page in the run was unreadable. Leave whatever the model made of them; the
            # degeneracy log already says the pages could not be read.
            continue

        owners = [c for c in candidates if c.get(page_field) in run]
        if not owners:
            continue
        base = dict(owners[0])
        total = round(sum(t["amount"] for t in tickets), 2)
        dates = sorted(t["date"] for t in tickets if t["date"])

        base["_pages"] = sorted(set(run) | {p for c in owners for p in R.page_span(c, page_field)})
        base[page_field] = run[0]
        base["originalTotal"] = _cell(total)
        base["clearingAmount"] = _cell(total)
        # No honest single value: a run can mix VAT-inclusive and non-VAT tickets on one page.
        base["amountBeforeVat"] = _cell(None, 0.0)
        base["vat"] = _cell(None, 0.0)
        base["vatRate"] = _cell(None, 0.0)
        # Each ticket has its own number; one field cannot carry them, so none of them goes in it.
        base["originalDocumentNumber"] = _cell(None, 0.0)
        base["documentBookNumber"] = _cell(None, 0.0)
        base["documentDate"] = _cell(dates[0]) if dates else _cell(None, 0.0)
        base["lineItems"] = [_line_item(t) for t in tickets]
        base["evidence"] = [e for c in owners for e in (c.get("evidence") or [])]
        # `regions` is deliberately NOT set here. This runs before `regions.attach_regions`, which
        # rebuilds it from `_pages` for every candidate -- so the merged row cannot end up
        # claiming a page that a neighbouring bill also claims, which is invariant 4.
        base.pop("regions", None)

        consumed.update(id(c) for c in owners)
        merged_out.append(base)
        report.append({"pages": run, "tickets": len(tickets), "total": total,
                       "rowsReplaced": len(owners),
                       "copiesDropped": sum(ticket_images(transcripts[p]) for p in run)
                       - len(tickets)})

    if not merged_out:
        return candidates, []

    kept, inserted = [], set()
    for candidate in candidates:
        if id(candidate) in consumed:
            for row in merged_out:
                if candidate.get(page_field) in row["_pages"] and id(row) not in inserted:
                    inserted.add(id(row))
                    kept.append(row)
            continue
        kept.append(candidate)
    for row in merged_out:
        if id(row) not in inserted:
            kept.append(row)
    return kept, report
