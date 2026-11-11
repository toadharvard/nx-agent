#!/usr/bin/env bash
# run_real.sh - the R1c pipeline on the recordings of the original (needs game-derived data: NOT part of test_shcache.py).
#   run_real.sh WORK            WORK: scratch dir (default /home/user/work/l6/shcache) holding build/ (render/shaders/build.sh output, see below) and
#                               rec/<profile>/<scene>.d9s (decompressed D9S1 streams); results in WORK/out/.
# Needs: FNVNX_SHADERS_DEPS=WORK/build/deps FNVNX_SHADERS_BUILD=WORK/build/host render/shaders/build.sh done, and the uam determinism patch applied to deps/uam
# (part of render/shaders/uam.patch since 2026-11-11) and libuam.a rebuilt. Never writes into the repository.
set -euo pipefail
W="${1:-/home/user/work/l6/shcache}"; R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; C="$R/render/shaders/cache"; N="$R/render/native"
B="$W/build"; O="$W/out"; D="$B/deps"; mkdir -p "$O"
LIBS="$B/host/libfnvnx_shaders.a $D/mojoshader/build/libmojoshader.a $D/uam/build/libuam.a -lm -lpthread"
gcc -O2 -std=gnu11 -I "$R/render/shaders/include" -I "$C" -c "$C/shxlate.c" -o "$B/shxlate.o"; gcc -O2 -std=gnu11 -c "$C/shcache_key.c" -o "$B/shcache_key.o"
g++ "$B/shxlate.o" "$B/shcache_key.o" $LIBS -o "$B/shxlate"
gcc -std=gnu11 -O1 -w -I "$R/render/gl4" -I "$R/render/backend" -I "$N" -I "$N/gl" "$R/tests/l6/shcache/glcheck.c" "$N/gl/nxgl.c" -lEGL -ldl -lm -o "$B/glcheck"
for m in 64 32; do gcc -m$m -std=gnu11 -w -O1 -I "$C" "$R/tests/l6/shcache/t_shcache.c" "$C/shcache.c" "$C/shcache_key.c" -o "$B/t_shcache$m"; done
spec_for() {   # the d3d9_spec.json a stream was recorded with (its sha256 is in the stream header): the current one or the one from git history
  local sha f c; sha="$(python3 -I -c "print(open('$1','rb').read(40)[8:].hex())")"; f="$W/spec/$sha.json"; mkdir -p "$W/spec"
  if [[ ! -f "$f" ]]; then for c in $(git -C "$R" log --format=%H -- render/d3d9/spec/d3d9_spec.json); do
      if [[ "$(git -C "$R" show "$c:./render/d3d9/spec/d3d9_spec.json" | sha256sum | cut -d' ' -f1)" == "$sha" ]]; then git -C "$R" show "$c:./render/d3d9/spec/d3d9_spec.json" > "$f"; break; fi; done; fi
  [[ -f "$f" ]] || { echo "no d3d9_spec.json with sha $sha" >&2; exit 1; }; echo "$f"; }
mkdir -p "$O/d9s"; DIRS=()
for p in docked handheld; do for s in menu cell; do
  python3 -I "$C/d9s_shaders.py" --spec "$(spec_for "$W/rec/$p/$s.d9s")" "$O/d9s/$p-$s" "$W/rec/$p/$s.d9s" > "$O/d9s/$p-$s.log"; DIRS+=("$O/d9s/$p-$s"); done; done
"$B/shxlate" "$O/xl_fwd" "${DIRS[@]}" > "$O/xl_fwd.log" 2>&1 &
"$B/shxlate" --reverse "$O/xl_rev" "${DIRS[@]}" > "$O/xl_rev.log" 2>&1 &
wait
cmp_n=0; diff_n=0
for f in $(ls "$O/xl_fwd" | grep -v '^timing.tsv$'); do cmp_n=$((cmp_n+1)); cmp -s "$O/xl_fwd/$f" "$O/xl_rev/$f" || { diff_n=$((diff_n+1)); echo "DIFF $f"; }; done
echo "determinism: $((cmp_n-diff_n))/$cmp_n files byte-equal between forward and reversed runs" | tee "$O/determinism.txt"
BID="$(python3 -I -c "import importlib.util as u;s=u.spec_from_file_location('m','$C/mkshcache.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(m.build_id().hex())")"
python3 -I "$C/mkshcache.py" --build-id "$BID" "$O/xl_fwd" "$O/shaders.shc"; python3 -I "$C/mkshcache.py" --build-id "$BID" "$O/xl_rev" "$O/shaders_rev.shc"
cmp "$O/shaders.shc" "$O/shaders_rev.shc" && echo "cache file byte-identical from both runs" | tee -a "$O/determinism.txt"
for m in 64 32; do "$B/t_shcache$m" verify "$O/shaders.shc" "$BID" "$O/xl_fwd"; done | tee "$O/verify.txt"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless
"$B/glcheck" "$O/xl_fwd" "$O/glfail.tsv" | tee "$O/glcheck.txt" || true
"$B/glcheck" --diag-pad-fog "$O/xl_fwd" "$O/glfail_pad.tsv" | tee "$O/glcheck_pad.txt" || true
