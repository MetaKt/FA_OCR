# FA_OCR — reading receipts for the FA clearing sheet

Reads scanned Thai receipts and produces the JSON the ADV Clear webapp needs, so FA does not have
to type every field by hand. Two AI models, both running locally — no receipt ever leaves the
machine.

```
receipt image  ──►  Stage 1  ──►  Thai text  ──►  Stage 2  ──►  JSON for the webapp
                  typhoon-ocr1.5-3b            qwen3:4b
                  reads the page               picks out the fields
```

Typhoon reads Thai handwriting well but can only produce plain text. A second model turns that text
into the 29 fields the contract requires.

---

## Where to start

| I want to... | Go to |
|---|---|
| Run the pipeline and see it work | **[`pipeline.ipynb`](pipeline.ipynb)** — run top to bottom |
| Measure accuracy | `pipeline.ipynb` section 9 — run **cell 2 first**, it puts `eval/` on the path |
| Understand the whole project plan | **[`plan/00-OVERVIEW.md`](plan/00-OVERVIEW.md)** |
| Edit the answer key | [`data/golden/cases.md`](data/golden/cases.md) then `data/golden/golden.json` |
| Send the contract to the webapp team | [`handover/README.md`](handover/README.md) — zip the whole folder |

Two things that trip up a fresh start: the notebook kernel must be the project **`.venv`** (the
system Python has no `jsonschema`), and **cell 2 must run after every kernel restart** or section 9
fails with `No module named harness`.

---

## Folder layout

```
FA_OCR/
├─ pipeline.ipynb        THE MAIN ONE. Both stages, with an explanation above every cell.
├─ model.ipynb           Earlier exploration and scratch work. Kept for reference.
│
├─ src/                  The code
│  ├─ ocr_pipeline.py      stage 1: PDF/image -> Thai text
│  ├─ stage2_extract.py    stage 2: Thai text -> contract JSON
│  └─ spot_test.py         compare several models side by side
│
├─ eval/                 Measuring accuracy
│  ├─ harness.py           runs everything and scores it
│  ├─ transcripts/         cached stage-1 output (generated)
│  └─ results/             files for the scorer (generated)
│
├─ data/
│  ├─ category_rules.json  extra stage-2 rules per expense category  <- edit this, no Python
│  ├─ samples/             the real receipts (never committed to git)
│  └─ golden/              the answer key + how to fill it in
│
├─ handover/             what the webapp team gets: the 29-field contract + 6 worked examples
│
│
└─ plan/                 The project plan, one file per phase
   ├─ 00-OVERVIEW.md        contract, decisions, findings, status  <- read this first
   ├─ 01-golden-set-and-harness.md
   ├─ 02-serving-spike.md
   ├─ 03-model-bakeoff.md
   ├─ 04-chunk-segmentation.md
   ├─ 05-accuracy-push.md
   ├─ 06-production-api.md
   ├─ 07-capacity.md
   └─ 08-handover-package.md
```

The code lives in `src/`, so notebooks start with `sys.path.insert(0, "src")`. That one line is why
`import ocr_pipeline` works from the project root.

---

## Setup

Everything is already installed. For reference:

```bash
ollama pull scb10x/typhoon-ocr1.5-3b
ollama pull qwen3:4b
```

Python packages are in `requirements.txt`, which says what each one is for. `typhoon-ocr` is used
only for `get_prompt("v1.5")` and pulls no torch — the `torch`, `bitsandbytes` and `datasets` in
`.venv` are left over from the fine-tuning experiments and are imported by nothing in `src/` or
`serving/`. Node.js 24 LTS is installed, needed only to run the webapp team's accuracy scorer.

### On a second machine

On a rented Linux GPU box, `serving/setup-vast.sh` does all of the below in one run — Ollama,
both model pulls, the venv, the 438 checks — and refuses to declare itself ready until the two
hand-carried files are in place:

```bash
./serving/setup-vast.sh /path/to/bill-extraction.schema.json
```

It writes `serving/env.sh`, that machine's half of the configuration. Source it before starting
the server.

`git clone` is not enough. Four things are deliberately not in the repo and are copied by hand:

| What | Where it goes | Why it is not committed |
|---|---|---|
| `bill-extraction.schema.json` | anywhere — point `$env:CONTRACT_SCHEMA` at it | their contract, replaced by hand; a committed copy is a fork that goes stale |
| `serving/.token` | `serving/.token` | a secret |
| `data/samples/` | `data/samples/` | real documents (R18) |
| `data/golden/golden.json` | `data/golden/golden.json` | real documents (R18) |

Only the first is needed to start the server — `serving/app.py` refuses to boot without it, rather
than failing inside validation after a minute of GPU. The other two are needed only to score.

On Linux the interpreter is `.venv/bin/python`, not `.venv/Scripts/python.exe`, and
`PYTHONIOENCODING=utf-8` is unnecessary. Nothing else in the tree is Windows-specific.

---

## Settings that matter

Two of these are non-obvious and were found the hard way:

| Setting | Value | Why |
|---|---|---|
| `TARGET_DIM` | **1500** | **Not 1800.** At exactly 1800 the model degenerates into `@@@@@@` on some scanned pages — and 1800 is what Typhoon's own model card recommends. |
| `repeat_penalty` | **1.25** | **Not 1.1.** Below ~1.2 the model falls into emitting `<tr><td>-</td>` rows for a form's empty ruled boxes and fills the whole context with them, so the totals at the bottom of the page are never transcribed. On `test2.png`: 1.1 gave 14251 chars in 80s with the discount and total missing; 1.25 gave 844 chars in 14s with both present. Raising `num_ctx` does not help — it just buys room for more dashes. Must go in Ollama's `options`; passing it as `repetition_penalty` through the OpenAI-compatible endpoint is silently ignored. |
| Stage 2 `think` | `False` | 6× faster with no loss on amounts. |

If pages come back as `@@@@@@`, run section 8 of `pipeline.ipynb`. Ollama reuses a prompt cache
between requests, so a long run can make one page's result depend on the page before it.

---

## Current status

Working: both stages end to end, output validates against the webapp team's real schema, and the
29-field contract is written up in [`handover/README.md`](handover/README.md) ready for the webapp
team.

### Accuracy — 77.3% (501 of 648 fields), measured 2026-08-20

The answer key is complete: **34 pages, 648 graded fields**, nine document types. The number comes
from the webapp team's own scorer, not from anything in this repo. Two runs of the same unchanged
code can differ by a field or two — see the last open item below — so a one-field move means
nothing.

```
2026-08-19    126/204   61.8%    12 cases
2026-08-20    439/648   67.7%    34 cases, no buyer guard
2026-08-20    501/648   77.3%    34 cases, buyer guard        +60
```

The jump is one change: **we are the buyer on every bill** — that is what makes a bill claimable —
so the buyer block is known before the page is read.
`FILL_BUYER_FROM_CONSTANT` in [`src/stage2_extract.py`](src/stage2_extract.py) lets the page decide
it, in both directions:

- page carries a TEAM fragment → all four buyer fields come from a constant, not from whatever
  survived OCR of a photocopy
- page names TEAM nowhere → all four are blanked. Needed because the prompt has to name TEAM for
  the model to recognise it, and the model then copies it onto pages where it does not appear:
  a motorway toll slip with no buyer came back with our name *and* our tax ID.

**Set it to `False` to see what the model actually transcribed.** That is the honest view when
judging whether stage 1 is improving, and it costs 60 fields.

Best and worst fields (n = 34 unless the field does not apply to every document):

```
currency, discount, paymentMethod           100%
withholdingTax                               97%
vatRate, withholdingTaxRate                  94%
originalTotal                                91%
payeeType                                    91%
buyerTaxId 88%, buyerName 85%, buyerBranch 79%, buyerAddress 74%
sellerBranch                                 81%
sellerTaxId, clearingAmount                  78%
vat                                          77%
documentDate                                 76%
documentBookNumber                           71%
amountBeforeVat                              68%
originalDocumentNumber                       54%
--------------------------------------------------------
sellerName                                   29%
sellerAddress                                18%
```

Printed 79.2%, handwritten 74.3%. Thai 77.3%, English 78.4% (n=37, too small to plan around).

**What is left is mostly not winnable by a better model.** Of the 147 remaining misses, **51 are
`sellerName` and `sellerAddress` alone** — free text, where the scorer gives zero for a
one-character slip (`หาญพันธ์` versus `นามพันธุ์` scores the same as a blank). FA corrects those by
eye in seconds, so the headline understates how much typing this saves.

Four model swaps were measured on 2026-08-20 and **all four lost** — a single-stage VLM, two
replacement stage-2 models, and a Thai-specialised one. The stage-2 candidates converted *less*
of the pool they could reach, not more. Full numbers in
[`plan/03-model-bakeoff.md`](plan/03-model-bakeoff.md); the short version is that the remaining
gap is not a model-choice problem, and deterministic guards are five for five where model and
prompt changes are nought for four.

Known open items — all tracked in [`plan/00-OVERVIEW.md`](plan/00-OVERVIEW.md):

- **79 of 82 expense categories have no rules yet.** `data/category_rules.json` is waiting on FA.
  Until those arrive, every category uses the shared prompt.
- **`confidence` does not predict correctness.** The 0.80–0.95 and 0.95–1.00 buckets score 65% and
  63% — the model says 0.95+ on nearly everything. Do not drive review-queue highlighting off it.
- Remaining `documentDate` and `originalDocumentNumber` misses are **stage 1 misreading digits**
  (`13/07/69` transcribed as `13/02/69`), so no amount of stage-2 prompting or Python can fix them.
- Seller names sometimes pick a product brand, or TEAM's own สำนักงานใหญ่, instead of the shop.
  `resolve_parties()` in `stage2_extract.py` handles the TEAM case; the brand case is open.
- `clearingAmount` and `discount` are graded on **2 fields each**, so their percentages are noise.
  More golden cases would tell us more than more tuning — ใบรับเงิน especially, which is ~35 rows
  of a real clearing set and has one case covering it.
- Output is not byte-identical between runs, which the contract requires. Ollama reuses a prompt
  cache between requests, so the score moves by a field or two run to run even with `seed=42`.
  Treat any single-field change as noise.

The contract was trimmed on 2026-08-11 by agreement with the webapp team: `regions`, `evidence`,
the three category fields and every `sourceRegions` were dropped, and `chunkPageIndex` was added
back on its own. `stage2_extract.contract_schema()` derives that shape from their file at run time,
so their next contract update flows through automatically.
