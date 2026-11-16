#!/usr/bin/env bash
# gamedata_fetch.sh - assemble a game install for the emulated boot from the private artifacts repository: FalloutNV.exe
# and the INIs from gamedata/exe, the d3dx9 DLLs from gamedata/redist, the Data/ items from gamedata/data/<slug>. The set is
# exactly the gamedata externals that the admitted recipes declare (`recipe.py externals --ref-prefix gamedata/`): each is fetched
# BY ITS COMMIT and its tree is checked against the recipe's tree_sha256 by the recipe's own fetch (BuildRed on a mismatch), then
# checked against its MANIFEST.tsv (every part's sha256 and the whole item's). A branch head is never fetched, so ARTIFACTS_URL
# changes where the bytes come from, never which bytes. An item that no recipe declares is red: the consumer declares the ref as
# an external of its root. The result is a directory to boot with FARM_GAME=<out> tests/s5/farm_boot.sh.
# Game data: <out> must be outside any git checkout, never committed.
#
#   farm/gamedata_fetch.sh <out dir> [item...]      default: every declared gamedata/data/* external
#   farm/gamedata_fetch.sh <out dir> --no-data      only the exe, the INIs, the static modules (gamedata/exe) and the d3dx9 DLLs
#                                                   (gamedata/redist): what a start to WinMain needs
#
# Env: ARTIFACTS_URL (default: verify/artifacts.py url, the repository env.json pins). GAMEDATA_CACHE (default $TMPDIR/gamedata-cache): the
# recipe's store and external cache, content-keyed, reused by the next run. No host path is read.
# A finished fetch writes <out>/.gamedata_pins (the declared pins it installed); when they have not changed, the next run with the same
# items does nothing ("up to date"), so farms can call it before every boot.
# Exit: 0 ok, 1 an undeclared item, a failed fetch, a tree mismatch or a checksum mismatch, 2 usage.
set -euo pipefail
OUT=${1:?usage: farm/gamedata_fetch.sh <out dir> [--no-data | item...]}; shift
NODATA=0; if [ "${1:-}" = --no-data ]; then NODATA=1; shift; [ $# -eq 0 ] || { echo "gamedata_fetch: --no-data takes no items" >&2; exit 2; }; fi
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
URL=${ARTIFACTS_URL:-$(python3 -I "$HERE/../verify/artifacts.py" url)}   # the pinned repository of env.json: one accessor (verify/artifacts.py)
mkdir -p "$OUT/Data"; OUT=$(cd "$OUT" && pwd)
if top=$(git -C "$OUT" rev-parse --show-toplevel 2>/dev/null); then
  echo "gamedata_fetch: $OUT is inside the git checkout $top; game data must stay out of git" >&2; exit 2
fi
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
CACHE=${GAMEDATA_CACHE:-${TMPDIR:-/tmp}/gamedata-cache}; mkdir -p "$CACHE"
rcp() { ( cd "$REPO" && python3 -I ports/fnv-nx/verify/recipe.py externals --ref-prefix gamedata/ "$@" ); }
# the declared pins: the only refs this script may read
PINS=$(rcp) || { echo "gamedata_fetch: the recipes' gamedata externals could not be read" >&2; exit 1; }
slugs=()
if [ $NODATA = 0 ]; then
  if [ $# -gt 0 ]; then for i in "$@"; do slugs+=("${i// /_}"); done
  else mapfile -t slugs < <(printf '%s\n' "$PINS" | awk -F'\t' '$1 ~ /^gamedata\/data\//{sub(/^gamedata\/data\//,"",$1); print $1}'); fi
fi
for slug in "${slugs[@]}"; do
  printf '%s\n' "$PINS" | awk -F'\t' -v r="gamedata/data/$slug" '$1==r{f=1} END{exit !f}' || { echo "gamedata_fetch: gamedata/data/$slug is declared by no recipe: declare it as an external of the root that reads it" >&2; exit 1; }
done
want=$(printf '%s\n' "$PINS" | awk -F'\t' -v nd=$NODATA '$1=="gamedata/exe"||$1=="gamedata/redist"||(!nd&&$1 ~ /^gamedata\/data\//)' ; echo "items: ${slugs[*]:-}"; echo "no-data: $NODATA")
if [ -f "$OUT/.gamedata_pins" ] && [ "$(cat "$OUT/.gamedata_pins")" = "$want" ] && [ -f "$OUT/FalloutNV.exe" ]; then
  echo "gamedata_fetch: $OUT up to date"; exit 0
fi
rm -f "$OUT/.gamedata_pins"
# The proxy cuts long transfers now and then ("Connection reset by peer", "RPC failed": sw36, 2026-11-07, on the
# 1.6 GB Voices1 item): the fetch is retried with backoff (GAMEDATA_TRIES, default 5: 2, 4, 8, 16 s); the recipe's cache keeps what arrived.
retry() { local i=1 n=${GAMEDATA_TRIES:-5}; while :; do "$@" && return 0; [ $i -ge $n ] && return 1
  echo "gamedata_fetch: retry $i of $((n - 1)) in $((2 ** i)) s" >&2; sleep $((2 ** i)); i=$((i + 1)); done; }
GOT=$(mktemp "${TMPDIR:-/tmp}/gamedata-got.XXXXXX"); trap 'rm -f "$GOT"' EXIT
fetchall() { rcp --fetch --store "$CACHE/store" --ext "$CACHE/external" --artifacts "$URL" > "$GOT"; }
retry fetchall || { echo "gamedata_fetch: fetch of the declared externals failed or a tree_sha256 did not match" >&2; exit 1; }
dir_of() { awk -F'\t' -v r="$1" '$1==r{print $2}' "$GOT"; }

d=$(dir_of gamedata/exe); [ -n "$d" ] || { echo "gamedata_fetch: no gamedata/exe declared" >&2; exit 1; }
cp -a "$d"/. "$OUT"/
echo "gamedata_fetch: gamedata/exe -> $(cd "$OUT" && ls | grep -v '^Data$' | tr '\n' ' ')"
d=$(dir_of gamedata/redist); [ -n "$d" ] || { echo "gamedata_fetch: no gamedata/redist declared" >&2; exit 1; }
cp -a "$d"/. "$OUT"/
( cd "$OUT" && sha256sum -c --quiet SHA256SUMS ) || { echo "gamedata_fetch: gamedata/redist checksum mismatch" >&2; exit 1; }
bad=0
for slug in "${slugs[@]}"; do
  d=$(dir_of "gamedata/data/$slug")
  name=$(awk -F'\t' '$1=="name"{print $2}' "$d/MANIFEST.tsv"); kind=$(awk -F'\t' '$1=="kind"{print $2}' "$d/MANIFEST.tsv")
  sum_want=$(awk -F'\t' '$1=="sha256"{print $2}' "$d/MANIFEST.tsv")
  while IFS=$'\t' read -r tag f size sum; do
    [ "$tag" = part ] || continue
    [ "$(sha256sum "$d/$f" | cut -c1-64)" = "$sum" ] || { echo "gamedata_fetch: $f: sha256 mismatch" >&2; bad=1; }
  done < "$d/MANIFEST.tsv"
  w=$(mktemp "${TMPDIR:-/tmp}/gamedata-whole.XXXXXX"); cat "$d/$slug".part* > "$w"
  got=$(sha256sum "$w" | cut -c1-64)
  if [ "$got" != "$sum_want" ]; then echo "gamedata_fetch: $name: sha256 $got, manifest says $sum_want" >&2; bad=1; rm -f "$w"; continue; fi
  if [ "$kind" = tar ]; then tar -C "$OUT/Data" -xf "$w"; rm -f "$w"; else mv "$w" "$OUT/Data/$name"; fi
  echo "gamedata_fetch: $name ok ($kind, sha256 $got)"
done
[ $bad = 0 ] || exit 1
printf '%s\n' "$want" > "$OUT/.gamedata_pins"
echo "gamedata_fetch: $OUT ready: FARM_GAME=$OUT tests/s5/farm_boot.sh <c12> <out>"
