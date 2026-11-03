// fnv-nx D3DX9 (stream S6c-nine, guest ILP32): the D3DX math functions the game imports, and the ones the shim
// declares for the backend. D3DX conventions: row vectors (v' = v * M), matrices row-major (_11.._44), left-handed
// helpers. Every function allows pOut to alias an input. Plain C++ (the compiler vectorizes the 4x4 products).
#include <d3dx9math.h>
#include <string.h>

namespace {
inline void mul(float r[4][4], const D3DMATRIX* a, const D3DMATRIX* b) {
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++)
            r[i][j] = a->m[i][0] * b->m[0][j] + a->m[i][1] * b->m[1][j] + a->m[i][2] * b->m[2][j] + a->m[i][3] * b->m[3][j];
}
} // namespace

extern "C" {

D3DXMATRIX* WINAPI D3DXMatrixMultiply(D3DXMATRIX* pOut, const D3DXMATRIX* pM1, const D3DXMATRIX* pM2) {
    float r[4][4];
    mul(r, pM1, pM2);
    memcpy(pOut->m, r, sizeof r);
    return pOut;
}

D3DXMATRIX* WINAPI D3DXMatrixMultiplyTranspose(D3DXMATRIX* pOut, const D3DXMATRIX* pM1, const D3DXMATRIX* pM2) {
    float r[4][4];
    mul(r, pM1, pM2);
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++) pOut->m[i][j] = r[j][i];
    return pOut;
}

D3DXMATRIX* WINAPI D3DXMatrixTranspose(D3DXMATRIX* pOut, const D3DXMATRIX* pM) {
    float r[4][4];
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++) r[i][j] = pM->m[j][i];
    memcpy(pOut->m, r, sizeof r);
    return pOut;
}

// Roll (about z) first, then pitch (about x), then yaw (about y): M = Rz(roll) * Rx(pitch) * Ry(yaw).
D3DXMATRIX* WINAPI D3DXMatrixRotationYawPitchRoll(D3DXMATRIX* pOut, FLOAT Yaw, FLOAT Pitch, FLOAT Roll) {
    float sy = sinf(Yaw), cy = cosf(Yaw), sp = sinf(Pitch), cp = cosf(Pitch), sr = sinf(Roll), cr = cosf(Roll);
    pOut->_11 = cr * cy + sr * sp * sy;  pOut->_12 = sr * cp;  pOut->_13 = sr * sp * cy - cr * sy;  pOut->_14 = 0;
    pOut->_21 = cr * sp * sy - sr * cy;  pOut->_22 = cr * cp;  pOut->_23 = sr * sy + cr * sp * cy;  pOut->_24 = 0;
    pOut->_31 = cp * sy;                 pOut->_32 = -sp;      pOut->_33 = cp * cy;                 pOut->_34 = 0;
    pOut->_41 = 0;                       pOut->_42 = 0;        pOut->_43 = 0;                       pOut->_44 = 1;
    return pOut;
}

// General inverse by cofactors; NULL when the matrix is singular.
D3DXMATRIX* WINAPI D3DXMatrixInverse(D3DXMATRIX* pOut, FLOAT* pDeterminant, const D3DXMATRIX* pM) {
    const float* a = &pM->_11;
    float c[16];
    c[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    c[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    c[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    c[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    c[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    c[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    c[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    c[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    c[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    c[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    c[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    c[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    c[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    c[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    c[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    c[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    float det = a[0] * c[0] + a[1] * c[4] + a[2] * c[8] + a[3] * c[12];
    if (pDeterminant) *pDeterminant = det;
    if (det == 0.0f) return nullptr;
    float inv = 1.0f / det;
    for (int i = 0; i < 16; i++) (&pOut->_11)[i] = c[i] * inv;
    return pOut;
}

D3DXPLANE* WINAPI D3DXPlaneNormalize(D3DXPLANE* pOut, const D3DXPLANE* pP) {
    float len = sqrtf(pP->a * pP->a + pP->b * pP->b + pP->c * pP->c);
    if (len == 0.0f) { pOut->a = pOut->b = pOut->c = pOut->d = 0.0f; return pOut; }
    float k = 1.0f / len;
    D3DXPLANE r(pP->a * k, pP->b * k, pP->c * k, pP->d * k);
    *pOut = r;
    return pOut;
}

// The plane as a row 4-vector times M (M is the inverse transpose of the point transform, as D3DX documents).
D3DXPLANE* WINAPI D3DXPlaneTransform(D3DXPLANE* pOut, const D3DXPLANE* pP, const D3DXMATRIX* pM) {
    D3DXPLANE p = *pP;
    pOut->a = p.a * pM->_11 + p.b * pM->_21 + p.c * pM->_31 + p.d * pM->_41;
    pOut->b = p.a * pM->_12 + p.b * pM->_22 + p.c * pM->_32 + p.d * pM->_42;
    pOut->c = p.a * pM->_13 + p.b * pM->_23 + p.c * pM->_33 + p.d * pM->_43;
    pOut->d = p.a * pM->_14 + p.b * pM->_24 + p.c * pM->_34 + p.d * pM->_44;
    return pOut;
}

D3DXVECTOR3* WINAPI D3DXVec3Normalize(D3DXVECTOR3* pOut, const D3DXVECTOR3* pV) {
    float len = sqrtf(pV->x * pV->x + pV->y * pV->y + pV->z * pV->z);
    if (len == 0.0f) { pOut->x = pOut->y = pOut->z = 0.0f; return pOut; }
    float k = 1.0f / len;
    D3DXVECTOR3 r(pV->x * k, pV->y * k, pV->z * k);
    *pOut = r;
    return pOut;
}

// (x, y, z, 1) * M, projected back to w = 1
D3DXVECTOR3* WINAPI D3DXVec3TransformCoord(D3DXVECTOR3* pOut, const D3DXVECTOR3* pV, const D3DXMATRIX* pM) {
    D3DXVECTOR3 v = *pV;
    float w = v.x * pM->_14 + v.y * pM->_24 + v.z * pM->_34 + pM->_44;
    float k = w != 0.0f ? 1.0f / w : 0.0f;
    pOut->x = (v.x * pM->_11 + v.y * pM->_21 + v.z * pM->_31 + pM->_41) * k;
    pOut->y = (v.x * pM->_12 + v.y * pM->_22 + v.z * pM->_32 + pM->_42) * k;
    pOut->z = (v.x * pM->_13 + v.y * pM->_23 + v.z * pM->_33 + pM->_43) * k;
    return pOut;
}

// (x, y, z, 0) * M
D3DXVECTOR3* WINAPI D3DXVec3TransformNormal(D3DXVECTOR3* pOut, const D3DXVECTOR3* pV, const D3DXMATRIX* pM) {
    D3DXVECTOR3 v = *pV;
    pOut->x = v.x * pM->_11 + v.y * pM->_21 + v.z * pM->_31;
    pOut->y = v.x * pM->_12 + v.y * pM->_22 + v.z * pM->_32;
    pOut->z = v.x * pM->_13 + v.y * pM->_23 + v.z * pM->_33;
    return pOut;
}

D3DXVECTOR4* WINAPI D3DXVec4Transform(D3DXVECTOR4* pOut, const D3DXVECTOR4* pV, const D3DXMATRIX* pM) {
    D3DXVECTOR4 v = *pV;
    pOut->x = v.x * pM->_11 + v.y * pM->_21 + v.z * pM->_31 + v.w * pM->_41;
    pOut->y = v.x * pM->_12 + v.y * pM->_22 + v.z * pM->_32 + v.w * pM->_42;
    pOut->z = v.x * pM->_13 + v.y * pM->_23 + v.z * pM->_33 + v.w * pM->_43;
    pOut->w = v.x * pM->_14 + v.y * pM->_24 + v.z * pM->_34 + v.w * pM->_44;
    return pOut;
}

D3DXMATRIX* WINAPI D3DXMatrixPerspectiveFovLH(D3DXMATRIX* pOut, FLOAT fovy, FLOAT Aspect, FLOAT zn, FLOAT zf) {
    float ys = 1.0f / tanf(fovy * 0.5f), xs = ys / Aspect, q = zf / (zf - zn);
    memset(pOut->m, 0, sizeof pOut->m);
    pOut->_11 = xs;
    pOut->_22 = ys;
    pOut->_33 = q;
    pOut->_34 = 1.0f;
    pOut->_43 = -zn * q;
    return pOut;
}

D3DXMATRIX* WINAPI D3DXMatrixOrthoOffCenterLH(D3DXMATRIX* pOut, FLOAT l, FLOAT r, FLOAT b, FLOAT t, FLOAT zn, FLOAT zf) {
    memset(pOut->m, 0, sizeof pOut->m);
    pOut->_11 = 2.0f / (r - l);
    pOut->_22 = 2.0f / (t - b);
    pOut->_33 = 1.0f / (zf - zn);
    pOut->_41 = (l + r) / (l - r);
    pOut->_42 = (t + b) / (b - t);
    pOut->_43 = zn / (zn - zf);
    pOut->_44 = 1.0f;
    return pOut;
}

D3DXMATRIX* WINAPI D3DXMatrixLookAtLH(D3DXMATRIX* pOut, const D3DXVECTOR3* pEye, const D3DXVECTOR3* pAt,
                                      const D3DXVECTOR3* pUp) {
    D3DXVECTOR3 z = *pAt - *pEye, x, y;
    D3DXVec3Normalize(&z, &z);
    D3DXVec3Cross(&x, pUp, &z);
    D3DXVec3Normalize(&x, &x);
    D3DXVec3Cross(&y, &z, &x);
    pOut->_11 = x.x; pOut->_12 = y.x; pOut->_13 = z.x; pOut->_14 = 0.0f;
    pOut->_21 = x.y; pOut->_22 = y.y; pOut->_23 = z.y; pOut->_24 = 0.0f;
    pOut->_31 = x.z; pOut->_32 = y.z; pOut->_33 = z.z; pOut->_34 = 0.0f;
    pOut->_41 = -D3DXVec3Dot(&x, pEye); pOut->_42 = -D3DXVec3Dot(&y, pEye); pOut->_43 = -D3DXVec3Dot(&z, pEye);
    pOut->_44 = 1.0f;
    return pOut;
}

// The game's import thunk for D3DXVec3TransformNormal (staging/blk_00ee0000_o2.cpp, FUN_00ee6df2) jumps through
// this pointer, the image's IAT slot under its decomp name.
void (*g_D3DXVec3TransformNormal)(void) = (void (*)(void))D3DXVec3TransformNormal;

} // extern "C"
