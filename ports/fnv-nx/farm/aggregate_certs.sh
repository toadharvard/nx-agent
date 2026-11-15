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
QID=$(grep -m1 -o 'queue_id=[0-9a-f]*' "$Q" | cut -d= -f2); IRID=$(python3 -I "$HERE/certs_build.py" --id); ID=$IRID-$QID
rm -rf "$W"; mkdir -p "$W/shards"
GOOD=0; DEFRC=0
for K in $(seq 1 "$N"); do
    ROWS=$(python3 -I "$HERE/shardok.py" rows "$Q" "$K" "$N")
    for r in $(git -C "$B" ls-remote origin "refs/heads/l3b-lift/shapeprove/$ID/${K}of${N}-*" | awk '{print $2}'); do
        git -C "$B" fetch -q --depth 1 origin "$r" || continue
        git -C "$B" show FETCH_HEAD:"shapeprove_${K}_of_$N.jsonl" > "$W/try.jsonl" 2>/dev/null || continue
        if python3 -I "$HERE/shardok.py" "$W/try.jsonl" "$ROWS"; then mv "$W/try.jsonl" "$W/shards/shapeprove_${K}_of_$N.jsonl"; GOOD=$((GOOD+1)); break; fi
    done
done
rm -f "$W/try.jsonl"
python3 -I "$HERE/certs_build.py" --shards "$W/shards" --out "$W/certs.tsv" --report "$W/report.txt" > "$W/build.log" 2>&1 || { cat "$W/build.log" >&2; exit 1; }
# shapes whose only disagreement is a declared model defect: certified against the SDM spec of the defect (defectspec.py); a defect id without spec fails
python3 -I "$HERE/defectspec.py" "$W/shards" > "$W/defects.tsv" 2> "$W/defects.log" || DEFRC=$?; tail -n 1 "$W/defects.log"
# far memory transfers compose the EA from the PUSH r/m32 certificate: the basis must be proved in certs.tsv (X86SYS = the L5 record, FARKEYS = the pack's far shapes)
if [ -n "${X86SYS:-}" ] && [ -n "${FARKEYS:-}" ]; then FNVNX_X86SYS="$X86SYS" python3 -I "$HERE/sysspec.py" --far-only "$FARKEYS" --certs "$W/certs.tsv" > "$W/far.tsv" || { echo "far spec FAILED" >&2; exit 1; }; fi
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
