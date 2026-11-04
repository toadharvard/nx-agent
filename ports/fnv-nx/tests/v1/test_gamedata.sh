#!/usr/bin/env bash
# test_gamedata.sh - V1 (D14) against J-0128: job_gamedata takes the owner's exe only when its sha256 matches, and no
# game data reaches a published link branch or report. Synthetic bytes only (no game data in this repository).
#   bash tests/v1/test_gamedata.sh   -> one line per case, exit 1 when any case shows a defect
#   G1  the branch's checksum file lists another file only: is FalloutNV.exe checked at all?
#   G2  the branch holds a different exe with its own matching checksum (nothing pins 1.4.0.525's hash)
#   G3  FalloutNV.exe on the branch is a symbolic link out of the gamedata directory
#   L1  publish_link after a boot on the exe, with a copy of the exe left in boot/sdmc (a boot killed before its
#       cleanup) and in boot/stub: tests/v1/scan_gamedata_leak.py over link/<commit12>
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIB=$HERE/../../tools/farm/lib_job.sh
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
g() { git -c user.name=t -c user.email=t@t "$@"; }
fail=0
say() { echo "$1: $2"; case "$2" in ok*) ;; *) fail=1 ;; esac; }
eval "$(sed -n '/^job_gamedata() {/,/^}/p' "$LIB")"
branch() { # branch <name> <cmd...>: a remote with gamedata/exe made by cmd, cloned as the artifacts repository
	rm -rf "$T/r-$1" "$T/a-$1"; g init -q "$T/r-$1"
	(cd "$T/r-$1" && g checkout -q -b gamedata/exe && eval "$2" && g add -A . && g commit -qm exe)
	g clone -q --no-checkout "$T/r-$1" "$T/a-$1"
}
head -c 65536 /dev/urandom > "$T/fake.exe"; printf 'MZ' | dd of="$T/fake.exe" conv=notrunc 2>/dev/null
REAL_SHA_HEAD=89e70204   # J-0127: FalloutNV.exe 1.4.0.525 (GOG) sha256 89e70204...d11dd0fe

# G1
branch g1 "cp '$T/fake.exe' FalloutNV.exe; echo readme > README; sha256sum README > FalloutNV.exe.sha256"
ARTIFACTS=$T/a-g1 FARM_HOME=$T/h1; job_gamedata 2>/dev/null
if [ -n "$GAME_EXE" ]; then say G1 "ACCEPTED: the checksum file names only README, FalloutNV.exe is taken unchecked"
else say G1 "ok (refused)"; fi
# G2
branch g2 "cp '$T/fake.exe' FalloutNV.exe; sha256sum FalloutNV.exe > FalloutNV.exe.sha256"
ARTIFACTS=$T/a-g2 FARM_HOME=$T/h2; job_gamedata 2>/dev/null
if [ -n "$GAME_EXE" ] && [ "$(sha256sum "$GAME_EXE" | cut -c1-8)" != $REAL_SHA_HEAD ]; then
	say G2 "ACCEPTED: an exe that is not 1.4.0.525 (sha256 $(sha256sum "$GAME_EXE" | cut -c1-8)...), the check is self-consistency only"
else say G2 "ok (refused)"; fi
# G3
echo secret-outside > "$T/outside.bin"
branch g3 "ln -s '$T/outside.bin' FalloutNV.exe; sha256sum FalloutNV.exe > FalloutNV.exe.sha256"
ARTIFACTS=$T/a-g3 FARM_HOME=$T/h3; job_gamedata 2>/dev/null
if [ -n "$GAME_EXE" ] && [ -L "$GAME_EXE" ]; then say G3 "ACCEPTED: FalloutNV.exe is a symbolic link to $(readlink "$GAME_EXE" | sed "s|$T|\$T|")"
else say G3 "ok (refused)"; fi

# L1: the real publish_link, a local artifacts repository
git init -q --bare "$T/origin.git"; g clone -q "$T/origin.git" "$T/artifacts" 2>/dev/null
g -C "$T/artifacts" commit -q --allow-empty -m init; g -C "$T/artifacts" push -q origin HEAD:refs/heads/main 2>/dev/null
export ARTIFACTS=$T/artifacts FARM_HOME=$T/farm; mkdir -p "$FARM_HOME/gamedata"
# shellcheck source=/dev/null
. "$LIB"
cp "$T/fake.exe" "$FARM_HOME/gamedata/FalloutNV.exe"; GAME_EXE=$FARM_HOME/gamedata/FalloutNV.exe
NOTE_TREE=t; NOTE_OBJS=o; OBJ_COMMIT=""; REPORT=$T/report.md; echo "# report" > "$REPORT"
COMMIT=4444444444444444444444444444444444444444; C12=${COMMIT:0:12}
BD=$T/build
rec() { mkdir -p "$BD/$1"; printf '{"stage": "%s", "status": "%s"%s}\n' "$1" "$2" "${4:-}" > "$BD/$1/stage.json"; touch -d "$3" "$BD/$1/stage.json"; }
mkdir -p "$BD/link/out/cache" "$BD/link/out/vtables" "$BD/link/fixed" "$BD/guest" "$BD/boot/sdmc/fnvnx/game" "$BD/boot/stub"
for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv; do echo x > "$BD/link/out/$f"; done
echo x > "$BD/link/out/cache/msvc.tsv"; echo x > "$BD/link/fixed/zz_bridges.o"; echo x > "$BD/link/summary.txt"
rec compile ok '2026-11-04 08:00'; rec link ok '2026-11-04 10:00' ", \"prepared_from\": {\"port_commit\": \"$COMMIT\"}"
echo elf > "$BD/guest/fnvnx.elf"; rec guest ok '2026-11-04 10:10'
for f in summary.txt frontier.txt frontier.tsv emu.log; do echo boot > "$BD/boot/$f"; done
cp "$GAME_EXE" "$BD/boot/sdmc/fnvnx/game/FalloutNV.exe"; cp "$GAME_EXE" "$BD/boot/stub/FalloutNV.exe"
printf 'STOPPED: x\n' > "$BD/boot/stub/emu_stub.log"
rec boot warn '2026-11-04 10:20' ", \"image\": \"$GAME_EXE\""
publish_link "$BD" >/dev/null 2>&1
mkdir -p "$T/pub"; git -C "$T/origin.git" archive "refs/heads/link/$C12" | tar -x -C "$T/pub"
if out=$(python3 -I "$HERE/scan_gamedata_leak.py" "$GAME_EXE" "$T/pub"); then say L1 "ok (no exe bytes in link/$C12, $(ls -R "$T/pub" | grep -c .) entries)"
else say L1 "LEAK: $(echo "$out" | tail -n +2 | tr -s ' ' | tr '\n' ';')"; fi
grep -rq "$FARM_HOME/gamedata" "$T/pub" && say L1-path "the exe's path is in the publish (meta/report)" || say L1-path "ok"
exit $fail
