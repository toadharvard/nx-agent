#!/usr/bin/env bash
# T1: the pipeline's compile stage keeps an object only while it was compiled from the prepared source and headers as
# they are now, by content (build.py's <unit>.o.hash), never by mtime alone (links ef092d51de92, 5ae3f0b13834 carried
# objects from before S1's RAWVT1-MEM). The real `make compile` on a tiny adopted tree (PREPARED, TC_DL=none: no
# toolchain build) through the unit cache: unchanged -> cached; a source changed with an OLD mtime -> rebuilt; a
# header changed with an old mtime -> rebuilt; the other unit kept. Needs aarch64-linux-gnu-g++ and ext/.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
command -v aarch64-linux-gnu-g++ > /dev/null && [ -d "$P/ext/sse2neon" ] || { echo "test_compile_stamp: skipped (no cross g++ or ext/)"; exit 0; }
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
B=$T/base; mkdir -p "$B/tree/build" "$B/tree/src" "$B/tree/include"
echo '{"units": [{"source": "src/a.cpp", "functions": []}, {"source": "src/b.cpp", "functions": []}]}' > "$B/tree/build/units.json"
echo 'int K = 7;' > "$B/tree/include/k.h"
printf '#include "k.h"\nint a() { return K; }\n' > "$B/tree/src/a.cpp"; echo 'int b() { return 2; }' > "$B/tree/src/b.cpp"
git -C "$P" rev-parse HEAD > "$B/prepared_from.txt"
mk() {   # mk <n>: one `make compile`; its compile line in $T/c<n>
	timeout 600 env FNVNX_UNIT_OBJCACHE="$T/uc" make -C "$P" compile BUILD_DIR="$T/build" PREPARED="$B/tree" FORCE_STALE=1 TESTS=none \
		EXT="$P/ext" JOBS=2 TC_DL=none > "$T/m$1.log" 2>&1
	grep -h '] compile  ' "$T/m$1.log" > "$T/c$1"
}
obj=$T/build/compile/obj
mk 1
ok "first: both compiled, each with its content stamp" 'grep -q "built now 2" $T/c1 && [ -s $obj/src_a.cpp.o.hash ] && [ -s $obj/src_b.cpp.o.hash ]'
mk 2
ok "unchanged: cached" 'grep -q "cached" $T/c2'
cp "$obj/src_b.cpp.o" "$T/b0.o"
printf '#include "k.h"\nint a() { return K + 1; } // FNVNX_D26_SLOT(2)\n' > "$B/tree/src/a.cpp"; touch -d '-3 hours' "$B/tree/src/a.cpp"
mk 3
ok "a source changed with an OLD mtime: rebuilt (only it), its stamp the new source's" 'grep -q "built now 1" $T/c3 && [ "$(head -1 $obj/src_a.cpp.o.hash)" = "$(sha256sum < $B/tree/src/a.cpp | cut -c1-64)" ] && cmp -s $obj/src_b.cpp.o $T/b0.o'
echo 'int K = 9;' > "$B/tree/include/k.h"; touch -d '-3 hours' "$B/tree/include/k.h"
mk 4
ok "a header changed with an old mtime: the unit that includes it rebuilt" 'grep -q "built now 1" $T/c4 && cmp -s $obj/src_b.cpp.o $T/b0.o'
[ $fails = 0 ] && echo "test_compile_stamp: all ok" || { tail -n 5 "$T"/m*.log; echo "test_compile_stamp: $fails failed"; exit 1; }
