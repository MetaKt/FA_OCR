"""Fixtures for the withholding-tax certificate rule, built by hand so the answer is known.

Synthetic transcripts rather than real pages, for the same reason test_merge.py uses synthetic
candidates: a document fixture exercises OCR, extraction and linking at once, so a failure does
not say which one broke. The rule was also verified end to end against three real certificates
(P06690 pages 86 and 90, and the DT05 sample) -- see plan/00-OVERVIEW.md.

The tax ids below are real-format but invented, and every one is checksum-valid so that the
mod-11 test is exercised rather than accidentally passed.

    ../.venv/Scripts/python.exe eval/test_certlink.py
"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

import certlink as CL

passed = failed = 0

OURS = CL.BUYER_TAX_ID          # TEAM, the withholder on every certificate
VENDOR = "0105546117469"        # checksum-valid
OTHER = "0105562192879"         # checksum-valid, a different vendor


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<54} got {got!r}"
          + ("" if ok else f"  want {want!r}"))


def cert_text(vendor=VENDOR, base="1,300.00", tax="39.00", extra=""):
    """A certificate stripped to the parts the rule reads."""
    return (f"หนังสือรับรองการหักภาษี ณ ที่จ่าย\n"
            f"ตามมาตรา 50 ทวิ แห่งประมวลรัษฎากร\n"
            f"เลขที่ WHT26070025\n"
            f"ผู้มีหน้าที่หักภาษี ณ ที่จ่าย : {OURS}\n"
            f"ผู้ถูกหักภาษี ณ ที่จ่าย : {vendor}\n"
            f"<table><tr><td>6. อื่นๆ (ระบุ) ค่าบริการ</td><td>10/07/2026</td>"
            f"<td>{base}</td><td>{tax}</td></tr></table>\n{extra}")


def w(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


def bill(tax_id=VENDOR, base=1300.0, date="2026-07-10", page=0, wht=None):
    return {"sellerTaxId": w(tax_id), "amountBeforeVat": w(base), "documentDate": w(date),
            "withholdingTax": w(wht), "withholdingTaxRate": w(None),
            "chunkPageIndex": page, "candidateIndex": 0}


# --------------------------------------------------------------------------- detection

print("detect-01  a certificate is recognised, an ordinary receipt is not")
check("certificate", CL.is_certificate(cert_text()), True)
check("receipt mentioning หัก ณ ที่จ่าย is not one",
      CL.is_certificate("ใบเสร็จรับเงิน\nหัก ณ ที่จ่าย 3% 39.00\nรวม 1,300.00"), False)
check("empty page", CL.is_certificate(""), False)

print("\ndetect-02  the mod-11 check digit")
check("a real id passes", CL.valid_thai_tax_id(OURS), True)
check("the DT05 phantom fails", CL.valid_thai_tax_id("0107685000030"), False)
check("twelve digits", CL.valid_thai_tax_id("010756100003"), False)
check("not digits", CL.valid_thai_tax_id("01075610000ab"), False)

# --------------------------------------------------------------------------- reading

print("\nread-01   the base/tax pair proves itself through the rate")
info = CL.read_certificate(cert_text())
check("counterparty", info["taxId"], VENDOR)
check("base", info["base"], 1300.0)
check("tax", info["tax"], 39.0)
check("rate", info["rate"], 3.0)

print("\nread-02   our own id is never the counterparty, and a phantom id is discarded")
check("phantom ignored, one real id left",
      CL.counterparty_tax_id(cert_text(extra="เลขประจำตัวผู้เสียภาษีอากร 0107685000030")), VENDOR)
check("two real vendors is ambiguous",
      CL.counterparty_tax_id(cert_text() + f"\n{OTHER}"), None)

print("\nread-03   amounts whose ratio is not a legal rate are not a base/tax pair")
check("7% VAT is not a withholding rate",
      CL.base_and_tax("รวม 1,300.00 ภาษีมูลค่าเพิ่ม 91.00"), None)
check("1.5% is legal", CL.base_and_tax("2,000.00 30.00"), (2000.0, 30.0, 1.5))

print("\nread-04   a certificate covering two payments goes to a human, not to a bill")
two = cert_text() + "<table><tr><td>ค่าบริการ</td><td>2,000.00</td><td>60.00</td></tr></table>"
check("two distinct bases -> refuse", CL.base_and_tax(two), None)

# --------------------------------------------------------------------------- matching

print("\nmatch-01  tax id and amount both agree -> attach, and the certificate row disappears")
transcripts = {0: "ใบเสร็จรับเงิน " + VENDOR, 1: cert_text()}
out, report = CL.apply_certificates([bill(page=0), {**bill(page=1), "sellerTaxId": w(None)}],
                                    transcripts)
check("candidates", len(out), 1)
check("attached", report[0]["attached"], True)
check("withholdingTax", out[0]["withholdingTax"]["value"], 39.0)
check("withholdingTaxRate", out[0]["withholdingTaxRate"]["value"], 3.0)

print("\nmatch-02  the amount alone is not enough -- a different vendor is a different bill")
transcripts = {0: "ใบเสร็จรับเงิน " + OTHER, 1: cert_text()}
out, report = CL.apply_certificates([bill(tax_id=OTHER, page=0),
                                     {**bill(page=1), "sellerTaxId": w(None)}], transcripts)
check("not attached", report[0]["attached"], False)
check("certificate survives as its own row", len(out), 2)

print("\nmatch-03  the tax id alone is not enough -- same vendor, different amount")
transcripts = {0: "ใบเสร็จรับเงิน " + VENDOR, 1: cert_text()}
out, report = CL.apply_certificates([bill(base=999.0, page=0),
                                     {**bill(page=1), "sellerTaxId": w(None)}], transcripts)
check("not attached", report[0]["attached"], False)

print("\nmatch-04  two purchases, same vendor and amount -> the date on the certificate decides")
transcripts = {0: "ใบเสร็จ " + VENDOR, 1: "ใบเสร็จ " + VENDOR, 2: cert_text()}
cert_cand = {**bill(page=2, date="2026-07-10"), "sellerTaxId": w(None)}
out, report = CL.apply_certificates(
    [bill(page=0, date="2026-07-10"), bill(page=1, date="2026-07-22"), cert_cand], transcripts)
check("attached", report[0]["attached"], True)
check("to the bill whose date matches", report[0]["target_page"], 0)
check("the other bill is untouched", out[1]["withholdingTax"]["value"], None)

print("\nmatch-04b same vendor and amount, and the certificate states no date -> refuse")
transcripts = {0: "ใบเสร็จ " + VENDOR, 1: "ใบเสร็จ " + VENDOR, 2: cert_text()}
undated = {**bill(page=2), "sellerTaxId": w(None), "documentDate": w(None)}
out, report = CL.apply_certificates(
    [bill(page=0, date="2026-07-10"), bill(page=1, date="2026-07-22"), undated], transcripts)
check("not attached", report[0]["attached"], False)
check("both bills and the certificate all survive", len(out), 3)

print("\nmatch-04c a form heading is not a seller name, and must not manufacture a conflict")
transcripts = {0: "ใบเสร็จ " + VENDOR, 1: cert_text()}
labelled = {**bill(page=1), "sellerTaxId": w(None),
            "sellerName": w("ผู้ถูกหักภาษี ณ ที่จ่าย")}
out, report = CL.apply_certificates(
    [{**bill(page=0), "sellerName": w("บริษัท สยามสตีล กัลวาไนซิ่ง จำกัด")}, labelled],
    transcripts)
check("attached", report[0]["attached"], True)

print("\nmatch-05  the same purchase stapled twice is one purchase, not ambiguity")
transcripts = {0: "ใบแจ้งหนี้ " + VENDOR, 1: "ใบเสร็จ " + VENDOR, 2: cert_text()}
out, report = CL.apply_certificates(
    [bill(page=0), bill(page=1), {**bill(page=2), "sellerTaxId": w(None)}], transcripts)
check("attached", report[0]["attached"], True)
check("duplicates still emitted for FA, certificate absorbed", len(out), 2)

print("\nmatch-06  the seller id is read from the page when the field is null (DT05)")
transcripts = {0: "ใบเสร็จรับเงิน เลขประจำตัวผู้เสียภาษีอากร " + VENDOR, 1: cert_text()}
out, report = CL.apply_certificates(
    [{**bill(page=0), "sellerTaxId": w(None)}, {**bill(page=1), "sellerTaxId": w(None)}],
    transcripts)
check("attached from the transcript alone", report[0]["attached"], True)
check("withholdingTax", out[0]["withholdingTax"]["value"], 39.0)

print("\nmatch-07  a withholding tax the bill already states is never overwritten")
transcripts = {0: "ใบเสร็จรับเงิน " + VENDOR, 1: cert_text()}
out, _ = CL.apply_certificates([bill(page=0, wht=25.0),
                                {**bill(page=1), "sellerTaxId": w(None)}], transcripts)
check("kept the bill's own figure", out[0]["withholdingTax"]["value"], 25.0)

print("\nmatch-08  no certificate in the chunk -> nothing is touched")
before = [bill(page=0), bill(page=1)]
out, report = CL.apply_certificates(before, {0: "ใบเสร็จ", 1: "ใบเสร็จ"})
check("unchanged", out, before)
check("no report", report, [])

print("\nindex-01  candidateIndex stays sequential after a certificate is absorbed")
transcripts = {0: "ใบเสร็จ " + VENDOR, 1: cert_text(), 2: "ใบเสร็จ " + OTHER}
out, _ = CL.apply_certificates(
    [bill(page=0), {**bill(page=1), "sellerTaxId": w(None)},
     bill(tax_id=OTHER, base=500.0, page=2)], transcripts)
check("indices", [c["candidateIndex"] for c in out], [0, 1])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
