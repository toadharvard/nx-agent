#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Produce the Remote Hekate source tree: upstream hekate at the pinned commit,
# plus patches/*.patch, plus the new files under src/.
#
# Usage: scripts/fetch-and-apply.sh [DEST]   (default: <remote-hekate>/hekate)
# Env:   HEKATE_UPSTREAM_URL  clone from this URL or local path instead of the pinned url.
#
# Idempotent: tracked files of an existing DEST are reset to the pinned commit
# (local edits there are discarded), files from src/ are replaced, then patches
# are applied again. Untracked build outputs (build/, output/) are kept.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:-"$root/hekate"}

pin() { sed -n "s/^$1=//p" "$root/UPSTREAM"; }
url=${HEKATE_UPSTREAM_URL:-$(pin url)}
tag=$(pin tag)
commit=$(pin commit)

if [ ! -d "$dest/.git" ]; then
	git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$url" "$dest"
fi

if ! git -C "$dest" cat-file -e "$commit^{commit}" 2>/dev/null; then
	git -C "$dest" fetch --quiet --depth 1 origin "refs/tags/$tag:refs/tags/$tag"
fi

tag_commit=$(git -C "$dest" rev-parse "refs/tags/$tag^{commit}")

if [ "$tag_commit" != "$commit" ]; then
	echo "fetch-and-apply: tag $tag is $tag_commit, UPSTREAM pins $commit" >&2
	exit 1
fi

git -C "$dest" -c advice.detachedHead=false checkout --quiet --force "$commit"
git -C "$dest" reset --quiet --hard "$commit"

head=$(git -C "$dest" rev-parse HEAD)

if [ "$head" != "$commit" ]; then
	echo "fetch-and-apply: HEAD is $head, expected $commit" >&2
	exit 1
fi

(cd "$root/src" && find . -type f) | while read -r f; do
	rm -f "$dest/$f"
done

for p in "$root"/patches/*.patch; do
	git -C "$dest" apply --whitespace=error-all "$p"
done

(cd "$root/src" && find . -type f) | while read -r f; do
	mkdir -p "$dest/$(dirname "$f")"
	cp "$root/src/$f" "$dest/$f"
done

echo "fetch-and-apply: $dest = hekate $tag ($commit) + $(ls "$root"/patches/*.patch | wc -l | tr -d ' ') patches + src/"
