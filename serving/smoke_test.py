"""Exercise every documented status path against a running server.

Run the server first:
    AUTH_TOKEN=dev-test-token ../.venv/Scripts/python.exe -m uvicorn serving.app:app --port 8000

Then:  ../.venv/Scripts/python.exe serving/smoke_test.py

Each check asserts the status *and* the retryable flag, because the flag is what their queue
actually branches on -- a status code is a convention, the boolean is the contract.
"""
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(Path(__file__).resolve().parent)]
import config

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
# Resolved exactly the way the server resolves it -- AUTH_TOKEN, else serving/.token. A separate
# default here is what made this script report five auth failures against a perfectly healthy
# server: it was asserting its own hardcoded token, not the one the server was started with.
TOKEN = config.AUTH_TOKEN
PDF = "application/pdf"
PNG = "image/png"

passed = failed = 0


def check(name, got, want, extra=""):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<44} got {got!r}"
          + ("" if ok else f"  want {want!r}") + (f"   {extra}" if extra else ""))


def post(body, token=TOKEN, ctype=PDF, timeout=600):
    headers = {"Content-Type": ctype}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(f"{BASE}/v1/extract", content=body, headers=headers, timeout=timeout)


print(f"{BASE}")
print(f"token fingerprint {config.token_fingerprint()} — must match the server's startup line\n")
if not TOKEN:
    sys.exit("No token. Set AUTH_TOKEN, or run: .\\.venv\\Scripts\\python.exe serving\\new_token.py")

r = httpx.get(f"{BASE}/health", timeout=15)
check("health", r.status_code, 200, r.json().get("status", ""))

print("\nauth")
check("no token -> 401", post(b"x", token=None).status_code, 401)
check("wrong token -> 401", post(b"x", token="nope").status_code, 401)
check("401 is terminal", post(b"x", token=None).json()["error"]["retryable"], False)

print("\nbad input (all terminal -- their queue must not retry these)")
r = post(b"", ctype=PDF)
check("empty body -> 400", r.status_code, 400, r.json()["error"]["code"])
r = post(b"this is not a pdf at all", ctype=PDF)
check("garbage bytes -> 400", r.status_code, 400, r.json()["error"]["code"])
check("400 is terminal", r.json()["error"]["retryable"], False)
r = post(b"x", ctype="video/mp4")
check("bad content-type -> 400", r.status_code, 400, r.json()["error"]["code"])
r = post(b"0" * (41 * 1024 * 1024), ctype=PDF)
check("41 MB -> 413", r.status_code, 413, r.json()["error"]["code"])
check("413 is terminal", r.json()["error"]["retryable"], False)

print("\na real page (this runs the model -- allow a minute)")
img = (ROOT / "data/samples/test2.png").read_bytes()
r = post(img, ctype=PNG)
check("png -> 200", r.status_code, 200)
if r.status_code == 200:
    body = r.json()
    check("body has exactly one key", list(body), ["billCandidates"])
    check("found a bill", len(body["billCandidates"]) >= 1, True)
    for h in ("X-Request-Id", "X-Model-Name", "X-Model-Version", "X-Processing-Ms"):
        check(f"header {h}", h in r.headers, True, r.headers.get(h, ""))
    sys.path[:0] = [str(ROOT / "src")]
    import stage2_extract as s2
    ok, err = s2.validate(body)
    check("response validates against the contract", ok, True, "" if ok else str(err)[:60])
    (ROOT / "serving" / "d1-sample-response.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  wrote serving/d1-sample-response.json  ({r.headers.get('X-Processing-Ms')} ms)")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
