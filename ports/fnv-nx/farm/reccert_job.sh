#!/usr/bin/env bash
# reccert_job.sh - L4 farm job: certify the reconstructions (recon/* branches of the artifacts repository, REC-CERT) and
# the site patches (patches/*.s1b, PATCH-CERT) for the rule set of one commit (tools/rules/reccert.py).
#
#   [FNVNX_PREPARE_SKIP=<rules>] FARM_HOME=... ARTIFACTS=... bash <checkout>/ports/fnv-nx/farm/reccert_job.sh <commit> <report.md>
#
# Two roots as cltree_job.sh: FARM_SCRIPTS (this repository: tools/farm/lib_job.sh) and SRC (the commit's checkout:
# $RECCERT_SRC at <commit>, else the stable worktree $FARM_HOME/reccert-src after fetching $RECCERT_BRANCH, default
# claude/fnv-l2-source); every tool the job runs comes from SRC. Steps:
#  1 sources, H = reccert.py hash (under the SKIP, default empty), the game's compiler (toolchain/msvcmodel/build.sh, or
#    RECCERT_CXX=<g++>), Wine + VC9 (equiv_job.sh --toolchain), the player's exe, the pristine MSVC objects
#  2 recon set R: recon.sh fetch_recon (every recon/* branch to R/<name>); map recon.json -> branch, tip, blob
#  3 candidate tree $FARM_HOME/reccert-<c12>/tree: prepare.py with FNVNX_RECON=R FNVNX_RECON_CERTIFY=1 FNVNX_PATCH_CERTIFY=1
#    and the SKIP. tree.id = commit, decomp commit, SKIP, both CERTIFY, hash of R, FNVNX_* environment, tree hash: reused only
#    when all are equal; no other tree (compile_job's, published) is ever adopted
#  4 tools/link/compile.sh on exactly U (the `unit` of every fnvnx-recon/1 recon.json) plus the units that hold a patch
#    site (s1b_patches.SITES paths that are units of the tree: what stamp-patches looks up) -> objects.tsv
#  5 reccert.py pristine-overlay <decomp> R <overlay>, then the overlay's tools/build_and_match.py -> match.json. The overlay
#    links tools/ to the decomp and build_and_match takes its ROOT from its resolved path, so the link is replaced by a
#    copy; the job checks that the report compared exactly the overlay's units
#  6 tests/s1b/msvc_check.py <decomp> <work> --pristine-objs --report msvc.jsonl
#  7 reccert.py stamp (cert.tsv) and stamp-patches (certs.json, patchcert.tsv); both must name rule set H
#  8 publish: each changed recon.json on top of the tip of its own recon/* branch (ordinary push; the blob at the tip must be
#    the one fetched; nothing changed, nothing pushed), then cert/<c12>: cert.tsv, patchcert.tsv, certs.json, meta.json,
#    overlay_rejected.tsv and recon/<branch name>/.../recon.json as stamped; 9 report.md
# Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS ($FARM_HOME/artifacts), JOBS (nproc), NICE (10), FNVNX_PREPARE_SKIP, RECCERT_BRANCH,
# RECCERT_SRC, RECCERT_CXX. Needs what cltree_job.sh, compile_job.sh and toolchain/msvcmodel/build.sh need, and push access to
# the artifacts repository. Exit 0: report written and published; 1: FAILED (one-line reason in the log and the report). The
# report has counts, unit names and rows of cert.tsv / patchcert.tsv; recons, tree, objects and the exe stay private.
set -euo pipefail
COMMIT=${1:?usage: reccert_job.sh <commit> <report.md>}; REPORT=${2:?report.md}
REPORT=$(mkdir -p "$(dirname "$REPORT")" && cd "$(dirname "$REPORT")" && echo "$PWD/$(basename "$REPORT")")
FARM_SCRIPTS=$(cd "$(dirname "$0")/../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}; JOBS=${JOBS:-$(nproc)}; NICE=${NICE:-10}
BRANCH=${RECCERT_BRANCH:-claude/fnv-l2-source}; SKIP=${FNVNX_PREPARE_SKIP:-}
export FARM_HOME ARTIFACTS FNVNX_PREPARE_SKIP="$SKIP"
mkdir -p "$FARM_HOME"
source "$FARM_SCRIPTS/ports/fnv-nx/tools/farm/lib_job.sh"       # log, job_sources, job_gamedata, job_pristine, host_check
die() { log "FAILED: $*"; if [ -n "${PUBLISHING:-}" ]; then printf '\n**FAILED: %s**\n' "$*" >> "$REPORT"; else printf '# reccert FAILED\n\n%s\n' "$*" > "$REPORT"; fi; exit 1; }
case $SKIP in *[!A-Za-z0-9_,]*) die "FNVNX_PREPARE_SKIP has characters that are not rule names: $SKIP" ;; esac
now() { date +%s; }; GITA() { git -C "$ARTIFACTS" "$@"; }; T0=$(now); GIT_ID=(-c user.name=fnv-nx-farm -c user.email=farm@invalid)
# 1. sources, rule set hash, toolchain, exe
git -C "$FARM_SCRIPTS" fetch -q origin "$BRANCH" || log "fetch of $BRANCH failed (using what the repository has)"
COMMIT=$(git -C "$FARM_SCRIPTS" rev-parse -q --verify "$COMMIT^{commit}" 2>/dev/null) \
  || die "commit $1 is not in $FARM_SCRIPTS even after fetching origin $BRANCH"
if [ -n "${RECCERT_SRC:-}" ]; then FARM_SRC=$RECCERT_SRC; export FARM_SRC; else FARM_STABLE_SRC=$FARM_HOME/reccert-src; fi
job_sources "$FARM_SCRIPTS"; for f in tools/rules/reccert.py tools/link/compile.sh tools/farm/recon.sh tools/farm/equiv_job.sh tools/equiv/equiv.py \
         tools/equiv/setup_msvc.sh tools/farm/tree_hash.sh tools/rules/s1b_patches.py tests/s1b/msvc_check.py toolchain/msvcmodel/build.sh; do [ -f "$P/$f" ] || die "$P/$f is missing: $COMMIT is not a commit of $BRANCH (the L2 tools)"; done
for r in $(tr , ' ' <<<"$SKIP"); do grep -rqs "def $r(" "$P/tools/prepare.py" "$P/tools/rules" || die "SKIP names $r, no such rule function"; done
( host_check aarch64-linux-gnu-g++ g++ jq python3 git ) || die "this host lacks tools (see the log above)"
H=$(python3 -I "$P/tools/rules/reccert.py" hash) || die "reccert.py hash failed"
W=$FARM_HOME/reccert-$C12; mkdir -p "$W"; DECOMP=$EXT/fnv_decomp; DCOMMIT=$(git -C "$DECOMP" rev-parse HEAD)
if [ -n "${RECCERT_CXX:-}" ]; then CXX=$RECCERT_CXX; else
  ms=$(sha256sum "$P/toolchain/msvcmodel/msvcmodel.patch" | cut -c1-16)      # build.sh's own stamp: a second run is immediate
  CXX=$(JOBS=$JOBS bash "$P/toolchain/msvcmodel/build.sh" "$FARM_HOME/gccwork-$ms" "$FARM_HOME/gcc-msvcmodel" 2> "$W/gcc-build.log" | tail -1) \
    || die "toolchain/msvcmodel/build.sh failed: $(tail -1 "$W/gcc-build.log" | cut -c1-200)"
fi
[ -x "$CXX" ] || die "no compiler: '$CXX' is not executable"; export CXX
# One compiler for the whole tree: the .c units go through the gcc driver of the same build as $CXX (compile.sh's CC
# default is the distribution's gcc, whose data model under -mabi=ilp32 is not the patched one).
CC=${CXX%g++}gcc; [ "$CC" != "$CXX" ] && [ -x "$CC" ] || die "no C driver next to $CXX ('$CC')"; export CC
CXXV=$("$CXX" --version | head -1); CC1=$(sha256sum "$("$CXX" -print-prog-name=cc1plus)" | cut -d' ' -f1) || die "no cc1plus of $CXX"
log "commit $C12 skip='${SKIP}' rules hash $H src=$SRC decomp=${DCOMMIT:0:12} jobs=$JOBS cxx=$CXXV"; bash "$P/tools/farm/equiv_job.sh" --toolchain > "$W/toolchain.log" 2>&1 && rc=0 || rc=$?
[ "$(tail -1 "$W/toolchain.log" | cut -c1-11)" = "toolchain: " ] || die "no toolchain (rc=$rc): $(tail -1 "$W/toolchain.log" | cut -c1-200)"
envline=$(tail -1 "$FARM_HOME/logs/setup_msvc.log") && eval "$envline"
export FNV_MSVC9_ROOT FNV_MSVC9_RTM_ROOT FNV_MSVC9_PREFIX WINEDEBUG=-all
[ -f "$FNV_MSVC9_ROOT/VC/bin/cl.exe" ] || die "no cl.exe under $FNV_MSVC9_ROOT"
job_gamedata; [ -n "$GAME_EXE" ] || die "no player exe for build_and_match: $NOTE_EXE"
job_pristine; [ -n "$PR" ] || die "no pristine MSVC objects for msvc_check.py: $NOTE_PR"
T1=$(now)
# 2. the recon set (recon.sh as prepare_job.sh uses it) and the map recon.json -> branch / tip / blob
R=$W/recon; nb=$(GITA ls-remote origin 'refs/heads/recon/*' | grep -c . || true)
. "$P/tools/farm/recon.sh"; fetch_recon "$ARTIFACTS" "$R" > "$W/recon.log" 2>&1 || true
{ [ -n "${FNVNX_RECON:-}" ] && [ -n "${RECON_ID:-}" ]; } || die "no recon set fetched: $RECON_NOTE"
[ "${RECON_NOTE%% *}" = "$nb" ] || die "$nb recon/* branches listed, fetch_recon took ${RECON_NOTE%% *} (a branch was left out: $W/recon.log)"
python3 -I - "$R" "$ARTIFACTS" "$W" > "$W/recon.counts" <<'EOF' || die "recon map failed"
import hashlib, json, os, subprocess, sys
R, art, W = sys.argv[1:4]
refs = subprocess.run(['git', '-C', art, 'for-each-ref', '--format=%(refname:strip=3) %(objectname)', 'refs/remotes/origin/recon/'],
                      capture_output=True, text=True, check=True).stdout.split('\n')
br = {l.split()[0]: l.split()[1] for l in refs if l.strip() and os.path.isdir(os.path.join(R, l.split()[0][6:]))}
units, rows = set(), []
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
open(W + '/recon.map', 'w').write(''.join(x + '\n' for x in rows))
open(W + '/U.list', 'w').write(''.join(u + '\n' for u in sorted(units)))
print(len(rows), len(units))
EOF
read -r NRECON NU < "$W/recon.counts"; [ "$NU" -gt 0 ] || die "the recon set has no fnvnx-recon/1 recon.json with a unit"
T2=$(now); log "recon set: $RECON_NOTE; $NRECON recon.json, $NU units"
# 3. the candidate tree
unset FNVNX_PREPARE_MEMO FNVNX_VLIT; export FNVNX_RECON="$R" FNVNX_RECON_CERTIFY=1 FNVNX_PATCH_CERTIFY=1
TREE=$W/tree; TH() { bash "$P/tools/farm/tree_hash.sh" "$TREE"; }
KEY="commit=$COMMIT decomp=$DCOMMIT skip=$SKIP recon_certify=1 patch_certify=1 recon=$RECON_ID env=$(env | grep '^FNVNX_' | grep -v '^FNVNX_RECON=' | LC_ALL=C sort | sha256sum | cut -c1-16)"
if [ -f "$W/tree.id" ] && [ "$(head -1 "$W/tree.id")" = "$KEY" ] && [ -f "$TREE/build/units.json" ] && [ "$(TH)" = "$(sed -n 2p "$W/tree.id")" ]; then
  how="own tree of an earlier run (tree.id equal)"
else
  rm -rf "$TREE" "$W/tree.id"
  python3 "$P/tools/prepare.py" "$DECOMP" "$TREE" > "$W/prepare.log" 2>&1 || die "prepare.py failed: $(tail -1 "$W/prepare.log" | cut -c1-200)"
  [ -f "$TREE/build/units.json" ] || die "no candidate tree: $TREE/build/units.json is missing"
  printf '%s\n%s\n' "$KEY" "$(TH)" > "$W/tree.id"; how="prepare.py with FNVNX_RECON_CERTIFY=1 FNVNX_PATCH_CERTIFY=1"
fi
TREE_HASH=$(TH); STATS=$TREE/prepare_stats.tsv; [ -f "$STATS" ] || die "the candidate tree has no prepare_stats.tsv"
T3=$(now); log "tree $TREE_HASH: $how"
# 4. GCC objects of U and of the units with patch sites (the paths stamp-patches looks up in objects.tsv)
python3 -I -c 'import json, sys
sys.path.insert(0, sys.argv[1]); import s1b_patches
units = {u["source"] for u in json.load(open(sys.argv[2]))["units"]}
print("".join(p + "\n" for p in sorted({s.path for s in s1b_patches.SITES} & units)), end="")' "$P/tools/rules" "$TREE/build/units.json" > "$W/S.list" \
  || die "the patch sites could not be listed (tools/rules/s1b_patches.py)"
sort -u "$W/U.list" "$W/S.list" > "$W/C.list"; NS=$(wc -l < "$W/S.list"); NC=$(wc -l < "$W/C.list")
OBJ=$W/obj; rm -rf "$OBJ"
SSE2NEON=$EXT/sse2neon nice -n "$NICE" bash "$P/tools/link/compile.sh" "$TREE" "$OBJ" "$W/C.list" "$JOBS" > "$W/compile.log" 2>&1 && rc=0 || rc=$?
[ -f "$OBJ/objects.tsv" ] || die "compile.sh rc=$rc and no objects.tsv: $(tail -1 "$W/compile.log" | cut -c1-200)"
[ "$(wc -l < "$OBJ/objects.tsv")" = "$NC" ] || die "objects.tsv has $(wc -l < "$OBJ/objects.tsv") rows for $NC units (compile.sh rc=$rc)"
GOK=$(grep -c $'\tok$' "$OBJ/objects.tsv" || true); GFAIL=$(grep -c $'\tfail$' "$OBJ/objects.tsv" || true)
GUOK=$(awk -F'\t' 'NR==FNR{u[$0]; next} ($1 in u) && $3=="ok"' "$W/U.list" "$OBJ/objects.tsv" | wc -l)
T4=$(now); log "compile.sh rc=$rc: $NC units ($NU with recons, $NS with patch sites): $GOK ok, $GFAIL fail"
# 5. MSVC half of REC-CERT: the pristine decomp with the recons, build_and_match against the exe
OV=$W/overlay; rm -rf "$OV" "$W/match.json"
python3 -I "$P/tools/rules/reccert.py" pristine-overlay "$DECOMP" "$R" "$OV" > "$W/overlay.log" 2>&1 || die "pristine-overlay failed: $(tail -1 "$W/overlay.log" | cut -c1-200)"
# build_and_match.py: ROOT = Path(__file__).resolve().parent.parent; through the overlay's link that is the decomp itself
if [ -L "$OV/tools" ]; then rm "$OV/tools"; cp -r "$DECOMP/tools" "$OV/tools"; fi
NOREJ=$(grep -vc '^#' "$OV/overlay_rejected.tsv" || true)
(cd "$OV" && nice -n "$NICE" python3 "$OV/tools/build_and_match.py" --image "$GAME_EXE" --jobs "$JOBS" --out "$W/match.json") > "$W/bam.log" 2>&1 && rc=0 || rc=$?
[ "$rc" != 3 ] || die "build_and_match: the image check failed (the player's exe is not the plain code of 1.4.0.525)"
[ -f "$W/match.json" ] || die "build_and_match rc=$rc and no result: $(tail -1 "$W/bam.log" | cut -c1-200)"
read -r NOV MOK MMIS MOTHER < <(python3 -I -c 'import json, sys
d = json.load(open(sys.argv[1])); want = sorted(u["source"] for u in json.load(open(sys.argv[2]))["units"])
if sorted(u["source"] for u in d["units"]) != want: sys.exit("not the overlay units")
st = [u["status"] for u in d["units"]]; print(len(st), st.count("ok"), st.count("mismatch"), len(st) - st.count("ok") - st.count("mismatch"))' \
  "$W/match.json" "$OV/build/units.json") || die "build_and_match did not compare exactly the overlay's units (its ROOT is not $OV)"
T5=$(now); log "build_and_match rc=$rc: $NOV overlay units: $MOK ok, $MMIS mismatch, $MOTHER not compared; $NOREJ recons do not fit the pristine text"
# 6. MSVC half of PATCH-CERT (the report is appended to: it starts empty)
rm -rf "$W/msvc-work"; : > "$W/msvc.jsonl"
nice -n "$NICE" python3 "$P/tests/s1b/msvc_check.py" "$DECOMP" "$W/msvc-work" --pristine-objs "$PR" --jobs "$JOBS" --report "$W/msvc.jsonl" > "$W/msvc_check.log" 2>&1 \
  || die "msvc_check.py failed: $(tail -1 "$W/msvc_check.log" | cut -c1-200)"
PMID=$(grep -c '"status": "identical"' "$W/msvc.jsonl" || true); PMROWS=$(grep -c . "$W/msvc.jsonl" || true)
T6=$(now); log "msvc_check: $PMROWS rows, $PMID identical"
# 7. stamp the recons and the patch sites
python3 -I "$P/tools/rules/reccert.py" stamp "$R" --stats "$STATS" --objects "$OBJ/objects.tsv" --match "$W/match.json" --report "$W/cert.tsv" > "$W/stamp.log" 2>&1 \
  || die "reccert.py stamp failed: $(tail -1 "$W/stamp.log" | cut -c1-200)"
grep -q "rule set $H:" "$W/stamp.log" || die "stamp ran under another rule set than $H: $(head -1 "$W/stamp.log")"
python3 -I "$P/tools/rules/reccert.py" stamp-patches --stats "$STATS" --objects "$OBJ/objects.tsv" --msvc "$W/msvc.jsonl" --out "$W/certs.json" \
  --report "$W/patchcert.tsv" > "$W/stamp-patches.log" 2>&1 || die "reccert.py stamp-patches failed: $(tail -1 "$W/stamp-patches.log" | cut -c1-200)"
grep -q "rule set $H:" "$W/stamp-patches.log" || die "stamp-patches ran under another rule set than $H: $(head -1 "$W/stamp-patches.log")"
NCERT=$(awk -F'\t' 'NR>1 && $3=="certified"' "$W/cert.tsv" | wc -l); NNOT=$(awk -F'\t' 'NR>1 && $3!="certified"' "$W/cert.tsv" | wc -l)
PCERT=$(awk -F'\t' 'NR>1 && $4=="certified"' "$W/patchcert.tsv" | wc -l); PNOT=$(awk -F'\t' 'NR>1 && $4!="certified"' "$W/patchcert.tsv" | wc -l)
T7=$(now); log "stamp: recons $NCERT certified, $NNOT not; patch sites $PCERT certified, $PNOT not"
# 8. publish: changed recon.json files back to their branches (non-forced, on the fetched tip), then the results branch cert/<c12>
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
PB=cert/$C12; ST=$W/stage; rm -rf "$ST"; mkdir -p "$ST/recon"
cp "$W/cert.tsv" "$W/patchcert.tsv" "$W/certs.json" "$OV/overlay_rejected.tsv" "$ST/"
(cd "$R" && find . -name recon.json -exec cp --parents -t "$ST/recon" {} +) || die "the stamped recon.json files could not be staged"
T8=$(now)
cat > "$ST/meta.json" <<EOF
{"commit": "$COMMIT", "decomp_commit": "$DCOMMIT", "skip": "$SKIP", "rules_hash": "$H", "tree_hash": "$TREE_HASH", "recon_id": "$RECON_ID",
 "recons": $((NCERT+NNOT)), "certified": $NCERT, "not_certified": $NNOT, "units_with_recons": $NU, "units_with_patch_sites": $NS,
 "units_compiled": $NC, "gcc_ok": $GOK, "gcc_fail": $GFAIL, "gcc_ok_with_recons": $GUOK, "overlay_units": $NOV, "overlay_rejected": $NOREJ,
 "msvc_ok": $MOK, "msvc_mismatch": $MMIS, "msvc_not_compared": $MOTHER, "patch_sites": $((PCERT+PNOT)), "patch_certified": $PCERT,
 "patch_not_certified": $PNOT, "msvc_check_rows": $PMROWS, "msvc_check_identical": $PMID, "branches_updated": $NPUSH, "branches_failed": "${PFAIL# }",
 "seconds": {"setup": $((T1-T0)), "recon": $((T2-T1)), "prepare": $((T3-T2)), "gcc": $((T4-T3)), "match": $((T5-T4)), "msvc_check": $((T6-T5)),
  "stamp": $((T7-T6)), "recon_push": $((T8-T7))}, "tool_commit": "$(git -C "$SRC" rev-parse HEAD)", "cxx": "$CXXV", "cc1plus_sha256": "$CC1",
 "cl_sha256": "$(sha256sum "$FNV_MSVC9_ROOT/VC/bin/cl.exe" | cut -c1-16)"}
EOF
idx=$(mktemp -u); GIT_INDEX_FILE=$idx git --git-dir="$ARTIFACTS/.git" --work-tree="$ST" -C "$ST" -c core.autocrlf=false add -A -f .
tr=$(GIT_INDEX_FILE=$idx GITA write-tree); rm -f "$idx"; par=()
GITA fetch -q origin "+refs/heads/$PB:refs/remotes/origin/$PB" 2>/dev/null && par=(-p "refs/remotes/origin/$PB")
cm=$(GITA "${GIT_ID[@]}" commit-tree "$tr" "${par[@]}" -m "$C12 cert: recons $NCERT of $((NCERT+NNOT)), patch sites $PCERT of $((PCERT+PNOT)), rule set $H")
RPUB=failed
for i in 1 2 3; do GITA push -q -f origin "$cm:refs/heads/$PB" 2>/dev/null && { RPUB=$PB; break; }; sleep $((i * 5)); done
T9=$(now); log "publish: $NPUSH recon branches updated, failed:${PFAIL:- none}; results: $RPUB"; PUBLISHING=1
# 9. report (counts, unit names, rows of cert.tsv and patchcert.tsv)
{ printf '# reccert %s\n\n- commit %s, decomp %s, tool commit %s\n- SKIP: %s\n- rule set hash H: %s\n- candidate tree %s (%s), recon set id %s\n' \
    "$C12" "$COMMIT" "${DCOMMIT:0:12}" "$(git -C "$SRC" rev-parse --short=12 HEAD)" "${SKIP:-none}" "$H" "$TREE_HASH" "$how" "$RECON_ID"
  printf -- '- compiler: %s, cc1plus sha256 %s\n' "$CXXV" "$CC1"
  printf -- '- recons %s: certified %s, not certified %s; units with recons (U) %s\n' "$((NCERT+NNOT))" "$NCERT" "$NNOT" "$NU"
  printf -- '- patch sites %s: certified %s, not certified %s; units with patch sites %s; msvc_check rows %s, identical %s\n' "$((PCERT+PNOT))" "$PCERT" "$PNOT" "$NS" "$PMROWS" "$PMID"
  printf -- '- GCC: %s units compiled, ok %s, fail %s (of U: ok %s, not ok %s)\n' "$NC" "$GOK" "$GFAIL" "$GUOK" "$((NU-GUOK))"
  printf -- '- MSVC build_and_match on the pristine overlay: %s units, ok %s, mismatch %s, not compared %s; recons that do not fit the pristine text %s\n' "$NOV" "$MOK" "$MMIS" "$MOTHER" "$NOREJ"
  printf -- '- seconds: setup %s, recon fetch %s, prepare %s, gcc %s, build_and_match %s, msvc_check %s, stamp %s, publish %s; jobs %s\n' \
    "$((T1-T0))" "$((T2-T1))" "$((T3-T2))" "$((T4-T3))" "$((T5-T4))" "$((T6-T5))" "$((T7-T6))" "$((T9-T7))" "$JOBS"
  printf -- '- published: recon branches updated%s; failed:%s; %s in toadharvard/fnv-nx-build\n' "${PUBLISHED:- none}" "${PFAIL:- none}" "$RPUB"
  printf '\n## cert.tsv (first 40 lines)\n\n```\n'; head -40 "$W/cert.tsv" | cut -c1-300
  printf '```\n\n## patchcert.tsv (first 40 lines)\n\n```\n'; head -40 "$W/patchcert.tsv" | cut -c1-300; printf '```\n'
} > "$REPORT"
log "report written: $REPORT"
[ -z "$PFAIL" ] || die "recon.json not published to:$PFAIL (the tip moved or the push failed; nothing was forced)"
[ "$RPUB" != failed ] || die "publish of $PB failed"
exit 0
