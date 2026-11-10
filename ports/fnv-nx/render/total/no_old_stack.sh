#!/bin/bash
# no_old_stack.sh - L6 grep gate: the removed parts of the old render stack and the forbidden hooks do not come back.
#   render/total/no_old_stack.sh [--strict]
# Exit 0 iff
#   (a) none of the REMOVED paths exists, and
#   (b) none of the REMOVED patterns occurs in L6-owned text (code, scripts, tests, docs), except on a line that says
#       "removed 2026-11-10" (a history note) and except in this file.
# --strict adds the PENDING class: parts whose removal is blocked by references from files other leads own
#   (render/native/n1, overrides.tsv, drop_units.txt, null device, Nine path ...). Without --strict they are only counted.
# Always prints the G-SKIP table: occurrences of Skip(, NX_PLAT_UNSUPPORTED, skipped++, droppedDraws per kept code file
# (the number to drive to 0; this script does not change it).
set -u
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd); cd "$ROOT" || exit 2
STRICT=0; [ "${1:-}" = "--strict" ] && STRICT=1
SELF=render/total/no_old_stack.sh
SCOPE=(render tests/s6n tests/s6b tests/v6n tests/l6 tools/compare)
DOCS=(docs/RENDER_NATIVE.md docs/RENDER_PERF.md docs/RENDER_VERIFY.md)

REMOVED_PATHS=(
  render/native/handover
  render/native/capture/d3d9_families.cpp render/native/capture/d3d9_families.h
  render/native/front/dynres.h tests/s6n/golden/dynres.png
  render/native/perf/nxd9_gpu.py render/native/perf/nxd9_import.py
  render/native/tools/native_dir.py render/native/tools/nxsp_cover.py render/native/tools/nxd9_census.py
  render/native/shaders/native.tsv render/native/shaders/is_dk.glsl
  render/native/shaders/d9_adts.vert.glsl render/native/shaders/d9_adts.frag.glsl
  render/native/shaders/d9_envmap.vert.glsl render/native/shaders/d9_envmap.frag.glsl
  render/native/shaders/d9_fogpass.vert.glsl render/native/shaders/d9_fogpass.frag.glsl
  render/native/shaders/is_quad.vert.glsl render/native/shaders/is_copy.frag.glsl render/native/shaders/is_blur.frag.glsl
  render/native/shaders/is_hdrdown.frag.glsl render/native/shaders/is_hdrblend.frag.glsl
  tests/s6n/test_d9_families.cpp render/verify/shaders/d9_parity.py render/verify/shaders/d9_synth.py
)
# fixed strings
REMOVED_PATTERNS=(
  d3d9_families nx_d9_classify nx_d9_pair NxD9Family NxD9Class nx_d3d9_set_families nx_d3d9_native_dir
  '--families' 'familyDraws' 'nativeShaders' 'nativeDraws'
  native_dir.py native.tsv nxd9_census nxd9_import nxd9_gpu nxsp_cover
  dynres.h DynResController DynResConfig S_dynres test_dynres
  d9_adts d9_envmap d9_fogpass is_hdrdown is_hdrblend
)
PENDING_PATHS=(
  render/native/n1 render/native/overrides.tsv render/native/drop_units.txt render/native/N1_PLAN.md render/native/REQUESTS.md
  render/native/tools/exe_vftables.py render/native/tools/nxsp_pack.py
  render/native/shaders/lit.vert.glsl render/native/shaders/lit.frag.glsl render/native/shaders/depth.vert.glsl
  render/native/shaders/land.vert.glsl render/native/shaders/sky.vert.glsl render/native/shaders/tile.vert.glsl
  render/native/shaders/nolight.vert.glsl render/native/shaders/menubg.vert.glsl render/native/shaders/grass.vert.glsl
  render/native/include/nx/families.h render/native/include/nx/skin.h
  render/native/perf/report.json render/native/perf/gpu_cost.py render/native/perf/engine_path.py
  toolchain/game/null_d3d9.cpp toolchain/game/gen_null_d3d9.py toolchain/game/native_device.cpp
  tests/s5/native_render.py tests/s5/native_render_inject.sh tests/s5/native_render_shims.cpp
  render/nine/build.sh render/nine/gen_proxy.py render/nine/switch_host.sh render/verify/build.sh
)
PENDING_PATTERNS=('render/native/n1' 'overrides.tsv' 'drop_units' 'libnxrender' 'engine_stubs' 'LINK_OVERRIDES=render' 'null_d3d9' 'native_device.cpp')

fail=0; pend=0
for p in "${REMOVED_PATHS[@]}"; do [ -e "$p" ] && { echo "REMOVED path is back: $p"; fail=$((fail+1)); }; done
files() { { find "${SCOPE[@]}" -type f \( -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.hpp' -o -name '*.py' -o -name '*.sh' \
    -o -name '*.mk' -o -name Makefile -o -name '*.md' -o -name '*.glsl' -o -name '*.tsv' -o -name '*.txt' -o -name '*.json' \) \
    -not -path '*/__pycache__/*' -not -path 'render/total/census/*' 2>/dev/null; printf '%s\n' "${DOCS[@]}"; } | grep -v -x "$SELF" | sort -u; }
FILES=$(files)
hits() {  # pattern -> "file:line" lines, history notes excluded
  echo "$FILES" | xargs -d '\n' grep -nIF -e "$1" 2>/dev/null | grep -v 'removed 2026-11-10' 
}
for pat in "${REMOVED_PATTERNS[@]}"; do
  h=$(hits "$pat"); [ -n "$h" ] && { echo "REMOVED pattern '$pat' occurs:"; echo "$h" | head -5 | sed 's/^/  /' | cut -c1-160; fail=$((fail+1)); }
done
pend_paths=0; for p in "${PENDING_PATHS[@]}"; do [ -e "$p" ] && pend_paths=$((pend_paths+1)); done
pend_pat=0; for pat in "${PENDING_PATTERNS[@]}"; do n=$(hits "$pat" | wc -l); pend_pat=$((pend_pat+n)); done
echo "PENDING (blocked by other leads' references): $pend_paths of ${#PENDING_PATHS[@]} paths still present, $pend_pat pattern lines in L6 text"
[ "$STRICT" = 1 ] && [ $((pend_paths+pend_pat)) -gt 0 ] && fail=$((fail+1))

echo "G-SKIP (Skip( | NX_PLAT_UNSUPPORTED | skipped++ | droppedDraws), kept code, per file:"
tot=0
while read -r f; do
  [ -f "$f" ] || continue
  a=$(grep -c 'Skip(' "$f"); b=$(grep -c 'NX_PLAT_UNSUPPORTED' "$f"); c=$(grep -c 'skipped++' "$f"); d=$(grep -c 'droppedDraws' "$f")
  n=$((a+b+c+d)); [ $n -gt 0 ] && { printf '  %-48s Skip(=%d NX_PLAT_UNSUPPORTED=%d skipped++=%d droppedDraws=%d  total %d\n' "$f" $a $b $c $d $n; tot=$((tot+n)); }
done < <(find render tests/s6n tests/l6 tools/compare -type f \( -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.inc' \) -not -path '*/__pycache__/*' | sort)
echo "G-SKIP total: $tot"
[ $fail -eq 0 ] && echo "no_old_stack: OK" || echo "no_old_stack: FAIL ($fail)"
[ $fail -eq 0 ]
