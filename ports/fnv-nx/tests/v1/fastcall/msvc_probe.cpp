// msvc_probe.cpp - compiled by MSVC 2008 (cl /O2 /c): per kind, a caller of `K __fastcall f_K(int)`. The caller's
// code before the call shows where the hidden result buffer goes: `lea ecx, [esp..]` with 0x55 in edx (ecx), a
// pushed address with 0x55 in ecx (first stack word), or neither (eax:edx, no buffer).
#include "kinds.h"
int sink;
#define X(name, ...) struct name __VA_ARGS__; name __fastcall f_##name(int); \
  void c_##name() { name r = f_##name(0x55); sink = *(int *)&r; }
KINDS(X)
