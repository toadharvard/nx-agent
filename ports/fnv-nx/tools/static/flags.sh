# flags.sh - sourced by tools/static/*.sh: the compiler flags of the real build (tools/link/compile.sh, D10 / J-0034)
# and the warning sets the static gate adds. run.sh checks that every flag of BUILD_FLAGS still appears in
# tools/link/compile.sh, so a flag change there shows up as a gate failure instead of silently diverging.
# shellcheck shell=bash
BUILD_FLAGS=(-mabi=ilp32 -std=gnu++17 -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing
             -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fpermissive)

# GCC warnings that point at code whose meaning differs between MSVC x86 and GCC AArch64 ILP32, or at UB that
# GCC -O2 exploits and MSVC 2008 did not (see docs/STATIC_CHECKS.md, one line per class there).
GCC_WARN=(-Wreturn-type -Wuninitialized -Wmaybe-uninitialized -Waggressive-loop-optimizations -Warray-bounds=1
          -Wsequence-point -Wshift-count-overflow -Wshift-count-negative -Wdiv-by-zero -Woverflow -Wnarrowing
          -Wreturn-local-addr -Wdangling-pointer=2 -Wstringop-overflow=2 -Wformat -Wformat-overflow=1 -Wattributes
          -Wignored-attributes -Wpacked-not-aligned -Wpsabi -Wfloat-conversion -Wtype-limits -Wcast-function-type
          -Wconditionally-supported -Wvarargs -Wfree-nonheap-object -Wmismatched-new-delete -Wclobbered
          -Wint-in-bool-context -Wbool-compare -Wtautological-compare)
GCC_DIAG=(-fno-diagnostics-show-caret -fdiagnostics-show-option -fno-diagnostics-color -fmax-errors=50)

# Flags the gate proposes to add to D10 (docs/STATIC_CHECKS.md, "Flags"); tests/s12 checks what each one changes.
PROPOSED_FLAGS=(-fwrapv-pointer -fno-finite-loops -fno-aggressive-loop-optimizations
                -fno-isolate-erroneous-paths-dereference -funreachable-traps)
