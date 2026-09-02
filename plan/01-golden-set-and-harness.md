# Phase 01 — Golden Set & Scoring Harness

**Goal:** be able to put a number on any model, at any time, in under two minutes.
**Why first:** every later phase claims "this is better". Without this, none of those claims are
checkable, and the §1 D4 deliverable is impossible.

**Inputs:** real receipt documents (Thai printed, Thai handwritten, Lao — confirmed available).
**Exit gate:** ≥ 40 scored cases spread across the four strata, and their TypeScript scorer runs
end-to-end on a prediction file we generated.

> ### Status 2026-08-19 — working, half the gate met
>
> **The loop runs end to end.** Their TypeScript scorer scores a prediction file we generate;
> `pipeline.ipynb` section 9 goes from images to a stratified percentage in about three minutes.
>
> **12 cases, not 40.** The answer key is complete for what exists — 204 graded fields, no TODOs —
> but it is a third of the gate, and the shortfall now limits what any further tuning can tell us:
> `clearingAmount` and `discount` are judged on **2 fields each**, ใบรับเงิน on one case. This is
> the single most useful thing left to do in this phase.
>
> **Built differently than planned, on purpose.** `to_prediction.py` and `check_response.py` became
> `build_inputs()` and `check_response()` inside one `eval/harness.py`; scoring is never
> reimplemented locally, so their file stays the authority. 22 fields are graded per case, not the
> 8 originally scoped, because the contract grew to 29.
>
> **Two measurement bugs found and fixed here, both flattering-in-reverse** — they made the model
> look *worse*. Comparing a correct `null` as the string `"None"` marked 40 of 47 nulls wrong;
> comparing amounts as text marked `"1750"` ≠ `"1750.00"`. `harness._matches()` now agrees with
> their scorer field for field. Lesson worth keeping: *check the harness before believing a bad
> number.*

---

## 1. Understand their scorer before building for it

Read [`scoring/scoreExtraction.ts`](../../../Downloads/adv-clear-model-handover/adv-clear-model-handover/scoring/scoreExtraction.ts)
and [`scoring/normalization.ts`](../../../Downloads/adv-clear-model-handover/adv-clear-model-handover/scoring/normalization.ts).
The behaviour that matters:

- **Only fields present in `golden.cases[].fields` are scored.** Omitting a field from golden
  means it is not counted at all. This is our lever for scoping — do not abuse it, or the number
  becomes meaningless.
- **A field the model did not return counts as WRONG, not skipped.** `normalizeField` maps
  `null`/`undefined` → `""`, and `""` will not equal a non-empty expected value. Silence cannot
  raise the score.
- **Normalization is narrow.** Only three special cases:
  - `documentDate` — Buddhist-era Thai dates parse to ISO (`15 มี.ค. 2567` → `2024-03-15`).
    The parser expects exactly `D+ <space> <month-token> <space> YYYY`. Anything else falls
    through as literal text.
  - amount fields (`clearingAmount`, `amountBeforeVat`, `vat`, `withholdingTax`, `originalTotal`)
    — commas stripped, forced to 2 decimals.
  - `currency` — uppercased.
  - Everything else: NFKC + whitespace collapse only. **`sellerName` has no fuzzy matching.**
- Grouping keys come from golden: `language`, `documentType`, `handwriting`. Those strings are
  ours to choose but must be **consistent**, or the stratified report fragments into useless
  one-case buckets.

### Get it running first, on their fixtures

```bash
cd "C:\Users\meta_k\Downloads\adv-clear-model-handover\adv-clear-model-handover\scoring" && npm install && npm run demo
```

Expected: `overall 87.5% (7/8 fields)` and `BELOW GATE — 95% required`. If that does not
reproduce, stop and fix the Node setup before doing anything else. Needs Node ≥ 20.

**Do not copy the scorer into our repo.** It is their artifact and may be regenerated. Call it
where it sits, or add it as a path dependency.

---

## 2. Stratum vocabulary — fix these strings now

Written down so every golden file in the project agrees. Changing them later invalidates
cross-phase comparisons.

| Key | Allowed values |
|---|---|
| `language` | `th` · `en` · `lo` · `mixed` |
| `documentType` | `tax_invoice` · `receipt` · `cash_bill` · `transfer_slip` · `other` |
| `handwriting` | `true` if **any scored field's value** is handwritten, else `false` |

`documentType` here is the *stratum label*, not the model's `documentType.value` string (which is
free text like `ใบกำกับภาษี/ใบเสร็จรับเงิน`). Do not conflate them.

`handwriting: true` is deliberately generous — a printed invoice with a handwritten total counts
as handwriting, because that handwritten total is the field most likely to be wrong.

---

## 3. Which fields to score

Start with the eight that drive the FA ledger and the reviewer's time. Widen later.

| Field | Score from day 1? | Note |
|---|---|---|
| `documentDate` | yes | BE→CE conversion is on us, normalization only helps if we emit ISO or the exact Thai pattern |
| `sellerName` | yes | hardest text field, no fuzzy match |
| `sellerTaxId` | yes | 13 digits, unambiguous, good sanity signal |
| `originalDocumentNumber` | yes | |
| `currency` | yes | should be near-100%, an early red flag if not |
| `originalTotal` | yes | |
| `vat` | yes | |
| `clearingAmount` | yes | the number the ledger actually uses |
| `amountBeforeVat` | later | |
| `withholdingTax` | later | often absent; decide `0` vs `null` policy first — see §6 |
| `sellerAddress` | no | long free text, exact match is a coin flip, will drag the number down without telling us anything actionable |
| `sellerBranch` | later | "record as printed, do not interpret" (§3) makes this testable, add once basics hold |
| `lineItems` | no | array field, their scorer is flat `Record<string,string>` and cannot score it. Needs a separate check — see §7 |

---

## 4. Building the golden set

**Target: 40–60 cases minimum**, distributed so no stratum has fewer than 8. With 8 scored fields
per case, 50 cases = 400 scored field comparisons, enough that a 95% gate means something
(each field is 0.25% of the total).

Suggested distribution:

Revised 2026-08-10 for the Lao descope (`00-OVERVIEW.md` F6) — those cases move to Thai handwriting,
which is now the stratum that decides the project:

| Stratum | Cases |
|---|---|
| th / printed | 20 |
| **th / handwritten** | **22** |
| en or mixed | 8 |

Keep `lo` in the vocabulary (§2) so a Lao case can be added later without renumbering anything,
but do not collect any now.

### Procedure

1. **Collect** source documents into `data/golden/source/` — one file per case, named `case-NNN`
   plus the original extension. Keep them out of version control (see §8).
2. **Define a case = one bill**, not one page. A bill spanning 3 pages is one case. Two bills on
   one page are two cases.
3. **Transcribe by hand.** Type what is printed. Do not clean up, do not expand abbreviations,
   do not fix the vendor's typos. The model will be scored on exact match against this, so a
   "helpful" correction here manufactures a permanent false failure.
4. **Dates → ISO `YYYY-MM-DD`** in golden. Their normalizer accepts Thai BE format too, but ISO
   removes any chance of the Thai parser's strict pattern rejecting our string.
5. **Amounts → plain string, no commas, 2 decimals** (`"1285.60"`). Their normalizer would handle
   commas, but being canonical in golden makes diffs readable.
6. **Unknown / not present on the document → omit the key entirely.** Do NOT put `""` or `null`.
   An omitted key is unscored; an empty string is a scored expectation of emptiness, which is a
   different and usually wrong thing to assert.
7. **Second-pass review.** Have someone else (or yourself a day later) re-read 10 random cases
   against the source. Golden-set errors are the most expensive kind of bug here: they make a
   correct model look broken and send phase 05 chasing ghosts.

### File location

```
FA_OCR/
  data/
    golden/
      source/            # the actual documents, gitignored
      golden.json        # the ground truth
      cases.md           # per-case notes: why a field was omitted, ambiguities, decisions
```

`cases.md` matters. Six weeks from now the question "why is `vat` missing on case-017?" needs an
answer that is not "I don't remember".

### Format (from `scoring/README.md`)

```json
{
  "cases": [
    {
      "caseId": "case-001",
      "language": "th",
      "documentType": "tax_invoice",
      "handwriting": false,
      "fields": {
        "documentDate": "2026-07-14",
        "sellerName": "บริษัท วัสดุก่อสร้างไทย จำกัด",
        "sellerTaxId": "0105536000123",
        "originalDocumentNumber": "IV6807-0142",
        "currency": "THB",
        "originalTotal": "6334.40",
        "vat": "414.40",
        "clearingAmount": "6334.40"
      }
    }
  ]
}
```

---

## 5. The adapter: model output → `prediction.json`

Their scorer wants a flat `{caseId, fields: Record<string, string|null>}`. Our model emits nested
wrapped fields inside `billCandidates`. Something must flatten it. That something is ours.

Write `eval/to_prediction.py`:

- input: a directory of raw model responses, one JSON per case, named `case-NNN.json`
- for each: parse, take `billCandidates[0]` (single-bill cases), read `.value` out of each
  wrapper, stringify numbers **without** losing decimals (`6334.4` → `"6334.40"`)
- `null` value → emit `null` (their scorer normalizes it to `""` and counts it wrong, which is
  the correct behaviour per R14)
- zero candidates → emit `{"caseId": ..., "fields": {}}`, which scores every field wrong. Correct:
  finding no bill on a page that has one is a total failure for that case.
- more than one candidate on a single-bill case → **do not silently pick one.** Log it loudly and
  emit `fields: {}`. Over-splitting is a real defect (R12) and must not be scored as if it were a
  field-level miss.

Keep the adapter dumb. Any cleverness in here (picking the best-matching candidate, fuzzy field
mapping) inflates the score by fixing model defects in the harness. If we want that behaviour it
belongs in the model service, where the colleague's system will actually get it.

---

## 6. Two policy decisions to make and write down

**`withholdingTax` absent from the document: `0` or `null`?**
R9 says unknown → `null`, never guess. But a Thai receipt with no WHT line genuinely means zero,
not unknown. Their own `sample-response.json` uses `"withholdingTax": {"value": 0, "confidence": 0.8}`
for a document with no WHT line — so their reference behaviour is `0`. Match that. Record the
decision in `cases.md` and apply it consistently in golden. Same reasoning for `vat` on a
non-VAT cash bill.

**`clearingAmount` when the document does not state one.**
It is a derived business figure (what gets cleared), not always printed. Decide whether golden
expects the model to compute it or return `null`. Ask the webapp team if unclear — this is
question 4-adjacent and worth adding to the list in `00-OVERVIEW.md` §3 if it bites.

---

## 7. What their scorer cannot check — build a second, small checker

`scoreExtraction.ts` only handles flat string fields. It cannot tell us about:

- **schema validity** — the single most important property, since an invalid response loses the
  whole chunk (§4). Check with `jsonschema` in Python against `bill-extraction.schema.json`.
- **candidate count** — did we find the right number of bills? (R4, R12)
- **bounding boxes** — present, in range, `xMax>xMin`, `yMax>yMin`, plausible `chunkPageIndex` (R7, R8)
- **confidence distribution** — flat 1.0 violates R13
- **lineItems**

Write `eval/check_response.py` producing a small report alongside the accuracy number:

```
schema valid        50/50
candidate count     47/50   (2 over-split, 1 missed)
regions present     50/50   (18 whole-page placeholders)
confidence spread   min 0.31  p50 0.88  max 0.99   [OK, not flat]
```

The "whole-page placeholders" line exists because of finding F1 in `00-OVERVIEW.md` — if we end
up emitting `{0,0,1000,1000}`, we count them and report the count honestly rather than letting a
green "regions present 50/50" imply the boxes are useful.

---

## 8. Data handling

These are real company documents containing employee names, tax IDs and bank account numbers.

- `data/` goes in `.gitignore` from the first commit. Add the ignore rule **before** the first
  document lands in the folder.
- Everything stays on the local machine. No cloud sync folder, no uploading a sample to a
  hosted model to "just check".
- If any Gemini reference number is wanted for comparison (their system already uses Gemini),
  that is a decision for the webapp team to make with their existing data-processing agreement —
  not something to do unilaterally from this laptop.

---

## 9. Task list

- [x] Node 24 LTS installed; their scorer runs on our files
- [x] `.gitignore` covers the sensitive parts of `data/` — `data/samples/` and `golden.json`
- [x] Stratum vocabulary from §2 written into `data/golden/cases.md`
- [ ] Documents collected, ≥ 8 per stratum — **12 cases total, the gate wants 40**
- [x] `golden.json` transcribed — 12 cases × 22 graded fields, 0 TODO
- [x] Cases re-reviewed against source; four answer-key errors found and corrected
      (`buyerName` typos, test3's `0644` printed in red, test2's `discount` decimals)
- [x] `eval/to_prediction.py` written — landed as `harness.build_inputs()`
- [x] `eval/check_response.py` written — landed as `harness.check_response()`
- [x] Full loop proven; stratified report appears and the numbers move as expected
