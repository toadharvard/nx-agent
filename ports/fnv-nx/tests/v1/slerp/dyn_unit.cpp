// dyn_unit.cpp - the decomp's Slerp, weight and normalisation as one ILP32 unit, for test_slerp_decomp.py's dynamic
// instruction count (unicorn). decomp_slerp.inc is generated at run time from the decomp (never in git).
extern "C" {
double __fnv_0109c9d0, __fnv_0109c9d8;
float __fnv_011f3fec, __fnv_011f3ff0;
}
#define DAT_0109c9d0 __fnv_0109c9d0
#define DAT_0109c9d8 __fnv_0109c9d8
#define DAT_011F3FEC __fnv_011f3fec
#define DAT_011F3FF0 __fnv_011f3ff0

class NiQuaternion {
  public:
    float m_fVectorX, m_fVectorY, m_fVectorZ, m_fScalarPart;
    void EnsureCanonicalSign();
    void Slerp(float t, const NiQuaternion &a, const NiQuaternion &b);
    static __attribute__((noinline)) float CalculateSlerpWeight(float t, float cosine);
};
struct FUN_00a4ff00_shell : public NiQuaternion {
    void NormalizeQuaternion();
};

#include "decomp_slerp.inc"

// the image binds CalculateSlerpWeight and EnsureCanonicalSign to 0x00a6dc30 / 0x00a4ff00 (units.json): aliases
extern "C" float dyn_weight(float t, float c) __asm__("_ZN12NiQuaternion20CalculateSlerpWeightEff")
    __attribute__((alias("_Z12FUN_00a6dc30ff")));
extern "C" void dyn_norm(void) __asm__("_ZN12NiQuaternion19EnsureCanonicalSignEv")
    __attribute__((alias("_ZN18FUN_00a4ff00_shell19NormalizeQuaternionEv")));

extern "C" void dyn_decomp(NiQuaternion *self, float t, const NiQuaternion *a, const NiQuaternion *b)
    __asm__("_ZN12NiQuaternion5SlerpEfRKS_S1_");
