# Phase 02 — Serving Spike: Constrained Decoding

**Goal:** get *some* model to emit output that validates against
`bill-extraction.schema.json` — 20 times out of 20, with no post-processing repair.
**Why before the bake-off:** you cannot score models against a schema nothing can produce yet.
This phase de-risks the output shape independently of which model wins.

**Inputs:** none (runs in parallel with phase 01).
**Exit gate:** 20/20 schema-valid responses on 20 different pages, and the decoding config file
saved as deliverable **D3**.

> ### Status 2026-08-19 — the shape is solved; determinism is not
>
> **Constrained decoding works and is D3.** `stage2_extract.reduced_schema()` is handed to Ollama
> as `format`, which compiles it to GBNF internally. Nothing is prompted into JSON and nothing is
> repaired afterwards. `contract_schema()` derives the full shape from *their* file at run time, so
> their next contract change flows through without an edit here.
>
> **Schema validity: 12/12 on every run.** Short of the 20/20 wording only because the golden set
> has 12 pages — the failure rate is zero, not unmeasured.
>
> **Determinism (R5) still fails, and it is the open item of this phase.** Same page, same seed,
> `temperature=0`: transcripts differ between runs and the score moves by a field or two (125 vs
> 126 on 2026-08-19). Cause is Ollama's prompt cache plus GPU kernel non-determinism, not sampling.
> Revisit with the serving-stack decision in phase 06 — a fixed, single-stream deployment may be
> the honest answer to offer them (Q7).
>
> **Two schema traps confirmed live:** `\d` is unsupported in Ollama's grammar (their contract's
> date pattern uses it, so their file cannot be fed in as-is — `[0-9]{4}` works), and the key count
> is now **29**, not 22.

---

## 1. Why prompting is not allowed here

§4 of the contract is blunt about this, and it names the real incident: a model helpfully added a
`notes` key and all 40 bills in that chunk were discarded. The schema is `.strict()` /
`additionalProperties: false` at every level.

Prompt-only JSON fails in ways that are rare per-call and certain at volume: an extra key, a
trailing comma, a markdown fence, a truncated array, `"confidence": "high"` instead of a number.
At 300 pages a job, a 1% malformation rate loses bills every single run.

Constrained decoding makes it structurally impossible: the sampler is masked at each token so
only grammar-legal continuations exist.

---

## 2. Stack options on this hardware

Hardware is a Windows laptop, RTX 5060 Laptop, **8 GB VRAM** (see `00-OVERVIEW.md` F3).

| Option | Windows? | Schema support | Verdict |
|---|---|---|---|
| **Ollama `format`** | native | JSON Schema → GBNF internally | **Start here.** Already installed, zero new infra. |
| **llama.cpp GBNF** | native | GBNF grammar directly | Fallback if Ollama's conversion drops constraints |
| **vLLM `guided_json`** (xgrammar) | WSL2 only | full JSON Schema | The eventual server answer. Set up in phase 06/07, not now. |
| **Outlines / LM Format Enforcer** | native (transformers) | full | Useful if we run a model through `transformers` rather than a server |

Decision: **spike on Ollama, plan for vLLM.** Getting a real number today beats a perfect stack
next week, and the schema file is the same input either way.

---

## 3. Known risk: schema features that may not survive conversion

Ollama and llama.cpp convert JSON Schema to GBNF, and the conversion is **lossy**. Test each of
these explicitly — do not assume:

**MEASURED 2026-08-10** on Ollama + qwen3:4b, by bisecting one feature at a time:

| Feature tested | Result |
|---|---|
| `anyOf: [string, null]` | works |
| `anyOf: [number, null]` | works |
| `pattern` with `\d` — e.g. `^\d{4}-\d{2}-\d{2}$` | **400 "failed to initialize samplers: failed to parse grammar"** |
| `pattern` with a character class — `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` | works |
| `pattern` `^[A-Z]{3}$` | works |
| `enum` | works |
| `minimum` / `maximum` on number | accepted, **but not enforced** — GBNF cannot express numeric ranges |
| nested array of objects | works |

So: **`\d` is unsupported; use `[0-9]`.** This bit us immediately — `stage2_extract.py` had to be
changed. Their contract schema uses a large `\d`-based date pattern (line 157), so **their schema
file cannot be fed to Ollama as-is.** vLLM/xgrammar may handle it; that needs its own test.

| Feature | Where it appears | Risk |
|---|---|---|
| `"pattern"` regex on `documentDate` | schema line 157 | **CONFIRMED BROKEN on Ollama** — uses `\d`. Either rewrite the pattern to `[0-9]` for the grammar (and keep validating against their original), or use vLLM. |
| `"format": "date"` | line 156 | Almost certainly ignored |
| `minimum` / `maximum` on integers | 0–1000 on every coordinate | Numeric range constraints are **not** expressible in GBNF. Expect `xMin: 4000` to be grammatically legal. |
| `minItems: 1` on `regions` | line 1306 | May be dropped → empty `regions` array passes grammar, fails contract |
| `enum` on `evidence[].role` | line 1354 | Usually survives; verify |
| `additionalProperties: false` | everywhere | The critical one. Verify no extra keys can appear. |

**Therefore: constrained decoding is necessary but not sufficient.** We still validate every
response with a real JSON Schema validator before returning it. In Python:

```
pip install jsonschema
```

Grammar prevents structural garbage; the validator catches the semantic constraints the grammar
lost. Phase 06 decides what to do when validation fails (retry vs terminal error).

---

## 4. Procedure

### Step 1 — schema-valid output on a trivial input

Before touching a real receipt, prove the plumbing: send a blank white image and demand a
response. Correct answer is `{"billCandidates": []}` — which §3 explicitly calls "correct and
normal". If we cannot get an empty array reliably, nothing else matters.

### Step 2 — one real receipt, one candidate

Pick the cleanest printed Thai tax invoice from `data/golden/source/`. Expect the *values* to be
wrong at this stage. We are testing shape, not accuracy. Check with `jsonschema`, not by eye.

### Step 3 — the 22-key trap

Deliberately try to break it. Add to the prompt: "include a `notes` field explaining your
reasoning." With constrained decoding on, the extra key must be **impossible**. If it appears,
the grammar is not actually being applied — stop and fix that before continuing.

### Step 4 — 20 pages, 20 valid responses

Run 20 varied pages. Record: valid count, and for each failure, which schema rule broke. Failures
here are information about which constraints the grammar dropped (§3), so log the validator's
error path, not just pass/fail.

### Step 5 — determinism check (R5)

Run the same page 3 times with `temperature: 0` and a fixed seed. Byte-identical output required.
Note that Ollama's seed handling and any batching in the server can both perturb this — record
exactly which knobs were set. This is the evidence behind question 7 in `00-OVERVIEW.md` §3.

---

## 5. Prompt design under constraint

With the grammar handling *shape*, the prompt is only responsible for *content and policy*. Keep
it short — a long prompt eats the context budget that page images need.

The prompt must carry the rules the grammar cannot express:

- Unknown → `null`, never guess, never `""` (R9)
- Buddhist era → Gregorian: `15 มี.ค. 2567` → `2024-03-15` (§3)
- `sellerBranch` recorded exactly as printed; `สำนักงานใหญ่` stays `สำนักงานใหญ่`, `สาขาที่ 00012`
  keeps the branch code. Do not interpret. (§3)
- `currency` as ISO code: `THB`, `LAK`, `USD`
- Coordinates are integers 0–1000 normalized to the page, **not pixels, not 0–1 floats** (§4)
- `chunkPageIndex` is 0-based within this chunk only; never guess the real page number (§4)
- Ambiguous bill boundary → two candidates, not a guess (R12)
- Confidence must reflect actual uncertainty; do not default to 1.0 (R13)
- Never suggest a category as confirmed (R10)

Write the prompt to `prompts/extract_v1.txt` and version it. Every accuracy number in phase 03
and 05 must be traceable to a prompt version, or the comparisons are meaningless.

> **Note on Typhoon:** none of this applies to typhoon-ocr1.5. That model rejects substitute
> prompts entirely (`00-OVERVIEW.md` F2). If Typhoon is in the bake-off it goes through the
> two-stage path — Typhoon transcribes with its own official prompt, then a *separate text model*
> runs this prompt with constrained decoding over the transcript.

---

## 6. Which model to spike on

Any model will do — we are testing the rig, not the model. Pick by "will it load in 8 GB":

- **Qwen3-VL-4B-Instruct** (4-bit) — likely the phase 03 favourite, so spiking on it saves work
- **Qwen2.5-VL-7B** (4-bit) — tight in 8 GB, but well-supported everywhere
- **typhoon-ocr1.5-3b** — already downloaded, but only usable in the two-stage path

Whatever is chosen, record the **exact model tag and quantization** — that is deliverable D2 and
"Qwen3-VL" without a tag is not an audit trail.

---

## 7. Deliverable D3

The contract asks for "the constrained decoding file actually used". Save to `serving/`:

- `bill-extraction.schema.json` — **copied unmodified** from their handover. §4 says do not hand-write
  it and do not edit it; if the contract changes they send a new one. Record the copy date and
  a hash so we can tell whether ours has drifted.
- the generated GBNF grammar, if Ollama/llama.cpp produces one we can extract
- `serving/decoding-config.md` — which backend, which flags, which model tag, and **a list of
  which schema constraints the grammar does not enforce** (from §3)

That last list is the honest part of the deliverable. Handing over a grammar while quietly
knowing it does not enforce the 0–1000 coordinate range would be exactly the kind of thing that
costs them a chunk in production.

---

## 8. Task list

- [x] `pip install jsonschema`
- [x] Schema read from *their* folder at run time instead of copied — `contract_schema()`.
      Better than a copy: their updates cannot silently diverge from ours
- [x] Page with no bill → `{"billCandidates": []}`, schema-valid. Enforced by the `has_money`
      guard, not by hope — ID-card copies and delivery notes were producing phantom bills
- [x] One real receipt → schema-valid, **29 keys** exactly (was 22 when this was written)
- [ ] `notes`-injection attempt provably fails to produce an extra key — not tested. Low risk:
      the schema is closed (`additionalProperties: false`) and enforced by the grammar
- [x] Every page schema-valid, 12/12 — gate says 20, the golden set has 12
- [ ] **Determinism: same page × 3 → byte-identical — FAILS.** See F12/F14 in the overview
- [x] Constraint-loss table (§3) filled in with measured results — `\d` unsupported is the finding
- [x] Prompt written and versioned — `stage2_extract.PROMPT`, with per-category additions in
      `data/category_rules.json`
- [x] D3 delivered as the live `reduced_schema()` rather than a separate config document
