#!/usr/bin/env bash
# fuzz_job.sh - farm job: differential fuzzing (tools/difffuzz, docs/DIFFFUZZ.md) of shard k of N of every decomp
# function: the native AArch64 ILP32 function of <commit> against the pristine decomp compiled with MSVC 2008.
#
#   tools/farm/fuzz_job.sh <commit> <k> <N> <report.md>
#
# Inputs, fetched or built when missing, cached under $FARM_HOME:
#   oracle     MSVC 2008 objects of the pristine decomp, one per unit (u<index in units.json>.obj). Fetched from
#              branch msvc/pristine/<decomp12> of the private artifacts repository; if that branch does not exist
#              yet, built here (Wine + Visual C++ 2008 Express SP1/RTM unpacked from Microsoft's ISOs,
#              tools/equiv/README.md; tools/equiv/setup_msvc.sh when present) and published there once.
#   link       the link farm's outputs (tools/farm/link_job.sh publishes branch link/<commit12>: out/, fixed/*.o,
#              meta.json; history on link-head) of the newest commit L that is an ancestor of <commit> or <commit>
#              itself (the link farm publishes only the heads it gets to), searched $FUZZ_LINK_DEPTH (300) commits
#              back and waited for up to $FUZZ_LINK_WAIT s (default 1800), else exit 3. Everything else follows
#              L, so the native side is exactly what that link linked: its rewritten objects (fixed/; meta.json
#              objects_commit names the compile farms' commit they came from), its tree (prepare farm, branch
#              t/<L12> or the rolling tree with the same prepare inputs: tools/farm/fetch_tree.sh) and L's tools
#              and pins (worktree src-<L12>: decomp, sse2neon). The report names L. $FUZZ_LINK_DIR=<dir with out/
#              and fixed/> (and $FUZZ_LINK_COMMIT) uses a local link instead.
# Campaign: every catalogued oracle function whose unit has both objects and whose control flow the harness models
# (no indirect calls or jumps, no unresolved relocations: counted as "not modelled"), shard = crc32(unit source)
# mod N + 1 (a unit's functions stay together: its native image is linked once), at most $ITERS executions and $TIME seconds per function (defaults 40 and 8), $JOBS processes, under nice.
# Incremental across heads: each verdict is cached with the function's key (tools/difffuzz/fnkeys.py: oracle code,
# native code and relocations of the function and of what it reaches in its unit and the bridges, harness
# version); a head fuzzes only functions whose key changed, then those never fuzzed. The cache of a shard is branch
# fuzz/cache/<k>of<N> of the artifacts repository (results.jsonl + div/), pushed after every $FUZZ_CHUNK (200)
# functions, so a recreated machine continues from it; a new shard count is seeded from the existing caches.
# $FUZZ_BUDGET=<seconds> stops after the chunk that reaches it (the rest is "pending", done by the next run).
# Deadline (J-0138): the job must have published its report before the farm session kills the runner (runner.sh's
# FARM_DEADLINE, 5 min before the 2 h background limit; else $FUZZ_MAX_S, default 5400 s, after the job's start).
# No chunk starts later than $FUZZ_REPORT_S (900) s before it, and a running chunk is stopped then: its unfinished
# functions stay pending. Without this a shard whose pass took longer than the runner's life (fz1, fz2, ... on the
# 26-shard layout) published nothing at all.
# A wall-clock watchdog per chunk (function count x 3 x TIME + 30 s / JOBS, x2) kills a hung chunk; its unfinished
# functions are recorded as run:timeout and fuzzed again by the next run, after everything else (fuzzcache.py todo).
# Every function has a hard wall-clock bound too (fuzz.py fn_limit, each machine's run world.RUN_TIMEOUT_US). After each chunk the job looks at the
# integration head ($FUZZ_HEAD_BRANCH): when its fnkeys.HARNESS differs from this checkout's, it stops and reports,
# so the runner moves to the newest head.
# Resumable: a rerun with the same arguments continues after the last finished function.
# Output: <report.md> (counts new this head and cumulative, verdicts by stream and cause for both, the cumulative hard
# divergences as address, cause, stream, unit) and this head's reproducers on branch fuzz/<commit12>/<k>of<N>.
#
# Needs: git, python3 with venv or pip, g++-aarch64-linux-gnu, binutils-aarch64-linux-gnu; to build the oracle:
# root or sudo (apt: wine wine32:i386 p7zip-full cabextract msitools), curl, about 4 GB of free disk while unpacking.
# Push access to toadharvard/fnv-nx-build. The oracle objects, the candidate objects and the reproducers are derived
# from the decomp: they go only to that private repository, never to nx-agent. The report holds counts, addresses,
# unit names and causes only.
# Local inputs: FUZZ_X86OBJ=<dir of u<i>.obj> seeds the oracle cache (and publishes it when the branch is missing);
# FUZZ_TREE, FUZZ_LINK_DIR, FUZZ_EXT use a local tree, link, external checkouts; ARTIFACTS_URL another artifacts
# remote (a local stand-in for tests).
# Testing: FUZZ_LIMIT=<n> fuzzes the first n targets of the shard; FUZZ_PUBLISH=0 skips every push.
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
DF=$HERE/../difffuzz
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
JOBS=${JOBS:-$(nproc)}
ITERS=${ITERS:-40}; TIME=${TIME:-8}
PUBLISH=${FUZZ_PUBLISH:-1}
C12=${COMMIT:0:12}
EXT=${FUZZ_EXT:-$FARM_HOME/ext}
RUN=$FARM_HOME/fuzz-$C12-${K}of${N}
mkdir -p "$FARM_HOME" "$RUN" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
# shellcheck source=lib_job.sh
. "$HERE/lib_job.sh"                 # job_gamedata (the player's exe, J-0128 / V1); its log() is replaced below
log() { echo "== $(date +%T) $*"; }
t0=$(date +%s)

fail_report() {   # fail_report <exit code> <message>
  printf '# fuzz %s shard %s/%s\n\n- status: not run\n- %s\n' "$C12" "$K" "$N" "$2" > "$REPORT"
  echo "fuzz_job: $2" >&2
  exit "$1"
}

publish() {   # publish <branch> <dir> <message>: one orphan commit of <dir>'s files, pushed to the artifacts repository
  [ "$PUBLISH" = 1 ] || { log "publish $1 skipped (FUZZ_PUBLISH=0)"; return 0; }
  local tmp=$FARM_HOME/pub-$$
  rm -rf "$tmp"; mkdir -p "$tmp"
  cp -a "$2/." "$tmp/"
  git -C "$tmp" init -q
  git -C "$tmp" add -A
  git -C "$tmp" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$3"
  local ok=0
  for i in 1 2 3; do
    if git -C "$tmp" push -q -f "$ARTIFACTS_URL" "HEAD:refs/heads/$1" 2>/dev/null; then ok=1; break; fi
    sleep $((i * 10))
  done
  rm -rf "$tmp"
  [ $ok = 1 ] || { echo "fuzz_job: push of $1 failed" >&2; return 1; }
  log "published $1"
}

fetch_branch() {  # fetch_branch <branch> <dest dir>: extract the branch's files; false when it does not exist
  [ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
  local ref="refs/remotes/origin/$1"
  git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$1:$ref" 2>/dev/null || return 1
  rm -rf "$2.tmp"; mkdir -p "$2.tmp"
  git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$2.tmp"
  rm -rf "$2"; mv "$2.tmp" "$2"
}

setup_msvc_inline() {   # <dest>: the decomp README's "Compilers without Windows", for commits without setup_msvc.sh
  local dest=$1 ms=https://download.microsoft.com/download v
  local -A url=([sp1]=$ms/E/8/E/E8EEB394-7F42-4963-A2D8-29559B738298/VS2008ExpressWithSP1ENUX1504728.iso
                [rtm]=$ms/8/B/5/8B5804AD-4990-40D0-A6AA-CE894CBBB3DC/VS2008ExpressENUX1397868.iso)
  local -A sha=([sp1]=6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91
                [rtm]=632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead)
  local sudo=; [ "$(id -u)" = 0 ] || sudo=sudo
  if ! command -v wine >/dev/null || ! command -v 7z >/dev/null || ! command -v cabextract >/dev/null \
      || ! command -v msiextract >/dev/null; then
    $sudo dpkg --add-architecture i386
    $sudo apt-get update -q >/dev/null
    DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -q wine wine32:i386 wine64 p7zip-full cabextract msitools \
      >/dev/null
  fi
  for v in sp1 rtm; do
    local r=$dest/msvc/$v w=$dest/unpack-$v
    [ -f "$r/VC/bin/cl.exe" ] && [ -d "$r/SDK/Include" ] && continue
    rm -rf "$w" "$r"; mkdir -p "$w" "$r"
    curl -sSL --retry 5 --retry-all-errors -o "$w/vs.iso" "${url[$v]}"
    echo "${sha[$v]}  $w/vs.iso" | sha256sum -c --quiet - || { echo "fuzz_job: $v ISO checksum mismatch" >&2; return 1; }
    7z x -y -o"$w/tc" "$w/vs.iso" VCExpress >/dev/null; rm -f "$w/vs.iso"
    cabextract -q -d "$w/ixp" "$w/tc/VCExpress/Ixpvc.exe"
    (cd "$w/ixp" && msiextract -C ../vcx vs_setup.msi >/dev/null)
    local vs="$w/vcx/Program Files/Microsoft Visual Studio 9.0"
    cp -r "$vs/VC:Vc7" "$r/VC"
    for f in mspdb80.dll msobj80.dll mspdbcore.dll mspdbsrv.exe; do
      find "$vs/Common7" -iname "$f" -exec cp {} "$r/VC/bin/" \;
    done
    cabextract -q -d "$w/sdkx" "$w/tc/VCExpress/WCU/WinSDK/WinSDK_Build.exe"
    (cd "$w/sdkx" && msiextract -C ../sdko VistaClientHeadersLibs-x86.msi >/dev/null)
    local sd="$w/sdko/SOURCEDIR/Program Files/Microsoft SDKs:./Windows:./v6.0A:."
    mkdir -p "$r/SDK"; cp -r "$sd/Include" "$r/SDK/Include"; cp -r "$sd/Lib" "$r/SDK/Lib"
    rm -rf "$w"
  done
  WINEPREFIX=$dest/wineprefix WINEARCH=win32 WINEDEBUG=-all timeout 600 wineboot -i >/dev/null 2>&1 || true
  echo "export FNV_MSVC9_ROOT=$dest/msvc/sp1 FNV_MSVC9_RTM_ROOT=$dest/msvc/rtm FNV_MSVC9_PREFIX=$dest/wineprefix"
}

# --- the link: newest link/<L12> on <commit> or an ancestor --------------------------------------------------------
find_link() {   # -> full hash of the newest commit among <commit> and its ancestors with a branch link/<commit12>
  local have c
  have=$(git ls-remote "$ARTIFACTS_URL" 'refs/heads/link/*' 2>/dev/null \
         | sed -n 's|.*refs/heads/link/\([0-9a-f]\{12\}\)$|\1|p')
  [ -n "$have" ] || return 0
  for c in $(git -C "$REPO" rev-list --max-count="${FUZZ_LINK_DEPTH:-300}" "$COMMIT"); do
    if grep -qx "${c:0:12}" <<<"$have"; then echo "$c"; return 0; fi
  done
}
if [ -n "${FUZZ_LINK_DIR:-}" ]; then
  LINK=$FUZZ_LINK_DIR; LCOMMIT=$(git -C "$REPO" rev-parse "${FUZZ_LINK_COMMIT:-$COMMIT}^{commit}")
  NOTE_LINK="local link $LINK (commit ${LCOMMIT:0:12})"
else
  end=$(( $(date +%s) + ${FUZZ_LINK_WAIT:-1800} ))
  LCOMMIT=$(find_link)
  while [ -z "$LCOMMIT" ]; do
    [ "$(date +%s)" -lt "$end" ] || fail_report 3 "no link/<commit12> for $C12 or an ancestor within \
${FUZZ_LINK_DEPTH:-300} commits after waiting ${FUZZ_LINK_WAIT:-1800}s (tools/farm/link_job.sh publishes them)"
    log "waiting for a link of $C12 or an ancestor"
    sleep 60
    LCOMMIT=$(find_link)
  done
  LINK=$FARM_HOME/link-${LCOMMIT:0:12}
  if [ ! -f "$LINK/.done" ]; then
    fetch_branch "link/${LCOMMIT:0:12}" "$LINK" || fail_report 3 "link/${LCOMMIT:0:12} could not be fetched"
    touch "$LINK/.done"
  fi
  NOTE_LINK="link/${LCOMMIT:0:12}"
fi
L12=${LCOMMIT:0:12}
OBJ_COMMIT=$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("objects_commit") or "")
except (OSError, ValueError): print("")' "$LINK/meta.json")
NOTE_LINK="$NOTE_LINK: $([ "$LCOMMIT" = "$(git -C "$REPO" rev-parse "$COMMIT^{commit}")" ] && echo "this commit" \
  || echo "ancestor ${L12} ($(git -C "$REPO" rev-list --count "$LCOMMIT..$COMMIT") commits back)")\
${OBJ_COMMIT:+, objects of ${OBJ_COMMIT:0:12}}"
for f in out/aliases.ld out/refs.tsv out/abi.tsv out/addrmap.tsv fixed/zz_bridges.o; do
  [ -e "$LINK/$f" ] || fail_report 3 "$NOTE_LINK lacks $f"
done
log "link: $NOTE_LINK"

# --- the link commit's sources, the decomp, Python modules ---------------------------------------------------------
SRC=$FARM_HOME/src-$L12
bash "$(dirname "$0")/worktree.sh" "$REPO" "$SRC" "$LCOMMIT" >/dev/null
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null
DECOMP=$EXT/fnv_decomp
D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
PY=python3
PYMODS='import unicorn, capstone, elftools'          # the harness's imports (check_rebind.py needs pyelftools)
PIPS="unicorn==2.1.4 capstone==5.0.9 pyelftools==0.33"
if ! python3 -c "$PYMODS" 2>/dev/null; then
  if [ ! -x "$FARM_HOME/venv/bin/python3" ]; then
    python3 -m venv "$FARM_HOME/venv" 2>/dev/null || { rm -rf "$FARM_HOME/venv"; }
  fi
  if [ -x "$FARM_HOME/venv/bin/python3" ]; then
    PY=$FARM_HOME/venv/bin/python3
    # shellcheck disable=SC2086
    $PY -c "$PYMODS" 2>/dev/null || $PY -m pip install -q $PIPS
  else
    # shellcheck disable=SC2086
    python3 -m pip install -q --user --break-system-packages $PIPS
  fi
fi
# Preflight: every module the harness imports must load here, or each function would come back as one harness
# exception ("crash:ModuleNotFoundError") instead of a verdict.
if ! (cd "$DF" && $PY -c 'import fuzz, classify, check_rebind, fnkeys, fuzzcache, effects, vtmodel' ) \
    > "$FARM_HOME/fuzz-preflight.log" 2>&1; then
  echo "fuzz harness does not load on this host ($PY):"; tail -5 "$FARM_HOME/fuzz-preflight.log"; exit 1
fi

# --- oracle: MSVC 2008 objects of the pristine decomp --------------------------------------------------------------
ORACLE=$FARM_HOME/oracle-$D12
if [ ! -f "$ORACLE/.done" ]; then
  if fetch_branch "msvc/pristine/$D12" "$ORACLE"; then
    log "oracle fetched from msvc/pristine/$D12"
  elif [ -n "${FUZZ_X86OBJ:-}" ]; then
    log "oracle: local objects $FUZZ_X86OBJ"
    rm -rf "$ORACLE"; mkdir -p "$ORACLE/obj"
    cp "$FUZZ_X86OBJ"/u*.obj "$ORACLE/obj/"
    printf '{"decomp_commit": "%s", "objects": %s, "built_by": "equiv.py build pristine (local copy)"}\n' \
      "$(git -C "$DECOMP" rev-parse HEAD)" "$(ls "$ORACLE/obj" | grep -c '\.obj$')" > "$ORACLE/meta.json"
    publish "msvc/pristine/$D12" "$ORACLE" "MSVC 2008 objects of the pristine decomp $D12" || true
  else
    log "oracle: building the pristine decomp with MSVC 2008 (once per decomp commit)"
    MS=$FARM_HOME/msvc
    if [ -f "$HERE/../equiv/setup_msvc.sh" ]; then
      eval "$(bash "$HERE/../equiv/setup_msvc.sh" "$MS" | grep '^export ')"
    else
      eval "$(setup_msvc_inline "$MS" | grep '^export ')"
    fi
    [ -n "${FNV_MSVC9_ROOT:-}" ] || fail_report 4 "MSVC 2008 setup failed (see tools/equiv/README.md)"
    mkdir -p "$ORACLE.build/obj"
    WINEDEBUG=-all nice $PY "$P/tools/equiv/equiv.py" build "$DECOMP" "$DECOMP" --out "$ORACLE.build/fp.jsonl" \
      --units all --objdir "$ORACLE.build/obj" --jobs "$JOBS" > "$ORACLE.build/build.log" 2>&1
    rm -rf "$ORACLE"; mkdir -p "$ORACLE"
    mv "$ORACLE.build/obj" "$ORACLE/obj"
    printf '{"decomp_commit": "%s", "objects": %s, "built_by": "tools/farm/fuzz_job.sh (equiv.py build pristine)"}\n' \
      "$(git -C "$DECOMP" rev-parse HEAD)" "$(ls "$ORACLE/obj" | grep -c '\.obj$')" > "$ORACLE/meta.json"
    publish "msvc/pristine/$D12" "$ORACLE" "MSVC 2008 objects of the pristine decomp $D12" || true
    rm -rf "$ORACLE.build"
  fi
  touch "$ORACLE/.done"
fi
NX86=$(ls "$ORACLE/obj" | grep -c '\.obj$' || true)

# --- the link's tree; its objects are its fixed/ (the rewritten objects difffuzz links unit by unit) --------------
if [ -n "${FUZZ_TREE:-}" ]; then TREE=$FUZZ_TREE; NOTE_TREE="local tree $TREE"
else
  TREE=$FARM_HOME/tree-$L12
  # The tree the link was made on. t/<L12> exists only when the prepare farm got to L itself; the link job may have
  # taken the rolling `tree` (published for an earlier commit with the same prepare inputs), which has moved on
  # since, so fetch_tree.sh on L alone waits 30 min and fails (link/c0d6c71516f6, 14:04, fz1 and fz25). Candidates,
  # first hit wins: L; the commit the link's meta.json "tree" note says its tree was published for (checked
  # against the tree hash it records); the link's objects commit (its objects were compiled from that tree). Only
  # L is waited for, and only when no candidate exists yet.
  if [ ! -f "$TREE.done" ]; then
    mapfile -t LT < <(python3 -I - "$LINK/meta.json" <<'EOF'
import json, re, sys
try:
    m = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    m = {}
note = m.get('tree') or ''
h = re.search(r'tree hash ([0-9a-f]{8,})', note)
print(h.group(1) if h else '')
c = re.search(r'published for ([0-9a-f]{7,40})', note)
# the objects commit's tree only with a recorded hash to check it against (V1 F2)
for x in (c.group(1) if c else '', (m.get('objects_commit') or '') if h else ''):
    if x:
        print(x)
EOF
)
    want_th=${LT[0]:-}; th=""; TREE_FROM=""
    cands=("$LCOMMIT")
    for x in "${LT[@]:1}"; do
      full=$(git -C "$REPO" rev-parse -q --verify "$x^{commit}" 2>/dev/null) || continue
      [ "$full" = "$LCOMMIT" ] || cands+=("$full")
    done
    : > "$RUN/fetch_tree.log"
    for c in "${cands[@]}"; do
      t=$(ARTIFACTS=$ARTIFACTS REPO=$REPO FARM_HOME=$FARM_HOME bash "$HERE/fetch_tree.sh" "$c" "$TREE" 0 \
          2>> "$RUN/fetch_tree.log") || continue
      if [ -n "$want_th" ] && [ "${t:0:${#want_th}}" != "$want_th" ] && [ "${want_th:0:${#t}}" != "$t" ]; then
        echo "tree of ${c:0:12}: hash $t, the link was made on $want_th" >> "$RUN/fetch_tree.log"; continue
      fi
      th=$t; TREE_FROM=$c; break
    done
    if [ -z "$th" ]; then
      th=$(ARTIFACTS=$ARTIFACTS REPO=$REPO FARM_HOME=$FARM_HOME bash "$HERE/fetch_tree.sh" "$LCOMMIT" "$TREE" \
           "${FUZZ_TREE_WAIT:-1800}" 2>> "$RUN/fetch_tree.log") \
        || fail_report 3 "no prepared tree for the link commit $L12, the tree it records or its objects commit (see fetch_tree.log)"
      # the tree waited for is checked like the candidates (V1 F1: a mismatched t/<L12> was taken here)
      if [ -n "$want_th" ] && [ "${th:0:${#want_th}}" != "$want_th" ] && [ "${want_th:0:${#th}}" != "$th" ]; then
        fail_report 3 "the prepared tree of $L12 has hash $th, the link was made on $want_th (see fetch_tree.log)"
      fi
      TREE_FROM=$LCOMMIT
    fi
    echo "$th ${TREE_FROM:0:12}" > "$TREE.done"
  fi
  read -r th TREE_FROM < "$TREE.done"
  NOTE_TREE="prepare farm tree of ${TREE_FROM:-$L12} (tree hash $th)"
fi

# --- the player's FalloutNV.exe (private branch gamedata/exe, sha256 pinned, lib_job.sh): its RTTI vftables are the
# objects' vptrs on the oracle (tools/difffuzz/vtmodel.py). Read only, never copied into a published branch or a
# report; without it virtual calls are not modelled. FARM_REAL_EXE=0 leaves it out.
[ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
job_gamedata
log "exe: $NOTE_EXE"
export FNVNX_EXE=$GAME_EXE
export FNVNX_DECOMP=$DECOMP FNVNX_X86OBJ=$ORACLE/obj FNVNX_TREE=$TREE FNVNX_A64OBJ=$LINK/fixed FNVNX_DFLINK=$LINK
export FNVNX_DFWORK=$RUN/work FNVNX_DFA64=$RUN/a64 SSE2NEON=$EXT/sse2neon
unset FNVNX_DFPROFILE
mkdir -p "$RUN/work"
# --- the catalog (per decomp commit; fnkeys.py and the audit cache read it too) -------------------------------------
CAT=$FARM_HOME/catalog-$D12.jsonl
CATV=$(sed -n 's/^CATALOG_VERSION = \([0-9]*\)/\1/p' "$DF/catalog.py")
if [ ! -s "$CAT" ] || ! head -1 "$CAT" | grep -q "\"cv\": $CATV[,}]"; then
  log "catalog"
  (cd "$DF" && nice $PY catalog.py --out "$CAT.tmp" > "$RUN/catalog.log" 2>&1) && mv "$CAT.tmp" "$CAT"
fi
# FUZZ_SETUP_ONLY=<file> (tools/farm/audit_job.sh): stop here, with every input in place, and write them as a harness
# profile (FNVNX_DFPROFILE format, KEY=VALUE) plus the python and the link it names; no fuzzing, no report, no push.
if [ -n "${FUZZ_SETUP_ONLY:-}" ]; then
  { for v in FNVNX_EXE FNVNX_DECOMP FNVNX_X86OBJ FNVNX_TREE FNVNX_A64OBJ FNVNX_DFLINK FNVNX_DFWORK FNVNX_DFA64 SSE2NEON; do
      echo "$v=${!v}"; done
    echo "FNVNX_AUDIT_PY=$PY"; echo "FNVNX_AUDIT_LINK=$NOTE_LINK"; echo "FNVNX_AUDIT_SRC=$P"; echo "FNVNX_AUDIT_CATALOG=$CAT"; } > "$FUZZ_SETUP_ONLY"
  exit 0
fi
t1=$(date +%s)

# --- targets: modelled functions with both objects, this shard (the catalog is made above) ---------------------------
(cd "$DF" && $PY - "$CAT" "$K" "$N" "${FUZZ_LIMIT:-0}" "$RUN/targets.jsonl" "$RUN/targets.json" <<'EOF'
import json, sys, zlib
import config, vtmodel
cat, k, n, limit, out, summary = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
# difffuzz-13: indirect calls and jumps are modelled with the player's exe (vptrs from its vftables, vtmodel.py);
# tail jumps and in-function switch tables always were (an intercepted call / the oracle's own relocated table)
vcalls = vtmodel.exe_path() is not None
counts = dict(catalogued=0, shard=0, no_native_object=0, not_modelled=0, not_modelled_unresolved=0,
              not_modelled_indirect=0, indirect=0, targets=0, vtable_model=vcalls)
have = {}
rows = []
for line in open(cat):
    r = json.loads(line)
    if 'error' in r:
        continue
    counts['catalogued'] += 1
    if zlib.crc32(r['source'].encode()) % n != k - 1:     # whole units: each unit image is built by one shard
        continue
    counts['shard'] += 1
    s = r['source']
    if s not in have:
        have[s] = config.a64_object(s) is not None
    if not have[s]:
        counts['no_native_object'] += 1
        continue
    if r['unresolved'] or (r['indirect'] and not vcalls):
        counts['not_modelled'] += 1
        counts['not_modelled_unresolved' if r['unresolved'] else 'not_modelled_indirect'] += 1
        continue
    counts['indirect'] += bool(r['indirect'])
    ncalls = len(r['calls']) + len(r['tails']) + r['indirect']
    rows.append(dict(unit=r['unit'], source=s, address=r['address'], symbol=r['symbol'], size=r['size'],
                     calls=r['calls'], x87=r['x87'], sse=r['sse'], fs=r['fs'],
                     kind='leaf' if ncalls == 0 else 'calls%d' % ncalls))
rows.sort(key=lambda r: r['address'])
if limit:
    rows = rows[:limit]
counts['targets'] = len(rows)
with open(out, 'w') as f:
    for r in rows:
        f.write(json.dumps(r) + '\n')
json.dump(counts, open(summary, 'w'))
print(counts)
EOF
) > "$RUN/targets.log"
log "targets: $(cat "$RUN/targets.json")"

# --- function keys and the verdict cache ---------------------------------------------------------------------------
# A verdict stays valid while the function's key (fnkeys.py: oracle code, native code and relocations of it and of
# what it reaches in its unit and the bridges, harness version) is unchanged. The cache of shard k of N lives on
# branch fuzz/cache/<k>of<N> (this machine is its only writer); a new shard cache is seeded from the caches of any
# other shard count, so changing N keeps the verdicts.
(cd "$DF" && $PY fnkeys.py "$RUN/targets.jsonl" "$CAT" "$RUN/keys.json" > "$RUN/keys.log" 2>&1)
CACHE=$FARM_HOME/fuzzcache-${K}of${N}
CB=fuzz/cache/${K}of${N}
if [ ! -d "$CACHE/.git" ]; then
  rm -rf "$CACHE"; mkdir -p "$CACHE"
  git -C "$CACHE" init -q
  git -C "$CACHE" remote add origin "$ARTIFACTS_URL"
  if git -C "$CACHE" fetch -q origin "+refs/heads/$CB:refs/remotes/origin/$CB" 2>/dev/null; then
    git -C "$CACHE" checkout -q -B cache "origin/$CB"
    log "cache: $CB fetched"
  else
    git -C "$CACHE" checkout -q -b cache
    others=()
    for b in $(git ls-remote "$ARTIFACTS_URL" 'refs/heads/fuzz/cache/*' 2>/dev/null | awk '{print $2}' \
               | sed 's|^refs/heads/||'); do
      d=$FARM_HOME/cache-seed/${b//\//_}
      if fetch_branch "$b" "$d"; then others+=("$d"); fi
    done
    if [ ${#others[@]} -gt 0 ]; then
      (cd "$DF" && $PY fuzzcache.py seed "$CACHE" "$RUN/targets.jsonl" "${others[@]}") > "$RUN/seed.log"
    fi
    log "cache: $CB is new, seeded from ${#others[@]} other shard caches"
  fi
fi
cache_commit() {  # cache_commit <message>
  git -C "$CACHE" add -A
  git -C "$CACHE" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$1" >/dev/null || true
  [ "$PUBLISH" = 1 ] || return 0
  for i in 1 2 3; do
    git -C "$CACHE" push -q -f origin "HEAD:refs/heads/$CB" 2>/dev/null && return 0
    sleep $((i * 10))
  done
  echo "fuzz_job: push of $CB failed (kept locally, pushed with the next chunk)" >&2
}
(cd "$DF" && $PY fuzzcache.py todo "$CACHE" "$RUN/targets.jsonl" "$RUN/keys.json" "$RUN/todo.jsonl" \
  "$RUN/todo.json") > "$RUN/todo.log"
log "cache: $(cat "$RUN/todo.json")"

# --- campaign over the changed and new functions, in chunks (cache merged and pushed after each) ----------------
CHUNK=${FUZZ_CHUNK:-200}; BUDGET=${FUZZ_BUDGET:-0}
FUZZ_END=$(( t0 + ${FUZZ_MAX_S:-5400} ))
if [ -n "${FARM_DEADLINE:-}" ] && [ "$FARM_DEADLINE" -lt "$FUZZ_END" ]; then FUZZ_END=$FARM_DEADLINE; fi
FUZZ_END=$(( FUZZ_END - ${FUZZ_REPORT_S:-900} ))
log "fuzzing stops at $(date -u -d "@$FUZZ_END" +%T) UTC (report deadline)"
mkdir -p "$RUN/camp"; touch "$RUN/camp/results.jsonl"
(cd "$DF" && $PY fuzzcache.py dropcrash "$RUN/camp") >> "$RUN/merge.log"
rm -f "$RUN"/chunk.* "$RUN/stop.txt"
split -l "$CHUNK" -d -a 4 "$RUN/todo.jsonl" "$RUN/chunk."
HEAD_BRANCH=${FUZZ_HEAD_BRANCH:-claude/port-nsw-un6t9s}
# HARNESS (a full refresh) and SCOPED (the newest scoped change, fnkeys.CHANGES): either one differing on the
# integration head makes this run's verdicts stale for some functions
harness_of() { sed -n "s/^\(HARNESS\|SCOPED\) = '\(.*\)'/\2/p" | paste -sd+; }
MY_HARNESS=$(harness_of < "$DF/fnkeys.py"); export MY_HARNESS
for c in "$RUN"/chunk.*; do
  [ -s "$c" ] || continue
  nf=$(wc -l < "$c")
  # watchdog: a harness hang costs this chunk, not the shard. Per function at most TIME s of fuzzing plus setup,
  # minimisation and classification (~3 x TIME + 30 s), JOBS in parallel, x2 margin.
  wd=$(( (nf * (3 * ${TIME%.*} + 30) / JOBS + 1) * 2 + 120 ))
  left=$(( FUZZ_END - $(date +%s) ))
  if [ "$left" -lt 60 ]; then
    log "report deadline: $(basename "$c") and later chunks stay pending"; echo deadline > "$RUN/stop.txt"; break
  fi
  capped=0; if [ "$left" -lt "$wd" ]; then wd=$left; capped=1; fi
  log "fuzz $(basename "$c"): $nf functions ($JOBS jobs, $ITERS executions / ${TIME}s per function, watchdog ${wd}s)"
  rc=0
  (cd "$DF" && timeout -k 30 "$wd" nice $PY difffuzz.py fuzz "$c" --out "$RUN/camp" --iters "$ITERS" \
    --time "$TIME" --jobs "$JOBS" --resume >> "$RUN/fuzz.log" 2>&1) || rc=$?
  if { [ $rc -eq 124 ] || [ $rc -eq 137 ]; } && [ $capped = 1 ]; then
    # stopped by the report deadline, not hung: the unfinished functions stay pending (the next run fuzzes them)
    log "report deadline reached during $(basename "$c")"
    (cd "$DF" && $PY fuzzcache.py merge "$CACHE" "$RUN/camp" "$RUN/keys.json" "$COMMIT") >> "$RUN/merge.log"
    cache_commit "$C12 shard $K/$N: $(basename "$c") (part)"
    echo deadline > "$RUN/stop.txt"
    break
  fi
  if [ $rc -eq 124 ] || [ $rc -eq 137 ]; then
    # the chunk's unfinished functions become harness failures (run:timeout): fuzzed again by the next run
    log "watchdog fired after ${wd}s on $(basename "$c")"
    (cd "$DF" && $PY fuzzcache.py timeouts "$RUN/camp" "$c") >> "$RUN/merge.log"
  fi
  (cd "$DF" && $PY fuzzcache.py merge "$CACHE" "$RUN/camp" "$RUN/keys.json" "$COMMIT") >> "$RUN/merge.log"
  cache_commit "$C12 shard $K/$N: $(basename "$c")"
  if [ "$BUDGET" -gt 0 ] && [ $(($(date +%s) - t1)) -ge "$BUDGET" ]; then log "time budget reached"; echo budget > "$RUN/stop.txt"; break; fi
  # a newer harness on the integration head (HARNESS bumped) makes these verdicts stale: stop after this chunk
  # and report, so the runner starts again at the newest head instead of finishing a pass that is thrown away
  if git -C "$REPO" fetch -q origin "$HEAD_BRANCH" 2>/dev/null; then
    new_h=$(git -C "$REPO" show "FETCH_HEAD:ports/fnv-nx/tools/difffuzz/fnkeys.py" 2>/dev/null | harness_of)
    if [ -n "$new_h" ] && [ "$new_h" != "$MY_HARNESS" ]; then
      log "integration head has harness $new_h (this run: $MY_HARNESS): stopping after $(basename "$c")"
      echo newer-harness > "$RUN/stop.txt"; break
    fi
  fi
done
cache_commit "$C12 shard $K/$N"
t2=$(date +%s)

# --- attribution, verdicts of this head and cumulative, report -----------------------------------------------------
ATT=$FARM_HOME/attrib-$L12          # stream attribution, once per link (every shard on this machine shares it)
mkdir -p "$ATT"
RSTEPS=""; rs_t=$(date +%s)   # T1: seconds per report step (the report took 1,324 s of qk1's fuzz on aec30808)
rstep() { local now; now=$(date +%s); RSTEPS="$RSTEPS${RSTEPS:+, }$1 $((now - rs_t))s"; rs_t=$now; }
if [ ! -s "$ATT/rules.json" ] || [ ! -s "$ATT/bridges.json" ]; then
  log "attribution (prepare rules, bridges)"
  # FNVNX_ATTRIB_CACHE: each unit's attribution kept for the next link (its two texts unchanged: the same result)
  (cd "$DF" && FNVNX_ATTRIB_CACHE=$FARM_HOME/attrib-cache/rules.marshal nice $PY attrib.py rules "$ATT/rules.json.tmp" > "$ATT/attrib.log" 2>&1 \
    && nice $PY attrib.py bridges "$ATT/bridges.json.tmp" >> "$ATT/attrib.log" 2>&1 \
    && mv "$ATT/rules.json.tmp" "$ATT/rules.json" && mv "$ATT/bridges.json.tmp" "$ATT/bridges.json") || true
fi
rstep attribution
(cd "$DF" && $PY fuzzcache.py view "$CACHE" "$RUN/targets.jsonl" "$RUN/keys.json" "$RUN/cum" \
  && $PY fuzzcache.py status "$CACHE" "$RUN/targets.jsonl" "$RUN/keys.json" "$COMMIT" "$RUN/status.json") \
  > "$RUN/view.log"
rstep view+status
for v in new cum; do
  d=$RUN/camp; [ $v = cum ] && d=$RUN/cum
  (cd "$DF" && $PY report.py "$d" --rules "$ATT/rules.json" --bridges "$ATT/bridges.json" \
    --out "$RUN/verdicts_$v" --hard-tsv "$RUN/hard_$v.tsv" > "$RUN/report_$v.log" 2>&1)
  rstep "report.py $v"
done
# per-cause verdict classes of the cumulative campaign (S13b causes.py, fnvnx-causes/1): embedded in the report
(cd "$DF" && $PY causes.py summary "$RUN/cum" --out "$RUN/causes_cum.json" > "$RUN/causes.log" 2>&1) || true
rstep causes
log "report steps: $RSTEPS"; echo "$RSTEPS" > "$RUN/report_steps.txt"
t3=$(date +%s)

$PY - "$RUN" "$REPORT" "$COMMIT" "$D12" "$K" "$N" "$NX86" "$ITERS" "$TIME" "$JOBS" "$((t1-t0))" "$((t2-t1))" \
  "$((t3-t2))" "$NOTE_LINK" "$NOTE_TREE" "$NOTE_EXE" <<'EOF'
import collections, json, os, sys
run, report, commit, d12, k, n, nx86, iters, tm, jobs, ts, tf, tr, note_link, note_tree, note_exe = sys.argv[1:]
counts = json.load(open(os.path.join(run, 'targets.json')))
st = json.load(open(os.path.join(run, 'status.json')))


def rows(d):
    p = os.path.join(run, d, 'results.jsonl')
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []


def table(v):
    md = open(os.path.join(run, 'verdicts_%s.md' % v)).read().split('\n## ')[0].strip().split('\n')
    return md[2:]


new, cum = rows('camp'), rows('cum')
fresh = {r['address'] for r in new}
status = collections.Counter(r.get('status', '?').split(':')[0] for r in new)
crashes = collections.Counter(r['status'] for r in new if str(r.get('status', '')).startswith('crash:'))
ncrash = sum(crashes.values())
broken = len(new) >= 10 and ncrash * 2 >= len(new)
banner = []
if broken:
    top, nt = crashes.most_common(1)[0]
    msg = next((r.get('error', '') for r in new if r.get('status') == top), '').strip().splitlines()
    banner = ['', '**HARNESS BROKEN: %d of %d functions fuzzed this head ended in a harness exception (%s); the '
              'verdicts below are not meaningful.** Last line: `%s`' % (ncrash, len(new), ', '.join(
                  '%s %d' % kv for kv in crashes.most_common(3)), (msg[-1] if msg else '')[:160])]
execs = sum(r.get('execs', 0) for r in new)
hard = [l.rstrip('\n').split('\t') for l in open(os.path.join(run, 'hard_cum.tsv'))] \
    if os.path.exists(os.path.join(run, 'hard_cum.tsv')) else []
out = ['# fuzz %s shard %s/%s%s' % (commit[:12], k, n, ' - HARNESS BROKEN' if broken else '')] + banner + ['',
       '- commit %s, decomp %s, oracle: %s MSVC 2008 objects of the pristine decomp' % (commit, d12, nx86),
       '- link: %s; tree: %s; exe: %s' % (note_link, note_tree, note_exe),
       '- functions catalogued %d; in this shard %d: no native object %d, control flow not modelled (unresolved '
       'relocations, indirect calls without the player\'s exe) %d, fuzzable %d (no native definition in the link: %d)' % (
           counts['catalogued'], counts['shard'], counts['no_native_object'], counts['not_modelled'],
           counts['targets'], st['no_key']),
       '- this head: fuzzed %d, verdict reused from an earlier head (key unchanged) %d, pending %d (key changed %d, '
       'never fuzzed %d), harness exception %d (fuzzed again by the next run)' % (
           st['fuzzed_this_head'], st['reused'], st['pending_changed'] + st['pending_new'], st['pending_changed'],
           st['pending_new'], st.get('crashed', 0)),
       '- fuzzed this head: %s; executions %d (at most %s and %ss per function, %s jobs)' % (
           ', '.join('%s %d' % kv for kv in status.most_common()) or 'none', execs, iters, tm, jobs),
       '- cumulative (verdicts valid for this head): %d functions' % len(cum),
       '- time: inputs %ss, fuzz %ss, report %ss%s' % (ts, tf, tr, (' (%s)' % open(os.path.join(run, 'report_steps.txt')).read().strip())
                                                      if os.path.isfile(os.path.join(run, 'report_steps.txt')) else ''),
       '- reproducers: branch fuzz/cache/%sof%s (all), fuzz/%s/%sof%s (this head) of the artifacts repository' % (
           k, n, commit[:12], k, n), '']
out += ['## Verdicts, new this head', ''] + table('new') + ['']
out += ['## Verdicts, cumulative', ''] + table('cum') + ['']
out += ['## Hard divergences, cumulative (%d in %d functions; * = fuzzed this head)' % (
    len(hard), len({row[0] for row in hard})), '', '| address | cause | stream | kind | unit |', '|---|---|---|---|---|']
out += ['| %s%s | %s |' % (row[0], '*' if row[0] in fresh else '', ' | '.join(row[1:])) for row in hard]
cp = os.path.join(run, 'causes_cum.json')
if os.path.exists(cp):
    cs = json.load(open(cp))
    out += ['', '## Causes by verdict class, cumulative (fnvnx-causes/1, tools/difffuzz/causes.py)', '',
            'functions by worst class: ' + ', '.join('%s %d' % kv for kv in cs['functions_by_class'].items()), '',
            '| cause | class | owner | divergences | functions | worst of |', '|---|---|---|---:|---:|---:|']
    out += ['| %s | %s | %s | %d | %d | %d |' % (c, r['verdict'], r['owner'], r['divergences'], r['functions'],
                                               r['worst_of']) for c, r in cs['causes'].items()]
    slim = dict(format=cs['format'], functions_ok=cs['functions_ok'], functions_diverging=cs['functions_diverging'],
                functions_by_class=cs['functions_by_class'],
                causes={c: {k_: r[k_] for k_ in ('verdict', 'owner', 'hard', 'divergences', 'functions', 'worst_of',
                                                  'kinds')} for c, r in cs['causes'].items()})
    out += ['', '```json fnvnx-causes', json.dumps(slim, sort_keys=True), '```']
# the machine-readable summary for the R0 gates (fnvnx-fuzz-summary/1): <report>.json beside the report (runner.sh
# publishes both) and the same object in the report's last block
by = collections.Counter(r_[1] for r_ in hard)
allrow = json.load(open(os.path.join(run, 'verdicts_cum.json'))).get('ALL', {}) \
    if os.path.exists(os.path.join(run, 'verdicts_cum.json')) else {}
with_obj = counts['shard'] - counts['no_native_object']
stop = open(os.path.join(run, 'stop.txt')).read().strip() if os.path.exists(os.path.join(run, 'stop.txt')) else ''
pending = st['pending_changed'] + st['pending_new']
summary = dict(
    format='fnvnx-fuzz-summary/1', commit=commit, decomp=d12, shard=int(k), shards=int(n), link=note_link,
    exe=bool(counts.get('vtable_model')), harness=os.environ.get('MY_HARNESS', ''),
    status='harness-broken' if broken else ('partial' if pending or stop else 'complete'), stopped=stop or None,
    functions=dict(catalogued=counts['catalogued'], shard=counts['shard'], no_native_object=counts['no_native_object'],
                   with_native_object=with_obj, not_modelled=counts['not_modelled'],
                   not_modelled_unresolved=counts.get('not_modelled_unresolved'),
                   not_modelled_indirect=counts.get('not_modelled_indirect'), targets=counts['targets'],
                   no_native_definition=st['no_key'], verdicts=len(cum), fuzzed_this_head=st['fuzzed_this_head'],
                   reused=st['reused'], pending=pending, harness_exception=st.get('crashed', 0)),
    coverage_pct=round(100.0 * len(cum) / with_obj, 2) if with_obj else None,
    targets_pct=round(100.0 * counts['targets'] / with_obj, 2) if with_obj else None,
    hard=dict(divergences=len(hard), functions=len({r_[0] for r_ in hard}), by_cause=dict(by.most_common())),
    verdicts=dict(ok=allrow.get('functions', 0), hard=allrow.get('hard', 0), soft=allrow.get('soft', 0),
                  oracle_gap=allrow.get('harness', 0), not_set_up=allrow.get('setup', 0)),
    divergences_by_cause=dict(collections.Counter(allrow.get('causes', {})).most_common()),
    executions_this_head=execs)
out += ['', '```json fnvnx-fuzz-summary', json.dumps(summary, sort_keys=True), '```']
open(report, 'w').write('\n'.join(out) + '\n')
json.dump(summary, open(os.path.splitext(report)[0] + '.json', 'w'), indent=1, sort_keys=True)
print('\n'.join(out[:10]))
EOF

# --- this head's reproducers ---------------------------------------------------------------------------------------
if [ -s "$RUN/camp/results.jsonl" ]; then
  PUB=$RUN/pub; rm -rf "$PUB"; mkdir -p "$PUB"
  cp "$RUN/camp/results.jsonl" "$RUN/targets.json" "$RUN/todo.json" "$PUB/"
  [ -d "$RUN/camp/div" ] && cp -a "$RUN/camp/div" "$PUB/div"
  cp "$RUN/hard_new.tsv" "$PUB/" 2>/dev/null || true
  cp "$RUN/causes_cum.json" "$PUB/" 2>/dev/null || true
  cp "${REPORT%.md}.json" "$PUB/summary.json" 2>/dev/null || true
  printf '{"commit": "%s", "decomp": "%s", "shard": %s, "shards": %s, "iters": %s, "time": %s}\n' \
    "$COMMIT" "$D12" "$K" "$N" "$ITERS" "$TIME" > "$PUB/meta.json"
  publish "fuzz/$C12/${K}of${N}" "$PUB" "$C12 fuzz shard $K/$N" || true
  rm -rf "$PUB"
fi
# housekeeping: the newest two heads' run directories, links and trees stay
for pat in "fuzz-*-${K}of${N}" "link-*" "tree-*"; do
  ls -1dt "$FARM_HOME"/$pat 2>/dev/null | grep -v '\.done$' | tail -n +3 | while read -r d; do rm -rf "$d" "$d.done"; done
done
log "done: $REPORT"
