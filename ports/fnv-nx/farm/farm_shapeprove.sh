#!/bin/bash
# Farm worker of the shape prover: prove shard K of N of the integer encoding shapes and publish the result to the private artifacts repository.
#   bash ports/fnv-nx/farm/farm_shapeprove.sh [--queue FILE] K N          (K = 1..N; the shapes at sorted position = K-1 mod N)
# --queue FILE: the PINNED queue of a pack (fnv-nx-build branch l3b-lift/queue/<pack12>-<prover12>, file queue.tsv, proof/packqueue.py): the rows `shape_key<TAB>shape_sha`
#   are the work, split by position mod N; nothing is fetched or scanned (steps 2 and 2b are skipped), so every farm of one run has the same queue.  The header's ir_sha256 must be
#   the IR of this machine and the rows must hash to the header's queue_id, else exit 1.  Output branch id = <IR id>-<queue_id>.  Every record carries model_fns / closure_sha (K).
# Inputs (private / exe-derived, fetched from the artifacts repository fnv-nx-build, never stored in this repository):
#   branch l3b-lift/shapes        KEYS.tsv + <module>/shapes.tsv (output of proof/census.py); every shapes.tsv is checked against KEYS.tsv
#   branch tools/sail-mirror      the pinned downloads of the Sail/isla toolchain; used by farm/sail_setup.sh (MIRROR=...) only when the toolchain
#                                 (isla binary + proof IR) is not installed on the machine
# Work = the QUEUE: the integer shapes that have no valid certificate in ANY published shard (certs_build.py --queue).  A certificate is valid iff
#   (shape key, symform.shape_sha(key) of the CURRENT DSL, IR sha256) all match its record (certs_build.py header): a DSL edit re-queues exactly the shapes whose
#   Form changed; nothing else is proved again, whatever shard, run or machine produced the earlier certificate.  Shard K/N is taken from the queue.
# Output: WRITE-ONCE branch  l3b-lift/shapeprove/<ID>/<K>of<N>-<sha12 of the jsonl>  = shapeprove_<K>_of_<N>.jsonl (only that file; a rerun writes a new branch); a branch counts as published only when shardok.py passes (records == queue rows, no error record), ID = <IR id 12 hex>-<queue id 12 hex>
#   (certs_build.py --id: the proof IR sha256; queue id = sha256 over the queue's (key, shape_sha) lines, so the same queue and K/N is the same branch).
#   Already published: nothing is re-run.
# Resumable and incremental: the shard's results branch is updated every PUBLISH_EVERY seconds (default 600) and on SIGTERM/SIGINT; a rerun of the same command resumes from the published file (shapes with a record are skipped), so a time limit, a crash or a kill loses at most one increment.
# Env: GATE_CACHE (required: the verdict cache; the Sail toolchain and caches are under it)  FNVNX_BUILD (clone of fnv-nx-build)  FNVNX_WORK (default $GATE_CACHE/farm-shapeprove)
#      SHAPES_BRANCH (default l3b-lift/shapes; the census branch, KEYS.tsv-pinned; a re-pinned census goes to ANOTHER branch, the running farms read the head of this one)  BRANCH_PREFIX (default l3b-lift/shapeprove; tests use l3b-lift/shapeprove-test)  PUBLISH (1 default; 0: prove only)
#      TIMEOUT (s per shape, default 600)  EIP (sym|fixed, default sym)  SLICE (default 1)  LIMIT (first LIMIT shapes of the shard, default all)
#      VMEM_KB (address-space limit per process, default 6000000)  SHAPES (a shapes.tsv: skips the fetch of l3b-lift/shapes; local tests)
# Last line: SHAPEPROVE shard=K/N id=ID shapes=<n> proved=<n> ... seconds=<wall> ; exit 1 if publication failed.
set -euo pipefail
PINQ=
if [ "${1:-}" = "--queue" ]; then PINQ=${2:?usage: farm_shapeprove.sh [--queue FILE] K N}; shift 2; [ -f "$PINQ" ] || { echo "queue file $PINQ not found" >&2; exit 1; }; fi
K=${1:?usage: farm_shapeprove.sh [--queue FILE] K N}; N=${2:?usage: farm_shapeprove.sh [--queue FILE] K N}
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd)
B=${FNVNX_BUILD:?FNVNX_BUILD (clone of fnv-nx-build)}; W=${FNVNX_WORK:-${GATE_CACHE:?GATE_CACHE not set}/farm-shapeprove}
PREFIX=${BRANCH_PREFIX:-l3b-lift/shapeprove}; PUBLISH=${PUBLISH:-1}
SAILBASE="${GATE_CACHE:?GATE_CACHE (the verdict cache) is not set}/sail/$(sha256sum "$HERE/sail_pins.txt" | cut -c1-12)"      # the toolchain and its caches live in the verdict cache, keyed by the pins (proof/sail_pins.txt)
WORKDIR=${WORK:-$SAILBASE/work}
mkdir -p "$W/in"
{ flock 9      # shards started together on one machine stage the inputs one after the other; fd 9 exists only inside this group, so no child of the proving phase holds the lock
T0=$(date +%s)
fetch_branch() {   # fetch_branch <branch> <dir>: a worktree of the branch head in <dir> (reused when present)
    local d=$2
    if [ ! -d "$d" ]; then git -C "$B" fetch -q --depth 1 origin "$1"; git -C "$B" worktree add -f "$d" FETCH_HEAD >/dev/null; fi
}

# 0. python dependencies of the prover: capstone (shape.py, the decoder) and keystone-engine (enc_table.py, imported by prove.py), ONLY from the mirror branch
#    tools/pywheels-prover of the artifacts repository, installed with --require-hashes against the sha256 of proof/sail_pins.txt (the same pins as the toolchain; no PyPI);
#    z3-solver is installed by sail_setup.sh from the tools/sail-mirror wheel
if ! python3 -c 'import capstone, keystone' 2>/dev/null; then
    fetch_branch tools/pywheels-prover "$W/in/pywheels"
    REQ="$W/in/prover-requirements.txt"
    : > "$REQ"
    for n in capstone keystone-engine; do
        v=$(awk -v n="$n" '$1==n{print $3}' "$HERE/sail_pins.txt"); h=$(awk -v n="$n" '$1==n{print $4}' "$HERE/sail_pins.txt")
        [ -n "$v" ] && [ -n "$h" ] || { echo "no pin for $n in sail_pins.txt" >&2; exit 1; }
        echo "$n==$v --hash=sha256:$h" >> "$REQ"
    done
    python3 -m pip install -q --break-system-packages --no-index --no-deps --require-hashes --find-links "$W/in/pywheels" -r "$REQ"
fi

# 1. toolchain: the installed isla binary, else built from the mirror branch (sail_setup.sh).  The proof IR is NOT built here: it is fetched by its sha (the pin of
#    formgate_inputs.json, the external x86ir) from the artifacts repository by the loader; farm/sail_build.sh produced and published it once (PUBLISH_IR=1)
ISLA=${ISLA_EXEC:-$SAILBASE/rems-project/isla/target/release/isla-execute-function}
export ISLA_EXEC=$ISLA SAIL_WORK=$WORKDIR      # tests (gate_tests strips GATE_*) and model_sail.py read the toolchain through these
if [ ! -x "$ISLA" ]; then
    fetch_branch tools/sail-mirror "$W/in/sail-mirror"
    MIRROR="$W/in/sail-mirror" bash "$FARM/sail_setup.sh" > "$W/sail_setup_$K.log" 2>&1 || { echo "sail_setup.sh failed (see $W/sail_setup_$K.log)" >&2; exit 1; }
fi
# the IR is pinned in ONE place: the external x86ir of formgate_inputs.json (commit + tree_sha256); the trunk's loader (model_sail.ir_file) fetches it into GATE_CACHE.  No pin literal here.
SHA=$(python3 -I "$HERE/certs_build.py" --model-sha) || { echo "the IR (SAIL_IR, else the external x86ir of formgate_inputs.json) does not load" >&2; exit 1; }
PINSHA=$(env -u SAIL_IR python3 -I "$HERE/certs_build.py" --model-sha) || { echo "the pinned IR (external x86ir of formgate_inputs.json) does not load" >&2; exit 1; }
[ "$SHA" = "$PINSHA" ] || { echo "IR sha256 $SHA is not the pinned IR $PINSHA (external x86ir of formgate_inputs.json)" >&2; exit 1; }

if [ -n "$PINQ" ]; then
    # 2. the pinned queue: its header names the IR and the queue id; the rows must hash to it
    QUEUE=$PINQ
    QID=$(python3 -I - "$QUEUE" "$SHA" <<'PY' || exit 1
import hashlib, re, sys
q, sha = sys.argv[1], sys.argv[2]
hdr = [l for l in open(q) if l.startswith('#')]; rows = [l for l in open(q) if l.strip() and l[0] != '#']
m = re.search(r'queue_id=([0-9a-f]{12})', hdr[0]); ir = [l.split('=', 1)[1].strip() for l in hdr if l.startswith('# ir_sha256=')]
if not m or ir != [sha]: sys.exit('queue header: queue_id missing or ir_sha256 differs from the IR of this machine (%s)' % sha)
if hashlib.sha256(''.join(rows).encode()).hexdigest()[:12] != m.group(1): sys.exit('queue rows do not hash to the queue_id of the header')
print(m.group(1))
PY
)
    echo "queue $QID pinned: $PINQ ($(grep -vc '^#' "$PINQ") shapes)"
else
# 2. shapes: l3b-lift/shapes (each module's shapes.tsv is checked against the sha256 in KEYS.tsv); the union of the modules' shape keys is the universe
if [ -z "${SHAPES:-}" ]; then
    fetch_branch "${SHAPES_BRANCH:-l3b-lift/shapes}" "$W/in/shapes"
    python3 -I - "$W/in/shapes" <<'PY' || { echo "a shapes.tsv of the shapes branch does not match KEYS.tsv" >&2; exit 1; }
import hashlib, os, sys
d = sys.argv[1]
sha = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()
for ln in open(os.path.join(d, 'KEYS.tsv')):
    if ln.strip() and ln[0] != '#':
        r = ln.rstrip('\n').split('\t'); assert sha(os.path.join(d, r[0], 'shapes.tsv')) == r[4], r[0]
PY
    SHAPES="$W/in/shapes_union.tsv"
    ( for m in $(cut -f1 "$W/in/shapes/KEYS.tsv" | grep -v '^#' | sort); do grep -hv '^#' "$W/in/shapes/$m/shapes.tsv" | cut -f1; done | LC_ALL=C sort -u ) > "$SHAPES.tmp"
    mv "$SHAPES.tmp" "$SHAPES"
fi
# 2b. the queue: published shards of every earlier run (all branches under $PREFIX/) are read; their valid certificates are skipped
PUBD="$W/published"; rm -rf "$PUBD"; mkdir -p "$PUBD"
if [ "$PUBLISH" = 1 ] || [ -n "${FETCH_PUBLISHED:-}" ]; then
    REFS=$(git -C "$B" ls-remote origin "refs/heads/$PREFIX/*" | awk '{print $2}')
    if [ -n "$REFS" ]; then
        SPECS=(); i=0; for r in $REFS; do i=$((i+1)); SPECS+=("+$r:refs/farm-pub/$i"); done
        git -C "$B" fetch -q --depth 1 origin "${SPECS[@]}"
        for j in $(seq 1 $i); do mkdir -p "$PUBD/$j"; git -C "$B" archive "refs/farm-pub/$j" | tar -x -C "$PUBD/$j"; git -C "$B" update-ref -d "refs/farm-pub/$j"; done
    fi
fi
QUEUE="$W/queue.tsv"
QLINE=$(python3 -I "$HERE/certs_build.py" --queue "$SHAPES" --shards "$PUBD" --out "$QUEUE")
QID=$(echo "$QLINE" | awk '{print $2}'); echo "queue $QLINE (from $(find "$PUBD" -name 'shapeprove_*.jsonl' | wc -l) published shard files)"
[ "$(awk '{print $3}' <<< "$QLINE")" != 0 ] || { echo "SHAPEPROVE shard=$K/$N: queue empty, every encoding with a Form has a valid certificate"; exit 0; }
fi
} 9> "$W/in/.lock"
ID="$(python3 -I "$HERE/certs_build.py" --id)-$QID"
OUT="$W/out/$ID"; mkdir -p "$OUT"
J="$OUT/shapeprove_${K}_of_$N.jsonl"
# a results branch is WRITE-ONCE (the git proxy refuses deletes and forced updates): its name carries the content, <prefix>/<ID>/<K>of<N>-<sha12 of the jsonl>.  A rerun writes a new branch.  A branch counts as
# published only when its record count equals the shard's queue rows and it has no error record (shardok); any other branch (empty, short, with errors) is ignored by rule, by every reader.
ROWS=$(python3 -I "$HERE/shardok.py" rows "$QUEUE" "$K" "$N" "${SLICE:-1}")
[ -n "${LIMIT:-}" ] && [ "$LIMIT" -lt "$ROWS" ] && ROWS=$LIMIT
shardok() { python3 -I "$HERE/shardok.py" "$1" "$ROWS"; }     # exit 0 iff the jsonl has exactly ROWS records and no error record
publish() {   # an orphan commit holding the jsonl only, on a NEW branch named by its content
    [ "$PUBLISH" = 1 ] && [ -s "$J" ] || return 0
    local h; h=$(sha256sum "$J" | cut -c1-12); BR="$PREFIX/$ID/${K}of${N}-$h"
    git -C "$B" ls-remote --exit-code origin "refs/heads/$BR" >/dev/null 2>&1 && return 0
    local p="$W/pub-$K"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
    cp "$J" "$p/"; git -C "$p" add "shapeprove_${K}_of_$N.jsonl"
    git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3b-lift shapeprove $ID shard $K/$N ($(wc -l < "$J") records)"
    git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$BR"; local rc=$?; rm -rf "$p"; return $rc
}
# 3. resume: among this shard's branches take the one with the most records; already published = such a branch that passes shardok (nothing is re-run)
if [ "$PUBLISH" = 1 ] && [ ! -s "$J" ]; then
    BEST=0
    for r in $(git -C "$B" ls-remote origin "refs/heads/$PREFIX/$ID/${K}of${N}-*" | awk '{print $2}'); do
        git -C "$B" fetch -q --depth 1 origin "$r" || continue
        git -C "$B" show FETCH_HEAD:"shapeprove_${K}_of_$N.jsonl" > "$J.try" 2>/dev/null || continue
        c=$(wc -l < "$J.try"); if [ "$c" -gt "$BEST" ]; then BEST=$c; mv "$J.try" "$J"; fi
    done
    rm -f "$J.try"
    [ -s "$J" ] && echo "resumed: $(wc -l < "$J") of $ROWS records"
    if [ -s "$J" ] && shardok "$J"; then echo "SHAPEPROVE shard=$K/$N id=$ID already published: $ROWS records, no error"; exit 0; fi
fi
# 4. prove; the results are published every PUBLISH_EVERY seconds (default 600) while it runs, and at the end or on SIGTERM/SIGINT
ulimit -v "${VMEM_KB:-6000000}"
python3 -I "$HERE/shapeprove.py" --shapes "$QUEUE" --shard "$K/$N" --out "$OUT" --timeout "${TIMEOUT:-600}" --eip "${EIP:-sym}" --slice "${SLICE:-1}" ${LIMIT:+--limit "$LIMIT"} >> "$OUT/shapeprove_${K}_of_$N.log" 2>&1 &
PP=$!
trap 'kill $PP 2>/dev/null; wait $PP 2>/dev/null; publish; exit 143' TERM INT
LASTP=$(date +%s)
while kill -0 $PP 2>/dev/null; do
    sleep 5
    if [ $(( $(date +%s) - LASTP )) -ge "${PUBLISH_EVERY:-600}" ]; then publish || echo "increment push failed (next try in ${PUBLISH_EVERY:-600} s)" >&2; LASTP=$(date +%s); fi
done
wait $PP || true
[ -f "$J" ] || { echo "no result file; see $OUT/shapeprove_${K}_of_$N.log" >&2; tail -n 5 "$OUT/shapeprove_${K}_of_$N.log" >&2; exit 1; }
SUM=$(python3 -I - "$J" <<'PY'
import collections, json, sys
c = collections.Counter(json.loads(l)['result'] for l in open(sys.argv[1]))
print('shapes=%d %s' % (sum(c.values()), ' '.join('%s=%d' % x for x in sorted(c.items()))))
PY
)
echo "SHAPEPROVE shard=$K/$N id=$ID $SUM rows=$ROWS seconds=$(( $(date +%s) - T0 )) cores=$(nproc) mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)"
if ! shardok "$J"; then echo "SHAPEPROVE shard=$K/$N: FAILED: records != queue rows ($ROWS) or an error record; nothing is published as complete" >&2; exit 1; fi
publish || { echo "SHAPEPROVE shard=$K/$N id=$ID: PUSH FAILED"; exit 1; }
echo "published ${BR:-none}"
