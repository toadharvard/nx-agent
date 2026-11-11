#!/bin/bash
# The packer's compiles go through the exact object cache (tools/link/objcache.py): one small shard (a module, 1 of 1) four times.
#   run 1 cold (every compile a miss, stored), run 2 warm (100 % hits, byte-identical lift_*.a and tsv outputs),
#   run 3 with FNVNX_LINK_OBJCACHE_VERIFY=1 (every compile run again and compared with the cache: 0 mismatches),
#   run 4 without the cache (FNVNX_LINK_OBJCACHE=none): the same bytes as the cached runs.
# Private inputs (exe-derived, never in the repository); env defaults are this container's:
#   FNVNX_EXE_DIR (dir with the module dlls), FNVNX_FACTS (facts dir with universe.tsv and <module>/universe.tsv),
#   FNVNX_MODULES (modules.tsv of L1's contract), MODULE (default libvorbisfile.dll), WORK (default /home/user/l3work/objcache-test).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
W=${WORK:-/home/user/l3work/objcache-test}; MODULE=${MODULE:-libvorbisfile.dll}
F=${FNVNX_FACTS:-/home/user/l3facts2}; D=${FNVNX_EXE_DIR:-/home/user/l3work/farm/in/gamedata_exe}
export FNVNX_UNIVERSE=$F/universe.tsv FNVNX_EXE=$D/FalloutNV.exe FNVNX_FACTS_OUT=$F PYTHONHASHSEED=0 LC_ALL=C
export FNVNX_MODULES=${FNVNX_MODULES:-/home/user/l3work/farm/in/l1-contract_mp4/modules/modules.tsv}
export FNVNX_MODDIRS=${FNVNX_MODDIRS:-$D:/home/user/l3work/farm/in/gamedata_redist}
export FNVNX_LIFTALL_CCDIR=$W/ccdir
rm -rf "$W"; mkdir -p "$W"
shard() {   # name, extra env...  -> $W/name/, $W/name.err ; prints seconds and the objcache line
    local n=$1; shift; local t0; t0=$(date +%s.%N)
    env "$@" python3 -s -B "$HERE/tools/liftall.py" --module "$MODULE" --shard 0/1 --out "$W/$n" --jobs "${JOBS:-3}" > "$W/$n.txt" 2> "$W/$n.err"
    python3 -c 'import sys,time; print("%s: %.1f s; %s" % (sys.argv[1], time.time() - float(sys.argv[2]), sys.argv[3]))' "$n" "$t0" "$(grep '^objcache' "$W/$n.err")"
}
export FNVNX_LINK_OBJCACHE=$W/cache
fail=0
shard cold FNVNX_LINK_OBJCACHE_VERIFY=0
shard warm FNVNX_LINK_OBJCACHE_VERIFY=0
shard verify FNVNX_LINK_OBJCACHE_VERIFY=1
shard none FNVNX_LINK_OBJCACHE=none
same() { for f in lift_0_of_1.a manifest_0.tsv missing_0.tsv forms_0.tsv undef_0.tsv; do cmp -s "$W/$1/$f" "$W/$2/$f" || return 1; done; }
objs() { (cd "$W/$1/obj" && sha256sum ./*.o | LC_ALL=C sort -k2); }
ok() { if (eval "$2") > /dev/null 2>&1; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
cold=$(grep '^objcache' "$W/cold.err"); warm=$(grep '^objcache' "$W/warm.err"); ver=$(grep '^objcache' "$W/verify.err")
ok "cold run: no hits, every compile stored" '[[ $cold == *" 0 hits"* && $cold == *stored* ]]'
ok "warm run: 100 % hits, nothing compiled" '[[ $warm == *"(100 %), 0 compiled"* ]]'
ok "warm outputs (.a, tsv) byte-identical to cold" 'same cold warm'
ok "warm objects byte-identical to cold" '[ "$(objs cold)" = "$(objs warm)" ]'
ok "verify run: every compile verified, 0 mismatches" '[[ $ver == *verified* && $ver != *MISMATCH* ]] && [ "$(grep -c MISMATCH "$W/verify.err")" = 0 ]'
ok "verify outputs byte-identical" 'same cold verify'
ok "no-cache outputs and objects byte-identical to cached" '[ "$(objs cold)" = "$(objs none)" ] && same cold none'
exit $fail
