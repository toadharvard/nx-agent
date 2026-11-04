#!/usr/bin/env bash
# v1_job.sh - farm job: V1-link's full-set audits (D14 verification of the link step, docs/LINK.md) on the link
# outputs of one commit. Long by nature (every reference, every vtable, every bridge class), so it runs here, not in
# a development session (D17).
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
#   msvc     the pristine decomp's MSVC 2008 objects, branch msvc/pristine/<decomp12> (as fuzz_job.sh); $V1_X86OBJ
#            instead. Used by C3 only; without them C3 is skipped.
#   ext      tools/fetch.sh's pinned checkouts in $V1_EXT (default $FARM_HOME/ext): the decomp's units.json (C3),
#            sse2neon; V1_NO_FETCH=1 uses $V1_EXT as it is (a shared checkout must not be moved).
# Audits (tools/verify/v1_link, each one process under nice, in this order; outputs in $RUN/<audit>/):
#   selftest      harness self-test (qemu ILP32 runner, wrong bridges caught, C5 scanner)
#   repro         the reproducers and adversarial cases (adversarial_abi, adversarial_r3, adversarial_cname,
#                 adversarial_typeinfo, chain_x87, repro_cname_abi, repro_typeinfo_mix): exit status per script
#   c1-bind       c1_bind.py, 160 random objects x 4 relocations, linked sites in trial.elf
#   c1-kinds      c1_kinds.py, every address-map row's kind + 60 sampled per kind
#   c2-bridges    c2_bridges.py stratified (6 per bridge class, 3 seeds) and V1_SELECT=fastcall-agg (every pair)
#   c2-shapes     c2_shapes.py (float/double results, fastcall aggregates, callee-pop mismatches, ...)
#   c3-ecx        c3_ecx.py, every bound reference against the definition's MSVC register use
#   c4-vtables    c4_vtables.py (V/G entries), c4_definers.py --audit (kept vtable vs kept typeinfo by object),
#                 c4_vtti.py (every vtable group against the kept typeinfo, whole image)
#   c5-pic        c5_pic.py, every rewritten object's relocations
# Output: <report.md> with each audit's summary lines and exit status (0 = holds), counts only; the detail files
# ($RUN, decomp-derived names and addresses) go to branch v1/<commit12> of the private artifacts repository
# ($V1_PUBLISH=0 skips the push). Resumable: an audit whose .done exists is not rerun.
#
# Needs: git, python3 with pyelftools and capstone (pip --user when missing), g++/binutils-aarch64-linux-gnu with
# ILP32 support, qemu-aarch64 (qemu-user), llvm-undname, c++filt; push access to toadharvard/fnv-nx-build.
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
RUN=$FARM_HOME/v1-$C12
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
python3 -c 'import elftools, capstone' 2>/dev/null || python3 -m pip install -q --user --break-system-packages \
  pyelftools==0.31 capstone==5.0.1
for t in qemu-aarch64 llvm-undname c++filt aarch64-linux-gnu-g++ aarch64-linux-gnu-ld; do
  command -v "$t" >/dev/null || fail_report 4 "missing tool: $t"
done
export SSE2NEON=${SSE2NEON:-$EXT/sse2neon} PYTHONDONTWRITEBYTECODE=1

# --- inputs -----------------------------------------------------------------------------------------------------
if [ -n "${V1_LINK_DIR:-}" ]; then LINK=$V1_LINK_DIR
else
  LINK=$FARM_HOME/link-$C12
  if [ ! -f "$LINK/.done" ]; then
    fetch_branch "link/$C12" "$LINK" || fail_report 3 "no link outputs for $C12 (branch link/$C12): a link job has to \
publish out/ and fixed/ first"
    touch "$LINK/.done"
  fi
fi
for f in out/refs.tsv out/abi.tsv out/addrmap.tsv out/aliases.ld fixed/zz_bridges.o; do   # bridges.S, objcopy/: optional
  [ -e "$LINK/$f" ] || fail_report 3 "link outputs for $C12 lack $f"
done
if [ ! -f "$LINK/out/trial.elf" ]; then
  log "trial link (fnvlink.py check)"
  nice -n 19 python3 "$P/tools/link/fnvlink.py" check "$LINK/out" "$LINK/fixed" > "$RUN/check.log" 2>&1 || true
fi
[ -f "$LINK/out/trial.elf" ] || fail_report 3 "no trial.elf for $C12"
if [ -n "${V1_TREE:-}" ]; then TREE=$V1_TREE
else
  TREE=$FARM_HOME/tree-$C12
  [ -f "$TREE/build/units.json" ] || bash "$HERE/fetch_tree.sh" "$COMMIT" "$TREE" 1800 > "$RUN/fetch_tree.log" 2>&1 \
    || fail_report 2 "no prepared tree t/$C12"
fi
UJ=$TREE/build/units.json
OBJ=${V1_OBJ_DIR:-$FARM_HOME/objs-$C12}
if [ ! -f "$OBJ/objects.tsv" ]; then
  M=${OBJ_SHARDS:-$(git ls-remote "$ARTIFACTS_URL" "refs/heads/c/$C12/*" 2>/dev/null \
      | sed -n 's|.*/\([0-9]*\)of\([0-9]*\)$|\2|p' | sort | uniq -c | awk '$1 == $2 {print $2}' | sort -n | head -1)}
  if [ -n "$M" ]; then ARTIFACTS=$ARTIFACTS bash "$HERE/fetch_objs.sh" "$COMMIT" "$M" "$OBJ" > "$RUN/fetch_objs.log" 2>&1 || true; fi
fi
[ "${V1_NO_FETCH:-0}" = 1 ] || bash "$P/tools/fetch.sh" "$EXT" >/dev/null 2>&1 || true   # V1_NO_FETCH=1: $EXT as is
DECOMP=$EXT/fnv_decomp
X86=${V1_X86OBJ:-}
if [ -z "$X86" ] && [ -d "$DECOMP/.git" ]; then
  D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
  ORACLE=$FARM_HOME/oracle-$D12
  [ -f "$ORACLE/.done" ] || { fetch_branch "msvc/pristine/$D12" "$ORACLE" && touch "$ORACLE/.done"; } || true
  [ -d "$ORACLE/obj" ] && X86=$ORACLE/obj
fi

# --- audits -------------------------------------------------------------------------------------------------------
declare -A RC
audit() {  # audit <name> <command...>: run once, keep the output and the exit status
  local name=$1; shift
  local d=$RUN/$name
  mkdir -p "$d"
  if [ ! -f "$d/.done" ]; then
    log "$name"
    set +e
    (cd "$d" && nice -n 19 "$@") > "$d/out.log" 2>&1
    echo $? > "$d/rc"
    set -e
    touch "$d/.done"
  fi
  RC[$name]=$(cat "$d/rc")
}
O=$LINK/out; F=$LINK/fixed
audit selftest python3 "$V/selftest_harness.py" "$RUN/selftest/work"
for s in adversarial_abi adversarial_r3 adversarial_cname adversarial_typeinfo chain_x87 repro_cname_abi \
         repro_typeinfo_mix; do
  audit "repro-$s" python3 "$V/$s.py" "$RUN/repro-$s/work"
done
if [ -f "$OBJ/objects.tsv" ]; then
  audit c1-bind python3 "$V/c1_bind.py" "$OBJ/objects.tsv" "$UJ" "$F" "$O/trial.elf" "$O/refs.tsv" "$RUN/c1-bind" 160 4
fi
audit c1-kinds python3 "$V/c1_kinds.py" "$O/addrmap.tsv" "$UJ" "$O/trial.elf" "$RUN/c1-kinds" 60
audit c2-bridges python3 "$V/c2_bridges.py" "$O" "$UJ" "$RUN/c2-bridges/work" 6 3
audit c2-fastcall-agg env V1_SELECT=fastcall-agg python3 "$V/c2_bridges.py" "$O" "$UJ" "$RUN/c2-fastcall-agg/work" 6 1
audit c2-shapes python3 "$V/c2_shapes.py" "$O" "$UJ" "$RUN/c2-shapes/work"
if [ -n "$X86" ] && [ -f "$DECOMP/build/units.json" ]; then
  audit c3-ecx python3 "$V/c3_ecx.py" "$O" "$UJ" "$DECOMP/build/units.json" "$X86" "$RUN/c3-ecx/work"
fi
audit c4-vtables python3 "$V/c4_vtables.py" "$O/trial.elf" "$O/addrmap.tsv" "$F" "$RUN/c4-vtables/c4.tsv"
audit c4-definers python3 "$V/c4_definers.py" --audit "$F" "$O/trial.elf" "$RUN/c4-definers/audit.tsv"
audit c4-vtti python3 "$V/c4_vtti.py" "$O/trial.elf" "$RUN/c4-vtti/vtti.tsv"
C5MAP=$O/objcopy; [ -d "$C5MAP" ] || C5MAP=$OBJ/objects.tsv     # farm links: original objects by name
audit c5-pic python3 "$V/c5_pic.py" "$F" "$C5MAP" "$UJ" "$RUN/c5-pic/c5.tsv"

# --- report (counts and statuses only) ------------------------------------------------------------------------------
{
  printf '# V1-link %s\n\n- commit: %s\n- link: %s\n- audits: exit 0 = holds / no defect reproduced\n\n' "$C12" "$COMMIT" \
    "${V1_LINK_DIR:-link/$C12}"
  printf '| audit | exit |\n|---|---:|\n'
  for k in $(printf '%s\n' "${!RC[@]}" | sort); do printf '| %s | %s |\n' "$k" "${RC[$k]}"; done
  printf '\n'
  for k in $(printf '%s\n' "${!RC[@]}" | sort); do
    printf '## %s\n\n```\n' "$k"
    grep -E '^(  )?[A-Za-z].*[0-9]' "$RUN/$k/out.log" | grep -vE '__fnv|_Z|\?|0x[0-9a-f]{6}|[0-9a-f]{8}' | tail -25 || true
    printf '```\n\n'
  done
} > "$REPORT"
if [ "$PUBLISH" = 1 ]; then
  tmp=$FARM_HOME/pub-v1-$$; rm -rf "$tmp"; mkdir -p "$tmp"
  (cd "$RUN" && find . -maxdepth 3 \( -name '*.tsv' -o -name '*.log' -o -name '*.txt' -o -name rc \) \
     -size -20M -print0 | xargs -0 -I{} cp --parents {} "$tmp/") || true
  git -C "$tmp" init -q && git -C "$tmp" add -A && \
    git -C "$tmp" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "V1-link audits of $C12" && \
    git -C "$tmp" push -q -f "$ARTIFACTS_URL" "HEAD:refs/heads/v1/$C12" 2>/dev/null || echo "v1_job: push of v1/$C12 failed" >&2
  rm -rf "$tmp"
fi
log "report $REPORT"
