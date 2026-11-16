#!/bin/bash
# Idempotent rebuild of everything model_sail.py needs, from the LOCAL trees (nothing is downloaded except by the optional
# first block, which only runs when a tool is missing).  Each step is skipped when its output exists.  Run: bash proof/sail_build.sh
# Layout (override with env vars):
#   REMS   $GATE_CACHE/sail/<pins>/rems-project           isla/ (isla + isla-sail) and sail-x86-from-acl2/ (model/*.sail, test-generation-patches/)
#   TOOLS  $GATE_CACHE/sail/<pins>/tools                opam binary + opam-root with the switch "sail" (Sail 0.20.3)
#   WORK   $GATE_CACHE/sail/<pins>/work           scratch: sail/x86_proof.ir (the IR used by model_sail.py), sail_cache/ (isla outputs)
# The toolchain itself (opam switch "sail" with Sail 0.20.3, isla, z3) is created from clean by proof/sail_setup.sh, which pins every
# version and then calls this script.  Steps this script does when an output is missing (they are not repeated otherwise):
#   make -C isla/isla-sail ; cargo build --release --locked (in isla/, z3 from the pip package z3-solver) ; make x86.ir (stock IR)
# What this script adds: the proof IR x86_proof.ir = the stock model + two extra splices that move instruction fetch into a register
# window (x86_code_regs.sail declares code_base/code_bytes and is compiled first; x86_fetch.sail replaces `rb`).  The stock x86.ir
# cannot be used because isla-execute-function has no memory initialisation: fetching from symbolic memory forks on every opcode.
# Options: --stock builds the UNPATCHED model IR (x86_proof_stock.ir); default builds x86_proof.ir with the local fixes of sail_patches/.
STOCK=0; [ "${1:-}" = "--stock" ] && STOCK=1
set -euo pipefail
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd)
SAILBASE="${GATE_CACHE:?GATE_CACHE (the verdict cache) is not set}/sail/$(sha256sum "$HERE/sail_pins.txt" | cut -c1-12)"      # the toolchain and its caches live in the verdict cache, keyed by the pins (proof/sail_pins.txt)
REMS=${REMS:-$SAILBASE/rems-project}; TOOLS=${TOOLS:-$SAILBASE/tools}; WORK=${WORK:-$SAILBASE/work}
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
OUT=$WORK/sail/x86_proof; APPLY="--bump ${BUMP:-1}"
[ $STOCK = 1 ] && { OUT=${OUT}_stock; APPLY="--stock"; }
# The pinned IR (BUMP defaults to 1; BUMP=0 rebuilds the superseded IR 70c8dc39...): the stock model + transcriptions of ACL2 master hunks in sail_patches/bump/ (cmps, rol/ror count 0, ror CF, sar count >= size, cpuid, bsf/bsr, + their dispatch arms).
# The patch list and the generated dispatch splices are composed by sail_patches/apply.py, the same script the recipe root lift/proof/sailmodel runs.
FILES="prelude.sail register_types.sail registers.sail register_accessors.sail opcode_ext.sail memory_accessors.sail init.sail config.sail logging.sail step.sail main.sail"
newest=$(ls -t "$HERE"/sail_patches/*.sail "$HERE"/sail_patches/bump/* "$HERE/sail_patches/apply.py" "$HERE/sdmfeat.py" "$HERE/../fnvlift/cpumodel_bits.py" "$HERE/../../verify/cpuid_model.json" "$HERE/x86_fetch.sail" "$HERE/x86_code_regs.sail" $(for f in $FILES; do echo "$MODEL/$f"; done) "$MODEL"/../test-generation-patches/*.sail | head -1)
if [ ! -f "$OUT.ir" ] || [ "$newest" -nt "$OUT.ir" ]; then
  # Path independence: isla-sail records source file names in the IR, so it is run in a staging tree with only relative names
  # (model/ = copy of all model/*.sail because of its relative $include, tgp/ = test-generation-patches, local/ = splices of this
  # directory, sail/ = the Sail library, found through SAIL_DIR=../sail).  The IR then depends on file contents only, not on REMS / WORK / checkout paths.
  STAGE=$WORK/sail/stage; rm -rf "$STAGE"
  python3 -I "$HERE/sail_patches/apply.py" $APPLY --decl "$HERE/sdmfeat.py" "$HERE/../fnvlift/cpumodel_bits.py" "$HERE/../../verify/cpuid_model.json" "$MODEL/.." "$HERE/sail_patches" "$STAGE" "$HERE/x86_fetch.sail" "$HERE/x86_code_regs.sail"
  # the Sail library files (flow.sail, vector.sail, option.sail) are recorded with the path found through SAIL_DIR: use a staged copy
  SAILSHARE=$(dirname "$(dirname "$(command -v sail)")")/share/sail; mkdir "$STAGE/sail"; cp -r "$SAILSHARE/lib" "$STAGE/sail/lib"
  SPL=""; while read -r f; do SPL="$SPL -splice $f"; done < "$STAGE/SPLICES"
  ( cd "$STAGE/model" && SAIL_DIR=../sail isla-sail $SPL -verbose 1 -o ../x86_proof ../local/x86_code_regs.sail $FILES ) > "$WORK/sail/build.log" 2>&1
  mv "$STAGE/x86_proof.ir" "$OUT.ir"; rm -rf "$STAGE"
  echo "built $OUT.ir ($(stat -c %s "$OUT.ir") bytes)"
else echo "$OUT.ir is up to date"; fi
sha256sum "$OUT.ir" "$HERE/x86.toml"
# PUBLISH_IR=1: publish the IR once as the external tools/sail-ir-<sha 12 hex> of the artifacts repository (every consumer fetches it by sha: farm_shapeprove.sh)
if [ "${PUBLISH_IR:-0}" = 1 ]; then
    SHA=$(sha256sum "$OUT.ir" | cut -d' ' -f1); BR=tools/sail-ir-${SHA:0:12}; PB=${FNVNX_BUILD:?FNVNX_BUILD (clone of fnv-nx-build)}; T=$(mktemp -d)
    mkdir -p "$T/ir"; cp "$OUT.ir" "$T/ir/x86_proof.ir"
    python3 - "$T" "$SHA" "$(git -C "$HERE" rev-parse HEAD)" <<'PY'
import json, sys
t, sha, commit = sys.argv[1:]
# no tree_sha256 here: a tree cannot name its own hash.  The external's tree_sha256 is the loader's (verify/cas.py tree_hash of the checkout), printed below and written into the consumer's recipe pin.
json.dump({'format': 'fnvnx-tool-mirror/1', 'what': 'x86_proof.ir built by farm/sail_build.sh at nx-agent commit ' + commit, 'ir_sha256': sha, 'pin': 'ports/fnv-nx/lift/proof/sail_pins.txt'}, open(t + '/MANIFEST.json', 'w'), indent=1)
PY
    ( cd "$PB" && git checkout -q --orphan "$BR" && git rm -rqf . >/dev/null 2>&1; cp -r "$T/ir" "$T/MANIFEST.json" . && git add ir MANIFEST.json && git commit -qm "tools/sail-ir: x86_proof.ir $SHA" && git push -q origin "$BR" )
    echo "published $BR commit $(git -C "$PB" rev-parse HEAD) tree_sha256 $(python3 -c "import sys; sys.path.insert(0, '$FARM/../contract'); sys.path.insert(0, '$FARM/../verify'); import cas; print(cas.tree_hash(sys.argv[1], skip=('.git',)))" "$PB")"
fi
# smoke test: add eax, ebx  (01 d8) must give paths ending in (_ unit)
export LD_LIBRARY_PATH=$Z3/lib
python3 -c "
import sys; sys.path.insert(0, '$HERE/..')
from proof import model_sail
model_sail.configure('$WORK', '$OUT.ir', '$REMS/isla/target/release/isla-execute-function', '$HERE/x86.toml')      # explicit toolchain, no environment
out, err = model_sail.run_isla(bytes.fromhex('01d8'))
assert out.count('Result: (_ unit)') >= 1, out[:300]
print('smoke test ok:', out.count('Result:'), 'paths')"
