# Phase 06 — Production API

**Goal:** an HTTP endpoint the colleague's ~50-line adapter can call without any further
negotiation.
**Inputs:** phase 03 model, phase 02 decoding config, phase 04 chunk handling.
**Exit gate:** deliverable **D1** — endpoint URL, auth method, and one real request/response pair.

Can be built in parallel with phase 05; accuracy work does not change the interface.

> ### Status 2026-08-24 — BUILT. Gate met; two limits need agreeing with the webapp team.
>
> `../serving/` holds the endpoint: `app.py`, `pipeline.py`, `config.py`, plus `smoke_test.py`
> which asserts every status path in section 3 — status **and** the `retryable` flag — 18 checks,
> all passing. D1's sample pair is captured from real runs: `d1-sample-response.json` and
> `d1-sample-response-empty.json`.
>
> **Measured: ~50 s/page single-stream on the RTX 5060 Laptop** (re-measured 2026-08-28;
> the earlier ~33 s figure was optimistic). So a 40-page chunk is ~33 min
> against their 5-minute lease, and this endpoint is good for **about 7 pages per request** at
> the internal deadline, now 540 s. That is the single most important thing to put in front of them —
> see `../serving/README.md` for the three ways forward.
>
> Verified rather than asserted: no file is written during a request, and no document content or
> token appears in the log (9 extracted values checked against the full server log, 0 found).
>
> Still open: TLS, `multipart/form-data`, and `MAX_CONCURRENT` above 1. All three are questions
> for them or for phase 07, not code that is missing.
>
> No endpoint exists. Everything runs from `pipeline.ipynb` or the harness; the colleague has no
> URL to call. With phases 01–03 and 05 substantially done, **this and phase 04 are what stand
> between here and something FA can actually use.**
>
> What is already in hand and does not need rebuilding:
> - the pipeline itself, as two importable modules with no notebook dependency
> - `validate()` — the internal schema check to run before every 200 response
> - six real request/response pairs in `../handover/` — most of D1's sample already exists
>
> One decision to make before writing code: **the determinism answer (Q7).** R5 wants a retried
> chunk to reproduce, and it currently does not. The honest offer is probably "deterministic for a
> pinned deployment", which is a thing to agree with them rather than discover later.
>
> One caution from phase 07's arithmetic: at ~30 s/page single-stream, a 40-page chunk takes ~20
> minutes against a **5-minute** worker lease. The API design should not assume the request can
> block until done.

---

## 1. The integration point

From README §9, their system switches providers by env var:

```
AI_PROVIDER=local
LOCAL_VISION_URL=https://...
LOCAL_VISION_TOKEN=...
```

Everything downstream — schema validation, human review, validation rules, export — is unchanged,
because the contract is the same one Gemini already satisfies.

**Implication: we are replacing a Gemini call.** Their adapter is small because it maps their
internal call shape onto ours. The closer our interface is to "send document, get
`{billCandidates}`", the smaller their adapter stays. Do not invent a session API, a job-submission
API, or a webhook callback. One request in, one JSON out.

`LOCAL_VISION_URL` being `https://` suggests they expect TLS. Confirm — on a laptop behind the
company network this may not be necessary or even possible without a certificate. Add to the
question list if it is not already resolved.

---

## 2. Interface

```
POST /v1/extract
Authorization: Bearer <token>
Content-Type: application/pdf   |   image/png | image/jpeg
Body: raw bytes, ≤ 40 MB
```

Response `200`:

```json
{ "billCandidates": [ ... ] }
```

Nothing else in the body. §3 says "only this, no explanation, no markdown fence". Do not add a
`meta` or `timing` envelope, however useful it would be for us — their validator is `.strict()` at
the top level too (`additionalProperties: false` on the root object, schema line 1481). Metadata
goes in **response headers**:

```
X-Model-Name: <tag>
X-Model-Version: <version>
X-Processing-Ms: 12345
X-Request-Id: <uuid>
```

`X-Model-Name` / `X-Model-Version` exist because D2 says the system records the model on every
extraction for audit. Headers give them that without touching the body contract. **Confirm they
can read it there** — otherwise they need it some other way, and that is a contract change on
their side, not ours.

### Multipart alternative

If they prefer `multipart/form-data` with a file field, accept both. Ask; do not guess.

---

## 3. Error taxonomy (R16)

> Separate **retryable** (overload, timeout) from **terminal** (unsupported input). The queue
> retries the first kind at 60 / 300 / 900 seconds and stops on the second.

Getting this wrong is expensive in both directions: a terminal error marked retryable burns three
retries and 21 minutes of lease time on a corrupt file that will never parse; a retryable error
marked terminal drops real bills on the floor during a transient overload.

| Status | Class | When |
|---|---|---|
| `200` | success | valid JSON, even if `billCandidates: []` |
| `400` | **terminal** | unparseable file, unsupported MIME, zero pages, encrypted PDF |
| `401` | **terminal** | bad or missing token |
| `413` | **terminal** | body > 40 MB |
| `422` | **terminal** | file parsed but is not a document we can process (e.g. a video container) |
| `429` | **retryable** | all model slots busy |
| `500` | **retryable** | unexpected internal error |
| `503` | **retryable** | model not loaded / loading / warming |
| `504` | **retryable** | inference exceeded our internal deadline |

Error body — this is *our* shape, not theirs, since a non-200 is not a contract response:

```json
{ "error": { "code": "MODEL_BUSY", "message": "...", "retryable": true } }
```

Include `"retryable"` explicitly. A status code is a convention; a boolean is unambiguous, and it
means their adapter does not have to encode our table.

**The hard case: schema validation fails on our own output.** Constrained decoding makes this
rare but not impossible (phase 02 §3 — the grammar cannot enforce numeric ranges or `minItems`).
Policy:

1. Validate internally with `jsonschema` before responding — **never** return a body we have not
   validated. Returning invalid JSON costs them the entire chunk.
2. On failure, retry inference internally once (different seed).
3. Still failing → `500` retryable, and **log the invalid output** for debugging, with the
   document content excluded (see §6).

---

## 4. Timeouts and the 5-minute lease

R15: a 40 MB chunk must be answered inside a **5-minute** worker lease. If we exceed it their
worker's lease expires and the job is redelivered — so a slow success is worse than a fast
failure, because it produces duplicate work *and* wasted GPU time.

Set an internal deadline **below** their lease. Suggest 4 minutes (`INFERENCE_DEADLINE_S=240`),
leaving a minute for network, PDF rasterization, and their overhead. On breach: abort inference,
return `504` retryable.

Track elapsed time from request receipt, not from inference start — rasterizing a 40 MB PDF is
not free and it is on our clock.

> **Fixed 2026-08-28 — a breach was reporting `503 MODEL_UNAVAILABLE`, not `504`.**
>
> Observed live: `WARNING 503 backend ReadTimeout`, followed by Ollama calls continuing for
> three more minutes after the response had gone out — the caller's queue retrying, because 503
> is marked retryable. Each retry burned another four minutes of GPU and 429'd every other
> request, which is very likely what wedged the server that morning.
>
> Cause: each model call was made with `timeout=max(5.0, deadline.remaining)`. Once the deadline
> was spent that collapsed to **five seconds**, httpx raised `ReadTimeout`, and `app.py` classed
> it as a backend fault. So the endpoint told the caller *"your model server is broken"* when the
> truth was *"you sent more pages than fit in the deadline"* — and those need opposite responses.
>
> Two changes in `serving/pipeline.py`: a `MIN_STEP_S` floor that refuses to start a call it
> cannot finish, and a `_deadline_aware` wrapper that reclassifies a timeout landing after the
> clock ran out. A genuine backend timeout while time remains is still `503`. The message now
> names the page reached — `exceeded 240s while reading page 5 of 20` — which is the part the
> caller can act on.
>
> **This does not make large chunks work.** At about **50 s per page** measured on DT05 (107 s
> for two pages), 240 s buys roughly **four pages**. The deadline is a symptom; throughput is the
> disease, and that is phase 07. Raising `INFERENCE_DEADLINE_S` past the webapp's own lease only
> moves which side gives up first.

---

## 5. Concurrency (R15: 5 concurrent jobs)

Not achievable on 8 GB. Be explicit about it in the handover rather than letting it be discovered
in a load test.

Build for it anyway:

- a **semaphore** limiting in-flight inference to `MAX_CONCURRENT` (env var; `1` on the laptop)
- requests beyond it either queue with a bounded wait or return `429` retryable — do **not** let
  them pile up until the process OOMs
- `MAX_CONCURRENT` becomes a tuning knob in phase 07, not a code change

---

## 6. Security and retention (R17, R18)

**Auth.** Bearer token from an env var, compared with a constant-time comparison
(`secrets.compare_digest`). Never log the token. mTLS if they prefer — ask which.

**Network.** Bind to the internal interface only, never `0.0.0.0` on a machine with a public
route. On a laptop on the corporate network this means binding to the LAN IP or running behind a
reverse proxy the colleague's app can reach. Confirm the topology with them.

**Retention — R18: the host must not retain documents and must not train on them.**

- Process entirely in memory. `pypdfium2` renders from bytes; no temp file needed.
- If memory pressure ever forces a spill to disk, it must be explicit, to a known directory, and
  deleted in a `finally` block. Question 8 in `00-OVERVIEW.md` §3 asks whether that is even
  allowed. Until answered: **do not spill.**
- **Log no document content.** Not the base64, not the extracted values, not the OCR text.
  Log `X-Request-Id`, page count, byte size, elapsed ms, status, model tag.
- The debug log of invalid model output (§3) is the obvious violation of the line above. Put it
  behind `DEBUG_DUMP_INVALID=false` by default, write to a directory that is gitignored, and
  document that it must stay off in production.
- Verify the inference backend is not caching to disk. Ollama and vLLM both write model weights
  and sometimes prompt caches — check what actually lands on disk during a request.

---

## 7. Implementation sketch

FastAPI + uvicorn. Roughly:

```
serving/
  app.py            # routes, auth, error mapping
  pipeline.py       # bytes -> pages -> windows -> model -> merge -> validate
  model_client.py   # backend-specific (Ollama now, vLLM later) behind one interface
  schema.py         # loads bill-extraction.schema.json, validates
  config.py         # env vars, all of them, with defaults
  decoding-config.md
  bill-extraction.schema.json
```

Keep `model_client.py` behind a narrow interface. The laptop runs Ollama and the server will run
vLLM; that switch should touch one file.

Reuse `../ocr_pipeline.py` for the PDF→image path — `load_pages` and `_fit` already do exactly
this, and `pypdfium2` needs no system install, which keeps the eventual server deployment
pip-only. Refactor it to accept bytes rather than a path.

### Env vars

| Var | Default | Purpose |
|---|---|---|
| `MODEL_TAG` | — | exact model tag, surfaced in `X-Model-Name` |
| `MODEL_BACKEND` | `ollama` | `ollama` \| `vllm` |
| `MODEL_BASE_URL` | `http://localhost:11434/v1` | |
| `AUTH_TOKEN` | — | required, no default |
| `MAX_CONCURRENT` | `1` | |
| `MAX_BODY_MB` | `40` | |
| `INFERENCE_DEADLINE_S` | `540` | raised from 240 on 2026-08-28; lease confirmed longer than 5 min |
| `WINDOW_PAGES` | `3` | from phase 04 |
| `TARGET_DIM` | `1500` | **not 1800** — 1800 degenerates into `'@'` on some pages (F11, retested F21) |
| `DEBUG_DUMP_INVALID` | `false` | must stay false in production |

---

## 8. Deliverable D1

Give them a copy-pasteable pair. Curl request:

```bash
curl -X POST http://<host>:8000/v1/extract -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/pdf" --data-binary @sample-chunk.pdf
```

Plus the response JSON, from a **real run**, not hand-written. Include a `{"billCandidates": []}`
example too so they can see the empty case is a normal 200.

Also hand over an OpenAPI spec (FastAPI generates it) — but note that the response schema in it
must be their schema file, not something FastAPI inferred from our Python types. Those will drift.

---

## 9. Task list

- [ ] Confirm with the webapp team: TLS required? multipart or raw body? headers readable for D2?
- [ ] `POST /v1/extract` accepting PDF and images
- [ ] Bearer auth, constant-time compare, token never logged
- [ ] Full error taxonomy with `retryable` boolean, each status path tested
- [ ] Internal `jsonschema` validation before every 200 response
- [x] Internal deadline + `504` on breach — **corrected 2026-08-28**: a breach was
      surfacing as `503 MODEL_UNAVAILABLE` via a collapsed 5-second timeout. See §4
- [ ] Concurrency semaphore + `429`
- [ ] Logging carries no document content; verified by reading an actual log file
- [ ] No temp files on disk during a request; verified by watching the filesystem during one
- [ ] `X-Model-Name` / `X-Model-Version` headers populated
- [ ] D1 sample request/response captured from a real run
