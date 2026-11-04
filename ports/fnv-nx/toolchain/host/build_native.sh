#!/bin/bash
# The game's host module with the native renderer (NX1 HOST=native, the default; owner decision: the native deko3d
# path is the target): toolchain/host/host.c built with -DFNVNX_HOST_NO_CONSOLE (deko3d owns the default window),
# S10's reservation, render/native/host (nxgpu.c, nxgpu_host.c: ext module 10, created at the guest's first call)
# and render/native/front/gpu_dispatch.c, linked with libdeko3d and libnx.
#   DEVKITPRO=<dkp> toolchain/host/build_native.sh <out dir> [deko3d prefix]  -> <out>/host.elf, host.map
# The deko3d prefix defaults to <out>/deko3d (toolchain/deko3d/build.sh builds it there when missing).
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
OUT=$(mkdir -p "$1" && cd "$1" && pwd); D3=${2:-$OUT/deko3d}
HERE=$(cd "$(dirname "$0")" && pwd); TC=$(cd "$HERE/.." && pwd); ROOT=$(cd "$TC/.." && pwd); N=$ROOT/render/native
[ -f "$D3/lib/libdeko3d.a" ] || "$TC/deko3d/build.sh" "$D3" >/dev/null
export PATH=$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
HA="-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIE"
HF="-O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -D__SWITCH__ -I$TC/include -I$ROOT/startup/host \
-I$D3/include -I$DEVKITPRO/libnx/include -I$N/include ${HOST_DEFS:-}"
O=$OUT/obj; rm -rf "$O"; mkdir -p "$O"
aarch64-none-elf-gcc $HA $HF -DFNVNX_HOST_NO_CONSOLE -c "$TC/host/host.c" -o "$O/host.o"
for s in "$ROOT/startup/host/reserve.c" "$ROOT/startup/host/reserve_nx.c" "$N/host/nxgpu_host.c" "$N/host/nxgpu.c" \
         "$N/front/gpu_dispatch.c"; do
	aarch64-none-elf-gcc $HA $HF -c "$s" -o "$O/$(basename "${s%.c}").o"
done
aarch64-none-elf-g++ -specs="$DEVKITPRO/libnx/switch.specs" $HA "$O"/*.o -L"$D3/lib" -L"$DEVKITPRO/libnx/lib" \
	-ldeko3d -lnx -Wl,--wrap=__syscall_thread_create -Wl,-Map,"$OUT/host.map" -o "$OUT/host.elf"
echo "$OUT/host.elf"
