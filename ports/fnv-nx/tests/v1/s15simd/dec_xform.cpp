// dec_xform.cpp - V1 (D14): stand-in classes for the decomp's NiAVObject::UpdateWorldLoc 0x00a68bf0 and the
// NiTransform compose it calls (0x0062c250 and the four helpers 0x0043f8d0, 0x004b4500, 0x004a3760, 0x00439e90, bound
// as a931294's header says). xform.inc is generated at run time by test_s15simd_decomp.py from the decomp (never
// in git). Our code: the shells, the glue that binds each called name to the body at its address, and dec_xform().
#include <stdint.h>
class NiPoint3 {
  public:
    float x, y, z;
    NiPoint3() {}
    NiPoint3 operator+(const NiPoint3 &) const;
    void Set(float a, float b, float c) { x = a; y = b; z = c; }
};
class ThreeByThreeMatrix {
  public:
    float m_afEntries[9];
    ThreeByThreeMatrix() {}
    ThreeByThreeMatrix operator*(const ThreeByThreeMatrix &) const;
    NiPoint3 operator*(const NiPoint3 &) const;
    ThreeByThreeMatrix operator+(const ThreeByThreeMatrix &matrix) const;   // 0x0043f8d0: the product
};
class NiTransform {
  public:
    ThreeByThreeMatrix rotation;
    NiPoint3 translation;
    float scale;
    NiTransform() {}
    NiTransform operator*(const NiTransform &) const;
};
class NiAVObject {
  public:
    char pad0[0x18];
    NiAVObject *parent;
    char pad1[0x14];
    unsigned int flags;
    NiTransform local;
    NiTransform world;
    void UpdateWorldLoc();
};
class NiPosData {
  public:
    static NiPoint3 *ComputePositionAtTime(NiPoint3 *, float, const NiPoint3 *);
    static NiPoint3 ComputePositionAtTime(float, const NiPoint3 &);
};
struct SwizzledMatrix { float e[9]; };
struct NiPoint3b : public NiPoint3 { NiPoint3b(float a, float b, float c) { x = a; y = b; z = c; } };
struct M_004b4500 : public SwizzledMatrix { NiPoint3b FUN_004b4500(const NiPoint3 *v); };
struct Point3Offset { NiPoint3 m_kOffset; NiPoint3 *ProjectileLaunched(NiPoint3 *pResult, NiPoint3 *pSource); };

#include "xform.inc"

// glue: each name the compose calls -> the body at the address the image binds it to
ThreeByThreeMatrix ThreeByThreeMatrix::operator*(const ThreeByThreeMatrix &o) const { return operator+(o); }
NiPoint3 ThreeByThreeMatrix::operator*(const NiPoint3 &p) const
{
    NiPoint3b r = ((M_004b4500 *)this)->FUN_004b4500(&p);
    NiPoint3 q;
    q.x = r.x; q.y = r.y; q.z = r.z;
    return q;
}
NiPoint3 NiPosData::ComputePositionAtTime(float s, const NiPoint3 &p)
{
    NiPoint3 r;
    ComputePositionAtTime(&r, s, &p);
    return r;
}
NiPoint3 NiPoint3::operator+(const NiPoint3 &o) const
{
    NiPoint3 r;
    ((Point3Offset *)this)->ProjectileLaunched(&r, (NiPoint3 *)&o);
    return r;
}

extern "C" void *memcpy(void *, const void *, unsigned long);
static NiAVObject g_child, g_parent;
// in: [0] has parent, [1] flags, [2..14] local, [15..27] parent world, [28..40] child world before; out: world
extern "C" void dec_xform(const uint32_t *in, uint32_t *out)
{
    memcpy(&g_child.local, in + 2, 52);
    memcpy(&g_parent.world, in + 15, 52);
    memcpy(&g_child.world, in + 28, 52);
    g_child.flags = in[1];
    g_child.parent = in[0] ? &g_parent : 0;
    g_child.UpdateWorldLoc();
    memcpy(out, &g_child.world, 52);
}
