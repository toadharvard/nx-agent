#!/usr/bin/env bash
# test_compile_reasons.sh - tools/link/compile.sh never fails a unit without a reason (try1of1, D26: 2,499 units
# "failed (no error line)", 1,716 logs of 0 bytes). compile_one and the objects.tsv pass, taken from compile.sh, with
# a stand-in compiler: no output + exit 3, killed by a signal, exit 0 without an object, success; then an object
# that disappears after compiling and a unit the run never reached. Each failed unit's log ends with its reason; a
# compile_one that fails still returns 0 (xargs goes on).   bash tests/t1/test_compile_reasons.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
TREE=$T/tree OUT=$T/obj; mkdir -p "$TREE/src" "$OUT"
for u in a b c d e f; do echo "int $u;" >"$TREE/src/$u.cpp"; done
cat >"$T/cc" <<'SH'
#!/bin/bash
src=; out=; while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift ;; *.cpp) src=$1 ;; esac; shift; done
case $src in
  src/a.cpp) exit 3 ;;                      # no output, exit 3
  src/b.cpp) kill -9 $$ ;;                  # killed by a signal
  src/c.cpp) exit 0 ;;                      # exit 0, no object
  *) echo obj >"$out" ;;                    # success
esac
SH
chmod +x "$T/cc"
export TREE OUT CXX=$T/cc CC=$T/cc FLAGS_STR="" CFLAGS_STR=""
: >"$OUT/defines.tsv"
eval "$(sed -n '/^compile_one() {/,/^}/p' "$P/tools/link/compile.sh")"
for u in a b c d e; do ( compile_one "src/$u.cpp" ); echo "$?" >>"$T/rcs"; done
ok "compile_one returns 0 for every unit" '[ "$(sort -u "$T/rcs")" = 0 ]'
ok "exit 3 without output: the log says so" 'grep -q "src/a.cpp: exit 3" "$OUT/src_a.cpp.log"'
ok "killed: the log names the signal" 'grep -q "src/b.cpp: killed by signal 9" "$OUT/src_b.cpp.log"'
ok "exit 0 without an object: the log says so" 'grep -q "src/c.cpp: exit 0 but no object" "$OUT/src_c.cpp.log" && [ ! -f "$OUT/src_c.cpp.o" ]'
ok "success: object and its marker" '[ -f "$OUT/src_d.cpp.o" ] && [ -f "$OUT/src_d.cpp.ok" ] && [ ! -s "$OUT/src_d.cpp.log" ]'
rm -f "$OUT/src_e.cpp.o"                     # compiled in this run, then removed (another compile.sh's stamp, say)
printf 'src/%s.cpp\n' a b c d e f >"$T/list"; LIST=$T/list
sed -n '/^: > "\$OUT\/objects.tsv"$/,/^done < "\$LIST"$/p' "$P/tools/link/compile.sh" >"$T/tail.sh"
ok "the objects.tsv pass was found" '[ "$(wc -l <"$T/tail.sh")" -gt 5 ]'
. "$T/tail.sh"
ok "an object that disappeared after compiling is named" 'grep -q "src/e.cpp: compiled in this run, then its object disappeared" "$OUT/src_e.cpp.log"'
ok "a unit the run never reached is named" 'grep -q "src/f.cpp: never compiled" "$OUT/src_f.cpp.log"'
ok "objects.tsv: d ok, the rest fail" '[ "$(grep -c $'"'"'\tok$'"'"' "$OUT/objects.tsv")" = 1 ] && [ "$(grep -c $'"'"'\tfail$'"'"' "$OUT/objects.tsv")" = 5 ]'
ok "every failed unit's log has a reason" '( for u in a b c e f; do grep -q "^compile.sh: src/$u.cpp: " "$OUT/src_$u.cpp.log" || exit 1; done )'
# the flags stamp changes while the pipeline asks for one unit: every unit that had an object is rebuilt (try1of1:
# 493 asked for, the other 2,485 objects wiped and left unbuilt)
W=$T/wipe; mkdir -p "$W/tree/build" "$W/obj"
printf '{"units": [{"source": "src/a.cpp"}, {"source": "src/b.cpp"}, {"source": "src/c.cpp"}, {"source": "src/d.cpp"}]}\n' >"$W/tree/build/units.json"
for u in a b c; do echo o >"$W/obj/src_$u.cpp.o"; done; echo old >"$W/obj/.objstamp"; echo src/a.cpp >"$W/asked.list"
sed -n '/^if \[ "\$(cat "\$OUT\/.objstamp" 2>\/dev\/null)" != "\$objstamp" \]; then$/,/^fi$/p' "$P/tools/link/compile.sh" >"$W/stamp.sh"
ok "the stamp block was found" 'grep -q "wiped.list" "$W/stamp.sh"'
( TREE=$W/tree OUT=$W/obj LIST=$W/asked.list objstamp=new; . "$W/stamp.sh"; cat "$LIST" ) >"$W/list.after" 2>"$W/err"
ok "stamp change: the work list holds every unit that had an object" '[ "$(cat "$W/list.after" | tr "\n" " ")" = "src/a.cpp src/b.cpp src/c.cpp " ]'
ok "stamp change: objects removed, new stamp, said on stderr" '[ -z "$(ls "$W/obj"/*.o 2>/dev/null)" ] && [ "$(cat "$W/obj/.objstamp")" = new ] && grep -q "3 had one, 1 were asked for" "$W/err"'
echo o >"$W/obj/src_a.cpp.o"
( TREE=$W/tree OUT=$W/obj LIST=$W/asked.list objstamp=new; . "$W/stamp.sh"; cat "$LIST" ) >"$W/list.same" 2>/dev/null
ok "same stamp: the list as given, objects kept" '[ "$(cat "$W/list.same")" = src/a.cpp ] && [ -f "$W/obj/src_a.cpp.o" ]'
# under set -euo pipefail, as compile.sh runs, with the LAST unit of units.json without an object (full1 on
# c46cd30231d9: `[ -f ] && echo` ended the loop with 1 and set -e stopped compile.sh with "exit 1" and no message)
for u in a b; do echo o >"$W/obj/src_$u.cpp.o"; done; rm -f "$W/obj/src_c.cpp.o"; echo old >"$W/obj/.objstamp"
( set -euo pipefail; TREE=$W/tree OUT=$W/obj LIST=$W/asked.list objstamp=new; . "$W/stamp.sh"; cat "$LIST"; echo reached ) >"$W/list.e" 2>"$W/err.e"; rc=$?
ok "set -e, last unit without an object: the block finishes (rc $rc)" '[ $rc = 0 ] && grep -qx reached "$W/list.e"'
ok "set -e: the work list is the units that had one plus the asked" '[ "$(grep -v reached "$W/list.e" | tr "\n" " ")" = "src/a.cpp src/b.cpp " ]'
ok "compile.sh names the line of any set -e failure" 'grep -q "trap .*compile.sh: line \$LINENO" "$P/tools/link/compile.sh"'
[ $fails = 0 ] && echo "test_compile_reasons: all ok" || { echo "test_compile_reasons: $fails failed"; exit 1; }
