#!/usr/bin/env bash
# T1: lib_job.sh publish_link refuses a build with an object not compiled from its prepared source (links ef092d51de92
# and 5ae3f0b13834 carried objects from before S1's RAWVT1-MEM). By content: build.py writes <unit>.o.hash after each
# compile, its first line the sha256 of the prepared source; a missing or different stamp is "not published: stale
# objects, N (units) not compiled from their prepared source", whatever the mtimes say. A synthetic build directory.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
C=$(printf 'a%.0s' $(seq 40))
mk() {   # mk <a's stamp: good|old|none>
	rm -rf "$T/bd"; mkdir -p "$T/bd/prepare/tree/build" "$T/bd/prepare/tree/src" "$T/bd/compile/obj" "$T/bd/link/fixed"
	echo '{"units": [{"source": "src/a.cpp"}, {"source": "src/b.cpp"}]}' > "$T/bd/prepare/tree/build/units.json"
	echo '{"status": "ok", "artifacts": {"tree": "'"$T/bd/prepare/tree"'"}}' > "$T/bd/prepare/stage.json"
	echo '{"status": "ok", "metrics": {}}' > "$T/bd/compile/stage.json"
	echo "{\"status\": \"ok\", \"prepared_from\": {\"port_commit\": \"$C\"}}" > "$T/bd/link/stage.json"
	echo o > "$T/bd/link/fixed/zz_bridges.o"
	echo 'int a(); // FNVNX_D26_SLOT(2)' > "$T/bd/prepare/tree/src/a.cpp"; echo 'int b();' > "$T/bd/prepare/tree/src/b.cpp"
	for u in a b; do echo o > "$T/bd/compile/obj/src_$u.cpp.o"; printf '%s\nx\n' "$(sha256sum < "$T/bd/prepare/tree/src/$u.cpp" | cut -c1-64)" > "$T/bd/compile/obj/src_$u.cpp.o.hash"; done
	case $1 in
		old) printf '%s\nx\n' "$(echo 'int a(); // before the rule' | sha256sum | cut -c1-64)" > "$T/bd/compile/obj/src_a.cpp.o.hash" ;;
		none) rm -f "$T/bd/compile/obj/src_a.cpp.o.hash" ;;
	esac
	touch -d '+1 hour' "$T/bd/compile/obj/"*.o   # the objects look newer than the sources: mtimes must not decide
}
pub() { ( set -e; export ARTIFACTS=$T/noart FARM_HOME=$T/home COMMIT=$C C12=${C:0:12} P=$P REPORT=$T/r.md NOTE_TREE=x NOTE_OBJS=y OBJ_COMMIT=
	. "$P/tools/farm/lib_job.sh"; publish_link "$T/bd" || true; echo "$NOTE_PUB" > "$T/note" ) > "$T/pub.log" 2>&1; }
mk old; pub
ok "an object compiled from another source content (newer mtime): not published, the unit named" 'grep -q "^not published: stale objects, 1 (src/a.cpp) not compiled from their prepared source" $T/note'
mk none; pub
ok "an object with no stamp: not published" 'grep -q "^not published: stale objects, 1 (src/a.cpp)" $T/note'
mk good; pub
ok "every stamp matches its source: the guard passes (the publish goes on)" '! grep -q "stale objects" $T/note'
[ $fails = 0 ] && echo "test_publish_stale: all ok" || { cat "$T/pub.log" "$T/note"; echo "test_publish_stale: $fails failed"; exit 1; }
