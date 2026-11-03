#!/usr/bin/env bash
# run.sh - the static gate: find code of the prepared tree whose meaning differs between MSVC 2008 x86-32 (what the
# decomp matches) and GCC AArch64 ILP32 (what we build). See docs/STATIC_CHECKS.md.
#
#   tools/static/run.sh <prepared tree> <out dir> [jobs]
#
# Steps (each reuses results newer than the unit; FORCE=1 redoes them; SKIP="gcc msvc atomic probe" skips steps):
#   flags    BUILD_FLAGS of flags.sh must still be the flags of tools/link/compile.sh and tools/census.sh
#   grep     text counts (__try, #pragma pack, bit-field declarations, ...)             -> <out>/grep.tsv
#   gcc      GCC -O2 with the real flags + target-semantics warnings (gcc_warn.sh)     -> <out>/gcc/
#   gnu      clang, GCC/Itanium ILP32 proxy: fnvcheck AST checks, layouts, warnings    -> <out>/clang/gnu/
#   msvc     clang, MSVC x86-32: layouts                                               -> <out>/clang/msvc/
#   atomic   plain stores to fields that are elsewhere updated with Interlocked*       -> <out>/clang/atomic/
#   layout   layout_diff.py (msvc vs gnu)                                              -> <out>/layout/
#   probe    layout_probe.py: real GCC AArch64 checks the gnu proxy on every differing record -> <out>/probe/
#   report   report.py: <out>/report.md, <out>/sites/<class>.tsv, <out>/counts.tsv; exit 1 when a class grows past
#            tools/static/baseline.tsv
# Everything under <out> is derived from the decomp: local only, never committed.
set -euo pipefail
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); JOBS=${3:-2}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
S="$HERE/tools/static"
SKIP=" ${SKIP:-} "
# shellcheck source=flags.sh
. "$S/flags.sh"

# ---- flags: the gate must look at what the build compiles
for f in "${BUILD_FLAGS[@]}"; do
  for script in tools/link/compile.sh tools/census.sh; do
    grep -qF -- "$f" "$HERE/$script" || { echo "run.sh: $f is not in $script any more - update tools/static/flags.sh" >&2; exit 2; }
  done
done

# ---- grep counts over the whole tree (src + include)
{
  cnt() { printf '%s\t%s\n' "$1" "$( (grep -rIhoE --include='*.cpp' --include='*.h' --include='*.hpp' --include='*.inl' --include='*.c' "$2" "$TREE/src" "$TREE/include" || true) | wc -l)"; }
  cnt 'grep: __try blocks (SEH: filters never run on the target)' '\b__try\b'
  cnt 'grep: #pragma pack(push, 1)' 'pragma[[:space:]]+pack[[:space:]]*\([[:space:]]*push[[:space:]]*,[[:space:]]*1[[:space:]]*\)'
  cnt 'grep: #pragma pack(push, 2)' 'pragma[[:space:]]+pack[[:space:]]*\([[:space:]]*push[[:space:]]*,[[:space:]]*2[[:space:]]*\)'
  cnt 'grep: #pragma pack(push, 4)' 'pragma[[:space:]]+pack[[:space:]]*\([[:space:]]*push[[:space:]]*,[[:space:]]*4[[:space:]]*\)'
  cnt 'grep: #pragma pack other forms' 'pragma[[:space:]]+pack[[:space:]]*\([[:space:]]*(show|[0-9]|[[:space:]]*\)|push[[:space:]]*,[[:space:]]*[A-Za-z_])'
  cnt 'grep: long double' 'long[[:space:]]+double'
  cnt 'grep: __declspec(thread)' '__declspec[[:space:]]*\([[:space:]]*thread'
  cnt 'grep: volatile' '\bvolatile\b'
  cnt 'grep: _ReadWriteBarrier/_ReadBarrier/_WriteBarrier calls' '\b_(ReadWrite|Read|Write)Barrier[[:space:]]*\([[:space:]]*\)[[:space:]]*;'
  cnt 'grep: __readfsdword / NtCurrentTeb (TEB access)' '\b(__readfsdword|NtCurrentTeb)\b'
} > "$OUT/grep.tsv"

step() { case "$SKIP" in *" $1 "*) return 1 ;; esac; echo "== $1"; }

if step gcc; then bash "$S/gcc_warn.sh" "$TREE" "$OUT" "$JOBS"; fi
if step gnu; then bash "$S/clang_pass.sh" "$TREE" "$OUT" gnu "$JOBS"; fi
if step msvc; then bash "$S/clang_pass.sh" "$TREE" "$OUT" msvc "$JOBS"; fi
if step atomic; then
  # keys of the Interlocked operands, and the units whose text names one of those fields / globals
  cat "$OUT"/clang/gnu/rows/*.tsv 2>/dev/null | awk -F'\t' '$1=="ATOMIC-USE"{print $5}' | sort -u > "$OUT/clang/atomic.keys"
  sed -E 's/^(field .*::|var (.*::)?)//' "$OUT/clang/atomic.keys" | sort -u > "$OUT/clang/atomic.names"
  mkdir -p "$OUT/clang/atomic"
  if [ -s "$OUT/clang/atomic.names" ]; then
    jq -r '.units[].source' "$TREE/build/units.json" | sort -u | while IFS= read -r u; do
      [ -f "$TREE/$u" ] && grep -qwF -f "$OUT/clang/atomic.names" "$TREE/$u" && printf '%s\n' "$u"
    done > "$OUT/clang/atomic/units.list" || true
    ATOMIC_KEYS="$OUT/clang/atomic.keys" bash "$S/clang_pass.sh" "$TREE" "$OUT" atomic "$JOBS" "$OUT/clang/atomic/units.list"
  fi
fi
if step layout; then python3 "$S/layout_diff.py" "$OUT"; fi
if step probe; then python3 "$S/layout_probe.py" "$TREE" "$OUT"; fi
step report || exit 0
python3 "$S/report.py" "$TREE" "$OUT" "$S/baseline.tsv"
