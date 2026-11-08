#!/usr/bin/env bash
# static_job.sh - farm job: the static gate (docs/STATIC_CHECKS.md) on one shard of the prepared tree of one commit.
#
#   tools/farm/static_job.sh <commit> <k> <N> <report.md>
#
# Shard k of N (1 <= k <= N): a unit belongs to shard (crc32(path) mod N) + 1, as in compile_job.sh, so the
# assignment does not move between commits. All gate steps run (tools/static/run.sh): GCC -O2 with the build's
# flags, clang gnu (AST checks, layouts, flow warnings), clang msvc (clang's MSVC x86 layout engine; no Microsoft
# tool is involved), atomic, layout diff, probe, report. SKIP="msvc layout probe" leaves out the layout steps.
# Atomic keys come from the shard's own Interlocked uses, so a cross-shard plain store is missed.
#
# The tree comes from the prepare farm (tools/farm/fetch_tree.sh, waiting up to TREE_WAIT seconds, default 300);
# when that fails the job prepares it itself (tools/prepare.py, ~6 min). Once per commit under $FARM_HOME.
#
# Writes <report.md>: shard, tree source, toolchain, timings and coverage counts, the per-class site counts, and
# every site as `file line CLASS` (a site in a header counts once per shard). Next to it <report>.tsv (class, file,
# line), so the shards of one commit merge with `cat *.tsv | sort -u`. No detail text, no prepared source. When GCC
# compiles no unit or the gate fails, a Diagnostics section holds the command line of one unit, the first errors
# of its log and the tail of the gate's step log (compiler messages only, no caret lines).
#
# Work trees live under $FARM_HOME (default ~/fnvnx-farm): the port at that commit (src-<commit>), the pinned
# third-party checkouts (ext, tools/fetch.sh), the tree and the gate output (static-<commit>). JOBS (default nproc)
# parallel compiles; every compiler runs under nice (NICE, default 19).
# Needs: git, python3, network; ARTIFACTS (a clone of toadharvard/fnv-nx-build, as runner.sh exports) for the tree.
# Every file the gate needs is checked with the Ubuntu 24.04 package that ships it (REQ below); a missing file's
# package is installed (apt, sudo when not root), or reinstalled when dpkg says it is installed. llvm-18-dev is one
# of them on its own: clang-18 pulls in llvm-18 (llvm-config) and libclang-cpp18 but not llvm-18-dev, whose headers
# (llvm/Frontend/OpenMP/OMP.inc) and libLLVM.so link fnvcheck needs (st1-4 at 7d1efb5: fnvcheck did not build, 0
# sites). Never g++-multilib: on Ubuntu it conflicts with the AArch64 cross compiler, and apt removes
# g++-aarch64-linux-gnu to install it (the first st1-4 runs compiled 0 units that way).
#
# Fails loudly (a FAILED report, its head on stderr for the runner's log, exit 2 or 3) when a tool is still missing,
# fnvcheck does not build, GCC compiles no unit, clang gnu writes no rows, or a gate step fails. A class above
# tools/static/baseline.tsv is a result (exit 0).
#
# Overrides for a machine that already has the inputs (e.g. a light test on a development machine, where a
# full-tree prepare is not allowed): FARM_SRC=<checkout at <commit>> (no worktree), FARM_EXT=<dir> (used as it is,
# no fetch), STATIC_TREE=<prepared tree> (read-only), SHIM=<dir> (run.sh: the shim of the tree's commit),
# STATIC_PREPARE=0 (fail instead of preparing when fetch_tree.sh has no tree).
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
[ "$K" -ge 1 ] && [ "$K" -le "$N" ] || { echo "static_job.sh: need 1 <= k <= N" >&2; exit 2; }
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
C12=${COMMIT:0:12}
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
WORK=$FARM_HOME/static-$C12
mkdir -p "$WORK"
san() { sed -e "s|$FARM_HOME|\$FARM_HOME|g" -e "s|$HOME|~|g"; }    # paths of this machine, not of the job

fail() {  # fail <exit code> <file with the report body>: FAILED report, its head on stderr, exit
  { echo "# static gate ${C12}, shard $K of $N FAILED"; echo; echo "- commit: \`$COMMIT\`"; cat "$2"; } > "$REPORT"
  head -40 "$REPORT" >&2
  echo "$REPORT"; exit "$1"
}

# 1. Tools: every file the gate needs, with the package that ships it on Ubuntu 24.04.
LLVM=${LLVM:-/usr/lib/llvm-18}; export LLVM
REQ=(/usr/bin/clang++-18:clang-18 "$LLVM/include/clang/Tooling/Tooling.h:libclang-18-dev"
     "$LLVM/include/llvm/Frontend/OpenMP/OMP.inc:llvm-18-dev" "$LLVM/lib/libLLVM.so:llvm-18-dev"
     "$LLVM/bin/llvm-config:llvm-18" "$LLVM/lib/libclang-cpp.so.18.1:libclang-cpp18"
     /usr/include/x86_64-linux-gnu/c++/13/32:lib32stdc++-13-dev /usr/include/x86_64-linux-gnu/gnu/stubs-32.h:libc6-dev-i386
     /usr/bin/aarch64-linux-gnu-g++:g++-aarch64-linux-gnu /usr/bin/g++:g++ /usr/bin/jq:jq /usr/bin/rsync:rsync
     /usr/lib/gcc-cross/aarch64-linux-gnu/13/plugin/include/gcc-plugin.h:gcc-13-plugin-dev-aarch64-linux-gnu)
check_req() { missing=(); for r in "${REQ[@]}"; do [ -e "${r%:*}" ] || missing+=("$r"); done; }
installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }
check_req
if [ ${#missing[@]} -gt 0 ]; then
  new=(); again=()
  for p in $(printf '%s\n' "${missing[@]}" | sed 's/.*://' | sort -u); do
    if installed "$p"; then again+=("$p"); else new+=("$p"); fi
  done
  SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo
  APT=${APT:-apt-get}    # APT=true: a test that must not install anything
  { $SUDO $APT update -q
    [ ${#new[@]} -eq 0 ] || DEBIAN_FRONTEND=noninteractive $SUDO $APT install -y -q --no-install-recommends "${new[@]}"
    [ ${#again[@]} -eq 0 ] || DEBIAN_FRONTEND=noninteractive $SUDO $APT install -y -q --no-install-recommends \
                                --reinstall "${again[@]}"; } > "$WORK/apt.log" 2>&1 || true
  check_req
  if [ ${#missing[@]} -gt 0 ]; then
    { echo "- missing after apt (file:package): ${missing[*]}"; echo "- installed: ${new[*]:-nothing}; reinstalled: ${again[*]:-nothing}"
      echo; echo '```'; { grep -iE "remov|conflict|error|unable|^E:" "$WORK/apt.log" || true; } | tail -15; echo '```'; } > "$WORK/fail.md"
    fail 2 "$WORK/fail.md"
  fi
fi

# 2. The port's tools at that commit, as a detached worktree outside the main checkout.
SRC=${FARM_SRC:-$FARM_HOME/src-$C12}
if [ -z "${FARM_SRC:-}" ]; then bash "$(dirname "$0")/worktree.sh" "$REPO" "$SRC" "$COMMIT" >/dev/null; fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$(git -C "$SRC" rev-parse "$COMMIT^{commit}")" ]; then
  echo "static_job.sh: $SRC is not at $COMMIT" >&2; exit 2
fi
P=$SRC/ports/fnv-nx

# 3. Pinned third-party checkouts.
EXT=${FARM_EXT:-$FARM_HOME/ext}
[ -n "${FARM_EXT:-}" ] || bash "$P/tools/fetch.sh" "$EXT" >/dev/null

# 4. The tree (once per commit): from the prepare farm, else prepared here.
t0=$(date +%s)
if [ -n "${STATIC_TREE:-}" ]; then
  TREE=$(cd "$STATIC_TREE" && pwd); TREE_FROM="given (STATIC_TREE)"
else
  TREE=$WORK/tree
  if [ ! -f "$WORK/tree.done" ]; then
    rm -rf "$TREE"
    if bash "$P/tools/farm/fetch_tree.sh" "$COMMIT" "$TREE" "${TREE_WAIT:-300}" > "$WORK/fetch_tree.log" 2>&1; then
      echo "fetch_tree.sh (tree hash $(tail -1 "$WORK/fetch_tree.log"))" > "$WORK/tree.from"
    else
      why=$(tail -1 "$WORK/fetch_tree.log" | san | cut -c1-160)
      rm -rf "$TREE"
      [ "${STATIC_PREPARE:-1}" = 1 ] || { echo "static_job.sh: no tree from fetch_tree.sh ($why), STATIC_PREPARE=0" >&2; exit 2; }
      python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$TREE" > "$WORK/prepare.log" 2>&1
      echo "prepare.py (fetch_tree.sh: $why)" > "$WORK/tree.from"
    fi
    touch "$WORK/tree.done"
  fi
  TREE_FROM=$(cat "$WORK/tree.from" 2>/dev/null || echo "earlier run")
fi
t1=$(date +%s)
LIST=$WORK/shard-$K-of-$N.list
jq -r '.units[].source' "$TREE/build/units.json" | sort -u | python3 -c '
import sys, zlib
k, n = int(sys.argv[1]), int(sys.argv[2])
for line in sys.stdin:
    u = line.rstrip("\n")
    if zlib.crc32(u.encode()) % n == k - 1: print(u)' "$K" "$N" > "$LIST"

# 5. Toolchain check: an empty unit through the gate's GCC command (build flags, shim, sse2neon).
SHIM_DIR=${SHIM:-$P/shim/include}
# shellcheck source=../static/flags.sh
. "$P/tools/static/flags.sh"
printf 'int fnvnx_static_probe;\n' > "$WORK/probe.cpp"
if aarch64-linux-gnu-g++ "${BUILD_FLAGS[@]}" -c -O2 -o /dev/null -include "$SHIM_DIR/fnvnx_compat.h" -I"$SHIM_DIR" \
     -I"$EXT/sse2neon" -I"$TREE/include" "$WORK/probe.cpp" > "$WORK/probe.log" 2>&1; then TC=ok
else TC="FAILED: $( { grep -m1 -E 'error|fatal' "$WORK/probe.log" || tail -1 "$WORK/probe.log"; } | san | cut -c1-200)"; fi

# 6. fnvcheck (the clang passes' checker), built before the gate so a failure is named, not counted as 0 sites.
OUT=$WORK/out-$K-of-$N
GLOG=$WORK/gate-$K-of-$N.log
mkdir -p "$OUT"
brc=0; bash "$P/tools/static/build.sh" "$OUT" > "$WORK/fnvcheck-build.log" 2>&1 || brc=$?
if [ "$brc" -ne 0 ]; then
  { echo "- fnvcheck does not build (tools/static/build.sh exit $brc), so the clang passes cannot run"
    echo "- llvm: $("$LLVM/bin/llvm-config" --version 2>&1 | head -1), shared mode: $("$LLVM/bin/llvm-config" --shared-mode 2>&1 | head -1)"
    echo; echo '```'; { grep -m8 -E 'missing|error|fatal|cannot|No such' "$WORK/fnvcheck-build.log" || true
                        tail -5 "$WORK/fnvcheck-build.log"; } | san | cut -c1-240; echo '```'; } > "$WORK/fail.md"
  fail 2 "$WORK/fail.md"
fi

# 7. The gate on the shard.
rc=0
UNITS=$LIST SSE2NEON=$EXT/sse2neon NICE=${NICE:-19} bash "$P/tools/static/run.sh" "$TREE" "$OUT" "$JOBS" > "$GLOG" 2>&1 || rc=$?
t2=$(date +%s)
compiled=$( (awk -F'\t' '$2=="0"' "$OUT/gcc/status.tsv" 2>/dev/null || true) | wc -l)
gnu_rows=$( (find "$OUT/clang/gnu/rows" -name '*.tsv' -size +0 2>/dev/null || true) | wc -l)
problems=()
case " ${SKIP:-} " in *" gcc "*) ;; *) [ "$compiled" -gt 0 ] || problems+=("GCC compiled no unit") ;; esac
case " ${SKIP:-} " in *" gnu "*) ;; *) [ "$gnu_rows" -gt 0 ] || problems+=("clang gnu wrote no rows") ;; esac
if [ "$rc" -ne 0 ] && ! grep -q '^gate: FAIL' "$GLOG"; then problems+=("a gate step failed (exit $rc)"); fi

# 8. Report: counts and sites (file, line, class) only; diagnostics when something failed.
TSV=${REPORT%.md}.tsv
: > "$TSV"
for f in "$OUT"/sites/*.tsv; do
  [ -f "$f" ] || continue
  cls=$(basename "$f" .tsv)
  tail -n +2 "$f" | awk -F'\t' -v c="$cls" '{print c"\t"$1"\t"$2}' >> "$TSV"
done
sort -u -o "$TSV" "$TSV"
{
  if [ ${#problems[@]} -gt 0 ]; then echo "# static gate ${C12}, shard $K of $N FAILED"; echo
    echo "- **failed**: ${problems[*]}"; else echo "# static gate ${C12}, shard $K of $N"; echo; fi
  echo "- commit: \`$COMMIT\`"
  echo "- units in the shard: $(wc -l < "$LIST"); GCC compiled: $compiled"
  echo "- tree: $TREE_FROM, $((t1-t0)) s; gate: $((t2-t1)) s; jobs: $JOBS, host: $(nproc) CPUs"
  echo "- toolchain: $(aarch64-linux-gnu-g++ --version | head -1); $(clang++-18 --version | head -1); check: $TC"
  echo "- gate exit code: $rc (0 ok, 1 a class above tools/static/baseline.tsv or a failed step, 2 the gate could not run)"
  echo "- clang gnu: $gnu_rows units with rows; fnvcheck: $(head -1 "$WORK/fnvcheck-build.log" 2>/dev/null | san | cut -c1-120)"
  echo "- steps:"
  echo; echo '```'
  grep -E '^(gcc|clang (gnu|msvc|atomic)|layouts|probe|report|gate|run\.sh)[: ]' "$GLOG" | san || true
  echo '```'
  if [ ${#problems[@]} -gt 0 ] || [ "$rc" != 0 ]; then
    echo; echo "## Diagnostics"; echo
    if [ -f "$OUT/gcc/command.txt" ]; then
      echo "Command line of one unit:"; echo; echo '```'; san < "$OUT/gcc/command.txt" | cut -c1-2000; echo '```'
      u=$(head -1 "$LIST"); ul=$OUT/gcc/logs/$(echo "$u" | tr '/ ' '__').log
      if [ -f "$ul" ]; then
        echo; echo "First errors of \`$u\`:"; echo; echo '```'
        { grep -m5 -E 'error|fatal|not found|No such file|cannot' "$ul" || head -5 "$ul"; tail -1 "$ul"; } | san | cut -c1-240
        echo '```'
      fi
    fi
    echo; echo "Tail of the gate's step log:"; echo; echo '```'; tail -25 "$GLOG" | san | cut -c1-240; echo '```'
  fi
  if [ -f "$OUT/report.md" ]; then
    echo; echo "## Coverage"; echo
    sed -n '/^## Coverage/,/^## Summary/p' "$OUT/report.md" | grep '^\* ' | grep -v '^\* \*\*sample' || true
  fi
  echo; echo "## Sites per class"; echo
  echo "| class | sites |"; echo "|---|---:|"
  cut -f1 "$TSV" | sort | uniq -c | sort -k2 | awk '{printf "| %s | %d |\n", $2, $1}'
  echo; echo "## Sites (file line class)"; echo
  echo '```'
  sort -t$'\t' -k2,2 -k3,3n -k1,1 "$TSV" | awk -F'\t' '{print $2" "$3" "$1}'
  echo '```'
} > "$REPORT"
if [ ${#problems[@]} -gt 0 ]; then
  head -60 "$REPORT" >&2     # the runner replaces the report of a failed job with its log's tail
  echo "$REPORT"; exit 3
fi
echo "$REPORT"     # a class above the baseline (gate exit 1 with "gate: FAIL") is a result, not a job failure
