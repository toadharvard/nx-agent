#!/usr/bin/env bash
# equiv_job.sh - farm job: the S11 MSVC equivalence gate (docs/EQUIVALENCE.md) for one commit, shard k of N units.
#
#   tools/farm/equiv_job.sh <commit> <k> <N> <report.md>
#   tools/farm/equiv_job.sh --seed-pristine <obj dir> <rows.jsonl>      publish an existing full pristine build
#   tools/farm/equiv_job.sh --toolchain                                  step 1 only (install / fetch / publish)
#
# 1. Installs Wine and Visual C++ 2008 SP1 / RTM into $FARM_HOME/msvc9 (tools/equiv/setup_msvc.sh), once. The unpacked
#    compilers (msvc/sp1, msvc/rtm: VC + SDK v6.0A, 440 MB, 54 MB packed) come from branch `toolchain/msvc9` of
#    the private artifacts repository when a farm has published it there; otherwise setup_msvc.sh downloads
#    Microsoft's ISOs (resumed, SHA-256 checked) and unpacks them, and this farm publishes the tree. Either way
#    setup_msvc.sh checks Microsoft's signatures on cl.exe / c1xx.dll / c2.dll and runs both cl.exe under Wine before
#    anything is used or published.
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
# Needs: Ubuntu 24.04 with root (apt: wine, wine32:i386, p7zip-full, cabextract, msitools, osslsigncode), git,
# python3, curl, about 6 GB of disk; the artifacts repository attached to the session with push. MSVC binaries,
# prepared trees and objects stay in $FARM_HOME / the private repository; the report holds numbers, unit names,
# addresses, short diagnostics.
# Environment: FARM_HOME (~/fnvnx-farm), JOBS (nproc), NICE (10), EQUIV_RULES, ARTIFACTS ($FARM_HOME/artifacts-equiv);
# EQUIV_PRISTINE_DIR=<dir with obj/u<i>.obj> uses a local pristine build and touches no artifacts.
# A failure prints its reason on stdout (the farm runner reports only its log tail): the step, the command, the exit
# status and the last lines of that step's log ($FARM_HOME/logs/, $FARM_HOME/equiv-<commit12>/).
set -eEuo pipefail
STEP=start; STEP_LOG=
step() { STEP=$1; STEP_LOG=${2:-}; }    # names the running step and its log for fail()
fail() {
  trap - ERR
  echo "equiv_job: FAILED in step '$STEP' (exit $1, line $2): $3"
  if [ -n "$STEP_LOG" ] && [ -s "$STEP_LOG" ]; then
    echo "equiv_job: last lines of $STEP_LOG:"; tail -n 40 "$STEP_LOG" | cut -c1-400
  fi
  exit "$1"
}
trap 'fail $? $LINENO "$BASH_COMMAND"' ERR
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts-equiv}
JOBS=${JOBS:-$(nproc)}; NICE=${NICE:-10}
RULES=${EQUIV_RULES:-neutral,FTOL1,STL1,STD1,ANON1,ANON2}
EXT=$FARM_HOME/ext
LOGS=$FARM_HOME/logs
mkdir -p "$FARM_HOME" "$LOGS"
artifacts() {   # refs are fetched one by one: the repository also holds every link shard
  if [ ! -d "$ARTIFACTS/.git" ]; then
    git init -q "$ARTIFACTS"; git -C "$ARTIFACTS" remote add origin https://github.com/toadharvard/fnv-nx-build
  fi
}

publish() {  # <dir> <branch> <message>: commit the dir's content as the branch's next commit (no checkout), push
  local dir=$1 br=$2 msg=$3 idx parent tree commit
  idx=$(mktemp); rm -f "$idx"
  # -f: a global excludes file must not drop *.obj / *.lib; no line-ending conversion of the SDK headers
  GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$dir" -C "$dir" -c core.autocrlf=false \
    -c core.safecrlf=false add -A -f .
  tree=$(GIT_INDEX_FILE=$idx git -C "$ARTIFACTS" write-tree); rm -f "$idx"
  parent=(); git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$br:refs/remotes/origin/$br" 2>/dev/null \
    && parent=(-p "refs/remotes/origin/$br")
  commit=$(git -C "$ARTIFACTS" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit-tree "$tree" "${parent[@]}" -m "$msg")
  for i in 1 2 3; do git -C "$ARTIFACTS" push -q -f origin "$commit:refs/heads/$br" 2>/dev/null && return 0; sleep $((i * 5)); done
  echo "equiv_job: push of $br failed (continuing)"; return 1
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

TC=$FARM_HOME/msvc9/msvc
compilers() {  # <port dir>: step 1; exports FNV_MSVC9_ROOT, FNV_MSVC9_RTM_ROOT, FNV_MSVC9_PREFIX
  local ref envline n
  if [ -z "${EQUIV_PRISTINE_DIR:-}" ] && ! { [ -f "$TC/sp1/VC/bin/cl.exe" ] && [ -f "$TC/rtm/VC/bin/cl.exe" ]; }; then
    artifacts
    if ref=$(fetch_ref toolchain/msvc9 || fetch_ref toolchain/msvc9); then
      rm -rf "$TC" "$TC.part"; mkdir -p "$TC.part"
      git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$TC.part" && mv "$TC.part" "$TC" \
        && touch "$FARM_HOME/msvc9/toolchain.remote"
    fi
  fi
  step compilers "$LOGS/setup_msvc.log"
  bash "$1/tools/equiv/setup_msvc.sh" "$FARM_HOME/msvc9" > "$STEP_LOG" 2>&1
  envline=$(tail -1 "$STEP_LOG")
  eval "$envline"
  export FNV_MSVC9_ROOT FNV_MSVC9_RTM_ROOT FNV_MSVC9_PREFIX WINEDEBUG=-all
  if [ -z "${EQUIV_PRISTINE_DIR:-}" ] && [ ! -f "$FARM_HOME/msvc9/toolchain.remote" ]; then
    artifacts
    if ! remote_has toolchain/msvc9; then
      n=$(find "$TC" -type f ! -name meta.json | wc -l)
      printf '{"cl_sp1": "15.00.30729.01", "cl_rtm": "15.00.21022.08", "cl_sp1_sha256": "%s", "cl_rtm_sha256": "%s", "files": %s, "from": "VS2008ExpressWithSP1ENUX1504728.iso, VS2008ExpressENUX1397868.iso (download.microsoft.com; SHA-256 in tools/equiv/setup_msvc.sh)"}\n' \
        "$(sha256sum "$TC/sp1/VC/bin/cl.exe" | cut -c1-16)" "$(sha256sum "$TC/rtm/VC/bin/cl.exe" | cut -c1-16)" "$n" > "$TC/meta.json"
      if publish "$TC" toolchain/msvc9 "Visual C++ 2008 Express SP1 + RTM and SDK v6.0A, unpacked by setup_msvc.sh ($n files)"; then
        touch "$FARM_HOME/msvc9/toolchain.remote"
      fi
    else
      touch "$FARM_HOME/msvc9/toolchain.remote"
    fi
  fi
}

if [ "${1:-}" = --toolchain ]; then
  compilers "$REPO/ports/fnv-nx"; echo "toolchain: $FNV_MSVC9_ROOT $FNV_MSVC9_RTM_ROOT"; exit 0
fi

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
step checkout "$LOGS/checkout.log"
bash "$(dirname "$0")/worktree.sh" "$REPO" "$SRC" "$COMMIT" > "$STEP_LOG" 2>&1
P=$SRC/ports/fnv-nx
step fetch "$LOGS/fetch.log"
bash "$P/tools/fetch.sh" "$EXT" > "$STEP_LOG" 2>&1
DECOMP=$EXT/fnv_decomp
D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
t0=$(date +%s)

# 1. compilers
compilers "$P"
EQ=(nice -n "$NICE" python3 "$P/tools/equiv/equiv.py")

# 2. pristine objects of this shard
step pristine "$LOGS/pristine.log"; : > "$STEP_LOG"
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
  have_all || { echo "equiv_job: FAILED in step 'pristine': $PR lacks units of shard $K/$N"; exit 1; }
elif ! have_all; then
  artifacts
  if ref=$(fetch_ref "msvc/pristine/$D12"); then
    git -C "$ARTIFACTS" archive "$ref" obj 2>> "$STEP_LOG" | tar -x -C "$PR" 2>> "$STEP_LOG"; touch "$PR/full.done"
  elif ref=$(fetch_ref "msvc/pristine-part/$D12/${K}of$N"); then
    git -C "$ARTIFACTS" archive "$ref" obj 2>> "$STEP_LOG" | tar -x -C "$PR" 2>> "$STEP_LOG"
    touch "$PR/part-$K-of-$N.done"
  else
    step pristine-build "$PR/build-$K-of-$N.log"
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
step pristine-assemble "$LOGS/pristine.log"
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
# the object cache (T1-fastbench, tools/equiv/unitcache.py): a unit whose prepared source, included headers, flags and
# compiler are unchanged is not compiled again (head build, EH-off builds, attribution's leave-out builds); it lives on
# branch equiv/cache/<k>of<N> (this shard its only writer). EQUIV_CACHE=0 compiles everything; EQUIV_CACHE_PUBLISH=0
# keeps it local (quick_job.sh).
EC=$FARM_HOME/equivcache-${K}of$N; EC_BEFORE=
if [ "${EQUIV_CACHE:-1}" = 1 ]; then
  step cache-fetch "$LOGS/equivcache.log"
  if [ ! -d "$EC/m" ] && [ -z "${EQUIV_PRISTINE_DIR:-}" ]; then
    artifacts; mkdir -p "$EC"
    if ref=$(fetch_ref "equiv/cache/${K}of$N"); then git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$EC"
    else   # a new shard count (quick_job.sh runs 1 of 1): seeded from every other shard's cache (content-keyed, no clash)
      for b in $(git -C "$ARTIFACTS" ls-remote origin 'refs/heads/equiv/cache/*' | awk '{print $2}' | sed 's|^refs/heads/||'); do
        ref=$(fetch_ref "$b") && git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$EC" || true
      done
    fi
  fi
  mkdir -p "$EC"; export FNVNX_EQUIV_CACHE=$EC
  EC_BEFORE=$(python3 "$P/tools/equiv/unitcache.py" stat "$EC" 2>/dev/null || true)
fi
for d in "$FARM_HOME"/equiv-*; do
  # older heads: drop the tree and every shard's objects and attribution work dirs (the reports and rows stay)
  if [ -d "$d" ] && [ "${d##*/equiv-}" != "$C12" ]; then rm -rf "$d/tree" "$d"/*-of-*/obj "$d"/*-of-*/fnvnx-equiv-attr-*; fi
done
W=$FARM_HOME/equiv-$C12; WK=$W/$K-of-$N; mkdir -p "$WK"
step prepare "$W/prepare.log"
# T1: the prepare runs in one stable directory ($FARM_HOME/eqprep/tree) under tools/pipeline/traced.py, which records
# every file it read and directory it listed; the next head keeps that tree when all of them, the decomp's head and
# the tree's own content are unchanged (an equal prepare, ~10 min, was run for every head), and W/tree is a hard-link
# copy of it (nothing writes into a tree after its prepare: the attribution's workers unlink before they write).
# EQUIV_PREPARE_REUSE=0 prepares every time.
# eqprep-begin
eqp_id() {   # the content id of a tree: paths and sha256 of every file
  python3 -I -c 'import hashlib, os, sys
h = hashlib.sha256()
for dp, dn, fn in sorted(os.walk(sys.argv[1])):
    dn.sort()
    for n in sorted(fn):
        p = os.path.join(dp, n)
        h.update(os.path.relpath(p, sys.argv[1]).encode("utf-8", "surrogateescape") + b"\0" + hashlib.sha256(open(p, "rb").read()).digest())
print(h.hexdigest())' "$1"
}
if [ ! -f "$W/tree.done" ]; then
  rm -rf "$W/tree"
  PT=$FARM_HOME/eqprep/tree; PD=$FARM_HOME/eqprep/deps.json; mkdir -p "$FARM_HOME/eqprep"
  dh=$(git -C "$DECOMP" rev-parse HEAD 2>/dev/null || echo none)
  PARGS=("$P/tools/equiv/equiv.py" prepare "$DECOMP" "$PT" --rules "$RULES")
  if [ "${EQUIV_PREPARE_REUSE:-1}" = 1 ] && [ -f "$PT.id" ] && [ "$(cat "$PT.decomp" 2>/dev/null)" = "$dh" ] &&
     python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import traced; sys.exit(0 if traced.check(sys.argv[2], sys.argv[3:]) else 1)' \
       "$P/tools/pipeline" "$PD" "${PARGS[@]}" && [ "$(eqp_id "$PT")" = "$(cat "$PT.id")" ]; then
    echo "prepare: the tree of the last head kept (every input unchanged)" > "$STEP_LOG"
  else
    rm -rf "$PT" "$PT.id" "$PT.decomp" "$PD"
    if nice -n "$NICE" python3 -I "$P/tools/pipeline/traced.py" "$PD" --exclude "$PT" -- "${PARGS[@]}" > "$STEP_LOG" 2>&1; then
      eqp_id "$PT" > "$PT.id"; echo "$dh" > "$PT.decomp"
    else
      rm -f "$PD" "$PT.id" "$PT.decomp"   # a failed prepare is never kept, and fails the job as before (set -e)
      false
    fi
  fi
  cp -al "$PT" "$W/tree"; touch "$W/tree.done"
fi
# eqprep-end
# the pristine rows from the objects (a published branch may carry no rows.jsonl; a unit without an object failed)
step refp "$WK/refp.log"
"${EQ[@]}" refp "$DECOMP" - "$PR/obj" --units "$SHARD" --out "$WK/base.jsonl" > "$STEP_LOG" 2>&1
step build "$WK/build.log"
"${EQ[@]}" build "$DECOMP" "$W/tree" --out "$WK/head.jsonl" --units "$SHARD" --objdir "$WK/obj" --jobs "$JOBS" > "$STEP_LOG" 2>&1
step compare "$WK/compare.log"
"${EQ[@]}" compare "$WK/base.jsonl" "$WK/head.jsonl" --out "$WK/cmp.json" > "$STEP_LOG" 2>&1
python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))["units"]
json.dump(dict(units=sorted(int(k) for k,v in d.items() if v["status"]=="differs")), open(sys.argv[2],"w"))' "$WK/cmp.json" "$WK/differ.json"
t2=$(date +%s)

# EH-only: the differing units without C++ exception handling on both sides
step eh-off "$WK/eh.log"
"${EQ[@]}" build "$DECOMP" "$DECOMP" --out "$WK/eh_base.jsonl" --units "$WK/differ.json" --eh-off --jobs "$JOBS" > "$STEP_LOG" 2>&1
"${EQ[@]}" build "$DECOMP" "$W/tree" --out "$WK/eh_head.jsonl" --units "$WK/differ.json" --eh-off --jobs "$JOBS" >> "$STEP_LOG" 2>&1
# attribution by rule
step attribute "$WK/attr.log"
rm -rf "$WK"/fnvnx-equiv-attr-*      # a killed earlier run's work dir (one hard-linked tree per worker, --jobs at most)
"${EQ[@]}" attribute "$DECOMP" "$W/tree" "$WK/base.jsonl" "$WK/head.jsonl" --rules "$RULES" --out "$WK/attr.jsonl" \
  --failed --hier --default-groups --units "$SHARD" --workdir "$WK" --jobs "$JOBS" > "$STEP_LOG" 2>&1
t3=$(date +%s)

t3c=$(date +%s); EC_NOTE=
if [ -n "${FNVNX_EQUIV_CACHE:-}" ]; then
  step cache-publish "$LOGS/equivcache.log"
  python3 "$P/tools/equiv/unitcache.py" prune "$EC" >> "$STEP_LOG" 2>&1 || true
  EC_AFTER=$(python3 "$P/tools/equiv/unitcache.py" stat "$EC" 2>/dev/null || true)
  EC_NOTE="object cache: $EC_BEFORE -> $EC_AFTER"
  if [ "$EC_AFTER" != "$EC_BEFORE" ] && [ -z "${EQUIV_PRISTINE_DIR:-}" ] && [ "${EQUIV_CACHE_PUBLISH:-1}" = 1 ]; then
    publish "$EC" "equiv/cache/${K}of$N" "equiv object cache $C12 shard $K/$N: $EC_AFTER" >> "$STEP_LOG" 2>&1 || true
  fi
fi

# 4. report
step report "$WK/report.log"
mkdir -p "$(dirname "$REPORT")"
"${EQ[@]}" report "$WK/base.jsonl" "$WK/head.jsonl" --attr "$WK/attr.jsonl" --eh "$WK/eh_base.jsonl" "$WK/eh_head.jsonl" \
  --title "$C12 shard $K/$N" --out "$REPORT" > "$STEP_LOG" 2>&1
{
  echo
  echo "## Run"
  echo
  echo "- commit $COMMIT, fnv_decomp $D12, rules \`$RULES\`, shard $K/$N (${#MINE[@]} units), jobs $JOBS"
  echo "- pristine objects $((t1-t0))s, prepare + build + compare $((t2-t1))s, EH-off + attribution $((t3-t2))s, cache publish $((t3c-t3))s"
  [ -z "$EC_NOTE" ] || echo "- $EC_NOTE"
} >> "$REPORT"
echo "equiv $C12 shard $K/$N: $(sed -n 3p "$REPORT")"
