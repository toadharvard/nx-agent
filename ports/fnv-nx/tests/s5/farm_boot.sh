#!/usr/bin/env bash
# farm_boot.sh - emulated boot of a farm link. Fetches branch link/<commit12> of the private artifacts repository
# (S9's link_job: out/ and fixed/ of tools/link resolve, probe, resolve, apply; the full job adds guest/fnvnx.elf,
# its own boots under boot/ and the data image boot/synth.exe), takes the farm's guest (packed to an NSO here) or
# links one (toolchain/game/build.sh, D3D9=null), boots it under tests/s5/emu_guest.py on a game image, and
# summarises. tools/farm/full_job.sh runs it on the farm for the stub boot (FARM_GUEST_DIR, FARM_BOOTS=stub).
#
#   tests/s5/farm_boot.sh <commit> <out dir> [image]
#
# image: the FalloutNV.exe the guest maps. Default: $FNVNX_IMAGE, else the link's boot/synth.exe (S14's synthesized
# image as the farm made it), else none (the empty image). Boots ($FARM_BOOTS, default "raw stub"), all with
# --skip-faulting-init: raw, and stub with the known-fault table (--stub-faults tests/s5/known_faults.txt,
# docs/TOOLCHAIN.md section 18).
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
#   FARM_BOOTS      the boots to run, from "raw stub" (default both)
#   FARM_UNITS      a units.json instead of the one from t/<commit12>
#   FARM_RELINK     1: link the guest here even when the link has one (about 1.5 minutes)
#   FARM_PIN        1: pack the farm's guest pinned to its link base (elf2nso --pin; default relocatable, as the
#                   farm boots it)
#   PY              the Python with unicorn (default python3)
#   VORBIS          the libvorbis prefix for build.sh; JOBS, SSE2NEON, DEVKITPRO passed to build.sh
#   EMU_TIMEOUT     seconds per boot (default 1200); BUDGET instructions per thread slice (default 6000000000)
# Runtime: under a minute per boot; the local guest link (f13) about 1.5 minutes. Inside D17's ten minutes.
set -euo pipefail
COMMIT=${1:?commit}; OUT=${2:?out dir}; IMAGE=${3:-${FNVNX_IMAGE:-}}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PY:-python3}
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
if [ -z "${ARTIFACTS:-}" ]; then
  if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
C12=${COMMIT:0:12}
BOOTS=${FARM_BOOTS:-raw stub}
EMU_TIMEOUT=${EMU_TIMEOUT:-1200}; BUDGET=${BUDGET:-6000000000}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
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
boot() {   # boot <tag> [extra emu_guest.py args]
  local tag=$1; shift
  log "boot $tag"
  local rc=0 root=$OUT/sdmc-$tag
  rm -rf "$root"; mkdir -p "$root"
  timeout "$EMU_TIMEOUT" nice "$PY" -I "$HERE/emu_guest.py" "$GDIR/guest.nso" --elf "$GDIR/fnvnx.elf" \
    --skip-faulting-init --root "$root" --budget "$BUDGET" ${IMAGE:+--file "fnvnx/game/FalloutNV.exe=$IMAGE"} \
    --summary-json "$OUT/boot_summary_$tag.json" "$@" \
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
    stub) boot stub --stub-faults "$HERE/known_faults.txt" ;;
    *) echo "farm_boot: unknown boot '$tag' (FARM_BOOTS: raw stub)" >&2; exit 1 ;;
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
  echo "- undefined symbols: $UNDEF"
  [ -f "$LINK/boot/summary.txt" ] && echo "- the farm's raw boot: $(grep -h '^STOPPED' "$LINK/boot/summary.txt" | head -1)"
  for tag in $BOOTS; do
    L=$OUT/emu_$tag.log
    echo
    echo "## boot $tag (exit $(cat "$OUT/emu_$tag.rc"))"
    grep -h "memory manager after construction\|memory manager before WinMain\|static initialisers done\|initializers skipped\|calling WinMain\|WinMain returned\|stub-faults:\|before-winmain:" "$L" || true
    grep -h "^stubbed-fault\|^replaced-call" "$L" | cut -f1-4 | head -25 || true
    grep -h "^skip-summary" "$L" | cut -f1-4 | head -8 || true
    sed -n '/^== trace/,/^   #4 /p' "$L" | head -14
    grep -h "^STOPPED\|^deadlock" "$L" || echo "(no stop line: timeout or normal end, see emu_$tag.log)"
    grep -h "^   \[fnvnx\] \(MessageBox\|ExitProcess\)" "$L" | sort -u | head -5 || true
    grep -h "^milestone	" "$L" | cut -f2-4 | tr '\t' ' ' | sed 's/^/game milestone /' || true
    if [ -f "$OUT/frontier_$tag.txt" ]; then echo; echo "### frontier ($tag boot)"; head -12 "$OUT/frontier_$tag.txt"; fi
  done
} > "$SUM"
log "summary: $SUM"
cat "$SUM"
