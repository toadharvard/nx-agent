#!/usr/bin/env bash
# link_job.sh - farm job: the link by address of one commit (S2c's sequence: fnvlink resolve, probe, resolve, apply,
# trial link, check_rebind) on the prepare farm's tree and the compile farms' objects, published for the fuzz farms.
#
#   tools/farm/link_job.sh <commit> <report.md>          (farm machines: tools/farm/runner.sh <name> link)
#
# Inputs, under $FARM_HOME (default ~/fnvnx-farm), never inside the repository:
#   tools      the commit as a detached worktree src-<commit12> (FARM_SRC=<checkout at <commit>> instead)
#   ext/       the pinned checkouts (tools/fetch.sh; FARM_EXT=<dir> uses existing ones read only)
#   tree       the prepare farm's tree, branch t/<commit12> (tools/farm/fetch_tree.sh, waited for up to
#              $FARM_TREE_WAIT s, default 1800; FARM_TREE=<dir> instead)
#   objects    the compile farms' objects: the compile farms each take the newest head when they get to it, so the
#              objects linked are those of the newest commit C that is an ancestor of <commit> (or <commit>
#              itself), has all $FARM_OBJ_SHARDS (default 4) shards c/<C12>/<i>of4 and the same compile inputs
#              as <commit> (prepare inputs, render/native/drop_units.txt, tools/link/compile.sh, shim, fetch.sh),
#              looked for $FARM_OBJ_DEPTH (default 300) commits back and waited for up to $FARM_OBJ_WAIT s
#              (default 3600); tools/farm/fetch_objs.sh checks that they were compiled from this tree (tree hash).
#              The report and meta.json (objects_commit) name C. FARM_OBJS=<objects.tsv> instead.
# The link runs through the pipeline (tools/pipeline/build.py `make link`, docs/BUILD.md) in the persistent build
# directory link/build, so fnvlink's demangle, layout and vtable caches carry over between commits (each dropped
# where its inputs changed). It needs only the AArch64 Linux cross tools (the host's when they are the pinned Ubuntu
# build, else toolchain/cross/setup.sh unpacks them, no root): no fnvnx-a64, no MSVC. The probe runs with
# JOBS (default nproc) parallel compiles; the report gives every step's time (layout probe and vtable synthesis
# apart). The extern-text list is made from the tree (tools/pipeline/extern_text.py). S11's pristine MSVC objects
# (branch msvc/pristine/<decomp12>, fetched once; PRISTINE_OBJS=<dir> instead) go to both resolves as
# --msvc-code (S2c: ecx-not-passed / this-not-passed judged on the definitions' code; needs python capstone);
# without them the verdicts are as before.
#
# Published when the link is complete and prepared from <commit> (FARM_PUBLISH=always also publishes a UNITS
# subset, for tests against a local ARTIFACTS remote; 0 never), as branch link/<commit12> of the private artifacts
# repository toadharvard/fnv-nx-build (branches only: the session proxy refuses tags and ref deletions):
#   out/{aliases.ld,refs.tsv,abi.tsv,addrmap.tsv,layouts.tsv,typeinfo_pairs.tsv,cache/msvc.tsv,vtables/}
#   (vtables/ without the probe work dir; typeinfo_pairs.tsv from apply, in check's link order)
#   fixed/*.o   summary.txt   report.md   meta.json
# The rolling branch with the history of every publish is link-head: a branch named `link` cannot exist next to
# link/<commit12> (git refs are paths). Each publish is a child of link-head's newest commit, so a push sends only
# what changed. The report holds the summary numbers only. Exit 0 once the report is written.
#
# Needs: git python3 make curl jq rsync dpkg-deb gcc llvm-18 (llvm-undname); python pyelftools; FARM_APT=1
# installs missing ones (root). $ARTIFACTS: a checkout of toadharvard/fnv-nx-build with push (runner.sh exports it).
# FARM_MAKE_ARGS: extra make variables (e.g. "UNITS=<list>" for a quick check of the job; such a link is not
# published). LINK_BUILD: another build directory (full_job.sh links in its own).
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
export ARTIFACTS FARM_HOME
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
# shellcheck source=lib_job.sh
. "$(dirname "$0")/lib_job.sh"
t0=$(date +%s)

host_check git python3 make curl jq rsync dpkg-deb gcc llvm-undname py:elftools
python3 -I -c 'import capstone' 2>/dev/null || [ "${FARM_APT:-0}" != 1 ] || python3 -m pip install -q --break-system-packages capstone
job_sources "$REPO"
LB=${LINK_BUILD:-$FARM_HOME/link/build}
job_inputs "$(dirname "$LB")"
job_pristine
log "tree: $NOTE_TREE; objects: $NOTE_OBJS; $NOTE_PR"
printf 'TREE=%q\nOBJS=%q\nOBJ_COMMIT=%q\nNOTE_TREE=%q\nNOTE_OBJS=%q\n' "$TREE" "$OBJS" "$OBJ_COMMIT" "$NOTE_TREE" "$NOTE_OBJS" \
	> "$(dirname "$LB")/inputs-$C12.env"     # for full_job.sh, which builds on this link
ARGS=(TC_DL=none VORBIS=none)          # the link needs only the cross tools, not fnvnx-a64
# S2c's resolve --msvc-code: ecx-not-passed / this-not-passed judged on the definitions' MSVC code (capstone)
[ -n "$PR" ] && ARGS+=(MSVC_CODE="$PR")
[ -n "$TREE" ] && ARGS+=(PREPARED="$TREE")
[ -n "$OBJS" ] && ARGS+=(OBJECTS="$OBJS")
rc=0
if [ -z "$TREE" ] || [ -z "$OBJS" ]; then
	rc=2; NOTE_FAIL="no tree or no objects from the farms: nothing linked"
else
	# A guest or boot of an earlier job must never be published with this link (V1 against J-0125: mtime order fails
	# on a clock step, and a stage that fails half-way leaves old files): drop them; full_job.sh's make BOOT=1 rebuilds
	rm -rf "$LB/guest" "$LB/boot"
	# shellcheck disable=SC2086
	make -C "$P" link BUILD_DIR="$LB" EXT="$EXT" JOBS="$JOBS" "${ARGS[@]}" ${FARM_MAKE_ARGS:-} \
		> "$(dirname "$LB")/make-link-$C12.log" 2>&1 || rc=$?
	ls -1t "$(dirname "$LB")"/make-link-*.log | tail -n +6 | xargs -r rm -f
	NOTE_FAIL=""
fi
t1=$(date +%s)

# Report: the summary numbers.
python3 -I - "$LB" "$COMMIT" "$rc" $((t1 - t0)) "$NOTE_TREE" "$NOTE_OBJS" "$NOTE_FAIL" "$NOTE_PR" > "$REPORT" <<'EOF'
import json, os, sys
bd, commit, rc, secs, tree, objs, fail, pristine = sys.argv[1:9]
def rec(s):
    try:
        return json.load(open(os.path.join(bd, s, 'stage.json')))
    except (OSError, ValueError):
        return {}
L = ['# link %s' % commit[:12], '', '- commit: `%s`; make exit %s; %.0f min' % (commit, rc, int(secs) / 60.0),
     '- tree: ' + tree, '- objects: ' + objs, '- resolve --msvc-code: ' + pristine]
if fail:
    L.append('- ' + fail)
pf = rec('prepare').get('prepared_from') or {}
L.append('- tree prepared from `%s` (%s)%s' % (pf.get('port_commit') or 'unknown', pf.get('mode') or '-',
                                              '; STALE' if rec('link').get('stale') else ''))
for s in ('toolchain', 'prepare', 'compile', 'link'):
    r = rec(s)
    if r:
        L.append('- %s: %s%s' % (s, r.get('status'), (', ' + r['error'][:200]) if r.get('error') else ''))
m = rec('link').get('metrics', {})
if m:
    g = lambda k: m.get('link_' + k)
    L += ['', '## Link', '',
          '- objects %s, references %s, by address %s (%s %%), bridges %s, ABI manual %s, x86-inconsistent %s' % (
              g('objects'), g('refs'), g('resolved'), g('resolved_pct'), g('bridges'), g('abi_manual'),
              g('abi_x86_inconsistent')),
          '- address map: ' + ' '.join('%s=%s' % (k[-1], v) for k, v in sorted(m.items()) if k.startswith('link_addrmap_')),
          '- trial link: %s objects, %s undefined, %s multiple definitions; split-global check %s objects / %s '
          'relocations (want 0); extern-text addresses %s; apply failures %s' % (
              g('check_objects'), g('check_undefined'), g('check_multidef'), g('bypassing_objects'),
              g('bypassing_relocs'), g('extern_text_addrs'), g('apply_failures')),
          '- seconds: resolve 1 %s, probe %s with %s jobs (layouts %s: %s units / %s types probed, %s rows cached, %s '
          'dropped for changed units; vtables %s: %s rounds, %s host unit probes), resolve 2 %s, apply %s, trial '
          'link %s' % (
              g('resolve1_s'), g('probe_s'), g('probe_jobs'), g('probe_layouts_s'), g('probe_units'), g('probe_types'),
              g('probe_cached'), g('layouts_dropped'), g('probe_vtables_s'), g('vtable_rounds'),
              g('vtable_host_probes'), g('resolve2_s'), g('apply_s'), g('check_s'))]
    cc = rec('link').get('check_classes') or {}
    if cc:
        L += ['', '| trial link: undefined names by class | names |', '|---|---|']
        L += ['| %s | %s |' % (k, v) for k, v in sorted(cc.items(), key=lambda x: -x[1])]
print('\n'.join(L))
EOF
if [ $rc -ne 2 ]; then publish_link "$LB"; else NOTE_PUB="not published"; fi
echo "- link outputs: $NOTE_PUB" >> "$REPORT"
echo "$REPORT"
