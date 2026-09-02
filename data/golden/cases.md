# The answer key — how to fill it in

`golden.json` is the list of correct answers. Everything the AI produces gets compared against it.
It is the only thing in this project that says what "right" means, so it is worth being fussy about.

**Do not let the AI fill this in.** It would then be graded against its own guesses, and the
resulting number would mean nothing.

---

## How to fill it in

Open `golden.json`. Every field says `"TODO"`. Replace each one by looking at the receipt.

Check your progress at any time:

```bash
.venv/Scripts/python.exe eval/harness.py
```

You do **not** have to finish all nine before scoring. Fill in two receipts and you already get a
real number — unfilled fields are skipped automatically.

### The rules

**Type exactly what is printed.** Do not tidy it up, do not expand abbreviations, do not fix the
shop's spelling mistakes. The AI is scored on an exact match, so "helpfully" correcting something
here creates a permanent false failure that no amount of tuning can fix.

**A field that is not on the receipt: you have two choices, and they mean different things.**

| You write | Meaning |
|---|---|
| delete the line | not scored — you didn't check, or don't want to judge |
| `null` (no quotes) | **scored** — "blank is the correct answer", and the model loses the point if it invents one |

Prefer `null` when you looked and the receipt genuinely has no such line. It is the only thing that
tests whether the model *stops* inventing — page 9 grabbed a phone number as a document number, and
a deleted line would hide that.

Never write `"null"` **in quotes**. That is the four-letter word `null`, which nothing will ever
match. Same for `""` and `"-"`.

**Dates → `YYYY-MM-DD`, Gregorian.** Thai Buddhist years are 543 ahead.
`13/07/69` → `2026-07-13`. `15 มี.ค. 2567` → `2024-03-15`.

**Amounts → plain string, no commas, two decimals.** `1,960` → `"1960.00"`.

**Seller = whoever was paid.** Not us. Our own company —
บริษัท ทีม คอนซัลติ้ง เอนจิเนียริ่ง แอนด์ แมเนจเมนท์ จำกัด (มหาชน), tax ID `0107561000030` — is
always the buyer. Its tax ID is printed on many of these receipts as the customer, which is exactly
what the AI keeps getting wrong, so be careful not to repeat the mistake in the answer key.

**`sellerTaxId` is the seller's own 13-digit ID.** If only the customer's ID is printed, delete the
line.

**Watch out for product brand names.** On page 1 and 2 the AI answered "Elephant Brand", which is a
product (ตราช้าง), not the shop. The seller is the shop name, usually at the top.

### `vat` and `withholdingTax` when there is no such line

**No line printed on the document at all → delete the line.** Not `"0.00"`.

Write `"0.00"` only when the document itself prints a zero.

A missing line and a printed zero are different facts. Missing means "go look at the paper";
zero means "the paper says nothing is owed, no need to check". Collapsing them together throws
away a signal FA can use.

This overrides the webapp team's `sample-response.json`, which shows `0` for a document with no
withholding line — confirmed with them on 2026-08-11 that null is what they want.

---

## Field meanings

22 fields per case, in the order they appear in the file.

| Field | What to type |
|---|---|
| `documentDate` | date on the document, `YYYY-MM-DD`, Gregorian |
| `originalDocumentNumber` | เลขที่ / เลขที่ใบกำกับภาษี / No. — any of those labels, same field |
| `documentBookNumber` | เล่มที่ / BOOK NO. — a *different* number from เลขที่ |
| `payeeType` | `company` or `individual`. A ใบรับเงิน paid to a person is `individual`. |
| `sellerName` | shop or person paid, exactly as printed |
| `sellerAddress` | seller's address, exactly as printed, one line |
| `sellerTaxId` | seller's 13 digits. For a person, their เลขประจำตัวประชาชน. |
| `sellerBranch` | the **seller's** branch — `สำนักงานใหญ่` or a number like `00024` |
| `buyerName` | whoever the document was issued to — usually us |
| `buyerAddress` | |
| `buyerTaxId` | ours is `0107561000030` |
| `buyerBranch` | ours, usually `สำนักงานใหญ่` |
| `currency` | `THB`, `USD` |
| `paymentMethod` | `เงินสด`, `เงินโอน`, `บัตรเครดิต`, `เช็ค`. **Never a card or account number.** |
| `discount` | ส่วนลด, document total |
| `amountBeforeVat` | the VAT-able base, after discount |
| `vatRate` | `7`, or `0`. Plain integer — not `7.0`, not `7%`. |
| `vat` | VAT amount |
| `withholdingTaxRate` | `1`, `3` or `5`. Plain integer. |
| `withholdingTax` | ภาษีหัก ณ ที่จ่าย |
| `originalTotal` | the final figure printed on the document |
| `clearingAmount` | `amountBeforeVat + vat − withholdingTax` — for most receipts this equals `originalTotal` |

### Why 22 here but 29 in the contract

The model returns **29** fields per bill (see `handover/README.md`). This file grades **22** of
them. Nothing is graded that the model does not produce — the seven left out are left out for a
reason:

| Not graded | Why |
|---|---|
| `candidateIndex`, `chunkPageIndex` | bookkeeping, not read off the document |
| `lineItems` | an array; the scorer only compares flat fields |
| `documentType` | already a case-level grouping key below — grading it twice adds nothing |
| `exchangeRate`, `exchangeRateSource` | null on every Thai document |
| `vatExemptAmount` | printed on two of the thirteen pages sampled from a real clearing set, and zero on both. Everything these projects buy is VAT-able. Still sent in the response; just not worth typing twelve times. |

Those last three would hand out free correct answers and flatter the score without measuring
anything. `29 − 7 = 22`. If the contract gains a field, it belongs here too unless it falls into
one of those rows.

### The three added later — read these carefully

**`sellerAddress`** — the whole address on one line, copied as printed. Keep the shop's own line breaks
as single spaces. Do not expand `ถ.` to `ถนน` or reorder anything.

**`sellerBranch` — the trap.** This is the *seller's* branch, but `สำนักงานใหญ่` is printed on these
receipts as part of **TEAM's** name in the customer line, because that is where the bill was sent.
The model already gets this wrong. If the shop does not state its own branch, delete the line.

**`withholdingTax`** — same null-vs-zero rule as `vat`: `"0.00"` if the document has a WHT line
showing zero, delete the line if the document has no WHT line at all. Most market cash bills have
none.

---

## Grouping labels — keep these consistent

The scorer groups results by these, so a typo splits one group into two useless ones.

| Key | Allowed values |
|---|---|
| `language` | `th` · `en` · `lo` · `mixed` |
| `documentType` | `tax_invoice` · `receipt` · `cash_bill` · `transfer_slip` · `other` |
| `handwriting` | `true` if **any field you are scoring** is handwritten |

`handwriting: true` is deliberately generous. A printed invoice with a handwritten total counts as
handwriting, because that handwritten total is the field most likely to be read wrong.

The `documentType` and `handwriting` values already in the file are **guesses**. Correct them as you
go — page 4 in particular I could not tell.

---

## Decisions log

Write down anything you had to judge, so it can be checked later instead of re-litigated.

| Case | Field | Decision and why |
|---|---|---|
| | | |
