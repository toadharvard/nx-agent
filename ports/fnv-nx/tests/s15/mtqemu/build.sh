#!/usr/bin/env bash
# build.sh <out dir> - build the mtqemu prototype (docs/PERF.md 6.6): linhost (LP64 aarch64 Linux, freestanding) and
# guest.elf (ILP32: the real platform/win32 sync layer, platform/os over the host API, toolchain/guest/threads.c,
# the test compiled with -fsanitize=thread and hb.c as its runtime). Then run:
#   qemu-aarch64 -R 0xC0000000 <out>/linhost <out>/guest.elf          (or natively on an aarch64 Linux machine)
# Needs aarch64-linux-gnu-gcc/g++/ld (Ubuntu gcc-aarch64-linux-gnu); seconds, no farm.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
CC=${CC:-aarch64-linux-gnu-gcc}
CXX=${CXX:-aarch64-linux-gnu-g++}
LD=${LD:-aarch64-linux-gnu-ld}

# guest: the game's data model and code generation (toolchain/guest/build.sh), freestanding, fixed address
G=(-mabi=ilp32 -O2 -g -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -fno-pic -fno-pie -fno-stack-protector -mno-outline-atomics
   -ftls-model=local-exec -fno-asynchronous-unwind-tables -fshort-wchar -frounding-math -Wall -Wno-unused-parameter
   -I"$HERE/include" -I"$ROOT/toolchain/include" -I"$ROOT/loader/include" -I"$ROOT/platform/include" -iquote "$ROOT/shim/include"
   -I"$ROOT/platform/crt")
GXX=(-std=gnu++17 -fno-exceptions -fno-rtti -fno-threadsafe-statics -fno-use-cxa-atexit)
TSAN=(-fsanitize=thread)
objs=(); lm=()   # lm: the link map of guest.elf, "<root-relative source>=<object>" (platform/manifest/check_doubles.py)
cc() { local o="$OUT/$(basename "$1").o"; shift_src=$1; shift; $CC -std=gnu11 "${G[@]}" "$@" -c "$shift_src" -o "$o"; objs+=("$o"); lm+=("${shift_src#"$ROOT"/}=$o"); }
cxx() { local o="$OUT/$(basename "$1").o"; shift_src=$1; shift; $CXX "${GXX[@]}" "${G[@]}" "$@" -c "$shift_src" -o "$o"; objs+=("$o"); lm+=("${shift_src#"$ROOT"/}=$o"); }

for f in sync thread handles waitgraph platstat; do cxx "$ROOT/platform/win32/$f.cpp"; done
for f in clock_real clock_rt; do cxx "$ROOT/platform/clock/$f.cpp"; done   # the clock interface (fnvnx_clock.h) the sync layer reads time through
cc "$ROOT/platform/guest/os_guest.c"
cc "$ROOT/platform/os/os_world.c"   # os_world_register/unregister (os_guest.c calls them; a platform root unit)
cc "$ROOT/toolchain/guest/threads.c" -Wno-missing-field-initializers
cc "$HERE/mlibc.c" -fno-builtin -fno-tree-loop-distribute-patterns -Wno-builtin-declaration-mismatch
cc "$HERE/gstart.c"
cc "$HERE/hb.c"
cc "$ROOT/tools/link/overrides/spinlock_0040fba0.c" "${TSAN[@]}"
cxx "$HERE/mt_test.cpp" "${TSAN[@]}"
# the inlined Interlocked* of fnvnx_win32.h, as game units get them (ilk-count, ilk-refs)
cxx "$HERE/ilk_mt.cpp" "${TSAN[@]}" -Wno-tsan
# B9: the cost of one Interlocked operation (ilk-cost), uninstrumented
cxx "$HERE/ilk_cost.cpp"
# and without the sanitizer (TSan turns atomics into calls), only for test_mtqemu.py's code check: not linked
$CXX "${GXX[@]}" "${G[@]}" -c "$HERE/ilk_mt.cpp" -o "$OUT/ilk_mt_codegen.o"
# S12's litmus test (tests/s12/order/litmus.cpp) twice: with the fnvnx_order plugin (the build's semantics) and
# without it. The plugin needs gcc-13-plugin-dev-aarch64-linux-gnu; without it the litmus cases report "skip".
cc "$HERE/litmus_glue.c"
LITMUS_ROUNDS=${LITMUS_ROUNDS:-2000}
LIT=(-fms-extensions -fno-strict-aliasing -fwrapv -mtune=cortex-a57 -DLITMUS_ROUNDS=$LITMUS_ROUNDS "${TSAN[@]}" -Wno-tsan)
if ORDER_SO=$(CXX=$CXX bash "$ROOT/toolchain/order/build.sh" "$OUT/.order" 2>/dev/null); then
  $CXX "${GXX[@]}" "${G[@]}" "${LIT[@]}" -fplugin="$ORDER_SO" -DLITMUS_NS=lit_order -DLITMUS_MAIN=litmus_main_order \
    -c "$HERE/litmus_mt.cpp" -o "$OUT/litmus_order.o"
  $CXX "${GXX[@]}" "${G[@]}" "${LIT[@]}" -DLITMUS_NS=lit_plain -DLITMUS_MAIN=litmus_main_plain \
    -c "$HERE/litmus_mt.cpp" -o "$OUT/litmus_plain.o"
  objs+=("$OUT/litmus_order.o" "$OUT/litmus_plain.o")
  lm+=("${HERE#"$ROOT"/}/litmus_mt.cpp=$OUT/litmus_order.o" "${HERE#"$ROOT"/}/litmus_mt.cpp=$OUT/litmus_plain.o")
else
  cc "$HERE/litmus_none.c"
fi

wraps=()
for s in EnterCriticalSection LeaveCriticalSection TryEnterCriticalSection SetEvent ReleaseSemaphore \
         WaitForSingleObject WaitForMultipleObjects InterlockedIncrement InterlockedDecrement InterlockedExchange \
         InterlockedExchangeAdd InterlockedCompareExchange fnvnx_interlocked_slow CreateThread; do
  wraps+=("--wrap=$s")
done
# --wrap applies to every object's undefined references; the platform objects define these names and call each other
# directly (definitions are not wrapped), so only the test's and the override's calls are intercepted.
(IFS=$'\t'; printf 'mtqemu_guest\t%s\n' "${lm[*]}") > "$OUT/linkmap.tsv"
$LD -m aarch64linux32 -T "$HERE/mt.ld" "${wraps[@]}" "${objs[@]}" -o "$OUT/guest.elf"

# host: LP64, static, freestanding, below 4 GiB
$CC -O2 -g -ffreestanding -nostdlib -static -fno-pic -no-pie -fno-stack-protector -mno-outline-atomics \
    -fno-asynchronous-unwind-tables -fno-tree-loop-distribute-patterns -Wall -Wl,-Ttext-segment=0x10000000 -Wl,--build-id=none \
    -I"$ROOT/toolchain/include" -I"$ROOT/toolchain/host" "$HERE/linhost.c" -o "$OUT/linhost"
echo "mtqemu: $OUT/linhost $OUT/guest.elf"
