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
- **Simplest design that reuses what exists.** Before adding a module, constant, config key
  or loader, check whether one already covers it. Split on *kind*: configuration a person
  changes goes in `data/category_rules.json`, behaviour goes in code — and never write the
  same account code in both, because the stale copy fails silently.
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
  -> personlink     folds an ID card into the wage receipt it evidences
  -> slips          folds a transfer slip into the payment it proves
  -> tolls          sums a trip's expressway tickets into one row (category 5223100 only)
  -> doctypes       tags each page's evidence role from its printed heading
  -> payee          decides company / individual from the seller's own name
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
| `src/personlink.py` | ID card + ใบรับเงิน — cross-check, fold, fill the payee's ID |
| `src/slips.py` | bank transfer slips — detect, fold on amount match, tag the role |
| `src/tolls.py` | expressway tickets — parse, drop photocopies, sum a trip into one row |
| `src/doctypes.py` | printed heading -> `evidence[].role` (receipt / tax_invoice / cash_bill) |
| `src/payee.py` | `payeeType` — company / individual (ภ.ง.ด.53 vs ภ.ง.ด.3), from sellerName + sellerTaxId |
| `serving/app.py` | HTTP, auth, concurrency, error taxonomy |
| `serving/pipeline.py` | the orchestration above; the only place stages are wired |
| `serving/config.py` | every knob, all from env; nothing else reads `os.environ` |
| `data/category_rules.json` | per-category prompt additions, keyed by account code |
| `eval/test_*.py` | 438 CPU-only checks — no GPU, no server |

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
for t in tolls payee doctypes slips personlink category arith degeneracy retry regions merge certlink; do .venv/Scripts/python.exe eval/test_$t.py; done
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

## Current state — 2026-09-03

### Accuracy
**76.7% (497/648)** on the 34-case golden key, their scorer, 2026-09-03, every guard live,
`payeeType` back to two values. Printed 78.2 / handwritten 74.3 / English 73.0 (n=37, a warning
not a measurement).

**Treat 76.7–77.3% as one number.** Four runs of the same 34 cases across two weeks gave 77.3 /
76.7 / 76.9 / 76.7, and roughly 620 of 648 fields are byte-identical between any two of them. The
±9-field churn is on money, date and name fields and swamps every code change measured so far.
**Do not report a 1–4 field move as a regression or a win** — attribute it field by field first,
the way the 2026-09-03 entries do.

`payeeType` on its own: **29/32**. The 3 misses are fall-throughs where neither the name nor a
valid tax id decides, so the model guesses.

### Live and verified
- `regions` — passes all four of the colleague's criteria on real files
- multi-page bills, certificate folding, orphan-page attachment
- **stage-1 loop guard + re-read** — deployed; toll set 190 to 265 baht; 5/5 runs identical.
  Widened 2026-09-03 after transcribing all 138 corpus pages: 8 caught, was 6. p118 went
  514 -> 3514 baht. **Not yet on the server** — restart to deploy.
- **arith guard** — deployed
- **`x-category-id`** — deployed. `data/category_rules.json` finally executes.
- **personlink** — deployed. ID card folds into its wage receipt and supplies the payee's ID.
- **slips** — deployed. Transfer slips detected, folded on an amount match, tagged
  `transfer_slip`. `id_document` tagged too, so `evidence[]` is no longer always empty.
- **doctypes** — deployed. `receipt` / `tax_invoice` / `cash_bill` from the printed heading.
- **payee** — deployed. `company` / `individual` only, deterministically, from sellerName then a
  checksum-valid tax id. State bodies (`กรม…`, `การทางพิเศษ…`, `โรงพยาบาล…`) → `company`.
  **`shop` was added 2026-09-02 and removed 2026-09-03** — see the changelog before re-adding it.
  **Not yet on the server** — restart to deploy.
- **tolls** — deployed. A trip's tickets sum to one row; photocopies dropped by running number.

### Server
`192.168.54.43:8000`, deadline 600 s. Running all current code as of 2026-09-02.
The IP moved twice today (`.61.45` -> `.253.49` -> `.54.43`). A bind failure that says
*could not bind on any address* usually means the address is gone, not that the port is held —
check `Get-NetIPAddress` before killing processes.

### Next, in order
1. **phase 07 capacity** — **D6 measured**, see `plan/07-capacity.md` section 1b. Remaining:
   confirm the three UNCONFIRMED targets with the colleague, ask FA how often a batch is run,
   then write the server spec. Concurrency 2/5 is not testable here — VRAM says it cannot fit.
2. Confirm the three toll defaults with FA (below); each is one constant in `src/tolls.py`

---

## Known defects

| | |
|---|---|
| Punch-card toll ticket (45฿) | unread at **every** resolution tried (900/1100/1300/1500/2000). A stage-1 capability limit, not a settings problem. |
| ใบรับเงิน gross misread | `24,826.80` for a true `24,226.80`. Now **caught** by `arith` — 2.93% is not a legal WHT rate. |
| ID number on ใบรับเงิน | read as 12 digits. **personlink now supplies it** from the stapled card, which is printed and check-digited. The *name* still disagrees (`ปรีดา` vs `ปรีชา`, ratio 0.897) and is not corrected — only flagged when the two clearly differ. |
| ID card makes a phantom candidate | **fixed** — personlink drops a candidate on a card page that reports no money at all. |
| Photocopy double-count | **fixed for tolls; not a defect elsewhere.** `tolls.parse_tickets` dedupes by `Receipt Running No`. Swept all 138 corpus pages 2026-09-03: exactly one non-toll photocopy exists (p26, a receipt printed twice on one sheet) and **stage 2 already returns one candidate for it**. No general dedupe was built — the failure does not reach the output. Tolls are the exception because there the model must judge *which* of several different tickets are copies. |
| Cold start changes answers | n=1. Warm the model before any run whose numbers you intend to quote. |
| Slip detection unverified for wage transfers | All six real slips are Kasikorn K+ **bill-payment** slips. Markers were chosen bank- and purpose-neutral and score 0 false positives on 105 pages, but no real `โอนเงิน` wage slip has ever been tested. |
| 6 of 58 corpus pages get no role | Honest misses, not misclassifications: a Trip.com receipt with no Thai heading, a toll ticket whose heading OCR dropped, two logo-heavy pages, and a **withholding certificate** — which genuinely has no role among the contract's eight. Untagged is the correct answer for all of them. |

---

## Contract with the webapp team

Their handover lives in **their repo**, `docs/handover/model/`, on branch **`master`** — the
default branch `codex/adv-clear-implementation` is 37 commits behind and will not have it.

Settled as of 2026-09-01:

- **`x-category-id`** is now sent on every request: lowercase, digits, sanitised to
  `[0-9A-Za-z._-]` capped at 64, omitted when empty. An absent header keeps today's behaviour.
- **`confidence`**: their review UI already thresholds at **`< 0.5`**, so our `0.3` on an
  arithmetically suspect field flags immediately. No UI work needed on their side.
- **`evidence[].role`**: safe to release **one role at a time**. Settled 2026-09-02: **keep
  every role detected** — a page headed ใบเสร็จรับเงิน/ใบกำกับภาษี gets two entries, not a
  choice. Verified against their schema: `evidence` has no uniqueness constraint and a
  double-tagged page validates.
- **`payeeType`** — **ours emits two: `company` / `individual`.** A round-3 list of theirs is
  recorded here as having carried a third, `shop`, but that document never reached us and **their
  schema file does not declare `payeeType` at all**, so nothing on their side depends on it.
  Emitting fewer values than a consumer declares is always safe. We tried `shop` for one day and
  removed it — the field picks ภ.ง.ด.3 vs ภ.ง.ด.53 and `shop` answers neither. **If they insist on
  three, ask what a `shop` files before implementing it.**
- **A ninth `evidence[].role`, `withholding_certificate`,** was agreed 2026-09-02 for the
  50 ทวิ certificate. **Their schema file does not declare it yet**, so we detect the page and
  withhold the tag — emitting an undeclared enum value fails validation and voids the whole
  chunk. It appears by itself the moment the new file lands.
- **`BillCandidate` has exactly 31 keys.**
- **`evidence[].role`** is one of 8: `receipt`, `tax_invoice`, `cash_bill`, `id_document`,
  `transfer_slip`, `exchange_rate_evidence`, `approval_document`, `other`.

Still outstanding:

- **Schema files never delivered** — third round. `bill-extraction.schema.json` and
  `sample-response.json` are generated but were still uncommitted as of their last message. The
  file we hold is dated **2026-08-10**.
- Three FA decisions on merged toll rows: `documentDate`, VAT fields, `originalDocumentNumber`.
- Which role for a document headed **ใบเสร็จรับเงิน/ใบกำกับภาษี** (both at once).

**Their `README.md` is the original acceptance contract and parts of it are stale** — but only
*parts*, and the difference matters:

| README says | status |
|---|---|
| Lao support, 95% accuracy gate | dropped by the user |
| 5-minute worker lease | **superseded → 900 s**, plus a 660 s client abort, confirmed 2026-08-28 |
| ≤ 40 MB / 40-page chunks | **not superseded.** Still their stated requirement |
| 300 pages per job, 5 concurrent | **not superseded.** Ours is `MAX_CONCURRENT=1` |

`serving/config.py` used to call a 7-page chunk "their ceiling". **Nothing they sent says that** —
it was the size of the test files they happened to send, written down as a requirement. Retracted
2026-09-02. Do not size hardware against the bottom three rows until they are reaffirmed;
`plan/07-capacity.md` section 1 carries the same table.

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

### 2026-09-03 (two loops the guard was missing, found by transcribing the whole corpus)
- **`src/degeneracy.py` catches 8 of 138 corpus pages, up from 6.** Both new catches are real
  losses, and one of them is the largest single miss measured on this project.
- **The corpus was only ever a third transcribed.** `eval/transcripts/` holds 47 pages; the three
  sample PDFs are **138**. Transcribing the other 91 (stage 1 only, 34 min) is what found these.
- **p118 — a handwritten บิลเงินสด worth 3,514 baht was being read as 514.** Not a blank: a
  plausible wrong number, at confidence 0.95, on a cash bill with no VAT or WHT line for `arith`
  to check it against. `sellerName` was TEAM (the buyer) misread as `ซีพีเอฟ`. Stage 1 emitted
  the table's *header row* ~50 times instead of the one filled row.
- **`MIN_SEGMENT_CHARS` 12 -> 8.** Every repeated piece on p118 is a column label of 6-11 chars
  (`รายการ`, `DESCRIPTION`, `หน่วยละ`, `貨名`), so a floor of 12 discarded all of them, left
  9 distinct segments and scored the page 1.00. At 8 it is 218 segments at 0.06.
- **p013 — `segments()` now splits on newlines as well as tags.** 10,306 chars in which one
  footer line repeats 54 times, separated by newlines, not tags. Tag-splitting saw 15 unique
  segments and cleared it. Both delimiters are needed and catch opposite failures: page 198
  arrives as one enormous line with no newline at all, so newlines alone would miss it.
- **No threshold moved.** `MAX_REPEAT_RATIO` and `MAX_COMPRESSION` are untouched; both fixes are
  to *what counts as a segment*. The conjunction still stands.
- **Re-reading earns its keep, measured per size.** p118 at 1500 px yields `514`; at 1300 and
  2000 px it yields `3,514`, and 2000 px also recovers the full 13-digit tax id. End to end after
  the fix: the guard fires, re-reads at 1300, and the bill comes back **3514**.
- **Zero false positives** on 138 corpus pages and on the 47 golden transcripts. The closest
  genuine page still clears both limits by 2.4x (`test6`, ratio 0.87 vs 0.35, compression 0.29
  vs 0.12).
- **A compression-only clause was tried first and rejected by an existing fixture.** `both-01`
  asserts one metric alone may never condemn a page, and it was right: a long itemised invoice
  compresses as hard as a loop. The fixture caught the bad fix before it shipped.
- **The photocopy dedupe was investigated and deliberately not built.** Page 26 carries the same
  receipt printed twice, and stage 2 already returns **one** candidate for it. The failure a
  general rule would guard against does not reach the output. Toll tickets remain the exception
  because there the model must decide *which* of several different tickets are copies of each
  other -- that is the judgement it got wrong at 70-vs-45 baht, and `tolls.parse_tickets` still
  owns it.
- Also checked and left alone: a 50 ทวิ certificate prints ฉบับที่ 1 and ฉบับที่ 2 on one sheet by
  design, and a Transmittal form is one very wide table row. Both look repetitive and neither is.
- `eval/test_degeneracy.py` +12 checks (split-01..05), total now **438**.

### 2026-09-03 (later — `shop` removed after one day)
- **`payeeType` is back to `["company", "individual"]`.** Owner's decision, and the reasoning is
  the law, not the score: **the field picks the withholding return — ภ.ง.ด.3 for a natural person,
  ภ.ง.ด.53 for a juristic one — and that split is binary.** A ร้าน is not a third category;
  unregistered it files ภ.ง.ด.3, registered as หจก./บริษัท it files ภ.ง.ด.53. Told a payee was a
  `shop`, FA still could not pick a form.
- **Two supporting claims for `shop` did not survive checking.** "Policy 88/2568" was cited in
  `payee.py` and `stage2_extract.py` as the justification; **it appears in no document we hold** —
  not their schema, README, scorer, or handover — only in text written here. And the webapp team's
  round-3 list that carried `shop` never reached us; their schema file still does not declare
  `payeeType` at all. **Do not re-add the value on the strength of either claim.**
- **A recorded "correction" was probably an error.** `ร้านข้าวต้มโกยาว` `individual` -> `shop` was
  logged as one of two genuine fixes. For an unregistered porridge shop, `individual` was very
  likely right, and the guard now leaves it alone.
- **The score did not improve, and that is not the argument.** Three runs of the same 34 cases:
  **76.7% (3-value) / 76.9% (state fix) / 76.7% (2-value)**. 622 of 648 fields identical between
  the last two; 8 gained, 9 lost, and every loss is churn on unrelated fields (documentDate,
  originalTotal, sellerName) because changing the grammar re-rolls the whole extraction.
  **The spread across all three runs is inside the churn — treat 76.7-76.9% as one number.**
- **`payeeType` itself: 28/32 -> 29/32 -> 29/32.** The state rule earned that field; removing
  `shop` held it while making the value answerable. The 3 remaining misses are fall-throughs where
  neither the name nor a valid id decides (`วังพุดตาล`), and the model guesses.
- **`STATE_PREFIXES` stays** — a government body is juristic, so ภ.ง.ด.53, so `company`. It was
  written to fix a `shop` bug and outlives it: without it a state name matches nothing and the
  model answers unchecked on a payee whose form is not in doubt.
- **A bare `ร้าน…` now deliberately settles nothing** and falls through to the tax id, whose first
  digit answers the question outright. `ร้านค้าสวัสดิการกรมทางหลวง` is the case that proves the
  start-anchoring matters: its own registration decides, not the department's.
- **Dropping an enum value is safe on the wire** — only an *extra* key or an undeclared value
  voids a chunk. Emitting fewer than a consumer declares never does.
- **`other` was considered and rejected** — it cannot pick a form either, so it is `null` with
  extra steps, and it reads as a positive classification that would stop reviewers looking. For
  Agoda (a Singapore Pte Ltd, already `company` via `PTE`) the real question is **ภ.ง.ด.54, which
  turns on residency, not on payee type** — a separate signal if FA ever needs it, and derivable
  from the absence of a valid 13-digit Thai id. **Ask FA whether they file ภ.ง.ด.54 at all first.**
- `eval/test_payee.py` rewritten rather than trimmed: the `shop` fixtures now assert the opposite
  answer to the same question. 64 checks, total now **426**.

### 2026-09-03 (the guards, scored at last)
- **The whole pipeline was re-scored on the 34-case golden key: 76.7%, then 76.9% after a fix.**
  The standing number, 77.3%, was measured 2026-08-20 and predates every guard. This is the first
  time loop-retry, arith, personlink, slips, doctypes, payee, tolls and the category header have
  been measured together against the answer key.
- **620 of 648 fields came back byte-identical** to the run two weeks earlier. Stage 2 is far
  more stable across code changes than the R5 determinism worry implied.
- **The eight guards did not cost accuracy.** 7 fields gained, 11 lost; nine of the eleven are
  ordinary run-to-run churn on money and name fields. Net of the churn: −2, and both of those
  were `payeeType`, from adding `shop`.
- **`STATE_PREFIXES` added to `src/payee.py`** — `กรม` / `กระทรวง` / `องค์การ` / `เทศบาล` /
  `มหาวิทยาลัย` / `โรงเรียน` / `โรงพยาบาล` / `การทางพิเศษ` / `การไฟฟ้า` / `การประปา` /
  `การรถไฟ` / `การท่าเรือ` / `ธนาคารแห่งประเทศไทย` / `สำนักงานเขต` and three English forms, all
  → `company`. **`กรมทางหลวง` was coming back as `shop`.**
- **The mechanism is worth remembering: widening an enum widens what an unguarded fall-through
  can produce.** `classify()` returns `None` for a state name, so the model's own answer stands —
  harmless while the grammar held only `company` and `individual`, wrong the moment `shop`
  existed. Adding a value to `PAYEE_TYPES` silently created this; no test could have caught it,
  because nothing was testing the fall-through.
- **Anchored at the start**, like `SHOP_PREFIXES` and unlike `COMPANY_MARKERS`, because these are
  ordinary Thai nouns mid-name. `บริษัท โรงพยาบาลกรุงเทพ จำกัด` stays a company via `จำกัด`;
  `ร้านค้าสวัสดิการกรมทางหลวง` stays a shop. A substring test breaks both, opposite ways.
- **Bare `สำนักงาน` and bare `การ` are deliberately excluded** — `สำนักงานบัญชี` and
  `สำนักงานทนายความ` are private practices whose sole practitioner is `individual`, and `การ`
  prefixes ordinary Thai nouns. Names they would have matched keep today's behaviour.
- `eval/test_payee.py` +21 checks, total now **424**. One existing fixture asserted
  `การทางพิเศษแห่งประเทศไทย` settles nothing from its name; that was correct before this change
  and is now the thing being fixed, so it moved rather than being deleted.
- **The plan's status blocks were 15 days stale and were rewritten** (00, 01, 02, 05, 06, 08).
  Two numbers had been read back out of them and quoted as current when both were superseded:
  "12 of 40 golden cases" (it is 34) and "62% accuracy" (it was 77.3%). Rule added to the
  overview: **when a status block and a dated finding disagree, the finding wins.**

### 2026-09-02 (phase 07 · D6 measured)
- **`T` = 57.1 s/page**, 12 real pages, single stream, RTX 5060 Laptop 8 GB. Stable across
  document types: 56.4 / 57.3 / 58.0. Peak VRAM **5351 MiB of 8151**; peak host RAM 232 MiB;
  rasterising 70 ms/page.
- **Stage 2 costs more than stage 1** — ~30 s/page against ~25 s. This inverts the plan's
  optimisation list, every item of which aimed at the vision model. The file's old note said
  stage 2 was 13-20 s/page; that is out of date. Constrained decoding is not free.
- **Every deterministic guard together costs 0.1% of a request** — merge, certlink, personlink,
  slips, doctypes, payee, tolls, arith and regions sum to 0.2-0.4 s across a whole file, against
  ~57 s for one page. The claim that guards are free is now measured, not asserted.
- **`MAX_CONCURRENT=1` is forced, not cautious.** One stream holds 5.3 GB; two need ~10.7 GB
  against 8.1 GB. No software change moves that, which makes it the hardest number in the
  procurement case.
- **Up to 10 pages per chunk fits the 600 s deadline today** (571 s). 20 pages needs 2 parallel
  streams, 40 needs 4 — and 40 × 5 concurrent needs ~20, i.e. a server.

### 2026-09-02 (phase 07 prep)
- `plan/07-capacity.md` targets corrected before measuring. It computed a procurement spec
  against a **5-minute lease that no longer exists** (900 s since 2026-08-28), used a 240 s
  budget where our deadline is 600 s, and recommended lowering `TARGET_DIM` from 1800 — the size
  that makes stage 1 degenerate, and the opposite of what the 1300 px retry measurement shows.
- **A claim of ours was retracted.** `serving/config.py` asserted a 7-page chunk was "their
  ceiling". No message from the webapp team says that; it was the size of their test files
  written down as a requirement. Chunk size, pages per job and concurrency are all still only
  in their stale README, and the plan now marks them UNCONFIRMED rather than superseded.
- Measurement instruments the pipeline from a script rather than adding timers to production
  code — per-stage split, peak VRAM and peak host RAM on real clearing sets.

### 2026-09-02 (late night)
- `src/tolls.py` + `eval/test_tolls.py` (50 checks). A trip's expressway tickets now sum to one
  ledger row, **only when the request carries `x-category-id: 5223100`**. Any other category, or
  no header, keeps one row per ticket exactly as before.
- **Photocopies are dropped by `Receipt Running No`.** The ticket prints
  `กรุณาทำสำเนาเพื่อนำไปใช้ในธุรกรรมต่อไป` on itself, so a page routinely carries each ticket
  twice: page 200 holds three ticket images and two real tickets. Counting images gives 70 baht
  where the page is worth 45.
- Two layouts parsed: EXAT/กรมทางหลวง flat text (`Receipt Running No :`) and BEM's HTML table
  (`<td>No.</td>`). A ticket whose number was read but whose amount was not is dropped, never
  guessed — an unknown amount would make the sum quietly wrong.
- **The three FA decisions are implemented as defaults, each a single constant.** `documentDate`
  = earliest ticket (page 200 alone mixes 13/07 and 20/07); `amountBeforeVat`/`vat`/`vatRate` =
  null (page 201 carries one `Baht(Vat Included)` ticket and one `Baht(Non Vat)` ticket, so no
  single split is honest); `originalDocumentNumber` = null with every running number preserved in
  `lineItems[].description`.
- Runs **before** `attach_orphans`, so `attach_regions` rebuilds `regions` from the merged span.
  Setting `regions` inside tolls would let a toll page adopted by a neighbouring bill be claimed
  twice, breaking invariant 4.
- Corrects an earlier note: **page 201 is two tickets (80 + 35), not one 115 ticket**. The old
  "ยอดถูก ช่องผิด" reading was the model folding two tickets into one bill's base and vat — which
  is why `arith` flagged 228% VAT on it.
- **The BEM amount cell carries its unit** — `<td>25.00 บาท</td>` where EXAT writes flat text
  with none. The first pattern required a bare number, so page 198's two tickets were read,
  found to have no parseable amount, and dropped by the safety rule. Live showed 190 where the
  pages are worth 265. Correct behaviour from a pattern that was too strict; cost 75 baht.
- **`X-Category-Rule` now names what fired** — `prompt` / `tolls` / `prompt,tolls` /
  `no-effect` / `none`. It used to answer only "is there a stage-2 prompt rule", so a request
  that had just summed five tickets came back `no-rule-for-this-code`: true of the prompt, and
  wrong about the request. **Tell the colleague — the earlier note documents the old values.**
- The **un-summed** path is not stable run to run: the same four pages gave 6 rows / 265 once
  and 5 rows / 245 the next time, because stage 2 moves where a bill starts. The summed path
  counts tickets by running number and lands on 265 every time. An argument for summing beyond
  FA's convenience.
- **The travel account code is written down once.** `5223100` was both a JSON entry and a
  constant in `src/tolls.py`; FA renumbering it would have needed two edits, and doing only the
  JSON one would have looked sufficient while silently switching the summing off. The switch is
  now `"sumTollTickets": true` in `data/category_rules.json`, read through the `load_categories()`
  that file already had — no new loader, no new file. The parsing stays in Python: expressing two
  printers' ticket layouts in JSON would mean inventing a rule language.
- Total now **403**.

### 2026-09-02 (night)
> **`shop` was removed the next day.** Everything below about the third value is superseded by the
> 2026-09-03 entry; the module, the id rule and the confidence policy all still stand.
- `src/payee.py` + `eval/test_payee.py` (41 checks). `PAYEE_TYPES` is now
  `company / shop / individual` — the grammar physically could not emit `shop` before, so every
  shop in the system was filed as something else.
- Decided deterministically from `sellerName` + `sellerTaxId`, **not** from the page: `บริษัท`
  appears on 45 of 54 real pages because TEAM is the buyer on every one, so a raw-text rule
  would call everything a company. The first digit of a valid Thai id settles the rest — `0` is
  a juristic person, `1`-`8` a natural one — and the check digit must pass first.
- Name before id, because an id cannot see a shop: a registered ร้าน stays `shop`.
  Personal titles outrank both. **Corrects** rather than only filling nulls, since leaving the
  model's answer would preserve the very error this fixes.
- Measured on every real seller name in saved responses: **2 corrections, both genuine errors**
  (`ร้านข้าวต้มโกยาว` individual→shop, `การทางพิเศษแห่งประเทศไทย` individual→company),
  10 agreements, 3 correctly left alone. Zero wrong changes.
- `stage2_extract.declared_roles()` added, mirroring `declared_fields()`. `doctypes` detects the
  50 ทวิ certificate and tags it `withholding_certificate` **only when their schema declares the
  value** — see Contract section. Total now **341**.

### 2026-09-02 (evening)
- `src/doctypes.py` + `eval/test_doctypes.py` (33 checks). Each page's printed heading now sets
  its `evidence[].role`: ใบเสร็จรับเงิน / ใบรับเงิน / ใบรับค่าผ่านทางพิเศษ -> `receipt`,
  ใบกำกับภาษี -> `tax_invoice`, บิลเงินสด / CASH SALE -> `cash_bill`.
- **A page headed both is tagged both.** 15 of 58 real pages carry ใบเสร็จรับเงิน *and*
  ใบกำกับภาษี — 26%, the ordinary case for Thai receipts, since one paper legally is both.
  Agreed with the webapp team; their schema accepts it (no uniqueness constraint, verified).
- **`เลขที่ใบกำกับภาษี` is stripped before matching.** 7 of the 21 pages mentioning ใบกำกับภาษี
  only *cite* an invoice number they settle. Tagging those `tax_invoice` would mislabel a third
  of them. Measured: 0 wrong assignments after stripping.
- Read from the transcript, not from the extracted `documentType` field — the heading is fixed
  wording that survives OCR, the field is a model output that is null more often than wrong.
- Unmapped on purpose: ใบส่งของ (delivery note) and ใบแจ้งหนี้ (billing note) prove goods arrived
  or payment was requested, not that money moved. They would have to be `other`, and `other`
  claims we classified the page. Coverage 52/58; the 6 misses are listed under Known defects.
- **`CASH SALE` only counts when nothing else claims the page.** Found on the live endpoint: a
  page headed `ใบเสร็จรับเงิน / ใบกำกับภาษี OFFICIAL RECEIPT / TAX INVOICE CASH SALE` was tagged
  all three roles. The form lists every type it can serve as, and the cash in it is the payment
  method (`☑ Cash เงินสด` further down). But `CASH SALE` cannot simply be dropped — three corpus
  pages are Chinese-Thai shop forms headed only `CASH SALE 現兑單`. So the English heading counts
  only when no ใบเสร็จรับเงิน / ใบกำกับภาษี heading is present. Corpus distribution unchanged.
- Total now **299**.

### 2026-09-02 (later still)
- `src/slips.py` + `eval/test_slips.py` (36 checks). Bank transfer slips are detected, folded
  into the payment they evidence, and tagged `transfer_slip`. personlink now tags `id_document`,
  and `regions.add_evidence` builds the entries. **`evidence[]` is no longer always empty**, so
  the webapp's `R-SLIP-001` has something to read for the first time.
- **A slip is not an ID card: it states an amount.** Left alone, stage 2 turns it into a bill for
  money the receipt already reports — a double count. But always dropping it is worse, because
  `R-SLIP-001` exists precisely for payments that have *no* receipt, where the slip is the only
  record. So a slip is folded **only when exactly one bill states the amount it paid**; no match
  or several leaves it standing as its own row, still tagged.
- `clearingAmount` is matched before `originalTotal`: the bank moves the net after withholding,
  which is what a wage slip shows.
- Markers measured: 3 of them hit **6/6 real slips and 0 of 105 other real pages**. Bare `สลิป`
  is deliberately *not* a marker — the wage receipt prints `สลิปโอนเงินของธนาคาร` in its
  attachment note, and 8 corpus pages mention a slip without being one.
- **Verification limit:** all six real slips are Kasikorn K+ *bill-payment* slips paying M-Flow.
  No real wage transfer slip exists in the corpus. The markers were chosen bank- and
  purpose-neutral (`สำเร็จ` covers โอนเงินสำเร็จ / ทำรายการสำเร็จ as well as จ่ายบิลสำเร็จ) and the
  bank list is broader than what could be tested, but the true-positive side is proven only for
  K+ bill payments.
- **One slip, one bill.** A bill claimed by more than one slip keeps none of them. Found by the
  live run, not by the fixtures: pages 195 and 196 are both 30.00 with different references and
  dates, and both folded into the single 30.00 toll ticket — claiming one payment had two slips
  and dropping 30 baht from the chunk total. Same refusal `certlink` makes when two purchases
  fit one certificate.
- Total now **263**.

### 2026-09-02 (later)
- `src/personlink.py` + `eval/test_personlink.py` (40 checks). An ID card stapled behind a
  ใบรับเงิน now folds into that payment: its page joins the bill, a phantom all-null row on
  the card page is dropped, and the payee's `sellerTaxId` is taken from the card when the
  bill has none that passes its check digit.
- **It does not re-derive the pairing.** On the one real pair (P06690 p20-21) the content
  cannot: the receipt's only valid 13-digit number is TEAM's own, and the two spellings of
  the name score 0.897 — just under `certlink.names_agree`'s threshold, so it returns
  *undecided*. The pairing comes from stapling order, the same rule `attach_orphans` uses.
- Confidence follows the *pairing*, not the digits: 0.9 when the names agree, **0.45** when
  undecided (below the review UI's 0.5 cut, so a human sees it). Names that clearly
  disagree fill nothing and distrust both fields.
- Total now **223**.

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
