#!/bin/sh
# fetch_freetype.sh <dir-with-the-two-tarballs> [dest]
#
# Unpacks the reference environment's FreeType (Ubuntu 24.04: freetype 2.13.2+dfsg-1ubuntu0.2) into ext/freetype
# (git-ignored, never committed) and applies debian/patches/series in order. The directory holds
#   freetype_2.13.2+dfsg.orig.tar.xz  freetype_2.13.2+dfsg-1ubuntu0.2.debian.tar.xz
# Rule for the patches: every hunk is applied; the only hunks left out are those whose target path is under
# ft2docs/ - that tree is a separate orig component of the Debian source package (the HTML documentation) which is not
# in the +dfsg orig tarball and is not part of any compiled object. A hunk that does not apply to a source file is an
# error (set -e), so the result is the Debian source tree or the script fails.
# The same result for the same inputs: the destination is removed first.
set -eu
src=${1:?usage: fetch_freetype.sh <dir-with-the-two-tarballs> [dest]}
here=$(cd "$(dirname "$0")" && pwd)
dest=${2:-$here/../../../ext/freetype}
orig=$src/freetype_2.13.2+dfsg.orig.tar.xz
deb=$src/freetype_2.13.2+dfsg-1ubuntu0.2.debian.tar.xz
test -f "$orig" && test -f "$deb" || { echo "fetch_freetype.sh: tarballs missing in $src" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
tar -C "$tmp" -xf "$orig"
tar -C "$tmp" -xf "$deb"
tree=$(ls -d "$tmp"/freetype-*/ | head -n 1)
while read -r p; do
    case "$p" in ''|'#'*) continue;; esac
    # drop the file sections under ft2docs/, keep the rest of the patch byte for byte
    awk 'BEGIN{skip=0} /^--- /{skip = ($2 ~ /^a\/ft2docs\//)} /^diff /{skip = ($3 ~ /^a\/ft2docs\//)} !skip{print}' \
        "$tmp/debian/patches/$p" > "$tmp/one.patch"
    # a patch that touched only ft2docs/ is empty now: nothing of it exists in the tree
    grep -q '^+++ ' "$tmp/one.patch" || continue
    patch -d "$tree" -p1 --no-backup-if-mismatch -s < "$tmp/one.patch"
done < "$tmp/debian/patches/series"
rm -rf "$dest"
mkdir -p "$(dirname "$dest")"
mv "$tree" "$dest"
echo "freetype: $(basename "$orig") + $(basename "$deb") -> $dest"
