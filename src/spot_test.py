"""
Side-by-side transcription comparison for the phase 03 model spot test.

Answers one question and only one: which model reads these receipts most accurately?
Plain transcription only -- no JSON, no schema, no field extraction. Structure comes later
from a separate model (see plan/03-model-bakeoff.md section 2, Architecture B).

Every candidate gets the same pages and the same prompt, and the raw text is written to
disk so phase 05 error analysis does not have to re-run inference to get it back.
"""
import base64
import io
import time
from pathlib import Path

from IPython.display import HTML, display

from ocr_pipeline import build_prompt, load_pages, ocr_page

# Label -> Ollama tag. Uncomment a line only after `ollama pull` -- a tag that is not on disk
# fails every page for that model.
#
# typhoon-ocr-7b never fitted: 16 GB on an 8 GB card means most of it runs on the CPU, minutes
# per page. That was also hypothesis 2 for why the 3b appeared to beat it (section 6 below), so
# the comparison was never really made on equal terms.
CANDIDATES = {
    "typhoon-3b": "scb10x/typhoon-ocr1.5-3b",
    # "typhoon-7b": "scb10x/typhoon-ocr-7b",
    #     Deleted from disk 2026-08-20 to reclaim 16 GB, after phase 03 settled on the 3b.
    #     Re-pull before uncommenting, and only on hardware where it fits -- see the note above.
    # "qwen3-vl-4b": "qwen3-vl:4b",
    #     Never pulled on this machine. Rejected on Thai in phase 03 (overview F8).
}

OUT_DIR = Path("eval/spot")


def compare(paths, models=None, prompt=None, target_dim=1800, save=True):
    """Transcribe every page of every path with every model.

    Returns a list of {file, page, model, text, seconds, error} records.

    Iterates model-outer, file-inner on purpose: Ollama keeps one model resident at a time,
    so alternating models per file would reload weights on every single call.
    """
    models = models or CANDIDATES
    # Typhoon rejects substitute prompts -- it echoes its training prompt instead of reading
    # the page. So the official v1.5 text is the only option for it, and since that text is a
    # plain transcription instruction it is a fair prompt for the others too. Identical input
    # keeps the comparison about reading ability rather than prompt-following.
    prompt = prompt or build_prompt(figure_language="English")

    pages = [(p, n, img) for p in paths for n, img in load_pages(p, target_dim)]
    records = []

    for label, tag in models.items():
        for path, page_num, img in pages:
            started = time.perf_counter()
            try:
                text, error = ocr_page(img, prompt, model=tag), None
            except Exception as e:
                text, error = None, str(e)
            elapsed = time.perf_counter() - started

            records.append({"file": Path(path).name, "page": page_num, "model": label,
                            "text": text, "seconds": elapsed, "error": error})
            status = f"{elapsed:6.1f}s" if error is None else "FAILED"
            print(f"{label:<14} {Path(path).name:<32} p{page_num}  {status}")

    if save:
        _save(records)
    return records


def _save(records):
    """One .txt per model per page, so a diff tool can be pointed at them."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for r in records:
        if r["error"]:
            continue
        stem = Path(r["file"]).stem
        (OUT_DIR / f"{stem}_p{r['page']}_{r['model']}.txt").write_text(r["text"], encoding="utf-8")
    print(f"\nsaved {len([r for r in records if not r['error']])} transcripts to {OUT_DIR}")


def _img_tag(img, width=380):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return (f'<img src="data:image/png;base64,{b64}" '
            f'style="width:{width}px;border:1px solid #888;border-radius:4px">')


def show(records, paths, target_dim=1800, height=560):
    """Render the source image beside every model's output, one row per page.

    Model output is injected as HTML because the v1.5 prompt asks for HTML tables, and a
    rendered table is far easier to check against the receipt than raw tags.
    """
    pages = {(Path(p).name, n): img for p in paths for n, img in load_pages(p, target_dim)}

    for (fname, page_num), img in pages.items():
        rows = [r for r in records if r["file"] == fname and r["page"] == page_num]
        if not rows:
            continue

        cells = [f'<div style="flex:0 0 auto">{_img_tag(img)}</div>']
        for r in rows:
            body = r["text"] if r["error"] is None else f'<b>FAILED</b><br>{r["error"]}'
            cells.append(
                '<div style="flex:1 1 380px;min-width:340px">'
                f'<div style="font:600 13px system-ui;padding:4px 0">'
                f'{r["model"]} &middot; {r["seconds"]:.1f}s</div>'
                f'<div style="height:{height}px;overflow:auto;border:1px solid #888;'
                f'border-radius:4px;padding:10px;font:13px/1.6 system-ui">{body}</div>'
                '</div>'
            )

        display(HTML(
            f'<div style="font:700 15px system-ui;margin:20px 0 8px">{fname} &middot; page {page_num}</div>'
            f'<div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap">'
            f'{"".join(cells)}</div>'
        ))


def timing(records):
    """Mean seconds per page per model -- feeds the phase 07 capacity math."""
    by_model = {}
    for r in records:
        if r["error"] is None:
            by_model.setdefault(r["model"], []).append(r["seconds"])
    print(f"{'model':<16}{'pages':>6}{'mean s':>9}{'min':>8}{'max':>8}")
    for label, times in by_model.items():
        print(f"{label:<16}{len(times):>6}{sum(times)/len(times):>9.1f}"
              f"{min(times):>8.1f}{max(times):>8.1f}")
