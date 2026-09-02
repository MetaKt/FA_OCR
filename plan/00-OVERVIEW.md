# ADV Clear — Local Vision Model — Plan Overview

**Owner:** AI model side (me). The webapp/orchestration side is owned by a colleague.
**Contract:** `C:\Users\meta_k\Downloads\adv-clear-model-handover\adv-clear-model-handover\`
**Created:** 2026-08-10

---

## 0. What we are actually being asked to build

Not "an OCR model". An **HTTP service** that takes a PDF chunk (≤ 40 MB) or page images
and returns one strict JSON object:

```json
{ "billCandidates": [ /* 0..n BillCandidate */ ] }
```

Everything about the JSON shape is **non-negotiable** — `packages/contracts/src/extraction.ts`
on the webapp side is the single source of truth, the schema is `.strict()`, and a response that
fails validation is **discarded whole**, not partially accepted. One stray key loses all 40 bills
in the chunk.

The internals are ours. The contract constrains the boundary, not the pipeline. We may run
OCR → LLM internally; §2's "no OCR in front" means *their* system does not pre-OCR for us.

### Hard requirements pulled from the contract

| # | Requirement | Source |
|---|---|---|
| R1 | Vision input: page images or PDF, not text | §2 |
| R2 | Thai + English + ~~Lao~~ — **descoped 2026-08-10, see F6** | §2 |
| R3 | Handwriting must be readable | §2 |
| R4 | **Model** decides bill boundaries — multi-bill-per-page and bill-across-pages | §2 |
| R5 | Deterministic: temperature 0, fixed seed, same chunk → same output (queue retries) | §2 |
| R6 | Constrained decoding against the real schema file. Prompting "reply in JSON" is not acceptable | §4 |
| R7 | ~~Bounding boxes: integers 0–1000, normalized per page~~ — **dead 2026-08-11**, `regions` removed from the contract | §4 |
| R8 | `chunkPageIndex` is 0-based **within the chunk we were handed**; never guess the real page number | §4 |
| R9 | Unknown → `null`. Never guess, never empty string | §3 |
| R10 | Never confirm a category. `suggestedCategoryId` is a suggestion + reason + confidence | §5 |
| R11 | Never invent keys (`notes`, `approved`, …) | §5 |
| R12 | Ambiguous bill boundary → emit 2 candidates, let the human decide | §5 |
| R13 | Confidence must not be flat 1.0 — it drives the reviewer's work queue | §5 |
| R14 | ≥ 95% field-level exact accuracy **before human correction**, reported by stratum | §6 |
| R15 | 5 concurrent jobs; 300 pages/job; ≤ 40 MB chunk answered inside a **5 min** worker lease | §7 |
| R16 | Errors split into **retryable** (load, timeout) vs **terminal** (unsupported input) | §7 |
| R17 | Bearer token or mTLS; endpoint unreachable from outside the network | §7 |
| R18 | Host machine must not retain documents and must not train on them | §7 |

### The 29 keys of `BillCandidate` — exactly, no more, no fewer

**Superseded twice since this file was written.** Trimmed 2026-08-11 by agreement with the webapp
team (`regions`, `evidence`, the three `…Category…` keys and every `sourceRegions` dropped;
`chunkPageIndex` added back), then extended 2026-08-18 with six fields FA asked for. The list
below is what `stage2_extract.contract_schema()` actually derives at run time — verified 29 keys.

`candidateIndex` (bare integer) · `chunkPageIndex` (bare integer) · `documentType`
· `originalDocumentNumber` · `documentBookNumber` · `documentDate` · `payeeType`
· `sellerName` · `sellerAddress` · `sellerTaxId` · `sellerBranch`
· `buyerName` · `buyerAddress` · `buyerTaxId` · `buyerBranch`
· `lineItems` · `currency` · `originalTotal` · `discount` · `exchangeRate` · `exchangeRateSource`
· `amountBeforeVat` · `vat` · `vatExemptAmount` · `vatRate`
· `withholdingTax` · `withholdingTaxRate` · `clearingAmount` · `paymentMethod`

Every scalar above except the two bare integers is **wrapped**:

```json
{ "value": <v|null>, "confidence": 0.0-1.0 }
```

The eleven added on 2026-08-18 — `buyerName` · `buyerAddress` · `buyerTaxId` · `buyerBranch`
· `documentBookNumber` · `payeeType` · `paymentMethod` · `discount` · `vatExemptAmount`
· `vatRate` · `withholdingTaxRate` — took the contract from 18 fields to 29. They came from **FA**,
not from the webapp team: we produce the data, so the shape is ours to define and only the naming
needs agreeing. Written up for them in [`../handover/README.md`](../handover/README.md) with six
worked examples.

`clearingAmount` = `amountBeforeVat + vat − withholdingTax`, verified against FM-FA-05 totals.

---

## 1. Findings that change the plan

### F1 — RESOLVED 2026-08-11: the contradiction was removed, not settled

**`regions` no longer exists.** The webapp team dropped it from the contract along with `evidence`
and the category keys, so there is nothing left to reconcile — no real boxes, no whole-page
placeholder, no degraded click-to-highlight. F1 and F1a below are kept as the record of how the
decision was reached; neither constrains anything now, and **D5 is answered: no bounding boxes.**

The one consequence that outlived the decision: choosing a stage-1 model for its grounding ability
(F1a's advice) stopped mattering, which is part of why Typhoon was free to win on Thai alone.

<details>
<summary>Original finding, kept for the record</summary>

#### F1 — Bounding boxes are mandatory, contrary to §0

README §0 presents boxes as optional ("the system still works, you just lose the
human-verification guarantee"). **The schema does not agree.**

`bill-extraction.schema.json` line 1305:

```json
"regions": { "minItems": 1, "type": "array", ... }
```

and `regions` is in the candidate's `required` list (line 1468). `evidence[].regions` is
`minItems: 1` too (line 1366).

So a schema-valid `BillCandidate` **cannot exist without at least one region**. There are only
two honest positions:

- emit real boxes, or
- emit a degenerate whole-page box `{0,0,1000,1000}` on every candidate purely to pass validation

The second passes the validator and silently destroys the guarantee §0 was trying to protect.
**This must be resolved with the webapp team before phase 03 picks a model.** See §3 below.

</details>

### F2 — Typhoon OCR 1.5 cannot satisfy this contract alone

Empirically established in earlier work (see `../ocr_pipeline.py` docstrings): typhoon-ocr1.5 is
fine-tuned so tightly on its own v1.5 prompt that any substitute prompt makes it echo the
training prompt instead of reading the page. Verified against `ollama show --system` — it is
baked into the weights, not an overridable config.

Consequences:
- Typhoon can only ever be **stage one** (image → Markdown) of a two-stage pipeline
- Typhoon output has **no coordinates**, so the Typhoon path cannot answer R7 without a third
  component (text detector → word boxes → fuzzy string match back)
- Typhoon is **Thai/English**; R2 requires Lao

Typhoon stays in the bake-off as a baseline, not as the presumed answer.

### F3 — Hardware is a laptop for now

Confirmed decision: development and first deployment run on my laptop (RTX 5060 Laptop, 8 GB
VRAM) or my colleague's, moving to a company server only if the project proves out.

- 8 GB VRAM caps us at roughly a 4B model at bf16, or an 8B at 4-bit quantization
- vLLM has no native Windows build → WSL2, or use llama.cpp / Ollama structured output on Windows
- **R15 (5 concurrent, 40 pages in 5 min) is not achievable on this hardware.** Phase 07 produces
  a procurement spec instead of a passing number, and every timing we report must be labelled
  "laptop, single stream" so it is never mistaken for the contract deliverable.

### F4 — The 95% gate will not hold uniformly

R14 with **no fuzzy matching on seller names** (`บริษัท เอ จำกัด` ≠ `บจก. เอ`) is plausible for
printed Thai tax invoices and unlikely for handwritten Lao cash bills. §6 already mandates a
stratified report, which is the mechanism that makes this visible rather than hidden in an
average. Expect to negotiate a per-stratum gate; do not expect to argue the number down.

### F6 — Lao descoped; Typhoon is back in contention

**Decision 2026-08-10:** focus on **Thai plus some English**. Lao is out of scope for now.

This reverses part of F2. Typhoon OCR 1.5 had two disqualifiers — no Lao, and no bounding boxes.
Lao is gone, and Q1 made boxes optional (F1a). What remains true is only that Typhoon cannot be
the *whole* system, because it emits Markdown, not JSON. It can be **stage 1 of Architecture B**,
and it is the only candidate purpose-built for Thai.

Consequences:
- Typhoon returns as a **primary** candidate, not a baseline
- The prior work on `../ocr_pipeline.py` stays useful
- Phase 01 golden set: drop the Lao stratum, reallocate those cases to Thai handwriting
- Phase 05: the handwriting stratum is now the hardest one, and the one that decides the project
- **Note for the handover:** R2 names Lao explicitly. Descoping it is a decision the webapp team
  must confirm, not something we assume. Add it to the question list and state it plainly in
  phase 08 — if Lao receipts reach production, the system will fail on them silently.

### F7 — Qwen3-VL on Thai handwriting is unverified, and there is reason for doubt

Qwen3-VL is the natural Architecture A candidate, but the case for it on Thai is weaker than it
looks. SCB10x built Typhoon OCR by fine-tuning Qwen2.5-VL on Thai documents — a Thai lab
evaluated base Qwen, judged it insufficient, and paid to improve it. Assuming Qwen3-VL closed
that gap is an assumption, not a finding.

Thai is hard for OCR in specific, measurable ways:
- no spaces between words, so segmentation errors cascade
- up to three levels of stacked diacritics (vowel + tone mark above the base glyph)
- visually near-identical pairs: ก/ถ · ด/ค/ต · ข/ช · บ/ป/ษ · พ/ผ/ฝ · น/ม · ร/ธ · ฎ/ฏ
- in **handwriting**, the loop (หัว) that distinguishes several of those pairs is routinely
  simplified or omitted

Do not resolve this by argument. Resolve it with the spot test in `03-model-bakeoff.md` §3a before
committing to an architecture.

### F8 — DECIDED: Architecture B (Typhoon → text LLM)

**2026-08-10.** Spot test run (`../spot_test.py`, `../model.ipynb`) over the four real receipts.
Typhoon-ocr1.5-3b beat Qwen3-VL-4B decisively on Thai, so F7's doubt is resolved against Qwen.

Measured on `test.png`, and consistent across the set:

| | typhoon-3b | qwen3-vl-4b |
|---|---|---|
| latency | 18.6 s | 68.9 s |
| document date | `20/7/2569` correct | `20/7/1956` invented |
| seller tax ID | correct | blank; digits leaked into the address line |
| seller name | correct | returned the blank form's **label** (`นามสกุล ชื่อ`) instead of the handwritten value |

Qwen3-VL's failure mode was not glyph confusion — it read printed form labels instead of the
handwriting and fabricated a date. Worse than a misread character, because it produces
confident-looking wrong values rather than obvious garbage.

**The architecture is therefore:**

```
PDF -> pages -> [typhoon-ocr1.5-3b] -> HTML/Markdown -> [text LLM + constrained decoding] -> schema JSON
```

Consequences, all of which are now commitments rather than options:

1. **No real bounding boxes.** Typhoon emits no coordinates. Per F1a we emit
   `{chunkPageIndex, 0, 0, 1000, 1000}` on every candidate to stay schema-valid. **Tell the
   webapp team explicitly** — this is deliverable D5 and it means their click-to-highlight
   degrades to "somewhere on this page". Box recovery via a separate detector stays on the shelf
   (`03-model-bakeoff.md` Branch C) if they want it later.
2. **Two models share 8 GB**, loaded sequentially. Latency is stage 1 + stage 2, so the phase 07
   arithmetic starts from ~19 s/page plus stage 2, not 19 s.
3. **Bill segmentation moves into the text domain.** Typhoon transcribes a page as one flowing
   document; it does not say "there are three bills here". Stage 2 must infer boundaries from the
   transcript. Plausible — each bill repeats a header and a total — but strictly weaker than a
   vision model that sees the layout, and it is the main risk this architecture carries.
   `บิลเงินสด_ร้านค้า_5ใบ.pdf` is the fixture for testing it, and phase 04 should start there.
4. **Typhoon 3b vs 7b is unresolved but not blocking.** The 7b is a 16 GB model on an 8 GB card,
   so it mostly runs on CPU — minutes per page. That is almost certainly the answer to the old
   "why does 3b beat 7b" puzzle (hypothesis 2, `03-model-bakeoff.md` §6). Revisit on server
   hardware, where 7b may win.
5. **Do not let this result bias the stage-2 choice.** Qwen3-VL lost at *reading Thai from an
   image*. Stage 2 is a different task — *understanding Thai text that is already transcribed* —
   and Qwen3's text models may be perfectly good at it. Judge stage 2 on its own evidence.

### F10 — stage 2: qwen3-4b with thinking OFF, plus deterministic date/amount parsing

Measured on the four real receipts, transcripts regenerated at `repeat_penalty=1.2`:

| | typhoon2-8b | qwen3-4b (thinking) | **qwen3-4b (think off)** |
|---|---|---|---|
| seconds/receipt | 18–33 | 64–116 | **10–22** |
| `originalTotal` correct | 1 of 4 | 4 of 4 | **4 of 4** |
| `sellerName` | hallucinated a name absent from the transcript on test3 | correct | correct |
| `documentDate` | 2 of 4 | 2 of 4 | 1 of 4 |
| schema valid | 4/4 | 4/4 | 4/4 |

**typhoon2-8b is rejected.** It returned `null` for the total on three of four receipts — the
single most important field — and on test3 it invented `นายสมศักดิ์ เกาะแก้ว`, a seller name that
appears nowhere in the transcript. A plausible-looking wrong value is worse than a null here: FA
will retype a blank, but may accept a fabrication.

**Thinking off is a clean win on everything except dates**, and dates should not be the model's
job anyway. The transcripts contain `3/7/69`, `30/7/69`, `20/7/2569` — a regex plus "year > 2400
means subtract 543" gets these right every time, deterministically, in microseconds. Asking a 4B
model to do calendar arithmetic when the raw string is sitting in front of us is the wrong
division of labour (already argued in `05-accuracy-push.md` §1 step 5; this is the evidence).

So the stage-2 design is: **qwen3:4b, `think: false`, grammar-constrained, with dates and amounts
normalized in Python afterwards.** Expected ~10–22 s/receipt on top of stage 1.

Still broken and needing the prompt fix: **`sellerTaxId` is consistently the buyer's ID or
garbage** (`901712569`, `149`). On these receipts TEAM is the customer and its ID `0107561000030`
is printed on the page, so the model grabs it. Two fixes, both cheap:
- prompt: state that TEAM Consulting is the buyer, never the seller, and that a tax ID in a
  customer/`ลูกค้า`/`รหัส CUSTOMER` block is not the seller's
- validation: a Thai tax ID is exactly 13 digits, else `null`

### F11 — RESOLVED: the failure was `target_dim=1800` plus measurement contamination

**Resolution 2026-08-10.** Two separate causes, both now understood.

**Cause 1 — exactly 1800 px breaks the model on some pages.** Page 3 of the 5-bill PDF, tested on a
cold runner at each size so the measurements are independent:

| longest side | result |
|---|---|
| 900 | 437 chars |
| 1100 | 15298 chars |
| 1300 | 1454 chars |
| 1500 | 13138 chars |
| **1800** | **FAILED — `'@'*31`** |
| 2000 | 1411 chars |

Only 1800 fails, and 1800 is the size Typhoon's own model card recommends ("trained with a fixed
image dimension of 1800 px"). `ocr_pipeline.DEFAULT_TARGET_DIM` is now **1500**.

**Cause 2 — Ollama's prompt cache made every back-to-back measurement dependent on the previous
request.** With `unload()` between requests, pages 4 and 5 transcribe fine at 1800; without it they
fail. That is why the earlier readings contradicted each other, and it is why the size sweep
initially looked like "everything fails at every size".

**Verified working configuration (2026-08-10):** `target_dim=1500`, `repeat_penalty=1.1`, no
unloading — **all 5 pages transcribe, twice, back to back (10/10).**

> **Superseded 2026-08-18 — do not use 1.1.** See F13. `target_dim=1500` still stands; the penalty
> is now **1.25**. 1.1 transcribes the page but loops on a pre-printed form's empty ruled boxes and
> gets truncated before it reaches the totals, which this 5-page fixture did not expose because
> its bills are hand-written on mostly blank paper.

**Methodology lesson worth keeping:** any measurement taken from a long sequential Ollama run is
suspect. Either unload between requests, or verify a result twice from a cold start before
believing it. Several hours were lost to conclusions drawn from contaminated single runs.

### F12 — output is still not byte-identical across runs (R5 risk, not a blocker)

At the working configuration, all pages succeed but three of five differ between two identical
runs:

| page | run 1 | run 2 |
|---|---|---|
| 1 | 815 chars | 755 chars |
| 2 | 6907 chars, same hash | 6907 chars, same hash |
| 3 | 13138 chars | 13148 chars |
| 4 | 13151 chars, same hash | 13151 chars, same hash |
| 5 | 1297 chars, hash d89 | 1297 chars, hash 62f |

`temperature=0`, `seed=42`, `top_p=0.6` on every call. Contract R5 wants a retried chunk to
produce the same output, so this needs closing before handover — but it is far less serious than
total failure and does not block phase 01. Likely candidates: non-deterministic GPU kernel
reductions, or prompt-cache state again. Revisit alongside the serving-stack decision in phase 06.

### F13 — `repeat_penalty` must be 1.25; empty ruled boxes were eating the page

**2026-08-18.** A pre-printed receipt form is a grid of empty ruled boxes, and the v1.5 prompt asks
for HTML tables, so Typhoon transcribes every blank line as `<tr><td>-</td>…`. Below ~1.2 it falls
into that pattern and never climbs out, filling the context with dash rows and getting truncated
**before it reaches the totals at the bottom of the page**. The transcript looks long and healthy;
the numbers FA needs are simply absent.

On `test2.png`:

| `repeat_penalty` | output | ส่วนลด + total present |
|---|---|---|
| 1.1 | 14251 chars in 80 s | **no** |
| **1.25** | 844 chars in 14 s | **yes** |

Raising `num_ctx` does not help — it buys room for more dashes (31066 chars, still no total).
Re-measured across all twelve golden pages: none lost content, two gained a third more.

Two changes came out of this, both live:

- `ocr_pipeline.ocr_page(repeat_penalty=1.25)` — the default
- `ocr_pipeline.collapse_empty_rows()` — deletes rows where *every* cell is blank, applied after
  OCR rather than during, so the rule can change without invalidating cached transcripts. On one
  page: 281 rows, 1124 cells, 1117 of them empty — 96% of a 13 KB transcript was markup for boxes
  nobody wrote in. Safe by construction: a row with no content in any cell carries nothing stage 2
  could have used.

This also retires the open question left hanging in F9 ("15 k chars for a delivery note is
suspicious and may be a new degeneration on the form's empty table rows"). It was.

### F14 — first real accuracy number: about 62%, and the split matters more than the number

**2026-08-19.** Answer key complete — 12 pages, 204 graded fields — scored with the webapp team's
own TypeScript scorer.

```
structured   102/136   75.0%    dates, amounts, VAT, withholding, tax IDs
free text     23/ 68   33.8%    shop names and addresses
overall      125/204   61.3%    (126 on a second run of the same code -- see F12)
```

**The headline understates the tool.** The fields FA actually types into FM-FA-05 run at three
quarters; the average is dragged down by names and addresses, where exact match scores
`หาญพันธ์` against `นามพันธุ์` the same as a blank. FA corrects those by eye in seconds. Report
the split, never the single number.

How it got there, in order, each step measured:

| | overall |
|---|---|
| first run | 53.9% |
| party resolver (`resolve_parties`) | 54.4% |
| **`repeat_penalty` 1.25** (F13) — 10/12 → 12/12 bills found | 56.4% |
| answer-key corrections (`buyerName`) | 58.3% |
| resolver moves individual parts, not whole blocks | 58.8% |
| tax-ID shape validated *before* side-assignment | 59.8% |
| document-number label proximity | 60.3% |
| answer-key corrections (`0644`, `discount`) | **61.3–61.8%** |

Measured and rejected as *not* helping: appending rules to the stage-1 prompt (page 20's national
ID went from correctly-rejected 12 digits to a wrongly-accepted 13), autocontrast and greyscale
(−2 points), `target_dim` 1600 and 1700 (content lost).

**The gate.** R14 asks for ≥95%. The owner's position, 2026-08-18: *"95% from the colleague's
handover is not that necessary, basically just have to be as accurate as possible"* — FA rechecks
every extraction anyway, so the tool's job is to save typing, not to be trusted unattended. Q5 is
therefore answered from our side and does not block.

### F15 — a deterministic code guard beats a prompt rule, five times out of five

**2026-08-18.** Every stage-2 defect that was fixed by writing Python stayed fixed. Every one
attempted in the prompt either failed outright or damaged unrelated documents.

Guards now live in `../src/stage2_extract.py`, each with the defect it closes:

| guard | was producing |
|---|---|
| `clean_tax_id` | the buyer's ID, or 9-digit garbage, in `sellerTaxId` |
| `clean_document_number` | postcodes and book numbers as `originalDocumentNumber` |
| `clean_payment_method` | masked card numbers — `KBANK-CARD/XXXXXXXXXXXX5348` |
| `has_money` | phantom bills from ID-card copies and delivery notes |
| `resolve_parties` | TEAM's own name and address as the *seller* |

`clean_payment_method` is the clearest case: the prompt fix was written, tested, and **verified not
to work** before the guard replaced it. Leaking masked card numbers into a field the webapp stores
is also the one defect here with a privacy dimension, which is another reason not to leave it to a
4B model's goodwill.

**And prompt bloat actively hurts.** Adding phantom-bill and ใบรับเงิน rules to the *shared* prompt
made unrelated documents worse — test6 and test7 flipped to `payeeType: individual`, test6 lost its
`sellerBranch`, p165's `amountBeforeVat` regressed. Reverted. Form-specific rules belong in
`../data/category_rules.json`, scoped to the category that needs them; the shared prompt stays
short.

### F16 — `confidence` is not predictive, which puts R13 at risk

**2026-08-19.** R13 says confidence must not be flat because it drives the reviewer's work queue.
It is not literally flat — but it carries no information:

```
confidence      correct     n   accuracy
0.80-0.95           34     52       65%
0.95-1.00           90    142       63%
```

194 of 204 fields sit in those two bands, and they score the same. The model says 0.95+ on nearly
everything, right or wrong. **Highlighting low-confidence fields in the review UI would send FA's
attention to the wrong rows** — worse than showing no confidence at all. Tell the webapp team
before they build on it.

Measuring this honestly took two fixes to the harness itself, both worth remembering: comparing a
correctly-predicted `null` as the string `"None"` marked 40 of 47 nulls wrong, and comparing
amounts as text marked `"1750"` ≠ `"1750.00"`. Both made the model look worse than it is and both
were in *our* measurement, not the pipeline. `harness._matches()` now carries the reasoning.

### F20 — the served endpoint was nine points below the pipeline, because of one flag

**2026-08-27.** `stage2_extract.FILL_BUYER_FROM_CONSTANT` was found set to `False`. It is a
diagnostic setting — off, the buyer block is whatever the model read off the photocopy; on, it is
filled from the constant when the page names us. Both measured the same day, same 1500px
transcripts, same model:

| `FILL_BUYER_FROM_CONSTANT` | overall | buyerName | buyerAddress | buyerTaxId |
|---|---|---|---|---|
| `False` — what was being served | **67.9%** (440/648) | 7/34 | 7/34 | 16/34 |
| `True` — what 77.3% was measured with | **77.3%** (501/648) | 29/34 | 25/34 | 30/34 |

Set back to `True`. The score is the smaller half of it: with the flag off, a page where stage 1
lost the buyer block yields a *well-formed wrong company* — `บริษัท พีม คอนสูร์ฟอร์`, a plausible
tax id, a district we have no office in — and per the guard's own docstring that reads as a fact
and gets typed into FM-FA-05. A blank sends FA back to the paper.

**The lesson is about measurement hygiene, not about the flag.** Nothing detected this. The
harness reports whatever `src/` is currently configured to do, and a diagnostic left switched on
looks exactly like a working configuration. Any run whose number is quoted later should print its
flags first — `eval/harness.py` now does, and the drift was found only because a comparison run
happened to print them side by side.

### F21 — resolution is per-page, and no single value is safe

**2026-08-27.** Re-tested after F13 changed `repeat_penalty` to 1.25, because F11's original sweep
was measured at 1.1 and its char counts were partly counting the blank-row loop rather than
content — page 3 of the 5-bill PDF read 13,138 chars at 1500 then, and reads 1,084 now.

Scored on the golden set, like for like, both arms with the buyer constant on:

```
1500 px    77.3%   (501/648)
2000 px    78.2%   (507/648)     +6 fields
```

2000 genuinely reads more: on the DT05 certificate it recovered the seller's whole second party
block, the English letterhead address, the correct document number (`047275`, where 1500 returned
the ISO certification number off the logo), the VAT line, and the branch. Its gains concentrate in
`sellerAddress`, `sellerTaxId` and the buyer fields — the fields stage 1 loses.

**Rejected anyway, and `TARGET_DIM` stays 1500.** No single resolution is safe on all pages:

```
                5bills-p2                    5bills-p3
1500 px         clean                        clean
1800 px         clean                        FAILS  '@'x31   (still, at 1.25)
2000 px         BLANK-ROW LOOP, 9266 chars   clean
```

1800 remains dead — F11 was right and the penalty was not the cause. The decisive objection to
2000 is that its failure is *undetectable*: the loop produces 9,266 characters with zero `@`, so
`extract_page`'s degeneracy guard passes it and the page is silently truncated before its totals.
1800's failure is caught and the page is dropped. A silent truncation in a live endpoint is worse
than a detected one, and +0.9 points does not buy it.

What this argues for, if anyone returns to it: re-reading a page at a second resolution when the
first transcript looks thin, keeping the better one — not a new constant.

Two things worth banking from the same run. **Stage 1 at 1500 did not fail once on 101 unseen
pages** of P06690, which is a stronger endorsement than the hand-picked golden set could give.
And **stage 2's run-to-run drift is larger than it looks** — F12's non-determinism, quantified.
Three identical runs over the same cached transcripts, same model, same settings:

```
501/648   the stored baseline
499/648   -2   six fields differed: 1 gained, 3 lost, 2 wrong either way
441/648   +1   two fields differed (buyer constant off in this pair)
```

So roughly **six fields flap per run and the net swings ±3**. Anything under about five fields is
inside the noise and must not be reported as a result — which includes most of the single-field
regressions listed above, and leaves 2000px's +6 only barely outside it. A claim this small needs
repeated runs before it means anything.

### F22 — the model cannot repair Thai wording on its own; a list can

**2026-08-27.** Tested directly, on `sellerAddress` — the field with the most recoverable misses
and full gold coverage. Stage 2 was asked to correct implausible Thai, everything else held fixed:

```
baseline                77.3%  (501)     sellerAddress  6/33
A  address alone        77.2%  (500)     sellerAddress  5/33
B  address + transcript 77.5%  (502)     sellerAddress  7/33
```

**Arm A is the hypothesis in its pure form and it loses a field.** It changed four addresses,
helped none, and broke one that was already exactly right — `จังหวัดสุราษฎร์ธานี` → `จังหวัดสุราษฎร์ธาน`.
Arm B's single gain was not wording correction at all: it stripped a hotel's name and branch off
the front of an address, which is a deterministic guard's job.

**Neither arm fixed a single real place-name error.** `อ.พุนหิน` (should be พุนพิน), `อากาศหินัน`
(should be อาคารทีวัน), `อ.สีคิ้น` (should be สีคิ้ว) all came through untouched. The reason is the
whole point: these are not implausible Thai, they are plausible strings that happen not to be real
places. And a model told to fix odd-looking Thai cannot distinguish them from `จีจี ป่าตอง`,
`วิยะตาปิโตรเลียม` or `เคยู พลัส` — real names that are not words either. There are 99 string fields
currently exactly right to lose that way.

A list knows the difference; a model does not. **F15 stands: 6 for 6 for guards, 0 for 6 for
prompts.**

Left parked, not built, with the case list measured: 42 recoverable string misses, of which ~2 are
pure whitespace/punctuation, ~4 are a branch marker or English name wrongly appended to
`sellerName`, ~5 are administrative names one character off (a gazetteer of Thailand's provinces,
districts and subdistricts would reach these), and ~8 need a supplier master list FA has not been
asked for yet. Also found: `clr-p068 sellerBranch` gold reads `สขาที่`, a typo in the answer key —
the model's `สาขาที่` is correct.

### F23 — a ใบเสร็จ and its withholding certificate are one payment, and `merge.py` cannot join them

**2026-08-27.** Reported from the webapp side: a two-page document where only page 1 appeared.
The page was a ใบเสร็จ followed by its `หนังสือรับรองการหักภาษี ณ ที่จ่าย`, and the certificate's
withholding tax was being lost silently. Two independent causes, both ours:

- `has_money` drops the certificate. Its only amounts are a payment base and a tax, neither of
  which is a document total — so it reads as an ID-card copy. **Intermittently**, because the model
  files those two numbers somewhere different on every run, which is worse than a clean failure.
- `merge.py` refuses the join. `conflicts()` treats two different document numbers as proof of two
  different bills, but a certificate carries its own WHT number. **Zero of three real certificates
  repeated the invoice number**, so that signal never fires and the polarity is backwards: a
  differing number is expected here, not disqualifying.

Surveyed the whole of P06690 (123 pages, 101 never transcribed before) to get real evidence:

```
certificates              2   (pages 86 and 90)
หน้า x/y page markers     0
degenerate pages          0
```

Certificates are rare — but when one appears, money data disappears with no trace.

**`src/certlink.py` handles it, deterministically, with the model out of the loop.** Stage 2 reads
certificates too unreliably to build on: on p090 it lost the 140.49 entirely and the page was then
dropped for having no amount, and on DT05 it filed the seller's tax id under `buyerTaxId`. So the
numbers are read from the transcript, where the form's fixed legal shape makes them recoverable —
and **the arithmetic proves itself**: withholding tax is always a legal percentage of the base, so
the correct pair is the one whose ratio is 1/1.5/2/3/5/10/15%. Nothing else on the page qualifies.

**Attach requires two of four to agree and none to conflict**: counterparty tax id, base amount,
document date, seller name. One signal is never enough — a tax id matches every bill from that
vendor in the chunk (the owner's objection, and a real one), an amount matches any coincidence of
round numbers, a date matches half a clearing set. A conflict refuses outright however many others
agree, because a certificate whose base disagrees with the subtotal is not that bill's certificate.

The rule started as tax id **and** amount, both required, and **that version failed on the live
endpoint** — see below. Only `withholdingTax` and `withholdingTaxRate` cross over, because
everything else the certificate produced was wrong on all three samples. An unmatched certificate
is emitted as its own row rather than guessed at or dropped, so FA links it by hand.

**Three things only end-to-end testing found, each of which had broken it:**

1. **Cached transcripts are not the serving path.** The first "3 of 3" was measured on cached
   transcripts. Run through `serving/pipeline.run`, DT05's certificate page came back with **no
   13-digit number anywhere on it, five runs out of five** — while the amounts read perfectly
   every time. The tax-id-and-amount rule declined, and the endpoint returned two candidates.
   Hence four signals and a threshold rather than two mandatory ones.
2. **A form heading is not a seller name.** Stage 2 returns `ผู้ถูกหักภาษี ณ ที่จ่าย` as
   `sellerName` when it cannot find a real one. Compared literally, that conflicts with every
   bill and refuses everything.
3. **Exact name comparison turns OCR noise into a conflict.** The same vendor is
   `อินเตอร์เนชันแนล` on the certificate and `อินเตอร์เนชั่นแนล` on the receipt — one character —
   which refused a correct attachment on p086. Name comparison is now three-valued: near match
   agrees, clearly different conflicts, and the band between decides nothing.

Two things the testing changed. Tax ids are read from the **transcript as well as the field** —
DT05's seller id is printed plainly in the letterhead but `resolve_parties` left `sellerTaxId`
null, and a field-only match failed. And the mod-11 check digit earns its place: DT05 page 2
carries `0107685000030`, a phantom stage 1 invented, which fails the checksum and leaves exactly
one counterparty.

Verified 3 of 3 with the certificate actually attached — not merely present in the output, which
an earlier version of the fixture mistook for success, since an unattached certificate carries its
own tax on its own row. DT05 confirmed through `serving/pipeline.run` itself: 2 pages in, **1
candidate out**, `withholdingTax 48.0`, schema-valid. `eval/test_certlink.py` 37/37,
`test_merge.py` still 26/26, and the golden set provably untouched — **0 of 34 golden cases
classify as certificates**, so the path cannot execute there.

**Still open.** FA staples the invoice, the receipt and the customer copy together — p084/p085 are
one purchase, p087/p088/p089 another — so one purchase yields two or three candidate rows. The
certificate attaches to one of them correctly, but the duplicate rows are a separate, larger
problem. The owner's position, 2026-08-27: *"this is just fa from manually input all the data,
they should know which one have what information."*

### F24 — no bill in 177 pages states that it spans pages

**2026-08-27.** `merge.py` was built around `หน้า 1/2` markers and field agreement across adjacent
pages. Across 54 earlier transcripts and all 123 pages of P06690, **not one page carries a page
marker.** Its 26 fixtures still pass and it still folds duplicate copies of one purchase together,
which is real work — but the case it was designed for has never appeared in FA's documents.

Worth knowing before anyone spends more on it: the multi-page problem FA actually has is F23, and
it is a different shape.

### F11 original investigation — BLOCKER: typhoon-3b on Ollama is unreliable on the real multi-page input

**2026-08-10.** First run against `../บิลเงินสด_ร้านค้า_5ใบ.pdf` (5 pages, 4.1 MB — the input
shape FA will actually upload). Pages 3, 4 and 5 returned exactly `'@'*31` and nothing else.

The pages are fine. Page 3 rendered and inspected visually is a clean handwritten cash bill
(ร้านข้าวต้มโกยาว, total 1,960, date 13/07/69). Brightness and contrast statistics are
indistinguishable from pages 1–2, which succeed.

Things ruled out by measurement:

| Suspected cause | Result |
|---|---|
| `repeat_penalty` too high | fails identically at 1.0, 1.05, 1.1, 1.15, 1.2 |
| image too large / small | fails at 1800, 1500, 1300, 1100, 1024 |
| greyscale, border crop, LANCZOS vs direct render | no effect |
| `num_ctx` too small | server log: input 3158 tokens of 8192, `truncated = 0` |
| seed / sampling | identical output at seeds 42, 7, 99 |
| VRAM exhaustion | fails with 5.3 GB of 8.1 GB used and after a full unload |

**The results are not reproducible run to run**, which is the more serious half of this:

- page 1 at 1800px produced 769 chars, then 11365 chars, then `@@@` — same bytes, same settings
- page 2 produced 11240 chars and then 6562 chars on two identical consecutive calls, at
  `temperature=0` with a fixed seed. **That alone violates R5.**
- a fresh runner tends to answer the first one or two requests correctly and then degrade

Page 3 *did* transcribe correctly exactly once: from a 636×900 PNG that had been written to disk
and reloaded. Re-rendering the same size in memory did not reproduce it.

The server log shows prompt caching active (`cached n_tokens = 15, memory_seq_rm [15, end)`) with
2880 image tokens appended after a 15-token cached prefix. Cache reuse across requests carrying
*different images* is a plausible mechanism for the order-dependence, but page 3 also fails as the
very first request on a fresh runner (6/6), so that cannot be the whole story.

Ollama 0.32.6. The 7b model returns HTTP 500 on this input, almost certainly OOM at 16 GB on an
8 GB card.

**Retractions.** Two earlier conclusions were drawn from single-image tests and do not survive:
- "`repeat_penalty=1.2` is the fix" (F9) — it fixed `test2.png` and does nothing here
- the `repeat_penalty` 1.1-vs-1.2 comparison in F9, and any transcript-quality claim resting on it

The `repeat_penalty` plumbing fix in `../ocr_pipeline.py` is still correct and still needed — the
parameter genuinely was being dropped. Only the claim about which *value* to use is withdrawn.

**Next test, and it is decisive:** run the same pages through `transformers` directly, which
already works with CUDA in `../model.ipynb`. That bypasses Ollama entirely.
- pages transcribe correctly → the bug is Ollama's, and the serving stack must change (vLLM on
  WSL2, or llama.cpp directly). Phase 02's stack choice reopens.
- pages fail there too → it is the model, and stage 1 needs a different one. Architecture B
  survives but `03-model-bakeoff.md` reopens for stage 1.

Do not tune prompts or build the golden set against this stack until this is resolved. Any number
measured on a non-deterministic pipeline is meaningless.

### F5 — `suggestedCategoryId` contradicts the earlier assumption

Earlier understanding was that FA staff upload into an already-known category, so the model never
categorizes. The contract asks for a suggestion anyway. It is nullable — we ship `null` until
phase 05 — but we need their category ID list. Their sample uses `"P-4.1"`, which does not match
the GL codes on the FM-FA-05 form (`5-122-100`, `5-211-100`, …).

---

## 2. Phase map

Each phase has its own file. Do not start a phase before its inputs exist.

| Phase | File | Gate to exit |
|---|---|---|
| 00 | this file | Questions in §3 sent; answers recorded here |
| 01 | [`01-golden-set-and-harness.md`](01-golden-set-and-harness.md) | ≥ 40 scored cases across 4 strata; their TS scorer runs on our output — **scorer runs; 12 cases of 40.** The shortfall is now the main limit on what any further tuning can tell us: `clearingAmount` and `discount` are judged on 2 fields each, ใบรับเงิน on one case. |
| 02 | [`02-serving-spike.md`](02-serving-spike.md) | 20/20 responses validate against `bill-extraction.schema.json` |
| 03 | [`03-model-bakeoff.md`](03-model-bakeoff.md) | One model chosen with a stratified score table behind the choice |
| 04 | [`04-chunk-segmentation.md`](04-chunk-segmentation.md) | Correct candidate count + `chunkPageIndex` on a multi-page, multi-bill chunk |
| 05 | [`05-accuracy-push.md`](05-accuracy-push.md) | Best achievable stratified accuracy, honestly reported |
| 06 | [`06-production-api.md`](06-production-api.md) | Endpoint the colleague's adapter can call, with auth and error taxonomy |
| 07 | [`07-capacity.md`](07-capacity.md) | Measured laptop numbers + a server spec to procure |
| 08 | [`08-handover-package.md`](08-handover-package.md) | All six §1 deliverables shipped back |

```
01 golden set ─────┐
                   ├──> 03 bakeoff ──> 04 chunking ──> 05 accuracy ──┐
02 serving spike ──┘                                                 ├──> 08 handover
                                       06 api ──> 07 capacity ───────┘
```

01 and 02 are independent and can run in parallel. Nothing downstream of 03 is meaningful
without both.

### Where we actually are — 2026-08-19

Each phase file now opens with its own dated status block. In one table:

| Phase | State | What is actually left |
|---|---|---|
| 01 golden set | **working, gate half met** | 12 cases of 40. Below ~40 the score cannot tell tuning from noise |
| 02 serving | **solved except R5** | output is not byte-identical between runs |
| 03 bake-off | **closed** | 3b-vs-7b parked until server hardware exists |
| 04 chunking | **barely started** | no cross-page window, overlap or merge at all |
| 05 accuracy | **D4 delivered, ~62%** | waiting on FA: 79 of 82 category rules, and more cases |
| 06 API | **not started** | no endpoint. Now the critical path |
| 07 capacity | **not started** | per-page timings exist; the R15 gap is already visible |
| 08 handover | **contract half ready** | `handover/` can be sent today; the connection cannot |

**The honest summary:** the *model* is largely done and measured. What is missing is the
*service* — phases 04, 06 and 07 — plus two inputs only other people can supply: FA's category
rules, and more golden cases.

Two dependencies worth naming, because they are not in the diagram:

- **Accuracy work is now rate-limited by the golden set, not by ideas.** Further tuning against 12
  cases produces numbers that move by one field for reasons nobody can attribute (F12).
- **Phase 06 should settle Q7 (determinism) before it is built**, not after. R5 is a contract
  requirement we currently fail, and "deterministic for a pinned deployment" is a thing to agree
  with them rather than discover during integration.

---

## 3. Open questions — send these to the webapp team before phase 03

Copy this block. Answers get recorded in §4 below.

1. ~~**`regions` is `minItems: 1` and required, but §0 treats bounding boxes as optional.**~~
   **Closed 2026-08-11 — `regions` was removed from the contract entirely.** See F1.

2. **What exactly is a "chunk"?** Page count range, typical and max. R15 says ≤ 40 MB and
   300 pages/job — is a chunk always the whole job, or is the job split into smaller chunks
   before reaching us? This determines whether the model must hold 40 pages in context at once.

3. **Does one HTTP call receive the whole chunk, or one page at a time?** R4 says the model owns
   bill boundaries including bills spanning pages — that is only possible if we see the pages
   together.

4. ~~**Category code format.**~~ **Decided 2026-08-20: `5122100`, digits only, no dashes.** That
   is already how `data/category_rules.json` is keyed, so no data changed.

   The question is closed rather than merely answered, because the code no longer depends on the
   answer being right. `category_key()` in `../src/stage2_extract.py` strips dashes and reads
   only the leading code token, so `5122100`, `5-122-100` and `5-122-100 ค่าจ้างเหมา` all resolve
   to the same account. Normalising rather than agreeing was deliberate: the webapp is somebody
   else's code, a mismatch here fires no error, and "the rule silently never fired" is the
   hardest class of bug to notice from the outside.

5. ~~**Is the 95% gate on the overall number, or per stratum?**~~ **Answered from our side
   2026-08-18** — the gate is not treated as binding. See F14.

6. **How many Lao samples exist in the real corpus, as a share of volume?** Determines how much
   effort Lao justifies versus reporting it as a known limitation.

7. **Determinism (R5) vs. our stack.** Fixed seed + temperature 0 gives reproducibility on a
   single fixed serving config. If you rebuild the server with different batching, output can
   shift. Is "deterministic for a pinned deployment" acceptable, or do you need it stable
   across redeploys?

8. **Retention (R18)** — does "must not retain" forbid a short-lived on-disk temp file during
   PDF rasterization, or only persistent storage? Affects whether we can spill to disk under
   memory pressure.

---

## 4. Answers received

_(record answers here as they come back; date each one)_

| # | Answer | Date |
|---|---|---|
| 1 | **Moot — `regions` removed from the contract 2026-08-11.** No boxes, no placeholder. D5 answered. | 2026-08-11 |
| 2 | pending | |
| 3 | **The whole PDF arrives in one call.** Forces internal windowing — see F3a. | 2026-08-10 |
| 4 | **Closed 2026-08-20 — `5122100`, digits only.** `category_key()` normalises dashes and labels away, so either spelling works. | 2026-08-20 |
| 5 | **Answered by us, not them** — 95% is not binding; be as accurate as possible. See F14. | 2026-08-18 |
| 6 | **N/A — Lao descoped by us** (F6). Still needs their confirmation, since R2 names Lao. | 2026-08-10 |
| 7 | pending | |
| 8 | pending | |

**Also owed to FA, not the webapp team:** 79 of the 82 entries in `../data/category_rules.json`
still have `"rule": ""`, meaning every category but three uses the shared prompt. This is the
single biggest open input to accuracy and it is not something we can resolve ourselves.

One open question for FA as well: do they ever receive a receipt mixing VAT-able and VAT-exempt
items? That decides whether `vatExemptAmount` earns its place in the contract.

### F1a — what "optional" actually means in practice

The answer to Q1 is *capability-dependent*, which is reasonable, but it does not remove the
schema constraint in F1: `regions` is `minItems: 1` and required. So the two paths are unchanged:

- model grounds → real boxes → reviewer can click a field and see it highlighted
- model does not ground → **`{chunkPageIndex, 0, 0, 1000, 1000}` on every candidate**, purely to
  pass validation

Since boxes are now "nice if we can", the sensible move is to **weight grounding capability in the
phase 03 model choice**. If two models score the same on text accuracy and one emits usable boxes,
that one wins for free. Do not treat "optional" as "ignore".

### F3a — one call for the whole PDF

Confirmed: one HTTP request carries the entire PDF (≤ 40 MB). Consequences:

- **Internal windowing is mandatory, not optional.** A 40-page PDF does not fit in a 4B VLM's
  context on 8 GB. Phase 04's sliding window is now on the critical path.
- **R4 becomes satisfiable.** Because we see all pages, a bill spanning pages 2–3 is detectable —
  provided our window keeps those pages together. This is exactly what the 1-page window overlap
  is for.
- **Peak host RAM is a real risk.** 40 pages at 1390×1800 RGB is ~300 MB uncompressed before
  base64. Stream pages through the window; never materialize the whole document at once.
- Question 2 (chunk size range) still matters for sizing, but is no longer blocking.

---

## 5. The six things we owe them (§1 of their README)

| # | Deliverable | Phase | Status |
|---|---|---|---|
| D1 | Endpoint URL, auth method, one sample request/response pair | 06 | **sample pairs done** — six in `../handover/samples/`. Endpoint + auth not started. |
| D2 | Model name + version (logged on every extraction for audit) | 03 | **known** — `scb10x/typhoon-ocr1.5-3b` → `qwen3:4b`. Not yet logged per extraction. |
| D3 | Constrained-decoding file actually used (GBNF or JSON-Schema config) | 02 | **done** — `stage2_extract.reduced_schema()`, passed to Ollama as `format` and compiled to GBNF internally |
| D4 | Accuracy report from their scorer, split by language and handwriting | 05 | **done 2026-08-19** — F14. Their scorer, our transcripts, split by type and handwriting |
| D5 | Answer: bounding boxes yes/no | 03 | **answered: no** — `regions` left the contract |
| D6 | Real elapsed time on a 40 MB / 40-page chunk | 07 | not started. Laptop, single stream: ~15–80 s/page stage 1 plus ~15 s/page stage 2 |

---

## 6. Status log

| Date | Phase | Note |
|---|---|---|
| 2026-08-10 | 00 | Contract read. F1–F5 recorded. Questions drafted, not yet sent. |
| 2026-08-10 | 00 | Q1 + Q3 answered: boxes optional, whole PDF in one call. F1a, F3a added. |
| 2026-08-10 | 00 | Lao descoped → F6. Typhoon reinstated as primary candidate. |
| 2026-08-10 | 03 | Spot test run on 4 real receipts. **Architecture B decided** → F8. Stage 1 = typhoon-ocr1.5-3b. Stage 2 model still open. |
| 2026-08-10 | 02 | Stage-2 spike built (`../stage2_extract.py`). **8/8 responses valid against their real schema** — the architecture works end to end. Two bugs found → F9. |
| 2026-08-10 | 02 | Bug 1 fixed in `../ocr_pipeline.py`: switched to Ollama's native `/api/chat` so `repeat_penalty`, `num_ctx` and `seed` actually apply. `repeat_penalty=1.2` needed, not 1.1. |
| 2026-08-10 | 03 | **Stage 2 chosen: qwen3:4b, thinking off** → F10. typhoon2-8b rejected. Dates and amounts move to deterministic post-processing. |
| 2026-08-10 | 02 | F11 **resolved**: `target_dim` 1800 was breaking pages, plus prompt-cache contamination. 1500 works 10/10. `pipeline.ipynb` written (both stages, plain-language notes per cell). |
| 2026-08-10 | 01 | Harness scaffolding built: `../eval/harness.py`, `../data/golden/golden.json` (9 cases, TODO placeholders), `../data/golden/cases.md`. **Blocked on the answer key being typed in.** Node.js not installed — needed to run their scorer. |
| 2026-08-11 | 00 | **Contract trimmed by agreement:** `regions`, `evidence`, the three category keys and every `sourceRegions` dropped; `chunkPageIndex` added back. F1/F1a and Q1 close. `contract_schema()` derives the shape from their file at run time, so their next update flows through automatically. |
| 2026-08-18 | 05 | **Contract extended 18 → 29 fields** at FA's request: buyer block, `documentBookNumber`, `payeeType`, `paymentMethod`, `discount`, `vatExemptAmount`, `vatRate`, `withholdingTaxRate`. `clearingAmount` formula settled against FM-FA-05. |
| 2026-08-18 | 05 | 123-page real clearing set analysed — nine document types, including ใบรับเงิน (~35 rows) where the payee is an individual and the "tax ID" is a national ID. Drove `payeeType`. |
| 2026-08-18 | 05 | **F13:** `repeat_penalty` → 1.25, `collapse_empty_rows()` added. Biggest single accuracy gain of the project, and it was a serving parameter, not a prompt. |
| 2026-08-18 | 05 | **F15:** five deterministic guards replace prompt rules. Prompt bloat measured as actively harmful; form-specific rules moved to `category_rules.json`. |
| 2026-08-18 | 08 | **Handover package built** — `../handover/` (contract README + six worked sample responses). Ready to send. |
| 2026-08-19 | 01 | Answer key complete: 12 cases, 204 fields. Node.js 24 installed, their scorer running. |
| 2026-08-19 | 05 | **F14: accuracy measured for the first time — 61.3–61.8%**, structured 75.0% vs free text 33.8%. **F16:** confidence found not predictive, R13 at risk. Two harness measurement bugs fixed (nulls, amounts) — the harness now agrees with their scorer field for field. |
| 2026-08-19 | 00 | `../README.md` and this file brought back in line with the code. Both had drifted nine days: 18-field contract, `repeat_penalty=1.1`, "accuracy unmeasured". |
| 2026-08-27 | 05 | **F20: `FILL_BUYER_FROM_CONSTANT` found switched off** — the served endpoint had been answering at 67.9% rather than 77.3%. Set back to `True`. A diagnostic flag left on looks exactly like a working configuration; runs whose numbers get quoted must print their flags. |
| 2026-08-27 | 05 | **F21:** resolution re-tested now that `repeat_penalty` is 1.25 — F11's sweep was measured at 1.1 and its char counts were partly counting the loop. 2000px scores +6 fields but re-triggers the blank-row loop *undetectably*. `TARGET_DIM` stays 1500. 1800 still fails page 3 outright. |
| 2026-08-27 | 05 | **F22:** the "let the model fix odd Thai wording" idea tested against gold and **rejected** — it loses a field and fixes no real place-name error. F15 holds. Guard list measured and parked. |
| 2026-08-27 | 04 | **F23: `src/certlink.py`** — a ใบเสร็จ and its หนังสือรับรองการหักภาษี ณ ที่จ่าย folded into one row, deterministically, two of four signals must agree (tax id, base amount, date, seller name) and none may conflict; the withholding rate proves itself. Fixes withholding tax vanishing silently. 3/3 on real certificates, DT05 confirmed through `serving/pipeline.run` (2 pages in, 1 candidate out), `eval/test_certlink.py` 37/37, golden set untouched. **The first version passed on cached transcripts and failed on the live path** — end-to-end testing is not optional. |
| 2026-08-27 | 04 | **F24:** all 123 pages of P06690 transcribed (101 for the first time). **Zero page markers in 177 pages** — the spanning-bill case `merge.py` was built for has never appeared. Zero degenerate pages at 1500. |
| 2026-08-28 | 06 | **A deadline breach was reporting `503 MODEL_UNAVAILABLE` instead of `504 DEADLINE_EXCEEDED`** — the per-call timeout collapsed to 5s once the clock ran out, so httpx's `ReadTimeout` read as a backend fault. The caller retried a too-large chunk repeatedly, burning four minutes of GPU each time and 429-ing everything else. Fixed in `serving/pipeline.py`; the message now names the page reached. **~50s/page means ~4 pages per request** — the deadline is the symptom, throughput is the disease (phase 07). |

### F9 — two bugs found by the stage-2 spike, one per stage

**Bug 1 (stage 1, live in `../ocr_pipeline.py`): the repetition penalty is silently ignored.**

`ocr_page` passes `extra_body={"repetition_penalty": 1.1, "top_p": 0.6}`. `top_p` is a real
OpenAI parameter and gets through; **`repetition_penalty` is not, and Ollama's native name is
`repeat_penalty` inside `options`.** So it has never been applied. No error, no warning.

Measured on `test2.png`:

| | output | header block repeated |
|---|---|---|
| current behaviour | 731 chars | 2× |
| `repeat_penalty: 1.1` in `options` | 15576 chars | 1× |

Typhoon was looping on the vendor's header and stopping before it reached the line items and
totals — which is why both stage-2 models returned `null` for every amount on that receipt. The
extraction was never the problem there.

Not yet verified that the longer output is *correct* rather than merely longer — 15 k chars for a
delivery note is suspicious and may be a new degeneration on the form's empty table rows. Read it
before declaring this fixed.

**Bug 2 (schema): `\d` is unsupported in Ollama's grammar.** See `02-serving-spike.md` §3 for the
measured feature table. Their contract schema's date pattern uses `\d`, so **their file cannot be
fed to Ollama as-is.**

**The useful part:** these two bugs live in different stages and presented as the same symptom
(empty fields). The two-stage split is what made them separable — a single-pass model would have
shown one bad result with no way to tell which half was at fault.
