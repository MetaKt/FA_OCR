# Serving API — D1

`POST /v1/extract`. One request in, one contract response out. Deliberately not a job API: this
replaces a Gemini call behind their `AI_PROVIDER=local` switch, and the smaller our interface, the
smaller their adapter stays.

```
serving/
  app.py          routes, auth, error mapping        <- uvicorn entrypoint
  pipeline.py     bytes -> pages -> validated JSON
  config.py       every env var, with defaults
  smoke_test.py   exercises every documented status path
  d1-sample-response.json         real 200, one bill
  d1-sample-response-empty.json   real 200, no bills
```

`pipeline.py` **imports** `../src/ocr_pipeline.py` and `../src/stage2_extract.py` rather than
copying them. A serving copy that drifts from the evaluated code makes the accuracy number
fiction.

---

## Running it

```bash
AUTH_TOKEN=<secret> ./.venv/Scripts/python.exe -m uvicorn serving.app:app --host 127.0.0.1 --port 8000
```

It refuses to start without `AUTH_TOKEN`. That is deliberate — an unauthenticated endpoint
accepting documents full of national ID and bank numbers is not something to have a default for.

Then, from another terminal:

```bash
./.venv/Scripts/python.exe serving/smoke_test.py
```

18 checks, ~1 minute. Ollama must be running with both models pulled.

---

## D1 — the request/response pair

```bash
curl -X POST http://127.0.0.1:8000/v1/extract \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/pdf" \
  --data-binary @chunk.pdf
```

`200` body is exactly `{"billCandidates": [...]}` and nothing else — their validator is strict at
the root, so an added `meta` or `timing` key would fail the whole chunk. Metadata is in headers:

```
X-Request-Id      echoed from the request if supplied, otherwise generated
X-Model-Name      scb10x/typhoon-ocr1.5-3b+qwen3:4b
X-Model-Version   f8b63e075c7bfdff9ef+359d7dd4bcdab3d86b8     <- digests, not tags
X-Processing-Ms   32801
X-Page-Count      1
```

`X-Model-Version` carries the backend's **digests**, not the tags, because a tag is mutable — the
publisher can repoint `qwen3:4b` and an audit log naming only the tag would record nothing useful.
**Confirm the colleague can read response headers**; if not, D2's per-extraction audit needs
another home, and that is a change on their side.

Both sample files in this folder are captured from real runs, including the empty case —
`{"billCandidates": []}` is a normal `200`, not an error. A not-a-bill page (an ID-card copy, an
FM-FA-05 cover sheet) is about a third of a real clearing set.

---

## Error taxonomy

`retryable` is stated explicitly in the body. A status code is a convention; the boolean is
unambiguous, and it means their adapter does not have to encode our table.

```json
{ "error": { "code": "MODEL_BUSY", "message": "...", "retryable": true } }
```

| Status | Code | Retryable | When |
|---|---|---|---|
| `200` | — | — | valid JSON, **including `billCandidates: []`** |
| `400` | `EMPTY_BODY` / `UNREADABLE_DOCUMENT` / `UNSUPPORTED_MEDIA_TYPE` | **no** | corrupt, encrypted, zero pages, wrong MIME |
| `401` | `UNAUTHORISED` | **no** | bad or missing bearer token |
| `413` | `PAYLOAD_TOO_LARGE` | **no** | body over `MAX_BODY_MB` |
| `429` | `MODEL_BUSY` | yes | all inference slots in use |
| `500` | `SCHEMA_VALIDATION_FAILED` / `INTERNAL_ERROR` | yes | our output failed the contract twice |
| `503` | `MODEL_UNAVAILABLE` | yes | backend unreachable or model not pulled |
| `504` | `DEADLINE_EXCEEDED` | yes | inference passed the internal deadline |

Every one of these is asserted in `smoke_test.py`, status **and** flag.

**We never return a body we have not validated.** `run_validated` checks against their schema with
`jsonschema` and, on failure, re-runs inference once with a different seed before giving up with a
retryable `500`. Constrained decoding makes invalid output rare, not impossible — the grammar
cannot enforce numeric ranges or `minItems`.

---

## Two limits to agree before wiring this up

### 1. A 40-page chunk cannot be answered synchronously

Measured: **~50 s per page**, single stream, on an RTX 5060 Laptop (8 GB). Re-measured
2026-08-28 (107 s for a two-page PDF, end to end through the endpoint); the earlier ~33 s figure
was optimistic and is superseded.

```
 1 page      ~50 s      fine
10 pages     ~9 min     near the 600 s internal deadline
40 pages    ~33 min     well beyond any single request
```

`INFERENCE_DEADLINE_S` defaults to **600** (240 -> 540 on 2026-08-28, then 540 -> 600 on 2026-08-31 against their confirmed 660 s client abort; the webapp side
confirmed their worker lease is longer than five minutes). On breach the endpoint returns a
retryable `504 DEADLINE_EXCEEDED` naming the page it reached — *"exceeded 600s while reading page
9 of 20"* — so the caller can act on it. A slow success is worse than a fast failure: the lease
expires, the job is redelivered, and the GPU is burned twice for one answer.

**So today this endpoint is good for roughly 10 pages per request** — and fewer is better. One
request holds the only inference slot for its whole duration, so a nine-minute extraction means
every other request gets `429` for nine minutes. A chunk of 10–15 pages also fails cheaper and
retries cheaper than a chunk of 40. Three ways forward, and it is
their call:

1. they split chunks page-wise or in small batches before calling (smallest change, works now)
2. they raise the worker lease past the real chunk time
3. we add a job-submit/poll API — a real change to the interface, and the plan explicitly warns
   against inventing one without being asked

### 2. Cross-page merging works, but is unproven on real documents

A bill running across a page break is joined into one candidate — `../src/merge.py`, 26 fixtures
in `../eval/test_merge.py`. Two routes: both pages state their position (`หน้า 1/2` then
`หน้า 2/2`), or two of the document number, seller, total and date agree. Any conflict refuses the
join, even against a page marker.

**Not yet seen in real data.** 54 transcripts of your documents carry no page markers at all, so
route 1 is tested only against constructed fixtures. Route 2 was checked live: a five-bill PDF
returns exactly 5 candidates, including two pages with `sellerName: null` that an over-eager rule
would have merged.

Duplicate scans of the *same* receipt are a different problem and are **not** handled — that needs
the whole upload in view, which is your side.

`candidateIndex` is assigned after all pages are collected, in page order, so it never repeats.

---

## Environment

| Var | Default | Notes |
|---|---|---|
| `AUTH_TOKEN` | — | **required**, no default, never logged |
| `STAGE1_MODEL` | `scb10x/typhoon-ocr1.5-3b` | changing it invalidates the accuracy report |
| `STAGE2_MODEL` | `qwen3:4b` | as above |
| `MODEL_BASE_URL` | `http://localhost:11434` | Ollama's native API, not `/v1` |
| `MAX_CONCURRENT` | `1` | honest value on 8 GB; R15 asks for 5 |
| `MAX_BODY_MB` | `40` | |
| `INFERENCE_DEADLINE_S` | `600` | keep below their **client abort** (660 s), not their lease (900 s) |
| `TARGET_DIM` | `1500` | **not 1800** — at 1800 stage 1 degenerates into a burst of `@` |
| `RETRY_TARGET_DIMS` | `1300,2000` | sizes to re-read a **looped** page at, in order. Empty disables the retry |
| `DEBUG_DUMP_INVALID` | `false` | writes document content. Must stay false in production (R18) |

---

## Privacy

- **Nothing touches disk.** `pypdfium2` and Pillow both read from memory, so a request is
  rasterised, inferred and discarded without a temp file (R18).
- **Logs carry no document content** — request id, byte size, page count, bill count, elapsed ms,
  status. No transcript, no field values, no base64.
- `DEBUG_DUMP_INVALID` is the one deliberate exception and it is off by default.
- Bearer token compared with `secrets.compare_digest`; a plain `==` leaks the token's prefix
  through timing, one character at a time.
- Bind to `127.0.0.1` for local testing. For the colleague's machine to reach it, bind to the LAN
  IP — **not** `0.0.0.0` on a machine with a public route, and **not** through a public tunnel
  such as ngrok or Cloudflare, which would route employee national ID and bank numbers through a
  third party.

---

## Not done

- TLS. `LOCAL_VISION_URL` in their README is `https://` — ask whether that is required, since on
  a laptop behind the corporate network it may not be possible without a certificate.
- `multipart/form-data`. Raw body only for now; ask before building the alternative.
- Concurrency above 1. The semaphore exists and `MAX_CONCURRENT` is the knob, but 8 GB will not
  hold two copies of the pair — that is phase 07's hardware question.
