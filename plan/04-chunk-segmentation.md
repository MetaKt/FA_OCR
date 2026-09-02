# Phase 04 — Chunk Handling: Segmentation & Multi-Page Bills

**Goal:** correct bill boundaries and correct `chunkPageIndex` on realistic multi-page chunks.
**Inputs:** phase 03 model choice.
**Exit gate:** on a hand-built 10-page chunk containing known bill boundaries, the model returns
the right number of candidates with correct page indices.

> ### Status 2026-08-24 — BUILT and unit-tested. One honest gap: no confirmed real spanning bill.
>
> `../src/merge.py` joins a bill that runs across a page break; `../eval/test_merge.py` is the
> fixture set from §6, **26 checks, all passing**. Wired into `../serving/pipeline.py`, so the
> API now merges before responding.
>
> **The design changed from §2, because `regions` left the contract on 2026-08-11.** "The regions
> overlap on the shared page" has nothing to stand on any more. What remains is the page index and
> the field values, so two candidates on **adjacent** pages become one bill when either:
>
> 1. both pages state their own position — `หน้า 1/2` then `หน้า 2/2` — and the numbers run on, or
> 2. **two** of `{originalDocumentNumber, sellerName, originalTotal, documentDate}` agree, both
>    stating a value. Two nulls are not agreement; a continuation page states almost nothing, so
>    counting nulls would make every pair of sparse pages look like one bill.
>
> Any conflict on those fields refuses the merge outright, **even against a page marker** — two
> different document numbers on consecutive pages are two bills that happen to be stapled
> together, which is the normal shape of a clearing set. Only the *last* candidate of a page can
> continue onto the next; a receipt in the middle of a page is complete by definition.
>
> Also changed: there is no sliding window. Pages are read independently and merged afterwards,
> which reaches the same place with one offset instead of two — §2 warned that the two independent
> offsets were "both capable of being wrong in a way that looks fine", and this removes one of them.
>
> **Verified on real data:** the five-bill PDF returns exactly 5 candidates through the API. Pages
> 1 and 3 of it have `sellerName: null`, which is the shape that would trip an over-eager rule,
> and it held.
>
> #### The gap, stated plainly
>
> **No spanning bill has been confirmed in the real data.** 54 transcripts were scanned — all 34
> golden cases plus pages 49, 58 and 88–90 of the clearing set — and **zero** carry a `หน้า 1/2`
> marker. The claim in `../handover/README.md` that "page 90 is marked หน้า 1/2" came from the old
> 216-page numbering and does not survive the correction to 123 pages; page 90 is a withholding
> tax certificate.
>
> So route 1 is tested only against synthetic fixtures. Either spanning bills are rarer here than
> assumed, or they are on pages nobody has OCR'd yet. **Resolving that needs a full 123-page scan**
> (~30 min of OCR) and it is the last thing standing between this phase and a closed gate.
>
> #### Still not built
>
> Deduplication of the *same* receipt scanned twice (§ "Duplicate scans" in the handover README) is
> a different problem from a spanning bill and is not addressed. It needs the whole upload in view,
> which is the webapp's position, not ours.

<details>
<summary>Original status, 2026-08-19 — the least-started phase, and the largest remaining risk</summary>

> ### Status 2026-08-19 — the least-started phase, and the largest remaining risk
>
> **What exists:** pages are processed one at a time. `chunkPageIndex` is passed in by the caller
> and is correct. Multi-bill-*within*-a-page works — stage 2 returns a list, and over-splitting is
> caught by the harness, which reports more than one candidate on a single-bill case as a total
> miss rather than quietly taking the first.
>
> **What does not exist: any cross-page handling at all.** No sliding window, no overlap, no merge,
> no deduplication. A bill spanning pages 2–3 will be emitted as two candidates today. R4 makes
> bill boundaries the model's job explicitly, and F3a confirmed the whole PDF arrives in one call,
> so this is required work, not optional polish.
>
> **Two inputs are already in hand** that this phase was told to wait for: Q3 is answered (one call
> carries the whole PDF), and the real shape of the problem is now known from a **123-page clearing
> set** — nine document types, many non-bill pages, and ~35 ใบรับเงิน rows. That set is a far better
> fixture than the hand-built 10-page chunk originally scoped.
>
> **Two of this phase's tasks were completed elsewhere:** confidence calibration was measured
> (overview F16 — not predictive, R13 at risk), and the rule-based validity checks became the five
> deterministic guards in `stage2_extract.py` (F15).

</details>

---

## 1. Why this is its own phase

R4 is the requirement most likely to be underestimated:

> Layout: multiple bills on one page, and one bill spanning multiple pages.
> **The model decides bill boundaries, not the pipeline.**

Phases 01–03 all operate on one clean bill at a time. That is a deliberate simplification to get
accuracy numbers moving. Real chunks are 40 pages of scanned receipts where three small taxi
receipts share a page and a hotel folio runs across two.

Everything in phases 01–03 can be at 98% and this phase can still make the system unusable,
because a bill split into two candidates produces two wrong ledger rows regardless of how well
each field was read.

---

## 2. The context problem

R15 implies chunks up to 40 pages. A 1800px page costs on the order of a thousand-plus vision
tokens. Forty of those does not fit in a 4B model's context on an 8 GB card — not close.

But R4 says the model owns boundary decisions, which requires seeing pages together. These two
facts are in tension and the resolution is an engineering decision on our side.

**RESOLVED 2026-08-10 (question 3): the whole PDF arrives in one call.** So there is no hope that
the webapp team splits the work for us — windowing is our job and this phase is on the critical
path. See `00-OVERVIEW.md` F3a.

The good news: because we see every page, R4 is actually *satisfiable*. A bill spanning pages 2–3
is detectable. That was not guaranteed before the answer came back.

Question 2 (typical and max page count) is still open and still worth having — it sizes the window
and the RAM budget — but it no longer blocks the design.

### Strategy: sliding window with overlap

1. Process pages in a **window of N** (N determined by what actually fits — start at 3)
2. Windows **overlap by 1 page**, so a bill spanning the boundary is fully visible in at least
   one window
3. Each window returns candidates with `chunkPageIndex` local to the window
4. **Offset correction**: add the window's start index to every `chunkPageIndex` before merging.
   This is our code's job and a very easy place to introduce an off-by-one that silently points
   every box at the wrong page.
5. **Deduplicate** candidates that appear in two overlapping windows

Note the layering: their system adds *its own* offset to map chunk pages back to document pages
(§4). Ours maps window pages to chunk pages. Two independent offsets, both invisible in the
output, both capable of being wrong in a way that looks fine. Test each in isolation.

### Deduplication rule

Two candidates from adjacent windows are the same bill if their regions overlap on the shared
page **and** at least two of {`originalDocumentNumber`, `sellerName`, `originalTotal`,
`documentDate`} match after normalization.

When in doubt, **do not merge** — R12 says ambiguity goes to the human as two candidates.
A false merge destroys a bill; a false split costs a reviewer ten seconds.

---

## 3. `candidateIndex` and ordering

`candidateIndex` is a bare integer ≥ 0 (not wrapped). The contract does not say what it means
beyond that.

Assign it **after** merging, sequentially from 0, in reading order: sort by
(first region's `chunkPageIndex`, then `yMin`, then `xMin`). Sequential and stable is what a
reviewer working through a queue expects. Write this down so it does not silently change between
versions — the colleague's UI may end up depending on it.

---

## 4. Confidence calibration (R13)

> Do not default confidence to 1.0. The system uses confidence to order the reviewer's queue.
> If everything is flat 1.0 the queue is useless.

This is a real requirement with a real failure mode, and it is easy to fail while producing
perfectly valid JSON. Language models asked for a confidence score tend to emit 0.95 for
everything.

Check the distribution, not the format:

- Compute min / p50 / max across all fields on the golden set
- Compute the **correlation between confidence and correctness** — that is the property the
  reviewer queue actually depends on. If low-confidence fields are no likelier to be wrong than
  high-confidence ones, the number is decoration.
- Sanity checks: a handwritten Lao amount should score lower than a printed Thai tax ID. If it
  does not, the confidence is not measuring anything.

If the model will not self-report usefully, alternatives:
- derive confidence from token logprobs of the emitted value (more honest, needs logprob access
  from the serving backend)
- rule-based penalties layered on the model's number: unparseable date, tax ID that is not 13
  digits, `vat` that is not ≈ 7% of `amountBeforeVat`, `originalTotal` ≠ sum of `lineItems`
- a blend

The rule-based checks are worth building regardless — they are cheap, deterministic, and catch
exactly the cases where the model is confidently wrong.

---

## 5. `evidence` and the 8 roles

`evidence` groups pages by what they are, with regions. A single bill can have several:
the cash bill itself (`cash_bill`) plus an attached exchange-rate printout
(`exchange_rate_evidence`) on a different page — their `sample-response.json` candidate 1 shows
exactly this, with `regions` on `chunkPageIndex: 2` and evidence on page 3.

This is how a multi-page bill is represented, so it is directly a phase-04 concern, not a detail.

Test that:
- an attached transfer slip becomes `role: "transfer_slip"` on the right page
- an attached exchange-rate sheet becomes `role: "exchange_rate_evidence"`
- roles outside the 8-value enum are impossible (the grammar should guarantee this — verify)
- `evidence` may be `[]` (no `minItems` on the array itself), but each entry that exists needs
  ≥ 1 region

---

## 6. Test fixtures to build by hand

The golden set from phase 01 is one-bill-per-case by design. This phase needs its own fixtures,
constructed so the right answer is known exactly:

| Fixture | Content | Correct answer |
|---|---|---|
| `multi-01` | 3 small receipts on 1 page | 3 candidates, all `chunkPageIndex: 0`, non-overlapping regions |
| `multi-02` | 1 receipt per page × 5 pages | 5 candidates, indices 0–4 |
| `span-01` | 1 hotel folio across pages 2–3 | **1** candidate, regions on both pages |
| `span-02` | receipt on page 0 + its transfer slip on page 1 | 1 candidate, 2 evidence entries with different roles |
| `mixed-01` | 10 pages: 2 multi-bill pages, 1 spanning bill, 2 blanks | exact count written out per page |
| `empty-01` | 2 blank / non-receipt pages | `{"billCandidates": []}` — correct and normal per §3 |
| `window-01` | spanning bill placed **exactly on a window boundary** | 1 candidate — this is the test the overlap logic exists for |

`empty-01` matters more than it looks. A model that hallucinates a bill from a blank separator
page injects phantom rows into the ledger, and a reviewer scanning for errors is far likelier to
correct a wrong value than to notice an entire row that should not exist.

Store as `data/fixtures/` with a `README.md` stating the expected answer for each. Gitignored,
same as golden.

---

## 7. Task list

- [x] Question 3 answered — one call carries the whole PDF (F3a). Question 2 (chunk size range)
      still pending but no longer blocking
- [x] Max pages per call — moot. Pages are read one at a time, so context never binds; the limit
      is latency, and phase 06 measured it at ~50 s/page (~10 pages inside the 540 s deadline)
- [x] Fixtures built with written expected answers — `../eval/test_merge.py`, 26 checks. Synthetic
      candidates rather than documents, on purpose: a document fixture exercises OCR, extraction
      and merging at once, so a failure does not say which one broke
- [x] Merge implemented — `../src/merge.py`, wired into `../serving/pipeline.py`. **No window**:
      pages are read independently and merged afterwards, which removes one of the two offsets §2
      warned about
- [x] Offset correctness verified in isolation — `multi-02` and `span-01` assert page indices
- [x] `window-01` passes: boundary-spanning bill is one candidate
- [x] **A real spanning bill, confirmed — it does not exist in this data.** The full 123-page scan
      was run 2026-08-27 (101 pages transcribed for the first time): **zero `หน้า x/y` markers in
      177 pages.** `merge.py`'s designed-for case has never appeared. It still earns its place by
      folding the duplicate copies of one purchase together — FA staples invoice, receipt and
      customer copy — but nobody should spend more on the marker path (overview F24)
- [x] **The multi-page problem FA actually has is a different one** (overview F23): a ใบเสร็จ and
      its `หนังสือรับรองการหักภาษี ณ ที่จ่าย`, two documents for one payment, wanted as one row.
      `merge.py` structurally cannot do it — it keys on adjacency and reads two differing document
      numbers as two bills, but a certificate carries its own WHT number, sits pages from its
      receipt, and never repeats the invoice number (0 of 3 real samples). `../src/certlink.py`
      handles it instead: **two of four signals must agree** — counterparty tax id, base amount,
      document date, seller name — **and none may conflict**, with the withholding rate proving
      itself. 3/3 on real certificates, DT05 confirmed through `serving/pipeline.run` (2 pages in,
      1 candidate out); `../eval/test_certlink.py` 37/37. The first version required tax id **and**
      amount, passed on cached transcripts, and **failed on the live path** — the serving run
      transcribed DT05's certificate with no 13-digit number at all, five runs out of five
- [ ] **Duplicate copies of one purchase still emit two or three rows.** p084/p085 are one
      purchase, p087/p088/p089 another. The certificate attaches to one of them correctly, but the
      duplicates are a separate and larger problem. Owner's position 2026-08-27: FA rechecks, so
      not a blocker
- [x] `empty-01`: no hallucinated bills — the `has_money` guard closes this. Pages with no amount
      anywhere return an empty list instead of a phantom bill
- [x] Confidence/correctness correlation measured — **it is flat** (F16). Not the answer we wanted,
      but measured rather than assumed
- [x] Rule-based validity checks implemented — the five guards in `stage2_extract.py` (F15)
- [x] `candidateIndex` ordering rule documented — in `merge_pages`' docstring: assigned last,
      sequentially in page order, so it never repeats and never depends on how many merges ran
