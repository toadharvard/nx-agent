#!/usr/bin/env bash
# reccert_job.sh - L4 farm job: recertify the reconstructions (recon/* branches of the artifacts repository) for the
# rule set of one commit (REC-CERT, tools/rules/reccert.py).
#
#   FNVNX_PREPARE_SKIP=<rules or empty> FARM_HOME=... ARTIFACTS=... bash <checkout>/ports/fnv-nx/farm/reccert_job.sh <commit> <report.md>
#
# Two roots as cltree_job.sh: FARM_SCRIPTS (this repository: tools/farm/lib_job.sh) and SRC (the commit's checkout:
# $RECCERT_SRC at <commit>, else the stable worktree $FARM_HOME/reccert-src after fetching $RECCERT_BRANCH, default
# claude/fnv-l2-source); every tool the job runs comes from SRC. Steps:
#  1 sources, H = reccert.py hash (under the SKIP), Wine + VC9 (equiv_job.sh --toolchain), the player's exe (job_gamedata)
#  2 recon set R: recon.sh fetch_recon (every recon/* branch to R/<name>); map recon.json -> branch, tip, blob
#  3 candidate tree $FARM_HOME/reccert-<c12>/tree: prepare.py with FNVNX_RECON=R FNVNX_RECON_CERTIFY=1 and the SKIP. tree.id =
#    commit, decomp commit, SKIP, CERTIFY=1, hash of R, FNVNX_* environment, tree hash: reused only when all are equal; no
#    other tree (compile_job's, published) is ever adopted
#  4 U = the `unit` of every fnvnx-recon/1 recon.json; tools/link/compile.sh on exactly U with FNVNX_SDD=1 -> objects.tsv
#  5 decomp tools/build_and_match.py (MSVC, the exe) on exactly U, run from a hard-link copy of the candidate tree plus the
#    decomp's tools/ (it takes its ROOT from its own path), with the cl flags equiv.py gives a prepared tree; the one
#    result is split into <match>/<unit with / -> _>.json
#  6 reccert.py stamp R --objects --match --report cert.tsv (its rule set must equal H)
#  7 publish: each changed recon.json on top of the tip of its own recon/* branch (ordinary push; the blob at the tip must be
#    the one fetched), then reports/reccert/<c12> (cert.tsv, meta.json); 8 report.md
# Nothing is certified by absence: a build_and_match entry that is not ok / mismatch (compile-failed, compare-error) writes
# no match file (reccert.py would read its missing `mismatches` as none); a unit with a recon REC1 did not apply in the
# certifying prepare (build/recon_rejected.tsv, or REC1-STALE: another decomp_commit) is `not-applied` in objects.tsv.
# Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS ($FARM_HOME/artifacts), JOBS (nproc), NICE (10), FNVNX_PREPARE_SKIP, RECCERT_BRANCH,
# RECCERT_SRC. Needs what cltree_job.sh and compile_job.sh need, and push access to the artifacts repository. Exit 0: report
# written and published; 1: FAILED (one-line reason in the log and the report). The report has counts, unit names, cert.tsv rows.
set -euo pipefail
COMMIT=${1:?usage: reccert_job.sh <commit> <report.md>}; REPORT=${2:?report.md}
REPORT=$(mkdir -p "$(dirname "$REPORT")" && cd "$(dirname "$REPORT")" && echo "$PWD/$(basename "$REPORT")")
FARM_SCRIPTS=$(cd "$(dirname "$0")/../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}; JOBS=${JOBS:-$(nproc)}; NICE=${NICE:-10}
BRANCH=${RECCERT_BRANCH:-claude/fnv-l2-source}; SKIP=${FNVNX_PREPARE_SKIP:-}
export FARM_HOME ARTIFACTS FNVNX_PREPARE_SKIP="$SKIP"
mkdir -p "$FARM_HOME"
source "$FARM_SCRIPTS/ports/fnv-nx/tools/farm/lib_job.sh"       # log, job_sources, job_gamedata, host_check
die() { log "FAILED: $*"; if [ -n "${PUBLISHING:-}" ]; then printf '\n**FAILED: %s**\n' "$*" >> "$REPORT"; else printf '# reccert FAILED\n\n%s\n' "$*" > "$REPORT"; fi; exit 1; }
case $SKIP in *[!A-Za-z0-9_,]*) die "FNVNX_PREPARE_SKIP has characters that are not rule names: $SKIP" ;; esac
now() { date +%s; }; GITA() { git -C "$ARTIFACTS" "$@"; }; T0=$(now); GIT_ID=(-c user.name=fnv-nx-farm -c user.email=farm@invalid)
# 1. sources, rule set hash, toolchain, exe
git -C "$FARM_SCRIPTS" fetch -q origin "$BRANCH" || log "fetch of $BRANCH failed (using what the repository has)"
COMMIT=$(git -C "$FARM_SCRIPTS" rev-parse -q --verify "$COMMIT^{commit}" 2>/dev/null) \
  || die "commit $1 is not in $FARM_SCRIPTS even after fetching origin $BRANCH"
if [ -n "${RECCERT_SRC:-}" ]; then FARM_SRC=$RECCERT_SRC; export FARM_SRC; else FARM_STABLE_SRC=$FARM_HOME/reccert-src; fi
job_sources "$FARM_SCRIPTS"; for f in tools/rules/reccert.py tools/link/compile.sh tools/farm/recon.sh tools/farm/equiv_job.sh tools/equiv/equiv.py \
         tools/equiv/setup_msvc.sh tools/farm/tree_hash.sh; do [ -f "$P/$f" ] || die "$P/$f is missing: $COMMIT is not a commit of $BRANCH (the L2 tools)"; done
for r in $(tr , ' ' <<<"$SKIP"); do grep -rqs "def $r(" "$P/tools/prepare.py" "$P/tools/rules" || die "SKIP names $r, no such rule function"; done
( host_check aarch64-linux-gnu-g++ g++ gccplugin jq python3 git ) || die "this host lacks tools (see the log above)"
H=$(python3 -I "$P/tools/rules/reccert.py" hash) || die "reccert.py hash failed"
W=$FARM_HOME/reccert-$C12; mkdir -p "$W"; DECOMP=$EXT/fnv_decomp; DCOMMIT=$(git -C "$DECOMP" rev-parse HEAD)
log "commit $C12 skip='${SKIP}' rules hash $H src=$SRC decomp=${DCOMMIT:0:12} jobs=$JOBS"; bash "$P/tools/farm/equiv_job.sh" --toolchain > "$W/toolchain.log" 2>&1 && rc=0 || rc=$?
[ "$(tail -1 "$W/toolchain.log" | cut -c1-11)" = "toolchain: " ] || die "no toolchain (rc=$rc): $(tail -1 "$W/toolchain.log" | cut -c1-200)"
envline=$(tail -1 "$FARM_HOME/logs/setup_msvc.log") && eval "$envline"
export FNV_MSVC9_ROOT FNV_MSVC9_RTM_ROOT FNV_MSVC9_PREFIX WINEDEBUG=-all
[ -f "$FNV_MSVC9_ROOT/VC/bin/cl.exe" ] || die "no cl.exe under $FNV_MSVC9_ROOT"
job_gamedata; [ -n "$GAME_EXE" ] || die "no player exe for build_and_match: $NOTE_EXE"
T1=$(now)
# 2. the recon set (recon.sh as prepare_job.sh uses it) and the map recon.json -> branch / tip / blob
R=$W/recon; nb=$(GITA ls-remote origin 'refs/heads/recon/*' | grep -c . || true)
. "$P/tools/farm/recon.sh"; fetch_recon "$ARTIFACTS" "$R" > "$W/recon.log" 2>&1 || true
{ [ -n "${FNVNX_RECON:-}" ] && [ -n "${RECON_ID:-}" ]; } || die "no recon set fetched: $RECON_NOTE"
[ "${RECON_NOTE%% *}" = "$nb" ] || die "$nb recon/* branches listed, fetch_recon took ${RECON_NOTE%% *} (a branch was left out: $W/recon.log)"
python3 -I - "$R" "$ARTIFACTS" "$W" "$DCOMMIT" > "$W/recon.counts" <<'EOF' || die "recon map failed"
import hashlib, json, os, subprocess, sys
R, art, W, head = sys.argv[1:5]
refs = subprocess.run(['git', '-C', art, 'for-each-ref', '--format=%(refname:strip=3) %(objectname)', 'refs/remotes/origin/recon/'],
                      capture_output=True, text=True, check=True).stdout.split('\n')
br = {l.split()[0]: l.split()[1] for l in refs if l.strip() and os.path.isdir(os.path.join(R, l.split()[0][6:]))}
units, stale, rows = set(), set(), []
for dp, _, ns in sorted(os.walk(R)):
    if 'recon.json' not in ns: continue
    f = os.path.join(dp, 'recon.json'); rel = os.path.relpath(f, R)
    b = [b for b in br if rel.startswith(b[6:] + '/')]
    if len(b) != 1: sys.exit('no single recon/* branch for ' + rel)
    data = open(f, 'rb').read()
    rows.append('\t'.join((rel, b[0], br[b[0]], rel[len(b[0]) - 5:], hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest())))
    try: r = json.loads(data)
    except ValueError: continue
    if r.get('format') != 'fnvnx-recon/1' or not r.get('unit'): continue
    units.add(r['unit'])
    if r.get('decomp_commit') and r['decomp_commit'] != head: stale.add(r['unit'])
open(W + '/recon.map', 'w').write(''.join(x + '\n' for x in rows))
open(W + '/U.list', 'w').write(''.join(u + '\n' for u in sorted(units)))
open(W + '/stale.list', 'w').write(''.join(u + '\n' for u in sorted(stale)))
print(len(rows), len(units), len(stale))
EOF
read -r NRECON NU NSTALE < "$W/recon.counts"; [ "$NU" -gt 0 ] || die "the recon set has no fnvnx-recon/1 recon.json with a unit"
T2=$(now); log "recon set: $RECON_NOTE; $NRECON recon.json, $NU units"
# 3. the candidate tree
unset FNVNX_PREPARE_MEMO FNVNX_VLIT; export FNVNX_RECON="$R" FNVNX_RECON_CERTIFY=1
TREE=$W/tree; TH() { bash "$P/tools/farm/tree_hash.sh" "$TREE"; }
KEY="commit=$COMMIT decomp=$DCOMMIT skip=$SKIP certify=1 recon=$RECON_ID env=$(env | grep '^FNVNX_' | grep -v '^FNVNX_RECON=' | LC_ALL=C sort | sha256sum | cut -c1-16)"
if [ -f "$W/tree.id" ] && [ "$(head -1 "$W/tree.id")" = "$KEY" ] && [ -f "$TREE/build/units.json" ] && [ "$(TH)" = "$(sed -n 2p "$W/tree.id")" ]; then
  how="own tree of an earlier run (tree.id equal)"
else
  rm -rf "$TREE" "$W/tree.id"
  python3 "$P/tools/prepare.py" "$DECOMP" "$TREE" > "$W/prepare.log" 2>&1 || die "prepare.py failed: $(tail -1 "$W/prepare.log" | cut -c1-200)"
  [ -f "$TREE/build/units.json" ] || die "no candidate tree: $TREE/build/units.json is missing"
  printf '%s\n%s\n' "$KEY" "$(TH)" > "$W/tree.id"; how="prepare.py with FNVNX_RECON_CERTIFY=1"
fi
TREE_HASH=$(TH)
{ cat "$W/stale.list"; if [ -f "$TREE/build/recon_rejected.tsv" ]; then cut -f2 "$TREE/build/recon_rejected.tsv"; fi; } | sort -u | grep -v '^$' > "$W/notapplied.list" || true
T3=$(now); log "tree $TREE_HASH: $how; $(wc -l < "$W/notapplied.list") units with a recon REC1 did not apply"
# 4. GCC objects of exactly U
OBJ=$W/obj; rm -rf "$OBJ"
FNVNX_SDD=1 SSE2NEON=$EXT/sse2neon nice -n "$NICE" bash "$P/tools/link/compile.sh" "$TREE" "$OBJ" "$W/U.list" "$JOBS" > "$W/compile.log" 2>&1 && rc=0 || rc=$?
[ -f "$OBJ/objects.tsv" ] || die "compile.sh rc=$rc and no objects.tsv: $(tail -1 "$W/compile.log" | cut -c1-200)"
awk -F'\t' -v OFS='\t' -v na="$(paste -sd, "$W/notapplied.list")" 'BEGIN{n=split(na,a,","); for(i=1;i<=n;i++) s[a[i]]=1} ($1 in s){$3="not-applied"} {print}' "$OBJ/objects.tsv" > "$W/objects.tsv"
GOK=$(grep -c $'\tok$' "$W/objects.tsv" || true); GFAIL=$(grep -c $'\tfail$' "$W/objects.tsv" || true); GNA=$(grep -c $'\tnot-applied$' "$W/objects.tsv" || true)
T4=$(now); log "compile.sh rc=$rc: $GOK ok, $GFAIL fail, $GNA not applied of $NU units"
# 5. MSVC build_and_match of exactly U
M=$W/match-root; MATCH=$W/match; rm -rf "$M" "$MATCH"; mkdir -p "$MATCH"; cp -al "$TREE" "$M"; cp -r "$DECOMP/tools" "$M/tools"
python3 -I - "$TREE/build/units.json" "$M/build/units.json" "$W/U.list" "$P/tools/equiv" <<'EOF' || die "match root: units.json could not be written"
import json, os, sys
src, dst, ul, eq = sys.argv[1:5]
sys.path.insert(0, eq); import equiv
want = set(open(ul).read().split('\n')) - {''}
d = json.load(open(src)); d['units'] = [x for x in d['units'] if x['source'] in want]
extra = ['/I' + str(equiv.SHIM), '/I' + str(equiv.gen_dir()), '/FI', str(equiv.FORCE_INCLUDE),
         '/I' + str(equiv.cmath_dir()), '/FI', str(equiv.COMPAT_INCLUDE)]     # equiv._compile_unit's flags for a prepared tree
for x in d['units']: x['flags'] = list(x['flags']) + extra
os.unlink(dst)                                                                # a hard link into the candidate tree
json.dump(d, open(dst, 'w'))
EOF
nice -n "$NICE" python3 "$M/tools/build_and_match.py" --image "$GAME_EXE" --jobs "$JOBS" --out "$W/bam.json" > "$W/bam.log" 2>&1 && rc=0 || rc=$?
[ "$rc" != 3 ] || die "build_and_match: the image check failed (the player's exe is not the plain code of 1.4.0.525)"
[ -f "$W/bam.json" ] || die "build_and_match rc=$rc and no result: $(tail -1 "$W/bam.log" | cut -c1-200)"
read -r MOK MMIS MFAIL < <(python3 -I - "$W/bam.json" "$MATCH" "$NU" <<'EOF'
import json, os, sys
d = json.load(open(sys.argv[1])); n = {}
for u in d['units']:
    n[u['status']] = n.get(u['status'], 0) + 1
    if u['status'] in ('ok', 'mismatch'):      # compile-failed / compare-error: no file, reccert.py then says "no match report"
        json.dump({'units': [u]}, open(os.path.join(sys.argv[2], u['source'].replace('/', '_') + '.json'), 'w'))
print(n.get('ok', 0), n.get('mismatch', 0), int(sys.argv[3]) - n.get('ok', 0) - n.get('mismatch', 0))
EOF
)
T5=$(now); log "build_and_match rc=$rc: $MOK ok, $MMIS mismatch, $MFAIL no result of $NU units"
# 6. stamp
python3 -I "$P/tools/rules/reccert.py" stamp "$R" --objects "$W/objects.tsv" --match "$MATCH" --report "$W/cert.tsv" > "$W/stamp.log" 2>&1 \
  || die "reccert.py stamp failed: $(tail -1 "$W/stamp.log" | cut -c1-200)"
grep -q "rule set $H:" "$W/stamp.log" || die "stamp ran under another rule set than $H: $(head -1 "$W/stamp.log")"
NCERT=$(awk -F'\t' 'NR>1 && $3=="certified"' "$W/cert.tsv" | wc -l); NNOT=$(awk -F'\t' 'NR>1 && $3!="certified"' "$W/cert.tsv" | wc -l)
T6=$(now); log "stamp: $NCERT certified, $NNOT not certified of $((NCERT+NNOT)) recons"
# 7. publish: changed recon.json files back to their branches (non-forced, on the fetched tip), then the reports branch
python3 -I - "$W/recon.map" "$R" > "$W/plan.tsv" <<'EOF'
import hashlib, sys
for l in open(sys.argv[1]):
    rel, b, tip, inb, blob = l.rstrip('\n').split('\t')
    d = open(sys.argv[2] + '/' + rel, 'rb').read()
    if hashlib.sha1(b'blob %d\0' % len(d) + d).hexdigest() != blob: print('\t'.join((b, tip, inb, rel, blob)))
EOF
PUBLISHED=""; PFAIL=""; NPUSH=0
for b in $(cut -f1 "$W/plan.tsv" | sort -u); do
  tip=$(awk -F'\t' -v b="$b" '$1==b{print $2; exit}' "$W/plan.tsv"); idx=$(mktemp -u); ok=1
  GIT_INDEX_FILE=$idx GITA read-tree "$tip"
  while IFS=$'\t' read -r _ _ inb rel blob; do
    [ "$(GITA rev-parse "$tip:$inb")" = "$blob" ] || { ok=0; break; }          # the branch tip still holds what was stamped
    mode=$(GITA ls-tree "$tip" -- "$inb" | cut -d' ' -f1)
    GIT_INDEX_FILE=$idx GITA update-index --add --cacheinfo "$mode,$(GITA hash-object -w "$R/$rel"),$inb"
  done < <(awk -F'\t' -v b="$b" '$1==b' "$W/plan.tsv")
  tr=$(GIT_INDEX_FILE=$idx GITA write-tree); rm -f "$idx"
  cm=$(GITA "${GIT_ID[@]}" commit-tree "$tr" -p "$tip" -m "recon.json: certificates for rule set $H ($C12)")
  if [ $ok = 1 ]; then
    for i in 1 2 3; do GITA push -q origin "$cm:refs/heads/$b" 2>/dev/null && break; sleep $((i * 5)); done
    [ "$(GITA ls-remote origin "refs/heads/$b" | cut -f1)" = "$cm" ] && { NPUSH=$((NPUSH+1)); PUBLISHED="$PUBLISHED $b"; continue; }
  fi
  PFAIL="$PFAIL $b"
done
PB=reports/reccert/$C12; ST=$W/stage; rm -rf "$ST"; mkdir -p "$ST"; cp "$W/cert.tsv" "$ST/"
cat > "$ST/meta.json" <<EOF
{"commit": "$COMMIT", "decomp_commit": "$DCOMMIT", "skip": "$SKIP", "rules_hash": "$H", "tree_hash": "$TREE_HASH", "recon_id": "$RECON_ID",
 "recons": $((NCERT+NNOT)), "certified": $NCERT, "not_certified": $NNOT, "units": $NU, "gcc_ok": $GOK, "gcc_fail": $GFAIL, "not_applied": $GNA,
 "msvc_ok": $MOK, "msvc_mismatch": $MMIS, "msvc_no_result": $MFAIL, "branches_updated": $NPUSH, "branches_failed": "${PFAIL# }",
 "seconds": {"setup": $((T1-T0)), "recon": $((T2-T1)), "prepare": $((T3-T2)), "gcc": $((T4-T3)), "msvc": $((T5-T4)), "stamp": $((T6-T5))},
 "tool_commit": "$(git -C "$SRC" rev-parse HEAD)", "cl_sha256": "$(sha256sum "$FNV_MSVC9_ROOT/VC/bin/cl.exe" | cut -c1-16)"}
EOF
idx=$(mktemp -u); GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$ST" -C "$ST" -c core.autocrlf=false add -A -f .
tr=$(GIT_INDEX_FILE=$idx GITA write-tree); rm -f "$idx"; par=()
GITA fetch -q origin "+refs/heads/$PB:refs/remotes/origin/$PB" 2>/dev/null && par=(-p "refs/remotes/origin/$PB")
cm=$(GITA "${GIT_ID[@]}" commit-tree "$tr" "${par[@]}" -m "$C12 reccert: $NCERT certified, $NNOT not, rule set $H")
RPUB=failed
for i in 1 2 3; do GITA push -q -f origin "$cm:refs/heads/$PB" 2>/dev/null && { RPUB=$PB; break; }; sleep $((i * 5)); done
T7=$(now); log "publish: $NPUSH recon branches updated, failed:${PFAIL:- none}; reports: $RPUB"; PUBLISHING=1
# 8. report (counts, unit names, cert.tsv rows)
{ printf '# reccert %s\n\n- commit %s, decomp %s, tool commit %s\n- SKIP: %s\n- rule set hash H: %s\n- candidate tree %s (%s), recon set id %s\n' \
    "$C12" "$COMMIT" "${DCOMMIT:0:12}" "$(git -C "$SRC" rev-parse --short=12 HEAD)" "${SKIP:-none}" "$H" "$TREE_HASH" "$how" "$RECON_ID"
  printf -- '- recons %s: certified %s, not certified %s; units with recons (U) %s\n' "$((NCERT+NNOT))" "$NCERT" "$NNOT" "$NU"
  printf -- '- GCC (FNVNX_SDD=1): ok %s, fail %s, not applied by REC1 %s\n- MSVC build_and_match: ok %s, mismatch %s, no result %s\n' "$GOK" "$GFAIL" "$GNA" "$MOK" "$MMIS" "$MFAIL"
  printf -- '- seconds: setup %s, recon fetch %s, prepare %s, gcc %s, msvc %s, stamp %s, publish %s; jobs %s\n' \
    "$((T1-T0))" "$((T2-T1))" "$((T3-T2))" "$((T4-T3))" "$((T5-T4))" "$((T6-T5))" "$((T7-T6))" "$JOBS"
  printf -- '- published: recon branches updated%s; failed:%s; %s in toadharvard/fnv-nx-build\n\n## cert.tsv (first 40 lines)\n\n```\n' "${PUBLISHED:- none}" "${PFAIL:- none}" "$RPUB"
  head -40 "$W/cert.tsv" | cut -c1-300; printf '```\n'
} > "$REPORT"
log "report written: $REPORT"
[ -z "$PFAIL" ] || die "recon.json not published to:$PFAIL (the tip moved or the push failed; nothing was forced)"
[ "$RPUB" != failed ] || die "publish of $PB failed"
exit 0
