# flags.sh - sourced by tools/static/*.sh: the compiler flags of the real build (tools/link/compile.sh: D10 semantics
# flags of J-0034, -fPIC of D16) and the warning sets the static gate adds. run.sh checks that BUILD_FLAGS is exactly
# the flag set of tools/link/compile.sh (and that tools/census.sh has every one of them but -fPIC), so a flag change
# there shows up as a gate failure instead of silently diverging.
# shellcheck shell=bash
BUILD_FLAGS=(-mabi=ilp32 -std=gnu++17 -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing
             -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fPIC -fpermissive -Werror=return-type
             -Wno-narrowing -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection
             -mno-outline-atomics)
# flags of BUILD_FLAGS that a -fsyntax-only run (tools/census.sh) does not need (code generation only)
SYNTAX_ONLY_EXEMPT=(-fPIC -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection
                    -mno-outline-atomics)

# script_flags <build script>: the compiler flags of the script's FLAGS=(...) array, without -c / -fsyntax-only, $OPT,
# -o, -fmax-errors=N, the -include / -I paths, -ffile-prefix-map (it only rewrites recorded paths, so objects do
# not depend on where the tree lives: tools/farm/) and -fplugin (a path; gcc_warn.sh loads the same plugin) - one
# per line
script_flags() {
  sed -n '/^FLAGS=(/,/)$/p' "$1" | tr -s ' \t' '\n\n' | sed -e 's/^FLAGS=(//' -e 's/)$//' |
    grep -E '^-' | grep -vE '^(-c|-fsyntax-only|-o|-include|-I.*|-fmax-errors=.*|-ffile-prefix-map=.*|-fplugin=.*|-fplugin-arg-.*)$' || true
}

# GCC warnings that point at code whose meaning differs between MSVC x86 and GCC AArch64 ILP32, or at UB that
# GCC -O2 exploits and MSVC 2008 did not (see docs/STATIC_CHECKS.md, one line per class there). Missing returns are
# errors of the build itself (-Werror=return-type); narrowing stays off as in the build (-Wno-narrowing).
GCC_WARN=(-Wuninitialized -Wmaybe-uninitialized -Waggressive-loop-optimizations -Warray-bounds=1
          -Wsequence-point -Wshift-count-overflow -Wshift-count-negative -Wdiv-by-zero -Woverflow
          -Wreturn-local-addr -Wdangling-pointer=2 -Wstringop-overflow=2 -Wformat -Wformat-overflow=1 -Wattributes
          -Wignored-attributes -Wpacked-not-aligned -Wpsabi -Wfloat-conversion -Wtype-limits -Wcast-function-type
          -Wconditionally-supported -Wvarargs -Wfree-nonheap-object -Wmismatched-new-delete -Wclobbered
          -Wint-in-bool-context -Wbool-compare -Wtautological-compare)
GCC_DIAG=(-fno-diagnostics-show-caret -fdiagnostics-show-option -fno-diagnostics-color -fmax-errors=50)

# Flags the gate proposes to add to D10 (docs/STATIC_CHECKS.md, "Flags"); tests/s12 checks what each one changes.
# (-funreachable-traps is no longer proposed: FALL1 traps at the end of non-void functions and the build's
# -Werror=return-type rejects any other missing return; the shim's __assume is a no-op.)
# (-fno-isolate-erroneous-paths-dereference neither: the same GCC pass turns a division by a provable zero into a
# trap, which is what x86 does there (#DE); without it the AArch64 udiv returns 0 and the game runs on - S12 J-0103+,
# UploadQuads 0x00e723a0.)
PROPOSED_FLAGS=(-fwrapv-pointer -fno-finite-loops -fno-aggressive-loop-optimizations)
