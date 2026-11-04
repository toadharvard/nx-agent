#!/usr/bin/env bash
# boot_game.sh <fnvnx.elf> <FalloutNV.exe> <work dir> - boot the whole game guest under linhost on real host threads
# (qemu-aarch64 -R 0xC0000000, or natively on aarch64 Linux) to "fnvnx boot: calling WinMain", the milestone the
# farm's Unicorn boot reaches (tests/s5/emu_guest.py --skip-faulting-init, boot/summary.txt of the link).
#   fnvnx.elf:     the guest image of a link (fnv-nx-build link-head guest/fnvnx.elf), loaded as it is
#   FalloutNV.exe: the owner's exe; symlinked to <work>/sdmc/fnvnx/game/FalloutNV.exe (sdmc:/ is <work>/sdmc)
#   work dir:      outside the repository: linhost, the sdmc root and boot.log go there
# Env: TIMEOUT (seconds, default 300), EXPECT (default the WinMain line), QEMU (default qemu-aarch64; empty: native),
#      HEAP_MIB (default 64, as emu_guest.py). Exit 0 when the line was reached.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -eq 3 ] || { echo "usage: $0 <fnvnx.elf> <FalloutNV.exe> <work dir>" >&2; exit 2; }
ELF=$(readlink -f "$1")
EXE=$(readlink -f "$2")
WORK=$(mkdir -p "$3" && cd "$3" && pwd)
TIMEOUT=${TIMEOUT:-300}
COLLECT=${COLLECT:-0}   # 1: collect-all boot (linhost --collect-traps): vslot/ctor traps return to their caller and are
                        # listed; no stop at WinMain unless EXPECT is set; tests/s5/collect_traps.py makes the TSV
if [ "$COLLECT" = 1 ]; then EXPECT=${EXPECT-}; else EXPECT=${EXPECT:-fnvnx boot: calling WinMain}; fi
QEMU=${QEMU-qemu-aarch64}
HEAP_MIB=${HEAP_MIB:-64}

bash "$HERE/build.sh" "$WORK/build" > "$WORK/build.log" 2>&1 || { tail -20 "$WORK/build.log" >&2; exit 2; }
mkdir -p "$WORK/sdmc/fnvnx/game"
ln -sfn "$EXE" "$WORK/sdmc/fnvnx/game/FalloutNV.exe"
mkdir -p "$WORK/sdmc/switch/fnv"
ln -sfn "$EXE" "$WORK/sdmc/switch/fnv/FalloutNV.exe"   # NX1's SD layout; guests linked before NX1 read fnvnx/game
if [ -d "$(dirname "$EXE")/Data" ]; then   # the game install's Data/ (tools/gamedata_fetch.sh), linked in place
  ln -sfn "$(readlink -f "$(dirname "$EXE")/Data")" "$WORK/sdmc/fnvnx/game/Data"
  ln -sfn "$(readlink -f "$(dirname "$EXE")/Data")" "$WORK/sdmc/switch/fnv/Data"
fi
for f in "$(dirname "$EXE")"/*.ini; do   # the exe's inis (Fallout_default.ini: WinMain stops without it)
  [ -f "$f" ] || continue
  ln -sfn "$(readlink -f "$f")" "$WORK/sdmc/fnvnx/game/$(basename "$f")"
  ln -sfn "$(readlink -f "$f")" "$WORK/sdmc/switch/fnv/$(basename "$f")"
done

run=("$WORK/build/linhost" --root "$WORK/sdmc" --skip-faulting-init --heap "$HEAP_MIB")
[ -n "$EXPECT" ] && run+=(--expect "$EXPECT")
[ "$COLLECT" = 1 ] && run+=(--collect-traps)
run+=("$ELF")
[ -n "$QEMU" ] && run=("$QEMU" -R 0xC0000000 "${run[@]}")
echo "boot_game: ${run[*]}"
t0=$(date +%s.%N)
set +e
timeout -k 5 "$TIMEOUT" "${run[@]}" > "$WORK/boot.log" 2>&1
rc=$?
set -e
t1=$(date +%s.%N)
secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')
grep -E '^(fnvnx boot|linhost: (guarding|guest heap|fnv-nx guest|reached|fault|trap|frames|initializers|the guest))' \
    "$WORK/boot.log" | grep -v ': no export$' | tail -25 || true
# symbolize a fault's addresses (the image keeps its .symtab)
if grep -q '^linhost: fault' "$WORK/boot.log"; then
  addrs=$(grep -E '^linhost: (fault|frames)' "$WORK/boot.log" | grep -oE '0x[0-9a-f]{16}' | sort -u)
  for a in $addrs; do
    printf '  %s %s\n' "$a" "$(aarch64-linux-gnu-addr2line -f -e "$ELF" "$a" 2>/dev/null | head -1)"
  done
fi
if [ $rc -eq 0 ] && grep -q '^linhost: reached' "$WORK/boot.log"; then
  echo "boot_game: REACHED \"$EXPECT\" in ${secs} s wall (rc 0), log $WORK/boot.log"
  exit 0
fi
[ $rc -eq 124 ] && why="timeout after ${TIMEOUT} s" || why="rc $rc"
echo "boot_game: NOT reached \"$EXPECT\" ($why, ${secs} s wall); last lines of $WORK/boot.log:"
tail -15 "$WORK/boot.log"
exit 1
