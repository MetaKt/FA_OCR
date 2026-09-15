#!/usr/bin/env bash
#
# Bring a rented Linux GPU box from `git clone` to a server that will start.
#
#     ./serving/setup-vast.sh [/path/to/bill-extraction.schema.json]
#
# Written for Vast.ai, where the machine is a root container with a GPU, no systemd, and a disk
# that goes away when the instance does. Nothing here is Vast-specific beyond that; any Ubuntu
# box with an NVIDIA card will do.
#
# Safe to re-run. A stopped-and-restarted instance keeps its disk, so the second run finds the
# venv and the models already there and only restarts Ollama.
#
# What it deliberately does NOT do:
#
#   - fabricate their schema file. It is not in the repo and cannot be generated; see
#     src/stage2_extract.CONTRACT_SCHEMA for why a committed copy would be worse than none.
#   - overwrite serving/.token. Replacing it invalidates the token the colleague holds, so the
#     existing new_token.py already refuses without --force, and this defers to it.
#   - raise MAX_CONCURRENT. It prints what the VRAM suggests; changing it is a capacity decision
#     (plan/07-capacity.md), not a setup one.
#   - copy any document. R18 -- data/samples/ and golden.json are carried by hand or not at all.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
OLLAMA_URL="http://127.0.0.1:11434"
ENV_FILE="$ROOT/serving/env.sh"

# Peak VRAM for one request, measured 2026-09-02 on the laptop's RTX 5060 8 GB: 5351 MiB of 8151,
# with BOTH models resident. Used below only to suggest a concurrency.
MIB_PER_STREAM=5351
# Left unallocated for KV growth, fragmentation and whatever else is sharing the card.
MIB_HEADROOM=2000

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    WARNING  %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mFAILED  %s\033[0m\n' "$*" >&2; exit 1; }

BLOCKERS=()

# --- 1. the two things git does not carry -------------------------------------------------------
# Checked first so the operator learns about them now rather than after a 5 GB download, but they
# do not abort the run: everything below is worth having in place whenever the files turn up.
step "Checking what git could not bring"

# The conventional home for it on a box like this: outside git, beside the repo, stable across
# re-runs so the second run needs no argument. An explicit path still wins.
mkdir -p "$ROOT/contract"
if [[ $# -ge 1 ]]; then
    export CONTRACT_SCHEMA="$1"
elif [[ -z "${CONTRACT_SCHEMA:-}" && -f "$ROOT/contract/bill-extraction.schema.json" ]]; then
    export CONTRACT_SCHEMA="$ROOT/contract/bill-extraction.schema.json"
fi
if [[ -n "${CONTRACT_SCHEMA:-}" && -f "$CONTRACT_SCHEMA" ]]; then
    CONTRACT_SCHEMA="$(cd "$(dirname "$CONTRACT_SCHEMA")" && pwd)/$(basename "$CONTRACT_SCHEMA")"
    export CONTRACT_SCHEMA
    note "contract schema  $CONTRACT_SCHEMA"
else
    warn "their bill-extraction.schema.json is not here."
    BLOCKERS+=("schema")
fi

if [[ -f "$ROOT/serving/.token" ]]; then
    note "bearer token     serving/.token"
else
    warn "serving/.token does not exist. The server refuses to start without it."
    BLOCKERS+=("token")
fi

# --- 2. the box ---------------------------------------------------------------------------------
step "Checking the machine"

command -v nvidia-smi >/dev/null 2>&1 \
    || die "no nvidia-smi -- not a GPU box, and both stages are far too slow on CPU to be useful."

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | while IFS= read -r line; do
    note "GPU  $line"
done
VRAM_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')"

STREAMS=$(( (VRAM_MIB - MIB_HEADROOM) / MIB_PER_STREAM ))
[[ $STREAMS -lt 1 ]] && STREAMS=1

# apt only when something is actually missing -- a rented box may already have all of it, and an
# unconditional apt-get update is a minute nobody asked for.
MISSING=()
command -v curl >/dev/null 2>&1 || MISSING+=(curl ca-certificates)
python3 -c "import venv" >/dev/null 2>&1 || MISSING+=(python3-venv)
if [[ ${#MISSING[@]} -gt 0 ]]; then
    note "installing: ${MISSING[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
fi

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
note "python3  $PY_VER"
# 3.10 is a floor chosen from the dependency wheels, not measured against our own syntax -- the
# code in src/ and serving/ uses nothing newer than the walrus operator and would run on 3.8.
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "python3 $PY_VER is below 3.10, which is what current fastapi/pydantic wheels expect."

# --- 3. ollama ----------------------------------------------------------------------------------
step "Ollama"

if ! command -v ollama >/dev/null 2>&1; then
    note "installing"
    curl -fsSL https://ollama.com/install.sh | sh
else
    note "already installed  $(ollama --version 2>/dev/null | head -1)"
fi

# Both models stay resident, so a page does not pay to swap stage 1 out for stage 2 and back on
# every single page. KEEP_ALIVE=-1 because a chunk can sit idle between requests while the
# colleague looks at the previous one, and the default 5-minute eviction would make the next
# request pay the load twice.
export OLLAMA_MAX_LOADED_MODELS=2
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_NUM_PARALLEL="$STREAMS"

if curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    note "already serving on 11434"
    note "NOTE: it was started by something else, so the settings above are NOT in effect."
    note "      kill it and re-run this script if you want them."
else
    note "starting  (OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL, both models kept resident)"
    nohup ollama serve >"$ROOT/serving/ollama.log" 2>&1 &
    for _ in $(seq 1 60); do
        curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
        sleep 1
    done
    curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 \
        || die "Ollama did not answer on 11434 within 60s. See serving/ollama.log"
    note "up"
fi

# --- 4. the models ------------------------------------------------------------------------------
# Tags are read out of serving/config.py rather than written here a second time. config.py imports
# only os and pathlib at module level, so bare python3 can read it before the venv exists -- and a
# model tag that lived in two files would be changed in one of them.
step "Models"

STAGE1="$(python3 -c "import sys; sys.path.insert(0, 'serving'); import config; print(config.STAGE1_MODEL)")"
STAGE2="$(python3 -c "import sys; sys.path.insert(0, 'serving'); import config; print(config.STAGE2_MODEL)")"

for tag in "$STAGE1" "$STAGE2"; do
    note "pull  $tag"
    ollama pull "$tag"
done

# --- 5. python ----------------------------------------------------------------------------------
step "Python environment"

[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$ROOT/requirements.txt"
note "installed from requirements.txt"

# --- 6. prove it ---------------------------------------------------------------------------------
# 438 CPU-only checks, no GPU and no server. They are what says the tree is intact -- and
# test_doctypes.py in particular reads their schema file, so this run is also the first real proof
# that CONTRACT_SCHEMA points somewhere usable.
step "Tests"

if [[ ${#BLOCKERS[@]} -gt 0 ]] && [[ " ${BLOCKERS[*]} " == *" schema "* ]]; then
    warn "skipped -- test_doctypes.py needs their schema file."
else
    TOTAL=0
    for t in tolls payee doctypes slips personlink category arith degeneracy retry regions merge certlink; do
        line="$("$PY" "eval/test_$t.py" 2>&1 | tail -1)"
        printf '    %-14s %s\n' "$t" "$line"
        [[ "$line" == *" 0 failed"* ]] || die "eval/test_$t.py failed. Do not run the server until this is green."
        TOTAL=$(( TOTAL + $(echo "$line" | grep -oE '^[0-9]+') ))
    done
    note "$TOTAL checks passed"
    [[ "$TOTAL" == "438" ]] || warn "expected 438 checks, counted $TOTAL -- has the suite changed?"
fi

# --- 7. leave the machine's half of the config behind --------------------------------------------
# The server runs in a later shell, on a box whose paths are not the laptop's. This is the one
# file that knows them.
step "Writing serving/env.sh"

cat > "$ENV_FILE" <<EOF
# Generated by serving/setup-vast.sh -- this machine's half of the configuration.
# Gitignored: the paths here are true of this box and of nowhere else.
#
#     source serving/env.sh
#
# AUTH_TOKEN is deliberately absent. serving/config.py reads serving/.token when the variable is
# unset, and one file that both the server and smoke_test.py read is the whole point -- a token
# exported here as well is a second copy that goes stale silently.
export CONTRACT_SCHEMA="${CONTRACT_SCHEMA:-/set/this/to/bill-extraction.schema.json}"

# ${VRAM_MIB} MiB of VRAM / ${MIB_PER_STREAM} MiB measured per stream, less ${MIB_HEADROOM} MiB headroom.
#
# CONSERVATIVE, and in the safe direction: the 5351 MiB measurement is weights + one request's KV
# cache, but Ollama loads the weights once and only the KV is per-slot, so the real ceiling is
# higher than this. Measure it before trusting either number.
export OLLAMA_MAX_LOADED_MODELS=2
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_NUM_PARALLEL=${STREAMS}

# MAX_CONCURRENT stays 1 until somebody measures otherwise on this card. Raising it is
# plan/07-capacity.md's open question, not a setup step.
# export MAX_CONCURRENT=${STREAMS}
EOF
note "$ENV_FILE"

# --- 8. what is left ------------------------------------------------------------------------------
if [[ ${#BLOCKERS[@]} -gt 0 ]]; then
    printf '\n\033[31m==> NOT READY -- %d thing(s) must be copied by hand\033[0m\n' "${#BLOCKERS[@]}"
    for b in "${BLOCKERS[@]}"; do
        case "$b" in
        schema)
            printf '\n    their schema, sent from the laptop (Vast puts ssh on a mapped port):\n\n'
            printf '        scp -P <port> bill-extraction.schema.json root@<host>:%s/contract/\n' "$ROOT"
            printf '\n    then re-run this script -- it looks in contract/ by default, no argument needed.\n'
            ;;
        token)
            printf '\n    a bearer token, on this box:\n\n'
            printf '        %s serving/new_token.py\n' "$PY"
            printf '\n    it prints a fingerprint, never the token. To give the token to the colleague:\n'
            printf '        cat serving/.token\n'
            ;;
        esac
    done
    exit 1
fi

printf '\n\033[32m==> Ready\033[0m\n\n'
printf '    source serving/env.sh\n'
printf '    .venv/bin/python -m uvicorn serving.app:app --host 0.0.0.0 --port 8000\n\n'
printf '    0.0.0.0 is correct HERE, unlike on the laptop: the container has one interface and\n'
printf '    Vast maps the port. Check the portal for the external host:port, and remember the\n'
printf '    bearer token is the only thing standing in front of it.\n\n'
printf '    Then, from another shell:  .venv/bin/python serving/smoke_test.py\n\n'
