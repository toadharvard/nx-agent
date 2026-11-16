#!/bin/bash
# Sourced by the farm scripts that run a prover program (farm_shapeprove.sh, aggregate_certs.sh, sail_setup.sh): the one place that derives the Sail toolchain paths the programs take as explicit
# arguments (model_sail.configure: --sail-work --sail-ir --isla).  Needs HERE (proof dir) and GATE_CACHE; defines SAILBASE, WORKDIR, ISLA, and sail_pinned_ir (prints the path of the pinned IR, the external x86ir of
# lift/proof/sailmodel/build.json fetched by the trunk's loader).  sail_args IR_PATH sets TC=(--sail-work .. --sail-ir IR_PATH --isla ..).  Scripts, not the programs, read ISLA_EXEC / SAIL_IR / WORK.
SAILBASE="${GATE_CACHE:?GATE_CACHE (the verdict cache) is not set}/sail/$(sha256sum "$HERE/sail_pins.txt" | cut -c1-12)"      # the toolchain and its caches live in the verdict cache, keyed by the pins (proof/sail_pins.txt)
WORKDIR=${WORK:-$SAILBASE/work}
ISLA=${ISLA_EXEC:-$SAILBASE/rems-project/isla/target/release/isla-execute-function}
sail_pinned_ir() { python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from proof import formgate; print(formgate.ir_path(sys.argv[2]))' "$HERE/.." "$GATE_CACHE"; }
ISLACFG="$HERE/x86.toml"      # the file the isla root declares as input and publishes as ISLACFG
X86SYS_FILE="$HERE/../../platform/declared/x86sys.json"      # the declared machine of THIS tree: the one x86sys input of every proof program (--x86sys), its sha256 is in every record and queue header
sail_args() { TC=(--sail-work "$WORKDIR" --sail-ir "$1" --isla "$ISLA" --isla-cfg "$ISLACFG" --cpumodel "$HERE/../../verify/cpuid_model.json" --rcptab "$HERE/../rt/rcptab.c" --rcpcert "$HERE/../rt/rcptab.cert.json" --x86sys "$X86SYS_FILE"); }
