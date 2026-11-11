/* d9replay_rt.h: types shared by the replayer runtime (d9replay_rt.cpp) and the generated tables/invokers (d9replay.gen.cpp, gen/gen_replay.py). */
#ifndef D9REPLAY_RT_H
#define D9REPLAY_RT_H
#include "d9wrap.gen.h"
union D9A { uint64_t u; void *p; };                    /* one argument as the invoker receives it */
enum { K_VALUE, K_ENUM, K_FLAGS, K_FLOAT, K_OHANDLE, K_HANDLE, K_STRUCT, K_BUFFER, K_LOCKED };
enum { DIR_IN, DIR_OUT, DIR_INOUT };
enum { RK_VOID, RK_RAW, RK_HANDLE };
struct D9RP { const char *name; uint8_t kind, dir, byref, in_when, cano /* 0 none 1 ohandle 2 handle */, lshape /* 0 linear 1 rect 2 box 3 dc */, npitch, has_off;
              int8_t iface /* handle: interface index, -1 = chosen by the riid param */; uint16_t ssz, ptr_off; const D9SDesc *sd; };
struct D9RM { const char *name; int iface; unsigned slot, np; const D9RP *p; uint8_t ret_kind, closes; int8_t ret_iface, riid_param;
              uintptr_t (*inv)(void *self, void *fn, const D9A *a);                              /* re-issues the call through the wrapper */
              uint64_t (*ev)(const D9A *a, D9Obj *o, unsigned kind, unsigned param); };          /* kind 0: `off` rule, 1: canon `when` rule of a buffer param */
struct D9Entry { const char *name; void *fn; };         /* the implementation's export entry table (null name ends it) */
extern const D9RM d9r_methods[], d9r_exports[]; extern const unsigned d9r_ibase[], d9r_niface, d9r_nexports;
extern "C" const D9Entry d9impl_entries[];
extern "C" void d9impl_before_call(unsigned ordinal, unsigned seq);   /* the integrator defines all three hooks. Called before each re-issued call (ordinal = index among depth-0 records) */
extern "C" void d9impl_bind(unsigned id, void *p);                   /* called whenever a recorded object id is bound to a live pointer */
extern "C" uintptr_t d9impl_ohandle(unsigned token);                  /* the raw value (HWND, IUnknown*, ...) of an ohandle token; the same token always gives the same value */
static inline float d9r_f(uint64_t u) { uint32_t b = (uint32_t)u; float f; memcpy(&f, &b, 4); return f; }
#endif
