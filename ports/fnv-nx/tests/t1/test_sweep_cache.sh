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
eval "$(sed -n '/^CAP_MODE=/,/^cap_publish() {/p' "$P/tools/farm/sweep_job.sh" | sed '$d')"
eval "$(sed -n '/^cap_publish() {/,/^}/p' "$P/tools/farm/sweep_job.sh")"
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
cap_setup; k1=$CKEY
ok "empty store" '[[ "${NOTES[-1]}" == *"0 scenes cached"* ]]'
python3 -I -c "import sys; sys.path.insert(0, '$P/tests/t1'); sys.path.insert(0, '$CMP')
import test_capstore as t; t.synth('$SP/a.nxd9', 1, b'q' * 9000)"
python3 -I "$CMP/capstore.py" pack "$CAPS" sceneA "$SP/a.nxd9" --meta scene=h1 >/dev/null && echo sceneA >"$S/packed.tsv"
printf 'sceneA\tinterior\tX\t\nsceneB\tinterior\tY\t\n' >"$S/cells.tsv"
cap_publish
ok "published" 'git --git-dir="$SP/remote.git" rev-parse -q --verify "refs/heads/capture/$k1/1of2" >/dev/null'
rm -rf "$FARM_HOME/capture"; NOTES=(); cap_setup
ok "same key on a fresh farm" '[ "$CKEY" = "$k1" ]'
ok "fetched" '[[ "${NOTES[-1]}" == *"1 scenes cached, 1 branch(es)"* ]]'
ok "scene found with its hash" 'python3 -I "$CMP/capstore.py" has "$CAPS" sceneA --scene-hash h1'
python3 -I "$CMP/capstore.py" unpack "$CAPS" sceneA "$SP/a.out"
ok "unpacked bytes equal" 'cmp -s "$SP/a.nxd9" "$SP/a.out"'
d1=$(data_key); d2=$(data_key)
ok "data key stable" '[ "$d1" = "$d2" ]'
echo more >"$GAME/Data/FalloutNV.esm"
ok "data key follows content" '[ "$(data_key)" != "$d1" ]'
NOTES=(); cap_setup
ok "new key when the data changes" '[ "$CKEY" != "$k1" ]'
[ $fails = 0 ] && echo "test_sweep_cache: all ok" || { echo "test_sweep_cache: $fails failed"; exit 1; }
