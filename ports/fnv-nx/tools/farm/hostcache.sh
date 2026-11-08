#!/usr/bin/env bash
# hostcache.sh - a fresh farm machine takes its toolchain and pinned checkouts from the artifacts repository instead
# of building and cloning them (T1, orchestrator: toolchain + sources cost 30 min per fresh machine; bl1 cold:
# sources 735 s, toolchain 1,082 s).
#
#   hostcache.sh fetch   <name> <key> <dir>   restore <dir> from branch hostcache/<name>-<key>; exit 1 when there is
#                                             none (nothing changed), 0 when <dir> was restored
#   hostcache.sh publish <name> <key> <dir> [--exclude <sub> ...]
#                                             pack <dir> into that branch when it does not exist yet; never fails the
#                                             caller (exit 0, a line on stderr)
#
# The branch is one orphan commit: <dir>'s tar (gzip -1), split into parts of HOSTCACHE_PART_MB (default 95; GitHub
# refuses files over 100 MB), plus manifest.json (name, key, the directory's path, the parts' sha256). <dir> is
# restored at the same absolute path it was packed from (a toolchain's build ids and devkitPro's rules name it;
# every farm has FARM_HOME=/root/fnvnx-farm), and only when the path matches: another path is a miss. A part whose
# sha256 differs is a miss too. The key is the caller's: it must cover every input of <dir>
# (full_job.sh: the toolchain's TCID; lib_job.sh: the pins in tools/fetch.sh). $ARTIFACTS: a clone of
# fnv-nx-build with push. HOSTCACHE=0 (in the callers): neither. Everything stays in the private repository.
set -uo pipefail
CMD=${1:?fetch|publish}; NAME=${2:?name}; KEY=${3:?key}; DIR=${4:?dir}; shift 4
: "${ARTIFACTS:?ARTIFACTS: a clone of the artifacts repository}"
B=hostcache/$NAME-$KEY
PART_MB=${HOSTCACHE_PART_MB:-95}
DIR=$(realpath -m "$DIR")
log() { echo "hostcache: $*" >&2; }
work=$(mktemp -d "${TMPDIR:-/tmp}/hostcache.XXXXXX"); trap 'rm -rf "$work"' EXIT

case "$CMD" in
fetch)
	git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/$B:refs/remotes/origin/$B" 2>/dev/null || exit 1
	git -C "$ARTIFACTS" archive "refs/remotes/origin/$B" | tar -x -C "$work" || { log "$B: unreadable"; exit 1; }
	want=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["path"])' "$work/manifest.json" 2>/dev/null)
	[ "$want" = "$DIR" ] || { log "$B was packed from $want, not $DIR: not used"; exit 1; }
	python3 -I - "$work" <<'EOF' || { log "$B: a part's sha256 differs: not used"; exit 1; }
import hashlib, json, os, sys
w = sys.argv[1]
m = json.load(open(os.path.join(w, 'manifest.json')))
for p, h in m['parts']:
    if hashlib.sha256(open(os.path.join(w, p), 'rb').read()).hexdigest() != h:
        sys.exit(1)
EOF
	rm -rf "$DIR.hostcache"; mkdir -p "$DIR.hostcache"
	if cat "$work"/part.* | tar -xz -C "$DIR.hostcache"; then
		rm -rf "$DIR"; mv "$DIR.hostcache/$(basename "$DIR")" "$DIR"; rm -rf "$DIR.hostcache"
		log "$DIR restored from $B"; exit 0
	fi
	rm -rf "$DIR.hostcache"; log "$B: the tar did not unpack"; exit 1 ;;
publish)
	[ -d "$DIR" ] || { log "publish: no $DIR"; exit 0; }
	if git -C "$ARTIFACTS" ls-remote --exit-code origin "refs/heads/$B" > /dev/null 2>&1; then exit 0; fi
	ex=()
	while [ $# -gt 0 ]; do [ "$1" = --exclude ] && { ex+=(--exclude="$(basename "$DIR")/$2"); shift; }; shift; done
	tar -C "$(dirname "$DIR")" -c "${ex[@]}" "$(basename "$DIR")" | gzip -1 | split -b "${PART_MB}m" -d -a 3 - "$work/part." \
		|| { log "publish: tar of $DIR failed"; exit 0; }
	python3 -I - "$work" "$NAME" "$KEY" "$DIR" <<'EOF'
import hashlib, json, os, sys
w, name, key, path = sys.argv[1:5]
parts = sorted(p for p in os.listdir(w) if p.startswith('part.'))
json.dump({'schema': 'fnvnx-hostcache/1', 'name': name, 'key': key, 'path': path,
           'parts': [[p, hashlib.sha256(open(os.path.join(w, p), 'rb').read()).hexdigest()] for p in parts]},
          open(os.path.join(w, 'manifest.json'), 'w'), indent=1)
EOF
	g=$work/repo; git init -q "$g" && mv "$work"/part.* "$work/manifest.json" "$g/" && git -C "$g" add -A &&
		git -C "$g" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "hostcache $NAME $KEY ($(du -sh "$g" | cut -f1))" \
		|| { log "publish: commit failed"; exit 0; }
	for i in 1 2 3; do
		git -C "$g" push -q "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/$B" 2>/dev/null && { log "published $B"; exit 0; }
		git -C "$ARTIFACTS" ls-remote --exit-code origin "refs/heads/$B" > /dev/null 2>&1 && exit 0   # another farm did
		sleep $((i * 10))
	done
	log "publish: push of $B failed"; exit 0 ;;
*) echo "usage: hostcache.sh fetch|publish <name> <key> <dir>" >&2; exit 2 ;;
esac
