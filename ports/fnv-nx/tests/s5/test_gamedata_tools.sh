#!/usr/bin/env bash
# test_gamedata_tools.sh - tools/gamedata_publish.sh and tools/gamedata_fetch.sh against a local bare repository, on
# synthetic files made here (no game data): a file split into parts, a directory as a tar, a re-publish that is
# skipped, a corrupted part that fetch refuses, and the refusal of an output inside a git checkout.
#   bash tests/s5/test_gamedata_tools.sh    -> "test_gamedata_tools: N ok, 0 failed"
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); T=$(cd "$HERE/../../tools" && pwd)
W=$(mktemp -d "${TMPDIR:-/tmp}/s5-gamedata.XXXXXX") || exit 1
trap 'rm -rf "$W"' EXIT
ok=0; bad=0
check() { if eval "$2"; then ok=$((ok + 1)); else bad=$((bad + 1)); echo "FAIL: $1"; fi; }
export HOME=$W/home GIT_CONFIG_GLOBAL=$W/gitconfig; mkdir -p "$HOME"
git config --global init.defaultBranch main
git config --global protocol.file.allow always
git init -q --bare "$W/remote.git"
export ARTIFACTS_URL=$W/remote.git WORK=$W/stage GAMEDATA_PART_MB=1
# gamedata/exe, as the private repository has it
mkdir -p "$W/exe"; printf 'MZsynthetic' > "$W/exe/FalloutNV.exe"; printf '[General]\r\n' > "$W/exe/Fallout_default.ini"
(cd "$W/exe" && git init -q && git checkout -q -b gamedata/exe && git add -A &&
  git -c user.name=t -c user.email=t@t commit -q -m exe && git push -q "$ARTIFACTS_URL" gamedata/exe)
# a synthetic install
mkdir -p "$W/inst/Data/Music/Explore"
head -c 2500000 /dev/urandom > "$W/inst/Data/Synth - Big.bsa"
printf 'TES4' > "$W/inst/Data/Synth.esm"
printf 'ogg' > "$W/inst/Data/Music/Explore/a.mp3"
out=$(bash "$T/gamedata_publish.sh" "$W/inst" "Synth.esm" "Synth - Big.bsa" Music 2>&1); rc=$?
check "publish runs" "[ $rc = 0 ]"
check "one branch per item" "[ \$(git ls-remote '$ARTIFACTS_URL' 'refs/heads/gamedata/data/*' | wc -l) = 3 ]"
check "space -> _ in the branch" "git ls-remote '$ARTIFACTS_URL' | grep -q 'gamedata/data/Synth_-_Big.bsa'"
git init -q "$W/peek"; git -C "$W/peek" fetch -q --depth 1 "$ARTIFACTS_URL" refs/heads/gamedata/data/Synth_-_Big.bsa
check "the big file is in 3 parts of at most 1 MiB" "[ \$(git -C '$W/peek' show FETCH_HEAD:MANIFEST.tsv | grep -c '^part') = 3 ]"
out=$(bash "$T/gamedata_publish.sh" "$W/inst" "Synth.esm" 2>&1)
check "an unchanged item is skipped" "echo \"\$out\" | grep -q skipped"
bash "$T/gamedata_fetch.sh" "$W/game" > "$W/fetch.log" 2>&1; rc=$?
check "fetch runs" "[ $rc = 0 ]"
check "exe from gamedata/exe" "cmp -s '$W/exe/FalloutNV.exe' '$W/game/FalloutNV.exe'"
check "file reassembled" "cmp -s '$W/inst/Data/Synth - Big.bsa' '$W/game/Data/Synth - Big.bsa'"
check "directory restored" "cmp -s '$W/inst/Data/Music/Explore/a.mp3' '$W/game/Data/Music/Explore/a.mp3'"
out=$(bash "$T/gamedata_fetch.sh" "$W/game" 2>&1)
check "a second fetch with unmoved heads does nothing" "echo \"\$out\" | grep -q 'up to date'"
# a corrupted part: publish a branch by hand whose part does not match its manifest
git -C "$W/peek" checkout -q FETCH_HEAD; printf 'X' >> "$W/peek/Synth_-_Big.bsa.part01"
git -C "$W/peek" -c user.name=t -c user.email=t@t commit -q -am corrupt
git -C "$W/peek" push -q -f "$ARTIFACTS_URL" HEAD:refs/heads/gamedata/data/Synth_-_Big.bsa
bash "$T/gamedata_fetch.sh" "$W/game2" "Synth - Big.bsa" > "$W/fetch2.log" 2>&1; rc=$?
check "a corrupted part is refused" "[ $rc = 1 ] && grep -q mismatch '$W/fetch2.log' && [ ! -e '$W/game2/Data/Synth - Big.bsa' ]"
mkdir -p "$W/repo"; git -C "$W/repo" init -q
bash "$T/gamedata_fetch.sh" "$W/repo/out" > /dev/null 2>&1; rc=$?
check "fetch refuses an output inside a git checkout" "[ $rc = 2 ]"
WORK=$W/repo/stage bash "$T/gamedata_publish.sh" "$W/inst" Synth.esm > /dev/null 2>&1; rc=$?
check "publish refuses a staging dir inside a git checkout" "[ $rc = 2 ]"
echo "test_gamedata_tools: $ok ok, $bad failed"
[ $bad = 0 ]
