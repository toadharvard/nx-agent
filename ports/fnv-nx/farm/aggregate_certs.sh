#!/bin/bash
# Aggregate the published shapeprove shards of one queue into certs.tsv and publish it.
#   bash ports/fnv-nx/farm/aggregate_certs.sh QUEUE.tsv N        (N = the shard count of the run)
# A shard counts only through a branch l3b-lift/shapeprove/<ID>/<K>of<N>-<sha12> whose jsonl passes shardok.py (records == the shard's queue rows, no error record); every other branch is ignored by rule.
# Output: branch l3b-lift/certsagg/<ID>/<N>-<sha12 of certs.tsv> (write-once, named by its content) holding certs.tsv, plus a status line: shards complete of N, rows, records by result.  A partial run (fewer than N shards) is aggregated too:
# certs_build keys every certificate by (shape, dsl_sha, IR, prover_sha), so the rows of the shards in are valid on their own.
# Env: FNVNX_BUILD (clone of fnv-nx-build, required)  GATE_CACHE (required)  FNVNX_WORK (default $GATE_CACHE/aggregate)  PUBLISH (1 default; 0: build only)  SAIL_IR (optional local IR)
set -euo pipefail
Q=${1:?usage: aggregate_certs.sh QUEUE.tsv N}; N=${2:?usage: aggregate_certs.sh QUEUE.tsv N}
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd)
B=${FNVNX_BUILD:?FNVNX_BUILD (clone of fnv-nx-build)}; W=${FNVNX_WORK:-${GATE_CACHE:?GATE_CACHE not set}/aggregate}; PUBLISH=${PUBLISH:-1}
. "$FARM/sail_toolchain.sh"; IR=${SAIL_IR:-$(sail_pinned_ir)} || exit 1; sail_args "$IR"      # the explicit toolchain arguments of certs_build (model_sail.configure)
QID=$(grep -m1 -o 'queue_id=[0-9a-f]*' "$Q" | cut -d= -f2); IRID=$(python3 -I "$HERE/certs_build.py" "${TC[@]}" --id); ID=$IRID-$QID
rm -rf "$W"; mkdir -p "$W/shards"
GOOD=0; DEFRC=0; MISSING=""; GL="$B/.git/farm.lock"
# per shard: every published branch whose jsonl has exactly the shard's queue rows and no error record qualifies (shardok); exactly one distinct content must qualify.
# Two DIFFERENT qualifying files are a failure (exit 1); a shard with none is MISSING (a partial aggregation is fine, REQUIRE_ALL=1 fails on it).
for K in $(seq 1 "$N"); do
    ROWS=$(python3 -I "$HERE/shardok.py" rows "$Q" "$K" "$N"); QH=""
    for r in $(git -C "$B" ls-remote origin "refs/heads/l3b-lift/shapeprove/$ID/${K}of${N}-*" | awk '{print $2}'); do
        flock "$GL" bash -c 'git -C "$1" fetch -q --depth 1 origin "$2" && git -C "$1" show FETCH_HEAD:"$3"' _ "$B" "$r" "shapeprove_${K}_of_$N.jsonl" > "$W/try.jsonl" 2>/dev/null || continue
        if python3 -I "$HERE/shardok.py" "$W/try.jsonl" "$ROWS"; then
            h=$(sha256sum "$W/try.jsonl" | cut -c1-16)
            if [ -z "$QH" ]; then QH=$h; mv "$W/try.jsonl" "$W/shards/shapeprove_${K}_of_$N.jsonl"
            elif [ "$QH" != "$h" ]; then echo "shard $K/$N: two different complete files qualify ($QH, $h): $r" >&2; exit 1; fi
        fi
    done
    if [ -n "$QH" ]; then GOOD=$((GOOD+1)); else MISSING="$MISSING $K"; fi
done
[ -z "${REQUIRE_ALL:-}" ] || [ -z "$MISSING" ] || { echo "shards without a complete file:$MISSING" >&2; exit 1; }
rm -f "$W/try.jsonl"
python3 -I "$HERE/certs_build.py" "${TC[@]}" --shards "$W/shards" --out "$W/certs.tsv" --report "$W/report.txt" > "$W/build.log" 2>&1 || { cat "$W/build.log" >&2; exit 1; }
# shapes whose only disagreement is a declared model defect: certified against the SDM spec of the defect (defectspec.py); a defect id without spec fails
python3 -I "$HERE/defectspec.py" "$W/shards" > "$W/defects.tsv" 2> "$W/defects.log" || DEFRC=$?; tail -n 1 "$W/defects.log"
# far memory transfers compose the EA from the PUSH r/m32 certificate: the basis must be proved in certs.tsv (X86SYS = the L5 record, FARKEYS = the pack's far shapes)
if [ -n "${FARKEYS:-}" ]; then python3 -I "$HERE/sysspec.py" --x86sys "$X86SYS_FILE" --far-only "$FARKEYS" --certs "$W/certs.tsv" > "$W/far.tsv" || { echo "far spec FAILED" >&2; exit 1; }; fi
H=$(sha256sum "$W/certs.tsv" | cut -c1-12); echo "AGGREGATE id=$ID shards=$GOOD/$N certs=$H rows=$(grep -vc '^#' "$W/certs.tsv") $(tail -n 1 "$W/build.log")"
if [ "$PUBLISH" = 1 ]; then
    BR="l3b-lift/certsagg/$ID/$N-$H"
    if ! git -C "$B" ls-remote --exit-code origin "refs/heads/$BR" >/dev/null 2>&1; then
        p="$W/pub"; mkdir -p "$p"; git -C "$p" init -q; git -C "$p" checkout -q --orphan pub; cp "$W/certs.tsv" "$p/"; [ -f "$W/far.tsv" ] && cp "$W/far.tsv" "$p/"; cp "$W/defects.tsv" "$p/"; git -C "$p" add .
        git -C "$p" -c user.name=farm -c user.email=farm@localhost commit -q -m "certs $ID: $GOOD/$N shards"
        git -C "$p" push -q "$(git -C "$B" remote get-url origin)" "pub:refs/heads/$BR"
    fi
    echo "published $BR"
fi
