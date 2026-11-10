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
"$ROOT/render/d3d9/rec/build.sh" "$W/rec" >/dev/null
ls -l "$W"/out/*.exe "$W/rec/d3d9.dll" | awk '{print $5, $9}'
