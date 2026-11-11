// app_unk_native.cpp: the application's IUnknown objects when the application is native code in this process (tests, the stream replayer).
#include "obj.h"
ULONG nxd3d_app_addref(IUnknown* u) { return u->AddRef(); }
ULONG nxd3d_app_release(IUnknown* u) { return u->Release(); }
// The loader's Wine-fixme log line in a native test process.
#include <stdio.h>
extern "C" void fnvnx_wine_fixme(uint32_t id) { fprintf(stderr, "fnvnx-wine-fixme: %u\n", (unsigned)id); }
