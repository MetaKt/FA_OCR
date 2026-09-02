# Phase 07 — Capacity & Hardware Spec

**Goal:** measure what the laptop actually does, and turn the gap to R15 into a procurement spec.
**Inputs:** phase 06 API.
**Exit gate:** deliverable **D6** — real elapsed time on a 40 MB / 40-page chunk — plus a server
spec the company can act on.

> ### Status 2026-09-02 — targets corrected, measurement not started
>
> **Two of the four numbers this phase measures against have changed since it was written, and
> two others were never confirmed at all.** Read section 1 before doing any arithmetic — the
> original draft computed a procurement spec against a 5-minute lease that no longer exists.
>
> Measured on the laptop, single stream:
>
> ```
> stage 1 (typhoon-ocr1.5-3b)   ~15-80 s/page   varies hugely with page content
> stage 2 (qwen3:4b)            ~13-20 s/page   stable
> end to end, real chunks       ~50-56 s/page   live endpoint, 2026-08-31 and 2026-09-02
> ```
>
> The live figures are whole-request wall clock over real clearing sets: a 5-page file at
> 280-297 s, a 3-page at 175-180 s, a 4-page toll set at 235-250 s. Those are the numbers to
> reason from; the per-stage splits above predate the guards now in the pipeline.
>
> One measured detail worth carrying in: `repeat_penalty=1.25` made the worst page **5.7× faster**
> (80 s → 14 s) while *improving* accuracy, because the model stopped generating dash rows it would
> never use. Wrong settings cost speed and accuracy together; there was no trade to make. The
> stage-1 loop guard added 2026-08-31 is the same shape: the broken page was the *slow* one
> (72 s producing nothing), and re-reading it at 1300 px cost 17 s and produced the answer.

---

## 1. Set expectations before measuring

R15 comes from the webapp team's `README.md`, which is the **original acceptance contract and is
partly stale** — it also still asks for Lao and a 95% accuracy gate, both since dropped. Later
messages superseded some of its numbers and were silent on others. Do not measure against a
target without checking which column it is in:

| Requirement | R15 (their README) | Status |
|---|---|---|
| Worker lease | 5 minutes | **superseded → 900 s**, confirmed 2026-08-28 |
| Client abort | not stated | **660 s**, confirmed 2026-08-28. The number to sit under |
| Our deadline | — | 600 s, `INFERENCE_DEADLINE_S`, set under their abort |
| Chunk size | ≤ 40 MB / 40 pages | **UNCONFIRMED.** Still what their README says |
| Throughput | 300 pages per job | **UNCONFIRMED.** Same README |
| Concurrency | 5 simultaneous jobs | **UNCONFIRMED.** Same README. Ours is 1 |

> **A retracted claim.** `serving/config.py` asserted that a 7-page chunk was "their ceiling".
> No message from the webapp team says that. It came from the size of the files they happened to
> send for testing, and it was written down as if it were a requirement. The three UNCONFIRMED
> rows above are open questions, not settled smaller numbers, and the difference matters: at
> ~50 s/page a 7-page chunk fits our deadline comfortably and a 40-page chunk cannot fit any
> lease they have mentioned.

Current hardware: Windows laptop, RTX 5060 Laptop, **8 GB VRAM**, single GPU.

**The laptop will not meet the README's numbers, and that is the expected outcome of this
phase.** The deliverable is not a passing number — it is a measured number plus the arithmetic
that converts it into "buy this". Do not present laptop timings as if they answered D6 for
production; label every measurement with the hardware it came from.

**Per-page cost `T` is worth measuring before any of the open questions are answered**, because
it is the input to every version of the arithmetic. What changes with their answers is the target
`T` has to clear, not `T` itself.

---

## 2. What to measure

Per page, at `MAX_CONCURRENT=1`, on the chosen model:

- **rasterization ms** — `pypdfium2` PDF page → PIL image
- **preprocessing ms** — resize, deskew, whatever phase 05 settled on
- **encode ms** — PNG + base64
- **inference ms** — the model call, the dominant term
- **postprocess ms** — merge, validate, normalize
- **peak VRAM**
- **peak host RAM** — a 40-page 40 MB PDF held as images is not small; at 1390×1800 RGB that is
  ~7.5 MB per page uncompressed, ~300 MB for 40 pages before base64 (which inflates ~33%). This
  is a real OOM risk on a laptop and an argument for streaming pages rather than materializing
  all of them.

Then scale:

| Test | Measures |
|---|---|
| 1 page | baseline per-page cost |
| 10 pages sequential | is per-page cost stable, or does something degrade? |
| 40 pages / 40 MB | **D6** |
| 40 pages × 2 concurrent | does throughput improve at all, or just thrash? |
| 40 pages × 5 concurrent | R15 target — expect failure; record *how* it fails (OOM? timeout? swap?) |

Record how it fails, not just that it failed. "OOM at 3 concurrent" and "completes at 5 concurrent
but takes 40 minutes" imply completely different fixes.

---

## 3. Optimizations to try on the laptop

Cheapest first. Rescore accuracy after each — every one of these can trade accuracy for speed,
and a speedup that costs 4% accuracy is usually the wrong trade for a system whose entire purpose
is avoiding manual re-entry.

1. ~~**Lower `TARGET_DIM`.**~~ **Already done, and the reasoning has changed.** Settled at 1500
   in phase 03: at exactly 1800 stage 1 degenerates into a burst of `@` on some scanned pages, so
   the old "1800 → 1400" advice starts from a size that does not work. Lower is also not
   uniformly faster — measured on P06690 page 198, 1300 px read the page in **17 s** where 1500
   looped for **72 s** producing nothing, while 900 and 1100 looped too. Resolution changes
   whether a page is read at all, which swamps its effect on token count. See
   `serving/config.py:RETRY_TARGET_DIMS` for the five-size measurement.
2. **Quantization.** 4-bit (AWQ / GPTQ) over bf16. Frees VRAM for KV cache and larger batches.
   Costs some accuracy; measure it.
3. **Smaller model.** If a 1.7B (dots.ocr) is within a point or two of a 4B, that is a very good
   trade at this scale.
4. **Batching.** vLLM's continuous batching is the main reason to move off Ollama. Meaningful
   throughput gains at concurrency, near-zero at concurrency 1.
5. **`max_tokens` ceiling.** A 40-page chunk with many bills produces a lot of JSON. Set the
   ceiling from the observed p99, not a round number — but too low truncates mid-object and the
   grammar cannot save a response that ran out of budget.
6. **Skip blank pages.** A cheap pixel-variance check before inference. Separator sheets are
   common in scanned batches, and blank pages cost full inference for a guaranteed empty result.
7. **Greyscale.** Fewer bytes to encode and transfer; verify no accuracy loss first.
8. **Cache nothing.** Tempting to cache by document hash, but R18 forbids retention. Do not.

---

## 4. Deriving the server spec

Once per-page cost on known hardware is measured, the arithmetic is straightforward.

Let `T` = seconds per page, single stream, on the chosen model and settings.

```
one chunk, single stream           = pages × T seconds
budget                             = 600 s (INFERENCE_DEADLINE_S, set under their 660 s abort)
required parallel streams per job  = ceil(pages × T / 600)
× concurrent jobs                  = total parallel streams needed
```

`pages` and `concurrent jobs` are the two UNCONFIRMED rows in section 1, so run the arithmetic
for both readings and present both:

| | pages | T | single stream | streams/job at 600 s |
|---|---|---|---|---|
| their README | 40 | 50 s | 2000 s (33 min) | **4** |
| the files they actually send | 5-7 | 50 s | 250-350 s | **1** — fits today |

At the README's 40 pages and 5 concurrent jobs that is 4 × 5 = **20 parallel inference streams**.
On a server GPU with continuous batching, a single A100/L40S-class card handles far more than 4
streams of a 4B model simultaneously, so this is likely one or two datacentre cards rather than
twenty of anything.

**At the sizes they have actually been sending, one stream already fits inside the deadline.**
That is not the same as meeting R15, and it is not a reason to skip the measurement — but it does
mean the honest headline may be "the requirement needs confirming" rather than "buy a server".
Ask before speccing hardware against a number nobody has reaffirmed since 2026-08-10.

Do the real arithmetic with the real `T`. Then specify:

| Item | How to derive |
|---|---|
| GPU model & count | from the parallel-stream requirement + model VRAM at target batch |
| VRAM per GPU | model weights + KV cache × batch size + vision token activations. Measure at batch=1 and extrapolate. |
| Host RAM | ≥ 4× peak observed, for 5 concurrent 40-page chunks held as images |
| Disk | model weights only — no document storage, per R18 |
| OS | **Linux.** vLLM has no native Windows build, and this is where the throughput lives. |
| CPU | PDF rasterization is CPU-bound and parallel; ≥ 8 cores |
| Network | internal only, per R17 |

Give **two options**: a minimum that meets R15, and a comfortable one with headroom. A single
number invites the answer "can we do it with less?", which is a conversation better had with
data already on the table.

---

## 5. If the server does not happen

Realistic scenario given F3 — the project has to prove itself on a laptop first. Then R15 cannot
be met and the honest positions are:

- **Reduce concurrency.** This is already the shipped position: `MAX_CONCURRENT=1`, and anything
  beyond it gets `429` immediately rather than queueing. A 300-page job takes about `300 × T`
  seconds — at the measured 50 s/page that is **over four hours**. Whether that matters depends
  entirely on how often FA runs a clearing batch, which nobody has asked them. If it is monthly,
  the whole capacity concern is theoretical; if it is daily, it is the main problem. **Ask FA
  before speccing anything.**
- **Smaller chunks.** If the webapp team can split jobs into 5-page chunks, each fits the lease
  easily and the queue absorbs the total time. This is likely the cheapest fix and it lives
  entirely on their side — which is why question 2 in `00-OVERVIEW.md` §3 matters.
- ~~**Longer lease.**~~ **Already happened.** The lease was raised to **900 s** and the client
  abort set at **660 s**, confirmed 2026-08-28. Our deadline sits at 600 s under both. This was
  the cheapest fix on the list and it is spent — do not offer it again as an option.

Present these as options with numbers attached, not as excuses. The measured `T` is what makes
the conversation productive.

---

## 6. Task list

- [ ] **Confirm the three UNCONFIRMED targets** (chunk size, pages per job, concurrency) with the
      webapp team before any hardware arithmetic. Section 1.
- [ ] **Ask FA how often a clearing batch is run.** It decides whether throughput is a real
      problem or a theoretical one, and it is one question.
- [ ] Per-stage timing instrumentation in the phase 06 pipeline
- [ ] Build a real 40 MB / 40-page test chunk
- [ ] Baseline: 1 page, 10 pages, 40 pages sequential
- [ ] **D6**: 40 MB / 40-page elapsed time, labelled with hardware
- [ ] Concurrency 2 and 5 — record the failure mode, not just the failure
- [ ] Peak VRAM and host RAM recorded
- [ ] Optimization matrix, each entry with its accuracy cost per stratum
- [ ] Server spec: two options with the arithmetic shown
- [ ] Fallback options (§5) written with real numbers
