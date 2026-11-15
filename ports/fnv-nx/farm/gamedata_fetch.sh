#!/usr/bin/env bash
# gamedata_fetch.sh - assemble a game install for the emulated boot from the private artifacts repository: FalloutNV.exe
# and the INIs from branch gamedata/exe, the Data/ items from gamedata/data/<slug> (the publishing job), each
# checked against its MANIFEST.tsv (every part's sha256 and the whole item's). The result is a directory to boot with
# FARM_GAME=<out> tests/s5/farm_boot.sh. Game data: <out> must be outside any git checkout, never committed.
#
#   farm/gamedata_fetch.sh <out dir> [item...]      default: every gamedata/data/* branch
#   farm/gamedata_fetch.sh <out dir> --no-data      only the exe, the INIs, the static modules (gamedata/exe) and the d3dx9 DLLs
#                                                   (gamedata/redist): what a start to WinMain needs
#
# Env: ARTIFACTS (a clone of the artifacts repository to fetch into; default /home/user/fnv-nx-build if it exists),
#      ARTIFACTS_URL (fetched over directly when there is no clone).
# A finished fetch writes <out>/.gamedata_heads (the branch heads it took); when the heads have not moved, the next
# run with the same items does nothing ("up to date"), so farms can call it before every boot.
# Exit: 0 ok, 1 a missing branch or a checksum mismatch, 2 usage.
set -euo pipefail
OUT=${1:?usage: farm/gamedata_fetch.sh <out dir> [--no-data | item...]}; shift
NODATA=0; if [ "${1:-}" = --no-data ]; then NODATA=1; shift; [ $# -eq 0 ] || { echo "gamedata_fetch: --no-data takes no items" >&2; exit 2; }; fi
URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
mkdir -p "$OUT/Data"; OUT=$(cd "$OUT" && pwd)
if top=$(git -C "$OUT" rev-parse --show-toplevel 2>/dev/null); then
  echo "gamedata_fetch: $OUT is inside the git checkout $top; game data must stay out of git" >&2; exit 2
fi
# The proxy cuts long transfers now and then ("Connection reset by peer", "RPC failed": sw36, 2026-11-07, on the
# 1.6 GB Voices1 item): every remote call is retried with backoff (GAMEDATA_TRIES, default 5: 2, 4, 8, 16 s).
retry() { local i=1 n=${GAMEDATA_TRIES:-5}; while :; do "$@" && return 0; [ $i -ge $n ] && return 1
  echo "gamedata_fetch: retry $i of $((n - 1)) in $((2 ** i)) s: ${*: -1}" >&2; sleep $((2 ** i)); i=$((i + 1)); done; }
lsr() { git ls-remote "$URL" "$@" > "$TMPL" && [ -s "$TMPL" ]; }
TMPL=$(mktemp "${TMPDIR:-/tmp}/gamedata-ls.XXXXXX")
retry lsr refs/heads/gamedata/exe refs/heads/gamedata/redist 'refs/heads/gamedata/data/*' || { echo "gamedata_fetch: ls-remote $URL failed" >&2; rm -f "$TMPL"; exit 1; }
heads=$(sort "$TMPL"; echo "items: $*"; echo "no-data: $NODATA")
if [ -f "$OUT/.gamedata_heads" ] && [ "$(cat "$OUT/.gamedata_heads")" = "$heads" ] && [ -f "$OUT/FalloutNV.exe" ]; then
  echo "gamedata_fetch: $OUT up to date"; exit 0
fi
rm -f "$OUT/.gamedata_heads"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/gamedata-fetch.XXXXXX")
trap 'rm -rf "$TMP" "$TMPL"' EXIT
# one fresh repository per item ($G, replaced by the next fetch): the items' objects (6.5 GB in all) never pile up
G=$TMP/repo
fetch1() { rm -rf "$G"; git init -q "$G" && git -C "$G" fetch -q --depth 1 "$URL" "refs/heads/$1"; }
fetch() { retry fetch1 "$1" && git -C "$G" rev-parse FETCH_HEAD; }

# the exe and the INIs (gamedata/exe)
c=$(fetch gamedata/exe) || { echo "gamedata_fetch: no gamedata/exe" >&2; exit 1; }
git -C "$G" archive "$c" | tar -x -C "$OUT"
echo "gamedata_fetch: gamedata/exe -> $(cd "$OUT" && ls | grep -v '^Data$' | tr '\n' ' ')"

# the d3dx9 DLLs the exe's static modules need (gamedata/redist)
c=$(fetch gamedata/redist) || { echo "gamedata_fetch: no gamedata/redist" >&2; exit 1; }
git -C "$G" archive "$c" | tar -x -C "$OUT"
( cd "$OUT" && sha256sum -c --quiet SHA256SUMS ) || { echo "gamedata_fetch: gamedata/redist checksum mismatch" >&2; exit 1; }
if [ $NODATA = 1 ]; then slugs=()
elif [ $# -gt 0 ]; then
  slugs=(); for i in "$@"; do slugs+=("${i// /_}"); done
else
  mapfile -t slugs < <(grep 'refs/heads/gamedata/data/' "$TMPL" | sed 's|.*refs/heads/gamedata/data/||')
fi
bad=0
mkdir -p "$OUT/.gamedata_items"
for slug in "${slugs[@]}"; do
  # an item whose branch head has not moved since it was installed here is skipped (only the exe branch moved, say)
  h=$(printf '%s\n' "$heads" | awk -v r="refs/heads/gamedata/data/$slug" '$2==r{print $1}')
  if [ -n "$h" ] && [ "$(cat "$OUT/.gamedata_items/$slug" 2>/dev/null)" = "$h" ]; then
    echo "gamedata_fetch: $slug unchanged ($h)"; continue
  fi
  rm -f "$OUT/.gamedata_items/$slug"
  c=$(fetch "gamedata/data/$slug") || { echo "gamedata_fetch: no branch gamedata/data/$slug" >&2; bad=1; continue; }
  d=$TMP/$slug; mkdir -p "$d"; git -C "$G" archive "$c" | tar -x -C "$d"; rm -rf "$G"
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
  [ -n "$h" ] && echo "$h" > "$OUT/.gamedata_items/$slug"
  echo "gamedata_fetch: $name ok ($kind, sha256 $got)"
done
[ $bad = 0 ] || exit 1
printf '%s\n' "$heads" > "$OUT/.gamedata_heads"
echo "gamedata_fetch: $OUT ready: FARM_GAME=$OUT tests/s5/farm_boot.sh <c12> <out>"
