#!/usr/bin/env bash
# T1: the real tools/link/compile.sh on a tiny tree whose LAST unit has no object (it does not compile), in every
# mode: no list, an explicit list, a flags-stamp change with a list (the wiped units), --print-flags. Each exits 0 and leaves no "line N: exit" message (set -E: the ERR trap names any
# failure). Needs aarch64-linux-gnu-g++ and the gcc plugin headers (skipped without the compiler).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
command -v aarch64-linux-gnu-g++ > /dev/null || { echo "test_compile_lastunit: skipped (no aarch64-linux-gnu-g++)"; exit 0; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
mkdir -p "$T/tree/build" "$T/tree/src" "$T/tree/include"
printf '{"units": [{"source": "src/a.cpp", "functions": []}, {"source": "src/z.cpp", "functions": []}]}\n' > "$T/tree/build/units.json"
echo 'int a() { return 1; }' > "$T/tree/src/a.cpp"; echo 'int z() { return nosuch; }' > "$T/tree/src/z.cpp"
printf 'src/a.cpp\nsrc/z.cpp\n' > "$T/both"; printf 'src/z.cpp\n' > "$T/last"; printf 'src/a.cpp\n' > "$T/first"
C=$P/tools/link/compile.sh
run() { local name=$1; shift; ( "$@" ) > "$T/$name.log" 2>&1; echo $? > "$T/$name.rc"; }
quiet() { [ "$(cat "$T/$1.rc")" = 0 ] && ! grep -q "compile.sh: line [0-9]*: exit" "$T/$1.log"; }
run nolist bash "$C" "$T/tree" "$T/o1" "" 2
ok "no list: exit 0, the last unit failed with a reason" 'quiet nolist && grep -q "src/z.cpp.*fail" "$T/o1/objects.tsv" && grep -q "compile.sh: src/z.cpp: exit" "$T/o1/src_z.cpp.log"'
run list bash "$C" "$T/tree" "$T/o1" "$T/last" 2
ok "a list of the last unit only: exit 0" 'quiet list && [ "$(cat "$T/o1/objects.tsv")" = "src/z.cpp	$T/o1/src_z.cpp.o	fail" ]'
run stamp env OPT=-O1 bash "$C" "$T/tree" "$T/o1" "$T/first" 2
ok "stamp change with a list, the last unit without an object: exit 0, the wiped unit rebuilt" 'quiet stamp && grep -q "1 had one, 1 were asked for" "$T/stamp.log" && grep -q "src/a.cpp.*ok" "$T/o1/objects.tsv"'
run flags bash "$C" --print-flags "$T/tree" "$T/o4"
ok "--print-flags: exit 0, flags only on stdout" 'quiet flags && grep -q "^-c$" "$T/flags.log"'
[ $fails = 0 ] && echo "test_compile_lastunit: all ok" || { echo "test_compile_lastunit: $fails failed"; exit 1; }
