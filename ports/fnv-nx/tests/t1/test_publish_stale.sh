#!/usr/bin/env bash
# T1: lib_job.sh publish_link refuses a build whose objects are older than their prepared sources (link/ef092d51de92
# carried objects from before S1's RAWVT1-MEM: the unit cache wrote no dependency files, so the compile stage kept
# every object): "not published: stale objects, N (units) older than their prepared source", nothing pushed. With
# current objects the guard passes (the publish then goes on as before). A synthetic build directory.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
C=$(printf 'a%.0s' $(seq 40))
mk() {   # mk <obj older: 1|0>
	rm -rf "$T/bd"; mkdir -p "$T/bd/prepare/tree/build" "$T/bd/prepare/tree/src" "$T/bd/compile/obj" "$T/bd/link/fixed"
	echo '{"units": [{"source": "src/a.cpp"}, {"source": "src/b.cpp"}]}' > "$T/bd/prepare/tree/build/units.json"
	echo '{"status": "ok", "artifacts": {"tree": "'"$T/bd/prepare/tree"'"}}' > "$T/bd/prepare/stage.json"
	echo '{"status": "ok", "metrics": {}}' > "$T/bd/compile/stage.json"
	echo "{\"status\": \"ok\", \"prepared_from\": {\"port_commit\": \"$C\"}}" > "$T/bd/link/stage.json"
	echo o > "$T/bd/link/fixed/zz_bridges.o"
	echo 'int a();' > "$T/bd/prepare/tree/src/a.cpp"; echo 'int b();' > "$T/bd/prepare/tree/src/b.cpp"
	echo o > "$T/bd/compile/obj/src_a.cpp.o"; echo o > "$T/bd/compile/obj/src_b.cpp.o"
	if [ "$1" = 1 ]; then touch -d '-1 hour' "$T/bd/compile/obj/src_a.cpp.o"; else touch -d '-1 hour' "$T/bd/prepare/tree/src/"*.cpp; fi
}
pub() { ( set -e; export ARTIFACTS=$T/noart FARM_HOME=$T/home COMMIT=$C C12=${C:0:12} P=$P REPORT=$T/r.md NOTE_TREE=x NOTE_OBJS=y OBJ_COMMIT=
	. "$P/tools/farm/lib_job.sh"; publish_link "$T/bd" || true; echo "$NOTE_PUB" > "$T/note" ) > "$T/pub.log" 2>&1; }
mk 1; pub
ok "an object older than its prepared source: not published, the unit named" 'grep -q "^not published: stale objects, 1 (src/a.cpp) older than their prepared source" $T/note'
mk 0; pub
ok "current objects: the guard passes (the publish goes on)" '! grep -q "stale objects" $T/note'
[ $fails = 0 ] && echo "test_publish_stale: all ok" || { cat "$T/pub.log" "$T/note"; echo "test_publish_stale: $fails failed"; exit 1; }
