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
  -> personlink     folds an ID card into the wage receipt it evidences
  -> slips          folds a transfer slip into the payment it proves
  -> doctypes       tags each page's evidence role from its printed heading
  -> payee          decides company / shop / individual from the seller's own name
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
| `src/doctypes.py` | printed heading -> `evidence[].role` (receipt / tax_invoice / cash_bill) |
| `src/payee.py` | `payeeType` — company / shop / individual, from sellerName + sellerTaxId |
| `serving/app.py` | HTTP, auth, concurrency, error taxonomy |
| `serving/pipeline.py` | the orchestration above; the only place stages are wired |
| `serving/config.py` | every knob, all from env; nothing else reads `os.environ` |
| `data/category_rules.json` | per-category prompt additions, keyed by account code |
| `eval/test_*.py` | 341 CPU-only checks — no GPU, no server |

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
for t in payee doctypes slips personlink category arith degeneracy retry regions merge certlink; do .venv/Scripts/python.exe eval/test_$t.py; done
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
- **personlink** — deployed. ID card folds into its wage receipt and supplies the payee's ID.
- **slips** — deployed. Transfer slips detected, folded on an amount match, tagged
  `transfer_slip`. `id_document` tagged too, so `evidence[]` is no longer always empty.
- **doctypes** — deployed. `receipt` / `tax_invoice` / `cash_bill` from the printed heading.
- **payee** — deployed. `shop` exists at last; corrects company/shop/individual deterministically.

### Server
`192.168.253.49:8000`, deadline 600 s. Running all current code as of 2026-09-02.

### Next, in order
1. toll summing — blocked on three FA decisions
2. phase 07 capacity — not started

---

## Known defects

| | |
|---|---|
| Punch-card toll ticket (45฿) | unread at **every** resolution tried (900/1100/1300/1500/2000). A stage-1 capability limit, not a settings problem. |
| ใบรับเงิน gross misread | `24,826.80` for a true `24,226.80`. Now **caught** by `arith` — 2.93% is not a legal WHT rate. |
| ID number on ใบรับเงิน | read as 12 digits. **personlink now supplies it** from the stapled card, which is printed and check-digited. The *name* still disagrees (`ปรีดา` vs `ปรีชา`, ratio 0.897) and is not corrected — only flagged when the two clearly differ. |
| ID card makes a phantom candidate | **fixed** — personlink drops a candidate on a card page that reports no money at all. |
| Photocopy double-count | seen once, on a cold-started server (`25.00` twice). Warm runs are stable. Dedupe by `Receipt Running No` is therefore **required**, not optional. |
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
- **`payeeType`** has **three** values: `company` / `shop` / `individual`. **Ours does too as
  of 2026-09-02.**
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

### 2026-09-02 (night)
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
