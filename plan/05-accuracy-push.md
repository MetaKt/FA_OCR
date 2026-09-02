# Phase 05 — Accuracy Push

**Goal:** the highest stratified accuracy this hardware and model can reach, honestly reported.
**Inputs:** phases 01, 03, 04.
**Exit gate:** deliverable **D4** — the stratified accuracy report — is written and defensible.

Note the gate is *"honestly reported"*, not *"≥ 95%"*. Whether 95% is reachable on handwritten
Lao is an empirical question, and the finding is the deliverable either way
(`00-OVERVIEW.md` F4).

> ### Status 2026-08-19 — D4 delivered; about 62%, and the split is the story
>
> ```
> structured   102/136   75.0%    dates, amounts, VAT, withholding, tax IDs
> free text     23/ 68   33.8%    shop names and addresses
> overall      125/204   61.3%    (126 on a second run of identical code)
> ```
>
> Printed 64.9%, handwritten 60.5%. Full progression and the rejected experiments are in overview
> F14; the guards that got there are F15; the serving parameter that mattered most is F13.
>
> **The 95% gate is not being chased.** Owner's decision, 2026-08-18: FA rechecks every extraction
> anyway, so the tool's job is to save typing. A field FA can fix by eye in two seconds is not
> worth a week of tuning.
>
> **Fine-tune go/no-go: NO, with evidence.** A DeepSeek-OCR Thai adapter was tried via unsloth on
> 2026-08-18 and lost to stock Typhoon; the working folders were deleted 2026-08-19. Revisiting
> would need transformers 4.57.x. The cheaper levers were not exhausted first — and two of them,
> `repeat_penalty` and the empty-row collapse, turned out to be worth more than a fine-tune was
> ever likely to be.
>
> **What is actually left here, in order:**
> 1. **More golden cases.** `clearingAmount` and `discount` rest on 2 fields each; ใบรับเงิน on one
>    case. Below ~40 cases, further tuning cannot be distinguished from noise.
> 2. **FA's per-category rules** — 79 of 82 entries in `category_rules.json` are still empty.
> 3. `amountBeforeVat` (66.7%) and `documentDate` (72.7%), both of which dipped as transcripts got
>    longer.
>
> **What is *not* worth more effort:** free-text seller names and addresses. Those misses are
> stage-1 character errors, unreachable from stage 2, and cheap for FA to correct.

---

## 1. Work cheapest-first

Every technique below costs more than the one above it. Exhaust each before moving down.

| Rank | Technique | Cost | Typical gain |
|---|---|---|---|
| 1 | Fix golden-set errors | hours | can be large, and it is *free* accuracy |
| 2 | Image preprocessing | hours | large on bad scans |
| 3 | Prompt refinement | days | moderate |
| 4 | Few-shot examples | days | moderate, costs context |
| 5 | Field-specific post-processing | days | large on dates/amounts specifically |
| 6 | Two-pass self-check | days | moderate, doubles latency |
| 7 | LoRA fine-tune | weeks + data | large, and the only real answer for handwriting |

### 1 — Audit golden before tuning anything

Before treating a mismatch as a model error, check the golden entry. In a hand-transcribed set,
a meaningful share of "failures" are transcription mistakes in the ground truth: a missed Thai
tone mark, a transposed digit, a date typed in BE by accident.

Pull the 20 worst mismatches from the scorer's `mismatches` array and re-read each against the
source document. Fix golden where golden is wrong. This is not cheating — it is the only way the
number means anything. Log each correction in `data/golden/cases.md`.

Do this **first, every time**, before any tuning round.

### 2 — Image preprocessing

Scanned receipts are the worst-case input: thermal paper, skew, shadow, fold lines, staples.

Test each independently against the harness — several of these can *hurt*:

- **Resolution.** `ocr_pipeline.py` currently fits the longest side to 1800px. Test 1400 / 1800 /
  2200. Higher is not automatically better: it costs vision tokens, and past the model's native
  patch resolution it adds nothing.
- **Deskew.** Big win on phone photos of receipts.
- **Contrast / adaptive threshold.** Helps faded thermal print. Can destroy light pencil
  handwriting — measure on the handwriting stratum separately.
- **Denoise.** Helps photocopies.
- **Colour vs greyscale.** Greyscale halves the bytes; verify it does not lose a red stamp or a
  coloured signature that the model was using.
- **Crop to content.** Removes scanner black borders, which otherwise consume tokens.

Never evaluate preprocessing on the overall number alone. A step that lifts printed Thai by 2%
and drops handwriting by 8% is a loss, and the average will not say so.

### 3 — Prompt refinement

Version every prompt (`prompts/extract_v2.txt`, …) and record which version produced which score.
An unversioned prompt makes the whole results history worthless.

Target the specific failures the `byField` breakdown reveals:

- `documentDate` failing → is the model emitting BE years? Emitting `DD/MM/YYYY`? Add the exact
  conversion rule with a worked example.
- `sellerName` failing → is it expanding `บจก.` to `บริษัท … จำกัด`, or picking the buyer instead of
  the seller? Both are common and both are prompt-fixable.
- `currency` failing → is it emitting `บาท` instead of `THB`?
- `sellerBranch` failing → is it interpreting rather than transcribing? §3 forbids that
  explicitly, so quote the rule verbatim in the prompt.

Change **one thing per round** and rescore. Bundled changes produce a number you cannot attribute.

### 4 — Few-shot

One or two worked examples in the prompt, ideally covering the hard strata (a Lao cash bill, a
handwritten receipt). Costs context, which competes with page images in an 8 GB budget — measure
the latency and the accuracy together.

### 5 — Field-specific post-processing

Deterministic code is more reliable than a model for well-defined transformations, and cannot
regress:

- **Date normalization.** Accept whatever the model emits (`15/03/2567`, `15 มี.ค. 67`,
  `2567-03-15`) and convert to ISO in code. Rule: year > 2400 → subtract 543. Also handle 2-digit
  BE years (`67` → 2567 → 2024). This alone may fix most of the `documentDate` stratum.
- **Amount cleanup.** Strip `฿`, `บาท`, `,`, spaces. Beware: some receipts use `.` as a thousands
  separator. Cross-check against `originalTotal` = `amountBeforeVat` + `vat` when both are present.
- **Currency mapping.** `บาท`/`฿`/`Baht` → `THB`; `กีบ`/`₭`/`Kip` → `LAK`; `$`/`USD` → `USD`.
- **Tax ID.** Strip spaces and dashes; must be 13 digits or `null`.

**Caution:** post-processing must not invent values. Converting `15/03/2567` → `2024-03-15` is
normalization. Filling a missing date from a neighbouring receipt is a guess, and R9 forbids it.

### 6 — Two-pass self-check

Run extraction, then feed the JSON *and* the image back and ask the model to verify. Doubles
latency, so only worth it if phase 07 has headroom. Restrict to low-confidence fields to keep the
cost bounded.

### 7 — LoRA fine-tune

The real answer for Thai/Lao handwriting, and the only technique here likely to move that stratum
by a lot. Also the largest commitment.

Prerequisites, all of which are real work:
- **Several hundred labelled examples minimum**, and they must not overlap the golden set —
  training on the eval set produces a number that predicts nothing
- A GPU that can train. An 8 GB laptop card can LoRA a 4B model at low resolution with gradient
  checkpointing, slowly. Not comfortable.
- A held-out split separate from both training and golden

Do not start this before phases 01–04 are complete and the prompt/preprocessing ceiling is
actually measured. It is very easy to spend three weeks fine-tuning past a problem that a
deskew step would have fixed in an afternoon.

If it goes ahead: R18 (host must not train on their documents) governs the **serving** host.
Training on company documents on a company machine for the company's own system is a different
activity — but confirm that reading with the webapp team and whoever owns data governance
before collecting a training set. Get it in writing.

---

## 2. Reporting (deliverable D4)

Their README §6: **"do not hand over a single overall number."**

```
model:      <exact tag + quantization>
prompt:     prompts/extract_vN.txt
decoding:   <backend + config>
preprocess: <steps>
golden:     data/golden/golden.json  (N cases, M scored fields)
date:       YYYY-MM-DD

overall   xx.x%  (m/M fields)          [GATE: 95%]  PASS / BELOW

by language
  th      xx.x%  (n)
  en      xx.x%  (n)
  lo      xx.x%  (n)

by handwriting
  false   xx.x%  (n)
  true    xx.x%  (n)

by documentType
  tax_invoice / receipt / cash_bill / ...

by field
  documentDate / sellerName / sellerTaxId / originalDocumentNumber /
  currency / originalTotal / vat / clearingAmount

top mismatches (from the scorer's `mismatches` array, with cause noted)
```

Report **every stratum**, including the ones that fail. A model at 97% overall and 58% on Lao
handwriting is a specific, actionable finding — it tells the team to route Lao to full manual
entry rather than to a review queue that will be wrong more than half the time. Averaging that
away and shipping "97%" would be the single most damaging thing we could do on this project,
because it survives contact with the demo and fails in production on the documents nobody tested.

State the sample size next to every stratum. 58% on 12 Lao fields is a warning, not a
measurement, and the reader needs to be able to tell the difference.

---

## 3. Task list

- [x] Golden audit round — worst mismatches re-read against source; four answer-key errors found,
      including one number printed in red that the eye had skipped
- [x] Preprocessing matrix tested — autocontrast and greyscale **lost 2 points**; `target_dim`
      1600/1700 lost content. 1500 stands
- [x] Resolution re-tested 2026-08-27 now that `repeat_penalty` is 1.25 (overview F21). The
      earlier sweep ran at 1.1, so its char counts were partly measuring the blank-row loop.
      2000px scores **78.2% vs 77.3%**, +6 fields, concentrated in `sellerAddress`/`sellerTaxId` —
      but it re-triggers the loop on `5bills-p2` with **zero `@`**, so the degeneracy guard misses
      it and the page truncates silently. **1500 stands, for a better reason than before.**
- [x] Model-led wording correction tested and **rejected** (overview F22). Asked to repair
      implausible Thai in `sellerAddress`: 77.2% blind (−1 field), 77.5% with the transcript, and
      **no real place-name error fixed in either arm**. A gazetteer would reach them; the model
      cannot, because `อ.พุนหิน` is not implausible Thai — it is merely not a real place.
      The 42 recoverable string misses are itemised in F22 and left parked, not built.
- [x] Prompt iterations, one change per round, each scored. Finding: shared-prompt additions
      cross-contaminate; form-specific rules belong in `category_rules.json` (F15)
- [ ] Few-shot tested with latency measured alongside accuracy — not attempted
- [x] Deterministic post-processing implemented — dates (BE→CE), amounts, tax IDs, plus the five
      guards (F15)
- [x] Ceiling recorded without fine-tuning: **~62%**, structured 75%
- [x] Fine-tune go/no-go: **no** — DeepSeek-OCR adapter tried 2026-08-18, lost to Typhoon
- [x] D4 report written — overview F14, produced by their scorer
