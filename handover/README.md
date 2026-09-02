# Model output — samples and requested schema changes

`samples/` holds one file per document type found in a real advance-clearing set
(`2.1 - P06690-ADV 7390000-CLEAR-TEAM-0027-0`, 123 pages). Every bill in every sample carries
the **same 29 keys** — only the values change. A value the document does not state is `null`,
never an empty string, never a missing key, never `0`.

| Sample | Source page | What it exercises |
|---|---|---|
| `tax-invoice.json` | 165 | full e-tax invoice, per-line discount, VAT-exempt split, numbered branches |
| `individual-receipt.json` | 20 | ใบรับเงิน — paid to a person, withholding tax, no VAT, no company |
| `cash-bill.json` | 180 | handwritten บิลเงินสด — no VAT, no tax ID, เล่มที่ and เลขที่ both present |
| `toll-receipts.json` | 200 | two bills on one page, VAT included in the price and never stated |
| `not-a-bill.json` | 21 | an ID-card copy. The correct answer is an empty list. |

Those five are transcribed by hand from the pages: they are the **target**, and they include the
6 fields requested below.

`live-output-today.json` is different and deliberately so. It is **unedited output from the
running pipeline**, regenerated 2026-08-20. It now carries all 29 keys — `payeeType` and
`vatRate` included — and it still carries the real OCR noise in the seller fields. Read it as the
honest picture of where accuracy stands today; read the other five as the shape being asked for.

Note its buyer block: `confidence: 1.0` on all four, because those come from a constant rather
than from OCR. See *The four buyer fields are filled from a constant* near the end of this file.

---

## The structure at a glance

One bill, every field, with the values from `samples/tax-invoice.json`. Comments are for reading
only — the real files are plain JSON.

```jsonc
{
  "billCandidates": [          // one entry per bill found. [] when the page is not a bill.
    {
      // --- where this bill came from -----------------------------------------
      "candidateIndex": 0,     // 0,1,2… several bills can share one page
      "chunkPageIndex": 164,   // which page of the upload

      // --- what the document is ----------------------------------------------
      "documentType":           { "value": "ใบกำกับภาษี/ใบเสร็จรับเงิน", "confidence": 0.9 },
      "originalDocumentNumber": { "value": "BNAIE26070421979", "confidence": 0.9 },
      "documentBookNumber":     { "value": null,               "confidence": 0.0 },  // new
      "documentDate":           { "value": "2026-07-25",       "confidence": 0.9 },

      // --- who was paid -------------------------------------------------------
      "payeeType":     { "value": "company",  "confidence": 0.9 },   // new: company | individual
      "sellerName":    { "value": "บริษัท ซีอาร์ซี ไทวัสดุ จำกัด (สาขาบางนา)", "confidence": 0.9 },
      "sellerAddress": { "value": "54 หมู่ 13 ต.บางแก้ว อ.บางพลี จ.สมุทรปราการ 10540", "confidence": 0.9 },
      "sellerTaxId":   { "value": "0105555021215", "confidence": 0.9 },
      "sellerBranch":  { "value": "00024",         "confidence": 0.9 },

      // --- who it was issued to ----------------------------------------------
      "buyerName":    { "value": "บริษัท ทีม คอนซัลติ้ง เอนจิเนียริ่ง แอนด์ แมเนจเมนท์ จำกัด (มหาชน)", "confidence": 0.9 },
      "buyerAddress": { "value": "151 ถนนนวลจันทร์ แขวงนวลจันทร์ เขตบึงกุ่ม กรุงเทพมหานคร 10230", "confidence": 0.9 },
      "buyerTaxId":   { "value": "0107561000030", "confidence": 0.9 },
      "buyerBranch":  { "value": "00000",         "confidence": 0.9 },

      // --- how it was paid ----------------------------------------------------
      "currency":      { "value": "THB",        "confidence": 0.9 },
      "paymentMethod": { "value": "บัตรเครดิต", "confidence": 0.9 },   // new

      // --- the money ----------------------------------------------------------
      "discount":           { "value": 18.15,  "confidence": 0.9 },
      "amountBeforeVat":    { "value": 548.46, "confidence": 0.9 },
      "vatExemptAmount":    { "value": 0.0,    "confidence": 0.9 },   // new
      "vatRate":            { "value": 7,      "confidence": 0.9 },   // new
      "vat":                { "value": 38.39,  "confidence": 0.9 },
      "withholdingTaxRate": { "value": null,   "confidence": 0.0 },   // new
      "withholdingTax":     { "value": null,   "confidence": 0.0 },
      "originalTotal":      { "value": 586.85, "confidence": 0.9 },
      "clearingAmount":     { "value": 586.85, "confidence": 0.9 },

      // --- foreign currency only ----------------------------------------------
      "exchangeRate":       { "value": null, "confidence": 0.0 },
      "exchangeRateSource": { "value": null, "confidence": 0.0 },

      // --- what was bought ----------------------------------------------------
      "lineItems": [
        {
          "description": { "value": "ประแจเลื่อน 300มม. (12นิ้ว) WORKPRO WP272004", "confidence": 0.9 },
          "quantity":    { "value": 1,      "confidence": 0.9 },
          "unit":        { "value": "ชิ้น", "confidence": 0.9 },      // new
          "unitPrice":   { "value": 258.00, "confidence": 0.9 },
          "discount":    { "value": 7.74,   "confidence": 0.9 },      // new
          "amount":      { "value": 250.26, "confidence": 0.9 }
        }
      ]
    }
  ]
}
```

Three rules that hold for every response:

1. **Every key is always present.** A value the document does not state is `null` — never `""`,
   never `0`, never a missing key.
2. **Every field is `{ value, confidence }`** except `candidateIndex` and `chunkPageIndex`, which
   are plain integers.
3. **`confidence` is the model's own uncertainty**, 0 to 1. A crisply printed number scores high;
   handwriting it had to guess at scores low. It is meant to drive which fields the UI highlights
   for review.

---

## What this JSON is actually for

The clearing set is assembled onto form **FM-FA-05**, and its detail sheet has exactly these
columns. Everything below exists to fill this table:

| FM-FA-05 column | Field |
|---|---|
| ว.ด.ป. | `documentDate` |
| ใบเสร็จเลขที่ | `originalDocumentNumber` |
| รายการ | `lineItems[].description` |
| ค่าสินค้า/บริการ (1) | `amountBeforeVat` |
| ภาษีมูลค่าเพิ่ม (2) | `vat` |
| ภาษีหัก ณ ที่จ่าย (3) | `withholdingTax` |
| รวมจ่าย (4) | `clearingAmount` |
| Re. ประเภท คชจ. | chosen by FA, not by the model |

### The `clearingAmount` rule, finally settled

The form's column 4 is labelled `4=(1+2+3)`, but the arithmetic on the sheet is a subtraction:

```
clearingAmount = amountBeforeVat + vat − withholdingTax
```

Verified against the totals row of the real form:
`857,047.20 + 20,205.33 − 18,591.16 = 858,661.37` ✓

So for an ordinary purchase `clearingAmount` equals `originalTotal`, and where tax is withheld it
is the net actually handed over. On `individual-receipt.json`:
`24,226.80 − 726.80 = 23,500.00`, which is the จำนวนเงินที่จ่าย-สุทธิ printed on the page.

---

## Requested additions — 6 fields

Everything else in the samples is the contract as published.

| Field | Type | Why |
|---|---|---|
| `payeeType` | `"company"` \| `"individual"` \| null | Decides **ภ.ง.ด.3 vs ภ.ง.ด.53**, which are two separate account codes in FA's chart (2153600 and 2153700). A ใบรับเงิน paid to a person has no company, no VAT and no tax invoice number — the whole document reads differently. |
| `documentBookNumber` | string \| null | เล่มที่ / Book No. Handwritten cash bills and toll tickets number restart in every book, so `เลขที่ 8` alone does not identify a document. |
| `vatRate` | number \| null | The printed rate (7, or 0 for exempt). Lets the webapp check `vat ≈ amountBeforeVat × rate` instead of trusting a single OCR'd number. |
| `withholdingTaxRate` | number \| null | 1, 3 or 5 depending on the service. Required on the ภ.ง.ด. filing and the same cross-check applies. |
| `vatExemptAmount` | number \| null | มูลค่ายกเว้น. Invoices that mix taxable and exempt lines print both subtotals; without this the totals cannot be reconciled. |
| `paymentMethod` | string \| null | เงินสด / เงินโอน / บัตรเครดิต / เช็ค. Advance clearing treats a cash payment differently from a transfer, and ใบรับเงิน requires an attached bank slip. |

Plus two inside `lineItems[]`: **`unit`** (หน่วย — ชิ้น, ล., กก.; fuel receipts are priced per litre
and the quantity is meaningless without it) and **`discount`** (the per-line ส่วนลด column, which
real invoices print as a percentage *and* as an amount).

### Optional, in priority order

If the field count needs trimming, drop from the bottom: `paymentMethod`, `vatExemptAmount`,
`documentBookNumber`. `payeeType`, `vatRate` and `withholdingTaxRate` earn their place — the
first changes how a document is read, the other two make every amount checkable.

---

## Full field reference — 29 fields

**new** marks a field not in the published schema.

| Field | Type | Notes |
|---|---|---|
| `candidateIndex` | integer | 0, 1, 2 … when one page holds several bills. Toll receipts routinely give four. |
| `chunkPageIndex` | integer | which page of the upload this bill came from |
| `documentType` | string \| null | the wording printed on the document, copied not translated |
| `originalDocumentNumber` | string \| null | เลขที่ / เลขที่ใบกำกับภาษี / No. — one field, any of those labels |
| `documentBookNumber` | string \| null | **new** — เล่มที่ / Book No. |
| `documentDate` | string \| null | `YYYY-MM-DD`, Gregorian |
| `payeeType` | string \| null | **new** — `company` or `individual` |
| `sellerName` | string \| null | the shop or person paid |
| `sellerAddress` | string \| null | |
| `sellerTaxId` | string \| null | 13 digits. For an individual this is their national ID, which is also their TIN. |
| `sellerBranch` | string \| null | the **seller's** branch — `สำนักงานใหญ่` or a number like `00024` |
| `buyerName` | string \| null | |
| `buyerAddress` | string \| null | |
| `buyerTaxId` | string \| null | ours is `0107561000030`; a different value means the receipt was not issued to the company |
| `buyerBranch` | string \| null | |
| `currency` | string \| null | ISO code |
| `paymentMethod` | string \| null | **new** |
| `discount` | number \| null | ส่วนลด, document level |
| `amountBeforeVat` | number \| null | the VAT-able base, after discount |
| `vatExemptAmount` | number \| null | **new** |
| `vatRate` | number \| null | **new** |
| `vat` | number \| null | `null` if no VAT line; `0` only if the document prints a zero |
| `withholdingTaxRate` | number \| null | **new** |
| `withholdingTax` | number \| null | same null-vs-zero rule |
| `originalTotal` | number \| null | the final figure printed on the document |
| `clearingAmount` | number \| null | `amountBeforeVat + vat − withholdingTax` |
| `exchangeRate` | number \| null | null on THB documents |
| `exchangeRateSource` | string \| null | |
| `lineItems[]` | array | `description`, `quantity`, `unit` **new**, `unitPrice`, `discount` **new**, `amount` |

All 29 are always present in the response. Our own accuracy measurement grades 23 of them —
`candidateIndex`, `chunkPageIndex` and `lineItems` are not read off the document, `documentType`
is used to group the score by document kind, and `exchangeRate`/`exchangeRateSource` are null on
every Thai document. That is a decision about what to measure, not about what to send.

---

## Document types in a real clearing set

All 123 pages reviewed. Nine distinct kinds, and roughly a third of the pages are not bills at
all — about 14 pages are not bills in any form, plus 5 ID-card copies attached to ใบรับเงิน.

1. **FM-FA-05 cover sheet** — the summary FA fills in. Not a bill.
2. **FM-FA-05 detail sheet** — the itemised table. Not a bill.
3. **Full tax invoice** — company, VAT, branch numbers, per-line discounts.
4. **ใบรับเงิน to an individual** — withholding tax, national ID, no VAT.
5. **National ID card copy** — attached to every ใบรับเงิน. Not a bill, and carries personal data.
6. **Fuel receipt** — POS printed, priced per litre, VAT backed out of the total.
7. **Printed toll receipt** — several per page, price stated VAT-inclusive.
8. **Toll ticket coupon** — the date is a **punched hole** on a printed calendar, not text. `documentDate` will be null and no prompt can fix that.
9. **Handwritten cash bill** — no tax ID, no VAT, เล่มที่ and เลขที่ both present.

The non-bill pages matter as much as the bills. On an ID-card copy the correct output is
`{"billCandidates": []}` — see `not-a-bill.json`. Anything else is an invented bill, and on a
123-page upload that is worse than a miss: a reviewer scanning for errors is far likelier to
correct a wrong value than to notice an entire ledger row that should not exist.

---

## One thing to send us: the account code

FA's expense categories carry per-category extraction rules on our side, so pass the account code
with the request and the matching rule fires. **Canonical spelling is `5122100` — digits only.**

You do not have to match it exactly. We normalise on the way in, so all of these resolve to the
same account:

```
5122100            5-122-100            5-122-100 ค่าจ้างเหมา
```

Send whatever your side already holds. Flagged only because a mismatch here raises no error — the
rule would simply never fire and the category would quietly fall back to the shared prompt.

Today 3 of FA's 82 codes have rules written; the rest are pending from FA and behave identically
to sending no code at all.

---

## Two things the schema does not solve

**Multi-page invoices — now handled, with one caveat.** A bill running across a page break is
joined back into a single candidate: either both pages state their position (`หน้า 1/2` then
`หน้า 2/2`), or two of the document number, seller, total and date agree. Any disagreement on
those refuses the join, because a false merge silently destroys a bill while a false split just
costs a reviewer ten seconds.

The caveat: **we have not yet found a real spanning bill in your clearing set.** 54 transcripts
carry no page markers at all, so this is tested against constructed fixtures rather than your
documents. If you have a chunk with a known two-page invoice, send it — that is the one thing
that would close this out.

Still your side: whoever splits the upload must keep such pages **in the same chunk**. We cannot
see past the pages we are given.

**Duplicate scans.** Page 200 holds four toll receipts, but they are two receipts scanned twice.
The model will honestly report four bills. De-duplication belongs in the webapp, where the whole
upload is visible.

---

## Known limitations, stated plainly

- **Dates and digits are misread on handwritten and low-contrast pages.** The cause is the OCR
  stage, not the extraction stage. Two scans of the same vendor's invoice gave one correct tax ID
  and one wrong one.
- **Whole blocks are sometimes dropped by OCR.** On one fuel receipt the entire customer block
  vanished from the transcript; the extraction stage faithfully reported what it was given. For
  the buyer block specifically this is now worked around — see below.
- **Accuracy is 77.3% (501 of 648 fields)**, measured 2026-08-20 with your scorer on 34 pages
  across nine document types. Structured fields run well ahead of that — `currency`, `discount`
  and `paymentMethod` at 100%, `originalTotal` 91%, the withholding and VAT rates 94–97%.
  **51 of the 147 misses are `sellerName` and `sellerAddress` alone**, where exact matching
  scores a one-character slip the same as a blank. Printed 79.2%, handwritten 74.3%. The English
  stratum reads 78.4% on n=37 — a warning, not a measurement.
- **`originalDocumentNumber` is the weakest structured field at 54%.** These forms print
  `เลขที่` / `BILL NO.` beside an empty box, so a missing number and a misread label look alike.
  We drop anything that is obviously the label rather than a value, which costs recall.
- **Output is not byte-identical between runs**, which the contract requires. Both stages are
  pinned to `temperature: 0` with a fixed seed and it still varies. Unresolved.

### The four buyer fields are filled from a constant, not read from the page

`buyerName`, `buyerAddress`, `buyerTaxId` and `buyerBranch` do **not** come from OCR. You should
know this, because it changes what those four values mean.

TEAM is the buyer on every bill in an advance-clearing set — that is what makes a bill claimable —
so all four values are known before the page is read. Asking the model to re-read them off a
photocopy only creates chances to get them wrong, and it did: on one test page OCR transcribed no
part of the buyer block at all, so the extractor, required to produce a buyer name and given none,
returned the *seller's* address instead. Deciding these four from the page rather than from the
model is worth **60 of 648 fields** — the single largest gain in the project.

Three consequences for your side:

1. **The page decides, in both directions.** Transcript carries a recognisable TEAM fragment →
   the constant is applied. Transcript names TEAM nowhere → all four come back `null`.
   **An empty buyer is a correct answer, not an error condition** — a motorway toll slip names no
   buyer at all, and two are in our test set. The blanking half is not optional tidiness: the
   prompt has to name TEAM for the model to recognise it, and the model was observed copying
   that name *and* our tax ID onto a toll slip that never mentions us.
2. **`confidence` is `1.0` on these four fields** when the constant is applied. That is honest —
   we do know the value — but it means these four cannot be used to gauge how well the page was
   read. Note this alongside the separate finding that `confidence` is not predictive generally.
3. **A bill genuinely billed to someone else would still be labelled TEAM**, if the page happens
   to mention TEAM somewhere. We have not seen this in the sample set and it may not be possible
   in your workflow. If it is possible, tell us and we will gate it harder.

The behaviour is one boolean — `FILL_BUYER_FROM_CONSTANT` in `stage2_extract.py`. If you would
rather receive what the model actually read, say so and we will ship it off; it costs 60 fields
of accuracy, and you would then receive invented buyers on pages that have none.

> The five hand-transcribed samples are targets, written before this change, and show the buyer
> block as the *document* states it. `live-output-today.json` is real output and shows the
> constant, at `confidence: 1.0`. `toll-receipts.json` is the one to check your validator
> against — a page with no buyer at all.
