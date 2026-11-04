#!/usr/bin/env bash
# test_job_gamedata.sh - lib_job.sh's job_gamedata (J-0128): the owner's exe from the artifacts repository's private
# branch gamedata/exe, taken only when its sha256 matches; FARM_REAL_EXE=0 and a missing branch give no exe.
#   bash tests/farm/test_job_gamedata.sh        -> "job_gamedata: ok"
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
eval "$(sed -n '/^job_gamedata() {/,/^}/p' "$HERE/../../tools/farm/lib_job.sh")"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
check() { [ "$1" = "$2" ] || { echo "FAIL: $3: want '$2', got '$1'"; fail=1; }; }
g() { git -c user.name=t -c user.email=t@t "$@"; }
# the "remote": a branch with an exe and its checksum
g init -q "$T/remote"; (cd "$T/remote" && printf 'MZfake' > FalloutNV.exe && sha256sum FalloutNV.exe > FalloutNV.exe.sha256 \
	&& g checkout -q -b gamedata/exe && g add . && g commit -qm exe)
g clone -q --no-checkout "$T/remote" "$T/art"
export FARM_EXE_SHA256=$(printf MZfake | sha256sum | cut -d" " -f1)
ARTIFACTS=$T/art FARM_HOME=$T/h1; job_gamedata
check "$GAME_EXE" "$T/h1/gamedata/FalloutNV.exe" "exe taken"
check "$(cat "$GAME_EXE")" "MZfake" "exe content"
FARM_REAL_EXE=0 FARM_HOME=$T/h2; job_gamedata
check "$GAME_EXE" "" "FARM_REAL_EXE=0"
unset FARM_REAL_EXE
# another exe (its own checksum file agrees with it): refused, only the pinned hash counts
(cd "$T/remote" && printf 'MZother' > FalloutNV.exe && sha256sum FalloutNV.exe > FalloutNV.exe.sha256 && g commit -qam other)
FARM_HOME=$T/h3; job_gamedata
check "$GAME_EXE" "" "not the pinned exe"
# a symbolic link with the right target content: refused
(cd "$T/remote" && printf MZfake > "$T/outside.bin" && rm FalloutNV.exe && ln -s "$T/outside.bin" FalloutNV.exe \
	&& g add -A && g commit -qm link)
FARM_HOME=$T/h5; job_gamedata
check "$GAME_EXE" "" "symlink"
# no such branch
g init -q "$T/empty"; (cd "$T/empty" && g commit -q --allow-empty -m e); g clone -q --no-checkout "$T/empty" "$T/art2"
ARTIFACTS=$T/art2 FARM_HOME=$T/h4; job_gamedata
check "$GAME_EXE" "" "no branch"
[ $fail = 0 ] && echo "job_gamedata: ok"
exit $fail
