#!/bin/bash
# Idempotent rebuild of everything model_sail.py needs, from the LOCAL trees (nothing is downloaded except by the optional
# first block, which only runs when a tool is missing).  Each step is skipped when its output exists.  Run: bash proof/sail_build.sh
# Layout (override with env vars):
#   REMS   /home/user/rems-project            isla/ (isla + isla-sail) and sail-x86-from-acl2/ (model/*.sail, test-generation-patches/)
#   TOOLS  /home/user/l3tools                 opam binary + opam-root with the switch "sail" (Sail 0.20.3)
#   WORK   /home/user/l3work/proof            scratch: sail/x86_proof.ir (the IR used by model_sail.py), sail_cache/ (isla outputs)
# The toolchain itself (opam switch "sail" with Sail 0.20.3, isla, z3) is created from clean by proof/sail_setup.sh, which pins every
# version and then calls this script.  Steps this script does when an output is missing (they are not repeated otherwise):
#   make -C isla/isla-sail ; cargo build --release --locked (in isla/, z3 from the pip package z3-solver) ; make x86.ir (stock IR)
# What this script adds: the proof IR x86_proof.ir = the stock model + two extra splices that move instruction fetch into a register
# window (x86_code_regs.sail declares code_base/code_bytes and is compiled first; x86_fetch.sail replaces `rb`).  The stock x86.ir
# cannot be used because isla-execute-function has no memory initialisation: fetching from symbolic memory forks on every opcode.
# Options: --stock builds the UNPATCHED model IR (x86_proof_stock.ir); default builds x86_proof.ir with the local fixes of sail_patches/.
STOCK=0; [ "${1:-}" = "--stock" ] && STOCK=1
set -euo pipefail
REMS=${REMS:-/home/user/rems-project}; TOOLS=${TOOLS:-/home/user/l3tools}; WORK=${WORK:-/home/user/l3work/proof}
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd)
Z3=${Z3:-$(python3 -c 'import z3,os;print(os.path.dirname(z3.__file__))')}; CARGO=${CARGO:-cargo}
MODEL=$REMS/sail-x86-from-acl2/model
export OPAMROOT=$TOOLS/opam-root
mkdir -p "$WORK/sail"

[ -x "$REMS/isla/isla-sail/plugin.cmxs" ] || { eval "$($TOOLS/opam env --switch=sail)"; make -C "$REMS/isla/isla-sail"; }
[ -x "$REMS/isla/target/release/isla-execute-function" ] || (
  cd "$REMS/isla" && LIBRARY_PATH=$Z3/lib LD_LIBRARY_PATH=$Z3/lib C_INCLUDE_PATH=$Z3/include $CARGO build --release --locked )
eval "$($TOOLS/opam env --switch=sail)"; export PATH=$REMS/isla/isla-sail:$PATH

# stock IR (reference for the Sail section of the README; not used by model_sail.py)
[ -f "$MODEL/x86.ir" ] || make -C "$MODEL" x86.ir

# proof IR: same file list as the model Makefile (PRELUDE_SAILS MODEL_SAILS MAIN_SAIL), plus the window registers first
OUT=$WORK/sail/x86_proof; PATCHES="$HERE/sail_patches/rotate_count.sail $HERE/sail_patches/ror_cf.sail $HERE/sail_patches/bt_bitnumber.sail $HERE/sail_patches/segment_bases.sail"
[ $STOCK = 1 ] && { OUT=${OUT}_stock; PATCHES=""; }
FILES="prelude.sail register_types.sail registers.sail register_accessors.sail opcode_ext.sail memory_accessors.sail init.sail config.sail logging.sail step.sail main.sail"
newest=$(ls -t $PATCHES "$HERE/x86_fetch.sail" "$HERE/x86_code_regs.sail" $(for f in $FILES; do echo "$MODEL/$f"; done) "$MODEL"/../test-generation-patches/*.sail | head -1)
if [ ! -f "$OUT.ir" ] || [ "$newest" -nt "$OUT.ir" ]; then
  # Path independence: isla-sail records source file names in the IR, so it is run in a staging tree with only relative names
  # (model/ = copy of all model/*.sail because of its relative $include, tgp/ = test-generation-patches, local/ = splices of this
  # directory, sail/ = the Sail library, found through SAIL_DIR=../sail).  The IR then depends on file contents only, not on REMS / WORK / checkout paths.
  STAGE=$WORK/sail/stage; rm -rf "$STAGE"; mkdir -p "$STAGE/tgp" "$STAGE/local"
  mkdir "$STAGE/model"; cp "$MODEL"/*.sail "$STAGE/model/"; cp "$MODEL"/../test-generation-patches/*.sail "$STAGE/tgp/"
  cp $PATCHES "$HERE/x86_fetch.sail" "$HERE/x86_code_regs.sail" "$STAGE/local/"
  # the Sail library files (flow.sail, vector.sail, option.sail) are recorded with the path found through SAIL_DIR: use a staged copy
  SAILSHARE=$(dirname "$(dirname "$(command -v sail)")")/share/sail; mkdir "$STAGE/sail"; cp -r "$SAILSHARE/lib" "$STAGE/sail/lib"
  SPL=""; for f in "$STAGE"/tgp/*.sail; do SPL="$SPL -splice ../tgp/$(basename "$f")"; done
  for f in $PATCHES "$HERE/x86_fetch.sail"; do SPL="$SPL -splice ../local/$(basename "$f")"; done
  ( cd "$STAGE/model" && SAIL_DIR=../sail isla-sail $SPL -verbose 1 -o ../x86_proof ../local/x86_code_regs.sail $FILES ) > "$WORK/sail/build.log" 2>&1
  mv "$STAGE/x86_proof.ir" "$OUT.ir"; rm -rf "$STAGE"
  echo "built $OUT.ir ($(stat -c %s "$OUT.ir") bytes)"
else echo "$OUT.ir is up to date"; fi
sha256sum "$OUT.ir" "$HERE/x86.toml"
# smoke test: add eax, ebx  (01 d8) must give paths ending in (_ unit)
export LD_LIBRARY_PATH=$Z3/lib SAIL_IR=$OUT.ir SAIL_WORK=$WORK Z3LIB=$Z3/lib ISLA_EXEC=$REMS/isla/target/release/isla-execute-function
python3 -c "
import sys; sys.path.insert(0, '$HERE/..')
from proof import model_sail
out, err = model_sail.run_isla(bytes.fromhex('01d8'))
assert out.count('Result: (_ unit)') >= 1, out[:300]
print('smoke test ok:', out.count('Result:'), 'paths')"
