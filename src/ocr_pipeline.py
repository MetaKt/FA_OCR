"""
Universal OCR pipeline for Typhoon OCR running on local Ollama.

Accepts PDFs (single or multi-page) and plain images (.png/.jpg/.jpeg/.bmp/.webp),
renders every page to an image, and sends each one to the model with a caller-supplied
prompt. Nothing leaves the machine: all inference goes to Ollama on localhost.
"""
import base64
import io
import os
import re

import httpx
import pypdfium2 as pdfium
from PIL import Image
from typhoon_ocr.ocr_utils import get_prompt

DEFAULT_MODEL = "scb10x/typhoon-ocr1.5-3b"
DEFAULT_BASE_URL = "http://localhost:11434"


# A cell holding nothing: no text, or only whitespace / &nbsp; / <br>, or one of the placeholder
# marks a ruled form is filled with -- a dash, an en/em dash, a bullet or a lone full stop. Those
# are struck-through boxes, not values. A row is only dropped when EVERY cell looks like this, so
# a real "-" meaning zero in an otherwise filled row is still kept.
_BLANK_CELL = (r"<t[dh][^>]*>(?:\s|&nbsp;|&#160;|<br\s*/?>|[-‐-―•.])*</t[dh]>")
_BLANK_ROW = re.compile(rf"<tr[^>]*>\s*(?:{_BLANK_CELL}\s*)+</tr>", re.I)
_EMPTY_TABLE = re.compile(r"<table[^>]*>\s*(?:<t(?:body|head)>\s*</t(?:body|head)>\s*)*</table>", re.I)


def collapse_empty_rows(html):
    """Delete table rows in which every cell is blank.

    Pre-printed receipt forms are a grid of empty ruled boxes, and the v1.5 prompt asks for HTML
    tables, so Typhoon dutifully transcribes each blank line as a row. Measured on page 3 of
    บิลเงินสด_ร้านค้า_10ใบ.pdf: 281 rows, 1124 cells, 1117 of them empty -- 96% of a 13 KB
    transcript was markup for boxes nobody wrote in. It also costs real time, because once the
    model starts emitting <tr><td></td> the cheapest next token is another one (the mild form of
    the degeneration in plan/00-OVERVIEW.md F11).

    Safe by construction: a row with no content in any cell carries no information, so this
    cannot delete anything stage 2 could have used. Rows with even one non-empty cell are kept
    untouched, including the ragged ones where only the amount column was filled in.
    """
    if not html:
        return html
    return _EMPTY_TABLE.sub("", _BLANK_ROW.sub("", html))


def build_prompt(extra_rules=None, figure_language="Thai"):
    """Build a prompt the model will actually obey.

    typhoon-ocr1.5 is fine-tuned so tightly on its own v1.5 prompt that a replacement
    prompt makes it echo the training prompt back instead of reading the page. Tested:
    full official prompt works, official + appended rules works, anything else fails.
    So we always start from the official text and only ever append.
    """
    prompt = get_prompt("v1.5")(figure_language=figure_language)
    if extra_rules:
        prompt = prompt.rstrip() + "\n" + extra_rules.strip() + "\n"
    return prompt


def is_pdf(source):
    """Path or raw bytes. Bytes are sniffed, because an upload has no filename to trust.

    The API receives a request body, not a file on disk, and R18 forbids retaining documents --
    so there is no temp file to take an extension from even if we wanted one.
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:5]) == b"%PDF-"
    return os.path.splitext(source)[1].lower() == ".pdf"


# Longest-side target in px. NOT 1800, despite that being the size Typhoon's own docs
# recommend: at exactly 1800 the model reliably degenerates to a burst of "@" on some scanned
# pages (measured on page 3 of บิลเงินสด_ร้านค้า_5ใบ.pdf -- fails at 1800, works at 900, 1100,
# 1300, 1500 and 2000). 1500 transcribes all five pages of that file, twice, back to back.
DEFAULT_TARGET_DIM = 1500


def load_pages(path, target_dim=DEFAULT_TARGET_DIM):
    """Yield (page_number, PIL.Image) for every page, whatever the input format.

    `path` may also be raw bytes, which is what the serving API passes -- pypdfium2 and Pillow
    both read from memory, so a request never touches the filesystem (R18).

    Plain images are treated as a single page. PDFs are rasterised with pypdfium2,
    which ships its own binaries -- no Poppler or PATH setup needed.
    """
    raw = isinstance(path, (bytes, bytearray))
    if is_pdf(path):
        pdf = pdfium.PdfDocument(bytes(path) if raw else path)
        try:
            for i in range(len(pdf)):
                page = pdf[i]
                # scale is a multiplier on the PDF's native 72 dpi
                scale = target_dim / max(page.get_size())
                img = page.render(scale=scale).to_pil()
                yield i + 1, _fit(img, target_dim)
        finally:
            pdf.close()
    else:
        # Pillow reads the real file content, so the extension does not have to be accurate
        yield 1, _fit(Image.open(io.BytesIO(bytes(path)) if raw else path), target_dim)


def _fit(img, target_dim):
    """Scale the longest side down to target_dim. See DEFAULT_TARGET_DIM on why not 1800."""
    longest = max(img.size)
    if longest <= target_dim:
        return img
    scale = target_dim / longest
    new_size = (int(img.width * scale), int(img.height * scale))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def to_base64(img):
    """Encode as lossless PNG -- JPEG artefacts blur thin strokes and hurt OCR."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def ocr_page(img, prompt=None, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL,
             num_ctx=8192, repeat_penalty=1.25, seed=42, timeout=900):
    """Send one page image to the model and return its raw text response.

    Talks to Ollama's native /api/chat, not the OpenAI-compatible /v1 endpoint.

    The /v1 layer only understands OpenAI's parameters, and the three that matter most here are
    not among them -- `repeat_penalty`, `num_ctx` and `seed` all live in Ollama's `options`.
    Passing them via `extra_body` looked right and did nothing, silently. Without repeat_penalty
    the model loops on a receipt's letterhead and stops before it reaches the line items, so the
    totals never appear in the transcript at all (measured on test2.png: 731 chars with the
    header twice, versus 15576 chars once the penalty is actually applied).

    1.25 rather than 1.1, changed 2026-08-18. A pre-printed form is a grid of empty ruled boxes,
    and at 1.1 the model would fall into emitting `<tr><td>-</td>...` and never climb out --
    filling the whole context with dash rows and getting truncated before it reached the totals
    at the bottom of the page. On test2.png: at 1.1, 14251 chars in 80s with the ส่วนลด and the
    total missing entirely; at 1.25, 844 chars in 14s with both present. Raising num_ctx does not
    help, it only buys room for more dashes (31066 chars, still no total). Re-measured across all
    twelve golden pages: none lost content, two gained a third more.

    `seed` is fixed because the contract requires the same page to produce the same text on a
    retry (R5).
    """
    prompt = prompt or build_prompt()
    root = base_url.rstrip("/").removesuffix("/v1")  # tolerate a /v1 URL from older callers
    response = httpx.post(
        f"{root}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [to_base64(img)]}],
            "options": {"temperature": 0, "seed": seed, "top_p": 0.6,
                        "repeat_penalty": repeat_penalty, "num_ctx": num_ctx},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def unload(model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL):
    """Evict the model from VRAM so the next request starts from a clean runner.

    Diagnostic tool, not something to do in production. Ollama keeps a prompt cache and reuses a
    cached prefix across requests, so a long back-to-back run makes each result depend on what
    ran before it -- which is what made the F11 measurements contradict each other. Unloading
    between requests costs a few seconds of reload and buys independent measurements.
    """
    root = base_url.rstrip("/").removesuffix("/v1")
    httpx.post(f"{root}/api/generate",
               json={"model": model, "keep_alive": 0}, timeout=60).raise_for_status()


def ocr_file(path, prompt=None, model=DEFAULT_MODEL,
             base_url=DEFAULT_BASE_URL, target_dim=DEFAULT_TARGET_DIM, on_page=None):
    """OCR every page of a file. Returns [{page, text}] and never dies on one bad page.

    Pass on_page(page_num, text) to stream results instead of waiting for the whole file.
    """
    prompt = prompt or build_prompt()
    results = []
    for page_num, img in load_pages(path, target_dim):
        try:
            text = ocr_page(img, prompt, model, base_url)
            error = None
        except Exception as e:
            text, error = None, str(e)
        row = {"page": page_num, "text": text, "error": error}
        results.append(row)
        if on_page:
            on_page(page_num, text if text is not None else f"[FAILED: {error}]")
    return results
