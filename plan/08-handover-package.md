# Phase 08 — Handover Package

**Goal:** answer all six items in their README §1 so the colleague can write their ~50-line
adapter and connect.
**Inputs:** everything.
**Exit gate:** package sent; `AI_PROVIDER=local` works against our endpoint end-to-end.

> ### Status 2026-08-19 — the contract half is built and ready to send
>
> `../handover/` holds a README describing all 29 fields plus **six worked sample responses**
> (tax invoice, individual receipt, cash bill, toll receipts, a not-a-bill page, and live output).
> `handover.zip` at the project root is the same thing packaged. It can go to the colleague today.
>
> **Send it with three things said out loud**, because the samples do not say them:
> 1. **Eleven fields are new** (buyer block, `documentBookNumber`, `payeeType`, `paymentMethod`,
>    `discount`, `vatExemptAmount`, `vatRate`, `withholdingTaxRate`) and are not implemented on his
>    side yet. They came from FA, not from his contract — we produce the data, so the shape is
>    ours; only the naming needs agreeing.
> 2. **`confidence` is not predictive** (F16). If the review UI plans to highlight low-confidence
>    fields, it will point FA at the wrong rows.
> 3. ~~Which account-code spelling does the webapp send?~~ **Decided 2026-08-20: `5122100`,
>    digits only.** No longer a question to ask — `category_key()` normalises `5-122-100` and a
>    code with its Thai label attached to the same key, so either spelling works.
>
> **Not ready:** D1's endpoint and D6's chunk timing, both waiting on phases 06 and 07. The exit
> gate — `AI_PROVIDER=local` working end to end — is therefore still open. What can be sent now is
> the contract, not the connection.

---

## 1. The six deliverables

| # | Item | Source phase | Status |
|---|---|---|---|
| D1 | Endpoint URL, auth method, one sample request/response pair | 06 | |
| D2 | Model name + version (recorded on every extraction for audit) | 03 | |
| D3 | Constrained-decoding file actually used (GBNF or JSON-Schema config) | 02 | |
| D4 | Accuracy report from their scorer, split by language and handwriting | 05 | |
| D5 | Bounding box: supported or not | 03 | |
| D6 | Real elapsed time on a 40 MB / 40-page chunk | 07 | |

---

## 2. Package layout

```
handover-to-webapp/
  README.md                     # everything below, in one place
  api/
    sample-request.sh           # runnable curl
    sample-response.json        # from a real run
    sample-response-empty.json  # the billCandidates: [] case
    openapi.json
  decoding/
    bill-extraction.schema.json # their file, unmodified, with copy date + hash
    grammar.gbnf                # if extractable
    decoding-config.md          # backend, flags, and constraints the grammar does NOT enforce
  accuracy/
    report.md                   # D4, stratified
    golden-summary.md           # case counts per stratum — NOT the documents themselves
  performance/
    benchmark.md                # D6 + concurrency results, labelled with hardware
    server-spec.md              # what to buy to actually meet R15
  runbook.md                    # start, stop, health check, common failures
```

**`golden-summary.md`, not the golden set.** The documents contain employee names, tax IDs and
bank account numbers. Send counts and strata, not content. If they need to audit the ground
truth, that is an in-person review on the machine, not a folder handed over.

---

## 3. What the covering README must say plainly

The temptation at handover is to lead with the best number. Do the opposite — lead with the
limits, because those are what will surprise them in production.

State explicitly:

1. **Where we do not meet the contract.** Almost certainly R15 on laptop hardware, possibly R14
   on some strata, possibly R7 depending on how question 1 resolved. Each with a number and a
   remedy.
2. **Hardware every measurement came from.** A laptop timing presented without that label will
   be read as a production number and planned around.
3. **Which schema constraints the grammar does not enforce** (phase 02 §3) and that we therefore
   validate with `jsonschema` before responding. They should know the belt-and-braces exists,
   because if they ever swap our backend the braces come off.
4. **Determinism scope.** Deterministic for a pinned deployment. If the serving config changes,
   output can shift. This is the answer to question 7 and they need it for their retry logic.
5. **`suggestedCategoryId` returns `null`** until we have their category vocabulary (question 4).
   Not a bug, a pending input.
6. **Strata with small sample sizes.** "58% on Lao handwriting (n=12)" is a warning, not a
   measurement, and it must be labelled as such so nobody plans around ±1 case.

---

## 4. Integration test with the colleague

Do this together, not over email. Agree a slot and run it live:

1. They set `AI_PROVIDER=local`, `LOCAL_VISION_URL`, `LOCAL_VISION_TOKEN`
2. Push one real chunk through their full pipeline
3. Confirm their Zod validator accepts our response — **their validator, not our `jsonschema`.**
   Zod `.strict()` and JSON Schema `additionalProperties: false` are close but not identical, and
   the only thing that matters is what their code accepts.
4. Confirm their reviewer UI renders the bounding boxes where the data actually is. If we are
   emitting whole-page placeholders, watch them see it — better a disappointed colleague in a
   test than a confused FA reviewer in production.
5. Confirm their queue's retry behaviour matches our error taxonomy: force a `429` and a `400`,
   and check the queue retries the first and stops on the second.
6. Confirm `X-Model-Name` / `X-Model-Version` reach their audit log.

Item 3 is the one that most often fails at this stage. Ask for their Zod schema or a validation
endpoint early — before phase 06 is finished, not at handover.

---

## 5. Runbook

Whoever is on call is unlikely to be the person who built it. Cover:

- start / stop the model backend and the API
- health check endpoint and what a healthy response looks like
- where logs go and what they contain (and what they deliberately do not — no document content)
- **common failures**: model not loaded (`503`), OOM under concurrency (`429`/`500`),
  schema validation failure after retry (`500`), deadline breach (`504`)
- how to update the model tag, and the rule that changing it **requires rerunning phase 05
  scoring** before it goes live — a silent model swap invalidates the accuracy report the whole
  contract rests on
- what to do when they send a new `bill-extraction.schema.json`: regenerate the grammar, rerun
  phase 02's validity check, rerun phase 05 scoring. §4 says do not hand-edit the schema, and
  that applies to us as much as to them.

---

## 6. Task list

- [ ] All six deliverables complete
- [ ] Package assembled per §2
- [ ] Covering README leads with limits, per §3
- [ ] Golden documents confirmed **excluded** from the package
- [ ] Their Zod validator obtained and tested against our output **before** handover
- [ ] Live integration session run with the colleague
- [ ] Error taxonomy verified against their actual queue behaviour
- [ ] Runbook written
