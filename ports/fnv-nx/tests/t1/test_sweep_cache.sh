#!/usr/bin/env bash
# test_sweep_cache.sh - sweep_job.sh's capture cache functions (cap_setup, cap_publish, data_key) against a local bare
# repository standing in for fnv-nx-build: a shard publishes capture/<key>/<k>of<N>, a fresh farm with the same key
# fetches it and finds the scene; the data key is stable. Synthetic data only.   bash tests/t1/test_sweep_cache.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); CMP=$P/tools/compare
SP=$(mktemp -d); trap 'rm -rf "$SP"' EXIT
mkdir -p "$SP/game/Data" "$SP/home"; echo exe >"$SP/game/FalloutNV.exe"; echo esm >"$SP/game/Data/FalloutNV.esm"
git init -q --bare "$SP/remote.git"; git clone -q "$SP/remote.git" "$SP/art" 2>/dev/null
FARM_HOME=$SP/home; ARTIFACTS=$SP/art; GAME=$SP/game; BIN=/x/nxd9play-abc; K=1; N=2; H12=deadbeef0000
S=$FARM_HOME/sweep/$H12/${K}of$N; mkdir -p "$S"; NOTES=()
key_of() { cat "$@" 2>/dev/null | sha256sum | cut -c1-16; }
wine() { echo wine-test; }
W=$FARM_HOME/compare; log() { :; }
eval "$(sed -n '/^free_gb() {/,/^need_space() {/p' "$P/tools/farm/sweep_job.sh" | sed '$d')"
eval "$(sed -n '/^need_space() {/,/^}/p' "$P/tools/farm/sweep_job.sh")"
eval "$(sed -n '/^cell_list() {/,/^}/p' "$P/tools/farm/sweep_job.sh")"
eval "$(sed -n '/^CAP_MODE=/,/^cap_publish() {/p' "$P/tools/farm/sweep_job.sh" | sed '$d')"
eval "$(sed -n '/^cap_publish() {/,/^}/p' "$P/tools/farm/sweep_job.sh")"
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
printf 'sceneA\tinterior\tX\t\nsceneB\tinterior\tY\t\n' >"$S/cells.tsv"
mkdir -p "$FARM_HOME/sweep/0123456789ab/1of2" "$FARM_HOME/sweep/wine/drive_c" "$FARM_HOME/capture/0000old0000key00"
cap_setup; k1=$CKEY
ok "empty store" '[[ "${NOTES[-1]}" == *"0 of this shard'"'"'s scenes cached"* ]]'
ok "disk: another head's sweep dir removed, this head's and the wine prefix kept" '[ ! -d "$FARM_HOME/sweep/0123456789ab" ] && [ -d "$S" ] && [ -d "$FARM_HOME/sweep/wine/drive_c" ]'
ok "disk: another capture key's store removed" '[ ! -d "$FARM_HOME/capture/0000old0000key00" ] && [ -d "$CAPS" ]'
NOTES=(); need_space 100000000 "test" >/dev/null; rcs=$?
ok "disk: need_space fails loudly below the threshold" '[ $rcs = 1 ] && [[ "${NOTES[-1]}" == "FAILED: disk: "*"test needs 100000000 GB"* ]]'
need_space 0 "test" >/dev/null; ok "disk: need_space passes with room" '[ $? = 0 ]'
NOTES=()
python3 -I -c "import sys; sys.path.insert(0, '$P/tests/t1'); sys.path.insert(0, '$CMP')
import test_capstore as t; t.synth('$SP/a.nxd9', 1, b'q' * 9000)"
python3 -I "$CMP/capstore.py" pack "$CAPS" sceneA "$SP/a.nxd9" --meta scene=h1 >/dev/null && echo sceneA >"$S/packed.tsv"
cap_publish
# another shard (2of2) publishes sceneC: a farm sweeping shard 1 must not keep it
S2=$FARM_HOME/sweep/$H12/2of2; mkdir -p "$S2"; CAPS2=$SP/store2; mkdir -p "$CAPS2"; cp "$CAPS/KEY" "$CAPS2/KEY"
python3 -I -c "import sys; sys.path.insert(0, '$P/tests/t1'); sys.path.insert(0, '$CMP')
import test_capstore as t; t.synth('$SP/c.nxd9', 2, b'r' * 9000)"
python3 -I "$CMP/capstore.py" pack "$CAPS2" sceneC "$SP/c.nxd9" --meta scene=h3 >/dev/null
( S=$S2; K=2; CAPS=$CAPS2; echo sceneC >"$S/packed.tsv"; printf 'sceneC\tinterior\tZ\t\n' >"$S/cells.tsv"; cap_publish )
ok "published" 'git --git-dir="$SP/remote.git" rev-parse -q --verify "refs/heads/capture/$k1/1of2" >/dev/null'
rm -rf "$FARM_HOME/capture"; NOTES=(); cap_setup
ok "same key on a fresh farm" '[ "$CKEY" = "$k1" ]'
ok "fetched: this shard's scene, both branches read" '[[ "${NOTES[-1]}" == *"1 of this shard'"'"'s scenes cached, 2 branch(es)"* ]]'
ok "disk: another shard's scene not kept" '! python3 -I "$CMP/capstore.py" has "$CAPS" sceneC'
ok "disk: no fetched packs or scratch store left" '[ ! -e "$FARM_HOME/capture/git" ] && [ ! -e "$FARM_HOME/capture/tmp" ]'
ok "scene found with its hash" 'python3 -I "$CMP/capstore.py" has "$CAPS" sceneA --scene-hash h1'
python3 -I "$CMP/capstore.py" unpack "$CAPS" sceneA "$SP/a.out"
ok "unpacked bytes equal" 'cmp -s "$SP/a.nxd9" "$SP/a.out"'
d1=$(data_key); d2=$(data_key)
ok "data key stable" '[ "$d1" = "$d2" ]'
echo more >"$GAME/Data/FalloutNV.esm"
ok "data key follows content" '[ "$(data_key)" != "$d1" ]'
NOTES=(); cap_setup
ok "new key when the data changes" '[ "$CKEY" != "$k1" ]'
ok "disk: the old key's store removed with the key change" '[ ! -d "$FARM_HOME/capture/$k1" ]'
[ $fails = 0 ] && echo "test_sweep_cache: all ok" || { echo "test_sweep_cache: $fails failed"; exit 1; }
