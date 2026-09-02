"""POST /v1/extract -- one request in, one contract response out.

Deliberately not a job API. Their system swaps us in where a Gemini call was, by env var, and the
smaller our interface the smaller their adapter stays. See ../plan/06-production-api.md section 1.

    ../.venv/Scripts/python.exe -m uvicorn serving.app:app --host 127.0.0.1 --port 8000
"""
import asyncio
import logging
import re
import secrets
import sys
import time
import uuid
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

import config
import pipeline
from pipeline import Deadline, Retryable, Terminal

# Importing pipeline first is what puts src/ on the path; these have to follow it.
import stage2_extract as s2
import tolls

# Format carries no document content by design (R18): request id, size, pages, timing, status.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("adv-clear")

app = FastAPI(title="ADV Clear — bill extraction", version="1.0.0",
              docs_url="/docs", openapi_url="/openapi.json")

# Bounds in-flight inference. Anything beyond it is refused immediately rather than queued:
# a request that waits 4 minutes for a slot then runs into the deadline has consumed a worker
# lease to produce a 504. On 8 GB the honest value is 1.
_slots = asyncio.Semaphore(config.MAX_CONCURRENT)

ACCEPTED = ("application/pdf", "image/png", "image/jpeg", "image/jpg",
            "application/octet-stream")

_model_versions = {}


def _fail(status, code, message, retryable, request_id):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
        headers={"X-Request-Id": request_id})


def _authorised(request):
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    # Constant-time: a plain == leaks the token's prefix through timing, one character at a time.
    return secrets.compare_digest(token, config.AUTH_TOKEN)


@app.on_event("startup")
async def _startup():
    if not config.AUTH_TOKEN:
        raise RuntimeError(
            "AUTH_TOKEN is not set. Refusing to start -- an unauthenticated endpoint that "
            "accepts documents containing national ID and bank numbers is not a default.")
    for tag in (config.STAGE1_MODEL, config.STAGE2_MODEL):
        _model_versions[tag] = config.model_version(tag)
    log.info("ready backend=%s stage1=%s stage2=%s max_concurrent=%d deadline=%ds",
             config.MODEL_BACKEND, config.STAGE1_MODEL, config.STAGE2_MODEL,
             config.MAX_CONCURRENT, config.INFERENCE_DEADLINE_S)
    # A fingerprint, never the token. Two terminals disagreeing about AUTH_TOKEN presents as
    # "every authenticated request 401s while the bad-token check passes", which is hard to tell
    # from a broken endpoint. Comparing eight hex characters settles it in seconds, and a
    # truncated SHA-256 gives nothing away.
    log.info("auth token fingerprint=%s from %s",
             config.token_fingerprint(), config.AUTH_TOKEN_SOURCE)


@app.get("/health")
async def health():
    """Is the service up *and* can it actually reach its models?

    A health check that only proves the web server is running is worse than none -- it reports
    green while every extraction fails with 503.
    """
    try:
        r = httpx.get(f"{config.MODEL_BASE_URL.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        have = {m["name"] for m in r.json().get("models", [])}
    except Exception as exc:
        return JSONResponse(status_code=503, content={
            "status": "unhealthy", "reason": f"model backend unreachable: {type(exc).__name__}"})

    missing = [t for t in (config.STAGE1_MODEL, config.STAGE2_MODEL)
               if t not in have and f"{t}:latest" not in have]
    if missing:
        return JSONResponse(status_code=503, content={
            "status": "unhealthy", "reason": f"model(s) not pulled: {', '.join(missing)}"})
    return {"status": "ok", "stage1": config.STAGE1_MODEL, "stage2": config.STAGE2_MODEL,
            "maxConcurrent": config.MAX_CONCURRENT,
            "inferenceDeadlineSeconds": config.INFERENCE_DEADLINE_S}


# The account code FA picked before uploading, sent by the webapp since 2026-09-01. Capped
# because it is used in a log line and echoed back in a header, and an unbounded header value
# should not decide the size of either. 64 matches the cap the webapp already applies.
#
# It is NOT sanitised beyond that, and does not need to be: `stage2_extract.category_prompt`
# uses the value only as a dictionary key to look up OUR OWN rule text, and appends that text.
# The value itself never reaches the model. A category we do not recognise -- junk, an injection
# attempt, or a code FA invents next year -- finds no entry and falls back to the shared prompt,
# which is exactly the behaviour every request had before this header existed.
MAX_CATEGORY_CHARS = 64

# What may be echoed back in a response header. HTTP headers are latin-1 on the wire, and the
# webapp is allowed to send the code with its Thai label attached -- echoing that verbatim would
# raise inside the response encoder and turn a good extraction into a 500.
_HEADER_SAFE = re.compile(r"[^0-9A-Za-z._-]")


def _category(request):
    """The x-category-id header, or None. Absent and empty are the same thing."""
    value = (request.headers.get("x-category-id") or "").strip()[:MAX_CATEGORY_CHARS]
    return value or None


def _category_headers(category):
    """Tell the caller what the category actually did, so a silent mismatch is visible.

    Three outcomes look identical from the webapp side otherwise: the header never arrived, it
    arrived and drove something, and it arrived and nothing is keyed on that code. The first is
    their bug and the last is ours (or a code FA added that we have no handling for), so they
    need opposite fixes.

    The value names what fired, because a category can drive two unrelated things and reporting
    only one of them misleads. `5223100` has no stage-2 prompt rule but does gate toll summing,
    and the first version of this header called that `no-rule-for-this-code` -- true of the
    prompt, and wrong about the request, which had just merged five tickets into one row.

        none            no header arrived
        no-effect       the code arrived and nothing is keyed on it
        prompt          extra stage-2 rules from data/category_rules.json
        tolls           expressway tickets summed into one row
        prompt,tolls    both
    """
    if category is None:
        return {"X-Category-Id": "-", "X-Category-Rule": "none"}
    key = s2.category_key(category)
    fired = []
    if any(s2.category_key(k) == key for k in s2.load_category_rules()):
        fired.append("prompt")
    if tolls.applies(category):
        fired.append("tolls")
    return {"X-Category-Id": _HEADER_SAFE.sub("", key)[:MAX_CATEGORY_CHARS] or "-",
            "X-Category-Rule": ",".join(fired) if fired else "no-effect"}


@app.post("/v1/extract")
async def extract(request: Request):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    started = time.perf_counter()
    category = _category(request)

    if not _authorised(request):
        log.warning("id=%s 401 unauthorised", request_id)
        return _fail(401, "UNAUTHORISED", "missing or invalid bearer token", False, request_id)

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type not in ACCEPTED:
        return _fail(400, "UNSUPPORTED_MEDIA_TYPE",
                     f"{content_type!r} is not one of {', '.join(ACCEPTED)}", False, request_id)

    body = await request.body()
    if not body:
        return _fail(400, "EMPTY_BODY", "request body is empty", False, request_id)
    if len(body) > config.MAX_BODY_BYTES:
        return _fail(413, "PAYLOAD_TOO_LARGE",
                     f"{len(body)} bytes exceeds the {config.MAX_BODY_MB} MB limit",
                     False, request_id)

    if _slots.locked():          # locked() is True exactly when no permits remain
        log.info("id=%s 429 all %d slot(s) busy", request_id, config.MAX_CONCURRENT)
        return _fail(429, "MODEL_BUSY",
                     f"all {config.MAX_CONCURRENT} inference slot(s) in use", True, request_id)

    deadline = Deadline(config.INFERENCE_DEADLINE_S)
    async with _slots:
        try:
            # The pipeline is blocking (sync httpx into Ollama). Off the event loop, or /health
            # stops answering for the whole of a four-minute extraction.
            response, n_pages, retries = await asyncio.to_thread(
                pipeline.run_validated, body, deadline, None, category)
        except Terminal as exc:
            log.info("id=%s 400 %s bytes=%d", request_id, exc.code, len(body))
            return _fail(400, exc.code, exc.message, False, request_id)
        except Retryable as exc:
            status = 504 if exc.code == "DEADLINE_EXCEEDED" else 500
            log.warning("id=%s %d %s bytes=%d elapsed=%.1fs",
                        request_id, status, exc.code, len(body), deadline.elapsed)
            return _fail(status, exc.code, exc.message, True, request_id)
        except httpx.HTTPError as exc:
            log.warning("id=%s 503 backend %s", request_id, type(exc).__name__)
            return _fail(503, "MODEL_UNAVAILABLE",
                         f"model backend error: {type(exc).__name__}", True, request_id)
        except Exception as exc:
            log.exception("id=%s 500 unexpected %s", request_id, type(exc).__name__)
            return _fail(500, "INTERNAL_ERROR", type(exc).__name__, True, request_id)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log.info("id=%s 200 bytes=%d pages=%d bills=%d retries=%d ms=%d category=%s",
             request_id, len(body), n_pages, len(response["billCandidates"]), retries, elapsed_ms,
             category or "-")

    # Metadata goes in headers, never in the body: their validator is strict at the root, so an
    # extra key there would fail the whole chunk. D2 wants the model recorded per extraction.
    return JSONResponse(content=response, headers={
        "X-Request-Id": request_id,
        "X-Model-Name": f"{config.STAGE1_MODEL}+{config.STAGE2_MODEL}",
        "X-Model-Version": f"{_model_versions.get(config.STAGE1_MODEL, 'unknown')}+"
                           f"{_model_versions.get(config.STAGE2_MODEL, 'unknown')}",
        "X-Processing-Ms": str(elapsed_ms),
        "X-Page-Count": str(n_pages),
        **_category_headers(category),
    })
