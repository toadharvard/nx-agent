#!/usr/bin/env bash
# V1-link harness workaround for BUG-1 (docs/VERIFY_V1_LINK.md): tools/link/layout.py flags() lacks the MSVC-semantics
# flags that compile.sh passes and that shim/include/fnvnx_compat.h enforces with #error since commit 4906b1b, so at
# HEAD every layout and vtable probe fails. Point CXX at this script to run `fnvlink.py probe` with compile.sh's flags.
exec aarch64-linux-gnu-g++ "$@" -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks \
    -ffp-contract=off -frounding-math
