"""Every knob the service has, in one place, read from the environment.

Nothing else in `serving/` reads os.environ. A setting that can be changed in two places gets
changed in one of them.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- models -----------------------------------------------------------------------------------
# Defaults match what the 77.3% accuracy figure was measured with. Changing either tag invalidates
# that number, and the runbook rule is that a model swap requires re-running phase 05 scoring
# before it goes live.
STAGE1_MODEL = os.environ.get("STAGE1_MODEL", "scb10x/typhoon-ocr1.5-3b")
STAGE2_MODEL = os.environ.get("STAGE2_MODEL", "qwen3:4b")
MODEL_BACKEND = os.environ.get("MODEL_BACKEND", "ollama")
MODEL_BASE_URL = os.environ.get("MODEL_BASE_URL", "http://localhost:11434")

# --- their contract ---------------------------------------------------------------------------
# `$env:CONTRACT_SCHEMA` -- the path to their bill-extraction.schema.json -- is the one knob that
# is deliberately NOT here. It is read by `src/stage2_extract.CONTRACT_SCHEMA`, because
# `eval/test_doctypes.py` imports that module with only `src/` on the path and cannot see this
# file. Repeating the default here would put the same path in two places, and the stale one would
# win silently. `serving/app.py` refuses to start if the file it names is missing.

# --- auth -------------------------------------------------------------------------------------
# No default on purpose. A service that authenticates with a built-in token is a service with no
# authentication; failing to start is the correct behaviour.
#
# AUTH_TOKEN wins; otherwise serving/.token is read if it exists. The file exists because the
# server and the client run in two different terminals, and setting the variable separately in
# each is how they end up disagreeing -- which presents as "every authenticated request 401s",
# indistinguishable from a broken endpoint. One file, both readers, nothing copied.
#
# .token is gitignored. It is a secret at rest on a machine that already holds the documents.
TOKEN_FILE = ROOT / "serving" / ".token"


def _token():
    """Returns (token, where it came from). The source is reported because the precedence is
    silent otherwise: a stale AUTH_TOKEN left in a shell from an earlier session overrides the
    file without a word, and the only symptom is that every authenticated request 401s.
    """
    value = os.environ.get("AUTH_TOKEN", "").strip()
    if value:
        return value, "$env:AUTH_TOKEN"
    if TOKEN_FILE.exists():
        # .strip() because an editor that adds a trailing newline would otherwise change the
        # token silently, and a whitespace mismatch is invisible in every error message.
        return TOKEN_FILE.read_text(encoding="utf-8").strip(), "serving/.token"
    return "", "nowhere"


AUTH_TOKEN, AUTH_TOKEN_SOURCE = _token()

# --- limits -----------------------------------------------------------------------------------
MAX_CONCURRENT = _int("MAX_CONCURRENT", 1)
MAX_BODY_MB = _int("MAX_BODY_MB", 40)
MAX_BODY_BYTES = MAX_BODY_MB * 1024 * 1024

# Below the caller's abort, leaving room for network, rasterisation and their overhead. A slow
# success is worse than a fast failure here: they stop listening, the job is redelivered, and we
# have burned the GPU twice for one answer.
#
# 600 since 2026-08-28, and this is the first value set against numbers they actually confirmed:
#
#   our deadline        600 s
#   their client abort  660 s   LOCAL_VISION_TIMEOUT_MS
#   their queue lease   900 s
#
# The number to sit under is the **client abort**, not the lease. Giving up 60s before they stop
# listening means they receive a 504 that names the page we reached; giving up after means they
# see a dead socket that says nothing. Their lease is the outer bound and has room to spare.
#
# History: 240 against a guessed 5-minute lease, rejecting real chunks at ~50s/page; then 540 as a
# deliberately conservative guess because the real numbers had not been given. Asking for them
# also found a fault on their side -- the lease was 300s, shorter than a chunk takes, so a second
# worker tick could seize a running job and send the same pages through again. Fixed to 900s.
#
# Note the cost: MAX_CONCURRENT is 1, so one request holds the only slot for up to ten minutes and
# everything else gets 429 for that whole time. A 7-page chunk is 350-500s and fits; chunks of
# 10-15 pages fail cheaper and retry cheaper than 40.
#
# This used to say 7 pages was "their ceiling". Nothing they have sent says that -- it was the
# size of the files they happened to send for testing, written down as if it were a
# requirement. Their README still asks for 40 MB / 40 pages. See plan/07-capacity.md section 1.
INFERENCE_DEADLINE_S = _int("INFERENCE_DEADLINE_S", 600)

# 1500, not the 1800 Typhoon's own docs recommend. At exactly 1800 the model degenerates into a
# burst of "@" on some scanned pages. See ocr_pipeline.DEFAULT_TARGET_DIM for the measurement.
TARGET_DIM = _int("TARGET_DIM", 1500)

# Sizes to re-render a page at when stage 1 comes back looped (src/degeneracy.py). Tried in
# order, first clean read wins, and the page is given up on if none of them works.
#
# It has to be the image that changes, not the sampler. Stage 1 runs at temperature 0, so the
# decode is greedy: the same image at the same size produces the same loop however many times it
# is asked, and a different `seed` changes nothing at all. Resolution is the lever, and the
# order below is measured, not guessed. Page 198 of P06690 carries three toll tickets worth 120
# baht: two ordinary printed ones, and a punch-card ticket with a calendar round its border --
# JAN..DEC down the sides, 1..31 across -- which is what the decoder latches onto. Read at five
# sizes, scored on the tickets' own running numbers rather than on whether the digits appear
# somewhere (a bare "45" turns up inside plenty of other numbers, and scoring it that way is
# what first made 1300 look like a complete fix):
#
#   dim    time    chars   unique  compressed   25.00  50.00  punch card
#   1500   71.9s    9893     0.04     0.03        no     no      no        <- live setting, loops
#   1300   16.9s    2471     0.85     0.27       YES    YES      no        <- best, and fastest
#   1100   71.1s   14447     0.01     0.02        no     no      no        <- worse than 1500
#   2000   53.4s    7883     0.41     0.09       YES    YES      no        <- usable, 3x slower
#   900    71.7s   13904     0.05     0.03        no     no      no        <- loops
#
# Two sizes out of five work, and they are not the neighbours of 1500 -- 1300 reads the page
# well while 1100 produces the worst output of the lot. So there is no "try bigger" or "try
# smaller" rule to lean on, and a size belongs in this list only once it has been measured on a
# page that actually breaks. 1100 and 900 are deliberately absent: they are not merely
# unhelpful, each costs a full 71 seconds to fail.
#
# Ordered by speed among the ones that work. 1300 is also the cheapest read of all five, because
# a looping decode spends its whole context emitting the same line -- the broken page is
# expensive precisely because it is broken.
#
# What this does NOT fix: the punch-card ticket is unread at every size tried, so page 198 comes
# back worth 75 of its 120 baht. Escaping the loop and reading the page are two different things,
# and this knob only does the first. The remainder is a stage-1 capability gap on that one ticket
# design, not something another resolution has been shown to solve.
#
# Empty string disables the retry and restores the old behaviour: a looped page is reported as
# bill-free, just with a reason in the log this time.
RETRY_TARGET_DIMS = tuple(
    int(x) for x in os.environ.get("RETRY_TARGET_DIMS", "1300,2000").split(",") if x.strip())

# --- debug ------------------------------------------------------------------------------------
# Writes model output that failed schema validation to disk. That is document content, so R18
# says it must stay off in production. Guarded, gitignored, and named so nobody turns it on by
# accident.
DEBUG_DUMP_INVALID = _bool("DEBUG_DUMP_INVALID", False)
DEBUG_DIR = ROOT / "serving" / "_invalid"


def model_version(tag, base_url=None):
    """The backend's own digest for a tag, for the X-Model-Version audit header.

    A tag like `qwen3:4b` is mutable -- the publisher can repoint it, and then an audit log
    naming only the tag records nothing useful. The digest is what actually ran.

    Read from /api/tags, not /api/show: show returns the template, licence and parameters but no
    digest at all. Tags reports `qwen3:4b` as-is and `scb10x/typhoon-ocr1.5-3b` as
    `...:latest`, so both spellings are tried.

    Falls back to "unknown" rather than raising -- a missing audit header is a smaller problem
    than a dropped chunk.
    """
    import httpx
    try:
        r = httpx.get(f"{(base_url or MODEL_BASE_URL).rstrip('/')}/api/tags", timeout=10)
        r.raise_for_status()
        by_name = {m["name"]: m.get("digest", "") for m in r.json().get("models", [])}
        digest = by_name.get(tag) or by_name.get(f"{tag}:latest") or ""
        return digest[:19] if digest else "unknown"
    except Exception:
        return "unknown"


def token_fingerprint(token=None):
    """First 8 hex of SHA-256 over the token. Safe to log; useless to an attacker.

    Exists because a token mismatch between the server terminal and a client presents as
    "everything 401s", which is indistinguishable from a broken endpoint until you can compare
    the two values -- and the two values are the one thing that must never be printed.
    """
    import hashlib
    value = config_token if (config_token := (token or AUTH_TOKEN)) else ""
    if not value:
        return "unset"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
