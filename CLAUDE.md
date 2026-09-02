# ADV Clear — model side

Two-stage local pipeline that turns a scanned Thai expense document into the webapp's
`billCandidates` JSON. We own **the model half only**; a colleague owns the webapp and the
contract. Client: บริษัท ทีม คอนซัลติ้ง เอนจิเนียริ่ง แอนด์ แมเนจเมนท์ จำกัด (มหาชน),
tax id `0107561000030` — this is *us* on every document, the buyer, never the seller.

**Keep this file current.** It is the shared memory across Claude Code windows. Anything a second
window would otherwise have to re-derive belongs here.

---

## How to work here

- **Discuss before writing or changing code.** Bring the plan, not the diff.
- **Never guess.** If a fact is not established, ask or measure it. Do not infer it.
- **Short answers.** Long explanations have been asked against repeatedly.
- **Deterministic guards beat prompt rules.** Measured: guards 6/6, prompt rules 0/6 (F15).
  Every accuracy win since has come from a rule, not from prompt wording.
- **Don't touch what already works.** Additive changes; leave working paths alone.
- **This is a prototype on the user's own Windows machine.** Do not propose infrastructure
  hardening (DHCP, single GPU, hand-started uvicorn) — deferred to the company server.
- **Lao is descoped.** Thai and some English only, whatever the colleague's README says.
- **The 95% gate is not binding.** The goal is "as accurate as possible"; FA rechecks anyway.
- Documents carry **national IDs, names, addresses, bank numbers**. Company prefers self-hosted.

---

## Architecture

```
PDF bytes
  -> rasterise            pypdfium2, TARGET_DIM=1500 (NOT 1800: @-burst degeneration)
  -> stage 1  typhoon-ocr1.5-3b   image -> Thai HTML/text     ~50 s/page
       degeneracy guard: loop / @-burst / empty -> re-read at 1300, then 2000
  -> stage 2  qwen3:4b            text -> JSON, constrained by reduced_schema()
  -> merge          joins one bill spanning several pages
  -> certlink       folds a 50 ทวิ WHT certificate into its receipt
  -> regions        assigns which pages each bill covers; attaches orphan pages
  -> arith          checks the money equations, lowers confidence on failures
  -> strip_internal removes bookkeeping keys, then validate against their schema
```

Both models run on **local Ollama** (`/api/chat`, native API — *not* `/v1`; `repeat_penalty`,
`num_ctx` and `seed` exist only on the native one and are silently dropped by `/v1`).

### Files

| File | Owns |
|---|---|
| `src/ocr_pipeline.py` | rasterising, prompt building, one stage-1 call |
| `src/stage2_extract.py` | the two schemas, the prompt, per-category rules, assemble, validate |
| `src/degeneracy.py` | is a transcript usable — loop / @-burst / empty |
| `src/merge.py` | joining a bill across page breaks |
| `src/certlink.py` | receipt + withholding certificate into one bill |
| `src/regions.py` | which pages a bill covers (`_pages` internal, `regions` on the wire) |
| `src/arith.py` | the money equations |
| `serving/app.py` | HTTP, auth, concurrency, error taxonomy |
| `serving/pipeline.py` | the orchestration above; the only place stages are wired |
| `serving/config.py` | every knob, all from env; nothing else reads `os.environ` |
| `data/category_rules.json` | per-category prompt additions, keyed by account code |
| `eval/test_*.py` | 183 CPU-only checks — no GPU, no server |

### Two schemas, do not conflate

- `reduced_schema()` — the grammar handed to stage 2. Small on purpose.
- `contract_schema()` — the colleague's file minus `DROPPED_FIELDS`, used to validate replies.
  Read **live off disk** each call (cached on mtime), so dropping in a new schema file needs no
  code change and no restart.

Their validator is `additionalProperties: false`: **one stray key voids the entire chunk**, not
just that field.

---

## Invariants — breaking these breaks the webapp

1. `regions` carries **which pages**, not geometry. Every box is `0,0,1000,1000`, forever.
2. Each candidate's own `chunkPageIndex` must appear in its own `regions`.
3. The union of all `regions` must cover every page in the chunk — no page unclaimed.
4. No page claimed twice.
5. Never emit a key their schema does not declare (see `declared_fields()`).
6. Never invent a page number. No page means no `regions` key — a loud failure, not a fake page 0.
7. Confidence is only ever **lowered**, never raised.
8. Never write document content to disk (R18). `DEBUG_DUMP_INVALID` stays false.

---

## Running it

```bash
for t in category arith degeneracy retry regions merge certlink; do .venv/Scripts/python.exe eval/test_$t.py; done
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn serving.app:app --host <today's IP> --port 8000
```

`--host` must be today's LAN IP — never `0.0.0.0`, and never `127.0.0.1` (uvicorn binds one
address, so loopback will not be listening). The IP is DHCP and **moves constantly**; the user
tells the colleague the new IP before each test. This is handled manually and needs no
automation, and no warnings about it.

Windows notes: prefix Python with `PYTHONIOENCODING=utf-8` or cp1252 throws on Thai output.
`pdftoppm`/poppler is **not** installed — render pages with pypdfium2. PowerShell has no inline
env-var prefix. Heredoc patches are a trap: `\n` inside a quoted heredoc stays literal, and it has
silently skipped replacements twice — prefer the Edit/Write tools for multi-line content.

---

## Current state — 2026-09-02

### Live and verified
- `regions` — passes all four of the colleague's criteria on real files
- multi-page bills, certificate folding, orphan-page attachment
- **stage-1 loop guard + re-read** — deployed; toll set 190 to 265 baht; 5/5 runs identical
- **arith guard** — deployed
- **`x-category-id`** — deployed. `data/category_rules.json` finally executes.

### Server
`192.168.253.49:8000`, deadline 600 s. Running all current code as of 2026-09-02.

### Next, in order
1. `payeeType` add `shop` (3 values, per policy 88/2568)
2. `evidence[].role` — colleague confirmed incremental release is safe (see below)
3. `personlink` — ใบรับเงิน + บัตรประชาชน + slip as one bill; same shape as `certlink`
4. toll summing — blocked on three FA decisions
5. phase 07 capacity — not started

---

## Known defects

| | |
|---|---|
| Punch-card toll ticket (45฿) | unread at **every** resolution tried (900/1100/1300/1500/2000). A stage-1 capability limit, not a settings problem. |
| ใบรับเงิน gross misread | `24,826.80` for a true `24,226.80`. Now **caught** by `arith` — 2.93% is not a legal WHT rate. |
| ID number on ใบรับเงิน | read as 12 digits, and the name disagrees with the ID card (`ปรีดา` vs `ปรีชา`). No cross-check exists yet. |
| ID card makes a phantom candidate | one row, every field null. Should not become a ledger row at all. |
| Photocopy double-count | seen once, on a cold-started server (`25.00` twice). Warm runs are stable. Dedupe by `Receipt Running No` is therefore **required**, not optional. |
| Cold start changes answers | n=1. Warm the model before any run whose numbers you intend to quote. |

---

## Contract with the webapp team

Their handover lives in **their repo**, `docs/handover/model/`, on branch **`master`** — the
default branch `codex/adv-clear-implementation` is 37 commits behind and will not have it.

Settled as of 2026-09-01:

- **`x-category-id`** is now sent on every request: lowercase, digits, sanitised to
  `[0-9A-Za-z._-]` capped at 64, omitted when empty. An absent header keeps today's behaviour.
- **`confidence`**: their review UI already thresholds at **`< 0.5`**, so our `0.3` on an
  arithmetically suspect field flags immediately. No UI work needed on their side.
- **`evidence[].role`**: safe to release **one role at a time**. No bill carries `receipt`
  today, so `R-SLIP-001` already fires on everything — partial tagging improves it, not breaks it.
- **`payeeType`** has **three** values: `company` / `shop` / `individual`.
- **`BillCandidate` has exactly 31 keys.**
- **`evidence[].role`** is one of 8: `receipt`, `tax_invoice`, `cash_bill`, `id_document`,
  `transfer_slip`, `exchange_rate_evidence`, `approval_document`, `other`.

Still outstanding:

- **Schema files never delivered** — third round. `bill-extraction.schema.json` and
  `sample-response.json` are generated but were still uncommitted as of their last message. The
  file we hold is dated **2026-08-10**.
- Three FA decisions on merged toll rows: `documentDate`, VAT fields, `originalDocumentNumber`.
- Which role for a document headed **ใบเสร็จรับเงิน/ใบกำกับภาษี** (both at once).

**Their `README.md` is the original acceptance contract and parts of it are stale** — it still
asks for Lao, a 95% gate, a 5-minute lease, 5 concurrent jobs and 300 pages per job. Later
documents supersede all of those. Do not treat it as current.

---

## Domain facts worth not re-deriving

- Thai tax id is 13 digits with a **mod-11 check digit** — verify before trusting one.
  EXAT's `0994000165421` is checksum-valid.
- **VAT is 7%, one rate.** WHT legal rates: 1, 1.5, 2, 3, 5, 10, 15%.
- **Withholding is computed on the pre-VAT subtotal**, never on the VAT-inclusive total.
- `clearingAmount` means different things per document type — it equals `originalTotal` on
  ordinary bills and the net after withholding on a ใบรับเงิน. **There is no single identity to
  test**, which is why `arith` does not test one.
- `vat = 0` is a real value (`Baht(Non Vat)` toll tickets), not a missing one.
- Stage 1 is **greedy** (`temperature 0`): the same image at the same size gives the same output
  every time. Changing `seed` does nothing — **change the image size instead.**
- Ollama shares a prompt cache across requests, so a page's output can depend on what ran before
  it. `ocr_pipeline.unload()` exists to get independent measurements.
- Attachment page-count metadata is unreliable (claimed 611 pages for a 216-page PDF). Verify
  with pypdfium2.

---

## Changelog

Newest first. **Add an entry whenever behaviour changes.**

### 2026-09-02
- **`x-category-id` is read and honoured.** `serving/app.py` parses the header (trimmed, capped
  at 64) and threads it through `run_validated -> run -> extract_page -> s2.extract`, so the
  per-category rules in `data/category_rules.json` execute for the first time. Absent header
  means `None` means the shared prompt — byte-identical to the old behaviour.
- The reply now carries `X-Category-Id` (normalised to digits) and `X-Category-Rule`
  (`applied` / `no-rule-for-this-code` / `none`), so "the header never arrived" and "the header
  arrived but matched nothing" stop looking identical from the webapp side.
- The header needs **no sanitising beyond a length cap**: `category_prompt` uses the value only
  as a dict key and appends *our own* rule text, so it cannot reach the model. Locked in by a
  test. It *is* filtered to `[0-9A-Za-z._-]` before being echoed, because HTTP headers are
  latin-1 and the webapp may legitimately send the Thai label.
- `eval/test_category.py` — 22 checks. Total now **183**.

### 2026-09-01
- `src/arith.py` + `eval/test_arith.py` — money-equation guard. Flags, never repairs; caps
  confidence at 0.3 on implicated fields. 16 real candidates, 0 false positives, 8.8 µs per
  chunk. Wired into `pipeline.run`, **not yet on the server**.
- `serving/asks-2026-09-01.md` — four asks to the colleague. All four now answered.
- Colleague shipped `x-category-id` (we began reading it the next day, see above).

### 2026-08-31 (evening)
- `src/degeneracy.py` + `eval/test_degeneracy.py` + `eval/test_retry.py` — stage-1 loop detector
  and re-read at 1300/2000. `RETRY_TARGET_DIMS` added to config. Deployed.
- Toll set 190 to 265 of 310. Five runs, zero variance, 5/5 loop caught and recovered.
- Log now distinguishes an unread page from a blank one.

### 2026-08-31 (morning)
- `src/regions.py` — page spans, orphan attachment, authoritative `regions` rebuild.
- `INFERENCE_DEADLINE_S` 540 to 600. Their client abort is 660 s; the lease is 900 s and is not
  the number to sit under.
- `relatedDocumentNumber` emitted only when their schema file on disk declares it.
