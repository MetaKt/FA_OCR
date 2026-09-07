"""Fixtures for src/degeneracy.py -- telling a looped transcript from a dense one.

The interesting number here is not that it catches page 198. Any threshold catches page 198. It
is that it catches page 198 while clearing all 94 real transcripts on disk, because a guard that
fires on a genuine receipt is worse than no guard: it would throw away a page we had already read
correctly and spend 50 GPU-seconds re-reading it.

So the corpus is the test. eval/transcripts and eval/transcripts-1500 are the pages the accuracy
number was measured on, and every one of them must pass untouched.

    ../.venv/Scripts/python.exe eval/test_degeneracy.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src")]

import degeneracy as D

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got {got!r}" + ("" if ok else f"  want {want!r}"))


def read(rel):
    return (HERE / rel).read_text(encoding="utf-8")


print("shape-01  a transcript with no newline in it is still split into segments")
# The reason a line-based check could never have caught this: page 198 is one single line.
loop = read("degenerate/p198-loop.txt")
check("newlines in the broken page", loop.count("\n"), 0)
check("segments found anyway", len(D.segments(loop)) > 100, True)

print("\nshape-02  empty and near-empty input never raises")
check("empty string", D.verdict(""), "empty")
check("None", D.verdict(None), "empty")
check("79 characters", D.verdict("ก" * 79), "empty")
check("score of empty is neutral, not a divide by zero", D.score(""), (1.0, 1.0, 0))

print("\nat-01  the original @ burst check still behaves exactly as it did")
check("11 at signs is degenerate", D.verdict("@" * 11 + "x" * 200), "at_burst")
check("10 is not", D.verdict("@" * 10 + "x" * 200), None)
check("and an @ burst is not worth re-reading", D.worth_rereading("at_burst"), False)

print("\nloop-01  page 198 of P06690 -- the 120 baht this whole guard exists for")
ratio, compression, count = D.score(loop)
print(f"        unique={ratio:.2f}  compressed={compression:.2f}  segments={count}")
check("caught", (D.verdict(loop) or "").startswith("loop"), True)
check("and it is worth re-reading", D.worth_rereading(D.verdict(loop)), True)
check("no amounts survived it, which is why it read as bill-free",
      any(a in loop for a in ("45.00", "25.00", "50.00")), False)

print("\nloop-02  the three toll pages that read correctly are left alone")
for name in ("p199-ok.txt", "p200-ok.txt", "p201-ok.txt"):
    check(name, D.verdict(read(f"degenerate/{name}")), None)

print("\ncorpus-01  every real transcript the accuracy number was measured on passes")
# 94 files. One false positive here and the guard is not shippable.
corpus = sorted(p for d in ("transcripts", "transcripts-1500") for p in (HERE / d).glob("*.txt"))
if not corpus:
    # Both corpus directories are gitignored: they are real transcripts carrying employee names
    # and national IDs, and git history is permanent. So a fresh clone cannot run this block. Say
    # so loudly rather than passing silently -- a guard whose false-positive test did not run is
    # a guard nobody has checked.
    print("  SKIP  eval/transcripts*/ absent (gitignored: real personal data).")
    print("        This is the block that proves the guard does not fire on correct pages.")
    print("        Re-run it on the machine that holds the transcripts before trusting a")
    print("        threshold change in src/degeneracy.py.")
else:
    check("corpus found", len(corpus) >= 90, True)
    flagged = [(p.name, D.verdict(p.read_text(encoding="utf-8"))) for p in corpus]
    check("false positives", [f for f in flagged if f[1]], [])

    worst_comp = min((D.score(p.read_text(encoding="utf-8"))[1], p.name) for p in corpus)
    tight_ratio = min((D.score(p.read_text(encoding="utf-8"))[0], p.name) for p in corpus)
    print(f"        closest real page by unique-ratio: {tight_ratio[1]} at {tight_ratio[0]:.2f} "
          f"(threshold {D.MAX_REPEAT_RATIO})")
    print(f"        closest real page by compression:  {worst_comp[1]} at {worst_comp[0]:.2f} "
          f"(threshold {D.MAX_COMPRESSION})")
    check("the closest real page still clears the ratio threshold by 2x",
          tight_ratio[0] > D.MAX_REPEAT_RATIO * 2, True)
    check("and clears the compression threshold by 2x",
          worst_comp[0] > D.MAX_COMPRESSION * 2, True)

print("\nsplit-01  a loop separated by newlines rather than tags is still a loop")
# Page 13 of P06690, found 2026-09-03 by transcribing all 138 corpus pages rather than the 47
# that had saved transcripts. 10,306 characters in which one footer line repeats 54 times,
# separated by newlines. `segments` split on tags only, so it saw 15 segments, every one
# distinct, scored the page 1.00 and cleared it -- while zlib compressed it to 0.06.
footer = "ใบเสร็จรับเงินและใบกำกับภาษีฉบับนี้จัดทำขึ้นโดยไม่ต้องมีลายเซ็นของเจ้าหน้าที่บริษัท"
newline_loop = ("<h1>ใบกำกับภาษี/ใบเสร็จรับเงิน บริษัท ซีอาร์ซี ไทวัสดุ จำกัด</h1>\n"
                + f"{footer}\n*   ส่วนลด 0.00 บาท (ไม่มี)\n*   รวมยอดขาย: 821.00 บาท\n" * 54)
check("no tags to split on inside the repeated part", newline_loop.count("<"), 2)
check("caught", bool(D.verdict(newline_loop)), True)
check("and it is worth re-reading", D.worth_rereading(D.verdict(newline_loop)), True)

print("\nsplit-02  splitting on tags is still needed -- newlines alone would miss page 198")
# The opposite failure, and the reason both delimiters are used. A looped HTML page arrives as
# one enormous line: newline-splitting alone would see a single segment and score it 1.00.
tag_loop = "<tr><td>การทางพิเศษแห่งประเทศไทย โทร 1543</td></tr>" * 40
check("no newline anywhere in it", "\n" in tag_loop, False)
check("still caught", bool(D.verdict(tag_loop)), True)

print("\nsplit-03  a page whose lines genuinely differ is untouched by the new split")
# The guard against the obvious over-reach: an itemised invoice has many lines, all distinct.
itemised = "<h1>ใบกำกับภาษี</h1>\n" + "".join(
    f"รายการที่ {i} ค่าบริการงวดเดือน {i} จำนวน {i * 37}.00 บาท\n" for i in range(300))
r, c, n = D.score(itemised)
check("many segments now, as intended", n > 200, True)
check("but nearly all distinct", r > D.MAX_REPEAT_RATIO, True)
check("so it is cleared", D.verdict(itemised), None)

print("\nsplit-04  a repeated table header is a loop, even though each cell is a short label")
# Page 118 of P06690, a handwritten บิลเงินสด worth 3,514 baht. The model emitted the table's
# header row ~50 times instead of the one filled row. Every repeated piece is a column label of
# 6-11 characters, so MIN_SEGMENT_CHARS=12 threw them all away, leaving 9 genuinely distinct
# segments and a score of 1.00. The cells are not empty, so `collapse_empty_rows` cannot help.
header_loop = ("<h1>บิลเงินสด CASHSALE</h1>\n<p>วันที่ DATE: 23/07/2569</p>\n" +
               "<tr><td></td><td>รายการ<br/>DESCRIPTION<br/>貨名</td><td></td>"
               "<td>หน่วยละ<br/>UNIT PRICE<br/>價格</td><td>จำนวนเงิน<br/>AMOUNT</td></tr>" * 50)
check("the repeated labels are all under the old floor of 12",
      max(len(s) for s in ("รายการ", "DESCRIPTION", "หน่วยละ", "UNIT PRICE", "貨名")) < 12, True)
check("caught", bool(D.verdict(header_loop)), True)
check("and worth re-reading", D.worth_rereading(D.verdict(header_loop)), True)

print("\nsplit-05  the lower floor still discards punctuation and stray digits")
# What MIN_SEGMENT_CHARS is for. A page of short numeric cells must not become a loop just
# because "100.00" appears in several rows of a genuine itemised bill.
numeric = "<h1>ใบกำกับภาษี บริษัท ทดสอบ จำกัด</h1>\n" + "".join(
    f"<tr><td>{i}</td><td>ค่าบริการรายการที่ {i} ประจำงวด</td><td>{100 + i}.00</td></tr>"
    for i in range(60))
check("not condemned", D.verdict(numeric), None)

print("\nboth-01  one metric alone is not enough -- both must agree before a page is condemned")
# A long genuine document compresses well without being looped, and a short repetitive header
# scores badly on unique-ratio without being looped. Neither alone may condemn a page.
long_but_varied = "".join(f"<tr><td>รายการที่ {i} ค่าบริการงวด</td><td>{i * 37}.00</td></tr>"
                          for i in range(400))
r, c, n = D.score(long_but_varied)
check("compresses far below the threshold", c <= D.MAX_COMPRESSION, True)
check("but every segment is distinct, so it is cleared", D.verdict(long_but_varied), None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
