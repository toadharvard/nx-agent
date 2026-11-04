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
# Installs (apt, sudo when not root) whatever is missing of: clang-18, libclang-18-dev, llvm-18-dev,
# lib32stdc++-13-dev and libc6-dev-i386 (32-bit headers of the clang passes), g++-aarch64-linux-gnu, g++, jq, rsync.
# Never g++-multilib: on Ubuntu it conflicts with the AArch64 cross compiler, and apt removes
# g++-aarch64-linux-gnu to install it (the first st1-4 runs compiled 0 units that way).
#
# Overrides for a machine that already has the inputs (e.g. a light test on a development machine, where a
# full-tree prepare is not allowed): FARM_SRC=<checkout at <commit>> (no worktree), FARM_EXT=<dir> (used as it is,
# no fetch), STATIC_TREE=<prepared tree> (read-only), SHIM=<dir> (run.sh: the shim of the tree's commit).
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

# 1. Tools: clang/LLVM development packages for fnvcheck, the AArch64 cross compiler, 32-bit libstdc++/glibc headers.
need=()
command -v clang++-18 >/dev/null || need+=(clang-18)
[ -f /usr/lib/llvm-18/include/clang/Tooling/Tooling.h ] || need+=(libclang-18-dev)
[ -x /usr/lib/llvm-18/bin/llvm-config ] && ls /usr/lib/llvm-18/lib/libclang-cpp.so* >/dev/null 2>&1 || need+=(llvm-18-dev)
[ -d /usr/include/x86_64-linux-gnu/c++/13/32 ] || need+=(lib32stdc++-13-dev)
[ -f /usr/include/x86_64-linux-gnu/gnu/stubs-32.h ] || need+=(libc6-dev-i386)
command -v aarch64-linux-gnu-g++ >/dev/null || need+=(g++-aarch64-linux-gnu)
command -v g++ >/dev/null || need+=(g++)
command -v jq >/dev/null || need+=(jq)
command -v rsync >/dev/null || need+=(rsync)
if [ ${#need[@]} -gt 0 ]; then
  SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo
  $SUDO apt-get update -q > "$WORK/apt.log" 2>&1
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q --no-install-recommends "${need[@]}" >> "$WORK/apt.log" 2>&1
fi
missing=()
for t in clang++-18 aarch64-linux-gnu-g++ g++ jq rsync; do command -v "$t" >/dev/null || missing+=("$t"); done
[ -x /usr/lib/llvm-18/bin/llvm-config ] || missing+=(llvm-config-18)
if [ ${#missing[@]} -gt 0 ]; then
  { echo "# static gate ${C12}, shard $K of $N FAILED"; echo; echo "- missing after apt: ${missing[*]}"
    echo "- installed: ${need[*]:-nothing}"; echo; echo '```'; grep -iE "remov|conflict|error|unable" "$WORK/apt.log" | tail -15
    echo '```'; } > "$REPORT"
  echo "$REPORT"; exit 2
fi

# 2. The port's tools at that commit, as a detached worktree outside the main checkout.
SRC=${FARM_SRC:-$FARM_HOME/src-$C12}
if [ -z "${FARM_SRC:-}" ] && [ ! -d "$SRC" ]; then git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null; fi
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
else TC="FAILED: $(grep -m1 -E 'error|fatal' "$WORK/probe.log" | san | cut -c1-200)"; fi

# 6. The gate on the shard.
OUT=$WORK/out-$K-of-$N
GLOG=$WORK/gate-$K-of-$N.log
rc=0
UNITS=$LIST SSE2NEON=$EXT/sse2neon NICE=${NICE:-19} bash "$P/tools/static/run.sh" "$TREE" "$OUT" "$JOBS" > "$GLOG" 2>&1 || rc=$?
t2=$(date +%s)
compiled=$( (awk -F'\t' '$2=="0"' "$OUT/gcc/status.tsv" 2>/dev/null || true) | wc -l)

# 7. Report: counts and sites (file, line, class) only; diagnostics when nothing compiled or the gate failed.
TSV=${REPORT%.md}.tsv
: > "$TSV"
for f in "$OUT"/sites/*.tsv; do
  [ -f "$f" ] || continue
  cls=$(basename "$f" .tsv)
  tail -n +2 "$f" | awk -F'\t' -v c="$cls" '{print c"\t"$1"\t"$2}' >> "$TSV"
done
sort -u -o "$TSV" "$TSV"
{
  echo "# static gate ${C12}, shard $K of $N"
  echo
  echo "- commit: \`$COMMIT\`"
  echo "- units in the shard: $(wc -l < "$LIST"); GCC compiled: $compiled"
  echo "- tree: $TREE_FROM, $((t1-t0)) s; gate: $((t2-t1)) s; jobs: $JOBS, host: $(nproc) CPUs"
  echo "- toolchain: $(aarch64-linux-gnu-g++ --version | head -1); $(clang++-18 --version | head -1); check: $TC"
  echo "- gate exit code: $rc (0 ok, 1 a class above tools/static/baseline.tsv or a failed step, 2 the gate could not run)"
  echo "- steps:"
  echo; echo '```'
  grep -E '^(gcc|clang (gnu|msvc|atomic)|layouts|probe|report|gate|run\.sh)[: ]' "$GLOG" | san || true
  echo '```'
  if [ "$compiled" = 0 ] || [ "$rc" != 0 ]; then
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
echo "$REPORT"
[ "$rc" -lt 2 ] || exit "$rc"     # the report says why; a class above the baseline is a result, not a job failure
