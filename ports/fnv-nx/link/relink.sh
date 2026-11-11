#!/bin/bash
# relink.sh - one command from published inputs to a strict-linked guest ELF, its gates and (optionally) one emulator run.
#
#   relink.sh --pack ID --bridge REV --platform REV --render ID --out DIR [--run] [--run-only]
#
#   --pack ID        fnv-nx-build branch l3a-lift/pack/ID (L3a pack; its own shards.txt names every shard branch)
#   --bridge REV     commit of fnv-nx-build branch l5-platform/manifest whose bridge.tsv is the bridge table (SHA-256 = table_id)
#   --platform REV   commit of nx-agent branch claude/fnv-l5-platform (the platform sources, built here with -fPIE)
#   --render ID      fnv-nx-build branch l6-render/bridge/ID (libfnvnx-d3d9.a)
#   --out DIR        output: guest.elf, manifest.json, gates.txt, milestones.tsv, keys.tsv, entries.tsv, inputs.rsp, libmembers.txt, gates/*.json, run.txt
#   --run            after a green link, run the ELF once in the emulator (600 s wall limit) and write run.txt
#   --run-only       only the emulator step, on the guest.elf already in DIR (start it with run_in_background)
# Environment (defaults in brackets):
#   ARTIFACTS   clone of the private fnv-nx-build repository [/home/user/fnv-nx-build]
#   DATA        cache of fetched game data and tool inputs [/home/user/l1data]
#   WORK        scratch (builds, fetched shards; reused between runs) [<out>/work]
#   TC_KEY      toolchain id for tools/farm/hostcache.sh [1fc1f7b1ef44]; TC_DIR its restore directory [/root/fnvnx-farm/toolchain-$TC_KEY]
#   FACTS_REV   commit of branch l3a-lift/facts (universes) [tip]; CONTRACT_BRANCH branch with facts.json and n1.json [l1-contract/mp4]
#   RUN_SECONDS emulator wall limit [600]
# Prints at most 15 lines of numbers. Any missing input or red gate: exit 1 with the reason on stderr.
#
# Layout assumptions (stated, checked where a check exists):
#   - pack branch: shards.txt (one shard branch per line: l3a-lift/obj/TAG/KofN for the exe, l3a-lift/obj/TAG-MODULE/KofN for a static module),
#     disp.o, rt.a, entries.tsv (last line `# table_id <sha256 of bridge.tsv>`: checked against --bridge), optional missing.tsv. A shard branch holds
#     lift_K_of_N.a. Old packs (exe only) and new packs (exe + static modules, one disp.o) are the same rule: the modules are the groups of shards.txt.
#   - each module group M needs M/universe.tsv in l3a-lift/facts; the disp gate checks the union of the exe universe and those.
#   - link order: rt, loader, platform, pack_only (disp.o rt.a), d3d9, lift (exe shards), then one directory per module in shards.txt order.
#   - the toolchain restore, game data branches (gamedata/exe, gamedata/redist), tools/{mc,nls,freetype,minimp3} follow build_guest.sh's inputs.
set -euo pipefail
export LC_ALL=C PYTHONHASHSEED=0
die() { echo "relink: $*" >&2; exit 1; }

PACK= BRIDGE= PLATFORM= RENDER= OUT= DO_RUN=0 RUN_ONLY=0
while [ $# -gt 0 ]; do
    case $1 in
        --pack) PACK=${2:?}; shift 2;; --bridge) BRIDGE=${2:?}; shift 2;; --platform) PLATFORM=${2:?}; shift 2;;
        --render) RENDER=${2:?}; shift 2;; --out) OUT=${2:?}; shift 2;; --run) DO_RUN=1; shift;; --run-only) RUN_ONLY=1; shift;;
        *) die "unknown argument $1 (usage: --pack ID --bridge REV --platform REV --render ID --out DIR [--run|--run-only])";;
    esac
done
[ -n "$OUT" ] || die "--out DIR is required"
mkdir -p "$OUT"; OUT=$(realpath "$OUT")
if [ "$RUN_ONLY" = 0 ]; then
    [ -n "$PACK" ] && [ -n "$BRIDGE" ] && [ -n "$PLATFORM" ] && [ -n "$RENDER" ] || die "--pack, --bridge, --platform, --render and --out are required"
fi

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)            # ports/fnv-nx of the checkout that holds this script
REPO=$(git -C "$SRC" rev-parse --show-toplevel)
ARTIFACTS=${ARTIFACTS:-/home/user/fnv-nx-build}; DATA=${DATA:-/home/user/l1data}; WORK=${WORK:-$OUT/work}
TC_KEY=${TC_KEY:-1fc1f7b1ef44}; TC_DIR=${TC_DIR:-/root/fnvnx-farm/toolchain-$TC_KEY}
CONTRACT_BRANCH=${CONTRACT_BRANCH:-l1-contract/mp4}; RUN_SECONDS=${RUN_SECONDS:-600}
TC=$TC_DIR/toolchain; PFX=$TC/prefix; DKP=$TC/dkp
mkdir -p "$WORK" "$DATA"; WORK=$(realpath "$WORK"); DATA=$(realpath "$DATA")
EXE=$DATA/gamedata_exe/FalloutNV.exe

# fetch BRANCH DEST [REV]: the branch (or its commit REV) of fnv-nx-build unpacked into DEST (sequential, 3 tries; history deepened when REV is not the tip)
fetch() {
    local br=$1 dest=$2 rev=${3:-} ref=refs/relink/$1 i d
    [ -e "$ARTIFACTS/.git" ] || [ -f "$ARTIFACTS/HEAD" ] || die "ARTIFACTS=$ARTIFACTS is not a git clone of fnv-nx-build"
    for i in 1 2 3; do
        git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/$br:$ref" 2>/dev/null && break
        [ $i = 3 ] && die "cannot fetch branch $br"; sleep 5
    done
    if [ -n "$rev" ] && ! git -C "$ARTIFACTS" cat-file -e "$rev^{commit}" 2>/dev/null; then
        for d in 50 400; do
            git -C "$ARTIFACTS" fetch -q --depth $d origin "+refs/heads/$br:$ref" 2>/dev/null || true
            git -C "$ARTIFACTS" cat-file -e "$rev^{commit}" 2>/dev/null && break
        done
        git -C "$ARTIFACTS" cat-file -e "$rev^{commit}" 2>/dev/null || die "commit $rev is not in branch $br"
    fi
    rm -rf "$dest"; mkdir -p "$dest"
    git -C "$ARTIFACTS" archive "${rev:-$ref}" | tar -x -C "$dest"
}
sha() { sha256sum "$1" | cut -d' ' -f1; }
now() { date +%s; }
# wall (s) and peak RSS (MB) of a command, written to the first argument as "wall rss"
timed() { python3 -I -c 'import resource,subprocess,sys,time;t=time.time();r=subprocess.call(sys.argv[2:]);u=resource.getrusage(resource.RUSAGE_CHILDREN)
open(sys.argv[1],"w").write("%.1f %d\n"%(time.time()-t,u.ru_maxrss//1024));sys.exit(r)' "$@"; }

# ---------------------------------------------------------------------------------------------------------------- emulator run
run_emulator() {
    [ -f "$OUT/guest.elf" ] || die "no guest.elf in $OUT"
    [ -f "$OUT/modules/modules.tsv" ] || die "no modules/modules.tsv in $OUT (link first)"
    local R=$OUT/run rc=0 t0; rm -rf "$R"; mkdir -p "$R/shim" "$R/game" "$R/root"
    python3 -c 'import unicorn' 2>/dev/null || die "python module unicorn missing (pip install unicorn)"
    for t in nm readelf c++filt gcc objdump; do ln -sf "$PFX/bin/aarch64-none-elf-$t" "$R/shim/aarch64-linux-gnu-$t"; done   # emu_guest.py's tool names
    ln -sf "$EXE" "$R/game/FalloutNV.exe"
    for d in "$DATA/gamedata_exe" "$DATA/gamedata_redist"; do
        for f in "$d"/*.dll; do [ -e "$f" ] && ln -sf "$f" "$R/game/$(basename "$f" | tr A-Z a-z)"; done
    done
    python3 -I "$SRC/toolchain/tools/elf2nso.py" "$OUT/guest.elf" "$R/guest.nso" >/dev/null 2>&1 || die "elf2nso.py failed"
    t0=$(now)
    PATH="$R/shim:$PATH" E2E_EXE=$EXE E2E_MODULES=$OUT/modules/modules.tsv E2E_MODDIR=$R/game \
        python3 -I "$SRC/loader/guest/e2e_host.py" "$R/guest.nso" --root "$R/root" --game "$R/game" --elf "$OUT/guest.elf" \
        --wall-seconds "$RUN_SECONDS" --summary-json "$R/summary.json" --icount "$R/icount.json" \
        --expect "fnvnx-start: identity ok" >"$R/run.log" 2>&1 || rc=$?
    python3 -I - "$R" "$rc" "$(( $(now) - t0 ))" >"$OUT/run.txt" <<'PY'
import json, os, re, sys
R, host_rc, wall = sys.argv[1:4]
def load(n):
    try:
        return json.load(open(os.path.join(R, n)))
    except Exception:
        return {}
log = open(os.path.join(R, 'run.log'), errors='replace').read().splitlines()
s, ic = load('summary.json'), load('icount.json')
starts = [l.strip() for l in log if 'fnvnx-start:' in l]
ms = s.get('milestones') or []
reached = [m['name'] for m in ms if m.get('reached')]
stop = s.get('stop') or {}
def total(o):
    if isinstance(o, dict):
        for k in ('total', 'instructions', 'executed'):
            if isinstance(o.get(k), int):
                return o[k]
        t = [total(v) for v in o.values()]
        t = [x for x in t if x is not None]
        return sum(t) if t and isinstance(o.get('threads'), (dict, list)) else None
    return None
print('host exit %s, wall %s s, stop: %s, guest exit code %s' % (host_rc, wall, stop.get('reason'), stop.get('exit_code')))
print('last start line: %s' % (starts[-1] if starts else 'none'))
print('instructions executed: %s' % (total(ic) if total(ic) is not None else 'see icount.json'))
print('host calls: %s, threads: %s' % (s.get('host_calls'), s.get('threads')))
print('milestones reached %s of %s: %s' % (len(reached), len(ms), ' '.join(reached) or 'none'))
PY
    echo "run: $(sed -n 1p "$OUT/run.txt")"; sed -n 2,5p "$OUT/run.txt"
}
if [ "$RUN_ONLY" = 1 ]; then run_emulator; exit 0; fi

# ---------------------------------------------------------------------------------------------------------------- inputs
T_START=$(now)
for t in git python3 tar make bash; do command -v "$t" >/dev/null || die "missing tool $t"; done
if [ ! -f "$DKP/devkitA64/bin/aarch64-none-elf-ld" ]; then
    ARTIFACTS=$ARTIFACTS bash "$REPO/tools/farm/hostcache.sh" fetch toolchain "$TC_KEY" "$TC_DIR" >"$WORK/hostcache.log" 2>&1 || die "toolchain restore failed (see $WORK/hostcache.log)"
fi
[ -x "$PFX/bin/aarch64-none-elf-gcc" ] && [ -x "$DKP/devkitA64/bin/aarch64-none-elf-ld" ] || die "toolchain $TC_KEY incomplete in $TC_DIR"
LD=$DKP/devkitA64/bin/aarch64-none-elf-ld
[ -f "$EXE" ] || fetch gamedata/exe "$DATA/gamedata_exe"
[ -d "$DATA/gamedata_redist" ] || fetch gamedata/redist "$DATA/gamedata_redist"
[ -f "$EXE" ] || die "FalloutNV.exe missing in $DATA/gamedata_exe"
[ -f "$DATA/contract/facts.json" ] || fetch "$CONTRACT_BRANCH" "$DATA/contract"
[ -f "$DATA/contract/n1.json" ] || die "n1.json missing in branch $CONTRACT_BRANCH"
for t in mc nls freetype minimp3; do [ -d "$DATA/tools/$t" ] || fetch "tools/$t" "$DATA/tools/$t"; done

I=$WORK/in; rm -rf "$I" "$WORK/bridge" "$WORK/pack" "$WORK/facts" "$WORK/render"; mkdir -p "$I"/{rt,loader,platform,pack_only,d3d9,lift}
fetch l5-platform/manifest "$WORK/bridge" "$BRIDGE"; BR=$WORK/bridge/manifest/bridge.tsv; [ -f "$BR" ] || die "manifest/bridge.tsv not in l5-platform/manifest $BRIDGE"
TABLE_ID=$(sha "$BR")
fetch "l3a-lift/pack/$PACK" "$WORK/pack"
for f in shards.txt disp.o rt.a entries.tsv; do [ -f "$WORK/pack/$f" ] || die "pack $PACK has no $f"; done
PACK_TID=$(tail -n 1 "$WORK/pack/entries.tsv" | sed -n 's/^# table_id //p')
[ "$PACK_TID" = "$TABLE_ID" ] || die "pack $PACK was built for table_id ${PACK_TID:-none}, bridge $BRIDGE has $TABLE_ID"
fetch l3a-lift/facts "$WORK/facts" "${FACTS_REV:-}"
FACTS_HEAD=$(git -C "$ARTIFACTS" rev-parse refs/relink/l3a-lift/facts)
cp "$WORK/pack/disp.o" "$WORK/pack/rt.a" "$I/pack_only/"
fetch "l6-render/bridge/$RENDER" "$WORK/render"; [ -f "$WORK/render/libfnvnx-d3d9.a" ] || die "render bridge $RENDER has no libfnvnx-d3d9.a"
cp "$WORK/render/libfnvnx-d3d9.a" "$I/d3d9/"

# shards: every line of shards.txt; group = lift (exe) or lift_<module>, in order of first appearance
GROUPS_=(lift); NSH=0
[ -f "$WORK/facts/universe.tsv" ] || die "facts have no universe.tsv"
grep -v '^#' "$WORK/facts/universe.tsv" >"$WORK/universe.tsv" || true
while read -r br; do
    [ -n "$br" ] || continue
    [[ $br =~ ^l3a-lift/obj/([^/-]+)(-([^/]+))?/([0-9]+)of([0-9]+)$ ]] || die "shards.txt: unrecognized shard branch $br"
    mod=${BASH_REMATCH[3]}; K=${BASH_REMATCH[4]}; N=${BASH_REMATCH[5]}; g=lift${mod:+_$mod}
    if [ ! -d "$I/$g" ]; then
        mkdir -p "$I/$g"; GROUPS_+=("$g")
        [ -f "$WORK/facts/$mod/universe.tsv" ] || die "facts have no $mod/universe.tsv"
        grep -v '^#' "$WORK/facts/$mod/universe.tsv" >>"$WORK/universe.tsv" || true
    fi
    cache=$DATA/obj/$(echo "$br" | tr / _)
    if [ ! -f "$cache/lift_${K}_of_${N}.a" ]; then fetch "$br" "$cache.tmp"; rm -rf "$cache"; mv "$cache.tmp" "$cache"; fi
    [ -f "$cache/lift_${K}_of_${N}.a" ] || die "shard $br has no lift_${K}_of_${N}.a"
    cp "$cache/lift_${K}_of_${N}.a" "$I/$g/"; NSH=$((NSH + 1))
done <"$WORK/pack/shards.txt"
[ "$NSH" -gt 0 ] || die "shards.txt is empty"

# ---------------------------------------------------------------------------------------------------------------- contract files
mkdir -p "$OUT/modules"
python3 -I "$SRC/contract/modules.py" --exe "$EXE" --bridge "$BR" --dir "$DATA/gamedata_exe" --dir "$DATA/gamedata_redist" --out-dir "$OUT/modules" \
    >"$WORK/modules.log" 2>&1 || die "modules.py failed (see $WORK/modules.log)"
margs=(); for f in "$OUT"/modules/exports/*.tsv; do [ -e "$f" ] && margs+=(--module "$(basename "$f" .tsv)=$f"); done
python3 -I "$SRC/contract/iat.py" --facts "$DATA/contract/facts.json" --bridge "$BR" "${margs[@]}" --out "$OUT/iat.tsv" --keys-out "$OUT/keys.tsv" \
    --c-out "$WORK/import_keys.c" >"$WORK/iat.log" 2>&1 || die "iat.py failed (see $WORK/iat.log)"
python3 -I "$SRC/loader/tools/mkidentity.py" --exe "$EXE" --out "$WORK/identity.c" >/dev/null || die "mkidentity.py failed"
cp "$WORK/pack/entries.tsv" "$OUT/entries.tsv"; cp "$BR" "$OUT/bridge.tsv"

# ---------------------------------------------------------------------------------------------------------------- builds (-fPIE)
export DEVKITPRO=$DKP
(cd "$WORK" && "$SRC/toolchain/guest/build.sh" "$WORK/rt" >"$WORK/rt.log" 2>&1) || die "guest runtime build failed (see $WORK/rt.log)"
(cd "$WORK" && "$SRC/loader/guest/build.sh" "$WORK/ldr" --keys-c "$WORK/import_keys.c" --identity-c "$WORK/identity.c" \
    --modules-c "$OUT/modules/modules.c" >"$WORK/ldr.log" 2>&1) || die "loader build failed (see $WORK/ldr.log)"
cp "$WORK/rt/crt0.o" "$WORK/rt/libfnvnx-guest.a" "$I/rt/"; cp "$WORK/ldr/libfnvnx-loader.a" "$I/loader/"
# platform: its own worktree of the given commit (build_guest.sh builds the tree it lives in)
git -C "$REPO" cat-file -e "$PLATFORM^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin claude/fnv-l5-platform || true
git -C "$REPO" cat-file -e "$PLATFORM^{commit}" 2>/dev/null || die "platform commit $PLATFORM not found (branch claude/fnv-l5-platform)"
PLAT_FULL=$(git -C "$REPO" rev-parse "$PLATFORM^{commit}")
PW=$WORK/plat-$PLAT_FULL; git -C "$REPO" worktree remove --force "$PW" 2>/dev/null || true; rm -rf "$PW"; git -C "$REPO" worktree prune
git -C "$REPO" worktree add -q --detach "$PW" "$PLAT_FULL" || die "worktree of $PLATFORM failed"
rm -rf "$WORK/ext"; mkdir -p "$WORK/ext"; cp -r "$DATA/tools/minimp3" "$WORK/ext/minimp3"
sh "$PW/ports/fnv-nx/platform/win32/tools/fetch_freetype.sh" "$DATA/tools/freetype" "$WORK/ext/freetype" >/dev/null 2>&1 || die "freetype unpack failed"
ln -sfn "$WORK/ext" "$PW/ports/fnv-nx/ext"                    # tests/s4/Makefile reads FreeType from <fnv-nx>/ext
FNVNX_TC=$PFX EXT=$PW/ports/fnv-nx/ext NLS_DIR=$DATA/tools/nls/nls MC_DIR=$DATA/tools/mc LIBNX_INC=$DKP/libnx/include GUEST_RT=$WORK/rt \
    bash "$PW/ports/fnv-nx/platform/build_guest.sh" "$WORK/platform" >"$WORK/platform.log" 2>&1 || die "platform build failed (see $WORK/platform.log)"
cp "$WORK/platform/libfnvnx-platform.a" "$I/platform/"
git -C "$REPO" worktree remove --force "$PW" 2>/dev/null || true

# ---------------------------------------------------------------------------------------------------------------- link
GCC=$PFX/bin/aarch64-none-elf-gcc
where() { realpath -m "$("$GCC" -mabi=ilp32 -fPIC -print-file-name="$1")"; }
libdirs=(); for n in stdc++ m gcc c sysbase; do d=$(dirname "$(where lib$n.a)"); [[ " ${libdirs[*]} " == *" --lib-dir=$d "* ]] || libdirs+=("--lib-dir=$d"); done
ins=(); for d in rt loader platform pack_only d3d9 "${GROUPS_[@]}"; do ins+=(--in "$I/$d"); done
rm -rf "$OUT/ld"; lrc=0
( ulimit -v 12000000; timed "$OUT/.timing" python3 -I "$SRC/link/ld.py" --ld "$LD" --ld-arg=-maarch64elf32 --ld-arg=-EL --ld-arg=-X \
    --script "$SRC/link/guest.ld" "${ins[@]}" --out "$OUT/ld" "${libdirs[@]}" --lib=stdc++ --lib=m --lib=gcc --lib=c --lib=sysbase \
    --startfile="$(where crti.o)" --startfile="$(where crtbegin.o)" --endfile="$(where crtend.o)" --endfile="$(where crtn.o)" ) >"$OUT/ld.log" 2>&1 || lrc=$?
read -r LD_WALL LD_RSS <"$OUT/.timing" || true
if [ $lrc != 0 ] || [ ! -f "$OUT/ld/guest.elf" ]; then
    tail -n 20 "$OUT/ld.log" >&2
    [ -f "$OUT/ld/undefined.tsv" ] && echo "undefined symbols: $(wc -l <"$OUT/ld/undefined.tsv") (see $OUT/ld/undefined.tsv)" >&2
    die "RED: strict link failed (exit $lrc), log $OUT/ld.log"
fi
mv -f "$OUT/ld/guest.elf" "$OUT/guest.elf"; cp "$OUT/ld/inputs.rsp" "$OUT/ld/libmembers.txt" "$OUT/"
LDLINE=$(grep -E '[0-9]+ undefined' "$OUT/ld.log" | tail -n 1 || true)
UNDEF=$(sed -n 's/.* \([0-9]*\) undefined (\([0-9]*\) weak only).*/\1/p' <<<"$LDLINE"); WEAK=$(sed -n 's/.* \([0-9]*\) undefined (\([0-9]*\) weak only).*/\2/p' <<<"$LDLINE")
RELR=$(sed -n 's/.*RELR \([0-9]*\).*/\1/p' <<<"$LDLINE" | head -n 1); NIN=$(grep -E '^[0-9]+ inputs' "$OUT/ld.log" | head -n 1 | cut -d' ' -f1 || true)
[ "${UNDEF:-x}" = 0 ] && [ "${WEAK:-x}" = 0 ] || die "RED: undefined/weak = ${UNDEF:-?}/${WEAK:-?} (log $OUT/ld.log)"

# ---------------------------------------------------------------------------------------------------------------- gates
export GATE_OUT=$OUT/gates; rm -rf "$GATE_OUT"; mkdir -p "$GATE_OUT"
mfile=(); [ -f "$WORK/pack/missing.tsv" ] && mfile=(--missing "$WORK/pack/missing.tsv")
red=0; : >"$OUT/gates.txt"
gate() { local n=$1 rc=0; shift; python3 -I "$SRC/link/gate_$n.py" "$@" >"$GATE_OUT/$n.out" 2>&1 || rc=$?
    echo "$n rc=$rc: $(tail -n 1 "$GATE_OUT/$n.out")" >>"$OUT/gates.txt"; [ $rc = 0 ] || red=1; }
gate disp --elf "$OUT/guest.elf" --universe "$WORK/universe.tsv" --entries "$OUT/entries.tsv" --keys "$OUT/keys.tsv" "${mfile[@]}"
gate pcrel --elf "$OUT/guest.elf" --inputs "$OUT/inputs.rsp"
gate abs --inputs "$OUT/inputs.rsp" --elf "$OUT/guest.elf"
MS=ok
python3 -I "$SRC/link/milestones.py" --exe "$EXE" --modules "$OUT/modules/modules.tsv" --n1 "$DATA/contract/n1.json" --keys "$OUT/keys.tsv" \
    --entries "$OUT/entries.tsv" --out "$OUT/milestones.tsv" >"$GATE_OUT/milestones.out" 2>&1 || { MS=red; red=1; }
echo "milestones: $MS: $(wc -l <"$OUT/milestones.tsv" 2>/dev/null || echo 0) rows" >>"$OUT/gates.txt"

# ---------------------------------------------------------------------------------------------------------------- manifest
# render archive sha: the very file in the linker's --in directory "$I/d3d9" (see ins=() above), never a separate copy
RENDER_A=$I/d3d9/libfnvnx-d3d9.a
[ -n "$RENDER" ] && [ -f "$RENDER_A" ] || die "manifest: render archive $RENDER_A missing (--render ${RENDER:-not given})"
RENDER_SHA=$(sha "$RENDER_A"); [[ "$RENDER_SHA" =~ ^[0-9a-f]{64}$ ]] || die "manifest: bad sha256 of $RENDER_A"
ELF_SHA=$(sha "$OUT/guest.elf"); ELF_SIZE=$(stat -c %s "$OUT/guest.elf")
python3 -I - "$OUT" "$ELF_SHA" "$ELF_SIZE" "$PACK" "$BRIDGE" "$TABLE_ID" "$PLAT_FULL" "$RENDER" "$TC_KEY" "$(git -C "$REPO" rev-parse HEAD)" \
    "$FACTS_HEAD" "$red" "${UNDEF:-0}" "${WEAK:-0}" "${RELR:-0}" "${NIN:-0}" "${LD_WALL:-0}" "${LD_RSS:-0}" "$RENDER_SHA" <<'PY'
import json, os, sys
a = sys.argv
out = a[1]
gates = {}
for n in ('disp', 'pcrel', 'abs'):
    p = os.path.join(out, 'gates', n + '.json')
    try:
        gates[n] = json.load(open(p)).get('status', 'unknown')
    except Exception:
        gates[n] = 'missing'
m = {'format': 'fnvnx-relink/1', 'elf': {'sha256': a[2], 'bytes': int(a[3])},
     'inputs': {'pack': a[4], 'bridge_rev': a[5], 'table_id': a[6], 'platform': a[7], 'render': a[8], 'toolchain': a[9],
                'relink_repo_head': a[10], 'facts': a[11]},
     'ld': {'undefined': int(a[13]), 'weak': int(a[14]), 'duplicates': 0, 'relr': int(a[15]), 'inputs': int(a[16]),
            'wall_s': float(a[17]), 'peak_rss_mb': int(a[18])},
     'render': {'branch': 'l6-render/bridge/' + a[8], 'archive_sha256': a[19]},
     'gates': gates, 'green': a[12] == '0'}
json.dump(m, open(os.path.join(out, 'manifest.json'), 'w'), indent=1, sort_keys=True)
PY
rm -f "$OUT/.timing"

# ---------------------------------------------------------------------------------------------------------------- summary
DISP=$(sed -n 's/.*: \([0-9]*\) keys, \([0-9]*\) certified.*/\1\/\2/p' "$GATE_OUT/disp.out" | tail -n 1)
PCREL=$(sed -n 's/.*: \([0-9]*\); examined \([0-9]*\).*/\1 of \2/p' "$GATE_OUT/pcrel.out" | tail -n 1)
ABS=$(sed -n 's/.*link: \([0-9]*\) of \([0-9]*\) items.*/\1 of \2/p' "$GATE_OUT/abs.out" | tail -n 1)
echo "elf sha256 $ELF_SHA bytes $ELF_SIZE"
echo "pack $PACK shards $NSH groups ${#GROUPS_[@]} inputs ${NIN:-?} table_id ${TABLE_ID:0:12}"
echo "ld undefined/weak/duplicates ${UNDEF:-?}/${WEAK:-?}/0 RELR ${RELR:-?} wall ${LD_WALL:-?} s peak RSS ${LD_RSS:-?} MB"
echo "disp keys/certified ${DISP:-?} pcrel ${PCREL:-?} abs ${ABS:-?}"
echo "milestones $MS, gates $([ $red = 0 ] && echo green || echo RED), total $(( $(now) - T_START )) s"
if [ $red != 0 ]; then cat "$OUT/gates.txt" >&2; die "RED gate (see $OUT/gates.txt, $GATE_OUT)"; fi
if [ "$DO_RUN" = 1 ]; then run_emulator; fi
