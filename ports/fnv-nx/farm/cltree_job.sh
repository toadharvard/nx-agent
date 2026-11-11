#!/usr/bin/env bash
# cltree_job.sh - L4 farm job: MSVC 2008 (cl) objects of the PREPARED tree of one commit, shard k of N, then the name
# gate (tools/link/namegate.py) of those objects against the GCC objects compile_job.sh published for the same shard.
#
#   FNVNX_PREPARE_SKIP=<rules> FARM_HOME=... ARTIFACTS=... bash <checkout>/ports/fnv-nx/farm/cltree_job.sh <commit> <k> <N> <report.md>
#
# Two roots: FARM_SCRIPTS = the repository this script lives in (its tools/farm/lib_job.sh, worktree.sh); SRC = the
# commit's own checkout ($CLTREE_SRC, which must be at <commit>, else the stable worktree $FARM_HOME/cltree-src made by
# lib_job.sh job_sources, after fetching $CLTREE_BRANCH, default claude/fnv-l2-source). All tools the job runs
# (equiv_job.sh --toolchain, setup_msvc.sh, prepare.py, fetch_tree.sh, build_tree.py, namegate.py) come from SRC.
# Steps: toolchain (Wine + VC9, tools/farm/equiv_job.sh --toolchain, then its setup_msvc.sh line exports FNV_MSVC9_ROOT /
# _RTM_ROOT / _PREFIX exactly as equiv_job.sh compilers() does, lines 96-100) | prepared tree | build_tree.py --units
# shard:K/N | publish branch msvc/prepared/<commit12>/<k>of<N> (obj/u*.obj, obj/u*.err, clbuild.tsv, meta.json) | namegate.
# The tree: $FARM_HOME/cltree-<c12>/tree with tree.id (commit, decomp commit, SKIP, tree_hash.sh). Taken in this order:
# own tree whose tree.id matches; with an empty SKIP only: compile_job.sh's compile-<c12>/tree (tree.done) copied, or
# fetch_tree.sh (t/<c12>, one try); else prepare.py as compile_job.sh does (FNVNX_PREPARE_SKIP from the environment).
# A tree whose hash differs from the published c/<c12>/<k>of<N> meta.json tree_hash: adopted trees exit 1; a freshly
# prepared one is still built and published, and the report says why the name gate did not run.
# Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS ($FARM_HOME/artifacts), JOBS (nproc), NICE (10), FNVNX_PREPARE_SKIP,
# CLTREE_BRANCH, CLTREE_SRC, CLTREE_NAMEGATE (1), CLTREE_OBJS_C12 (GCC objects from c/<that>/<k>of<N>; same tree hash required). Needs: what equiv_job.sh --toolchain and compile_job.sh need (root, Wine,
# ~6 GB toolchain + the tree + objects, aarch64 binutils for the gate). Exit 0: report written (publish ok); 1: the
# job could not run (no toolchain, no tree, no clbuild.tsv, publish failed; the report says which when it was started).
# The tree and the objects are decomp-derived: private artifacts repository only; the report holds numbers and unit names.
set -euo pipefail
COMMIT=${1:?usage: cltree_job.sh <commit> <k> <N> <report.md>}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
REPORT=$(mkdir -p "$(dirname "$REPORT")" && cd "$(dirname "$REPORT")" && echo "$PWD/$(basename "$REPORT")")
FARM_SCRIPTS=$(cd "$(dirname "$0")/../../.." && pwd)            # repository root of this script
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}; JOBS=${JOBS:-$(nproc)}; NICE=${NICE:-10}
BRANCH=${CLTREE_BRANCH:-claude/fnv-l2-source}; SKIP=${FNVNX_PREPARE_SKIP:-}
export FARM_HOME ARTIFACTS FNVNX_PREPARE_SKIP="$SKIP"
mkdir -p "$FARM_HOME"
# shellcheck source=/dev/null
source "$FARM_SCRIPTS/ports/fnv-nx/tools/farm/lib_job.sh"       # log, job_sources (sets SRC P C12 EXT D12, clones ARTIFACTS)
die() { log "FAILED: $*"; exit 1; }
case $K$N in *[!0-9]*) die "k and N must be numbers (got $K $N)" ;; esac
{ [ "$K" -ge 1 ] && [ "$K" -le "$N" ]; } || die "k=$K is outside 1..$N"

# 1. sources at <commit>
git -C "$FARM_SCRIPTS" fetch -q origin "$BRANCH" || log "fetch of $BRANCH failed (using what the repository has)"
COMMIT=$(git -C "$FARM_SCRIPTS" rev-parse -q --verify "$COMMIT^{commit}" 2>/dev/null) \
  || die "commit $1 is not in $FARM_SCRIPTS even after fetching origin $BRANCH"
if [ -n "${CLTREE_SRC:-}" ]; then FARM_SRC=$CLTREE_SRC; export FARM_SRC; else FARM_STABLE_SRC=$FARM_HOME/cltree-src; fi
job_sources "$FARM_SCRIPTS"
for f in tools/equiv/build_tree.py tools/link/namegate.py tools/farm/equiv_job.sh tools/equiv/setup_msvc.sh tools/farm/tree_hash.sh; do
  [ -f "$P/$f" ] || die "$P/$f is missing: $COMMIT is not a commit of $BRANCH (the L2 tools)"
done
W=$FARM_HOME/cltree-$C12; OUT=$W/out-${K}of$N; mkdir -p "$W" "$OUT"
log "commit $C12 shard $K/$N skip='${SKIP}' src=$SRC decomp=$D12 jobs=$JOBS"

# 2. Wine + VC9: equiv_job.sh --toolchain installs / fetches branch toolchain/msvc9 (its ARTIFACTS is ours, a clone)
bash "$P/tools/farm/equiv_job.sh" --toolchain > "$W/toolchain.log" 2>&1 && rc=0 || rc=$?
[ "$(tail -1 "$W/toolchain.log" | cut -c1-11)" = "toolchain: " ] || { tail -20 "$W/toolchain.log" | cut -c1-300; die "no toolchain (rc=$rc)"; }
envline=$(tail -1 "$FARM_HOME/logs/setup_msvc.log") && eval "$envline"
export FNV_MSVC9_ROOT FNV_MSVC9_RTM_ROOT FNV_MSVC9_PREFIX WINEDEBUG=-all
[ -f "$FNV_MSVC9_ROOT/VC/bin/cl.exe" ] || die "no cl.exe under $FNV_MSVC9_ROOT"

# 3. the prepared tree
TREE=$W/tree; DCOMMIT=$(git -C "$EXT/fnv_decomp" rev-parse HEAD)
KEY="commit=$COMMIT decomp=$DCOMMIT skip=$SKIP"
TH() { bash "$P/tools/farm/tree_hash.sh" "$TREE"; }
t0=$(date +%s); how=""
if [ -f "$W/tree.id" ] && [ "$(head -1 "$W/tree.id")" = "$KEY" ] && [ -f "$TREE/build/units.json" ] && [ "$(TH)" = "$(sed -n 2p "$W/tree.id")" ]; then
  how="own tree of an earlier run (tree.id matches)"
else
  rm -rf "$TREE" "$W/tree.id"
  if [ -z "$SKIP" ] && [ -f "$FARM_HOME/compile-$C12/tree.done" ] && [ -f "$FARM_HOME/compile-$C12/tree/build/units.json" ]; then
    mkdir -p "$TREE"; rsync -rc "$FARM_HOME/compile-$C12/tree/" "$TREE/"; how="compile_job.sh's tree compile-$C12/tree (copied)"
  elif [ -z "$SKIP" ] && bash "$P/tools/farm/fetch_tree.sh" "$COMMIT" "$TREE" 0 > "$W/fetch_tree.log" 2>&1; then
    how="published tree (fetch_tree.sh, t/$C12 or the rolling tree)"
  else
    rm -rf "$TREE"; python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$TREE" > "$W/prepare.log" 2>&1 \
      || { tail -10 "$W/prepare.log" | cut -c1-300; die "prepare.py failed"; }
    how="prepare.py (as compile_job.sh does) with SKIP='$SKIP'"
  fi
  [ -f "$TREE/build/units.json" ] || die "no prepared tree: $TREE/build/units.json is missing"
  printf '%s\n%s\n' "$KEY" "$(TH)" > "$W/tree.id"
fi
TREE_HASH=$(TH); t1=$(date +%s); log "tree $TREE_HASH: $how"
CB=c/${CLTREE_OBJS_C12:-$C12}/${K}of$N; GCCMETA=""   # CLTREE_OBJS_C12: the GCC objects of another commit; the tree hash decides
if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$CB:refs/remotes/origin/$CB" 2>/dev/null; then
  GCCMETA=$(git -C "$ARTIFACTS" show "refs/remotes/origin/$CB:meta.json")
  ghash=$(python3 -I -c 'import json,sys; print(json.loads(sys.argv[1]).get("tree_hash",""))' "$GCCMETA")
  case $how in compile_job*|published*) [ "$ghash" = "$TREE_HASH" ] || die "tree hash $TREE_HASH ($how) != $ghash of $CB: not the tree the GCC objects were built from" ;; esac
fi

# 4. cl objects (the tool skips units already built; its rc is recorded, clbuild.tsv decides)
# equiv.gen_dir() writes its generated headers through a temporary file named by the process id; build_tree.py's
# workers are threads of one process, so the first use must happen once, before the pool (reported to L2).
(cd "$P/tools/equiv" && python3 -c 'import equiv; equiv.gen_dir()') > "$W/gen_dir.log" 2>&1 \
  || { tail -5 "$W/gen_dir.log" | cut -c1-300; die "equiv.gen_dir() failed"; }
nice -n "$NICE" python3 "$P/tools/equiv/build_tree.py" "$EXT/fnv_decomp" "$TREE" "$OUT" --units "shard:$K/$N" --jobs "$JOBS" \
  > "$W/build_tree-$K.log" 2> "$W/build_tree-$K.err" && brc=0 || brc=$?
t2=$(date +%s)
[ -f "$OUT/clbuild.tsv" ] || { tail -15 "$W/build_tree-$K.err" | cut -c1-300; die "build_tree rc=$brc and no clbuild.tsv"; }
UNITS=$(grep -vc '^#' "$OUT/clbuild.tsv" || true); OK=$(grep -c $'\tok\t' "$OUT/clbuild.tsv" || true); FAIL=$(grep -c $'\tfail\t' "$OUT/clbuild.tsv" || true)
log "build_tree rc=$brc: $UNITS units, $OK ok, $FAIL fail, $((t2-t1)) s"

# 5. publish (commit-tree without a checkout and a forced push with retries, as equiv_job.sh publish(), lines 61-71)
PB=msvc/prepared/$C12/${K}of$N; ST=$W/stage; rm -rf "$ST"; mkdir -p "$ST/obj"
find "$OUT" -maxdepth 1 \( -name 'u*.obj' -o -name 'u*.err' \) -exec cp -l -t "$ST/obj" {} + 2>/dev/null \
  || find "$OUT" -maxdepth 1 \( -name 'u*.obj' -o -name 'u*.err' \) -exec cp -t "$ST/obj" {} +
cp "$OUT/clbuild.tsv" "$ST/"
cat > "$ST/meta.json" <<EOF
{"commit": "$COMMIT", "decomp_commit": "$DCOMMIT", "shard": $K, "shards": $N, "skip": "$SKIP", "tree_hash": "$TREE_HASH",
 "tree_from": "$how", "units": $UNITS, "ok": $OK, "fail": $FAIL, "build_tree_rc": $brc, "build_s": $((t2-t1)), "prepare_s": $((t1-t0)),
 "tool_commit": "$(git -C "$SRC" rev-parse HEAD)", "build_tree_blob": "$(git -C "$SRC" hash-object "$P/tools/equiv/build_tree.py" | cut -c1-12)",
 "cl_sha256": "$(sha256sum "$FNV_MSVC9_ROOT/VC/bin/cl.exe" | cut -c1-16)"}
EOF
idx=$(mktemp -u); GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$ST" -C "$ST" -c core.autocrlf=false add -A -f .
tr=$(GIT_INDEX_FILE=$idx git -C "$ARTIFACTS" write-tree); rm -f "$idx"; par=()
git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$PB:refs/remotes/origin/$PB" 2>/dev/null && par=(-p "refs/remotes/origin/$PB")
cm=$(git -C "$ARTIFACTS" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit-tree "$tr" "${par[@]}" \
  -m "$C12 shard $K/$N: cl objects of the prepared tree, $OK ok, $FAIL failed")
PUB=failed
for i in 1 2 3; do git -C "$ARTIFACTS" push -q -f origin "$cm:refs/heads/$PB" 2>/dev/null && { PUB=$PB; break; }; sleep $((i * 5)); done
log "publish: $PUB"

# 6. name gate against the GCC objects of the same shard (fetch_objs.sh's layout: objects.tsv with absolute paths)
GATE="namegate: not run (CLTREE_NAMEGATE=0)"
if [ "${CLTREE_NAMEGATE:-1}" = 1 ]; then
  if [ -z "$GCCMETA" ]; then GATE="namegate: no GCC objects published for this commit and shard"
  elif [ "$ghash" != "$TREE_HASH" ]; then GATE="namegate: not run, GCC objects ($CB) were built from tree $ghash, this tree is $TREE_HASH ($how)"
  elif ! command -v "${NM:-aarch64-linux-gnu-nm}" >/dev/null; then GATE="namegate: not run, no aarch64-linux-gnu-nm on this host"
  else
    G=$W/gcc-${K}of$N; rm -rf "$G"; mkdir -p "$G"
    git -C "$ARTIFACTS" archive "refs/remotes/origin/$CB" obj | tar -x -C "$G" --strip-components=1
    git -C "$ARTIFACTS" show "refs/remotes/origin/$CB:objects.tsv" | awk -F'\t' -v d="$G" '{sub(/^obj\//,"",$2); print $1"\t"d"/"$2"\t"$3}' > "$G/objects.tsv"
    python3 -I "$P/tools/link/namegate.py" "$G" "$TREE/build/units.json" "$OUT" --out "$W/gate-$K.json" > "$W/namegate-$K.log" 2>&1 && grc=0 || grc=$?
    GATE="namegate rc=$grc:"
  fi
fi
log "$GATE"

# 7. report (numbers only)
{ printf '# cltree %s shard %s/%s\n\n- commit %s, decomp %s, tool commit %s\n- SKIP: %s\n- tree %s (%s)\n' "$C12" "$K" "$N" "$COMMIT" "${DCOMMIT:0:12}" "$(git -C "$SRC" rev-parse --short=12 HEAD)" "${SKIP:-none}" "$TREE_HASH" "$how"
  printf -- '- units in shard %s, cl ok %s, cl fail %s (build_tree rc %s)\n- seconds: tree %s, cl build %s, jobs %s\n- published: %s in toadharvard/fnv-nx-build\n\n' \
    "$UNITS" "$OK" "$FAIL" "$brc" "$((t1-t0))" "$((t2-t1))" "$JOBS" "$PUB"
  printf '## build_tree\n\n```\n'; head -20 "$W/build_tree-$K.log"; printf '```\n\n## name gate\n\n%s\n' "$GATE"
  if [ -s "$W/namegate-$K.log" ]; then printf '\n```\n'; head -30 "$W/namegate-$K.log"; printf '```\n'; fi
} > "$REPORT"
log "report written: $REPORT"
[ "$PUB" != failed ] || die "publish of $PB failed (report written)"
exit 0
