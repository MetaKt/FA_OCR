"""
Accuracy harness: turn "this looks about right" into a number.

Three jobs:

1. `run_cases()`   -- run both pipeline stages over every golden case and keep the responses
2. `build_inputs()` -- write the two files the webapp team's TypeScript scorer expects
3. `check_response()` / `confidence_report()` -- the things their scorer structurally cannot check

Their scorer only handles flat string fields, so it says nothing about whether the response was
even schema-valid, whether the right number of bills was found, or whether `confidence` means
anything. Those are checked here instead.

Scoring is deliberately NOT reimplemented. `scoring/scoreExtraction.ts` in their handover is the
authority on what counts as correct; a second implementation here would drift from it and give us
a comfortable number that their code disagrees with.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import jsonschema

import stage2_extract as s2
from ocr_pipeline import (DEFAULT_TARGET_DIM, build_prompt, collapse_empty_rows, load_pages,
                          ocr_page)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "data" / "golden" / "golden.json"
OUT = ROOT / "eval" / "results"
TRANSCRIPTS = ROOT / "eval" / "transcripts"

# Their TypeScript scorer, run from the handover folder as they shipped it. Not vendored in here:
# it is their file and it is the authority on what counts as correct, so it stays theirs.
SCORER = (Path.home() / "Downloads" / "adv-clear-model-handover"
          / "adv-clear-model-handover" / "scoring")

STAGE1_MODEL = "scb10x/typhoon-ocr1.5-3b"
STAGE2_MODEL = "qwen3:4b"
STAGE2_OPTIONS = {"think": False}

# Fields the scorer compares as amounts. Written with two decimals so a diff is readable; their
# normalizer would accept other spellings but being canonical here keeps mismatch output clean.
#
# `discount` and `vatExemptAmount` are money too, but their normalizer's own AMOUNT_FIELDS list
# predates them, so it will compare these as plain text. Two decimals on both sides is what makes
# that work -- write "18.15" and "0.00" in the answer key, never "18.150" or "0".
AMOUNT_FIELDS = {"originalTotal", "amountBeforeVat", "vat", "withholdingTax", "clearingAmount",
                 "discount", "vatExemptAmount"}

# Rates are whole numbers on every document seen so far: VAT 7, withholding 1/3/5. The model
# sometimes returns 7 and sometimes 7.0, which their text comparison would call a mismatch, so
# they are pinned to the integer spelling here. Write "7" and "3" in the answer key.
RATE_FIELDS = {"vatRate", "withholdingTaxRate"}

TODO = "TODO"


# --------------------------------------------------------------------------- golden bookkeeping

def load_golden(path=GOLDEN):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def todo_report(golden=None):
    """How much of the answer key is still unfilled. Run this while typing it in."""
    golden = golden or load_golden()
    done = pending = 0
    print(f"{'caseId':<16}{'filled':>8}{'todo':>6}   remaining fields")
    for case in golden["cases"]:
        todos = [k for k, v in case["fields"].items() if v == TODO]
        filled = len(case["fields"]) - len(todos)
        done += filled
        pending += len(todos)
        print(f"{case['caseId']:<16}{filled:>8}{len(todos):>6}   {', '.join(todos) or '-'}")
    print(f"\n{done} filled, {pending} still TODO")
    return pending


def scorable(golden):
    """Drop TODO fields and any case with nothing filled in yet.

    This is what makes a partly-typed answer key useful: fill in two receipts and you already get
    a real number, instead of having to finish all nine before learning anything.
    """
    cases = []
    for case in golden["cases"]:
        fields = {k: v for k, v in case["fields"].items() if v != TODO}
        if fields:
            cases.append({**case, "fields": fields})
    return {"cases": cases}


# --------------------------------------------------------------------------- running the pipeline

def run_cases(golden=None, target_dim=DEFAULT_TARGET_DIM, reuse_transcripts=True):
    """Run stage 1 + stage 2 for every case. Returns {caseId: contract response}.

    Stage 1 is the slow half, so its output is cached on disk under eval/transcripts/ and reused
    unless reuse_transcripts=False. When iterating on the stage-2 prompt this turns a 90-second
    loop into a 15-second one.
    """
    golden = golden or load_golden()
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    prompt1 = build_prompt(figure_language="English")
    responses = {}

    for case in golden["cases"]:
        case_id = case["caseId"]
        src, page = case["source"], case.get("page", 1)
        cached = TRANSCRIPTS / f"{case_id}.txt"

        if reuse_transcripts and cached.exists():
            text, t_ocr = cached.read_text(encoding="utf-8"), 0.0
        else:
            img = next(img for n, img in load_pages(ROOT / src, target_dim) if n == page)
            started = time.perf_counter()
            text = ocr_page(img, prompt1, model=STAGE1_MODEL)
            t_ocr = time.perf_counter() - started
            cached.write_text(text, encoding="utf-8")

        if text is None or len(text) < 80 or text.count("@") > 10:
            print(f"{case_id:<16} stage 1 FAILED -- no usable transcript")
            responses[case_id] = None
            continue

        # Cached transcripts on disk stay raw, so this runs every time rather than once at OCR:
        # the cleaning rule can change without invalidating an hour of stage-1 output.
        text = collapse_empty_rows(text)

        started = time.perf_counter()
        reduced = s2.extract(text, STAGE2_MODEL, STAGE2_OPTIONS)
        response = s2.assemble(reduced, page_index=page - 1, transcript=text)
        t_ext = time.perf_counter() - started
        responses[case_id] = response

        ok, err = s2.validate(response)
        print(f"{case_id:<16} ocr {t_ocr:5.1f}s  ext {t_ext:5.1f}s  "
              f"{'valid' if ok else 'INVALID: ' + str(err)}  "
              f"{len(response['billCandidates'])} bill(s)")
    return responses


# --------------------------------------------------------------------------- adapter to their scorer

def _as_string(field, value):
    if value is None:
        return None
    if field in AMOUNT_FIELDS:
        return f"{float(value):.2f}"
    if field in RATE_FIELDS:
        return f"{int(float(value))}"
    return str(value)


def _matches(field, actual, expected):
    """Is this one field right? Used by the confidence buckets, never for the official score.

    Two rules, both there because their absence produced a number that looked like a score and
    disagreed with the real one:

    Nulls compare as nulls. `str(actual) == str(expected)` turns a correctly-predicted null into
    the string "None" and compares it against "", so every field the model rightly called absent
    counted as wrong -- 40 of 47 nulls on the twelve golden cases, dragging the report from 57% to
    38% and flattening the very buckets it exists to show.

    Amounts and rates compare as numbers. An answer key may say "1750" where the model says
    "1750.00"; their normalizer parses both and calls it a match, and so must this, or the two
    numbers drift apart for no reason anyone can see (five fields, 121 versus 126).

    Still cruder than their TypeScript normalizer on purpose -- it does not know that "13/07/26"
    and "2026-07-13" are the same date, and it does not try. `scoring/scoreExtraction.ts` remains
    the authority; this only has to be close enough that a gap means something real.
    """
    if actual is None or expected is None:
        return actual is None and expected is None
    if field in AMOUNT_FIELDS or field in RATE_FIELDS:
        try:
            return abs(float(actual) - float(expected)) < 0.005
        except (TypeError, ValueError):
            pass        # a non-numeric answer key -- fall through and compare it as text
    return actual.strip().lower() == str(expected).strip().lower()


def flatten(response, fields):
    """One contract response -> the flat {field: string} their scorer wants.

    Deliberately dumb. Any cleverness here -- picking the best-matching candidate, fuzzy field
    mapping -- would repair the model's mistakes inside the harness and inflate the score.

    More than one candidate on a single-bill case is a real defect (over-splitting), so it is
    reported as a total miss rather than quietly taking the first one.
    """
    if response is None:
        return {}
    candidates = response.get("billCandidates") or []
    if len(candidates) != 1:
        return {}
    c = candidates[0]
    return {f: _as_string(f, c[f]["value"]) for f in fields if f in c}


def build_inputs(responses, golden=None, out_dir=OUT):
    """Write golden.scorable.json and prediction.json, then print the command to run."""
    golden = golden or load_golden()
    target = scorable(golden)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prediction = {"cases": [
        {"caseId": case["caseId"],
         "fields": flatten(responses.get(case["caseId"]), case["fields"].keys())}
        for case in target["cases"]]}

    gold_path = out_dir / "golden.scorable.json"
    pred_path = out_dir / "prediction.json"
    gold_path.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
    pred_path.write_text(json.dumps(prediction, ensure_ascii=False, indent=2), encoding="utf-8")

    n_fields = sum(len(c["fields"]) for c in target["cases"])
    print(f"{len(target['cases'])} case(s), {n_fields} field(s) to score")
    print(f"\nRun their scorer -- this, not anything here, is the official number:\n"
          f"  cd \"{SCORER}\" && npm run score -- \"{gold_path}\" \"{pred_path}\"")
    return gold_path, pred_path


# --------------------------------------------------------------------------- our own checks

def check_response(responses):
    """The properties their scorer cannot see, and the most important one is the first."""
    schema = s2.contract_schema()
    valid = total_candidates = failed = 0
    confidences = []

    for response in responses.values():
        if response is None:
            failed += 1
            continue
        try:
            jsonschema.validate(response, schema)
            valid += 1
        except jsonschema.ValidationError:
            pass
        for c in response["billCandidates"]:
            total_candidates += 1
            confidences += [c[k]["confidence"] for k in c
                            if isinstance(c[k], dict) and "confidence" in c[k]]

    n = len(responses)
    print(f"stage 1 failures   {failed}/{n}")
    print(f"schema valid       {valid}/{n - failed}")
    print(f"bills found        {total_candidates}")
    if confidences:
        confidences.sort()
        p50 = confidences[len(confidences) // 2]
        flat = confidences[0] == confidences[-1]
        print(f"confidence         min {confidences[0]:.2f}  p50 {p50:.2f}  "
              f"max {confidences[-1]:.2f}" + ("   FLAT -- useless for the review queue" if flat else ""))


def confidence_report(responses, golden=None, buckets=((0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.01))):
    """Is `confidence` actually predictive? This is what makes the UI highlighting worth having.

    Buckets every scored field by the model's confidence and shows how often each bucket was
    right. Accuracy should climb across the buckets. If it is flat, the number is decoration and
    FA's attention would be misdirected -- which is worse than showing no confidence at all.
    """
    golden = golden or load_golden()
    tally = {b: [0, 0] for b in buckets}

    for case in scorable(golden)["cases"]:
        response = responses.get(case["caseId"])
        if response is None or len(response.get("billCandidates") or []) != 1:
            continue
        c = response["billCandidates"][0]
        for field, expected in case["fields"].items():
            if field not in c:
                continue
            actual = _as_string(field, c[field]["value"])
            conf = c[field]["confidence"]
            correct = _matches(field, actual, expected)
            for b in buckets:
                if b[0] <= conf < b[1]:
                    tally[b][1] += 1
                    tally[b][0] += int(correct)
                    break

    print(f"{'confidence':<14}{'correct':>9}{'n':>6}{'accuracy':>11}"
          "     n = fields in this confidence band")
    for (lo, hi), (correct, n) in tally.items():
        acc = f"{100 * correct / n:.0f}%" if n else "-"
        print(f"{lo:.2f}-{min(hi, 1.0):.2f}    {correct:>9}{n:>6}{acc:>11}")

    total_correct = sum(c for c, _ in tally.values())
    total_n = sum(n for _, n in tally.values())
    if total_n:
        print(f"{'all':<14}{total_correct:>9}{total_n:>6}"
              f"{100 * total_correct / total_n:>10.0f}%   approximate -- the scorer is the number")
    print("\nAccuracy should rise across the rows. Flat means confidence is not predictive.")


if __name__ == "__main__":
    todo_report()
