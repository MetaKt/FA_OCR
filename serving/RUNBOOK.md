# Runbook — running the endpoint for a live session with the webapp

For the first joint test, and for whoever is on call later. Assumes the laptop:
RTX 5060 Laptop 8 GB, Wi-Fi, LAN IP **192.168.61.45** as of 2026-08-31. **It has now moved three times, twice in one session** (192.168.252.47 -> .137 -> .64 -> 192.168.61.45, the last one a different subnet) and each move broke the colleague's configured URL, so check it with step 4 every session rather than trusting this line. A DHCP reservation would fix it; the owner's call on 2026-08-31 is not to bother -- this box is a prototype and hosting moves to the company server later. So the standing cost is: after every move, tell him the new address.

> **That IP is DHCP.** It changes when the laptop reconnects or the lease expires. Re-check it
> before every session — a session that "worked yesterday" and now times out is almost always this.
>
> ```powershell
> Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' }
> ```

---

## Which terminal

Two different shells appear below and they are **not** interchangeable.

| Marked | Where | Notes |
|---|---|---|
| `powershell` | VS Code's terminal, or Start → PowerShell | The default on this machine |
| `powershell (Administrator)` | Start → right-click PowerShell → **Run as administrator** | Firewall rules only. VS Code's terminal will not do unless VS Code itself was launched as admin |

Everything except the firewall rule runs fine in VS Code's terminal.

**The one trap:** `AUTH_TOKEN=xxx command` is bash syntax. In PowerShell it is a parse error —
`The term 'AUTH_TOKEN=xxx' is not recognized`. PowerShell sets an environment variable on its own
line with `$env:`. Both forms are given below; use the one matching your shell.

---

## Before the session — 10 minutes, alone

### 1. Make a token — once, ever

```powershell
.\.venv\Scripts\python.exe serving
ew_token.py
```

Writes `serving/.token` and prints only a **fingerprint**, never the token. Both the server and
`smoke_test.py` read that file, so there is nothing to copy between terminals and nothing to get
out of step.

It refuses to overwrite an existing token; pass `--force` to replace one, which invalidates the
copy your colleague holds.

`serving/.token` is gitignored. To hand the token over, print it deliberately:

```powershell
Get-Content serving\.token | Set-Clipboard
```

### 2. Check the models are there

```powershell
ollama list
```

Both must appear: `scb10x/typhoon-ocr1.5-3b` and `qwen3:4b`. Nothing else is needed.

### 3. Open the port to the local network only

**powershell (Administrator)** — once, ever. Start → right-click PowerShell → Run as
administrator. `Private` scope, not `Any`: this is a laptop on Wi-Fi and the rule should not
follow it onto a coffee-shop network.

```powershell
New-NetFirewallRule -DisplayName "ADV Clear API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private -Action Allow
```

`Access is denied` means the terminal is not elevated. To remove it later:

```powershell
Remove-NetFirewallRule -DisplayName "ADV Clear API"
```

### 4. Check today's IP

It is DHCP and it moves.

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' }
```

### 5. Start the server — terminal 1

```powershell
.\.venv\Scripts\python.exe -m uvicorn serving.app:app --host <today's IP> --port 8000
```

No `AUTH_TOKEN` needed; it reads `serving/.token`. Note the two startup lines:

```
ready backend=ollama stage1=... stage2=... max_concurrent=1 deadline=600s
auth token fingerprint=1980fe19
```

**`--host <IP>`, not `0.0.0.0`.** Binding to the one interface means reachable from the office LAN
and nowhere else — but it also means `127.0.0.1` is **not** listening, which matters in step 6.

Leave this terminal running. The server lives in it; Ctrl-C stops it.

### 6. Prove it — terminal 2

```powershell
$env:BASE_URL = "http://<today's IP>:8000"
.\.venv\Scripts\python.exe serving\smoke_test.py
```

`BASE_URL` is required because the smoke test defaults to `127.0.0.1`, which step 5 deliberately
did not bind. Without it:

```
httpx.ConnectError: [WinError 10061] ... the target machine actively refused it
```

which looks like a dead server and is not. Check what is really listening with:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress, LocalPort
```

Expect **18 passed, 0 failed**, about a minute. The first line prints the token fingerprint —
**it must match the server's startup line.** If it does not, the two terminals are using different
tokens and every authenticated check will fail while the bad-token check passes, which reads like
a broken endpoint and is not.

This run also warms Ollama, so your colleague's first request is not the slow cold-start one.

---

## What to send your colleague Get-Content serving\.token | Set-Clipboard

```
LOCAL_VISION_URL   = http://192.168.61.45:8000/v1/extract
LOCAL_VISION_TOKEN = <the token>
AI_PROVIDER        = local
```

And these four facts, because none of them are guessable from the URL:

1. **It is `http`, not `https`.** Their README writes `LOCAL_VISION_URL` as `https://`. If their
   client insists on TLS this will not connect, and that is a five-minute conversation now versus
   a confusing failure during the session.
2. **Send at most ~10 pages per request.** ~50-56 s/page against a 600 s internal deadline. More
   than that returns a retryable `504 DEADLINE_EXCEEDED` naming the page it reached, so the
   message says how much smaller the chunk needs to be. Fewer pages is better: one request holds
   the only inference slot for its whole duration.
3. **One request at a time.** `MAX_CONCURRENT=1`; a second concurrent request gets `429` with
   `retryable: true`, immediately rather than queued.
4. **`{"billCandidates": []}` is a normal `200`.** About a third of a real clearing set is not
   bills — ID-card copies, FM-FA-05 cover sheets. Their validator must accept an empty list.

---

## During the session — the six checks

From `../plan/08-handover-package.md` §4. Do these in order; each one has failed for somebody.

| # | Check | Why it matters |
|---|---|---|
| 1 | They set the three env vars and hit `/health` | Proves routing and firewall before any model runs |
| 2 | Push **one page** through their full pipeline | Smallest thing that can work. Do not start with a real chunk |
| 3 | **Their Zod validator accepts the response** | Zod `.strict()` and JSON Schema `additionalProperties:false` are close but not identical. This is the one that most often fails |
| 4 | Push a **not-a-bill page** — expect `[]` and no error | Confirms check 4 above is really true in their code |
| 5 | Force a `429` (two requests at once) and a `400` (send a .txt) | Their queue must retry the first and stop on the second |
| 6 | Confirm `X-Model-Name` / `X-Model-Version` reach their audit log | D2. If they cannot read response headers, the audit trail needs another home — their change, not ours |

Watch the server's own log while they do it. One line per request, no document content:

```
2026-08-24 16:14:59 INFO id=bb4826f6 200 bytes=998645 pages=1 bills=1 retries=0 ms=32801
```

---

## When something goes wrong

| Symptom | Almost always |
|---|---|
| Their client cannot connect at all | The IP changed (DHCP), or the firewall rule is missing, or you bound to `127.0.0.1` |
| `401` | Token mismatch — a trailing newline or space when it was copied |
| `WinError 10061` connecting to `127.0.0.1` | The server is bound to the LAN address, so loopback is not listening. Set `$env:BASE_URL` to the LAN address |
| `503` at `/health` | Ollama is not running, or a model was removed |
| `504` | Chunk too long. Split it, or raise `INFERENCE_DEADLINE_S` **only** if they also raise their worker lease |
| `429` on every request | They are sending in parallel. `MAX_CONCURRENT=1` on this hardware |
| `500 SCHEMA_VALIDATION_FAILED` | Rare. The model produced output that failed the contract twice. Capture the request id and tell us |
| First request of the day is very slow | Ollama is loading weights. Warm it with `serving/smoke_test.py` before the session |
| First request of the day also gives a *different answer* | Seen once, 2026-09-01: the cold request returned an extra duplicate candidate; five warm runs of the same file were then identical to the baht. Warming may matter for correctness, not only speed — warm before any run whose numbers you intend to quote |
| One page takes ~90s instead of ~50s | Stage 1 looped on it and it was re-read at another size. Working as intended — that page would otherwise have been silently blank |
| A page returns no bill and you expected one | Check the log for `loop(...)`. If it says that, stage 1 could not read the page at any size; if it does not, stage 1 read it and stage 2 found nothing |

---

## After the session

```powershell
Remove-NetFirewallRule -DisplayName "ADV Clear API"
```

Stop the server with Ctrl-C. There is nothing to clean up on disk — no document ever touches it.

**Rotate the token** if the session was recorded or the token went through a shared channel.

---

## Rules that outlive this session

- **Changing `STAGE1_MODEL` or `STAGE2_MODEL` invalidates the accuracy report.** 77.3% was
  measured on that exact pair. A model swap requires re-running phase 05 scoring *before* it goes
  live, not after.
- **`DEBUG_DUMP_INVALID` must stay `false`.** It writes model output, which is document content
  (R18). The directory is gitignored as a second line of defence, not as permission.
- **If they send a new `bill-extraction.schema.json`**: regenerate the grammar, re-run phase 02's
  validity check, re-run phase 05 scoring. Do not hand-edit the schema.
