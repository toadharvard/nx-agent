#!/usr/bin/env bash
# T1: compile.sh's units through the shared content-addressed cache (FNVNX_UNIT_OBJCACHE, tools/link/objcache.py cc).
# The real compile.sh on a tiny tree: with the cache, the objects are byte-identical to a compile without it; the
# objects removed and compiled again come from the cache (no new result is stored), the failing unit's error is
# given back too; a changed header is a miss. Needs aarch64-linux-gnu-g++ and the gcc
# plugin headers (skipped without the compiler).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
command -v aarch64-linux-gnu-g++ > /dev/null || { echo "test_unit_objcache: skipped (no aarch64-linux-gnu-g++)"; exit 0; }
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
mkdir -p "$T/tree/build" "$T/tree/src" "$T/tree/include"
printf '{"units": [{"source": "src/a.cpp", "functions": []}, {"source": "src/b.cpp", "functions": []}, {"source": "src/z.cpp", "functions": []}]}\n' > "$T/tree/build/units.json"
echo 'int K = 7;' > "$T/tree/include/k.h"
echo '#include "k.h"
int a() { return K + 1; }' > "$T/tree/src/a.cpp"
echo 'int b(int x) { return x * 3; }' > "$T/tree/src/b.cpp"
echo 'int z() { return nosuch; }' > "$T/tree/src/z.cpp"
C=$P/tools/link/compile.sh
cc() { ( env ORDER_KEYS=unit "$@" bash "$C" "$T/tree" "$T/out" "" 2 ) > "$T/cc.log" 2>&1; }
cc; cp -a "$T/out" "$T/plain"                                                     # no cache: the reference
rm -rf "$T/out"; cc FNVNX_UNIT_OBJCACHE=$T/cache
ok "with the cache: the same objects as without" 'cmp -s "$T/plain/src_a.cpp.o" "$T/out/src_a.cpp.o" && cmp -s "$T/plain/src_b.cpp.o" "$T/out/src_b.cpp.o" && [ ! -f "$T/out/src_z.cpp.o" ]'
ok "the cache holds them" '[ "$(find "$T/cache/r" -name "*.o" | wc -l)" = 2 ]'
# the objects gone: compiled again, every result must come from the cache (no new result stored)
rm -f "$T/out"/*.o "$T/out"/*.ok
cc FNVNX_UNIT_OBJCACHE=$T/cache
ok "compiled again from the cache: the same objects, the failing unit's error replayed" 'cmp -s "$T/plain/src_a.cpp.o" "$T/out/src_a.cpp.o" && cmp -s "$T/plain/src_b.cpp.o" "$T/out/src_b.cpp.o" && grep -q "nosuch" "$T/out/src_z.cpp.log" && [ "$(find "$T/cache/r" -name "*.json" | wc -l)" = 3 ]'
echo 'int K = 8;' > "$T/tree/include/k.h"; touch "$T/tree/src/a.cpp"
cc FNVNX_UNIT_OBJCACHE=$T/cache
ok "a changed header: a miss, a new object" '! cmp -s "$T/plain/src_a.cpp.o" "$T/out/src_a.cpp.o" && [ "$(find "$T/cache/r" -name "*.o" | wc -l)" = 3 ]'
# the dependency file the pipeline asks for (build.py: CXX="g++ -MD", it reads <unit>.o.d for its mtime checks): written
# on a miss and on a hit, naming the headers (link/ef092d51de92: the cache wrote none, every cached object looked
# current forever and S1's RAWVT1-MEM never reached the link)
rm -rf "$T/out" "$T/cache2"
CXX="aarch64-linux-gnu-g++ -MD" cc FNVNX_UNIT_OBJCACHE=$T/cache2
ok "a miss: <unit>.o.d written where gcc -MD puts it, naming the header" 'grep -q "include/k.h" "$T/out/src_a.cpp.o.d" && grep -q "src/a.cpp" "$T/out/src_a.cpp.o.d"'
rm -f "$T/out"/*.o "$T/out"/*.o.d "$T/out"/*.ok
CXX="aarch64-linux-gnu-g++ -MD" cc FNVNX_UNIT_OBJCACHE=$T/cache2
ok "a hit: <unit>.o.d written from the recorded dependencies (the header too)" 'grep -q "include/k.h" "$T/out/src_a.cpp.o.d" && grep -q "src/a.cpp" "$T/out/src_a.cpp.o.d"'
ok "build.py: a unit with no dependency file is compiled again, never kept" 'grep -q "if deps is None or any((mtime(p) is None or mtime(p) > ot) for p in deps) or not stamp_ok(base, src, deps):" "$P/tools/pipeline/build.py"'
[ $fails = 0 ] && echo "test_unit_objcache: all ok" || { tail -n 20 "$T/cc.log"; echo "test_unit_objcache: $fails failed"; exit 1; }
