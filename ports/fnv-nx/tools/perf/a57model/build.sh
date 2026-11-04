#!/usr/bin/env bash
# build.sh - builds the A57 model's native parts into <out> (default: ./build, git-ignored):
#   liba57sim.so  the simulator, for uctrace.py (ctypes)
#   a57trace.so   the QEMU TCG plugin (needs qemu-plugin.h: QEMU_PLUGIN_H=<dir>, default /usr/include or ./qemu-hdr)
#   bench.elf     the calibration kernels (bare metal, qemu-system-aarch64 -M virt -cpu cortex-a57)
# Needs gcc, pkg-config glib-2.0 (plugin only), aarch64-linux-gnu-gcc (kernels only). Builds in seconds.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HERE/build}
mkdir -p "$OUT"
gcc -O2 -fPIC -shared -Wall -Wextra -o "$OUT/liba57sim.so" "$HERE/a57sim.c"
HDR=${QEMU_PLUGIN_H:-}
for d in "$HDR" /usr/include /usr/include/qemu "$HERE/qemu-hdr" /home/user/qemu-hdr; do
  if [ -n "$d" ] && [ -f "$d/qemu-plugin.h" ]; then HDR=$d; break; fi
done
if [ -n "$HDR" ] && [ -f "$HDR/qemu-plugin.h" ] && pkg-config --exists glib-2.0; then
  gcc -O2 -fPIC -shared -Wall -I"$HDR" -I"$HERE" $(pkg-config --cflags glib-2.0) -o "$OUT/a57trace.so" \
    "$HERE/a57trace.c" "$HERE/a57sim.c" $(pkg-config --libs glib-2.0)
else
  echo "build.sh: no qemu-plugin.h (QEMU_PLUGIN_H) or glib: a57trace.so not built" >&2
fi
if command -v aarch64-linux-gnu-gcc >/dev/null; then
  aarch64-linux-gnu-gcc -O2 -mcpu=cortex-a57 -ffreestanding -fno-builtin -nostdlib -static -fno-pie -no-pie \
    -Wl,--build-id=none -T "$HERE/calib/link.ld" -o "$OUT/bench.elf" "$HERE/calib/start.S" "$HERE/calib/bench.c"
fi
ls "$OUT"
