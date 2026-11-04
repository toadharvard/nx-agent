#!/usr/bin/env bash
# gamedata_publish.sh - put Data/ items of the player's install on the PRIVATE artifacts repository, one orphan branch
# per item, so guests and farms can boot on real data (tools/gamedata_fetch.sh assembles them again). Game data:
# never into nx-agent (docs/ARCHITECTURE.md, Legal).
#
#   tools/gamedata_publish.sh <install dir> <item>...      e.g. FalloutNV.esm "Fallout - Misc.bsa" Music
#
# An item is a file or directory under <install>/Data (a directory is stored as one tar). Branch:
# gamedata/data/<slug> (the name with ' ' -> '_'). It holds <slug>.partNN (at most GAMEDATA_PART_MB, default 95
# MiB each, under GitHub's 100 MB file limit) and MANIFEST.tsv:
#   name <real name> / kind file|tar / size <bytes> / sha256 <whole item, or the tar> / part <file> <bytes> <sha256>
# One push per item (the largest Data/ file is about 1.6 GB, under the 2 GB push limit); an item whose branch exists
# with the same sha256 is skipped. Env: ARTIFACTS_URL (default the private repository), WORK (default
# $HOME/gamedata-publish, the staging clone; must be outside any git checkout).
set -euo pipefail
INSTALL=${1:?install dir}; shift
[ $# -gt 0 ] || { echo "usage: $0 <install dir> <item>..." >&2; exit 2; }
URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
WORK=${WORK:-$HOME/gamedata-publish}
PART=$(( ${GAMEDATA_PART_MB:-95} * 1024 * 1024 ))
DATA=$(cd "$INSTALL/Data" && pwd)
mkdir -p "$WORK"; WORK=$(cd "$WORK" && pwd)
if top=$(git -C "$(dirname "$WORK")" rev-parse --show-toplevel 2>/dev/null); then
  echo "gamedata_publish: $WORK is inside the git checkout $top" >&2; exit 2
fi
for item in "$@"; do
  src=$DATA/$item
  [ -e "$src" ] || { echo "gamedata_publish: no $src" >&2; exit 1; }
  slug=${item// /_}; branch=gamedata/data/$slug
  st=$WORK/$slug; mkdir -p "$st"
  rm -f "$st"/*.part* "$st/MANIFEST.tsv" "$st/item.tar"
  if [ -d "$src" ]; then
    kind=tar; tar -C "$DATA" --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf "$st/item.tar" "$item"
    whole=$st/item.tar
  else
    kind=file; whole=$src
  fi
  sum=$(sha256sum "$whole" | cut -c1-64); size=$(stat -L -c %s "$whole")
  if have=$(git ls-remote "$URL" "refs/heads/$branch" | cut -f1) && [ -n "$have" ]; then
    if git -C "$st" init -q 2>/dev/null && git -C "$st" fetch -q --depth 1 "$URL" "refs/heads/$branch" &&
       git -C "$st" show FETCH_HEAD:MANIFEST.tsv 2>/dev/null | grep -q "^sha256	$sum$"; then
      echo "$(date +%T) $item: $branch already has sha256 $sum, skipped"; rm -rf "$st"; continue
    fi
  fi
  rm -rf "$st/.git"
  split -b "$PART" -d -a 2 "$whole" "$st/$slug.part"
  rm -f "$st/item.tar"
  {
    printf 'name\t%s\nkind\t%s\nsize\t%s\nsha256\t%s\n' "$item" "$kind" "$size" "$sum"
    for p in "$st/$slug".part*; do printf 'part\t%s\t%s\t%s\n' "$(basename "$p")" "$(stat -c %s "$p")" "$(sha256sum "$p" | cut -c1-64)"; done
  } > "$st/MANIFEST.tsv"
  git -C "$st" init -q; git -C "$st" checkout -q --orphan "$branch"
  git -C "$st" add -A
  git -C "$st" -c user.name=Claude -c user.email=noreply@anthropic.com commit -q -m "gamedata/data: $item ($size bytes, $(grep -c '^part' "$st/MANIFEST.tsv") parts)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  for try in 1 2 3 4; do
    git -C "$st" push -q "$URL" "$branch:refs/heads/$branch" && break
    [ $try = 4 ] && { echo "gamedata_publish: push of $branch failed" >&2; exit 1; }
    sleep $((2 ** try))
  done
  echo "$(date +%T) $item: pushed $branch ($size bytes, sha256 $sum)"
  rm -rf "$st"
done
