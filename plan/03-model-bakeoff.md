# Phase 03 — Model Bake-off

**Goal:** pick one model, with a stratified score table behind the choice.
**Inputs:** phase 01 golden set + harness, phase 02 constrained-decoding rig.
**Exit gate:** one model chosen; deliverables **D2** (model name + version) and **D5** (bounding
box answer) ready to send.

> ### Status 2026-08-19 — CLOSED, both gates met
>
> **Chosen:** `scb10x/typhoon-ocr1.5-3b` → `qwen3:4b` (`think: false`), Architecture B. Typhoon beat
> Qwen3-VL-4B decisively on Thai; typhoon2-8b was rejected as stage 2 for returning `null` on the
> total three times in four and inventing a seller name. Overview F8 and F10 carry the numbers.
>
> **D2 answered:** the two model tags above. Not yet logged per extraction for audit — that lands
> with the API in phase 06.
>
> **D5 answered: no bounding boxes.** The question dissolved rather than being decided — `regions`
> left the contract on 2026-08-11. §1's Branch A/B/C fork below no longer turns on *grounding* —
> no model needs to be judged on boxes any more. The single-stage-versus-two-stage question the
> fork also poses is still live; see the 2026-08-20 update.
>
> ### Update 2026-08-20 — Architecture A retested and lost again. **Branch stays open.**
>
> `qwen3.5:9b` (6.6 GB, text+image, 256K ctx) was run single-stage, image → JSON, on the same
> twelve golden cases. The experiment folder was deleted once the finding was recorded; what
> follows is the record.
>
> ```
> single-stage  qwen3.5:9b     117/204  (57.4%)
> two-stage     baseline       126/204  (61.8%)
> ```
>
> **The hypothesis it was built to test:** four fifths of our misses (61 of 78) are values that
> never reach the transcript, so a model reading the pixels directly should recover them. It did
> the opposite — buyer fields went **15/48 → 12/48**, `buyerAddress` 2/12 → 0/12. It was worse at
> precisely the thing it was meant to fix, which kills the mechanism rather than the tuning.
>
> Three findings that outlive the experiment:
>
> 1. **Script contamination.** qwen3.5 injects Chinese and Cyrillic into Thai — `วังพุดตาล` came
>    back as `วังпутตาล Wang Put Tan 芙蓉宫`, and a Thai receipt's transcript carried `現兌單`,
>    `数量`, `收银人`. Typhoon has never done this. Characteristic of a Chinese-centric base and
>    not reachable by prompting. **Check for this in any future VLM candidate.**
> 2. **Reading the page and filling the contract are separate skills.** On `test2` it transcribed
>    `600`, `120`, `480` correctly and still returned an empty `billCandidates`, losing all 15
>    fields. Collapsing the stages does not make the extraction half free.
> 3. **Latency roughly triples** — 44–60 s/page against ~17 s for the pair. On R15's 40-page
>    chunks that loses even a tie.
>
> **Why this stays open rather than closed.** Both attempts were against the Qwen line, and the
> useful number is that the union of the two pipelines is **144/204 (70.6%)** — they fail on
> *different* fields, 18 that only the VLM gets against 27 that only the baseline gets. That gap
> is evidence a single-stage model can read things ours cannot, not evidence the architecture is
> wrong. A Thai-specialised grounded VLM, or a non-Chinese-base one, is still worth a day when
> one appears. The bar to beat is now **77.3% (501/648 on the full 34-case key)**, not 61.8% —
> and a rerun must be scored on all 34 cases, not the 12 this experiment used.
>
> **Cost of the retest: about two hours.** Cheap enough to repeat annually.
>
> ### Update 2026-08-20 (2) — stage-2 bake-off on the full 34-case key. Incumbent holds.
>
> Run after the buyer guard, when stage 2's share of the misses had risen from 10/78 to 39/147 and
> a swap was worth measuring again. Transcripts were cached, so every candidate read identical
> text and only the model tag changed.
>
> ```
> model                        score       %   s/case   fixable left   bills
> qwen3:4b   (incumbent)     501/648   77.3%     16.4             39      34
> openthai/openthai-1.5-7b   484/648   74.7%     17.9             46      35
> qwen3.5:4b                 480/648   74.1%     17.6             67      33
> ```
>
> `qwen3:4b` reproduced its baseline exactly, so none of these gaps is drift.
>
> **F17 — stage 2 is not a language task, and Thai tuning does not help it.** The hypothesis was
> that a Thai-specialised model would assign fields better from the same Thai transcript. The
> field that predicts most directly is `sellerName`:
>
> ```
> sellerName      qwen3:4b 10/34    openthai 10/34    qwen3.5:4b 8/34
> sellerAddress   qwen3:4b  6/33    openthai  4/33    qwen3.5:4b 6/33
> ```
>
> Identical, then worse, from a model fine-tuned on 2M+ Thai instruction pairs. The transcript is
> *already* Thai text; stage 2 copies values into a constrained schema and leaves the rest null —
> structure-following and restraint, not comprehension. Chat and RAG tuning optimise helpfulness,
> which fights "return null when the page does not say it": **OpenThai returned 35 bills for 34
> pages**, splitting one page and putting a phantom row in the ledger.
>
> **F18 — newer generation is not better on this task either.** `qwen3.5:4b` reads *stated* values
> better (`vat` 26→30, `vatRate` 32→34, `withholdingTaxRate` 32→34) and produces *derived* ones far
> worse (`clearingAmount` 18→**4**, `originalTotal` 31→23, `discount` 22→19). It also lost a page
> entirely. So "use the latest model" is falsified here independently of the Thai question — which
> is exactly why the control was in the line-up.
>
> **The column that settled it: `fixable left`** — misses where the answer was in the transcript
> the model was handed. 39 / 46 / 67. Both candidates converted *less* of the only pool a stage-2
> swap can draw from, so neither is a good model held back by a bad transcript.
>
> **Consequence:** the 39 recoverable fields are real but are not reachable by swapping the model.
> Deterministic guards are the next thing to try on them — five for five so far (F15).
>
> ### Attempted but NOT tested: `fredrezones55/chandra-ocr-2` (stage 1)
>
> 5.8 GB OCR VLM on a Qwen3.5-4B base, 262K ctx. Would have competed for the 68 stage-1 misses,
> the largest remaining block. **Abandoned mid-run, no verdict — it returned an empty string for
> all 34 pages at ~81 s each.** Almost certainly our harness, not the model: `ollama show` lists
> `thinking` among its capabilities and our `ocr_page` does not send `think: false`, so the output
> may be landing in `message.thinking` while we read only `message.content`. Its card also says it
> will "OCR unprompted" given just the image, so our custom prompt may be fighting the fine-tune
> the way Typhoon's baked-in prompt does (F2).
>
> **Still open, and still the biggest prize.** Whoever retries it: test one page first, print the
> whole response body rather than `message.content`, and try both `think: false` and no prompt at
> all. Check the transcripts for Chinese and Cyrillic before trusting any score — its base family
> contaminated Thai on these exact pages (see the 2026-08-20 update above).
>
> **One item deliberately left open:** typhoon 3b vs 7b. The 7b is a 16 GB model on an 8 GB card,
> so it runs mostly on CPU — minutes per page. Not worth resolving until server hardware exists.
>
> **A later fine-tune attempt also belongs here.** 2026-08-18: a DeepSeek-OCR Thai adapter was
> tried via unsloth and **lost to Typhoon**; the working folders were deleted 2026-08-19. That is
> the phase 05 fine-tune go/no-go answered with evidence — see that file.
>
> ### Update 2026-08-26 — `qwen3.8:27b`, 3-case pilot. Rejected. Folder and model deleted.
>
> 27B multimodal, 18 GB + 931 MB projector ≈ **19 GB against 8 GB of VRAM**. Tested as stage 2 and
> as stage 1; the single-stage arm was never run. Three golden cases (`5bills-p3`, `test2`,
> `clr-p012`), 51 gold fields — a pilot to price the full run, deliberately not a verdict on
> accuracy.
>
> **It does not load unattended.** Ollama's automatic split dies with `CUDA error: out of memory`
> during startup — with 7.4 GB of the 8 GB free — never reaching its own projector-on-CPU fallback.
> `options.num_gpu` has to be pinned by hand. Measured, 111 tokens per rung:
>
> ```
> num_gpu=40   2.15 tok/s   loads, but past the card and thrashing
> num_gpu=36, 32, 30        crash: 0xc0000409 stack buffer overrun
> num_gpu=28   4.31 tok/s   best
> num_gpu=20, 12            crash: 0xc0000409
> num_gpu=0    3.11 tok/s   pure CPU, the floor
> ```
>
> Most neighbouring values crash the runner with a *stack overrun* rather than a memory error —
> an Ollama bug, not a limit of the machine. Note the GPU is only buying 39% over pure CPU: the
> constraint is memory **bandwidth**, not capacity. 23.4 GB of system RAM, of which the model needs
> ~10 GB, so more RAM would change nothing.
>
> **F19 — stage 2 ignores model size. Three for three, and it should now be treated as settled.**
>
> ```
> stage 2, same cached transcripts, think=False both      fields    s/case
> qwen3:4b      (incumbent, 2.5 GB)                      35/51  69%    18.8
> qwen3.8:27b   (17 GB, 6x the parameters)               35/51  69%   373.7
> ```
>
> **Identical totals**, and near-identical case by case (9/8/18 against 10/7/18). Twenty times
> slower for the same 35 fields; 11 min against 212 min extrapolated over 34 cases. Being slow did
> not make it less accurate — better hardware would make this model *fast at tying*.
>
> With F17 (a 7B Thai fine-tune: identical `sellerName`, 10/34) and F18 (a newer 4B: worse), that
> is three independent attempts across size, language-tuning and generation, all landing on the
> same fields. Stage 2 copies values into a constrained schema and returns null otherwise; nothing
> about that is capacity-limited. **Do not spend another evening swapping the stage-2 tag.** The 39
> recoverable fields are a guard problem (F15), not a model problem.
>
> **The stage-1 arm is inconclusive, and the flaw was ours.** `qwen3.8` as stage 1 scored 25/51
> against the baseline's 35/51 — but `test2` contributed 0/15 by falling into a repetition loop:
> one real line item, then ~200 blank `<tr>` rows, truncated before it reached the totals. That is
> exactly F13, and the pilot never set `repeat_penalty`, so it ran at Ollama's default 1.1 instead
> of the 1.25 this page class requires. The other two pages were clean and competitive:
>
> ```
>                qwen3.8    typhoon    fields (baseline)
> 5bills-p3       2610c      1084c      8/15  (9/15)
> clr-p012        4308c      2837c     17/21  (18/21)
> test2            886c       986c      0/15  (8/15)   <- the loop
> ```
>
> So 25/36 against 27/36 on the two valid pages. **"A big general VLM reads Thai bills badly" is
> NOT supported** — on this evidence it is roughly Typhoon's equal at reading, using a plain
> instruction rather than a fine-tuned prompt. `clr-p012` also came back wrapped in a ```` ```html ````
> fence, which nothing strips and which was fed straight into stage 2.
>
> **Why it was still rejected without a fair rerun:** ~23 min/page against the 240 s serving
> deadline. No accuracy result could make it shippable on this hardware, so a corrected number
> would have changed the record and not the decision.
>
> **What stays open.** Stage 1 is still the largest miss bucket (68 of 147, 46%) and Typhoon is
> still the only stage-1 model ever properly measured on it. The lesson to carry forward is *not*
> "big VLMs don't work" but "we have not yet tested a **fast** alternative stage 1". Whoever picks
> this up: set `repeat_penalty=1.25`, forbid markdown fences in the prompt, and screen candidates on
> the ~10 GB-and-under shelf so they fit the card.

### Update 2026-09-01 — `pielee/qwen3-4b-thinking-2507_q8`, 3-case pilot. Rejected. Folder and model deleted.

> Sixth stage-2 swap. Same shape as F19: cached stage-1 transcripts, so both arms read **identical**
> text and any difference is stage 2's alone. Scored by their TypeScript scorer, 48 fields.
>
> ```
> arm                                        score          s/case    34-case
> qwen3:4b        think=False  (incumbent)   33/48  68.8%     17.5     10 min
> candidate       think=False                33/48  68.8%     35.2     20 min
> candidate       thinking on                24/48  50.0%    535.8    304 min
> ```
>
> **F20 — the tie is not coincidental, and the field-level diff is the real result.** At
> `think=False` the candidate's output was **byte-identical to the incumbent's on 47 of the 48
> fields**. The single disagreement was one both got wrong:
>
> ```
> test2 sellerName   gold  เอ็ม ดับบลิว อิเล็กทรอนิกส์
>                    base  MWELEC ELECTRONICS CO., LTD.
>                    cand  MWELEC
> ```
>
> Two models that merely score the same can still differ everywhere; two models that emit the same
> bytes are running the same procedure. Under a JSON grammar, on text that is already extracted,
> stage 2 is copy-or-null — and there is very little room in that for a better model to be better.
> The candidate charges 2× the time for the same answer.
>
> **Thinking is a configuration failure, not a model failure, and it is now 2 for 2.** With thinking
> on, the candidate dropped to 50.0% and lost `test3` outright — zero bill candidates, so all 15 of
> that case's fields missed. The incumbent, run with thinking on by accident during the F19 pilot,
> collapsed the same way: 47% against its own 69%. Two different models, same behaviour. **Thinking
> actively hurts structured extraction under a constrained grammar.** `STAGE2_OPTIONS = {"think":
> False}` in `eval/harness.py` is a load-bearing setting, not a preference — do not let a future
> experiment quietly drop it. At 536 s/case for stage 2 alone against a 240 s deadline for the whole
> request, this arm was unshippable regardless of accuracy.
>
> **Scoreboard.** Stage-2 model swaps are now **0 for 6** — F17 (7B Thai fine-tune, tie), F18 (newer
> 4B, worse), F19 (27B, identical), F20 (thinking 4B, tie or much worse). Deterministic guards are
> **6 for 6**. The asymmetry is no longer arguable.
>
> **Honest limits of this run.** Three cases, not 34, and the incumbent's own full-set number
> (77.3%) was not re-measured here. The 47/48 identical-output finding is what carries the weight,
> not the 68.8%; a full run was offered and judged not worth 20 minutes to confirm a tie.

### Update 2026-09-01 — `LisyNeko/qwen3.8-4b-coder`, 3-case pilot. Rejected. Folder and model deleted.

> Seventh stage-2 swap, same day as F20 and the same method: cached transcripts, both arms on
> identical text, `think=False`, 48 fields, their scorer.
>
> ```
> arm                                    score          s/case
> qwen3:4b        (incumbent, 2.5 GB)    33/48  68.8%     27.6
> qwen3.8-4b-coder (2.5 GB, code-tuned)  33/48  68.8%     42.2
> ```
>
> Identical overall **and in every field-level bucket** the scorer prints. 47 of 48 scored fields
> byte-identical; the lone difference is the same wrong answer with a newline in it:
>
> ```
> test2 sellerAddress   gold  บ้านหม้อพลาซ่า ชั้น 1 ล็อค W117
>                       base  87/4-6, Soi 59, Taphan Hin Road, Mitraphap ...
>                       cand  87/4-6, Soi 59, Taphan Hin Road,\nMitraphap ...
> ```
>
> **F21 — the code-tuning hypothesis was testable and is now falsified, for a reason worth keeping.**
> The idea was that stage 2's real job is emitting a constrained JSON object, which is nearer to a
> coding task than to the Thai-comprehension task F17 tested — a genuinely different hypothesis, not
> F17-F20 again. It made no difference because **the incumbent was never failing at JSON**: every
> response in every arm of every one of these bake-offs has been schema-valid. Constrained decoding
> already guarantees the structure, so there was no defect there for a code-tuned model to repair.
> Any future candidate justified by "better at structured output" is answering a question that
> `format` already answered.
>
> Timing note: the baseline measured 27.6 s/case here against 17.5 s/case in the F20 run a few hours
> earlier. Machine noise between sessions — both arms here ran back to back, so the 1.5× ratio is
> sound even though the absolute numbers drift. Do not compare s/case across these updates.
>
> **Scoreboard: stage-2 model swaps 0 for 7; deterministic guards 6 for 6.** The last three
> candidates — 27B, thinking 4B, coder 4B — each produced output ~47/48 identical to a 2.5 GB model
> chosen three weeks earlier. Not similar scores: the same bytes. Stage 2 is copy-or-null under a
> grammar, and model choice does not move it.
>
> **Recommendation to whoever reads this next: stop testing stage-2 models.** The 39
> stage-2-recoverable fields are a guard problem (F15). The 68 stage-1 misses — 46% of all misses,
> and `sellerName` + `sellerAddress` alone are 51 of 147 — are where an untested model could still
> help, and exactly one stage-1 model has ever been measured. Screen those at ~10 GB and under, set
> `repeat_penalty=1.25`, and forbid markdown fences.

---

## 1. The fork this phase depends on

**RESOLVED 2026-08-10:** the webapp team says bounding boxes are **optional, depending on whether
our model can do it**. See `00-OVERVIEW.md` F1a.

So this is no longer a branch we have to pick blind — it is a **tie-breaker**. Score every model
on text accuracy, and separately record whether it grounds. If two models are close on accuracy,
the one that emits usable boxes wins, because the reviewer gets click-to-highlight for free. If
the winner does not ground, we emit `{0,0,1000,1000}` placeholders to stay schema-valid and say so
plainly in the handover.

Practically: **prefer Branch A, accept Branch B, keep Branch C on the shelf.**

The three paths, for reference:

### Branch A — real bounding boxes required

Only natively-grounded VLMs qualify. A model that emits Markdown cannot be rescued cheaply.
Single-stage: image → grounded JSON.

### Branch B — whole-page placeholder accepted

The Typhoon two-stage path stays viable: Typhoon transcribes → text LLM extracts with constrained
decoding → every candidate gets `{chunkPageIndex, 0, 0, 1000, 1000}`. Cheaper, uses work already
done, and gives up click-to-locate.

### Branch C — hybrid (only if A is required and no grounded VLM is accurate enough)

Extract with the best text-accuracy model, then recover coordinates separately: run a text
detector (PaddleOCR, Surya, docTR) to get word-level boxes, then match each extracted string back
onto the page to derive a region. Costs a third component, adds a failure mode (string appears
twice, or the extracted value was normalized and no longer matches the page text), and roughly
doubles inference time. Keep as a fallback, not a plan.

**Run the bake-off so it answers both branches at once**: score text accuracy for everyone, and
separately record whether each model can produce usable boxes. Then the branch decision picks the
winner from an already-measured table rather than triggering new work.

---

## 2. Candidates

### Two architectures, not just a list of models

Every candidate below belongs to one of two shapes. Test one model from each before going deep.

**Architecture A — one general VLM: image → grounded JSON.**
One model, one call, one prompt. Boxes come free if it grounds. Fewest moving parts, so the
fastest path to a real number. Risk: a 4B model is being asked to read Thai handwriting, decide
bill boundaries, structure 22 fields, and localize them — all at once. Small models tend to drop
one of those jobs, usually grounding accuracy.

**Architecture B — specialist OCR → text LLM.**
Stage 1 is a document model that returns text *plus* layout boxes. Stage 2 is a text-only LLM
that maps that into the schema with constrained decoding. Each stage does one job well, boxes come
from the stage that is actually trained for them, and constrained decoding is easier because no
vision tokens compete for context. Cost: two models sharing 8 GB, roughly double the latency, and
stage 2 cannot see the page — so anything the transcript loses is gone.

This is the shape Typhoon would have fit into. The difference now is that we pick a stage-1 model
that **also emits boxes**, which Typhoon does not.

### Hardware filter

**8 GB VRAM.** Severe: roughly ~4B at bf16, or ~8B at 4-bit, leaving room for image tokens
(a 1800px page is not cheap) and KV cache. Since the whole PDF now arrives in one call
(`00-OVERVIEW.md` F3a), context headroom matters more than it did — a model with a large context
window lets us use bigger windows and stitch less.

Revised 2026-08-10: Lao descoped (`00-OVERVIEW.md` F6), so the ranking changed. Typhoon moves from
baseline to **primary**, because it is the only candidate purpose-built for Thai.

| Model | Params | Arch | Grounding? | Thai | Notes |
|---|---|---|---|---|---|
| **typhoon-ocr1.5-3b / -7b** | 3B / 7B | **B** stage 1 | **no** | purpose-built | Fine-tuned on Thai documents by SCB10x. Strongest prior for Thai text. Already installed. Needs a stage-2 text LLM. **Check the model card for handwriting claims** — this was never verified. |
| **Qwen3-VL-4B-Instruct** | 4B | A | native | **unverified — see F7** | Best Architecture A candidate: one model, boxes free, large context. But Typhoon exists *because* base Qwen was judged insufficient on Thai. Do not assume. |
| **Qwen3-VL-8B-Instruct** | 8B | A | native | unverified | 4-bit only, tight in 8 GB. Better if it fits. |
| **Qwen2.5-VL-7B-Instruct** | 7B | A | native | unverified | The base Typhoon was built from — so it is the **control**: if Typhoon beats it on Thai, that quantifies what the Thai fine-tune bought, and tells us whether a Qwen3-VL generational jump is plausibly enough. |
| **dots.ocr** | 1.7B | **B** stage 1 | native, layout-first | unverified | Purpose-built layout+OCR emitting text **and** boxes as JSON. The stage-1 slot Typhoon fills, except it grounds. Tiny footprint. |
| **PaddleOCR-VL** | 0.9B | **B** stage 1 | native layout | unverified | Same role, even smaller |
| **InternVL3.5-4B / -8B** | 4/8B | A | native | unverified | Strong general OCR benchmarks; no Thai specialization |

**Also search for other Thai-specialized VLMs before settling.** Typhoon is the one we know, but
Thai research groups (NECTEC, OpenThaiGPT, and others) have released multimodal models. Spend an
hour looking, and check licences — some Thai releases are research-only, which would rule them out
for a company deployment. Verify anything found actually exists and loads before adding it here.

### Stage-2 text models (Architecture B)

Reads stage 1's transcript and emits the schema under constrained decoding. Needs Thai
comprehension, not Thai OCR.

- Qwen3-4B / Qwen3-8B (4-bit)
- **Typhoon2-Qwen2.5-7B** — Thai-tuned text LLM from the same lab as the OCR model
- Gemma 3 4B

Sequential loading is acceptable in the bake-off; it will hurt in phase 07.

### 3a. Settle the Thai handwriting question first — one hour, before anything else

F7 says the Qwen-on-Thai assumption is untested. This is the cheapest possible experiment and it
decides the architecture, so run it before building any harness.

1. Pick **10 receipts** from `data/golden/source/`: 6 handwritten Thai, 4 printed Thai.
2. Ask each candidate for **plain transcription only** — no JSON, no schema, no extraction. We are
   measuring reading ability, nothing else. (Typhoon must use its own official v1.5 prompt; it
   rejects substitutes — `00-OVERVIEW.md` F2.)
3. Read the output **against the image, character by character**, on the amounts and the seller
   name. Do not skim. The failures that matter are ก/ถ and ด/ค/ต confusions and dropped tone marks,
   and they are invisible unless you look for them.
4. Score crudely: for each receipt, is the total amount exactly right? Is the seller name exactly
   right? That is 20 yes/no answers per model — enough to separate "usable" from "not".

Outcome decides the architecture:

| Result | Do this |
|---|---|
| Typhoon clearly best on handwriting | Architecture B. Typhoon + text LLM. Whole-page box placeholders, or add a detector later for boxes. |
| Qwen3-VL comparable to Typhoon | Architecture A. One model, boxes free, far simpler pipeline. Prefer this when it is close. |
| Both weak on handwriting | Stop and reassess. This is a project-level finding, not a model-selection one — it means the handwriting stratum needs the Gemini number for comparison, and possibly a fine-tune (phase 05 §7) is unavoidable. Tell the webapp team early. |

Record the raw transcriptions, not just the scores. They are the evidence for phase 05's error
analysis, and re-running inference to get them back is wasted time.

**Reference ceiling (not a candidate):** the webapp team's system already runs Gemini through this
exact contract. Their Gemini accuracy number is the most useful benchmark we could possibly have —
**ask them for it.** It tells us whether 95% is a demonstrated bar or an aspiration, and it tells
us how much accuracy self-hosting costs. Do not run their documents through Gemini ourselves from
this laptop; that is their data agreement to invoke, not ours.

### Stage-two text models (Branch B / hybrid only)

The extraction LLM that reads Typhoon's Markdown. Needs Thai comprehension and must run
*alongside* the OCR model or sequentially in the same 8 GB.

- Qwen3-4B / Qwen3-8B (4-bit)
- Typhoon2-Qwen2.5-7B (Thai-tuned text LLM from the same lab)
- Gemma 3 4B

Sequential loading is fine for the bake-off; it will hurt in phase 07.

---

## 3. Ruthless prefilter — do this before scoring anything

Loading and scoring eight models is days of work. Most will fail on something basic. Spend one
hour per model on a three-test gate first:

1. **Does it load in 8 GB and produce output on one page?** If it OOMs at 4-bit, drop it.
2. **Lao smoke test.** One Lao receipt, ask for a plain transcription. If the output is
   nonsense or transliterated Thai, the model fails R2 and only continues as a Thai-only
   comparison point, clearly labelled.
3. **Grounding smoke test.** Ask for a bounding box around the seller name on one page. Then
   **draw the box on the image and look at it.** A model can emit perfectly-formatted coordinates
   that point at nothing — this is the single most common way grounding claims fail, and it is
   invisible if you only inspect the JSON.

Record all three in the table below. Only survivors go to full scoring.

| Model | Loads in 8 GB | Lao readable | Box lands on target | → full scoring |
|---|---|---|---|---|
| | | | | |

---

## 4. Coordinate convention — verify per model, do not assume

R7 wants integers **0–1000 normalized to the page**. Different model families use different
native conventions:

- some emit `[0, 1000)` normalized (convert: none needed)
- some emit **absolute pixel** coordinates (convert: `round(x * 1000 / image_width)`)
- some emit `[0, 1]` floats (convert: `round(x * 1000)`)

The conversion is trivial arithmetic **once you know which one it is**, and catastrophic if you
guess wrong — pixel coordinates on a 1390px-wide page would clamp against the 1000 maximum and
land in the wrong place, while still being schema-valid integers.

Determine empirically per model, via the draw-the-box test in §3. Write the convention and the
conversion into the per-model notes. Also note whether the convention is relative to the
*original* page or the *resized* image we sent — if we downscale to 1800px (as `ocr_pipeline.py`
does), normalized coordinates are unaffected but pixel coordinates are relative to the resized
image, not the source PDF.

---

## 5. Scoring protocol

Same golden set, same prompt version, same decoding config for every model. Only the model
changes. Any deviation gets written down next to the number.

For each survivor produce:

```
model: <exact tag + quantization>
prompt: prompts/extract_v1.txt
decoding: <backend + guided_json/GBNF>

overall            xx.x%  (n/N)
by language        th / en / lo
by documentType    tax_invoice / receipt / cash_bill / ...
by handwriting     true / false
by field           documentDate / sellerName / ...

schema valid       n/N
candidate count    n/N correct
boxes              n/N present   |  m/N verified on-target (sampled)
confidence spread  min / p50 / max
latency            s/page, single stream, laptop
VRAM peak          GB
```

The latency and VRAM lines are inputs to phase 07 — capture them now while each model is loaded
rather than reloading everything later.

**Never report a single overall number without the strata** (§6 of their README says this
explicitly). A model at 98% overall and 60% on Lao handwriting is a worse choice than one at 94%
that is even, and the average hides that.

---

## 6. Resolve the 3B-vs-7B question

An earlier informal impression was that typhoon-ocr1.5-**3b** read pages better than the **7b** on
this laptop. That was never explained and it matters — if there is a systematic cause it will
affect every model we quantize.

Candidate explanations, in order of likelihood:

1. **Quantization damage.** The 7b at Q4 may be more degraded than the 3b at Q4, or the Ollama
   tags differ in quantization level. Check `ollama show` on both.
2. **VRAM spill.** The 7b may not fit in 8 GB and is partially offloaded to CPU — slower, and if
   layers are dropped or context is truncated, worse output.
3. **Context truncation.** A larger model with the same VRAM budget gets a smaller effective
   context, truncating a long page.
4. **Sampling noise.** The comparison may have been run before `temperature=0` was set, in which
   case it measured nothing. This is the most likely and cheapest to rule out.

Rerun the comparison at `temperature=0` on 10 fixed pages before spending time on the others.

---

## 7. Choosing

Rank by, in order:

1. **Schema validity** — below 100% is disqualifying; an invalid response costs the whole chunk
2. **Accuracy on the worst stratum** — not the average
3. **Bounding boxes** — if the answer to question 1 is Branch A, this is also disqualifying
4. **Accuracy overall**
5. **Latency and VRAM** — phase 07 will care a lot; a model that is 2% better and 3× slower is
   probably the wrong trade at 300 pages a job

Write the choice and the reasoning into `00-OVERVIEW.md` §6 status log, with the score table
saved to `eval/results/bakeoff-YYYY-MM-DD.md`. If the answer to question 1 arrives after the
bake-off, the table should already contain enough to decide without rerunning anything.

---

## 8. Task list

- [ ] Ask the webapp team for their Gemini accuracy number on this contract — still worth having
      as a reference point, now that we have our own number to compare against
- [ ] Rerun typhoon 3b vs 7b at `temperature=0` (§6) — **parked**, 7b does not fit in 8 GB VRAM;
      revisit on server hardware
- [x] Prefilter applied — Qwen3-VL-4B and typhoon2-8b eliminated on measured failures, not guesses
- [x] Coordinate convention — **N/A**, `regions` removed from the contract
- [x] Stratified score for the chosen stack — 2026-08-19, split by document type and handwriting
- [x] Bake-off recorded — overview F8 (stage 1) and F10 (stage 2) rather than a separate file
- [x] Model chosen; D2 recorded: `scb10x/typhoon-ocr1.5-3b` → `qwen3:4b`
- [x] D5 answered: no bounding boxes
