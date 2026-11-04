#!/usr/bin/env bash
# farm_boot.sh - emulated boot of a farm link. Fetches branch link/<commit12> of the private artifacts repository
# (S9's link_job: out/ and fixed/ of tools/link resolve, probe, resolve, apply; the full job adds guest/fnvnx.elf
# and its own boot/), takes the farm's guest (packed to an NSO here) or links one (toolchain/game/build.sh,
# D3D9=null), boots it under tests/s5/emu_guest.py on a game image, and summarises.
#
#   DEVKITPRO=... tests/s5/farm_boot.sh <commit> <out dir> [image]
#
# image: the FalloutNV.exe the guest maps; the default is $FNVNX_IMAGE (S14's synthesized image for now, see
# docs/TOOLCHAIN.md section 17); without one the guest boots on the empty image. Two boots, both with
# --skip-faulting-init: raw, and with the known-fault table (--stub-faults tests/s5/known_faults.txt, section 18).
#
# Output in <out dir>. It must be outside the repository, because everything there is derived from the decomp:
#   link/          the fetched link outputs (out/, fixed/, and guest/, boot/ when the farm built and booted it)
#   guest/         guest.nso packed from link/guest/fnvnx.elf, or build.sh's output (fnvnx.elf, fnvnx.map,
#                  guest.nso, undefined.txt) when the link has no guest or FARM_RELINK=1
#   emu_raw.log, emu_stub.log
#   frontier_raw*  tests/s5/frontier.py on the raw boot, when units.json of the prepared tree t/<commit12> exists
#   summary.txt    the undefined count and, per boot, how far it got and where it stopped
# Exit: 0 when both boots ran (their stops are in summary.txt), 3 when there is no link/<commit12> yet, 1 when the
# link outputs are incomplete or the guest does not build.
# Env:
#   ARTIFACTS       a clone of toadharvard/fnv-nx-build (default /home/user/fnv-nx-build if present, else
#                   $FARM_HOME/artifacts, cloned on first use)
#   FARM_LINK_DIR   a local link (a dir with out/ and fixed/) instead of the branch
#   FARM_UNITS      a units.json instead of the one from t/<commit12>
#   FARM_RELINK     1: link the guest here even when the link has one (about 1.5 minutes)
#   FARM_PIN        1: pack the farm's guest pinned to its link base (elf2nso --pin; default relocatable, as the
#                   farm boots it)
#   VORBIS          the libvorbis prefix for build.sh
#   JOBS, SSE2NEON  passed to build.sh
#   EMU_TIMEOUT     seconds per boot (default 1200)
#   BUDGET          instructions per thread slice (default 6000000000)
# Runtime: under a minute per boot; the local guest link (f13) about 1.5 minutes. Inside D17's ten minutes.
set -euo pipefail
COMMIT=${1:?commit}; OUT=${2:?out dir}; IMAGE=${3:-${FNVNX_IMAGE:-}}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
if [ -z "${ARTIFACTS:-}" ]; then
  if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
C12=${COMMIT:0:12}
EMU_TIMEOUT=${EMU_TIMEOUT:-1200}; BUDGET=${BUDGET:-6000000000}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
TOP=$(git -C "$ROOT" rev-parse --show-toplevel)
case "$OUT/" in "$TOP"/*) echo "farm_boot: $OUT is inside the repository; the outputs are decomp-derived" >&2; exit 1 ;; esac
[ -z "$IMAGE" ] || IMAGE=$(cd "$(dirname "$IMAGE")" && pwd)/$(basename "$IMAGE")
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
# --- guest ----------------------------------------------------------------------------------------------------------
mkdir -p "$OUT/guest"
if [ -f "$LINK/guest/fnvnx.elf" ] && [ "${FARM_RELINK:-0}" != 1 ]; then
  log "guest: the farm's (link/guest/fnvnx.elf)"
  GUEST_ELF=$LINK/guest/fnvnx.elf
  python3 -I "$ROOT/toolchain/tools/elf2nso.py" ${FARM_PIN:+--pin} "$GUEST_ELF" "$OUT/guest/guest.nso" \
    > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log" >&2; exit 1; }
  UNDEF="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("guest_undefined", "?"))' \
    "$LINK/meta.json" 2>/dev/null || echo "?") (farm meta.json)"
  MAP=$LINK/guest/fnvnx.map
else
  for f in out/aliases.ld out/addrmap.tsv fixed/zz_bridges.o fixed/zz_addrmap.o; do
    [ -e "$LINK/$f" ] || { echo "farm_boot: link outputs lack $f" >&2; exit 1; }
  done
  log "guest link (build.sh, D3D9=null)"
  if ! D3D9=null nice "$ROOT/toolchain/game/build.sh" "$LINK/out" "$LINK/fixed" "$OUT/guest" ${VORBIS:+"$VORBIS"} \
      > "$OUT/build.log" 2>&1; then
    tail -20 "$OUT/build.log" >&2
    echo "farm_boot: the guest does not build (build.log)" >&2
    exit 1
  fi
  GUEST_ELF=$OUT/guest/fnvnx.elf
  UNDEF=$(wc -l < "$OUT/guest/undefined.txt")
  MAP=$OUT/guest/fnvnx.map
fi

# --- boots ----------------------------------------------------------------------------------------------------------
boot() {   # boot <tag> [extra emu_guest.py args]
  local tag=$1; shift
  log "boot $tag"
  local rc=0
  timeout "$EMU_TIMEOUT" nice python3 -I "$HERE/emu_guest.py" "$OUT/guest/guest.nso" --elf "$GUEST_ELF" \
    --skip-faulting-init --budget "$BUDGET" ${IMAGE:+--file "fnvnx/game/FalloutNV.exe=$IMAGE"} "$@" \
    > "$OUT/emu_$tag.log" 2>&1 || rc=$?
  echo "$rc" > "$OUT/emu_$tag.rc"
}
boot raw
boot stub --stub-faults "$HERE/known_faults.txt"

# --- frontier (needs units.json of the prepared tree) ----------------------------------------------------------------
UNITS=${FARM_UNITS:-}
if [ -z "$UNITS" ] && git -C "$ARTIFACTS" fetch -q origin "+refs/heads/t/$C12:refs/remotes/origin/t/$C12" 2>/dev/null \
    && git -C "$ARTIFACTS" archive "refs/remotes/origin/t/$C12" tree/build/units.json | tar -xO > "$OUT/units.json" \
    2>/dev/null; then   # only units.json, not the whole prepared tree
  UNITS=$OUT/units.json
fi
if [ -n "$UNITS" ] && [ -f "$LINK/out/addrmap.tsv" ]; then
  log "frontier"
  MAPARG=(); [ -f "$MAP" ] && MAPARG=(--map "$MAP")
  python3 -I "$HERE/frontier.py" "$OUT/emu_raw.log" --addrmap "$LINK/out/addrmap.tsv" --units "$UNITS" \
    "${MAPARG[@]}" --out "$OUT/frontier_raw" > "$OUT/frontier_raw.txt" 2>&1 || true
fi

# --- summary --------------------------------------------------------------------------------------------------------
{
  echo "farm boot $C12 (link: ${FARM_LINK_DIR:-link/$C12}, image: ${IMAGE:-empty})"
  echo "- undefined symbols: $UNDEF"
  [ -f "$LINK/boot/summary.txt" ] && echo "- the farm's own boot: $(grep -h '^STOPPED' "$LINK/boot/summary.txt" | head -1)"
  for tag in raw stub; do
    L=$OUT/emu_$tag.log
    echo
    echo "## boot $tag (exit $(cat "$OUT/emu_$tag.rc"))"
    grep -h "memory manager before WinMain\|static initialisers done\|initializers skipped\|calling WinMain\|WinMain returned\|stub-faults:\|before-winmain:" "$L" || true
    grep -h "^stubbed-fault\|^replaced-call" "$L" | cut -f1-4 | head -25 || true
    sed -n '/^== trace/,/^   #4 /p' "$L" | head -12
    grep -h "^STOPPED\|^deadlock" "$L" || echo "(no stop line: timeout or normal end, see emu_$tag.log)"
  done
  if [ -f "$OUT/frontier_raw.txt" ]; then echo; echo "## frontier (raw boot)"; head -30 "$OUT/frontier_raw.txt"; fi
} > "$SUM"
log "summary: $SUM"
cat "$SUM"
