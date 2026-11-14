#!/bin/bash
# Farm worker of the shape prover: prove shard K of N of the integer encoding shapes and publish the result to the private artifacts repository.
#   bash ports/fnv-nx/farm/farm_shapeprove.sh K N          (K = 1..N; the shapes at sorted position = K-1 mod N)
# Inputs (private / exe-derived, fetched from the artifacts repository fnv-nx-build, never stored in this repository):
#   branch l3b-lift/shapes        KEYS.tsv + <module>/shapes.tsv (output of proof/census.py); every shapes.tsv is checked against KEYS.tsv
#   branch tools/sail-mirror      the pinned downloads of the Sail/isla toolchain; used by farm/sail_setup.sh (MIRROR=...) only when the toolchain
#                                 (isla binary + proof IR) is not installed on the machine
# Work = the QUEUE: the integer shapes that have no valid certificate in ANY published shard (certs_build.py --queue).  A certificate is valid iff
#   (shape key, symform.shape_sha(key) of the CURRENT DSL, IR sha256) all match its record (certs_build.py header): a DSL edit re-queues exactly the shapes whose
#   Form changed; nothing else is proved again, whatever shard, run or machine produced the earlier certificate.  Shard K/N is taken from the queue.
# Output: branch  l3b-lift/shapeprove/<ID>/<K>of<N>  = shapeprove_<K>_of_<N>.jsonl (only that file), ID = <IR id 12 hex>-<queue id 12 hex>
#   (certs_build.py --id: the proof IR sha256; queue id = sha256 over the queue's (key, shape_sha) lines, so the same queue and K/N is the same branch).
#   Already published: nothing is re-run.
# Resumable: rerun the same command after an interruption (the jsonl is appended, shapes already in it are skipped).
# Env: FNVNX_BUILD (clone of fnv-nx-build, default /home/user/fnv-nx-build)  FNVNX_WORK (default /home/user/l3work/farm-shapeprove)
#      SHAPES_BRANCH (default l3b-lift/shapes; the census branch, KEYS.tsv-pinned; a re-pinned census goes to ANOTHER branch, the running farms read the head of this one)  BRANCH_PREFIX (default l3b-lift/shapeprove; tests use l3b-lift/shapeprove-test)  PUBLISH (1 default; 0: prove only)
#      TIMEOUT (s per shape, default 600)  EIP (sym|fixed, default sym)  SLICE (default 1)  LIMIT (first LIMIT shapes of the shard, default all)
#      VMEM_KB (address-space limit per process, default 6000000)  SHAPES (a shapes.tsv: skips the fetch of l3b-lift/shapes; local tests)
# Last line: SHAPEPROVE shard=K/N id=ID shapes=<n> proved=<n> ... seconds=<wall> ; exit 1 if publication failed.
set -euo pipefail
K=${1:?usage: farm_shapeprove.sh K N}; N=${2:?usage: farm_shapeprove.sh K N}
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd)
B=${FNVNX_BUILD:-/home/user/fnv-nx-build}; W=${FNVNX_WORK:-/home/user/l3work/farm-shapeprove}
PREFIX=${BRANCH_PREFIX:-l3b-lift/shapeprove}; PUBLISH=${PUBLISH:-1}
WORKDIR=${WORK:-/home/user/l3work/proof}
mkdir -p "$W/in"
exec 9> "$W/in/.lock"; flock 9      # shards started together on one machine fetch the inputs one after the other
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

# 1. toolchain: the installed one, else built from the mirror branch (sail_setup.sh prints the IR sha256 and exits 1 unless it equals the pin)
ISLA=${ISLA_EXEC:-/home/user/rems-project/isla/target/release/isla-execute-function}
if [ ! -x "$ISLA" ] || [ ! -f "$WORKDIR/sail/x86_proof.ir" ]; then
    fetch_branch tools/sail-mirror "$W/in/sail-mirror"
    MIRROR="$W/in/sail-mirror" bash "$FARM/sail_setup.sh" > "$W/sail_setup_$K.log" 2>&1 || { echo "sail_setup.sh failed (see $W/sail_setup_$K.log)" >&2; exit 1; }
fi
bash "$FARM/sail_build.sh" > "$W/sail_build_$K.log" 2>&1 || { echo "sail_build.sh failed (see $W/sail_build_$K.log): the toolchain / IR is not the pinned one" >&2; exit 1; }
SHA=$(sha256sum "${SAIL_IR:-$WORKDIR/sail/x86_proof.ir}" | cut -d' ' -f1)
grep -q "^x86_proof.ir .* $SHA\$" "$HERE/sail_pins.txt" || { echo "IR sha256 $SHA is not the pin in sail_pins.txt" >&2; exit 1; }

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
flock -u 9
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
ID="$(python3 -I "$HERE/certs_build.py" --id)-$QID"
OUT="$W/out/$ID"; mkdir -p "$OUT"
BR="$PREFIX/$ID/${K}of${N}"
if [ "$PUBLISH" = 1 ] && git -C "$B" ls-remote --exit-code origin "refs/heads/$BR" >/dev/null 2>&1; then echo "SHAPEPROVE shard=$K/$N id=$ID: already published"; exit 0; fi

# 3. prove
ulimit -v "${VMEM_KB:-6000000}"
python3 -I "$HERE/shapeprove.py" --shapes "$QUEUE" --shard "$K/$N" --out "$OUT" --timeout "${TIMEOUT:-600}" --eip "${EIP:-sym}" --slice "${SLICE:-1}" ${LIMIT:+--limit "$LIMIT"} > "$OUT/shapeprove_${K}_of_$N.log" 2>&1 || true
J="$OUT/shapeprove_${K}_of_$N.jsonl"
[ -f "$J" ] || { echo "no result file; see $OUT/shapeprove_${K}_of_$N.log" >&2; tail -n 5 "$OUT/shapeprove_${K}_of_$N.log" >&2; exit 1; }
SUM=$(python3 -I - "$J" <<'PY'
import collections, json, sys
c = collections.Counter(json.loads(l)['result'] for l in open(sys.argv[1]))
print('shapes=%d %s' % (sum(c.values()), ' '.join('%s=%d' % x for x in sorted(c.items()))))
PY
)

# 4. publish: an orphan commit holding the jsonl only
if [ "$PUBLISH" = 1 ]; then
    p="$W/pub-$K"; rm -rf "$p"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub
    cp "$J" "$p/"
    git -C "$p" add shapeprove_${K}_of_$N.jsonl
    git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "l3b-lift shapeprove $ID shard $K/$N"
    git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$BR" || { echo "SHAPEPROVE shard=$K/$N id=$ID: PUSH FAILED"; exit 1; }
    rm -rf "$p"
fi
echo "SHAPEPROVE shard=$K/$N id=$ID $SUM seconds=$(( $(date +%s) - T0 )) branch=$([ "$PUBLISH" = 1 ] && echo "$BR" || echo none)"
