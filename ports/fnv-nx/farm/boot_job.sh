#!/usr/bin/env bash
# boot_job.sh - L4 farm job: run the boot stage (verify/boot.py) on one real guest ELF and publish the result.
#
#   [ARTIFACTS=...] [GAME_DIR=...] bash <checkout>/ports/fnv-nx/farm/boot_job.sh <elf_id> --quantum N [--limit-instructions N] [--profile handheld|docked]
#                                [--gpu-branch <artifacts branch>]
#
# Steps:
#  1 setup: nothing is installed. verify/envcheck.py check (the verdict farms' own check of verify/env.json) runs first;
#    a mismatch is an input error (rc 1, "set the host up with farm/setup_verdict_env.sh") before anything is fetched or published
#  2 branch l1-contract/elf/<elf_id> of the artifacts repository (the contract's own file formats; verify/bootinputs.py converts
#    them, and fails on any row or line it does not understand): guest.elf, or manifest.json + part.NNN (joined in manifest
#    order, every part's and the whole sha256 and size checked; a mismatch fails the job); entries.tsv (addr, symbol, kind
#    code|import, `# table_id` trailer); modules.tsv (name sha256 size preferred_base assigned_base size_of_image ...; reserved
#    ranges = assigned_base .. + size_of_image, plus the exe's own PE image range); keys.tsv (import keys; the key range is
#    derived from the import rows of entries.tsv, and both table ids must agree); milestones.tsv (the contract's rows: exe_entry, module_entry:*, WinMain, import:*!Present; the table always gains first-frame = the GL backend's first present host event) + the loader's
#    console lines of run.txt (when present) -> the bootmiles table; optional softfloat.syms or softfloat.tsv (one symbol
#    per line). Every missing required file is listed and fails the job
#  3 icount.py spec: range lifted = section .text.fnvl, class softfloat from the softfloat file (left out when absent)
#  4 SD root: a fresh empty card directory (--root); the installed game is mounted in place at the guest's sdmc:/switch/fnv
#    (--game, verify/bootpath.py). The game is GAME_DIR, else fetched by farm/gamedata_fetch.sh into $FARM_HOME/game
#    (the exe, INIs, static modules and d3dx9 DLLs; plus every Data item (gamedata/data/* of the artifacts repository: *.bsa, *.esm, Music,
#    Shaders; every part's and every file's sha256 checked by the fetcher), or only the Data items named in BOOT_DATA_ITEMS; `--no-data`
#    there means no Data item, a run without the game's archives). FalloutNV.exe must be in it; its sha256 is printed. The fetched
#    Data items are recorded in meta.json (data_items; null with GAME_DIR, whose content the job does not know)
#    The declared port input (manifest/fonts_mirror.json: the font mirror, fnv-nx-build tools/fonts-wine9 at a pinned commit and tree_sha256) is fetched
#    by boot.py --declared-inputs through the recipe's external mechanism (recipe.fetch_externals; commit and tree hash verified) and mounted read-only
#    at FNVNX_SD_FONTS (verify/bootdecl.py); a write the guest makes there is a fault (OUT.json declared_input_faults) and fails the run (exit 2,
#    verdict failed); any mismatch ends the job "input-error" before the boot; OUT.json inputs.declared and meta.json declared_inputs record
#    {name, commit, tree_sha256}
#  5 verify/boot.py run, heap size 0x40000000. Exit 0 or 2 (the guest stopped with a fault, a deadlock, a missing
#    module or a nonzero exit) is a RESULT; 1 is an input error and fails the job
#  6 publish the orphan commit of OUT.json, OUT.console.txt, spec.json, meta.json to branch l4-verify/boot/<elf_id>
#  7 final line: BOOT elf=<id> rc=<boot rc> reason=<r> last=<milestone|none> instr=<n> main=<n> threads=<n> seconds=<s> published=<branch>@<commit12>
# --gpu-branch B: the renderer. Branch B of the artifacts repository must hold libnxgl4.so (the renderer's host library, GL 4.5 on
#    Mesa llvmpipe). The job fetches it, requires the host to match the declared environment (verify/env.json, checked by verify/envcheck.py above, installed by farm/setup_verdict_env.sh, never by this job), checks that the library loads, and runs boot.py with --gpu LIB --frames DIR. EVERY
#    presented frame is published, losslessly, by content (no cap, no sampling): boot.py stores each distinct frame once as <key>.rgba + <key>.txt
#    in the frames directory (key = sha256 of the .txt bytes followed by the .rgba bytes; a repeated identical frame, such as a static menu, is the
#    same file), and the job publishes every file there under frames/ with the result. OUT.json "frames" has a record for every frame (n, t_ns,
#    per_tid, sha256 = its key), so n frames with d distinct images are n records and d file pairs. Size: one distinct frame is width x height x 4
#    bytes (1280x720 handheld = 3.7 MB, 1920x1080 docked = 8.3 MB) plus a few bytes of .txt; a publish over a store limit is reported to L4, not
#    trimmed. meta.json gains gpu_branch and
#    gpu_branch_commit, the final line gains presents=<n>.
#    The renderer pair rule (bootinputs.py pair), checked before the run; false = the job fails: the ELF branch's manifest.json names
#    the bridge it was linked with (render: {branch, archive_sha256}); that branch's build.json archive_sha256 and its libfnvnx-d3d9.a
#    bytes equal it; B's meta.json sha256 equals the libnxgl4.so bytes; bridge build.json nx_agent_commit == B's meta.json
#    nx_agent_commit (bridge head == nxgl4 head). meta.json gains bridge_branch, bridge_branch_commit and render_head.
# Stage table: env BOOT_STAGE_TABLE_BRANCH (default l4-verify/milestones), file stage.milestones (bootmiles format), appended after the
#    contract's rows (bootinputs.py milestones --stage-table: names unique, every exe address a lifted code entry). A missing branch: the
#    job runs with the contract rows only and the final line says stage_table=absent; a present branch without the file fails the job.
# The declared inputs (verify/env.json, manifest/fonts_mirror.json) are read only from the tree the job runs from; no variable redirects them.
# Env: ARTIFACTS (clone of the artifacts repository, default $FARM_HOME/artifacts; cloned --filter=blob:none --no-checkout
# when absent), ARTIFACTS_URL (default https://github.com/toadharvard/fnv-nx-build), FARM_HOME (default $HOME/fnvnx-farm),
# WORK (default $FARM_HOME/boot/<elf_id>), GAME_DIR (an installed game; never inside a git checkout). The profile is
# passed to boot.py, recorded in meta.json and exported as FNVNX_PROFILE (a render host library reads it).
# --quantum N (required, no default): instructions per slice of a core's thread in the N-core machine (bootmachine.py rule 3); passed to boot.py
# and recorded in meta.json (quantum).
# Exit 0 iff the result was published and boot.py exited 0 or 2; 1 otherwise (one-line reason on stderr).
set -euo pipefail
die() { echo "boot_job: FAILED: $*" >&2; exit 1; }
log() { echo "boot_job: $*" >&2; }

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
NX=$(cd "$SELF_DIR/.." && pwd)                     # ports/fnv-nx of the checkout this script lives in
ELF_ID=${1:?usage: boot_job.sh <elf_id> --quantum N [--limit-instructions N] [--profile handheld|docked] [--gpu-branch B]}; shift
case $ELF_ID in ''|*[!A-Za-z0-9._-]*|.*) die "elf_id '$ELF_ID' must be letters, digits, '.', '_' or '-' and not start with '.'" ;; esac
LIMIT=""; PROFILE=handheld; GPU_BRANCH=; QUANTUM=
while [ $# -gt 0 ]; do
  case $1 in
    --limit-instructions) [ $# -ge 2 ] || die "--limit-instructions needs a value"; LIMIT=$2; shift 2 ;;
    --quantum) [ $# -ge 2 ] || die "--quantum needs a value"; QUANTUM=$2; shift 2 ;;
    --profile) [ $# -ge 2 ] || die "--profile needs a value"; PROFILE=$2; shift 2 ;;
    --gpu-branch) [ $# -ge 2 ] || die "--gpu-branch needs a value"; GPU_BRANCH=$2; shift 2 ;;
    *) die "unknown argument '$1'" ;;
  esac
done
[ -n "$QUANTUM" ] || die "--quantum N is required (instructions per slice of a core's thread; a declared parameter of the run, no default)"
[[ $QUANTUM =~ ^([1-9][0-9]*|0[xX][0-9a-fA-F]*[1-9a-fA-F][0-9a-fA-F]*)$ ]] || die "--quantum '$QUANTUM' is not a positive integer (decimal or 0x hex)"
if [ -n "$LIMIT" ]; then
  [[ $LIMIT =~ ^([1-9][0-9]*|0[xX][0-9a-fA-F]*[1-9a-fA-F][0-9a-fA-F]*)$ ]] || die "--limit-instructions '$LIMIT' is not a positive integer (decimal or 0x hex)"
fi
case $PROFILE in handheld|docked) ;; *) die "--profile '$PROFILE' is neither handheld nor docked" ;; esac
export FNVNX_PROFILE=$PROFILE
case $GPU_BRANCH in *[!A-Za-z0-9._/-]*|-*) die "--gpu-branch '$GPU_BRANCH' is not a branch name" ;; esac

FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
WORK=${WORK:-$FARM_HOME/boot/$ELF_ID}
IN_BRANCH=l1-contract/elf/$ELF_ID; OUT_BRANCH=l4-verify/boot/$ELF_ID
GIT_ID=(-c user.name=fnv-nx-farm -c user.email=farm@invalid)
GITA() { git -C "$ARTIFACTS" "$@"; }
# network calls are retried (4 tries, 2/4/8 s); only calls that talk to the remote go through here
retry() { local i=1; while :; do "$@" && return 0; [ $i -ge 4 ] && return 1; log "retry $i of 3 in $((2 ** i)) s: $1 $2"; sleep $((2 ** i)); i=$((i + 1)); done; }
mkdir -p "$FARM_HOME" "$WORK"
NX_COMMIT=$(git -C "$SELF_DIR" rev-parse HEAD) || die "$SELF_DIR is not inside a git checkout (meta.json records its commit)"

# 1. setup
ENVF=$NX/verify/env.json   # the declared inputs are read only from the tree the job runs from: verify/env.json, manifest/fonts_mirror.json (no variable redirects them)
# GATE_CACHE (required input, no default): the verdict's own store (VDIR/cache). boot.py --gpu loads the recipe-built host runtime verify/bootrt from it
# (bootgpu.py); nothing is compiled here and no other cache exists.
[ -n "${GATE_CACHE:-}" ] || die "input-error: GATE_CACHE is not set (the verdict's cache; the host runtime verify/bootrt is built in it by recipe.py)"
export GATE_CACHE
python3 -I "$NX/verify/envcheck.py" check --env "$ENVF" > "$WORK/envcheck.txt" 2>&1 || { sed 's/^/boot_job: /' "$WORK/envcheck.txt" >&2; die "input-error: the host does not match the declared environment ($ENVF); set the host up with farm/setup_verdict_env.sh"; }

# 2. the guest branch
if [ ! -d "$ARTIFACTS/.git" ] && [ ! -f "$ARTIFACTS/HEAD" ]; then
  mkdir -p "$(dirname "$ARTIFACTS")"
  retry git clone -q --filter=blob:none --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS" || die "git clone of $ARTIFACTS_URL failed"
fi
LS=$WORK/ls-remote.txt
retry git -C "$ARTIFACTS" ls-remote "$ARTIFACTS_URL" "refs/heads/$IN_BRANCH" > "$LS" || die "ls-remote of $ARTIFACTS_URL failed"
[ -s "$LS" ] || die "$ARTIFACTS_URL has no branch $IN_BRANCH"
retry GITA fetch -q "$ARTIFACTS_URL" "+refs/heads/$IN_BRANCH:refs/remotes/boot-job/$IN_BRANCH" || die "fetch of $IN_BRANCH failed"
ELF_COMMIT=$(GITA rev-parse "refs/remotes/boot-job/$IN_BRANCH^{commit}") || die "no commit for $IN_BRANCH after fetch"
GITA ls-tree --name-only "$ELF_COMMIT" > "$WORK/branch-files.txt" || die "cannot list the tree of $IN_BRANCH@${ELF_COMMIT:0:12}"
MISSING=""
for f in entries.tsv modules.tsv keys.tsv milestones.tsv; do grep -qxF "$f" "$WORK/branch-files.txt" || MISSING="$MISSING $f"; done
want=(entries.tsv modules.tsv keys.tsv milestones.tsv)
if grep -qxF guest.elf "$WORK/branch-files.txt"; then want+=(guest.elf)
elif grep -qxF manifest.json "$WORK/branch-files.txt"; then want+=(manifest.json $(grep -E '^part\.[0-9]+$' "$WORK/branch-files.txt"))
else MISSING="$MISSING guest.elf-or-manifest.json"; fi
if [ -n "$GPU_BRANCH" ]; then   # the renderer pair rule reads manifest.json
  if grep -qxF manifest.json "$WORK/branch-files.txt"; then case " ${want[*]} " in *" manifest.json "*) ;; *) want+=(manifest.json) ;; esac
  else MISSING="$MISSING manifest.json"; fi
fi
[ -z "$MISSING" ] || die "branch $IN_BRANCH@${ELF_COMMIT:0:12} lacks the required file(s):$MISSING"
IN=$WORK/in; SOFT=absent; SOFTFILE=
for f in softfloat.syms softfloat.tsv; do
  if grep -qxF "$f" "$WORK/branch-files.txt"; then want+=("$f"); SOFT=present; SOFTFILE=$f; break; fi
done
HAVE_RUN=0; if grep -qxF run.txt "$WORK/branch-files.txt"; then want+=(run.txt); HAVE_RUN=1; fi
# an optional declared card input of the run, the file `mode` (4 bytes): it is placed at sdmc:/mode and its sha256 is recorded in meta.json (mode_input); the test guests read their mode from it
HAVE_MODE=0; if grep -qxF mode "$WORK/branch-files.txt"; then want+=(mode); HAVE_MODE=1; fi
extract() { rm -rf "$IN"; mkdir -p "$IN"; GITA archive "$ELF_COMMIT" "${want[@]}" | tar -x -C "$IN"; }
retry extract || die "cannot extract ${want[*]} from $IN_BRANCH@${ELF_COMMIT:0:12}"
BI=(python3 -s -B "$NX/verify/bootinputs.py")
if [ ! -f "$IN/guest.elf" ]; then
  "${BI[@]}" join --dir "$IN" -o "$IN/guest.elf" 2> "$WORK/join.err" || die "$(tail -1 "$WORK/join.err" | cut -c1-400)"
  log "guest.elf joined from the manifest's parts and verified"
fi

# 2b. the renderer library
GPU_LIB=""; GPU_COMMIT=""
if [ -n "$GPU_BRANCH" ]; then
  retry GITA fetch -q "$ARTIFACTS_URL" "+refs/heads/$GPU_BRANCH:refs/remotes/boot-job/gpu/$GPU_BRANCH" || die "fetch of renderer branch $GPU_BRANCH failed"
  GPU_COMMIT=$(GITA rev-parse "refs/remotes/boot-job/gpu/$GPU_BRANCH^{commit}") || die "no commit for $GPU_BRANCH after fetch"
  for f in libnxgl4.so meta.json; do
    GITA ls-tree --name-only "$GPU_COMMIT" | grep -qxF "$f" || die "renderer branch $GPU_BRANCH@${GPU_COMMIT:0:12} lacks $f"
  done
  GPU_DIR=$WORK/gpu; rm -rf "$GPU_DIR"; mkdir -p "$GPU_DIR"
  gpu_extract() { GITA archive "$GPU_COMMIT" libnxgl4.so meta.json | tar -x -C "$GPU_DIR"; }
  retry gpu_extract || die "cannot extract libnxgl4.so and meta.json from $GPU_BRANCH"
  # the renderer pair rule: the bridge linked into the ELF and this library are of one head
  RB=$("${BI[@]}" render-branch --manifest "$IN/manifest.json" 2> "$WORK/pair.err") || die "$(tail -1 "$WORK/pair.err" | cut -c1-400)"
  BRIDGE_BRANCH=${RB%% *}
  retry GITA fetch -q "$ARTIFACTS_URL" "+refs/heads/$BRIDGE_BRANCH:refs/remotes/boot-job/bridge/$BRIDGE_BRANCH" || die "fetch of bridge branch $BRIDGE_BRANCH failed"
  BRIDGE_COMMIT=$(GITA rev-parse "refs/remotes/boot-job/bridge/$BRIDGE_BRANCH^{commit}") || die "no commit for $BRIDGE_BRANCH after fetch"
  for f in build.json libfnvnx-d3d9.a; do
    GITA ls-tree --name-only "$BRIDGE_COMMIT" | grep -qxF "$f" || die "bridge branch $BRIDGE_BRANCH@${BRIDGE_COMMIT:0:12} lacks $f"
  done
  BRIDGE_DIR=$WORK/bridge; rm -rf "$BRIDGE_DIR"; mkdir -p "$BRIDGE_DIR"
  bridge_extract() { GITA archive "$BRIDGE_COMMIT" build.json libfnvnx-d3d9.a | tar -x -C "$BRIDGE_DIR"; }
  retry bridge_extract || die "cannot extract build.json and libfnvnx-d3d9.a from $BRIDGE_BRANCH"
  RENDER_HEAD=$("${BI[@]}" pair --manifest "$IN/manifest.json" --bridge "$BRIDGE_DIR" --gpu "$GPU_DIR" 2> "$WORK/pair.err") \
    || die "renderer pair: $(tail -1 "$WORK/pair.err" | cut -c1-500)"
  log "renderer pair of one head $RENDER_HEAD: $BRIDGE_BRANCH@${BRIDGE_COMMIT:0:12} + $GPU_BRANCH@${GPU_COMMIT:0:12}"
  GPU_LIB=$GPU_DIR/libnxgl4.so
  python3 -s -B "$NX/verify/bootgpu.py" "$GPU_LIB" > /dev/null 2> "$WORK/gpu.err" || die "libnxgl4.so does not load: $(tail -1 "$WORK/gpu.err" | cut -c1-300)"
  log "renderer library $GPU_BRANCH@${GPU_COMMIT:0:12} loads"
fi

# 3. icount spec
SPEC_ARGS=(--elf "$IN/guest.elf" --range lifted=section:.text.fnvl -o "$WORK/spec.json")
[ $SOFT = absent ] || SPEC_ARGS+=(--entries "softfloat=symfile:$IN/$SOFTFILE")
python3 -I "$NX/verify/icount.py" spec "${SPEC_ARGS[@]}" 2> "$WORK/spec.err" || die "icount.py spec failed: $(tail -1 "$WORK/spec.err" | cut -c1-300)"
log "icount spec: lifted=section:.text.fnvl softfloat=$SOFT"

# 4. SD root and the installed game
DATA_ITEMS=""
if [ -z "${GAME_DIR:-}" ]; then
  GAME_DIR=$FARM_HOME/game
  ARTIFACTS_URL=$ARTIFACTS_URL bash "$NX/farm/gamedata_fetch.sh" "$GAME_DIR" ${BOOT_DATA_ITEMS:-} > "$WORK/gamedata.log" 2>&1 || die "farm/gamedata_fetch.sh failed: $(tail -1 "$WORK/gamedata.log" | cut -c1-300)"
  DATA_ITEMS=$(ls "$GAME_DIR/.gamedata_items" 2> /dev/null | sort | tr '\n' ' '); DATA_ITEMS="fetched:$DATA_ITEMS"
fi
[ -d "$GAME_DIR" ] || die "GAME_DIR $GAME_DIR is not a directory"
[ -s "$GAME_DIR/FalloutNV.exe" ] || die "$GAME_DIR/FalloutNV.exe is missing or empty"
EXE_SHA=$(sha256sum "$GAME_DIR/FalloutNV.exe" | cut -c1-64)
log "game $GAME_DIR: FalloutNV.exe sha256 $EXE_SHA"
SD=$WORK/sd; chmod -R u+w "$SD" 2> /dev/null || true; rm -rf "$SD"; mkdir -p "$SD"
MODE_SHA=""; if [ $HAVE_MODE = 1 ]; then cp "$IN/mode" "$SD/mode"; MODE_SHA=$(sha256sum "$IN/mode" | cut -c1-64); fi
export MODE_SHA
# the declared port input (manifest/fonts_mirror.json: the font mirror, fnv-nx-build tools/fonts-wine9 at a pinned commit and tree_sha256): boot.py
# --declared-inputs fetches it through the recipe's external mechanism (verify/recipe.py fetch_externals, the cache $GATE_CACHE), checks the tree hash and
# mounts it read-only at FNVNX_SD_FONTS. Any mismatch ends the job as input-error before the boot (boot.py exit 1). The pin file is read only from the
# tree the job runs from; it also goes into meta.json (declared_inputs: name, commit, tree_sha256).
PINS=$NX/manifest/fonts_mirror.json
[ -s "$PINS" ] || die "input-error: $PINS is missing"
# the contract's files in the forms boot.py takes (each converter fails on any row it does not understand)
conv() { "$@" 2> "$WORK/conv.err" || die "$(tail -1 "$WORK/conv.err" | cut -c1-400)"; }
conv "${BI[@]}" reserved --modules "$IN/modules.tsv" --exe "$GAME_DIR/FalloutNV.exe" -o "$IN/reserved.tsv"
KEYS=$("${BI[@]}" keys --entries "$IN/entries.tsv" --keys "$IN/keys.tsv" 2> "$WORK/conv.err") || die "$(tail -1 "$WORK/conv.err" | cut -c1-400)"
RUN_ARGS=(); [ $HAVE_RUN = 0 ] || RUN_ARGS=(--run "$IN/run.txt")
# the stage table (milestones after the exe entry: WinMain .. first frame): branch $BOOT_STAGE_TABLE_BRANCH, file stage.milestones,
# appended after the contract's rows; an absent branch means contract rows only (reported as stage_table=absent)
STAGE_BRANCH=${BOOT_STAGE_TABLE_BRANCH:-l4-verify/milestones}
case $STAGE_BRANCH in *[!A-Za-z0-9._/-]*|-*) die "BOOT_STAGE_TABLE_BRANCH '$STAGE_BRANCH' is not a branch name" ;; esac
STAGE_ARGS=(); STAGE=absent
retry GITA ls-remote "$ARTIFACTS_URL" "refs/heads/$STAGE_BRANCH" > "$WORK/ls-stage.txt" || die "ls-remote of $ARTIFACTS_URL failed"
if [ -s "$WORK/ls-stage.txt" ]; then
  retry GITA fetch -q "$ARTIFACTS_URL" "+refs/heads/$STAGE_BRANCH:refs/remotes/boot-job/stage/$STAGE_BRANCH" || die "fetch of $STAGE_BRANCH failed"
  STAGE_COMMIT=$(GITA rev-parse "refs/remotes/boot-job/stage/$STAGE_BRANCH^{commit}") || die "no commit for $STAGE_BRANCH after fetch"
  GITA ls-tree --name-only "$STAGE_COMMIT" | grep -qxF stage.milestones || die "branch $STAGE_BRANCH@${STAGE_COMMIT:0:12} lacks stage.milestones"
  stage_extract() { GITA show "$STAGE_COMMIT:stage.milestones" > "$IN/stage.milestones"; }
  retry stage_extract || die "cannot extract stage.milestones from $STAGE_BRANCH"
  STAGE_ARGS=(--stage-table "$IN/stage.milestones"); STAGE=${STAGE_BRANCH}@${STAGE_COMMIT:0:12}
  log "stage table $STAGE"
else
  log "no branch $STAGE_BRANCH: contract milestones only"
fi
conv "${BI[@]}" milestones --table "$IN/milestones.tsv" --entries "$IN/entries.tsv" "${RUN_ARGS[@]}" "${STAGE_ARGS[@]}" -o "$IN/table.txt"

# 5. boot
OUT=$WORK/OUT.json; rm -f "$OUT" "$WORK/OUT.console.txt"
BOOT=(python3 -s -B "$NX/verify/boot.py" run --elf "$IN/guest.elf" --reserved "$IN/reserved.tsv" --keys "$KEYS" --heap-size 0x40000000
      --profile "$PROFILE" --root "$SD" --game "$GAME_DIR" --declared-inputs "$PINS" --milestones "$IN/table.txt" --manifest "$IN/entries.tsv" --icount-spec "$WORK/spec.json")
BOOT+=(--quantum "$QUANTUM")
[ -z "$LIMIT" ] || BOOT+=(--limit-instructions "$LIMIT")
FRAMES=$WORK/frames; rm -rf "$FRAMES"
[ -z "$GPU_LIB" ] || BOOT+=(--gpu "$GPU_LIB" --frames "$FRAMES")
BOOT+=(--out "$OUT")
log "${BOOT[*]}"
T0=$(date +%s)
"${BOOT[@]}" > "$WORK/boot.out" 2> "$WORK/boot.err" && RC=0 || RC=$?
SECONDS_RUN=$(( $(date +%s) - T0 ))
case $RC in 0|2) ;; *) die "boot.py exited $RC: $(tail -1 "$WORK/boot.err" | cut -c1-300)" ;; esac
SUMMARY=$(grep -E '^boot: reason=' "$WORK/boot.out" | tail -1) || die "boot.py printed no summary line (exit $RC)"
field() { sed -n "s/.* $1=\([^ ]*\).*/\1/p" <<< "$SUMMARY" | head -1; }
REASON=$(field reason); LAST=$(field last); INSTR=$(field instr); MAIN=$(field main); THREADS=$(field threads)
for v in REASON LAST INSTR MAIN THREADS; do [ -n "${!v}" ] || die "summary line has no $v field: $SUMMARY"; done
[ -f "$OUT" ] && [ -f "$WORK/OUT.console.txt" ] || die "boot.py exit $RC but OUT.json or OUT.console.txt is missing"

PRESENTS=""
if [ -n "$GPU_LIB" ]; then
  PRESENTS=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["gpu"]["presents"])' "$OUT") || die "OUT.json has no gpu record"
fi

# 6. publish
CPU=$(lscpu 2> /dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)
[ -n "$CPU" ] || CPU=$(sed -n 's/^model name[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo | head -1)
[ -n "$CPU" ] || die "the host cpu model is in neither lscpu nor /proc/cpuinfo"
PIN_FILE="$PINS" python3 -I -c 'import json, os, sys
k = ["elf_id", "elf_branch_commit", "nx_agent_commit", "exe_sha256", "boot_rc", "seconds", "limit_instructions", "quantum", "profile", "host_cpu_model", "softfloat", "data_items"]
v = sys.argv[1:]
d = dict(zip(k, v))
d["boot_rc"], d["seconds"] = int(d["boot_rc"]), int(d["seconds"])
d["limit_instructions"] = int(d["limit_instructions"], 0) if d["limit_instructions"] else None
d["quantum"] = int(d["quantum"], 0)
d["mode_input"] = os.environ.get("MODE_SHA") or None
d["data_items"] = d["data_items"][len("fetched:"):].split() if d["data_items"] else None
pin = json.load(open(os.environ["PIN_FILE"]))
d["declared_inputs"] = [{k: pin[k] for k in ("name", "commit", "tree_sha256")}]
if v[len(k):]:
    d["gpu_branch"], d["gpu_branch_commit"], d["bridge_branch"], d["bridge_branch_commit"], d["render_head"] = v[len(k):]
print(json.dumps(d, sort_keys=True, indent=1))' "$ELF_ID" "$ELF_COMMIT" "$NX_COMMIT" "$EXE_SHA" "$RC" "$SECONDS_RUN" "$LIMIT" "$QUANTUM" "$PROFILE" "$CPU" "$SOFT" "$DATA_ITEMS" ${GPU_BRANCH:+"$GPU_BRANCH" "$GPU_COMMIT" "$BRIDGE_BRANCH" "$BRIDGE_COMMIT" "$RENDER_HEAD"} > "$WORK/meta.json" \
  || die "meta.json could not be written"
IDX=$(mktemp -u "$WORK/index.XXXXXX")
for f in OUT.json OUT.console.txt spec.json meta.json; do
  GIT_INDEX_FILE=$IDX GITA update-index --add --cacheinfo "100644,$(GITA hash-object -w "$WORK/$f"),$f" || die "cannot stage $f"
done
if [ -n "$GPU_LIB" ]; then   # EVERY distinct frame the renderer dumped (<key>.rgba + <key>.txt, boot.py store_frame), staged in one batch
  (cd "$FRAMES" && ls *.rgba 2> /dev/null | sort) > "$WORK/frames.lst" || true
  while read -r r; do [ -f "$FRAMES/${r%.rgba}.txt" ] || die "frame file ${r%.rgba}.txt is missing next to $r"; done < "$WORK/frames.lst"
  if [ -s "$WORK/frames.lst" ]; then
    : > "$WORK/frames.paths"; : > "$WORK/frames.names"
    while read -r r; do
      printf '%s\n%s\n' "$FRAMES/$r" "$FRAMES/${r%.rgba}.txt" >> "$WORK/frames.paths"
      printf '%s\n%s\n' "$r" "${r%.rgba}.txt" >> "$WORK/frames.names"
    done < "$WORK/frames.lst"
    GITA hash-object -w --stdin-paths < "$WORK/frames.paths" > "$WORK/frames.sha" || die "cannot store the frame files"
    paste -d '\t' "$WORK/frames.sha" "$WORK/frames.names" | awk -F '\t' '{printf "100644 %s\tframes/%s\n", $1, $2}' \
      | GIT_INDEX_FILE=$IDX GITA update-index --add --index-info || die "cannot stage the frame files"
  fi
fi
TREE=$(GIT_INDEX_FILE=$IDX GITA write-tree) || die "write-tree failed"; rm -f "$IDX"
MSG=$(printf 'boot %s: rc %s reason %s last %s instr %s' "$ELF_ID" "$RC" "$REASON" "$LAST" "$INSTR")
COMMIT=$(GITA "${GIT_ID[@]}" commit-tree "$TREE" -m "$MSG") || die "commit-tree failed"
retry GITA push -q -f "$ARTIFACTS_URL" "$COMMIT:refs/heads/$OUT_BRANCH" || die "push of $OUT_BRANCH failed"
[ "$(git ls-remote "$ARTIFACTS_URL" "refs/heads/$OUT_BRANCH" | cut -f1)" = "$COMMIT" ] || die "$OUT_BRANCH on the remote is not the pushed commit $COMMIT"

# 7. result
echo "BOOT elf=$ELF_ID rc=$RC reason=$REASON last=$LAST instr=$INSTR main=$MAIN threads=$THREADS seconds=$SECONDS_RUN published=$OUT_BRANCH@${COMMIT:0:12}${GPU_LIB:+ presents=$PRESENTS} stage_table=$STAGE"
