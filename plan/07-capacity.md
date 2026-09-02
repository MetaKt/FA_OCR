# Phase 07 — Capacity & Hardware Spec

**Goal:** measure what the laptop actually does, and turn the gap to R15 into a procurement spec.
**Inputs:** phase 06 API.
**Exit gate:** deliverable **D6** — real elapsed time on a 40 MB / 40-page chunk — plus a server
spec the company can act on.

> ### Status 2026-08-19 — not started, but the per-page numbers already exist
>
> Measured on the laptop, single stream, from the golden runs:
>
> ```
> stage 1 (typhoon-ocr1.5-3b)   ~15-80 s/page   varies hugely with page content
> stage 2 (qwen3:4b)            ~13-20 s/page   stable
> ```
>
> **The R15 gap is already visible without building anything.** At roughly 30 s/page combined, a
> 40-page chunk is ~20 minutes against a **5-minute** lease — and that is at concurrency 1, where
> R15 asks for 5. This phase's real output was always going to be a procurement spec rather than a
> passing number (F3), and these numbers are enough to start writing it.
>
> One measured detail worth carrying in: `repeat_penalty=1.25` made the worst page **5.7× faster**
> (80 s → 14 s) while *improving* accuracy, because the model stopped generating dash rows it would
> never use. Wrong settings cost speed and accuracy together; there was no trade to make.

---

## 1. Set expectations before measuring

R15 asks for:

| Requirement | Value |
|---|---|
| Concurrency | 5 simultaneous jobs |
| Throughput | 300 pages per job |
| Latency | ≤ 40 MB chunk answered inside a **5-minute** worker lease |

Current hardware: Windows laptop, RTX 5060 Laptop, **8 GB VRAM**, single GPU.

**This will not meet R15, and that is the expected outcome of this phase.** The deliverable is
not a passing number — it is a measured number plus the arithmetic that converts it into
"buy this". Do not present laptop timings as if they answered D6 for production; label every
measurement with the hardware it came from.

Reference point already in hand: typhoon-ocr1.5-3b via Ollama took **~14 s for a single page**
on this laptop. At that rate, 40 pages is ~9 minutes on one stream — nearly double the entire
lease, before any concurrency.

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

1. **Lower `TARGET_DIM`.** Vision token count scales roughly with area, so 1800 → 1400 is about
   a 40% token reduction. Often the single biggest win. Measure the accuracy cost per stratum —
   handwriting will suffer first.
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
one 40-page chunk, single stream   = 40 × T seconds
budget                             = 240 s (our internal deadline, under their 5-min lease)
required parallel streams per job  = ceil(40 × T / 240)
× 5 concurrent jobs                = total parallel streams needed
```

Worked example at `T = 14 s`: 40 × 14 = 560 s single stream. 560 / 240 = 2.33 → **3 parallel
streams per job**, × 5 jobs = **15 parallel inference streams**. On a server GPU with continuous
batching, a single A100/L40S-class card handles far more than 3 streams of a 4B model
simultaneously, so this is likely one or two datacentre cards rather than fifteen of anything.

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

- **Reduce concurrency.** 1 job at a time, queued. Throughput drops to whatever one stream gives;
  a 300-page job takes about `300 × T` seconds. At T=14 s that is 70 minutes. Whether that is
  acceptable depends entirely on how often FA runs a clearing batch — if it is monthly, an hour
  is fine and the whole capacity concern is theoretical.
- **Smaller chunks.** If the webapp team can split jobs into 5-page chunks, each fits the lease
  easily and the queue absorbs the total time. This is likely the cheapest fix and it lives
  entirely on their side — which is why question 2 in `00-OVERVIEW.md` §3 matters.
- **Longer lease.** Their 5 minutes is a worker configuration, not a law of nature. Ask whether
  it can be raised for the local provider.

Present these as options with numbers attached, not as excuses. The measured `T` is what makes
the conversation productive.

---

## 6. Task list

- [ ] Per-stage timing instrumentation in the phase 06 pipeline
- [ ] Build a real 40 MB / 40-page test chunk
- [ ] Baseline: 1 page, 10 pages, 40 pages sequential
- [ ] **D6**: 40 MB / 40-page elapsed time, labelled with hardware
- [ ] Concurrency 2 and 5 — record the failure mode, not just the failure
- [ ] Peak VRAM and host RAM recorded
- [ ] Optimization matrix, each entry with its accuracy cost per stratum
- [ ] Server spec: two options with the arithmetic shown
- [ ] Fallback options (§5) written with real numbers
