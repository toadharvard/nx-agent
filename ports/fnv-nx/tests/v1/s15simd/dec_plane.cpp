// dec_plane.cpp - V1 (D14): stand-ins for the decomp's sphere/plane classification 0x004b6100, the plane distance
// 0x004b6160, the dot product 0x004b6190 (globalfunc.cpp) and the bound getter 0x0043d450 (combatthreatmap.cpp);
// plane.inc is generated at run time from the decomp (never in git). Glue binds Distance -> 0x004b6160 and
// Dot -> 0x004b6190 (cull_worker_00a694e0.c's header: 0x004b6100 calls 0x004b6160, which calls 0x004b6190).
#include <stdint.h>
class NiPoint3 { public: float x, y, z; };
struct NiPlane_004b6100 { float Distance(void *); };
struct B_004b6100 {
    float cx, cy, cz, radius;
    int ClassifySphereAgainstPlane(NiPlane_004b6100 *m_kFogPlane);
};
struct NiPoint3_ext : NiPoint3 { float Dot(const NiPoint3 &pt) const; };
struct FUN_004b6160_class {
    NiPoint3_ext normal;
    float d;
    float EvaluatePlane(const NiPoint3 &pt) const;
};
struct FUN_004b6190_shell : public NiPoint3 { float DotProduct(void *); };
struct NiBound;
extern NiBound DAT_011f4288;
struct FUN_0043d450_Owner { NiBound *FUN_0043d450(); };

#include "plane.inc"

struct NiBound { float f[4]; };
NiBound DAT_011f4288;
float NiPlane_004b6100::Distance(void *s) { return ((FUN_004b6160_class *)this)->EvaluatePlane(*(const NiPoint3 *)s); }
float NiPoint3_ext::Dot(const NiPoint3 &p) const { return ((FUN_004b6190_shell *)this)->DotProduct((void *)&p); }
extern "C" void *fn_0043d450(const void *self) { return ((FUN_0043d450_Owner *)self)->FUN_0043d450(); }

extern "C" void *memcpy(void *, const void *, unsigned long);
static B_004b6100 g_s;
static uint32_t g_p[4];
// in: [0..3] sphere, [4..7] plane; out: [0] side
extern "C" void dec_plane(const uint32_t *in, uint32_t *out)
{
    memcpy(&g_s, in, 16);
    memcpy(g_p, in + 4, 16);
    out[0] = (uint32_t)g_s.ClassifySphereAgainstPlane((NiPlane_004b6100 *)g_p);
}
