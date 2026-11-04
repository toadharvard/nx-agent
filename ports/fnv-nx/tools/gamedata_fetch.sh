#!/usr/bin/env bash
# gamedata_fetch.sh - assemble a game install for the emulated boot from the private artifacts repository: FalloutNV.exe
# and the INIs from branch gamedata/exe, the Data/ items from gamedata/data/<slug> (tools/gamedata_publish.sh), each
# checked against its MANIFEST.tsv (every part's sha256 and the whole item's). The result is a directory to boot with
# FARM_GAME=<out> tests/s5/farm_boot.sh. Game data: <out> must be outside any git checkout, never committed.
#
#   tools/gamedata_fetch.sh <out dir> [item...]      default: every gamedata/data/* branch
#
# Env: ARTIFACTS (a clone of the artifacts repository to fetch into; default /home/user/fnv-nx-build if it exists),
#      ARTIFACTS_URL (fetched over directly when there is no clone).
# Exit: 0 ok, 1 a missing branch or a checksum mismatch, 2 usage.
set -euo pipefail
OUT=${1:?usage: tools/gamedata_fetch.sh <out dir> [item...]}; shift || true
URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
mkdir -p "$OUT/Data"; OUT=$(cd "$OUT" && pwd)
if top=$(git -C "$OUT" rev-parse --show-toplevel 2>/dev/null); then
  echo "gamedata_fetch: $OUT is inside the git checkout $top; game data must stay out of git" >&2; exit 2
fi
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gamedata-fetch.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q
fetch() { git -C "$TMP" fetch -q --depth 1 "$URL" "refs/heads/$1" && git -C "$TMP" rev-parse FETCH_HEAD; }

# the exe and the INIs (gamedata/exe)
c=$(fetch gamedata/exe) || { echo "gamedata_fetch: no gamedata/exe" >&2; exit 1; }
git -C "$TMP" archive "$c" | tar -x -C "$OUT"
echo "gamedata_fetch: gamedata/exe -> $(cd "$OUT" && ls | grep -v '^Data$' | tr '\n' ' ')"

if [ $# -gt 0 ]; then
  slugs=(); for i in "$@"; do slugs+=("${i// /_}"); done
else
  mapfile -t slugs < <(git ls-remote "$URL" 'refs/heads/gamedata/data/*' | sed 's|.*refs/heads/gamedata/data/||')
fi
bad=0
for slug in "${slugs[@]}"; do
  c=$(fetch "gamedata/data/$slug") || { echo "gamedata_fetch: no branch gamedata/data/$slug" >&2; bad=1; continue; }
  d=$TMP/$slug; mkdir -p "$d"; git -C "$TMP" archive "$c" | tar -x -C "$d"
  name=$(awk -F'\t' '$1=="name"{print $2}' "$d/MANIFEST.tsv"); kind=$(awk -F'\t' '$1=="kind"{print $2}' "$d/MANIFEST.tsv")
  want=$(awk -F'\t' '$1=="sha256"{print $2}' "$d/MANIFEST.tsv")
  while IFS=$'\t' read -r tag f size sum; do
    [ "$tag" = part ] || continue
    [ "$(sha256sum "$d/$f" | cut -c1-64)" = "$sum" ] || { echo "gamedata_fetch: $f: sha256 mismatch" >&2; bad=1; }
  done < "$d/MANIFEST.tsv"
  cat "$d/$slug".part* > "$d/whole"
  got=$(sha256sum "$d/whole" | cut -c1-64)
  if [ "$got" != "$want" ]; then echo "gamedata_fetch: $name: sha256 $got, manifest says $want" >&2; bad=1; continue; fi
  if [ "$kind" = tar ]; then tar -C "$OUT/Data" -xf "$d/whole"; else mv "$d/whole" "$OUT/Data/$name"; fi
  rm -rf "$d"
  echo "gamedata_fetch: $name ok ($kind, sha256 $got)"
done
[ $bad = 0 ] || exit 1
echo "gamedata_fetch: $OUT ready: FARM_GAME=$OUT tests/s5/farm_boot.sh <c12> <out>"
