#!/bin/bash
# V6n tests (D14 verifier of the native renderer). One command:
#   tests/v6n/run.sh              self-check of the harness, then every defect test (exit 1 while a defect is open)
#   tests/v6n/run.sh selfcheck    statediff (S6n translator vs Nine), state + pixel trap mutants, conformance scenes,
#                                 analytic claim checks: must all pass
#   tests/v6n/run.sh defects      every defect test (each FAILS while its defect is open)
#   tests/v6n/run.sh <defect>     one defect test (names: tests/v6n/DEFECTS.md)
#   tests/v6n/run.sh fuzz [N]     render-state fuzzer, N cases (default 300)
# Needs the host Nine build: NINE=<work dir of render/nine/build.sh ... host> (default /home/user/work/v6n/nine).
set -uo pipefail
T=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$T/../.." && pwd); V=$ROOT/render/verify; B=$ROOT/build/v6n/bin
export NINE=${NINE:-/home/user/work/v6n/nine}
py() { python3 -I "$@"; }
build() {
	[ -x "$B/v6n_gl" ] && [ -x "$B/v6n_nine" ] && [ -x "$B/v6n_s6ngl" ] && [ -x "$B/statediff" ] && [ -z "$(find "$V" "$ROOT/render/native/front" "$ROOT/render/native/gl" "$ROOT/render/native/shaders" -newer "$B/v6n_gl" \( -name '*.[ch]' -o -name '*.glsl' \) | head -1)" ] && return 0
	"$V/build.sh" "$B" > "$ROOT/build/v6n/build.log" 2>&1 || { echo "build failed: build/v6n/build.log"; exit 2; }
}
selfcheck() {
	local bad=0
	echo "== statediff: S6n d3d9_state.c vs Nine nine_pipe.c (2M cases)"; "$B/statediff" -n 2000000 -s 1 || bad=1
	echo "== state-level trap mutants"; py "$V/statediff/mutants.py" "$B" 300000 | tail -1 | grep -q " 23/23 killed" && echo "23/23 killed" || { bad=1; echo "FAIL"; }
	echo "== pixel trap mutants"; py "$V/pixdiff.py" --traps "$T/traps" | tail -1; py "$V/pixdiff.py" --traps "$T/traps" > /dev/null || bad=1
	echo "== conformance scenes (Nine vs target)"; py "$V/pixdiff.py" "$T/scenes" || bad=1
	echo "== coverage vs the exact D3D9 rule (raster_ref.py): V6n's GL executor exact, the oracle within 5 px"
	py "$V/rastercheck.py" v6n_gl || bad=1; py "$V/rastercheck.py" --allow 5 v6n_nine || bad=1
	echo "== analytic claim checks"; py "$V/claims.py" || bad=1
	if [ -x "${FNVNX_SHADERS_BUILD:-$ROOT/render/shaders/build/host}/s6b_asm" ]; then
		echo "== shader paths (S6b tools): VS+PS+indexed scenes, the double-half-pixel trap, PS parity self-test (P1 known)"
		py "$V/shaders/vsscenes.py" || bad=1
		py "$V/shaders/vsscenes.py" --mutant vs_posfix_halfpixel > /dev/null || { echo "vs_posfix_halfpixel survived"; bad=1; }
		py "$V/shaders/parity.py" --selftest | tail -1
	else echo "== shader paths skipped: build S6b's tools (render/shaders/build.sh --tests, FNVNX_SHADERS_BUILD)"; fi
	echo "== capture/replay mutation (C66): every corrupted capture must be flagged by S6n's replay_gl"
	if [ -n "${S6N_TESTS:-}" ] && [ -f "$S6N_TESTS/cap/n0.nxcap" ]; then
		n=$(py "$V/capture/nxcap.py" mutate "$S6N_TESTS/cap/n0.nxcap" --replay "$S6N_TESTS/replay_gl" | grep -c "rc=1")
		echo "$n/5 capture mutants flagged"; [ "$n" = 5 ] || bad=1
	else echo "skipped: make -C tests/s6n capture OUT=<dir>, then S6N_TESTS=<dir>"; fi
	for m in d24_bias_scale x8_dst_alpha; do
		py "$V/claims.py" --mutant $m > /dev/null && { echo "claim check missed mutant $m"; bad=1; }
	done
	echo "== S6n claim checks: C54 MAXMIPLEVEL / mip scenes vs Nine on both targets, C65 radix sort property test"
	py "$V/pixdiff.py" "$T/claims" | tail -1; py "$V/pixdiff.py" --target s6ngl "$T/claims" | tail -1
	py "$V/pixdiff.py" "$T/claims" > /dev/null || bad=1; py "$V/pixdiff.py" --target s6ngl "$T/claims" > /dev/null || bad=1
	if make -s -C "$ROOT/tests/s6n" all OUT="${S6N_TESTS:-$ROOT/build/v6n/s6n-tests}" > /dev/null 2>&1; then
		S=${S6N_TESTS:-$ROOT/build/v6n/s6n-tests}
		g++ -std=c++17 -O2 -I"$ROOT/render/native/include" -I"$ROOT/render/native/front" -o "$S/v6n_c65" "$T/claims/c65_sort.cpp" \
			"$S/front.o" "$S/gpu_dispatch.o" "$S/d3d9_state_s.o" -lm && "$S/v6n_c65" || bad=1
	fi
	echo "== repro corpus: tests/v6n/repro/*.scene must match (fixed model/harness gaps); repro/impl_defined/ is reported only"
	if ls "$T/repro"/*.scene > /dev/null 2>&1; then py "$V/pixdiff.py" "$T/repro" || bad=1; fi
	if ls "$T/repro/impl_defined"/*.scene > /dev/null 2>&1; then py "$V/pixdiff.py" "$T/repro/impl_defined" | tail -1; fi
	return $bad
}
defect() {   # name -> 0 when the defect is fixed (test passes)
	case "$1" in
	border_swizzle) py "$V/pixdiff.py" --target s6ngl "$T/defects/border_swizzle.scene" ;;
	y_fill_bias) local r=0; py "$V/pixdiff.py" --target s6ngl "$T/defects/y_fill_bias.scene" || r=1
		py "$V/rastercheck.py" v6n_s6ngl || r=1; return $r ;;
	screen_space_halfpixel)
		local S=${S6N_TESTS:-$ROOT/build/v6n/s6n-tests} N=$ROOT/render/native
		make -s -C "$ROOT/tests/s6n" n1 OUT="$S" > /dev/null || return 2
		g++ -std=c++17 -O1 -I"$N/include" -I"$N/front" -I"$N/n1" -I"$S/shaders" -I"$S" -o "$S/v6n_ssh" "$T/defects/screen_space_halfpixel.cpp" \
			"$N/n1/renderer.cpp" "$S/front.o" "$S/nxgl.o" "$S/gpu_dispatch.o" "$S/d3d9_state_s.o" "$S/nx_shaders.o" -lEGL -lm || return 2
		"$S/v6n_ssh" ;;
	camera_port_rounding)
		local S=${S6N_TESTS:-$ROOT/build/v6n/s6n-tests} N=$ROOT/render/native
		make -s -C "$ROOT/tests/s6n" n1 OUT="$S" > /dev/null || return 2
		g++ -std=c++17 -O1 -I"$N/include" -I"$N/front" -I"$N/n1" -I"$S/shaders" -I"$S" -o "$S/v6n_cpr" "$T/defects/camera_port_rounding.cpp" \
			"$N/n1/renderer.cpp" "$S/front.o" "$S/nxgl.o" "$S/gpu_dispatch.o" "$S/d3d9_state_s.o" "$S/nx_shaders.o" -lEGL -lm || return 2
		"$S/v6n_cpr" ;;
	*) echo "unknown defect $1"; return 2 ;;
	esac
}
DEFECTS=$(sed -n 's/^| `\([a-z0-9_]*\)` |.*/\1/p' "$T/DEFECTS.md")
build
case "${1:-all}" in
selfcheck) selfcheck ;;
defects) rc=0; for d in $DEFECTS; do echo "== defect $d"; defect "$d" || rc=1; done; exit $rc ;;
fuzz) py "$V/fuzz/fuzz.py" -n "${2:-300}" ;;
all) selfcheck; s=$?; rc=0; for d in $DEFECTS; do echo "== defect $d"; defect "$d" || rc=1; done
	echo "== summary: selfcheck $([ $s = 0 ] && echo PASS || echo FAIL), open defects: $([ $rc = 0 ] && echo none || echo yes)"
	exit $(( s | rc )) ;;
*) defect "$1" ;;
esac
