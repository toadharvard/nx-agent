#!/usr/bin/env bash
# farm_boot.sh - emulated boot of a farm link. Fetches branch link/<commit12> of the private artifacts repository
# (S9's link_job: out/ and fixed/ of tools/link resolve, probe, resolve, apply; the full job adds guest/fnvnx.elf,
# its own boots under boot/ and the data image boot/synth.exe), takes the farm's guest (packed to an NSO here) or
# links one (toolchain/game/build.sh, D3D9=null), boots it under tests/s5/emu_guest.py on a game image, and
# summarises. tools/farm/full_job.sh runs it on the farm for the stub boot (FARM_GUEST_DIR, FARM_BOOTS=stub).
#
#   tests/s5/farm_boot.sh <commit> <out dir> [image]
#
# image: the FalloutNV.exe the guest maps (put at switch/fnv/, NX1's SD layout, and at fnvnx/game/, where guests
# linked before NX1 look for it). Default: $FNVNX_IMAGE, else the link's boot/synth.exe (S14's synthesized
# image as the farm made it), else none (the empty image). Boots ($FARM_BOOTS, default "raw slots stub"), all with
# --skip-faulting-init: raw; slots, raw plus the exe's vftable slots in the native vtables' trap slots
# (--exe-vtables, the stand-in for S2c's link fix; needs the image); and stub with the known-fault table
# (--stub-faults tests/s5/known_faults.txt, docs/TOOLCHAIN.md section 18). slots and stub also map
# toolchain/game/heap_standin.c, compiled here (--inject; aarch64-linux-gnu-gcc), so a guest linked before a weak
# stand-in there was added boots as a new link would; a guest that has the function keeps its own.
#
# Output in <out dir>. It must be outside the repository, because everything there is derived from the decomp:
#   link/              the fetched link outputs (not with FARM_LINK_DIR)
#   guest/             guest.nso packed from link/guest/fnvnx.elf, or build.sh's output when the link has no guest or
#                      FARM_RELINK=1 (not with FARM_GUEST_DIR)
#   emu_<boot>.log     one per boot, emu_<boot>.rc its exit code
#   frontier_<boot>*   tests/s5/frontier.py on that boot, when a units.json is at hand (FARM_UNITS, or
#                      tree/build/units.json of t/<commit12>) and the link has out/addrmap.tsv
#   summary.txt        the undefined count, the image and its sha256, and per boot how far it got and where it stopped
# Exit: 0 when the boots ran (their stops are in summary.txt), 3 when there is no link/<commit12> yet, 1 when the
# link outputs are incomplete or the guest does not build.
# Env:
#   ARTIFACTS       a clone of toadharvard/fnv-nx-build (default /home/user/fnv-nx-build if present, else
#                   $FARM_HOME/artifacts, cloned on first use)
#   FARM_LINK_DIR   a local link (a dir with out/ and fixed/, e.g. the pipeline's build/link) instead of the branch
#   FARM_GUEST_DIR  a dir with guest.nso and fnvnx.elf (e.g. the pipeline's build/guest): boot that guest as it is
#   FARM_BOOTS      the boots to run, from "raw slots stub" (default all three)
#   FARM_UNITS      a units.json instead of the one from t/<commit12>
#   FARM_RELINK     1: link the guest here even when the link has one (about 1.5 minutes)
#   FARM_PIN        1: pack the farm's guest pinned to its link base (elf2nso --pin; default relocatable, as the
#                   farm boots it)
#   PY              the Python with unicorn (default python3)
#   VORBIS          the libvorbis prefix for build.sh; JOBS, SSE2NEON, DEVKITPRO passed to build.sh
#   FARM_NATIVE_RENDER  1: build those inputs here (cached in $FARM_HOME/native_render-<key>; needs root for the
#                   cross compiler on first use), or a dir with tests/s5/native_render_inject.sh's outputs (nxrender_inject.o, libnxgl.so): every
#                   boot runs on the native renderer's engine path (tests/s5/native_render.py --native-render --gpu,
#                   the stand-in for a LINK_OVERRIDES=render link), its frames as PNG in <out dir>/frames_<boot>/
#   FARM_GAME       a game install (FalloutNV.exe, Data/, Fallout_default.ini), mounted in place at the guest's
#                   sdmc:/switch/fnv (emu_guest.py --game, tests/s5/datapath.py); its FalloutNV.exe is the default image.
#                   Without it the image alone is copied to sdmc:/switch/fnv. Never inside the repository.
#   FNVNX_STUB_NULLDEV  1: the stub boot passes --stub-nulldev (CreateDevice succeeds headless without a GPU backend;
#                   toolchain/game/native_device.cpp); a measurement mode for stub boots, never the product
#   EMU_EXTRA       extra emu_guest.py arguments for every boot (word-split; e.g. --trace-fn ... --trace-out ...)
#   EMU_PY          the emulator script (default tests/s5/emu_guest.py; S18: tools/scenario/emu_scenario.py, a wrapper
#                   that takes the same arguments plus its own --scenario ...)
#   FARM_PROFILE    a profile dir (Documents/..., AppData/...; tools/lockstep/profile.py) copied to the guest's
#                   sdmc:/switch/fnv/user before each boot, so the first-run profile is not made (lockstep: same inputs)
#   FARM_FAULTS     the stub boot's fault table (default tests/s5/known_faults.txt)
#   EMU_TIMEOUT     seconds per boot (default 1200); SLICE_MS wall-clock ms per thread slice (default 2000);
#                   BUDGET instructions per slice (default 0: none - an instruction count makes unicorn hook every
#                   instruction, about 10x slower; before: default 200000000, as
#                   S5's local boots: the timeout's SIGTERM waits for the running unicorn slice, and with 6e9 a boot
#                   ran 22 min past its timeout)
# Runtime: under a minute per boot; the local guest link (f13) about 1.5 minutes. Inside D17's ten minutes.
set -euo pipefail
COMMIT=${1:?commit}; OUT=${2:?out dir}; IMAGE=${3:-${FNVNX_IMAGE:-}}
GAME=${FARM_GAME:-}
if [ -n "$GAME" ]; then
  GAME=$(cd "$GAME" && pwd)
  [ -n "$IMAGE" ] || IMAGE=$GAME/FalloutNV.exe
fi
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PY:-python3}
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
if [ -z "${ARTIFACTS:-}" ]; then
  if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
C12=${COMMIT:0:12}
BOOTS=${FARM_BOOTS:-raw slots stub}
EMU_TIMEOUT=${EMU_TIMEOUT:-1200}; BUDGET=${BUDGET:-0}; SLICE_MS=${SLICE_MS:-2000}
NULLDEV_ARGS=(); [ "${FNVNX_STUB_NULLDEV:-0}" = 1 ] && NULLDEV_ARGS=(--stub-nulldev)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# The real install by default (orchestrator: every farm boots on the real Data/): when no FARM_GAME is given and the
# image is none or the owner's exe, tools/gamedata_fetch.sh assembles it from the private repository into
# FARM_GAMEDATA_DIR (default $FARM_HOME/gamedata; cached: nothing is fetched while the branches stay put).
# FARM_GAMEDATA=0 turns it off; a failed fetch leaves the boot as it was (image only).
OWNER_EXE_SHA=${FNVNX_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}
if [ -z "$GAME" ] && [ "${FARM_GAMEDATA:-1}" != 0 ] &&
   { [ -z "$IMAGE" ] || [ "$(sha256sum "$IMAGE" | cut -c1-64)" = "$OWNER_EXE_SHA" ]; }; then
  GD=${FARM_GAMEDATA_DIR:-${FARM_HOME:-$HOME/fnvnx-farm}/gamedata}
  if bash "$ROOT/tools/gamedata_fetch.sh" "$GD" > "$OUT/gamedata_fetch.log" 2>&1; then
    GAME=$GD; IMAGE=$GD/FalloutNV.exe
  else
    echo "farm_boot: no real install (tools/gamedata_fetch.sh failed, see $OUT/gamedata_fetch.log): image only" >&2
  fi
fi
if TOP=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null); then
  case "$OUT/" in "$TOP"/*) echo "farm_boot: $OUT is inside the repository; the outputs are decomp-derived" >&2; exit 1 ;; esac
fi
SUM=$OUT/summary.txt
log() { echo "== $(date +%T) $*"; }

fetch_branch() {   # fetch_branch <branch> <dest dir>: extract the branch's files; false when it does not exist
  [ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
  local ref="refs/remotes/origin/$1"
  git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$1:$ref" 2>/dev/null || return 1
  rm -rf "$2.tmp"; mkdir -p "$2.tmp"
  git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$2.tmp"
  rm -rf "$2"; mv "$2.tmp" "$2"
}

# --- link outputs ---------------------------------------------------------------------------------------------------
if [ -n "${FARM_LINK_DIR:-}" ]; then
  LINK=$(cd "$FARM_LINK_DIR" && pwd)
  log "link: local $LINK"
else
  LINK=$OUT/link
  log "link: branch link/$C12"
  if ! fetch_branch "link/$C12" "$LINK"; then
    printf 'farm boot %s\n- status: not run\n- no branch link/%s in the artifacts repository yet (S9 link_job)\n' \
      "$C12" "$C12" > "$SUM"
    echo "farm_boot: no link/$C12 yet (S9's link_job publishes it)" >&2
    exit 3
  fi
fi
if [ -z "$IMAGE" ] && [ -f "$LINK/boot/synth.exe" ]; then IMAGE=$LINK/boot/synth.exe; fi
[ -z "$IMAGE" ] || IMAGE=$(cd "$(dirname "$IMAGE")" && pwd)/$(basename "$IMAGE")

# --- guest ----------------------------------------------------------------------------------------------------------
UNDEF="?"
if [ -n "${FARM_GUEST_DIR:-}" ]; then
  GDIR=$(cd "$FARM_GUEST_DIR" && pwd)
  log "guest: local $GDIR"
  [ -f "$GDIR/guest.nso" ] && [ -f "$GDIR/fnvnx.elf" ] || { echo "farm_boot: $GDIR lacks guest.nso or fnvnx.elf" >&2; exit 1; }
  [ -f "$GDIR/undefined.txt" ] && UNDEF=$(wc -l < "$GDIR/undefined.txt")
elif [ -f "$LINK/guest/fnvnx.elf" ] && [ "${FARM_RELINK:-0}" != 1 ]; then
  log "guest: the farm's (link/guest/fnvnx.elf)"
  GDIR=$OUT/guest; mkdir -p "$GDIR"
  cp "$LINK/guest/fnvnx.elf" "$GDIR/fnvnx.elf"
  "$PY" -I "$ROOT/toolchain/tools/elf2nso.py" ${FARM_PIN:+--pin} "$GDIR/fnvnx.elf" "$GDIR/guest.nso" \
    > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log" >&2; exit 1; }
  UNDEF="$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("guest_undefined", "?"))' \
    "$LINK/meta.json" 2>/dev/null || echo "?") (farm meta.json)"
else
  for f in out/aliases.ld out/addrmap.tsv fixed/zz_bridges.o fixed/zz_addrmap.o; do
    [ -e "$LINK/$f" ] || { echo "farm_boot: link outputs lack $f" >&2; exit 1; }
  done
  log "guest link (build.sh, D3D9=null)"
  GDIR=$OUT/guest
  if ! D3D9=null nice "$ROOT/toolchain/game/build.sh" "$LINK/out" "$LINK/fixed" "$GDIR" ${VORBIS:+"$VORBIS"} \
      > "$OUT/build.log" 2>&1; then
    tail -20 "$OUT/build.log" >&2
    echo "farm_boot: the guest does not build (build.log)" >&2
    exit 1
  fi
  UNDEF=$(wc -l < "$GDIR/undefined.txt")
fi

# --- units.json for the frontier ------------------------------------------------------------------------------------
UNITS=${FARM_UNITS:-}
if [ -z "$UNITS" ] && git -C "$ARTIFACTS" fetch -q origin "+refs/heads/t/$C12:refs/remotes/origin/t/$C12" 2>/dev/null \
    && git -C "$ARTIFACTS" archive "refs/remotes/origin/t/$C12" tree/build/units.json 2>/dev/null \
       | tar -xO > "$OUT/units.json" 2>/dev/null && [ -s "$OUT/units.json" ]; then   # only units.json, not the tree
  UNITS=$OUT/units.json
fi

# --- boots ----------------------------------------------------------------------------------------------------------
ADDRMAP=""
[ -f "$LINK/out/addrmap.tsv" ] && ADDRMAP=$LINK/out/addrmap.tsv
IMGARG=()
if [ -n "$GAME" ]; then
  IMGARG=(--game "$GAME")
  [ "$IMAGE" -ef "$GAME/FalloutNV.exe" ] || { echo "farm_boot: FARM_GAME has its own FalloutNV.exe; give no image" >&2; exit 1; }
elif [ -n "$IMAGE" ]; then
  IMGARG=(--file "switch/fnv/FalloutNV.exe=$IMAGE")
fi
INJECT=()
if command -v aarch64-linux-gnu-gcc > /dev/null \
    && aarch64-linux-gnu-gcc -mabi=ilp32 -O2 -fno-asynchronous-unwind-tables -c "$ROOT/toolchain/game/heap_standin.c" \
       -o "$OUT/heap_standin.o" 2> "$OUT/heap_standin.log"; then
  INJECT=(--inject "$OUT/heap_standin.o")
else
  log "heap_standin.c not compiled (aarch64-linux-gnu-gcc): slots and stub boot without it"
fi
# The exe's ini files next to it (fnv-nx-build gamedata/exe: Fallout_default.ini, low/medium/high/VeryHigh.ini; the
# game copies Fallout_default.ini to My Games/FalloutNV/FALLOUT.INI and stops in WinMain without it). They go where
# the exe goes. Nothing is placed for the synthesized image.
GAMEFILES=()
if [ -n "$IMAGE" ] && [ -z "$GAME" ]; then   # with FARM_GAME the install's own inis are mounted with it
  for f in "$(dirname "$IMAGE")"/*.ini; do
    [ -f "$f" ] || continue
    b=$(basename "$f")
    GAMEFILES+=(--file "switch/fnv/$b=$f" --file "fnvnx/game/$b=$f")
  done
fi
EMU=("${EMU_PY:-$HERE/emu_guest.py}"); NR=${FARM_NATIVE_RENDER:-}
if [ "$NR" = 1 ]; then
  # build the native renderer's inputs here (tests/s5/native_render_inject.sh: the ILP32 object and the headless GL
  # backend libnxgl.so, EGL surfaceless on Mesa), cached by the hash of their sources; under a minute when it builds
  local_key=$( { cat "$HERE/native_render_inject.sh" "$HERE/menu_standins.c" "$ROOT/toolchain/game/native_device.cpp" \
    "$ROOT/toolchain/game/null_d3d9.cpp" "$ROOT/tests/s6n/Makefile"; find "$ROOT/render/native" -type f \( -name '*.c' -o \
    -name '*.cpp' -o -name '*.h' -o -name '*.glsl' -o -name '*.py' -o -name '*.sh' \) | sort | xargs cat; } 2>/dev/null | sha256sum | cut -c1-16)
  NR=$FARM_HOME/native_render-$local_key
  if [ ! -f "$NR/nxrender_inject.o" ] || [ ! -f "$NR/libnxgl.so" ]; then
    command -v aarch64-linux-gnu-g++ >/dev/null || { [ "$(id -u)" = 0 ] && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      g++-aarch64-linux-gnu binutils-aarch64-linux-gnu libegl-dev >/dev/null 2>&1; }
    [ -d "${SSE2NEON:-$ROOT/ext/sse2neon}" ] || bash "$ROOT/tools/fetch.sh" >/dev/null 2>&1 || true
    log "native renderer inputs -> $NR"
    bash "$HERE/native_render_inject.sh" "$NR" > "$OUT/native_render_inject.log" 2>&1 \
      || { log "FARM_NATIVE_RENDER=1: native_render_inject.sh failed (log $OUT/native_render_inject.log)"; exit 1; }
  fi
fi
[ -n "$NR" ] && EMU=("$HERE/native_render.py" --native-render "$NR/nxrender_inject.o" --gpu "$NR/libnxgl.so")
boot() {   # boot <tag> [extra emu_guest.py args]
  local tag=$1; shift
  log "boot $tag"
  local rc=0 root=$OUT/sdmc-$tag
  local NR_ARGS=()
  [ -n "$NR" ] && NR_ARGS=(--frames "$OUT/frames_$tag")
  rm -rf "$root"; mkdir -p "$root"
  if [ -n "${FARM_PROFILE:-}" ]; then   # the player's profile (tools/lockstep/profile.py): the guest's user dir
    mkdir -p "$root/switch/fnv/user" && cp -a "$FARM_PROFILE"/. "$root/switch/fnv/user/"
  fi
  timeout "$EMU_TIMEOUT" nice "$PY" -I "${EMU[@]}" "$GDIR/guest.nso" --elf "$GDIR/fnvnx.elf" "${NR_ARGS[@]}" \
    --skip-faulting-init --root "$root" --budget "$BUDGET" --slice-ms "$SLICE_MS" "${IMGARG[@]}" "${GAMEFILES[@]}" \
    ${ADDRMAP:+--addrmap "$ADDRMAP"} --summary-json "$OUT/boot_summary_$tag.json" "$@" ${EMU_EXTRA:-} \
    > "$OUT/emu_$tag.log" 2>&1 || rc=$?
  rm -rf "$root"
  echo "$rc" > "$OUT/emu_$tag.rc"
  if [ -n "$UNITS" ] && [ -f "$LINK/out/addrmap.tsv" ]; then
    local maparg=()
    [ -f "$GDIR/fnvnx.map" ] && maparg=(--map "$GDIR/fnvnx.map")
    "$PY" -I "$HERE/frontier.py" "$OUT/emu_$tag.log" --addrmap "$LINK/out/addrmap.tsv" --units "$UNITS" \
      "${maparg[@]}" --out "$OUT/frontier_$tag" > "$OUT/frontier_$tag.txt" 2>&1 || true
  fi
}
for tag in $BOOTS; do
  case "$tag" in
    raw) boot raw ;;
    slots) boot slots --exe-vtables "${INJECT[@]}" ;;
    stub) boot stub --stub-faults "${FARM_FAULTS:-$HERE/known_faults.txt}" "${INJECT[@]}" \
            "${NULLDEV_ARGS[@]}" ;;
    *) echo "farm_boot: unknown boot '$tag' (FARM_BOOTS: raw slots stub)" >&2; exit 1 ;;
  esac
done

# --- summary --------------------------------------------------------------------------------------------------------
{
  echo "farm boot $C12 (link: ${FARM_LINK_DIR:-link/$C12})"
  if [ -n "$IMAGE" ]; then
    echo "- image: $IMAGE, sha256 $(sha256sum "$IMAGE" | cut -c1-64)"
  else
    echo "- image: none (the empty image)"
  fi
  if [ -n "$GAME" ]; then
    echo "- game install: $GAME$( [ -f "$GAME/SYNTHETIC" ] && echo ' (synthetic install, tests/s5/synth_install.py: not the game data)')"
  fi
  echo "- undefined symbols: $UNDEF"
  [ -f "$LINK/boot/summary.txt" ] && echo "- the farm's raw boot: $(grep -h '^STOPPED' "$LINK/boot/summary.txt" | head -1)"
  for tag in $BOOTS; do
    L=$OUT/emu_$tag.log
    echo
    echo "## boot $tag (exit $(cat "$OUT/emu_$tag.rc"))"
    grep -h "exe-vtables:\|inject:\|memory manager after construction\|memory manager before WinMain\|static initialisers done\|initializers skipped\|calling WinMain\|WinMain returned\|stub-faults:\|before-winmain:" "$L" || true
    grep -h "^stubbed-fault\|^replaced-call\|^vslot-past-end" "$L" | cut -f1-5 | head -25 || true
    grep -h "^skip-summary" "$L" | cut -f1-4 | head -8 || true
    sed -n '/^== trace/,/^   #4 /p' "$L" | head -14
    grep -h "^STOPPED\|^deadlock" "$L" || echo "(no stop line: timeout or normal end, see emu_$tag.log)"
    grep -h "^   \[fnvnx\] \(MessageBox\|ExitProcess\)" "$L" | sort -u | head -5 || true
    grep -h "^milestone	" "$L" | cut -f2-4 | tr '\t' ' ' | sed 's/^/game milestone /' || true
    grep -h "^data-counts	" "$L" | tr '\t' ' ' || true
    grep -h "^file-fault	" "$L" | head -10 | tr '\t' ' ' || true
    grep -h "^native-render	\|^gpu	\|^gpu-frame	\|native-render: \|fnvnx native renderer: " "$L" | tr '\t' ' ' | head -20 || true
    if [ -f "$OUT/frontier_$tag.txt" ]; then echo; echo "### frontier ($tag boot)"; head -12 "$OUT/frontier_$tag.txt"; fi
  done
} > "$SUM"
log "summary: $SUM"
cat "$SUM"
