"""Fixtures for the looped-page retry in serving/pipeline.extract_page.

test_degeneracy.py proves the metric can tell a looped transcript from a real one. It does not
prove the pipeline does anything about it, and those are separate failures -- a correct detector
wired to nothing still loses the 120 baht. So stage 1 and stage 2 are both replaced with stubs
here and the assertions are about control flow: was the page read again, at what size, was the
second answer the one that got used, and does a page that read fine the first time still cost
exactly one model call.

No GPU, no Ollama, no server.

    ../.venv/Scripts/python.exe eval/test_retry.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src"), str(HERE.parent / "serving")]

import config
import pipeline

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<54} got {got!r}" + ("" if ok else f"  want {want!r}"))


LOOPED = (HERE / "degenerate" / "p198-loop.txt").read_text(encoding="utf-8")
CLEAN = (HERE / "degenerate" / "p200-ok.txt").read_text(encoding="utf-8")


class Stub:
    """Stands in for stage 1 and stage 2. Records every call so the flow can be asserted on."""

    def __init__(self, by_dim, default=CLEAN, deadline=None, burn=0.0):
        self.by_dim, self.default = by_dim, default
        self.reads, self.extracts = [], 0
        # A stub returns instantly, so without this the clock never moves and the deadline
        # branch is unreachable. `burn` is how many seconds the fake model pretends to take.
        self.deadline, self.burn = deadline, burn

    def __enter__(self):
        self._saved = (pipeline.ocr_page, pipeline.s2.extract, pipeline.s2.assemble)

        def ocr_page(image, *a, **kw):
            self.reads.append(image)                       # the stub image IS its target_dim
            if self.deadline is not None and self.burn:
                self.deadline.started -= self.burn
            return self.by_dim.get(image, self.default)

        def extract(text, *a, **kw):
            self.extracts += 1
            return {"billCandidates": [{"originalTotal": {"value": 120.0, "confidence": 0.9}}]}

        pipeline.ocr_page, pipeline.s2.extract = ocr_page, extract
        pipeline.s2.assemble = lambda reduced, **kw: reduced
        return self

    def __exit__(self, *exc):
        pipeline.ocr_page, pipeline.s2.extract, pipeline.s2.assemble = self._saved


def rerender(page_index, dim):
    return dim                                            # the "image" is just its size


def run_page(by_dim, dims=(1300, 2000), deadline_s=600.0, with_rerender=True, burn=0.0):
    saved, config.RETRY_TARGET_DIMS = config.RETRY_TARGET_DIMS, dims
    deadline = pipeline.Deadline(deadline_s)
    try:
        with Stub(by_dim, deadline=deadline, burn=burn) as stub:
            cands, text = pipeline.extract_page(
                1500, 0, deadline, 42, 1, rerender if with_rerender else None)
        return stub, cands, text
    finally:
        config.RETRY_TARGET_DIMS = saved


print("retry-01  a clean first read costs exactly one model call and no re-render")
stub, cands, text = run_page({1500: CLEAN})
check("stage 1 called once", stub.reads, [1500])
check("stage 2 reached", stub.extracts, 1)
check("candidates returned", len(cands), 1)

print("\nretry-02  page 198: looped at 1500, clean at 1300 -- the measured case")
# dim=1500 -> 9893 chars, no amounts.  dim=1300 -> 2471 chars with 45, 25 and 50 in it.
stub, cands, text = run_page({1500: LOOPED, 1300: CLEAN})
check("read twice, second time at 1300", stub.reads, [1500, 1300])
check("the second transcript is the one kept", text.startswith(CLEAN[:40]), True)
check("stage 2 ran on it", stub.extracts, 1)
check("the page is no longer bill-free", len(cands), 1)

print("\nretry-03  the first size that works wins; later sizes are not spent")
stub, _, _ = run_page({1500: LOOPED, 1300: CLEAN, 2000: CLEAN})
check("2000 never tried", 2000 in stub.reads, False)

print("\nretry-04  every size loops -> the page is given up on, not passed to stage 2")
stub, cands, text = run_page({1500: LOOPED, 1300: LOOPED, 2000: LOOPED})
check("all three sizes tried", stub.reads, [1500, 1300, 2000])
check("stage 2 never called on garbage", stub.extracts, 0)
check("reported bill-free, as before the guard existed", cands, [])
check("but the broken transcript is still returned for the log", len(text), len(LOOPED))

print("\nretry-05  a blank page is not worth a re-read, and does not get one")
stub, cands, _ = run_page({1500: ""})
check("read once only", stub.reads, [1500])
check("bill-free", cands, [])

print("\nretry-06  an @ burst is a resolution fault we avoid, not a loop -- no re-read")
stub, cands, _ = run_page({1500: "@" * 400})
check("read once only", stub.reads, [1500])

print("\nretry-07  the deadline outranks the retry")
# Under MIN_STEP_S left there is no room for another page. Better a bill-free page than a
# 504 for the whole chunk -- the other pages have already been read and paid for.
stub, cands, _ = run_page({1500: LOOPED, 1300: CLEAN}, deadline_s=60.0, burn=50.0)
check("no re-read attempted", stub.reads, [1500])
check("no exception, just a bill-free page", cands, [])

print("\nretry-08  RETRY_TARGET_DIMS empty restores exactly the old behaviour")
stub, cands, _ = run_page({1500: LOOPED, 1300: CLEAN}, dims=())
check("read once", stub.reads, [1500])
check("bill-free", cands, [])

print("\nretry-09  and so does passing no rerender at all")
stub, cands, _ = run_page({1500: LOOPED, 1300: CLEAN}, with_rerender=False)
check("read once", stub.reads, [1500])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
