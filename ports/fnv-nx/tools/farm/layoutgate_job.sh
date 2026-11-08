#!/usr/bin/env bash
# layoutgate_job.sh - farm job: the LAYOUT gate (tools/link/layoutgate.py) over the whole prepared tree of one commit.
#
#   tools/farm/layoutgate_job.sh <commit> <report.md>
#   (runner.sh: `runner.sh <farm> layoutgate`)
#
# Every class / struct of the prepared tree laid out by MSVC's rules (clang i686-pc-windows-msvc record dumps) and by
# the native compiler (GCC AArch64 ILP32 probes, compile.sh's flags), compared field by field, plus the member check
# (pack / ebo records placed inside other records). The prepared tree is the published one (fetch_tree.sh: branch
# t/<c12> of the private artifacts repository, as the fuzz farms take it). Writes <report.md> (the summary in its
# first lines; runner.sh publishes it to reports/<farm>) and pushes <c12>.tsv and <c12>.md to branch
# reports/layoutgate of the artifacts repository. Without a published tree it prepares one itself (step 2). The boot set (column boot) is the home units of the newest
# s2d/list/*/topdown.tsv there plus the units of the exe functions on its call chains; without one, boot is 0.
# Rows carry class and member names, offsets and sizes only (no source). Needs: git, python3, rsync, clang++ (installed
# with apt-get when missing and root), g++-aarch64-linux-gnu.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
C12=${COMMIT:0:12}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=${LAYOUTGATE_REPO:-$(cd "$HERE/../../../.." && pwd)}
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
EXT=$FARM_HOME/ext
WORK=$FARM_HOME/layoutgate-$C12
if [ -z "${ARTIFACTS:-}" ]; then
  if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"

# 1. The port's tools at that commit (a detached worktree outside the main checkout), sse2neon for the native flags.
SRC=$FARM_HOME/src-$C12
bash "$HERE/worktree.sh" "$REPO" "$SRC" "$COMMIT" >/dev/null
P=$SRC/ports/fnv-nx
# the commit's own job script: runner.sh runs the farm checkout's copy, which may be older than <commit> (5e94e04e5a61
# ran a copy without the recon-set tree check and took a tree prepared before three recons existed)
if [ -z "${LAYOUTGATE_REEXEC:-}" ] && [ -f "$P/tools/farm/layoutgate_job.sh" ] && ! cmp -s "$0" "$P/tools/farm/layoutgate_job.sh"; then
  LAYOUTGATE_REEXEC=1 LAYOUTGATE_REPO=$REPO exec bash "$P/tools/farm/layoutgate_job.sh" "$@"
fi
bash "$P/tools/fetch.sh" "$EXT" >/dev/null
command -v clang++ >/dev/null || { [ "$(id -u)" = 0 ] && apt-get install -y -qq clang >/dev/null; } || true
command -v clang++ >/dev/null || { echo "- FAIL: clang++ is missing (apt-get install clang)" > "$REPORT"; exit 1; }

# 2. The published prepared tree, or (LAYOUTGATE_LOCAL_PREPARE=1, the default) one prepared here when t/<c12> does not
#    appear within LAYOUTGATE_TREE_WAIT seconds (default 120): the commit's own prepare.py over the decomp its pins
#    fetch, with the artifacts repository's recon/* set (recon.sh), as prepare_job.sh would prepare it (about 10 min).
#    LAYOUTGATE_LOCAL_PREPARE=0 waits TREE_WAIT (default 1800) for a published tree instead.
rm -rf "$WORK"; mkdir -p "$WORK"
TREE_HOW=published
# shellcheck source=recon.sh
. "$HERE/recon.sh"
fetch_recon "$ARTIFACTS" "$WORK/recon" > /dev/null 2>&1 || true
if [ "${LAYOUTGATE_LOCAL_PREPARE:-1}" = 1 ]; then
  if ! TREE_HASH=$(FETCH_TREE_RECON=${RECON_ID:-} bash "$HERE/fetch_tree.sh" "$COMMIT" "$WORK/tree" \
                   "${LAYOUTGATE_TREE_WAIT:-120}" 2> "$WORK/fetch_tree.log"); then
    tp0=$(date +%s)
    rm -rf "$WORK/tree"
    python3 "$P/tools/prepare.py" "$EXT/fnv_decomp" "$WORK/tree" > "$WORK/prepare_local.log" 2>&1 \
      || { echo "- FAIL: no published tree t/$C12, and prepare.py here failed (exit $?, prepare_local.log)" > "$REPORT"; exit 1; }
    TREE_HASH=$(bash "$HERE/tree_hash.sh" "$WORK/tree")
    TREE_HOW="prepared here, $(( $(date +%s) - tp0 )) s, recon ${RECON_ID:-none}"
  fi
else
  TREE_HASH=$(bash "$HERE/fetch_tree.sh" "$COMMIT" "$WORK/tree" "${TREE_WAIT:-1800}")
fi

# 3. The boot set, from the newest topdown.tsv of the s2d/list/* branches (optional).
BOOT=$WORK/boot_units.txt; : > "$BOOT"
git -C "$ARTIFACTS" fetch -q origin '+refs/heads/s2d/list/*:refs/remotes/origin/s2d/list/*' 2>/dev/null || true
LIST=$(git -C "$ARTIFACTS" for-each-ref --sort=-committerdate --format='%(refname)' 'refs/remotes/origin/s2d/list/' | head -1)
if [ -n "$LIST" ] && git -C "$ARTIFACTS" show "$LIST:topdown.tsv" > "$WORK/topdown.tsv" 2>/dev/null; then
  python3 - "$WORK/tree/build/units.json" "$WORK/topdown.tsv" "$BOOT" <<'EOF'
import json, re, sys
by = {}
for u in json.load(open(sys.argv[1]))['units']:
    for f in u['functions']:
        by.setdefault(int(f['address'], 16), u['source'])
units = set()
for line in open(sys.argv[2]):
    if line.startswith('#'):
        continue
    f = line.rstrip('\n').split('\t')
    if len(f) > 8 and f[8]:
        units.add(f[8])
    units.update(by[int(a, 16)] for a in re.findall(r'__fnv_([0-9a-f]{8})', line) if int(a, 16) in by)
open(sys.argv[3], 'w').write(''.join(u + '\n' for u in sorted(units) if u.endswith('.cpp')))
EOF
fi

# 4. The gate.
t0=$(date +%s)
SSE2NEON=$EXT/sse2neon python3 "$P/tools/link/layoutgate.py" --tree "$WORK/tree" --boot "$BOOT" --jobs "$JOBS" \
  -o "$WORK/$C12.tsv" --workdir "$WORK/probe" > "$WORK/layoutgate.log" 2>&1
t1=$(date +%s)
SUM=$(cat "$WORK/$C12.tsv.summary")

# 5. Report: the summary first, then the counts by cause and the boot-set rows.
{
  echo "# layoutgate $C12"
  echo
  echo "- $SUM"
  echo "- commit: \`$COMMIT\`, tree $TREE_HASH ($TREE_HOW, recon ${RECON_ID:-none}), $((t1-t0)) s, jobs $JOBS, boot units $(wc -l < "$BOOT") ($(basename "${LIST:-none}"))"
  echo "- MSVC side: clang $(clang++ --version | head -1 | grep -o '[0-9][0-9.]*' | head -1) i686-pc-windows-msvc record layouts"
  echo
  echo "## Rows by cause (size / field / member rows)"; echo
  awk -F'\t' 'NR>2 && ($3=="size" || $3 ~ /^field/ || $3 ~ /^member/) {n[$6]++} END {for (c in n) printf "- %s: %d\n", c, n[c]}' \
    "$WORK/$C12.tsv" | sort -t: -k2,2nr
  echo; echo "## Boot-set rows"; echo
  awk -F'\t' 'NR>2 && $7==1 {printf "- `%s` %s: msvc %s, native %s (%s) - %s\n", $2, $3, $4, $5, $6, $1}' "$WORK/$C12.tsv" | head -200
} > "$REPORT"
cp "$REPORT" "$WORK/$C12.md"

# 6. Publish <c12>.tsv / <c12>.md to reports/layoutgate.
WT=$FARM_HOME/layoutgate-wt; RB=reports/layoutgate
git -C "$ARTIFACTS" worktree prune
if [ ! -e "$WT/.git" ]; then
  rm -rf "$WT"
  if git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$RB:refs/remotes/origin/$RB" 2>/dev/null; then
    git -C "$ARTIFACTS" worktree add -q -f -B "$RB" "$WT" "origin/$RB"
  else
    git -C "$ARTIFACTS" worktree add -q --detach "$WT" && git -C "$WT" checkout -q --orphan "$RB" && git -C "$WT" rm -rqf . 2>/dev/null || true
  fi
fi
for i in 1 2 3; do
  git -C "$WT" fetch -q origin "+refs/heads/$RB:refs/remotes/origin/$RB" 2>/dev/null && git -C "$WT" reset -q --hard "origin/$RB" || true
  cp "$WORK/$C12.tsv" "$WORK/$C12.md" "$WT/"
  git -C "$WT" add "$C12.tsv" "$C12.md"
  git -C "$WT" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "layoutgate $C12: $SUM" || true
  git -C "$WT" push -q origin "HEAD:refs/heads/$RB" 2>/dev/null && break
  sleep $((i * 5))
done
echo "$REPORT"
