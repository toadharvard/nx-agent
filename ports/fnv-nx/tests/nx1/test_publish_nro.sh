#!/usr/bin/env bash
# NX1: tools/farm/lib_job.sh publish_nro against a throw-away artifacts repository with a bare origin.
#   K1  package stage ok after host, record names this commit, real NRO whose NACP version is the commit12 ->
#       published, and the remote branch is exactly the pushed commit (with build.json naming the commit)
#   P1a this job's host stage failed after an earlier job's package (stage order)         -> refused, rc 1
#   P1b the package record names another commit (port_commit)                             -> refused, rc 1
#   P1c the record names this commit but fnv.nro carries another commit12 (NACP version)   -> refused, rc 1
#   P2  the push is rejected while an earlier nro/<c12> exists                            -> "FAILED", rc 2
#   bash tests/nx1/test_publish_nro.sh   -> one line per case, exit 1 on any failure
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); PORT=$(cd "$HERE/../.." && pwd)
T=$(mktemp -d) && [ -d "$T" ] || { echo "mktemp failed"; exit 2; }
trap 'rm -rf "$T"' EXIT
g() { git -c user.name=t -c user.email=t@invalid "$@"; }
COMMIT=aaaaaaaaaaaa1111111111111111111111111111; C12=${COMMIT:0:12}
fails=0
setup() {
	rm -rf "$T/origin.git" "$T/art" "$T/farm" "$T/bd"
	git init -q --bare "$T/origin.git"; git init -q "$T/art"
	g -C "$T/art" commit -q --allow-empty -m init; g -C "$T/art" remote add origin "$T/origin.git"
	mkdir -p "$T/farm" "$T/bd/package/sd/switch/fnv" "$T/bd/host"
}
stage() { mkdir -p "$T/bd/$1"; printf '{"stage": "%s", "status": "%s", "port_commit": "%s"}\n' "$1" "$2" "${3:-}" > "$T/bd/$1/stage.json"; }
nro() {  # nro <version>: a real (fixture) NRO whose NACP display version is <version>
	python3 -I - "$PORT" "$1" "$T/bd/package/sd/switch/fnv/fnv.nro" "$T" <<'PY'
import os, subprocess, sys
port, ver, out, t = sys.argv[1:5]
sys.path.insert(0, os.path.join(port, "toolchain", "tools"))
import elf2nro, nxelf
elf = os.path.join(t, "fx.elf")
if not os.path.exists(elf):
    subprocess.run([os.path.join(port, "tests", "nx1", "fixture", "build.sh"), elf], check=True)
open(out, "wb").write(elf2nro.build_nro(nxelf.read(elf), open(os.path.join(port, "toolchain", "nro", "icon.jpg"), "rb").read(),
                                        elf2nro.build_nacp("fnv-nx", "t", ver)))
PY
}
publish() {
	(
		export FARM_HOME=$T/farm ARTIFACTS=$T/art C12 COMMIT FARM_APT=0 FARM_PUBLISH=auto
		# shellcheck disable=SC1091
		. "$PORT/tools/farm/lib_job.sh"
		rc=0; publish_nro "$T/bd" >/dev/null 2>&1 || rc=$?
		echo "$rc|$NOTE_NRO"
	)
}
remote() { git -C "$T/origin.git" rev-parse -q --verify "refs/heads/nro/$C12" 2>/dev/null; }
check() {  # check <case> <cond> <text>
	if eval "$2"; then echo "ok   $1: $3"; else echo "FAIL $1: $3"; fails=$((fails + 1)); fi
}
command -v aarch64-linux-gnu-as >/dev/null || { echo "SKIP: aarch64-linux-gnu binutils missing"; exit 0; }

setup; stage host ok; sleep 1; stage package ok "$COMMIT"; nro "$C12"
r=$(publish); bj=$(git -C "$T/origin.git" show "refs/heads/nro/$C12:build.json" 2>/dev/null)
check K1 '[ "${r%%|*}" = 0 ] && [[ "$r" == *published:* ]] && [[ "$bj" == *"$COMMIT"* ]]' "$r; build.json $bj"

setup; stage package ok "$COMMIT"; nro "$C12"; sleep 1; stage host fail
r=$(publish)
check P1a '[ "${r%%|*}" = 1 ] && [ -z "$(remote)" ]' "$r"

setup; stage host ok; sleep 1; stage package ok bbbbbbbbbbbb2222222222222222222222222222; nro "$C12"
r=$(publish)
check P1b '[ "${r%%|*}" = 1 ] && [ -z "$(remote)" ]' "$r"

setup; stage host ok; sleep 1; stage package ok "$COMMIT"; nro bbbbbbbbbbbb
r=$(publish)
check P1c '[ "${r%%|*}" = 1 ] && [ -z "$(remote)" ]' "$r"

setup; stage host ok; sleep 1; stage package ok "$COMMIT"; nro "$C12"
publish >/dev/null; before=$(remote)
printf '#!/bin/sh\nexit 1\n' > "$T/origin.git/hooks/pre-receive"; chmod +x "$T/origin.git/hooks/pre-receive"
sleep 1; stage package ok "$COMMIT"
r=$(publish)
check P2 '[ "${r%%|*}" = 2 ] && [[ "$r" == *FAILED* ]] && [ "$(remote)" = "$before" ]' "$r"
echo "$fails failed"
exit $((fails > 0))
