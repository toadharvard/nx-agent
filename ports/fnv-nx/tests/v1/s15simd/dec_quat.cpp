// dec_quat.cpp - V1 (D14): the decomp's NiQuaternion::FromRotationMatrix 0x00a6df40 with its QuaternionSqrt
// (niquaternion.cpp); quat.inc is generated at run time from the decomp (never in git).
#include <stdint.h>
extern "C" double sqrt(double);
class ThreeByThreeMatrix { public: float m[3][3]; };
class NiQuaternion {
  public:
    float m_fVectorX, m_fVectorY, m_fVectorZ, m_fScalarPart;
    void FromRotationMatrix(const ThreeByThreeMatrix &matrix);
};
extern const unsigned int DAT_011a9e98[3];

#include "quat.inc"

const unsigned int DAT_011a9e98[3] = {1, 2, 0};
extern "C" void *memcpy(void *, const void *, unsigned long);
static ThreeByThreeMatrix g_m;
static NiQuaternion g_q;
// in: [0..8] matrix, [9..12] quaternion before; out: quaternion after
extern "C" void dec_quat(const uint32_t *in, uint32_t *out)
{
    memcpy(&g_m, in, 36);
    memcpy(&g_q, in + 9, 16);
    g_q.FromRotationMatrix(g_m);
    memcpy(out, &g_q, 16);
}
