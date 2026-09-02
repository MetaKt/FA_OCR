Subject: ADV Clear — local model ready to test, contract + connection details

Hi,

The local model is ready for us to try together. Attached is `handover.zip` — the field contract
and six worked sample responses.

## Connecting

```
AI_PROVIDER        = local
LOCAL_VISION_URL   = http://192.168.61.45:8000/v1/extract
LOCAL_VISION_TOKEN = (sending separately)
```

`POST` the raw file bytes with `Content-Type: application/pdf` (or `image/png` / `image/jpeg`) and
`Authorization: Bearer <token>`. A `200` body is exactly `{"billCandidates": [...]}` and nothing
else — I kept metadata out of the body because your validator is strict at the root. It goes in
headers instead: `X-Model-Name`, `X-Model-Version`, `X-Processing-Ms`, `X-Request-Id`.

I'll start the server before we get on the call. It only runs while I'm running it — my laptop,
not a service.

## Five things worth knowing before you write the adapter

1. **It's `http`, not `https`.** Your README writes `LOCAL_VISION_URL` as `https://`. If your
   client requires TLS, tell me now — it's a laptop on the office network and a certificate is a
   separate job.

2. **Send at most ~10 pages per request.** It runs at ~50 s/page on my GPU, against a 540 s
   internal deadline. A 40-page chunk would be ~33 minutes, so it returns a retryable
   `504 DEADLINE_EXCEEDED` rather than silently blowing your lease — and the message names the
   page it got to, like *"exceeded 540s while reading page 9 of 20"*, so you can size the chunk
   from the error itself.

   Two things I'd ask. **What is your worker lease, exactly?** I raised our deadline from 240s to
   540s on the strength of "longer than five minutes", and I'd rather set it to your real number
   minus a minute than guess. And **fewer pages is genuinely better**, even though the deadline
   now allows ten: one request holds the only inference slot for its whole duration, so a
   nine-minute extraction means everything else gets `429` for nine minutes. Ten to fifteen pages
   also fails cheaper and retries cheaper than forty.

   If you'd rather not split at all, the alternative is a job-submit/poll API — a bigger change,
   and I didn't want to invent it without asking.

3. **One request at a time.** A second concurrent request gets `429` with `retryable: true`,
   immediately rather than queued. 8 GB of VRAM won't hold two copies. R15 wants five concurrent
   and that's a hardware conversation, not a code one.

4. **`{"billCandidates": []}` is a normal `200`, not an error.** About a third of a real clearing
   set isn't bills — ID-card copies, FM-FA-05 cover sheets. `d1-sample-response-empty.json` in
   the zip is a real one. Worth checking your Zod schema accepts an empty array, because if it
   doesn't, a third of every upload fails.

5. **The four buyer fields come from a constant, not from OCR.** We're the buyer on every bill in
   a clearing set, so I fill `buyerName` / `buyerAddress` / `buyerTaxId` / `buyerBranch` from a
   stored value rather than re-reading them off a photocopy. It's worth 60 of 648 fields. It's
   evidence-gated: a page that doesn't mention TEAM anywhere — a motorway toll slip — returns
   `null` for all four, which is correct. `confidence` is `1.0` on those four, so don't use them
   to judge how well a page was read.

## Where accuracy actually stands

**77.3%** (501 of 648 fields), measured with your scorer on 34 pages across nine document types.

The headline undersells it. `currency`, `discount` and `paymentMethod` are at 100%,
`originalTotal` 91%, the VAT and withholding rates 94–97%. **51 of the 147 misses are
`sellerName` and `sellerAddress` alone**, where exact matching scores a one-character slip the
same as a blank — and FA fixes those by eye in seconds.

Printed 79%, handwritten 74%.

## Three limitations, stated plainly

- **`confidence` does not predict correctness.** I measured it. If the review UI highlights or
  sorts by low confidence, it will point FA at the wrong rows. Don't build on it yet.
- **Output isn't byte-identical between runs.** Both stages are pinned to `temperature: 0` with a
  fixed seed and it still varies by a field or two. Deterministic for a pinned deployment is the
  honest claim; treat it as such in your retry logic.
- **Multi-page bills are handled but unproven on real documents.** A bill spanning a page break is
  joined back into one candidate. I couldn't find a real spanning bill in the clearing set to test
  against — 54 pages scanned, no `หน้า 1/2` markers anywhere. **If you have a chunk with a known
  two-page invoice, send it.** That's the one thing that would close it out. Duplicate scans of
  the same receipt are a separate problem and aren't handled — that needs the whole upload in
  view, which is your side.

## Two questions for you

- **Can you read response headers?** `X-Model-Name` / `X-Model-Version` carry the model digests
  for the per-extraction audit trail. If headers don't reach your audit log, that data needs
  another home and it's a change on your side, not mine.
- **Eleven fields in the samples are new** — the buyer block, `documentBookNumber`, `payeeType`,
  `paymentMethod`, `discount`, `vatExemptAmount`, `vatRate`, `withholdingTaxRate`. They came from
  FA's requirements, not from your contract. Happy to rename any of them to match your conventions
  — the shape is what matters.

One thing you *don't* need to worry about: the account code. Send `5122100`, `5-122-100`, or the
code with its Thai label — all three resolve to the same category on my side.

## For the session

Suggest we go in this order, smallest first:

1. You set the env vars and hit `/health`
2. One single page through your full pipeline
3. Confirm your Zod validator accepts the response — this is the one I'd most expect to trip
4. A not-a-bill page, expect `[]`
5. Force a `429` (two at once) and a `400` (send a .txt) so we can see your queue retry the first
   and stop on the second
6. Check the `X-Model-*` headers land in your audit log

Should take half an hour.

Thanks,
