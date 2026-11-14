/* fnv-nx shim: <d3dx9math.h> - D3DX math types and the math functions the engine imports (stream S6-render).
 *
 * Types have the SDK layouts (D3DXMATRIX is a D3DMATRIX, 64 bytes; vectors are packed floats). The C++ helpers
 * (constructors, operators) and the SDK's inline functions are implemented here; the exported functions
 * (D3DXMatrixMultiply, ...) are implemented by render/ (NEON). Only what the engine or the backend needs is declared.
 */
#ifndef FNVNX_SHIM_D3DX9MATH_H
#define FNVNX_SHIM_D3DX9MATH_H
#include <math.h>
#include "d3d9.h"

#define D3DX_PI ((FLOAT)3.141592654f)
#define D3DX_1BYPI ((FLOAT)0.318309886f)
#define D3DXToRadian(degree) ((degree) * (D3DX_PI / 180.0f))
#define D3DXToDegree(radian) ((radian) * (180.0f / D3DX_PI))

struct D3DXVECTOR2
{
    FLOAT x, y;
    D3DXVECTOR2() {}
    D3DXVECTOR2(const FLOAT *f) : x(f[0]), y(f[1]) {}
    D3DXVECTOR2(FLOAT fx, FLOAT fy) : x(fx), y(fy) {}
    operator FLOAT *() { return &x; }
    operator const FLOAT *() const { return &x; }
    D3DXVECTOR2 &operator+=(const D3DXVECTOR2 &v) { x += v.x; y += v.y; return *this; }
    D3DXVECTOR2 &operator-=(const D3DXVECTOR2 &v) { x -= v.x; y -= v.y; return *this; }
    D3DXVECTOR2 &operator*=(FLOAT f) { x *= f; y *= f; return *this; }
    D3DXVECTOR2 &operator/=(FLOAT f) { return *this *= 1.0f / f; }
    D3DXVECTOR2 operator+() const { return *this; }
    D3DXVECTOR2 operator-() const { return D3DXVECTOR2(-x, -y); }
    D3DXVECTOR2 operator+(const D3DXVECTOR2 &v) const { return D3DXVECTOR2(x + v.x, y + v.y); }
    D3DXVECTOR2 operator-(const D3DXVECTOR2 &v) const { return D3DXVECTOR2(x - v.x, y - v.y); }
    D3DXVECTOR2 operator*(FLOAT f) const { return D3DXVECTOR2(x * f, y * f); }
    D3DXVECTOR2 operator/(FLOAT f) const { return *this * (1.0f / f); }
    bool operator==(const D3DXVECTOR2 &v) const { return x == v.x && y == v.y; }
    bool operator!=(const D3DXVECTOR2 &v) const { return !(*this == v); }
};

struct D3DXVECTOR3 : public D3DVECTOR
{
    D3DXVECTOR3() {}
    D3DXVECTOR3(const FLOAT *f) { x = f[0]; y = f[1]; z = f[2]; }
    D3DXVECTOR3(const D3DVECTOR &v) { x = v.x; y = v.y; z = v.z; }
    D3DXVECTOR3(FLOAT fx, FLOAT fy, FLOAT fz) { x = fx; y = fy; z = fz; }
    operator FLOAT *() { return &x; }
    operator const FLOAT *() const { return &x; }
    D3DXVECTOR3 &operator+=(const D3DXVECTOR3 &v) { x += v.x; y += v.y; z += v.z; return *this; }
    D3DXVECTOR3 &operator-=(const D3DXVECTOR3 &v) { x -= v.x; y -= v.y; z -= v.z; return *this; }
    D3DXVECTOR3 &operator*=(FLOAT f) { x *= f; y *= f; z *= f; return *this; }
    D3DXVECTOR3 &operator/=(FLOAT f) { return *this *= 1.0f / f; }
    D3DXVECTOR3 operator+() const { return *this; }
    D3DXVECTOR3 operator-() const { return D3DXVECTOR3(-x, -y, -z); }
    D3DXVECTOR3 operator+(const D3DXVECTOR3 &v) const { return D3DXVECTOR3(x + v.x, y + v.y, z + v.z); }
    D3DXVECTOR3 operator-(const D3DXVECTOR3 &v) const { return D3DXVECTOR3(x - v.x, y - v.y, z - v.z); }
    D3DXVECTOR3 operator*(FLOAT f) const { return D3DXVECTOR3(x * f, y * f, z * f); }
    D3DXVECTOR3 operator/(FLOAT f) const { return *this * (1.0f / f); }
    bool operator==(const D3DXVECTOR3 &v) const { return x == v.x && y == v.y && z == v.z; }
    bool operator!=(const D3DXVECTOR3 &v) const { return !(*this == v); }
};

struct D3DXVECTOR4
{
    FLOAT x, y, z, w;
    D3DXVECTOR4() {}
    D3DXVECTOR4(const FLOAT *f) : x(f[0]), y(f[1]), z(f[2]), w(f[3]) {}
    D3DXVECTOR4(const D3DVECTOR &v, FLOAT fw) : x(v.x), y(v.y), z(v.z), w(fw) {}
    D3DXVECTOR4(FLOAT fx, FLOAT fy, FLOAT fz, FLOAT fw) : x(fx), y(fy), z(fz), w(fw) {}
    operator FLOAT *() { return &x; }
    operator const FLOAT *() const { return &x; }
    D3DXVECTOR4 &operator+=(const D3DXVECTOR4 &v) { x += v.x; y += v.y; z += v.z; w += v.w; return *this; }
    D3DXVECTOR4 &operator-=(const D3DXVECTOR4 &v) { x -= v.x; y -= v.y; z -= v.z; w -= v.w; return *this; }
    D3DXVECTOR4 &operator*=(FLOAT f) { x *= f; y *= f; z *= f; w *= f; return *this; }
    D3DXVECTOR4 &operator/=(FLOAT f) { return *this *= 1.0f / f; }
    D3DXVECTOR4 operator+() const { return *this; }
    D3DXVECTOR4 operator-() const { return D3DXVECTOR4(-x, -y, -z, -w); }
    D3DXVECTOR4 operator+(const D3DXVECTOR4 &v) const { return D3DXVECTOR4(x + v.x, y + v.y, z + v.z, w + v.w); }
    D3DXVECTOR4 operator-(const D3DXVECTOR4 &v) const { return D3DXVECTOR4(x - v.x, y - v.y, z - v.z, w - v.w); }
    D3DXVECTOR4 operator*(FLOAT f) const { return D3DXVECTOR4(x * f, y * f, z * f, w * f); }
    D3DXVECTOR4 operator/(FLOAT f) const { return *this * (1.0f / f); }
    bool operator==(const D3DXVECTOR4 &v) const { return x == v.x && y == v.y && z == v.z && w == v.w; }
    bool operator!=(const D3DXVECTOR4 &v) const { return !(*this == v); }
};

struct D3DXMATRIX : public D3DMATRIX
{
    D3DXMATRIX() {}
    D3DXMATRIX(const FLOAT *f) { for (int i = 0; i < 16; i++) (&_11)[i] = f[i]; }
    D3DXMATRIX(const D3DMATRIX &m) : D3DMATRIX(m) {}
    D3DXMATRIX(FLOAT f11, FLOAT f12, FLOAT f13, FLOAT f14, FLOAT f21, FLOAT f22, FLOAT f23, FLOAT f24,
               FLOAT f31, FLOAT f32, FLOAT f33, FLOAT f34, FLOAT f41, FLOAT f42, FLOAT f43, FLOAT f44)
    {
        _11 = f11; _12 = f12; _13 = f13; _14 = f14; _21 = f21; _22 = f22; _23 = f23; _24 = f24;
        _31 = f31; _32 = f32; _33 = f33; _34 = f34; _41 = f41; _42 = f42; _43 = f43; _44 = f44;
    }
    FLOAT &operator()(UINT row, UINT col) { return m[row][col]; }
    FLOAT operator()(UINT row, UINT col) const { return m[row][col]; }
    operator FLOAT *() { return &_11; }
    operator const FLOAT *() const { return &_11; }
    inline D3DXMATRIX &operator*=(const D3DXMATRIX &o);
    D3DXMATRIX &operator+=(const D3DXMATRIX &o) { for (int i = 0; i < 16; i++) (&_11)[i] += (&o._11)[i]; return *this; }
    D3DXMATRIX &operator-=(const D3DXMATRIX &o) { for (int i = 0; i < 16; i++) (&_11)[i] -= (&o._11)[i]; return *this; }
    D3DXMATRIX &operator*=(FLOAT f) { for (int i = 0; i < 16; i++) (&_11)[i] *= f; return *this; }
    D3DXMATRIX &operator/=(FLOAT f) { return *this *= 1.0f / f; }
    D3DXMATRIX operator+() const { return *this; }
    D3DXMATRIX operator-() const { D3DXMATRIX r(*this); return r *= -1.0f; }
    inline D3DXMATRIX operator*(const D3DXMATRIX &o) const;
    D3DXMATRIX operator+(const D3DXMATRIX &o) const { D3DXMATRIX r(*this); return r += o; }
    D3DXMATRIX operator-(const D3DXMATRIX &o) const { D3DXMATRIX r(*this); return r -= o; }
    D3DXMATRIX operator*(FLOAT f) const { D3DXMATRIX r(*this); return r *= f; }
    D3DXMATRIX operator/(FLOAT f) const { D3DXMATRIX r(*this); return r /= f; }
    bool operator==(const D3DXMATRIX &o) const { for (int i = 0; i < 16; i++) if ((&_11)[i] != (&o._11)[i]) return false; return true; }
    bool operator!=(const D3DXMATRIX &o) const { return !(*this == o); }
};
typedef D3DXMATRIX *LPD3DXMATRIX;
typedef D3DXMATRIX D3DXMATRIXA16 __attribute__((aligned(16)));
typedef D3DXMATRIXA16 *LPD3DXMATRIXA16;

struct D3DXQUATERNION
{
    FLOAT x, y, z, w;
    D3DXQUATERNION() {}
    D3DXQUATERNION(const FLOAT *f) : x(f[0]), y(f[1]), z(f[2]), w(f[3]) {}
    D3DXQUATERNION(FLOAT fx, FLOAT fy, FLOAT fz, FLOAT fw) : x(fx), y(fy), z(fz), w(fw) {}
    operator FLOAT *() { return &x; }
    operator const FLOAT *() const { return &x; }
};

struct D3DXPLANE
{
    FLOAT a, b, c, d;
    D3DXPLANE() {}
    D3DXPLANE(const FLOAT *f) : a(f[0]), b(f[1]), c(f[2]), d(f[3]) {}
    D3DXPLANE(FLOAT fa, FLOAT fb, FLOAT fc, FLOAT fd) : a(fa), b(fb), c(fc), d(fd) {}
    operator FLOAT *() { return &a; }
    operator const FLOAT *() const { return &a; }
    bool operator==(const D3DXPLANE &p) const { return a == p.a && b == p.b && c == p.c && d == p.d; }
    bool operator!=(const D3DXPLANE &p) const { return !(*this == p); }
};

struct D3DXCOLOR
{
    FLOAT r, g, b, a;
    D3DXCOLOR() {}
    D3DXCOLOR(DWORD argb)
        : r(((argb >> 16) & 0xff) / 255.0f), g(((argb >> 8) & 0xff) / 255.0f), b((argb & 0xff) / 255.0f),
          a((argb >> 24) / 255.0f) {}
    D3DXCOLOR(const FLOAT *f) : r(f[0]), g(f[1]), b(f[2]), a(f[3]) {}
    D3DXCOLOR(const D3DCOLORVALUE &c) : r(c.r), g(c.g), b(c.b), a(c.a) {}
    D3DXCOLOR(FLOAT fr, FLOAT fg, FLOAT fb, FLOAT fa) : r(fr), g(fg), b(fb), a(fa) {}
    operator FLOAT *() { return &r; }
    operator const FLOAT *() const { return &r; }
};

FNVNX_D3D_SIZE(D3DXVECTOR2, 8);
FNVNX_D3D_SIZE(D3DXVECTOR3, 12);
FNVNX_D3D_SIZE(D3DXVECTOR4, 16);
FNVNX_D3D_SIZE(D3DXMATRIX, 64);
FNVNX_D3D_SIZE(D3DXPLANE, 16);
FNVNX_D3D_SIZE(D3DXQUATERNION, 16);
FNVNX_D3D_SIZE(D3DXCOLOR, 16);

extern "C" {
/* imported by FalloutNV.exe */
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixMultiply(D3DXMATRIX *pOut, const D3DXMATRIX *pM1, const D3DXMATRIX *pM2);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixMultiplyTranspose(D3DXMATRIX *pOut, const D3DXMATRIX *pM1, const D3DXMATRIX *pM2);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixTranspose(D3DXMATRIX *pOut, const D3DXMATRIX *pM);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixRotationYawPitchRoll(D3DXMATRIX *pOut, FLOAT Yaw, FLOAT Pitch, FLOAT Roll);
FNVNX_D3D_TRACED D3DXPLANE *WINAPI D3DXPlaneNormalize(D3DXPLANE *pOut, const D3DXPLANE *pP);
FNVNX_D3D_TRACED D3DXPLANE *WINAPI D3DXPlaneTransform(D3DXPLANE *pOut, const D3DXPLANE *pP, const D3DXMATRIX *pM);
FNVNX_D3D_TRACED D3DXVECTOR3 *WINAPI D3DXVec3Normalize(D3DXVECTOR3 *pOut, const D3DXVECTOR3 *pV);
FNVNX_D3D_TRACED D3DXVECTOR3 *WINAPI D3DXVec3TransformCoord(D3DXVECTOR3 *pOut, const D3DXVECTOR3 *pV, const D3DXMATRIX *pM);
FNVNX_D3D_TRACED D3DXVECTOR4 *WINAPI D3DXVec4Transform(D3DXVECTOR4 *pOut, const D3DXVECTOR4 *pV, const D3DXMATRIX *pM);
/* named in the decomp, not imported (inlined or reimplemented there); declared for the backend */
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixInverse(D3DXMATRIX *pOut, FLOAT *pDeterminant, const D3DXMATRIX *pM);
FNVNX_D3D_TRACED D3DXVECTOR3 *WINAPI D3DXVec3TransformNormal(D3DXVECTOR3 *pOut, const D3DXVECTOR3 *pV, const D3DXMATRIX *pM);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixPerspectiveFovLH(D3DXMATRIX *pOut, FLOAT fovy, FLOAT Aspect, FLOAT zn, FLOAT zf);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixOrthoOffCenterLH(D3DXMATRIX *pOut, FLOAT l, FLOAT r, FLOAT b, FLOAT t, FLOAT zn, FLOAT zf);
FNVNX_D3D_TRACED D3DXMATRIX *WINAPI D3DXMatrixLookAtLH(D3DXMATRIX *pOut, const D3DXVECTOR3 *pEye, const D3DXVECTOR3 *pAt, const D3DXVECTOR3 *pUp);
}

/* SDK inline functions (d3dx9math.inl) */
static inline D3DXMATRIX *D3DXMatrixIdentity(D3DXMATRIX *pOut)
{
    for (int i = 0; i < 16; i++) (&pOut->_11)[i] = (i % 5 == 0) ? 1.0f : 0.0f;
    return pOut;
}
static inline BOOL D3DXMatrixIsIdentity(const D3DXMATRIX *pM)
{
    for (int i = 0; i < 16; i++) if ((&pM->_11)[i] != ((i % 5 == 0) ? 1.0f : 0.0f)) return FALSE;
    return TRUE;
}
static inline FLOAT D3DXVec3Dot(const D3DXVECTOR3 *a, const D3DXVECTOR3 *b) { return a->x * b->x + a->y * b->y + a->z * b->z; }
static inline FLOAT D3DXVec3Length(const D3DXVECTOR3 *v) { return sqrtf(D3DXVec3Dot(v, v)); }
static inline FLOAT D3DXVec3LengthSq(const D3DXVECTOR3 *v) { return D3DXVec3Dot(v, v); }
static inline D3DXVECTOR3 *D3DXVec3Cross(D3DXVECTOR3 *pOut, const D3DXVECTOR3 *a, const D3DXVECTOR3 *b)
{
    D3DXVECTOR3 r(a->y * b->z - a->z * b->y, a->z * b->x - a->x * b->z, a->x * b->y - a->y * b->x);
    *pOut = r;
    return pOut;
}
static inline FLOAT D3DXVec4Dot(const D3DXVECTOR4 *a, const D3DXVECTOR4 *b) { return a->x * b->x + a->y * b->y + a->z * b->z + a->w * b->w; }
static inline FLOAT D3DXPlaneDotCoord(const D3DXPLANE *p, const D3DXVECTOR3 *v) { return p->a * v->x + p->b * v->y + p->c * v->z + p->d; }
static inline FLOAT D3DXPlaneDotNormal(const D3DXPLANE *p, const D3DXVECTOR3 *v) { return p->a * v->x + p->b * v->y + p->c * v->z; }

inline D3DXMATRIX D3DXMATRIX::operator*(const D3DXMATRIX &o) const
{
    D3DXMATRIX r;
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++)
            r.m[i][j] = m[i][0] * o.m[0][j] + m[i][1] * o.m[1][j] + m[i][2] * o.m[2][j] + m[i][3] * o.m[3][j];
    return r;
}
inline D3DXMATRIX &D3DXMATRIX::operator*=(const D3DXMATRIX &o) { return *this = *this * o; }

#endif
