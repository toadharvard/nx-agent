#!/usr/bin/env bash
# linkcache.sh - the link's compile cache (tools/link/objcache.py) shared through the artifacts repository (T1).
#
#   tools/farm/linkcache.sh seed <cache dir>      a cache with fewer than LINKCACHE_SEED_MIN (500) results is filled
#                                                 from branch linkobj/cache (content-addressed: merging never clashes)
#   tools/farm/linkcache.sh publish <cache dir>   prune to LINKCACHE_PUBLISH_MB (2000) and push the cache as one
#                                                 orphan commit of linkobj/cache when it changed since the last publish
#
# A fresh farm's first link is otherwise cold (~45 min on 4 cores: leq1's first head, every new container); the keys
# hold the compiler's bytes, the command lines verbatim and every dependency's sha256, so an entry made on another
# machine is used only where all of those are the same (the farms share $FARM_HOME's layout and the pinned cross
# toolchain). Orphan commits: no history piles up; a push sends only the blobs the old tip does not have.
# The cache holds objects compiled from the decomp: the artifacts repository only, never nx-agent.
# $ARTIFACTS: a checkout of toadharvard/fnv-nx-build (default /home/user/fnv-nx-build, else $FARM_HOME/artifacts).
set -uo pipefail
CMD=${1:?seed|publish}; DIR=${2:?cache dir}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
BR=${LINKCACHE_BRANCH:-linkobj/cache}
log() { echo "$(date -u +%FT%TZ) linkcache: $*"; }
count() { find "$DIR/r" -name '*.json' 2>/dev/null | wc -l; }
[ -d "$ARTIFACTS/.git" ] || { log "no artifacts checkout at $ARTIFACTS: nothing to $CMD"; exit 0; }
case "$CMD" in
seed)
	n=$(count)
	if [ "$n" -ge "${LINKCACHE_SEED_MIN:-500}" ]; then log "$n results here: not seeded"; exit 0; fi
	if ! git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/$BR:refs/remotes/origin/$BR" 2>/dev/null; then
		log "no $BR yet: the cache fills from this machine's links"; exit 0
	fi
	mkdir -p "$DIR"
	# files this cache has already stay as they are (content-addressed results; a manifest here may list more variants)
	git -C "$ARTIFACTS" archive "refs/remotes/origin/$BR" | tar -x --skip-old-files -C "$DIR" 2>/dev/null
	log "seeded from $BR: $n -> $(count) results ($(git -C "$ARTIFACTS" log -1 --format=%s "refs/remotes/origin/$BR"))"
	;;
publish)
	[ -d "$DIR/r" ] || { log "no cache at $DIR"; exit 0; }
	python3 -I "$P/tools/link/objcache.py" prune "$DIR" "${LINKCACHE_PUBLISH_MB:-2000}" || true
	now=$(count); stamp=$DIR/.published
	if [ "$(cat "$stamp" 2>/dev/null)" = "$now" ]; then log "unchanged since the last publish ($now results)"; exit 0; fi
	idx=$(mktemp); rm -f "$idx"
	# only the cache's own files (results r/, manifests m/), never the stamp or temporaries
	(cd "$DIR" && find m r -type f ! -name '*.tmp' 2>/dev/null | LC_ALL=C sort) > "$idx.list"
	(cd "$DIR" && GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$DIR" -c core.autocrlf=false \
		update-index --add --stdin < "$idx.list") || { log "index failed"; rm -f "$idx" "$idx.list"; exit 0; }
	tree=$(GIT_INDEX_FILE=$idx git -C "$ARTIFACTS" write-tree); rm -f "$idx" "$idx.list"
	# the old tip, so the push sends only the new blobs
	git -C "$ARTIFACTS" fetch -q --depth 1 --filter=blob:none origin "+refs/heads/$BR:refs/remotes/origin/$BR" 2>/dev/null || true
	commit=$(git -C "$ARTIFACTS" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit-tree "$tree" \
		-m "link compile cache: $now results (${LINKCACHE_NOTE:-farm})")
	for i in 1 2 3; do
		if git -C "$ARTIFACTS" push -q -f origin "$commit:refs/heads/$BR" 2>/dev/null; then
			echo "$now" > "$stamp"; log "published $now results to $BR"; exit 0
		fi
		sleep $((i * 10))
	done
	log "push of $BR failed (kept here; the next link publishes again)"
	;;
*) echo "linkcache.sh: seed|publish <cache dir>" >&2; exit 2 ;;
esac
exit 0
