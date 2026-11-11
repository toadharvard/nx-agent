// app_unk_native.cpp: the application's IUnknown objects when the application is native code in this process (tests, the stream replayer).
#include "obj.h"
ULONG nxd3d_app_addref(IUnknown* u) { return u->AddRef(); }
ULONG nxd3d_app_release(IUnknown* u) { return u->Release(); }
// The platform's process exit when the layer runs as native code in a test process (pv_exit.cpp ends the process through it).
#include <stdlib.h>
extern "C" void os_exit_process(int code) { _Exit(code & 0xff ? code & 0xff : 1); }
