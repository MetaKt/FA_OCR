"""
Stage 2 of Architecture B: Typhoon's transcript -> the webapp team's schema JSON.

Runs on Ollama's structured-output mode (`format` = a JSON Schema), which compiles the schema
to a GBNF grammar internally, so malformed JSON and stray keys are impossible rather than
merely discouraged.

DESIGN NOTE -- the model is given a REDUCED schema, not the contract schema.

The contract wants a bounding box on every candidate (`regions`, minItems 1). Typhoon emits no
coordinates, so stage 2 has no way to know where anything is on the page. Asking the model for
coordinates it cannot know would produce invented numbers that look valid and point nowhere --
the worst possible outcome, because nothing downstream can detect it.

So the model is asked only for what the transcript can actually support, and `assemble()` adds
the whole-page placeholder deterministically (plan/00-OVERVIEW.md F1a, F8). The guarantee that
this still satisfies the contract comes from validating the assembled result against their real
schema file -- see `validate()`. That validation is also what catches drift if they send a new
contract.
"""
import json
import re
from pathlib import Path

import httpx
import jsonschema

import certlink
import regions as R

CONTRACT_SCHEMA = Path(
    r"C:\Users\meta_k\Downloads\adv-clear-model-handover\adv-clear-model-handover"
    r"\bill-extraction.schema.json"
)

OLLAMA = "http://localhost:11434"

CATEGORY_RULES = Path(__file__).resolve().parent.parent / "data" / "category_rules.json"

# Label -> (ollama tag, extra request options).
#
# qwen3 is a reasoning model. Do NOT pass think:False -- measured behaviour is that it moves the
# reasoning into message.content instead of suppressing it, which breaks the JSON we read from
# there. Left on, Ollama keeps reasoning in message.thinking and content stays clean JSON.
CANDIDATES = {
    "qwen3-4b": ("qwen3:4b", {}),
    "typhoon2-8b": ("scb10x/llama3.1-typhoon2-8b-instruct", {}),
}

STRING_FIELDS = ["documentType", "originalDocumentNumber", "documentBookNumber", "payeeType",
                 "sellerName", "sellerAddress", "sellerTaxId", "sellerBranch",
                 "buyerName", "buyerAddress", "buyerTaxId", "buyerBranch",
                 "paymentMethod", "exchangeRateSource"]
NUMBER_FIELDS = ["originalTotal", "exchangeRate", "amountBeforeVat", "vat", "vatExemptAmount",
                 "vatRate", "withholdingTax", "withholdingTaxRate", "clearingAmount", "discount"]

# One line item. `unit` matters because fuel is priced per litre and a bare quantity of 37.657
# means nothing without it; `discount` because real invoices print the reduction per line, often
# as a percentage, and only sometimes repeat it as a document total.
LINE_ITEM_FIELDS = ["description", "quantity", "unit", "unitPrice", "discount", "amount"]
LINE_ITEM_STRINGS = {"description", "unit"}

PAYEE_TYPES = ["company", "individual"]
PROMPT = """You read a Thai receipt that has already been transcribed to text and return structured data.

Each distinct bill in the text becomes one entry in billCandidates. If you cannot tell whether something is one bill or two, return two entries -- a human will decide.

A bill states an amount that was paid. Many pages in a clearing set do not -- ID card copies, company summary forms, delivery notes, blank pages. For those, return an empty billCandidates. An empty list is a correct answer, not a failure.

Rules:
- A value you cannot find in the text is null. Never guess, never return an empty string.
- documentDate must be YYYY-MM-DD in the Gregorian calendar. Thai Buddhist years are 543 ahead: 20/7/2569 is 2026-07-20, and 15 mar 2567 is 2024-03-15.
- currency is an ISO code: THB, LAK, USD.
- documentType is the wording printed on the document itself -- บิลเงินสด, ใบเสร็จรับเงิน, ใบกำกับภาษี, CASH SALE. Copy it, do not translate it and do not pick a category of your own. If the document does not say, it is null.
- originalDocumentNumber is the document's own number. The label may be เลขที่, เลขที่ใบกำกับภาษี, เลขที่ใบกำกับ, No. or NO. -- these all mean the same field. If two of them are printed with the same value, use it once. Ignore POS numbers, RD numbers, อนุมัติเลขที่, เล่มที่ and telephone numbers.
- sellerBranch is the SELLER's branch, copied exactly as written. Keep "สำนักงานใหญ่" as "สำนักงานใหญ่" and keep branch numbers as printed. A สาขา printed inside a customer block -- ลูกค้า, นามผู้ซื้อ, ที่อยู่ผู้ซื้อ -- is the buyer's, not the seller's, and so is "สำนักงานใหญ่" written beside our own company name. If the seller does not state its own branch, sellerBranch is null.
- Amounts are plain numbers with no commas and no currency symbol.
- Every amount is copied from a total printed on the document -- ยอดค้างชำระ, รวมเป็นเงิน, ยอดก่อนภาษี, รวมทั้งสิ้น. Never multiply, add or subtract to produce one. If a subtotal is not printed, amountBeforeVat is null.
- discount is ส่วนลด, the total discount given on the document. If a ส่วนลด column exists but is empty, discount is null. amountBeforeVat is the subtotal after the discount was taken off, exactly as printed.
- If the document has no VAT line at all, vat is null. Write 0 only when the document itself prints a zero. Same rule for withholdingTax. A missing line and a printed zero are different facts, and a reviewer needs to tell them apart.
- vatRate and withholdingTaxRate are the percentages printed beside those lines, as plain numbers: 7 for "ภาษีมูลค่าเพิ่ม 7%", 3 for "หัก ณ ที่จ่าย 3%". If the document shows the tax amount but not its rate, the rate is null. Do not calculate it.
- vatExemptAmount is มูลค่ายกเว้น or สินค้าที่ยกเว้นภาษีมูลค่าเพิ่ม, printed on invoices that mix taxable and exempt goods. Null when the document does not separate them.
- documentBookNumber is เล่มที่ or BOOK NO., which is a different number from เลขที่. Handwritten bills and toll tickets print both. Null when only one number is printed.
- paymentMethod is how it was paid and nothing else: เงินสด, เงินโอน, บัตรเครดิต, เช็ค. Cash bills and receipts often tick a box. Never copy a card number, a masked card number, a bank account number, a cheque number or a payment reference into this field -- if the document shows only such a number, write the method it implies, or null. Null if the document does not say.
- payeeType is "individual" when the money went to a person and "company" when it went to a business. A ใบรับเงิน with ข้อมูลผู้รับเงิน (บุคคลธรรมดา), a เลขประจำตัวประชาชน and no company letterhead is "individual"; anything with a company name or a ใบกำกับภาษี is "company". Null only when you genuinely cannot tell.
- For an individual, the 13 digits beside เลขประจำตัวประชาชน go in sellerTaxId. A Thai person's national ID is also their tax ID, so it belongs in the same field.
- unit is the หน่วย printed for a line -- ชิ้น, อัน, ล., กก., L. Fuel is sold by the litre and the quantity is meaningless without it. Null when no unit is printed.
- confidence is your real uncertainty for that one value, between 0 and 1. A clearly printed number is high. Handwriting you had to guess at is low. Do not put the same number everywhere.

These documents name two parties, and every name, address, tax ID and branch on the page belongs to one of them.

The SELLER is the shop or person who was paid. Its details are usually in the letterhead at the top. They go in sellerName, sellerAddress, sellerTaxId, sellerBranch.
The BUYER is whoever the document was issued to. Its details sit under a customer heading -- ลูกค้า, นามลูกค้า, นามผู้ซื้อ, ที่อยู่ผู้ซื้อ, รหัสลูกค้า, CUSTOMER. They go in buyerName, buyerAddress, buyerTaxId, buyerBranch.

Our own company is บริษัท ทีม คอนซัลติ้ง เอนจิเนียริ่ง แอนด์ แมเนจเมนท์ จำกัด (มหาชน), TEAM Consulting Engineering and Management, tax ID 0107561000030. We buy, we never sell. If you see that name, or anything close to it, or that tax ID, it is the BUYER. Put it in the buyer fields and never in a seller field. A สาขา or สำนักงานใหญ่ printed beside it is buyerBranch, not sellerBranch.

Do not assume the buyer is us. Copy whatever the customer block actually says: some receipts are issued to an employee by name, and that name is the correct buyerName. Our name is there to tell you which side of the document you are reading, not to be filled in when it is absent.

If a party's detail is not printed, that field is null."""

BUYER_TAX_ID = "0107561000030"


def _wrapped(value_schema):
    """One scalar field, minus the sourceRegions the transcript cannot support."""
    return {"type": "object",
            "properties": {"value": value_schema,
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["value", "confidence"], "additionalProperties": False}


def reduced_schema():
    """What we actually ask the model for. Kept small on purpose: every token of grammar is a
    token the model can trip over, and coordinates are not knowable from text."""
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    nullable_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}

    props = {f: _wrapped(nullable_str) for f in STRING_FIELDS}
    props.update({f: _wrapped(nullable_num) for f in NUMBER_FIELDS})
    # [0-9] not \d: Ollama's schema-to-GBNF converter rejects \d with
    # "failed to parse grammar". Character classes and {n} repetition are fine.
    props["documentDate"] = _wrapped(
        {"anyOf": [{"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                   {"type": "null"}]})
    props["currency"] = _wrapped(
        {"anyOf": [{"type": "string", "pattern": "^[A-Z]{3}$"}, {"type": "null"}]})
    # An enum, not a free string: "individual" vs "company" decides ภ.ง.ด.3 vs ภ.ง.ด.53, and a
    # near-miss spelling would send the whole document to the wrong filing.
    props["payeeType"] = _wrapped({"anyOf": [{"enum": PAYEE_TYPES}, {"type": "null"}]})
    props["lineItems"] = {
        "type": "array",
        "items": {"type": "object",
                  "properties": {
                      f: _wrapped(nullable_str if f in LINE_ITEM_STRINGS else nullable_num)
                      for f in LINE_ITEM_FIELDS},
                  "required": LINE_ITEM_FIELDS,
                  "additionalProperties": False}}
    return {"type": "object",
            "properties": {"billCandidates": {"type": "array",
                                              "items": {"type": "object", "properties": props,
                                                        "required": list(props),
                                                        "additionalProperties": False}}},
            "required": ["billCandidates"], "additionalProperties": False}


def clean_tax_id(value, reject_buyer=True):
    """A Thai tax ID is exactly 13 digits. Anything else is a misread, so drop it.

    `reject_buyer` also drops our own company's ID: TEAM is always the buyer, so seeing it in
    sellerTaxId means the model picked up the customer block. Both checks are deterministic -- a
    prompt rule alone was not enough, the model kept doing it (plan/00-OVERVIEW.md F10).

    buyerTaxId passes reject_buyer=False, because there our ID is the expected answer. It is not
    forced, though: a receipt made out to the employee personally carries a different ID, and that
    difference is exactly what the webapp needs in order to flag the claim.
    """
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 13 or (reject_buyer and digits == BUYER_TAX_ID):
        return None
    return digits


# A run of four or more digits or masking characters. Card numbers, account numbers, cheque
# numbers and payment references all trip this; no name of a payment method does.
_ACCOUNT_LIKE = re.compile(r"[0-9Xx*•]{4,}")


def clean_payment_method(value):
    """Keep the method, drop the account.

    The model reads "ชำระเงินโดย CENT52566810XXXX7422" and copies the reference, which puts a
    masked card number into a field FA only wants the word for. Telling it not to in the prompt
    did not work -- the number is the only thing printed next to the label. So it is rejected
    here instead, where the rule cannot be talked out of.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or _ACCOUNT_LIKE.search(text):
        return None
    return text


def load_categories(path=CATEGORY_RULES):
    """FA's account codes: {code: {"group", "name", "rule"}}. Keys beginning with _ are notes.

    Re-read on every call rather than cached at import: FA will be editing this file while the
    service is running, and a rule that needs a restart to take effect is a rule that quietly
    stays wrong. The file is a few kilobytes.
    """
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for code, entry in raw.items():
        if code.startswith("_"):
            continue
        # Tolerate a bare string, which is what a hand-edit is most likely to produce.
        out[code] = {"rule": entry} if isinstance(entry, str) else entry
    return out


def load_category_rules(path=CATEGORY_RULES):
    """Only the categories that actually say something: {code: rule text}.

    Most of FA's 80-odd codes need no special handling, and an empty rule must behave exactly
    like no entry at all -- otherwise every category would append a blank line to the prompt.
    """
    return {code: entry["rule"].strip()
            for code, entry in load_categories(path).items()
            if (entry.get("rule") or "").strip()}


_CODE_HEAD = re.compile(r"^[\s]*([0-9][0-9\-]*)")


def category_key(category):
    """An account code reduced to its digits. `5122100` is the canonical spelling.

    Settled 2026-08-20: digits only, no dashes, and that is how data/category_rules.json is
    keyed. But the webapp is somebody else's code and may well send `5-122-100`, or the code
    with its Thai label attached -- so normalise on the way in rather than depending on the two
    sides agreeing forever. A mismatch here is silent: the rule simply never fires and the
    category quietly uses the shared prompt.

    Only the leading code token is read, so a label with its own digits ("5122100 ค่าน้ำมัน 95")
    does not turn into 512210095.
    """
    m = _CODE_HEAD.match(str(category or ""))
    return m.group(1).replace("-", "") if m else str(category or "").strip()


def category_prompt(category, rules=None):
    """The stage-2 prompt for one category: the shared prompt, plus that category's extra rules.

    A category with no entry -- including one FA invents next year -- gets the shared prompt
    unchanged. Falling back is deliberate: a missing rule should mean ordinary behaviour, never
    an error in the middle of a batch.
    """
    if category is None:
        return PROMPT
    rules = load_category_rules() if rules is None else rules
    wanted = category_key(category)
    extra = next((v for k, v in rules.items() if category_key(k) == wanted), None)
    return PROMPT if not extra else PROMPT.rstrip() + "\n" + extra.strip()


# Labels that introduce a document number. เล่มที่ / BOOK NO. are deliberately absent: they
# introduce the book number, which is a separate field.
_DOC_NUMBER_LABEL = re.compile(
    r"(เลขที่ใบกำกับภาษี|เลขที่ใบกำกับ|เลขที่|Receipt\s+Running\s+No|BILL\s+NO|No|NO|#)"
    r"[\s:.]*$", re.I)
# ...unless the nearest label is the book's. "เล่มที่ BOOK NO. 08 เลขที่ BILL NO. 009" puts a
# bare NO. directly before the book number too, so the two are told apart by which came last.
_BOOK_LABEL = re.compile(r"(เล่มที่|BOOK\s*NO)[\s:.]*$", re.I)
_LABEL_WINDOW = 60


def clean_document_number(value, transcript=None):
    """Drop a document number that is really the pre-printed label, or an unlabelled number.

    These bill forms print เลขที่ / BILL NO. / เล่มที่ / BOOK NO. next to an empty box. When the
    shop leaves the box blank the model returns the label itself -- measured "BOOK NO." and
    "เล่มที่ BOOK NO." on page 3. A real document number always contains at least one digit and a
    label never does, so this separates them without a list of label spellings to maintain.

    Given the transcript, also require a number label shortly before the value where it appears
    on the page. A receipt is covered in numbers that are not document numbers -- postcodes,
    telephone numbers, tax ids, POS and RD references -- and the model reaches for one of them
    when the bill has no number of its own. On test2.png it returned the shop's postcode 10230;
    that page prints no เลขที่ anywhere, so null is the honest answer. Listing the labels to
    ignore in the prompt was not enough, because the wrong number is often the only number there.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not any(ch.isdigit() for ch in text):
        return None
    if not transcript or text not in transcript:
        # Not on the page as written -- the model assembled or corrected it. Nothing to check
        # against, so it is kept: this guard exists to reject unlabelled numbers, not OCR noise.
        return text
    for m in re.finditer(re.escape(text), transcript):
        before = transcript[max(0, m.start() - _LABEL_WINDOW):m.start()]
        if _DOC_NUMBER_LABEL.search(before) and not _BOOK_LABEL.search(before):
            return text
    return None


# Dropped from the contract by agreement with the webapp team, 2026-08-11.
#
# The three category keys: FA picks the category before uploading, so a guess from us is at best
# redundant and at worst argues with them on screen.
# `sourceRegions`: per-field coordinates, which Typhoon cannot give us at all. Still dropped --
# their §3.3 confirms it is optional and not blocking.
#
# `regions` and `evidence` were on this list too, and came back on 2026-08-28. See src/regions.py
# for why: the box was never the point, the page span was, and dropping it hid every page of a
# bill but the first. They are absent from `reduced_schema` -- the grammar the model decodes
# against -- and assembled deterministically afterwards, so the model is still never asked for
# coordinates it cannot see.
#
DROPPED_FIELDS = ["suggestedCategoryId", "categorySuggestionReason",
                  "categorySuggestionConfidence"]

# Not in their published file: `regions` carried the page number in the original contract, and
# once `regions` was dropped nothing said which scan a candidate came from. Kept now that
# `regions` is back, because it is what the merge and certlink key on, and their schema requires
# a candidate's own page to appear in its `regions` -- which is true by construction here.
PAGE_INDEX_FIELD = "chunkPageIndex"

# Requested by the webapp team on 2026-08-17 and not yet in their published file, so they are
# grafted on here the same way chunkPageIndex is. When their next contract arrives with these in
# it, delete them from this list -- the graft is additive, so a duplicate would be harmless, but a
# stale local definition that drifts from theirs would not be.
#
# The four buyer fields are not redundant even though the buyer is nearly always TEAM. Every one
# of these documents prints two companies, two addresses, two tax IDs and two branches; with only
# one slot for each, a wrong pick lands the buyer's value in a seller field. Giving the buyer its
# own slots removes the ambiguity instead of asking the model to resolve it.
ADDED_STRING_FIELDS = ["buyerName", "buyerAddress", "buyerTaxId", "buyerBranch",
                       "documentBookNumber", "payeeType", "paymentMethod"]

# In their contract, required, and deliberately not read by the model.
#
# `relatedDocumentNumber` (v6, 2026-08-28) is the number of *another* document that this one
# prints a reference to -- a receipt citing the tax invoice it settles. They asked for `null` this
# round rather than a guess, and they are right to: the nearest number on the page belongs to a
# neighbouring document, and copying it would look exactly like a real cross-reference while being
# fabricated. A blank is honest; a plausible wrong number is not.
NULL_STRING_FIELDS = ["relatedDocumentNumber"]
ADDED_NUMBER_FIELDS = ["discount", "vatExemptAmount", "vatRate", "withholdingTaxRate"]
ADDED_LINE_ITEM_FIELDS = {"unit": "string", "discount": "number"}


MONEY_FIELDS = ["originalTotal", "amountBeforeVat", "clearingAmount"]

# The four fields that describe one party. Both sides carry the same set, which is what makes
# them swappable as a block.
PARTY_PARTS = ["Name", "Address", "TaxId", "Branch"]

# Fragments distinctive enough to identify us through OCR noise. The full registered name is
# never spelled the same way twice -- across the sample set it has come back as เอนจิเนียริ่ง,
# เอนิจเนียริ่ง, พีที คอนซัลติ้ง and คอนซัลติ้งสปอร์ตสโตร์ -- but "คอนซัลติ้ง" survives.
BUYER_NAME_MARKERS = ("คอนซัลติ้ง", "TEAM CONSULTING")

# Our office street. Distinctive enough that no supplier in the sample set shares it, which is
# what lets our address be recognised in a seller field. Branch is deliberately absent: every
# other company is also สำนักงานใหญ่, so it carries no signal.
BUYER_ADDRESS_MARKERS = ("นวลจันทร์",)


def _is_our_value(part, value):
    """Is this one value ours rather than a supplier's?"""
    text = str(value or "")
    if part == "TaxId":
        return "".join(c for c in text if c.isdigit()) == BUYER_TAX_ID
    if part == "Name":
        return any(m.lower() in text.lower() for m in BUYER_NAME_MARKERS)
    if part == "Address":
        return any(m in text for m in BUYER_ADDRESS_MARKERS)
    return False


def _is_us(block):
    """Does this party block describe TEAM rather than a supplier?"""
    return any(_is_our_value(part, value) for part, value in block.items())


def resolve_parties(full):
    """Put the seller's details in the seller fields, whatever the model decided.

    Two failures make this necessary, and neither is reachable from the prompt:

    1. On a handwritten cash bill the model reads the shop at the top of the page as the
       customer, even when the transcript labels the customer block นามลูกค้า.
    2. The prompt has to name our own company so the model can recognise it. On a page where
       our name is not printed, the model has been observed copying that name straight out of
       the instructions into sellerName -- the one field the prompt forbids.

    Mutates `full` in place. Only ever moves values between fields; never invents one.
    """
    seller = {p: full[f"seller{p}"] for p in PARTY_PARTS}
    buyer = {p: full[f"buyer{p}"] for p in PARTY_PARTS}
    seller_vals = {p: v["value"] for p, v in seller.items()}
    buyer_vals = {p: v["value"] for p, v in buyer.items()}

    blank = {"value": None, "confidence": 0.0}

    if _is_us(seller_vals) and _is_us(buyer_vals):
        # Both blocks describe us, which cannot be true -- we are only ever the buyer. The buyer
        # block is the one that belongs to us, so the seller block is contamination and is
        # cleared. Clearing rather than guessing: a blank tells FA to look at the paper, whereas
        # our own name sitting in sellerName reads as a fact and would be filed as one.
        for p in PARTY_PARTS:
            if _is_our_value(p, seller_vals[p]) or (
                    seller_vals[p] is not None and seller_vals[p] == buyer_vals[p]):
                full[f"seller{p}"] = blank
    elif _is_us(seller_vals) and not _is_us(buyer_vals):
        # The two blocks are the wrong way round. Swapping keeps both parties' details.
        for p in PARTY_PARTS:
            full[f"seller{p}"], full[f"buyer{p}"] = buyer[p], seller[p]
    elif seller_vals["Name"] is None and buyer_vals["Name"] is not None \
            and not _is_our_value("Name", buyer_vals["Name"]):
        # The shop's name was filed as the buyer and the seller is empty. Move it across -- but
        # part by part, not as a block. These pages routinely carry the shop's name in the buyer
        # field while buyerTaxId and buyerAddress hold ours correctly, and moving the lot dragged
        # our own tax id out of the buyer field and threw it away.
        for p in PARTY_PARTS:
            if _is_our_value(p, buyer_vals[p]) or seller_vals[p] is not None:
                continue
            full[f"seller{p}"], full[f"buyer{p}"] = buyer[p], blank
    return full


# --------------------------------------------------------------------------- buyer identity

FILL_BUYER_FROM_CONSTANT = True
"""Decide the buyer block from the page rather than from what the model read.

Set to False to see exactly what the model transcribed, which is what you want when judging
whether stage 1 is getting better. It costs about 55 fields on the 34 golden cases.

**Leave this True outside a diagnostic session.** It was found switched off on 2026-08-27, and
the served endpoint had been answering at 67.9% instead of 77.3% for that reason alone -- both
measured the same day on the same transcripts. See plan/00-OVERVIEW.md F20.

We are the buyer on every bill in an advance-clearing set -- that is what makes a bill claimable.
So all four buyer fields are known before the page is read, and asking the model to re-read them
off a photocopy only creates chances to get them wrong. Measured on the twelve golden cases:
31 of the 78 misses were buyer fields, and stage 1 had dropped or mangled the block on most of
them (on test2 it transcribed no part of it at all -- so stage 2, required to produce a buyer
name and given none, returned the *seller's* address instead).
"""

BUYER_IDENTITY = {
    "Name": "บริษัท ทีม คอนซัลติ้ง เอนจิเนียริ่ง แอนด์ แมเนจเมนท์ จำกัด (มหาชน)",
    "Address": "151 ถนนนวลจันทร์ แขวงนวลจันทร์ เขตบึงกุ่ม กรุงเทพมหานคร 10230",
    "TaxId": BUYER_TAX_ID,
    "Branch": "สำนักงานใหญ่",
}

BUYER_EVIDENCE = ("คอนซัลติ้ง", "เอนจิเนียริ่ง", "แมเนจเมนท์", "ทีม คอนซัล",
                  "TEAM CONSULTING", BUYER_TAX_ID, "นวลจันทร์", "บึงกุ่ม")
"""Fragments that mean this page really does name us.

Evidence-gated rather than unconditional, because not every page in a clearing set is billed to
us: a motorway toll slip names no buyer at all, and stamping our name onto one would turn a
correct blank into a confident lie. A fragment rather than the whole string because the whole
string is exactly what stage 1 fails to produce -- if it survived intact we would not need this.

The bare postcode 10230 is deliberately NOT here. Including it reaches 27 of the 31 buyer misses
instead of 19, but 10230 covers a whole district of Bangkok and a supplier in Bueng Kum would
trigger a false fill. Eight fields is not worth inventing a buyer.
"""


def buyer_is_named(transcript):
    """Does this page actually name us? No transcript means no evidence, so no."""
    return bool(transcript) and any(m.lower() in transcript.lower() for m in BUYER_EVIDENCE)


def apply_buyer_identity(full, transcript):
    """The page decides. Names us -> our details. Names nobody -> nothing.

    Runs after resolve_parties, never instead of it: resolve_parties still has to move a
    misfiled *seller* out of the buyer fields first, and that decision is made on the values the
    model actually read.

    The clearing half exists because the prompt has to name our company for the model to
    recognise it, and the model then copies that name onto pages where it does not appear. A
    motorway toll slip names no buyer at all and still came back with our name and our tax id in
    the buyer fields, straight out of the instructions. Telling the model not to do that is the
    approach that has failed five times here (overview F15); the page either says we are the
    buyer or it does not, and that is a question about the transcript, not about the model.

    Blanking rather than leaving the model's guess is also the better answer on the pages where
    stage 1 simply lost the buyer block. There the model invents a plausible wrong company --
    `บริษัท พีม คอนสูร์ฟอร์`, tax id `0105541008369`, an address in a district we have no office
    in. Both score zero. But a blank sends FA back to the paper, whereas a well-formed wrong
    company reads as a fact and gets typed into FM-FA-05.
    """
    if not FILL_BUYER_FROM_CONSTANT:
        return full
    named = buyer_is_named(transcript)
    for part in PARTY_PARTS:
        full[f"buyer{part}"] = ({"value": BUYER_IDENTITY[part], "confidence": 1.0} if named
                                else {"value": None, "confidence": 0.0})
    return full


def has_money(candidate):
    """A bill states an amount that was paid. A page with none of them is not a bill.

    Roughly a third of a real clearing set is ID-card copies, FM-FA-05 summary sheets and
    delivery notes. Asked to skip them the model still assembles a bill out of whatever numbers
    it finds -- on a national ID card it returned the card's laser code as a document number and
    the holder as the seller. Telling it not to made the rest of the extraction worse, so the
    test lives here instead: no amount anywhere, no bill.
    """
    return any((candidate.get(f) or {}).get("value") is not None for f in MONEY_FIELDS)


def assemble(reduced, page_index=0, transcript=None):
    """Reduced model output -> the agreed response shape.

    `page_index` is 0-based, matching the numbering the contract used inside `regions`.
    Candidates with no amount at all are dropped, so `candidateIndex` numbers the bills we
    actually return rather than the raw model output.
    """
    out = []
    # A withholding-tax certificate states a payment base and the tax withheld on it. Neither is
    # a document total, so has_money reads the page as a non-bill and drops it -- intermittently,
    # because the model files those two numbers in a different place on every run. `certlink`
    # needs the page to survive so it can either fold the tax into the bill it belongs to or,
    # when it cannot find one, hand the certificate to FA as its own row instead of losing it.
    keep_regardless = certlink.is_certificate(transcript)
    kept = [c for c in reduced.get("billCandidates", [])
            if keep_regardless or has_money(c)]
    declared = declared_fields()
    for i, c in enumerate(kept):
        # Every candidate owns its own page from birth, so a response is contract-shaped even on
        # the single-page path the eval harness uses. `regions.attach_regions` rebuilds this
        # authoritatively once the merge knows the full span.
        full = {"candidateIndex": i, PAGE_INDEX_FIELD: page_index,
                "regions": [R.whole_page(page_index, PAGE_INDEX_FIELD)], "evidence": []}
        for f in STRING_FIELDS + NUMBER_FIELDS + ["documentDate", "currency"]:
            w = c.get(f) or {"value": None, "confidence": 0.0}
            value, confidence = w.get("value"), w.get("confidence", 0.0)
            if f in ("sellerTaxId", "buyerTaxId"):
                # Validate the shape only. Deciding which side an id belongs to is left to
                # resolve_parties below, which can move it; rejecting our own id here would
                # destroy it instead, and on these pages it is usually the only correct tax id
                # in the transcript.
                value = clean_tax_id(value, reject_buyer=False)
            elif f == "originalDocumentNumber":
                value = clean_document_number(value, transcript)
            elif f == "paymentMethod":
                value = clean_payment_method(value)
            if value is None and w.get("value") is not None:
                confidence = 0.0      # we rejected it, so we are not confident in the blank
            full[f] = {"value": value, "confidence": confidence}
        for f in NULL_STRING_FIELDS:
            if f in declared:
                full[f] = {"value": None, "confidence": 0.0}
        full["lineItems"] = [
            {k: {"value": (item.get(k) or {}).get("value"),
                 "confidence": (item.get(k) or {}).get("confidence", 0.0)}
             for k in LINE_ITEM_FIELDS}
            for item in c.get("lineItems", [])]
        out.append(apply_buyer_identity(resolve_parties(full), transcript))
    return {"billCandidates": out}


_DECLARED = {}


def declared_fields():
    """The candidate keys their schema file declares, read live from their file.

    Cached on the file's mtime rather than once per process. Their contract is a file on a shared
    path that a person replaces by hand, so a new one has to take effect without a restart --
    while `assemble` must not re-parse 45 KB of JSON on every page.

    Used to decide whether a key their contract requires may be sent yet. Both directions are
    failures: a key their current file does not declare fails `additionalProperties: false` and
    voids the whole chunk, while a required key we omit fails validation just as hard. Asking the
    file is the only answer that is right before *and* after they hand over a new one -- which
    matters here, because the last two contract updates were announced without the file arriving.
    """
    stamp = CONTRACT_SCHEMA.stat().st_mtime_ns
    if _DECLARED.get("stamp") != stamp:
        schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
        _DECLARED.update(
            stamp=stamp,
            keys=frozenset(schema["properties"]["billCandidates"]["items"]["properties"]))
    return _DECLARED["keys"]


def contract_schema():
    """Their schema file with the dropped fields removed.

    Derived from their file at run time rather than kept as an edited copy, so if they send a new
    contract we inherit every change they made instead of validating against a stale fork. When
    they publish the trimmed schema themselves, delete this and read their file directly.
    """
    schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    bill = schema["properties"]["billCandidates"]["items"]

    for f in DROPPED_FIELDS:
        bill["properties"].pop(f, None)
    bill["required"] = [f for f in bill["required"] if f not in DROPPED_FIELDS]

    bill["properties"][PAGE_INDEX_FIELD] = {"type": "integer", "minimum": 0}
    bill["required"].append(PAGE_INDEX_FIELD)

    def wrapped(kind):
        return {"type": "object",
                "properties": {"value": {"anyOf": [{"type": kind}, {"type": "null"}]},
                               "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                "required": ["value", "confidence"], "additionalProperties": False}

    for field, kind in ([(f, "string") for f in ADDED_STRING_FIELDS]
                        + [(f, "number") for f in ADDED_NUMBER_FIELDS]):
        bill["properties"][field] = wrapped(kind)
        bill["required"].append(field)

    item = bill["properties"]["lineItems"]["items"]
    for field, kind in ADDED_LINE_ITEM_FIELDS.items():
        item["properties"][field] = wrapped(kind)
        item["required"].append(field)

    def drop_source_regions(node):
        """sourceRegions sits inside every wrapped field, including the ones in lineItems."""
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict) and "sourceRegions" in props and "value" in props:
            props.pop("sourceRegions")
            node["required"] = [f for f in node.get("required", []) if f != "sourceRegions"]
        for value in node.values():
            if isinstance(value, dict):
                drop_source_regions(value)

    drop_source_regions(bill)
    return schema


def validate(response):
    """Check against the agreed contract. Returns (ok, error_message)."""
    try:
        jsonschema.validate(response, contract_schema())
        return True, None
    except jsonschema.ValidationError as e:
        return False, f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"


def extract(transcript, model, options=None, num_ctx=16384, timeout=900,
            category=None, prompt=None):
    """One transcript -> reduced JSON. Raises on transport or parse failure.

    Pass `category` and the matching rules from data/category_rules.json are appended to the
    shared prompt. Pass `prompt` to supply the whole thing yourself, which wins over `category`
    and is mainly for experimenting. Pass neither -- what every existing caller does -- and the
    shared prompt is used unchanged.
    """
    system = prompt or category_prompt(category)
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": transcript}],
            "format": reduced_schema(),
            "stream": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx}}
    body.update(options or {})
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def run(transcripts, models=None):
    """Run every model over every transcript.

    `transcripts` is {label: text}. Returns records with the assembled contract response,
    whether it validates, and how long it took.
    """
    import time
    models = models or CANDIDATES
    records = []
    for label, (tag, options) in models.items():
        for name, text in transcripts.items():
            started = time.perf_counter()
            reduced, response, error = None, None, None
            try:
                reduced = extract(text, tag, options)
                response = assemble(reduced)
                ok, verr = validate(response)
                error = verr
            except Exception as e:
                ok, error = False, str(e)
            elapsed = time.perf_counter() - started
            records.append({"source": name, "model": label, "reduced": reduced,
                            "response": response, "valid": ok, "error": error,
                            "seconds": elapsed})
            n = len(response["billCandidates"]) if response else 0
            print(f"{label:<14}{name:<34}{elapsed:6.1f}s  "
                  f"{'valid' if ok else 'INVALID'}  {n} candidate(s)"
                  + (f"  {error}" if error else ""))
    return records


def load_transcripts(pattern="eval/spot/*typhoon-3b.txt"):
    """Pick up the Typhoon transcripts saved by spot_test.compare()."""
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(Path().glob(pattern))}


def fields(record, keys=("documentDate", "sellerName", "sellerTaxId",
                         "originalDocumentNumber", "currency", "originalTotal",
                         "vat", "clearingAmount")):
    """Flatten one candidate to {field: value} for eyeballing against the receipt."""
    cands = (record["response"] or {}).get("billCandidates") or []
    if not cands:
        return {}
    c = cands[0]
    return {k: c[k]["value"] for k in keys}
