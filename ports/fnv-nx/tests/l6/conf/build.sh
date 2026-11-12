#!/usr/bin/env bash
# build.sh [WORK]: builds Wine 9.0's d3d9 conformance tests (LGPL, fetched into scratch, never copied here) as standalone i686 PE exes
# plus our recorder d3d9.dll. Output: WORK/out/{device,stateblock,visual,d3d9ex}.exe, WORK/rec/d3d9.dll.  WORK default /home/user/work/l6/conf.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../.." && pwd)
W=${1:-/home/user/work/l6/conf}; mkdir -p "$W/out" "$W/shim/wine" "$W/rec"
[ -d "$W/wine/dlls/d3d9/tests" ] || git clone --depth 1 --branch wine-9.0 https://gitlab.winehq.org/wine/wine.git "$W/wine"   # tag = installed runtime (Wine 9.0)
# wine/test.h is header-only (STANDALONE: own main + testlist); it needs wine/debug.h, replaced here by a 20-line shim (no ntdll debug channels)
cp "$W/wine/include/wine/test.h" "$W/shim/wine/test.h"
cat > "$W/shim/wine/debug.h" <<'EOT'
#ifndef SHIM_WINE_DEBUG_H
#define SHIM_WINE_DEBUG_H
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>
#define __WINE_PRINTF_ATTR(fmt,args) __attribute__((format(printf,fmt,args)))
#define WINE_DEFAULT_DEBUG_CHANNEL(x)
#define FIXME(...) do {} while (0)
#define WARN(...) do {} while (0)
#define TRACE(...) do {} while (0)
#define ERR(...) do {} while (0)
static inline const char *wine_dbgstr_rect(const RECT *r)
{
    static char buf[8][64]; static int n; char *b = buf[n++ & 7];
    if (!r) return "(null)";
    snprintf(b, 64, "(%ld,%ld)-(%ld,%ld)", r->left, r->top, r->right, r->bottom);
    return b;
}
#endif
EOT
T=$W/wine/dlls/d3d9/tests
for t in stateblock device d3d9ex visual; do   # -include initguid.h: the IIDs (mingw has no libd3d9 IIDs); -O1 -w as the tests are not ours
  ( cd "$T" && i686-w64-mingw32-gcc -DSTANDALONE -D__WINESRC__ -O1 -w -include initguid.h -I"$W/shim" -I. -o "$W/out/$t.exe" $t.c \
      -ld3d9 -luser32 -lgdi32 -lole32 -luuid -static-libgcc )
done
"$ROOT/render/tools/rec/build.sh" "$W/rec" >/dev/null
ls -l "$W"/out/*.exe "$W/rec/d3d9.dll" | awk '{print $5, $9}'
# Variant WORK/rec_nofree/d3d9.dll (diagnostic, scratch only): identical except an object whose Release returned 0 is "zombied": its id is retired
# once (D9Rec::retire); the D9Obj and its wrappers are NOT freed (`free_obj(ret[i])` replaced by: unlink from the real-pointer map only, so a reused real address gets a fresh object) and a later Release->0 does not retire again. Needed because
# Wine's d3d9 keeps objects alive after a Release to 0 (implicit swapchain, back buffers) and the tests use the pointer again (device.c:1294,
# test_swapchain): the as-built recorder then dereferences a freed wrapper (README). The zombie streams are NOT valid contract vectors for such objects.
N=$W/rec_nofree; rm -rf "$N/src"; mkdir -p "$N"; cp -r "$W/rec/src" "$N/src"; S=$N/src
sed -i 's/for (unsigned i = 0; i < nret; i++) free_obj(ret\[i\]);/for (unsigned i = 0; i < nret; i++) { D9Obj **t = \&g_objs[hp((uintptr_t)ret[i]->real)]; while (*t \&\& *t != ret[i]) t = \&(*t)->hnext; if (*t) *t = ret[i]->hnext; }/; s/void D9Rec::retire(D9Obj \*o) { if (nret < 4)/void D9Rec::retire(D9Obj *o) { if (o->dead) return; o->dead = 1; if (nret < 4)/' "$S/d9stream.cpp"
sed -i 's/uint32_t k0, k1; int tall; D9Tv \*tv;/uint32_t k0, k1; int dead; int tall; D9Tv *tv;/' "$S/d9stream.h"
grep -q 'free_obj(ret' "$S/d9stream.cpp" && { echo "nofree patch did not apply" >&2; exit 1; }; grep -q 'o->dead = 1' "$S/d9stream.cpp" && grep -q 'int dead' "$S/d9stream.h" || { echo "zombie patch did not apply" >&2; exit 1; }
CXX=${MINGW_CXX:-i686-w64-mingw32-g++-win32}; command -v "$CXX" >/dev/null || CXX=i686-w64-mingw32-g++
CXXF="-std=c++17 -O2 -w -fno-exceptions -fno-rtti -fno-threadsafe-statics -I$S"
rm -f "$S"/*.o; for f in d9wrap.gen d9stream rec_main rec_dc; do $CXX $CXXF -c "$S/$f.cpp" -o "$S/$f.o"; done
$CXX -shared -o "$N/d3d9.dll" "$S"/*.o "$S/d9rec.def" -static-libgcc -static-libstdc++ -Wl,--enable-stdcall-fixup -Wl,--kill-at -lgdi32 -luser32
echo "$N/d3d9.dll"
