r"""Stage-2 bake-off: scb10x/typhoon2.5-qwen3-4b against the incumbent qwen3:4b.

Eighth stage-2 swap (F17-F21 all rejected). Same method throughout: cached stage-1 transcripts,
so both arms read *identical* text and any difference is stage 2's alone. Nothing here touches
src/ or serving/.

Why it was still worth running after F21 said stop: on paper this is the best-matched stage-2
candidate we have seen -- Qwen3-4B base so same size and speed class as the incumbent, Thai-tuned,
and explicitly NOT a thinking model (F20 showed thinking collapses this task). The prior is still
poor: F17 tested "Thai fine-tune at stage 2" and tied. Recorded as a deliberate last attempt.

`think=False` on both arms, per F20.

Scoring is NOT done here. This writes the two files their TypeScript scorer eats, per tag, and
prints the command. `scoring/scoreExtraction.ts` stays the authority on what counts as correct.

    python run.py base   --limit 3
    python run.py cand   --limit 3
    python run.py cand                # all 34
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval")]

import harness as H
import stage2_extract as s2
from ocr_pipeline import collapse_empty_rows

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

CANDIDATE = "scb10x/typhoon2.5-qwen3-4b"

ARMS = {
    "base": (H.STAGE2_MODEL, {"think": False}),
    "cand": (CANDIDATE,      {"think": False}),
}


def run_arm(tag, limit=None):
    model, options = ARMS[tag]
    golden = H.load_golden()
    cases = golden["cases"][:limit] if limit else golden["cases"]
    print(f"=== {tag}: {model}  options={options}  {len(cases)} case(s) ===\n")

    responses, timings, failures = {}, [], []
    for case in cases:
        case_id = case["caseId"]
        cached = H.TRANSCRIPTS / f"{case_id}.txt"
        if not cached.exists():
            # Deliberately not re-running stage 1 here: an arm that read different text than the
            # other arm is not a comparison of stage 2.
            print(f"{case_id:<16} SKIPPED -- no cached transcript")
            failures.append(case_id)
            continue

        text = collapse_empty_rows(cached.read_text(encoding="utf-8"))
        started = time.perf_counter()
        try:
            reduced = s2.extract(text, model, options)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"{case_id:<16} {elapsed:6.1f}s  ERROR {type(exc).__name__}: {exc}")
            failures.append(case_id)
            responses[case_id] = None
            continue
        response = s2.assemble(reduced, page_index=case.get("page", 1) - 1, transcript=text)
        elapsed = time.perf_counter() - started

        responses[case_id] = response
        timings.append(elapsed)
        ok, err = s2.validate(response)
        print(f"{case_id:<16} {elapsed:6.1f}s  {'valid' if ok else 'INVALID: ' + str(err)}  "
              f"{len(response['billCandidates'])} bill(s)")

    out = RESULTS / tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "responses.json").write_text(
        json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(timings)
    print(f"\n{len(timings)} scored, {len(failures)} failed."
          f"  {total:.1f}s total, {total / max(1, len(timings)):.1f}s/case")
    if failures:
        print(f"  failures: {', '.join(failures)}")

    subset = {"cases": [c for c in golden["cases"] if c["caseId"] in responses]}
    H.build_inputs(responses, golden=subset, out_dir=out)
    return responses


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", choices=sorted(ARMS))
    ap.add_argument("--limit", type=int, default=None)
    run_arm(**vars(ap.parse_args()))
