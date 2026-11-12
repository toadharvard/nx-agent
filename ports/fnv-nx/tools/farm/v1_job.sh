#!/usr/bin/env bash
# v1_job.sh - farm job: V1-link's full-set audits (D14 verification of the link step, docs/LINK.md) on the link
# outputs of one commit. Long by nature (every reference, every vtable, every bridge class), so it runs on a farm, not
# in a development session (D17): as step 4d of full_job.sh on that job's own link (no fetch, published with it as
# link/<commit12>/v1/), or on its own.
#
#   tools/farm/v1_job.sh <commit> <report.md>
#
# Inputs, fetched when missing, cached under $FARM_HOME:
#   link     branch link/<commit12> of the private artifacts repository: out/ (refs.tsv, abi.tsv, addrmap.tsv,
#            aliases.ld; layouts.tsv, bridges.S, objcopy/, trial.elf when published) and fixed/*.o. Without bridges.S
#            the bridges run from fixed/zz_bridges.o as assembled; without objcopy/ C5 maps objects by name.
#            Without trial.elf, `fnvlink.py check` relinks it here. $V1_LINK_DIR=<dir with out/ and fixed/> instead.
#   tree     the prepared tree of <commit> (fetch_tree.sh: branch t/<commit12>); $V1_TREE instead. Only
#            build/units.json is read.
#   objects  the compile farms' objects of <commit> (fetch_objs.sh, c/<commit12>/<i>of<M>; $OBJ_SHARDS = M);
#            $V1_OBJ_DIR instead. Used by the C1 sampler only; without them C1's sampler is skipped.
#   msvc     the pristine decomp's MSVC 2008 objects, branch msvc/pristine/<decomp12>; $V1_X86OBJ
#            instead. Used by C3 only; without them C3 is skipped.
#   ext      tools/fetch.sh's pinned checkouts in $V1_EXT (default $FARM_HOME/ext): the decomp's units.json (C3),
#            sse2neon.
# V1_NO_FETCH=1 fetches nothing (full_job.sh): $V1_EXT is used as it is (a shared checkout must not be moved), the
# link and the tree must be given, and the audits that lack the objects or the MSVC objects are skipped.
# Audits (tools/verify/v1_link, each one process under nice, in this order, cheap and decisive ones first; outputs in
# $RUN/<audit>/; seconds on link/9a0243c7eabe, two runs on a 4-core development machine under nice; 6 min in all):
#   selftest      2-3 s  harness self-test (qemu ILP32 runner, wrong bridges caught, C5 scanner)
#   repro-*     25-27 s  the reproducers and adversarial cases (adversarial_abi 3, adversarial_r3 13, adversarial_cname
#                        7, adversarial_typeinfo 1, chain_x87, repro_cname_abi, repro_ecx_push_arg, repro_typeinfo_mix <1)
#   c1-kinds      7-9 s  c1_kinds.py, every address-map row's kind + 60 sampled per kind
#   c4-definers     9 s  c4_definers.py --audit (kept vtable vs kept typeinfo by object; synthesized ones without)
#   c4-vtti       7-8 s  c4_vtti.py (every vtable group against the kept typeinfo, whole image)
#   c4-vtables  10-11 s  c4_vtables.py (V/G entries, synthesized vtable objects)
#   c3-ecx      15-18 s  c3_ecx.py, every bound reference against the definition's MSVC register use
#   c2-shapes       14 s  c2_shapes.py (float/double results, fastcall aggregates, callee-pop mismatches, ...)
#   c2-fastcall-agg 3 s  c2_bridges.py V1_SELECT=fastcall-agg (every such pair)
#   c1-bind     33-34 s  c1_bind.py, 160 random objects x 4 relocations, linked sites in trial.elf
#   c5-pic      78-92 s  c5_pic.py, every rewritten object's relocations
#   c2-bridges 142-152 s c2_bridges.py stratified (6 per bridge class, 3 seeds), each pair run under qemu
# (plus 10 s for the trial link when the link outputs have no trial.elf). Time budget: $V1_BUDGET seconds (default
# 1200) for all of them; an audit whose estimate (about 1.5x the time above) no longer fits is skipped, one still
# running when the budget ends is stopped, and the report says which ("skip", "timeout"). V1_ONLY="<audit>..." runs
# only those (e.g. "c4-definers c4-vtti c2-shapes", the watched counters: under a minute, fine in a session).
# Output: <report.md> with each audit's exit status (0 = holds), seconds and summary lines, counts only; the detail
# files ($RUN, decomp-derived names and addresses: tables and logs, about 1 MB) go to $V1_OUT_DIR when set (full_job.sh:
# published as link/<commit12>/v1/) and to branch v1/<commit12> of the private artifacts repository ($V1_PUBLISH=0
# skips that push). $RUN is $V1_RUN (default $FARM_HOME/v1-<commit12>); an audit whose .done exists there is not
# rerun. Nothing is written into the link's directory.
#
# Needs: git, python3 with pyelftools and capstone (pip when missing), g++/binutils-aarch64-linux-gnu with ILP32
# support (the pinned cross tools of toolchain/cross on PATH), llvm-undname (or llvm-undname-18), c++filt; qemu-aarch64
# (qemu-user) for the bridge runs and the qemu reproducers, which are skipped without it; for the push, access to
# toadharvard/fnv-nx-build.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
P=$(cd "$HERE/../.." && pwd)
V=$P/tools/verify/v1_link
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
EXT=${V1_EXT:-$FARM_HOME/ext}
PUBLISH=${V1_PUBLISH:-1}
C12=${COMMIT:0:12}
RUN=${V1_RUN:-$FARM_HOME/v1-$C12}
mkdir -p "$FARM_HOME" "$RUN" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "== $(date +%T) $*"; }
fail_report() { printf '# V1-link %s\n\n- status: not run\n- %s\n' "$C12" "$2" > "$REPORT"; echo "v1_job: $2" >&2; exit "$1"; }
fetch_branch() {  # fetch_branch <branch> <dest dir>
  [ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
  local ref="refs/remotes/origin/$1"
  git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$1:$ref" 2>/dev/null || return 1
  rm -rf "$2.tmp"; mkdir -p "$2.tmp"
  git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$2.tmp"
  rm -rf "$2"; mv "$2.tmp" "$2"
}
PIP=""   # only what is missing, at the versions the harness was run with (pyelftools 0.33 as host_check's)
python3 -c 'import elftools' 2>/dev/null || PIP="$PIP pyelftools==0.33"
python3 -c 'import capstone' 2>/dev/null || PIP="$PIP capstone==5.0.7"
# shellcheck disable=SC2086
[ -z "$PIP" ] || python3 -m pip install -q --break-system-packages $PIP 2>/dev/null \
  || python3 -m pip install -q --user --break-system-packages $PIP || fail_report 4 "pip install$PIP failed"
if ! command -v llvm-undname >/dev/null && command -v llvm-undname-18 >/dev/null; then   # the farms' LLVM 18 name
  mkdir -p "$RUN/bin"; ln -sf "$(command -v llvm-undname-18)" "$RUN/bin/llvm-undname"; export PATH=$RUN/bin:$PATH
fi
for t in llvm-undname c++filt aarch64-linux-gnu-gcc aarch64-linux-gnu-g++ aarch64-linux-gnu-ld aarch64-linux-gnu-objcopy \
         aarch64-linux-gnu-nm; do
  command -v "$t" >/dev/null || fail_report 4 "missing tool: $t"
done
HAVE_QEMU=0; command -v qemu-aarch64 >/dev/null && HAVE_QEMU=1   # without it: no bridge runs, no qemu reproducers
export SSE2NEON=${SSE2NEON:-$EXT/sse2neon} PYTHONDONTWRITEBYTECODE=1

# --- inputs -----------------------------------------------------------------------------------------------------
if [ -n "${V1_LINK_DIR:-}" ]; then LINK=$V1_LINK_DIR
else
  LINK=$FARM_HOME/link-$C12
  if [ ! -f "$LINK/.done" ]; then
    [ "${V1_NO_FETCH:-0}" = 1 ] && fail_report 3 "no link outputs for $C12 (V1_NO_FETCH=1 and no V1_LINK_DIR)"
    fetch_branch "link/$C12" "$LINK" || fail_report 3 "no link outputs for $C12 (branch link/$C12): a link job has to \
publish out/ and fixed/ first"
    touch "$LINK/.done"
  fi
fi
for f in out/refs.tsv out/abi.tsv out/addrmap.tsv out/aliases.ld fixed/zz_bridges.o; do   # bridges.S, objcopy/: optional
  [ -e "$LINK/$f" ] || fail_report 3 "link outputs for $C12 lack $f"
done
TRIAL=$LINK/out/trial.elf
if [ ! -f "$TRIAL" ]; then   # relinked in the job's own directory: the link's directory is never written
  log "trial link (fnvlink.py check)"
  mkdir -p "$RUN/check"
  cp "$LINK/out/aliases.ld" "$LINK/out/addrmap.tsv" "$RUN/check/"
  nice -n 19 python3 "$P/tools/link/fnvlink.py" check "$RUN/check" "$LINK/fixed" > "$RUN/check.log" 2>&1 || true
  TRIAL=$RUN/check/trial.elf
fi
[ -f "$TRIAL" ] || fail_report 3 "no trial.elf for $C12"
if [ -n "${V1_TREE:-}" ]; then TREE=$V1_TREE
else
  TREE=$FARM_HOME/tree-$C12
  [ -f "$TREE/build/units.json" ] || [ "${V1_NO_FETCH:-0}" != 1 ] || fail_report 2 "no tree (V1_NO_FETCH=1, no V1_TREE)"
  [ -f "$TREE/build/units.json" ] || bash "$HERE/fetch_tree.sh" "$COMMIT" "$TREE" 1800 > "$RUN/fetch_tree.log" 2>&1 \
    || fail_report 2 "no prepared tree t/$C12"
fi
UJ=$TREE/build/units.json
OBJ=${V1_OBJ_DIR:-$FARM_HOME/objs-$C12}
if [ ! -f "$OBJ/objects.tsv" ] && [ "${V1_NO_FETCH:-0}" != 1 ]; then
  M=${OBJ_SHARDS:-$(git ls-remote "$ARTIFACTS_URL" "refs/heads/c/$C12/*" 2>/dev/null \
      | sed -n 's|.*/\([0-9]*\)of\([0-9]*\)$|\2|p' | sort | uniq -c | awk '$1 == $2 {print $2}' | sort -n | head -1)}
  if [ -n "$M" ]; then ARTIFACTS=$ARTIFACTS bash "$HERE/fetch_objs.sh" "$COMMIT" "$M" "$OBJ" > "$RUN/fetch_objs.log" 2>&1 || true; fi
fi
[ "${V1_NO_FETCH:-0}" = 1 ] || bash "$P/tools/fetch.sh" "$EXT" >/dev/null 2>&1 || true   # V1_NO_FETCH=1: $EXT as is
DECOMP=$EXT/fnv_decomp
X86=${V1_X86OBJ:-}
if [ -z "$X86" ] && [ -d "$DECOMP/.git" ] && [ "${V1_NO_FETCH:-0}" != 1 ]; then
  D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
  ORACLE=$FARM_HOME/oracle-$D12
  [ -f "$ORACLE/.done" ] || { fetch_branch "msvc/pristine/$D12" "$ORACLE" && touch "$ORACLE/.done"; } || true
  [ -d "$ORACLE/obj" ] && X86=$ORACLE/obj
fi

# --- audits, within a time budget -------------------------------------------------------------------------------
# V1_BUDGET seconds (default 1200) for all audits: before each one, its estimate (seconds, about 1.5x the time it took
# on link/9a0243c7eabe, see the header) is compared with what is left; an audit that does not fit is skipped, one that
# runs past the rest of the budget is stopped (timeout); the report says which. Cheap and decisive audits come first,
# the bridge executions last.
BUDGET=${V1_BUDGET:-1200}
T0=$(date +%s)
declare -A RC SECS NOTE EST=([selftest]=5 [repro]=20 [c1-kinds]=14 [c4-definers]=14 [c4-vtti]=12 [c4-vtables]=17
  [c3-ecx]=27 [c2-shapes]=21 [c2-fastcall-agg]=5 [c1-bind]=50 [c5-pic]=140 [c2-bridges]=230)
ORDER=()
audit() {  # audit <name> <estimate key> <command...>: run once (a rerun reuses .done), timed, inside the budget
  local name=$1 key=$2; shift 2
  local d=$RUN/$name left t rc
  ORDER+=("$name")
  if [ -n "${V1_ONLY:-}" ] && [[ " $V1_ONLY " != *" $name "* ]]; then
    RC[$name]=skip; SECS[$name]=0; NOTE[$name]="not selected (V1_ONLY)"; return 0
  fi
  mkdir -p "$d"
  if [ -f "$d/.done" ]; then
    RC[$name]=$(cat "$d/rc"); SECS[$name]=$(cat "$d/seconds" 2>/dev/null || echo 0); NOTE[$name]="from an earlier run"
    return 0
  fi
  left=$(( BUDGET - ($(date +%s) - T0) ))
  if [ "$left" -le "${EST[$key]:-30}" ]; then
    RC[$name]=skip; SECS[$name]=0; NOTE[$name]="skipped: ${left}s of the budget left, estimate ${EST[$key]:-30}s"
    log "$name skipped (budget)"
    return 0
  fi
  log "$name"
  t=$(date +%s)
  set +e
  (cd "$d" && timeout --kill-after=10 "$left" nice -n 19 "$@") > "$d/out.log" 2>&1
  rc=$?
  set -e
  echo $rc > "$d/rc"; echo $(( $(date +%s) - t )) > "$d/seconds"
  RC[$name]=$rc; SECS[$name]=$(cat "$d/seconds"); NOTE[$name]=""
  if [ "$rc" = 124 ] || [ "$rc" = 137 ]; then NOTE[$name]="stopped: budget exhausted"; RC[$name]=timeout
  else touch "$d/.done"; fi
}
not_run() { ORDER+=("$1"); RC[$1]=skip; SECS[$1]=0; NOTE[$1]="skipped: $2"; }   # not_run <audit> <why>
need_qemu() { [ "$HAVE_QEMU" = 1 ] || { not_run "$1" "no qemu-aarch64"; return 1; }; }
O=$LINK/out; F=$LINK/fixed
need_qemu selftest && audit selftest selftest python3 "$V/selftest_harness.py" "$RUN/selftest/work"
for s in adversarial_abi adversarial_r3 adversarial_cname adversarial_typeinfo chain_x87 repro_cname_abi \
         repro_ecx_push_arg repro_typeinfo_mix; do
  case $s in repro_ecx_push_arg) audit "repro-$s" repro python3 "$V/$s.py" "$RUN/repro-$s/work" ;;
             *) need_qemu "repro-$s" && audit "repro-$s" repro python3 "$V/$s.py" "$RUN/repro-$s/work" ;; esac
done
audit c1-kinds c1-kinds python3 "$V/c1_kinds.py" "$O/addrmap.tsv" "$UJ" "$TRIAL" "$RUN/c1-kinds" 60
audit c4-definers c4-definers python3 "$V/c4_definers.py" --audit "$F" "$TRIAL" "$RUN/c4-definers/audit.tsv"
audit c4-vtti c4-vtti python3 "$V/c4_vtti.py" "$TRIAL" "$RUN/c4-vtti/vtti.tsv"
audit c4-vtables c4-vtables python3 "$V/c4_vtables.py" "$TRIAL" "$O/addrmap.tsv" "$F" "$RUN/c4-vtables/c4.tsv"
if [ -n "$X86" ] && [ -f "$DECOMP/build/units.json" ]; then
  audit c3-ecx c3-ecx python3 "$V/c3_ecx.py" "$O" "$UJ" "$DECOMP/build/units.json" "$X86" "$RUN/c3-ecx/work"
else
  not_run c3-ecx "no pristine MSVC objects or decomp units.json"
fi
audit c2-shapes c2-shapes python3 "$V/c2_shapes.py" "$O" "$UJ" "$RUN/c2-shapes/work"
need_qemu c2-fastcall-agg && audit c2-fastcall-agg c2-fastcall-agg env V1_SELECT=fastcall-agg python3 "$V/c2_bridges.py" \
  "$O" "$UJ" "$RUN/c2-fastcall-agg/work" 6 1
if [ -f "$OBJ/objects.tsv" ]; then
  audit c1-bind c1-bind python3 "$V/c1_bind.py" "$OBJ/objects.tsv" "$UJ" "$F" "$TRIAL" "$O/refs.tsv" "$RUN/c1-bind" 160 4
else
  not_run c1-bind "no original objects"
fi
C5MAP=$O/objcopy; [ -d "$C5MAP" ] || C5MAP=$OBJ/objects.tsv     # farm links: original objects by name
audit c5-pic c5-pic python3 "$V/c5_pic.py" "$F" "$C5MAP" "$UJ" "$RUN/c5-pic/c5.tsv"
need_qemu c2-bridges && audit c2-bridges c2-bridges python3 "$V/c2_bridges.py" "$O" "$UJ" "$RUN/c2-bridges/work" 6 3
rm -rf "$RUN"/c2-bridges/work/0* "$RUN"/c2-fastcall-agg/work/0*   # per-case builds (images of several MB each)
USED=$(( $(date +%s) - T0 ))

# --- report (counts and statuses only) ------------------------------------------------------------------------------
# counters followed from one link to the next (J-0111): "-" when the audit did not run
counter() {  # counter <audit> <line regex>: the number ending the first such line of its log (0: no such line)
  [ -f "$RUN/$1/out.log" ] && [ "${RC[$1]}" != skip ] && [ "${RC[$1]}" != timeout ] || { echo -; return; }
  local v; v=$(grep -m1 -E "$2" "$RUN/$1/out.log" | grep -oE '[0-9]+$' || true); echo "${v:-0}"
}
E=$RUN/c2-shapes/out.log   # its E group: "== E callee-pop ...: <n> unique pairs", then the tool verdicts by pair
E_SAME=$(grep -m1 -A1 -E '^== E callee-pop' "$E" 2>/dev/null | grep -oE "'code/same': [0-9]+" | grep -oE '[0-9]+$' || true)
E_ALL=$(grep -m1 -E '^== E callee-pop' "$E" 2>/dev/null | grep -oE '[0-9]+ unique pairs' | grep -oE '^[0-9]+' || true)
[ "$(counter c2-shapes .)" != - ] || E_SAME=-   # c2-shapes did not run
WATCH=$(printf -- '- C4 synthesized vtable objects (zz_vt_*) that define no typeinfo (want 0; t5): %s\n' \
    "$(counter c4-definers '^vtable object defines no typeinfo: synthesized')"
  printf -- '- C4 kept vtables whose object defines no typeinfo, all objects: %s\n' \
    "$(counter c4-definers '^vtable object defines no typeinfo +[0-9]+$')"
  printf -- '- C4 vtable groups naming a subobject the kept typeinfo lacks (12 on 9a0243c7eabe): %s\n' \
    "$(counter c4-vtti '^vtable names a subobject the kept typeinfo lacks')"
  printf -- "- C2 callee-pop mismatch pairs the tool binds 'same' (23 on 9a0243c7eabe): %s of %s\n" \
    "${E_SAME:-0}" "${E_ALL:--}")
holds=0; defects=""; skipped=""; stopped=""
for k in "${ORDER[@]}"; do
  case ${RC[$k]} in 0) holds=$((holds + 1)) ;; skip) skipped="$skipped $k" ;; timeout) stopped="$stopped $k" ;;
    *) defects="$defects $k" ;; esac
done
SUMMARY="${#ORDER[@]} audits in ${USED}s of ${BUDGET}s: $holds hold${defects:+; exit != 0:$defects}${stopped:+; stopped by the budget:$stopped}${skipped:+; skipped:$skipped}"
{
  printf '# V1-link %s\n\n- commit: %s\n- link: %s\n- %s\n- audits: exit 0 = holds / no defect reproduced\n\n' "$C12" \
    "$COMMIT" "${V1_LINK_DIR:+the directory given as V1_LINK_DIR}${V1_LINK_DIR:-branch link/$C12}" "$SUMMARY"
  printf '| audit | exit | seconds | note |\n|---|---:|---:|---|\n'
  for k in "${ORDER[@]}"; do printf '| %s | %s | %s | %s |\n' "$k" "${RC[$k]}" "${SECS[$k]}" "${NOTE[$k]}"; done
  printf '\n## watched counters\n\n%s\n\n' "$WATCH"
  for k in "${ORDER[@]}"; do
    [ -f "$RUN/$k/out.log" ] || continue
    printf '## %s\n\n```\n' "$k"
    grep -E '^((  )?[A-Za-z]|== |   tool verdicts).*[0-9]' "$RUN/$k/out.log" | grep -vE '__fnv|_Z|\?|0x[0-9a-f]{6}|[0-9a-f]{8}' \
      | tail -25 || true
    printf '```\n\n'
  done
} > "$REPORT"
echo "$SUMMARY" > "$RUN/summary.txt"
echo "$WATCH" > "$RUN/watched.txt"
copy_details() {  # copy_details <dir>: the report, the summary and each audit's small tables and logs
  mkdir -p "$1"
  (cd "$RUN" && find . -maxdepth 3 \( -name '*.tsv' -o -name '*.log' -o -name '*.txt' -o -name rc -o -name seconds \) \
     -size -20M -print0 | xargs -0 -r -I{} cp --parents {} "$1/") || true
  cp "$REPORT" "$1/report.md" 2>/dev/null || true
}
[ -n "${V1_OUT_DIR:-}" ] && copy_details "$V1_OUT_DIR"
if [ "$PUBLISH" = 1 ]; then
  tmp=$FARM_HOME/pub-v1-$$; rm -rf "$tmp"
  copy_details "$tmp"
  git -C "$tmp" init -q && git -C "$tmp" add -A && \
    git -C "$tmp" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "V1-link audits of $C12" && \
    git -C "$tmp" push -q -f "$ARTIFACTS_URL" "HEAD:refs/heads/v1/$C12" 2>/dev/null || echo "v1_job: push of v1/$C12 failed" >&2
  rm -rf "$tmp"
fi
log "report $REPORT"
