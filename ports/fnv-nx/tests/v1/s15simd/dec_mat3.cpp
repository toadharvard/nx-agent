// dec_mat3.cpp - V1 (D14): the decomp's NiMatrix3 transpose product Matrix3A588::FUN_00a588a0 (0x00a588a0,
// console/nimatrix3.cpp); mat3.inc is generated at run time from the decomp (never in git).
#include <stdint.h>
class Matrix3A588 {
  public:
    float m_afEntries[3][3];
    Matrix3A588 FUN_00a588a0(const Matrix3A588 &kMat) const;
};

#include "mat3.inc"

extern "C" void *memcpy(void *, const void *, unsigned long);
static Matrix3A588 g_a, g_b, g_r;
// in: [0..8] this, [9..17] kMat; out: product
extern "C" void dec_mat3(const uint32_t *in, uint32_t *out)
{
    memcpy(&g_a, in, 36);
    memcpy(&g_b, in + 9, 36);
    g_r = g_a.FUN_00a588a0(g_b);
    memcpy(out, &g_r, 36);
}
