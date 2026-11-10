#!/bin/bash
# Idempotent rebuild of everything model_sail.py needs, from the LOCAL trees (nothing is downloaded except by the optional
# first block, which only runs when a tool is missing).  Each step is skipped when its output exists.  Run: bash proof/sail_build.sh
# Layout (override with env vars):
#   REMS   /home/user/rems-project            isla/ (isla + isla-sail) and sail-x86-from-acl2/ (model/*.sail, test-generation-patches/)
#   TOOLS  /home/user/l3tools                 opam binary + opam-root with the switch "sail" (Sail 0.20.3)
#   WORK   /home/user/l3work/proof            scratch: sail/x86_proof.ir (the IR used by model_sail.py), sail_cache/ (isla outputs)
# Commands the lead ran to create the toolchain (kept here as the record; they are NOT repeated when the outputs exist):
#   opam 2.2.1 binary -> $TOOLS/opam ; opam switch create sail 4.14.2 ; opam install sail
#   make -C isla/isla-sail
#   (z3 from the pip package z3-solver 5.4)  LIBRARY_PATH=$Z3/lib LD_LIBRARY_PATH=$Z3/lib C_INCLUDE_PATH=$Z3/include cargo build --release   (in isla/)
#   make x86.ir  (in sail-x86-from-acl2/model, isla-sail on PATH; stock IR, splices ../test-generation-patches/*.sail)
# What this script adds: the proof IR x86_proof.ir = the stock model + two extra splices that move instruction fetch into a register
# window (x86_code_regs.sail declares code_base/code_bytes and is compiled first; x86_fetch.sail replaces `rb`).  The stock x86.ir
# cannot be used because isla-execute-function has no memory initialisation: fetching from symbolic memory forks on every opcode.
set -euo pipefail
REMS=${REMS:-/home/user/rems-project}; TOOLS=${TOOLS:-/home/user/l3tools}; WORK=${WORK:-/home/user/l3work/proof}
HERE=$(cd "$(dirname "$0")" && pwd)
Z3=${Z3:-/usr/local/lib/python3.13/site-packages/z3}
MODEL=$REMS/sail-x86-from-acl2/model
export OPAMROOT=$TOOLS/opam-root
mkdir -p "$WORK/sail"

[ -x "$REMS/isla/isla-sail/isla-sail" ] || { eval "$($TOOLS/opam env --switch=sail)"; make -C "$REMS/isla/isla-sail"; }
[ -x "$REMS/isla/target/release/isla-execute-function" ] || (
  cd "$REMS/isla" && LIBRARY_PATH=$Z3/lib LD_LIBRARY_PATH=$Z3/lib C_INCLUDE_PATH=$Z3/include cargo build --release )
eval "$($TOOLS/opam env --switch=sail)"; export PATH=$REMS/isla/isla-sail:$PATH

# stock IR (reference for the Sail section of the README; not used by model_sail.py)
[ -f "$MODEL/x86.ir" ] || make -C "$MODEL" x86.ir

# proof IR: same file list as the model Makefile (PRELUDE_SAILS MODEL_SAILS MAIN_SAIL), plus the window registers first
OUT=$WORK/sail/x86_proof
FILES="prelude.sail register_types.sail registers.sail register_accessors.sail opcode_ext.sail memory_accessors.sail init.sail config.sail logging.sail step.sail main.sail"
newest=$(ls -t "$HERE/x86_fetch.sail" "$HERE/x86_code_regs.sail" $(for f in $FILES; do echo "$MODEL/$f"; done) "$MODEL"/../test-generation-patches/*.sail | head -1)
if [ ! -f "$OUT.ir" ] || [ "$newest" -nt "$OUT.ir" ]; then
  ( cd "$MODEL" && isla-sail $(for f in ../test-generation-patches/*.sail "$HERE/x86_fetch.sail"; do echo -splice "$f"; done) \
      -verbose 1 -o "$OUT" "$HERE/x86_code_regs.sail" $FILES ) > "$WORK/sail/build.log" 2>&1
  echo "built $OUT.ir ($(stat -c %s "$OUT.ir") bytes)"
else echo "$OUT.ir is up to date"; fi
sha256sum "$OUT.ir" "$HERE/x86.toml"
# smoke test: add eax, ebx  (01 d8) must give paths ending in (_ unit)
export LD_LIBRARY_PATH=$Z3/lib
python3 -c "
import sys; sys.path.insert(0, '$HERE')
import model_sail
out, err = model_sail.run_isla(bytes.fromhex('01d8'))
assert out.count('Result: (_ unit)') >= 1, out[:300]
print('smoke test ok:', out.count('Result:'), 'paths')"
