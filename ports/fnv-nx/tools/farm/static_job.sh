#!/usr/bin/env bash
# static_job.sh - farm job: prepare the tree from one commit of this repo and run the static gate (docs/STATIC_CHECKS.md)
# on one shard of its units.
#
#   tools/farm/static_job.sh <commit> <k> <N> <report.md>
#
# Shard k of N (1 <= k <= N): a unit belongs to shard (crc32(path) mod N) + 1, as in compile_job.sh, so the
# assignment does not move between commits. All gate steps run (tools/static/run.sh): GCC -O2 with the build's
# flags, clang gnu (AST checks, layouts, flow warnings), clang msvc (clang's MSVC x86 layout engine; no Microsoft
# tool is involved), atomic, layout diff, probe, report. SKIP="msvc layout probe" leaves out the layout steps.
# Atomic keys come from the shard's own Interlocked uses, so a cross-shard plain store is missed.
#
# Writes <report.md>: shard, timings and coverage counts, the per-class site counts, and every site as
# `file line CLASS` (a site in a header counts once per shard). Next to it <report>.tsv (class, file, line), so
# the shards of one commit merge with `cat *.tsv | sort -u`. No detail text, no prepared source.
#
# Work trees live under $FARM_HOME (default ~/fnvnx-farm): the port at that commit (src-<commit>), the pinned
# third-party checkouts (ext, tools/fetch.sh), the prepared tree and the gate output (static-<commit>). JOBS
# (default nproc) parallel compiles; every compiler runs under nice (NICE, default 19).
# Needs: git, python3, network for the fetch. Installs (apt, sudo when not root) whatever is missing of: clang-18,
# libclang-18-dev, llvm-18-dev, g++-multilib, g++-aarch64-linux-gnu, jq.
#
# Overrides for a machine that already has the inputs (e.g. a light test on a development machine, where a
# full-tree prepare is not allowed): FARM_SRC=<checkout at <commit>> (no worktree), FARM_EXT=<dir> (used as it is,
# no fetch), STATIC_TREE=<prepared tree> (read-only, no prepare), SHIM=<dir> (run.sh: the shim of the tree's commit).
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
[ "$K" -ge 1 ] && [ "$K" -le "$N" ] || { echo "static_job.sh: need 1 <= k <= N" >&2; exit 2; }
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
C12=${COMMIT:0:12}
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")

# 1. Tools: clang/LLVM development packages for fnvcheck, the AArch64 cross compiler, 32-bit libstdc++ headers.
need=()
command -v clang++-18 >/dev/null || need+=(clang-18)
[ -f /usr/lib/llvm-18/include/clang/Tooling/Tooling.h ] || need+=(libclang-18-dev)
[ -x /usr/lib/llvm-18/bin/llvm-config ] || need+=(llvm-18-dev)
[ -d /usr/include/x86_64-linux-gnu/c++/13/32 ] || need+=(g++-multilib)
command -v aarch64-linux-gnu-g++ >/dev/null || need+=(g++-aarch64-linux-gnu)
command -v jq >/dev/null || need+=(jq)
if [ ${#need[@]} -gt 0 ]; then
  SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo
  $SUDO apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q --no-install-recommends "${need[@]}" >/dev/null
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

# 4. Prepared tree (once per commit) and the shard list.
WORK=$FARM_HOME/static-$C12
mkdir -p "$WORK"
t0=$(date +%s)
if [ -n "${STATIC_TREE:-}" ]; then
  TREE=$(cd "$STATIC_TREE" && pwd)
else
  TREE=$WORK/tree
  if [ ! -f "$WORK/tree.done" ]; then
    rm -rf "$TREE"
    python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$TREE" > "$WORK/prepare.log" 2>&1
    touch "$WORK/tree.done"
  fi
fi
t1=$(date +%s)
LIST=$WORK/shard-$K-of-$N.list
jq -r '.units[].source' "$TREE/build/units.json" | sort -u | python3 -c '
import sys, zlib
k, n = int(sys.argv[1]), int(sys.argv[2])
for line in sys.stdin:
    u = line.rstrip("\n")
    if zlib.crc32(u.encode()) % n == k - 1: print(u)' "$K" "$N" > "$LIST"

# 5. The gate on the shard.
OUT=$WORK/out-$K-of-$N
rc=0
UNITS=$LIST SSE2NEON=$EXT/sse2neon NICE=${NICE:-19} bash "$P/tools/static/run.sh" "$TREE" "$OUT" "$JOBS" \
  > "$WORK/gate-$K-of-$N.log" 2>&1 || rc=$?
t2=$(date +%s)

# 6. Report: counts and sites (file, line, class) only.
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
  echo "- units in the shard: $(wc -l < "$LIST"); prepare: $((t1-t0)) s, gate: $((t2-t1)) s, jobs: $JOBS, host: $(nproc) CPUs"
  echo "- gate exit code: $rc (0 ok, 1 a class above tools/static/baseline.tsv, 2 the gate could not run: see steps)"
  echo "- steps:"
  echo; echo '```'
  grep -E '^(gcc|clang (gnu|msvc|atomic)|layouts|probe|report|gate|run\.sh)[: ]' "$WORK/gate-$K-of-$N.log" \
    | sed "s|$FARM_HOME|\$FARM_HOME|g" || true
  echo '```'
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
