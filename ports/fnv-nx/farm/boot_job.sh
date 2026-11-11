#!/usr/bin/env bash
# boot_job.sh - L4 farm job: run the boot stage (verify/boot.py) on one real guest ELF and publish the result.
#
#   [ARTIFACTS=...] [GAME_DIR=...] bash <checkout>/ports/fnv-nx/farm/boot_job.sh <elf_id> [--limit-instructions N] [--profile handheld|docked]
#                                [--gpu-branch <artifacts branch>]
#
# Steps:
#  1 setup: what the stage needs (python3, git, the aarch64 binutils readelf/nm that icount.py spec runs, unicorn==2.1.4)
#    is checked first; only what is missing is installed (apt-get, pip), and an install that fails fails the job
#  2 branch l1-contract/elf/<elf_id> of the artifacts repository: guest.elf, entries.tsv (exe_addr -> symbol), modules.tsv
#    (name lo hi), keys.tsv (one row: lo hi of the never-mapped key range), milestones.tsv (verify/bootmiles.py table),
#    optional softfloat.syms (one symbol per line). Every missing required file is listed and fails the job
#  3 icount.py spec: range lifted = section .text.fnvl, class softfloat from softfloat.syms (left out when the file is absent)
#  4 SD root: a fresh empty card directory (--root); the installed game is mounted in place at the guest's sdmc:/switch/fnv
#    (--game, verify/bootpath.py). The game is GAME_DIR, else fetched by farm/gamedata_fetch.sh into $FARM_HOME/game
#    (each item checked against its MANIFEST.tsv there). FalloutNV.exe must be in it; its sha256 is printed
#  5 verify/boot.py run, heap size 0x40000000. Exit 0 or 2 (the guest stopped with a fault, a deadlock, a missing
#    module or a nonzero exit) is a RESULT; 1 is an input error and fails the job
#  6 publish the orphan commit of OUT.json, OUT.console.txt, spec.json, meta.json to branch l4-verify/boot/<elf_id>
#  7 final line: BOOT elf=<id> rc=<boot rc> reason=<r> last=<milestone|none> instr=<n> main=<n> threads=<n> seconds=<s> published=<branch>@<commit12>
# --gpu-branch B: the renderer. Branch B of the artifacts repository must hold libnxgl4.so (the renderer's host library, GL 4.5 on
#    Mesa llvmpipe). The job fetches it, installs what is missing of GPU_PKGS (default "libegl1 libgl1-mesa-dri libgles2 mesa-utils") and
#    gcc (the callback shim of verify/bootgpu.py), checks that the library loads, and runs boot.py with --gpu LIB --frames DIR. The first 3
#    frames (frame_*.rgba + .txt) are published under frames/ with the result; later ones are not. meta.json gains gpu_branch and
#    gpu_branch_commit, the final line gains presents=<n>.
# Env: GPU_PKGS (see above), ARTIFACTS (clone of the artifacts repository, default $FARM_HOME/artifacts; cloned --filter=blob:none --no-checkout
# when absent), ARTIFACTS_URL (default https://github.com/toadharvard/fnv-nx-build), FARM_HOME (default $HOME/fnvnx-farm),
# WORK (default $FARM_HOME/boot/<elf_id>), GAME_DIR (an installed game; never inside a git checkout). The profile is
# recorded in meta.json and exported as FNVNX_PROFILE (a render host library reads it; boot.py does not take it).
# Exit 0 iff the result was published and boot.py exited 0 or 2; 1 otherwise (one-line reason on stderr).
set -euo pipefail
die() { echo "boot_job: FAILED: $*" >&2; exit 1; }
log() { echo "boot_job: $*" >&2; }

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
NX=$(cd "$SELF_DIR/.." && pwd)                     # ports/fnv-nx of the checkout this script lives in
ELF_ID=${1:?usage: boot_job.sh <elf_id> [--limit-instructions N] [--profile handheld|docked] [--gpu-branch B]}; shift
case $ELF_ID in ''|*[!A-Za-z0-9._-]*|.*) die "elf_id '$ELF_ID' must be letters, digits, '.', '_' or '-' and not start with '.'" ;; esac
LIMIT=""; PROFILE=handheld; GPU_BRANCH=
while [ $# -gt 0 ]; do
  case $1 in
    --limit-instructions) [ $# -ge 2 ] || die "--limit-instructions needs a value"; LIMIT=$2; shift 2 ;;
    --profile) [ $# -ge 2 ] || die "--profile needs a value"; PROFILE=$2; shift 2 ;;
    --gpu-branch) [ $# -ge 2 ] || die "--gpu-branch needs a value"; GPU_BRANCH=$2; shift 2 ;;
    *) die "unknown argument '$1'" ;;
  esac
done
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
APT=()
command -v python3 > /dev/null || APT+=(python3)
command -v git > /dev/null || APT+=(git)
{ command -v aarch64-linux-gnu-readelf > /dev/null && command -v aarch64-linux-gnu-nm > /dev/null; } || APT+=(binutils-aarch64-linux-gnu)
if [ -n "$GPU_BRANCH" ]; then
  command -v gcc > /dev/null || APT+=(gcc)
  for p in ${GPU_PKGS-libegl1 libgl1-mesa-dri libgles2 mesa-utils}; do dpkg -s "$p" > /dev/null 2>&1 || APT+=("$p"); done
fi
unicorn_ok() { python3 -s -c 'import importlib.metadata as m, sys; sys.exit(0 if m.version("unicorn") == "2.1.4" else 1)' 2> /dev/null; }
if [ ${#APT[@]} -gt 0 ] || ! unicorn_ok; then
  if [ "$(id -u)" = 0 ]; then SUDO=(); else command -v sudo > /dev/null || die "packages are missing (${APT[*]:-unicorn==2.1.4}) and this user is not root and has no sudo"; SUDO=(sudo -n); fi
  if [ ${#APT[@]} -gt 0 ]; then
    log "apt-get install ${APT[*]}"
    "${SUDO[@]}" apt-get update -q > /dev/null || die "apt-get update failed"
    "${SUDO[@]}" apt-get install -y -q "${APT[@]}" > /dev/null || die "apt-get install ${APT[*]} failed"
  fi
  if ! unicorn_ok; then
    python3 -m pip --version > /dev/null 2>&1 || { "${SUDO[@]}" apt-get install -y -q python3-pip > /dev/null || die "apt-get install python3-pip failed"; }
    log "pip install unicorn==2.1.4"
    "${SUDO[@]}" env PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install -q unicorn==2.1.4 > /dev/null || die "pip install unicorn==2.1.4 failed"
    unicorn_ok || die "unicorn==2.1.4 is still not importable after pip install"
  fi
fi

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
for f in guest.elf entries.tsv modules.tsv keys.tsv milestones.tsv; do grep -qxF "$f" "$WORK/branch-files.txt" || MISSING="$MISSING $f"; done
[ -z "$MISSING" ] || die "branch $IN_BRANCH@${ELF_COMMIT:0:12} lacks the required file(s):$MISSING"
IN=$WORK/in; SOFT=absent
want=(guest.elf entries.tsv modules.tsv keys.tsv milestones.tsv)
if grep -qxF softfloat.syms "$WORK/branch-files.txt"; then want+=(softfloat.syms); SOFT=present; fi
extract() { rm -rf "$IN"; mkdir -p "$IN"; GITA archive "$ELF_COMMIT" "${want[@]}" | tar -x -C "$IN"; }
retry extract || die "cannot extract ${want[*]} from $IN_BRANCH@${ELF_COMMIT:0:12}"
# keys.tsv: exactly one row, two hex fields lo hi -> LO:HI
KEYS=$(awk -F'[ \t]+' '!/^#/ && NF > 0 { n++; if (NF != 2) bad = 1; lo = $1; hi = $2 } END { if (n != 1 || bad) exit 1; print lo ":" hi }' "$IN/keys.tsv") \
  || die "$IN_BRANCH keys.tsv must hold exactly one row 'lo hi' (hex)"

# 2b. the renderer library
GPU_LIB=""; GPU_COMMIT=""
if [ -n "$GPU_BRANCH" ]; then
  retry GITA fetch -q "$ARTIFACTS_URL" "+refs/heads/$GPU_BRANCH:refs/remotes/boot-job/gpu/$GPU_BRANCH" || die "fetch of renderer branch $GPU_BRANCH failed"
  GPU_COMMIT=$(GITA rev-parse "refs/remotes/boot-job/gpu/$GPU_BRANCH^{commit}") || die "no commit for $GPU_BRANCH after fetch"
  GITA ls-tree --name-only "$GPU_COMMIT" | grep -qxF libnxgl4.so || die "renderer branch $GPU_BRANCH@${GPU_COMMIT:0:12} lacks libnxgl4.so"
  GPU_DIR=$WORK/gpu; rm -rf "$GPU_DIR"; mkdir -p "$GPU_DIR"
  gpu_extract() { GITA archive "$GPU_COMMIT" libnxgl4.so | tar -x -C "$GPU_DIR"; }
  retry gpu_extract || die "cannot extract libnxgl4.so from $GPU_BRANCH"
  GPU_LIB=$GPU_DIR/libnxgl4.so
  python3 -s -B "$NX/verify/bootgpu.py" "$GPU_LIB" > /dev/null 2> "$WORK/gpu.err" || die "libnxgl4.so does not load: $(tail -1 "$WORK/gpu.err" | cut -c1-300)"
  log "renderer library $GPU_BRANCH@${GPU_COMMIT:0:12} loads"
fi

# 3. icount spec
SPEC_ARGS=(--elf "$IN/guest.elf" --range lifted=section:.text.fnvl -o "$WORK/spec.json")
[ $SOFT = absent ] || SPEC_ARGS+=(--entries "softfloat=symfile:$IN/softfloat.syms")
python3 -I "$NX/verify/icount.py" spec "${SPEC_ARGS[@]}" 2> "$WORK/spec.err" || die "icount.py spec failed: $(tail -1 "$WORK/spec.err" | cut -c1-300)"
log "icount spec: lifted=section:.text.fnvl softfloat=$SOFT"

# 4. SD root and the installed game
if [ -z "${GAME_DIR:-}" ]; then
  GAME_DIR=$FARM_HOME/game
  ARTIFACTS_URL=$ARTIFACTS_URL bash "$NX/farm/gamedata_fetch.sh" "$GAME_DIR" > "$WORK/gamedata.log" 2>&1 || die "farm/gamedata_fetch.sh failed: $(tail -1 "$WORK/gamedata.log" | cut -c1-300)"
fi
[ -d "$GAME_DIR" ] || die "GAME_DIR $GAME_DIR is not a directory"
[ -s "$GAME_DIR/FalloutNV.exe" ] || die "$GAME_DIR/FalloutNV.exe is missing or empty"
EXE_SHA=$(sha256sum "$GAME_DIR/FalloutNV.exe" | cut -c1-64)
log "game $GAME_DIR: FalloutNV.exe sha256 $EXE_SHA"
SD=$WORK/sd; rm -rf "$SD"; mkdir -p "$SD"

# 5. boot
OUT=$WORK/OUT.json; rm -f "$OUT" "$WORK/OUT.console.txt"
BOOT=(python3 -s -B "$NX/verify/boot.py" run --elf "$IN/guest.elf" --reserved "$IN/modules.tsv" --keys "$KEYS" --heap-size 0x40000000
      --root "$SD" --game "$GAME_DIR" --milestones "$IN/milestones.tsv" --manifest "$IN/entries.tsv" --icount-spec "$WORK/spec.json")
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
python3 -I -c 'import json, sys
k = ["elf_id", "elf_branch_commit", "nx_agent_commit", "exe_sha256", "boot_rc", "seconds", "limit_instructions", "profile", "host_cpu_model", "softfloat"]
v = sys.argv[1:]
d = dict(zip(k, v))
d["boot_rc"], d["seconds"] = int(d["boot_rc"]), int(d["seconds"])
d["limit_instructions"] = int(d["limit_instructions"], 0) if d["limit_instructions"] else None
if v[len(k):]:
    d["gpu_branch"], d["gpu_branch_commit"] = v[len(k):]
print(json.dumps(d, sort_keys=True, indent=1))' "$ELF_ID" "$ELF_COMMIT" "$NX_COMMIT" "$EXE_SHA" "$RC" "$SECONDS_RUN" "$LIMIT" "$PROFILE" "$CPU" "$SOFT" ${GPU_BRANCH:+"$GPU_BRANCH" "$GPU_COMMIT"} > "$WORK/meta.json" \
  || die "meta.json could not be written"
IDX=$(mktemp -u "$WORK/index.XXXXXX")
for f in OUT.json OUT.console.txt spec.json meta.json; do
  GIT_INDEX_FILE=$IDX GITA update-index --add --cacheinfo "100644,$(GITA hash-object -w "$WORK/$f"),$f" || die "cannot stage $f"
done
if [ -n "$GPU_LIB" ]; then   # the first 3 frames only (lexical order = present order)
  for r in $(cd "$FRAMES" && ls frame_*.rgba 2> /dev/null | sort | head -3); do
    for f in "$r" "${r%.rgba}.txt"; do
      [ -f "$FRAMES/$f" ] || die "frame file $f is missing next to $r"
      GIT_INDEX_FILE=$IDX GITA update-index --add --cacheinfo "100644,$(GITA hash-object -w "$FRAMES/$f"),frames/$f" || die "cannot stage $f"
    done
  done
fi
TREE=$(GIT_INDEX_FILE=$IDX GITA write-tree) || die "write-tree failed"; rm -f "$IDX"
MSG=$(printf 'boot %s: rc %s reason %s last %s instr %s\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>' "$ELF_ID" "$RC" "$REASON" "$LAST" "$INSTR")
COMMIT=$(GITA "${GIT_ID[@]}" commit-tree "$TREE" -m "$MSG") || die "commit-tree failed"
retry GITA push -q -f "$ARTIFACTS_URL" "$COMMIT:refs/heads/$OUT_BRANCH" || die "push of $OUT_BRANCH failed"
[ "$(git ls-remote "$ARTIFACTS_URL" "refs/heads/$OUT_BRANCH" | cut -f1)" = "$COMMIT" ] || die "$OUT_BRANCH on the remote is not the pushed commit $COMMIT"

# 7. result
echo "BOOT elf=$ELF_ID rc=$RC reason=$REASON last=$LAST instr=$INSTR main=$MAIN threads=$THREADS seconds=$SECONDS_RUN published=$OUT_BRANCH@${COMMIT:0:12}${GPU_LIB:+ presents=$PRESENTS}"
