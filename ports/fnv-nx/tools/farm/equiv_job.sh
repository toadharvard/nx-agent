#!/usr/bin/env bash
# equiv_job.sh - farm job: the S11 MSVC equivalence gate (docs/EQUIVALENCE.md) for one commit, shard k of N units.
#
#   tools/farm/equiv_job.sh <commit> <k> <N> <report.md>
#   tools/farm/equiv_job.sh --seed-pristine <obj dir> <rows.jsonl>      publish an existing full pristine build
#
# 1. Installs Wine and Visual C++ 2008 SP1 / RTM from Microsoft's ISOs (tools/equiv/setup_msvc.sh) into
#    $FARM_HOME/msvc9, once.
# 2. Pristine MSVC objects of the decomp, published to the private artifacts repository toadharvard/fnv-nx-build:
#    branch `msvc/pristine/<decomp12>` holds every unit (obj/u<i>.obj, meta.json, rows.jsonl optional); S13 reuses it.
#    When it is missing, this shard's pristine units are built and published as `msvc/pristine-part/<decomp12>/<k>of<N>`;
#    the first job that finds all N parts assembles and pushes the full branch. Only the objects are read: the pristine
#    rows are fingerprinted from them on every run (equiv.py refp - --units), with the head's equiv.py, into
#    $FARM_HOME/equiv-<commit12>/<k>-of-<N>/base.jsonl; a unit without an object did not compile (the vc8 unit).
# 3. Prepares the tree at <commit> with the gated rules ($EQUIV_RULES, default: the neutral set and the platform rules
#    that have an MSVC meaning) and builds the shard's units (crc32(source) mod N + 1 == k, as compile_job.sh);
#    compares per function (compare_builds' fingerprint, link-by-address bindings); builds the differing units with
#    /EHs-c- on both sides to separate EH-only differences; attributes every differing / no longer compiling unit by
#    rule (leave-out of rule groups, then of the rules of a group that explains something).
# 4. Writes <report.md>: unit and function counts, functions by kind (EH-only, retarget, code), the per-rule table,
#    the differing functions (unit, address, kind, rule) and the units that no longer compile (first error).
#
# Needs: Ubuntu 24.04 with root (apt: wine, wine32:i386, p7zip-full, cabextract, msitools), git, python3, curl, about
# 6 GB of disk; the artifacts repository attached to the session with push. MSVC binaries, prepared trees and objects
# stay in $FARM_HOME / the private repository; the report holds numbers, unit names, addresses, short diagnostics.
# Environment: FARM_HOME (~/fnvnx-farm), JOBS (nproc), NICE (10), EQUIV_RULES, ARTIFACTS ($FARM_HOME/artifacts-equiv);
# EQUIV_PRISTINE_DIR=<dir with obj/u<i>.obj> uses a local pristine build and touches no artifacts.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts-equiv}
JOBS=${JOBS:-$(nproc)}; NICE=${NICE:-10}
RULES=${EQUIV_RULES:-neutral,FTOL1,STL1,STD1,ANON1,ANON2}
EXT=$FARM_HOME/ext
mkdir -p "$FARM_HOME"
artifacts() {   # refs are fetched one by one: the repository also holds every link shard
  if [ ! -d "$ARTIFACTS/.git" ]; then
    git init -q "$ARTIFACTS"; git -C "$ARTIFACTS" remote add origin https://github.com/toadharvard/fnv-nx-build
  fi
}

publish() {  # <dir> <branch> <message>: commit the dir's content as the branch's next commit (no checkout), push
  local dir=$1 br=$2 msg=$3 idx parent tree commit
  idx=$(mktemp); rm -f "$idx"
  GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$dir" -C "$dir" add -A .
  tree=$(GIT_INDEX_FILE=$idx git -C "$ARTIFACTS" write-tree); rm -f "$idx"
  parent=(); git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$br:refs/remotes/origin/$br" 2>/dev/null \
    && parent=(-p "refs/remotes/origin/$br")
  commit=$(git -C "$ARTIFACTS" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit-tree "$tree" "${parent[@]}" -m "$msg")
  for i in 1 2 3; do git -C "$ARTIFACTS" push -q -f origin "$commit:refs/heads/$br" 2>/dev/null && return 0; sleep $((i * 5)); done
  echo "equiv_job: push of $br failed" >&2; return 1
}
fetch_ref() {  # <branch> -> local ref name, or fail
  git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$1:refs/remotes/origin/$1" 2>/dev/null && echo "refs/remotes/origin/$1"
}
remote_has() { git -C "$ARTIFACTS" ls-remote origin "refs/heads/$1" | grep -q .; }
meta() {  # <decomp dir> <msvc dir> <ok> <units>
  printf '{"decomp_commit": "%s", "cl_sp1_sha256": "%s", "cl_rtm_sha256": "%s", "units": %s, "ok": %s}\n' \
    "$(git -C "$1" rev-parse HEAD)" "$(sha256sum "$2/sp1/VC/bin/cl.exe" | cut -c1-16)" \
    "$(sha256sum "$2/rtm/VC/bin/cl.exe" | cut -c1-16)" "$4" "$3"
}

if [ "${1:-}" = --seed-pristine ]; then
  artifacts
  OBJ=$(cd "${2:?obj dir}" && pwd); ROWS=${3:?rows.jsonl}
  bash "$REPO/ports/fnv-nx/tools/fetch.sh" "$EXT" >/dev/null
  D12=$(git -C "$EXT/fnv_decomp" rev-parse HEAD | cut -c1-12)
  remote_has "msvc/pristine/$D12" && { echo "msvc/pristine/$D12 exists"; exit 0; }
  STAGE=$FARM_HOME/stage-pristine; rm -rf "$STAGE"; mkdir -p "$STAGE/obj"
  cp "$OBJ"/u*.obj "$STAGE/obj/"; cp "$ROWS" "$STAGE/rows.jsonl"
  n=$(ls "$STAGE/obj" | wc -l)
  meta "$EXT/fnv_decomp" "${MSVC9:-$FARM_HOME/msvc9/msvc}" "$n" 2988 > "$STAGE/meta.json" 2>/dev/null || echo '{}' > "$STAGE/meta.json"
  publish "$STAGE" "msvc/pristine/$D12" "pristine MSVC objects of fnv_decomp $D12 ($n units)"
  rm -rf "$STAGE"; echo "published msvc/pristine/$D12 ($n objects)"; exit 0
fi

COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
C12=${COMMIT:0:12}
SRC=$FARM_HOME/src-$C12
[ -d "$SRC" ] || git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null
DECOMP=$EXT/fnv_decomp
D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
t0=$(date +%s)

# 1. compilers
eval "$(bash "$P/tools/equiv/setup_msvc.sh" "$FARM_HOME/msvc9" | tail -1)"
export FNV_MSVC9_ROOT FNV_MSVC9_RTM_ROOT FNV_MSVC9_PREFIX WINEDEBUG=-all
EQ=(nice -n "$NICE" python3 "$P/tools/equiv/equiv.py")

# 2. pristine objects of this shard
PR=${EQUIV_PRISTINE_DIR:-$FARM_HOME/pristine-$D12}; mkdir -p "$PR/obj"
SHARD=shard:$K/$N
python3 -c 'import json,sys,zlib
k,n=map(int,sys.argv[2].split("/"))
u=json.load(open(sys.argv[1]))["units"]
print(" ".join(str(i) for i,x in enumerate(u) if zlib.crc32(x["source"].encode())%n+1==k and x["compiler"]!="vc8"))' \
  "$DECOMP/build/units.json" "$K/$N" > "$FARM_HOME/shard-$K-of-$N.txt"
read -r -a MINE < "$FARM_HOME/shard-$K-of-$N.txt"
have_all() {   # the objects of this shard are there (units that failed in pristine: the fetch left a marker)
  [ -f "$PR/full.done" ] || [ -f "$PR/part-$K-of-$N.done" ] && return 0
  for i in "${MINE[@]}"; do [ -f "$PR/obj/u$i.obj" ] || return 1; done   # MINE leaves out the vc8 unit
}
if [ -n "${EQUIV_PRISTINE_DIR:-}" ]; then
  have_all || { echo "equiv_job: $PR lacks units of shard $K/$N" >&2; exit 1; }
elif ! have_all; then
  artifacts
  if ref=$(fetch_ref "msvc/pristine/$D12"); then
    git -C "$ARTIFACTS" archive "$ref" obj | tar -x -C "$PR"; touch "$PR/full.done"
  elif ref=$(fetch_ref "msvc/pristine-part/$D12/${K}of$N"); then
    git -C "$ARTIFACTS" archive "$ref" obj | tar -x -C "$PR"; touch "$PR/part-$K-of-$N.done"
  else
    "${EQ[@]}" build "$DECOMP" "$DECOMP" --out "$PR/rows-$K-of-$N.jsonl" --units "$SHARD" --objdir "$PR/obj" \
      --jobs "$JOBS" > "$PR/build-$K-of-$N.log" 2>&1
    PART=$FARM_HOME/stage-part-$K-of-$N; rm -rf "$PART"; mkdir -p "$PART/obj"
    for i in "${MINE[@]}"; do cp "$PR/obj/u$i.obj" "$PART/obj/" 2>/dev/null || true; done
    cp "$PR/rows-$K-of-$N.jsonl" "$PART/rows.jsonl"
    publish "$PART" "msvc/pristine-part/$D12/${K}of$N" "pristine MSVC objects of fnv_decomp $D12, shard $K/$N" || true
    rm -rf "$PART"; touch "$PR/part-$K-of-$N.done"
  fi
fi
# every part there and no full branch yet: assemble it once
if [ -z "${EQUIV_PRISTINE_DIR:-}" ] && ! remote_has "msvc/pristine/$D12"; then
  parts=$(git -C "$ARTIFACTS" ls-remote origin "refs/heads/msvc/pristine-part/$D12/*of$N" | wc -l)
  if [ "$parts" = "$N" ]; then
    FULL=$FARM_HOME/stage-full; rm -rf "$FULL"; mkdir -p "$FULL/obj"
    for k in $(seq 1 "$N"); do
      ref=$(fetch_ref "msvc/pristine-part/$D12/${k}of$N") || continue
      git -C "$ARTIFACTS" archive "$ref" obj | tar -x -C "$FULL"
      git -C "$ARTIFACTS" show "$ref:rows.jsonl" >> "$FULL/rows.jsonl" 2>/dev/null || true
    done
    meta "$DECOMP" "$FARM_HOME/msvc9/msvc" "$(ls "$FULL/obj" | wc -l)" "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["units"]))' "$DECOMP/build/units.json")" > "$FULL/meta.json"
    remote_has "msvc/pristine/$D12" || publish "$FULL" "msvc/pristine/$D12" "pristine MSVC objects of fnv_decomp $D12 (from $N parts)" || true
    rm -rf "$FULL"
  fi
fi
t1=$(date +%s)

# 3. prepared tree, shard build, comparison
for d in "$FARM_HOME"/equiv-*; do [ -d "$d" ] && [ "${d##*/equiv-}" != "$C12" ] && rm -rf "$d/tree"; done
W=$FARM_HOME/equiv-$C12; WK=$W/$K-of-$N; mkdir -p "$WK"
if [ ! -f "$W/tree.done" ]; then
  rm -rf "$W/tree"; "${EQ[@]}" prepare "$DECOMP" "$W/tree" --rules "$RULES" > "$W/prepare.log" 2>&1; touch "$W/tree.done"
fi
# the pristine rows from the objects (a published branch may carry no rows.jsonl; a unit without an object failed)
"${EQ[@]}" refp "$DECOMP" - "$PR/obj" --units "$SHARD" --out "$WK/base.jsonl" > /dev/null
"${EQ[@]}" build "$DECOMP" "$W/tree" --out "$WK/head.jsonl" --units "$SHARD" --objdir "$WK/obj" --jobs "$JOBS" > "$WK/build.log" 2>&1
"${EQ[@]}" compare "$WK/base.jsonl" "$WK/head.jsonl" --out "$WK/cmp.json" > /dev/null
python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))["units"]
json.dump(dict(units=sorted(int(k) for k,v in d.items() if v["status"]=="differs")), open(sys.argv[2],"w"))' "$WK/cmp.json" "$WK/differ.json"
t2=$(date +%s)

# EH-only: the differing units without C++ exception handling on both sides
"${EQ[@]}" build "$DECOMP" "$DECOMP" --out "$WK/eh_base.jsonl" --units "$WK/differ.json" --eh-off --jobs "$JOBS" > /dev/null 2>&1
"${EQ[@]}" build "$DECOMP" "$W/tree" --out "$WK/eh_head.jsonl" --units "$WK/differ.json" --eh-off --jobs "$JOBS" > /dev/null 2>&1
# attribution by rule
"${EQ[@]}" attribute "$DECOMP" "$W/tree" "$WK/base.jsonl" "$WK/head.jsonl" --rules "$RULES" --out "$WK/attr.jsonl" \
  --failed --hier --default-groups --units "$SHARD" --workdir "$WK" --jobs "$JOBS" > "$WK/attr.log" 2>&1
t3=$(date +%s)

# 4. report
mkdir -p "$(dirname "$REPORT")"
"${EQ[@]}" report "$WK/base.jsonl" "$WK/head.jsonl" --attr "$WK/attr.jsonl" --eh "$WK/eh_base.jsonl" "$WK/eh_head.jsonl" \
  --title "$C12 shard $K/$N" --out "$REPORT" > /dev/null
{
  echo
  echo "## Run"
  echo
  echo "- commit $COMMIT, fnv_decomp $D12, rules \`$RULES\`, shard $K/$N (${#MINE[@]} units), jobs $JOBS"
  echo "- pristine objects $((t1-t0))s, prepare + build + compare $((t2-t1))s, EH-off + attribution $((t3-t2))s"
} >> "$REPORT"
echo "equiv $C12 shard $K/$N: $(sed -n 3p "$REPORT")"
