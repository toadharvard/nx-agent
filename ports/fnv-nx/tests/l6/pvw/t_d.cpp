// part d: the row of Wine's own d3d9 test test_sysmem_draw (dlls/d3d9/tests/visual.c, wine-9.0) that is not todo_wine / broken: lighting off, FVF XYZ|DIFFUSE source in a SYSTEMMEM buffer, destination SYSTEMMEM buffer created
// with D3DFVF_XYZRHW, ProcessVertices(0, 0, 4, dst, NULL, 0) == D3D_OK, every destination vertex = (x * 320 + 320, -y * 240 + 240, 0, 1) within 1 ulp (compare_vec4(..., 1)). Run on both device classes. Inputs are the test's.
#include "t_common.h"
#include <math.h>
static bool near_ulp(float a, float b, int ulps) { int32_t x, y; memcpy(&x, &a, 4); memcpy(&y, &b, 4); if (x < 0) x = (int32_t)0x80000000 - x; if (y < 0) y = (int32_t)0x80000000 - y; int64_t d = (int64_t)x - y; return d >= -ulps && d <= ulps; }
void t_d() {
    t_part('d');
    struct V { float x, y, z; uint32_t d; };
    static const V quad[4] = {{-0.5f, -0.5f, 0.0f, 0xffff0000}, {-0.5f, 0.5f, 0.0f, 0xff00ff00}, {0.5f, -0.5f, 0.0f, 0xff0000ff}, {0.5f, 0.5f, 0.0f, 0xffffffff}};
    for (int ex = 0; ex < 2; ex++) {
        IDirect3D9* d3d = t_d3d(ex); D3DPRESENT_PARAMETERS pp = t_pp(); IDirect3DDevice9* dev = t_dev(d3d, &pp); if (!CHECK(dev != nullptr)) continue;
        CHECK_HR(dev->SetRenderState(D3DRS_LIGHTING, FALSE), D3D_OK);
        IDirect3DVertexBuffer9 *vb = nullptr, *dst = nullptr; void* p;
        CHECK_HR(dev->CreateVertexBuffer(sizeof quad, 0, 0, D3DPOOL_SYSTEMMEM, &vb, nullptr), D3D_OK); CHECK_HR(vb->Lock(0, sizeof quad, &p, 0), D3D_OK); memcpy(p, quad, sizeof quad); CHECK_HR(vb->Unlock(), D3D_OK);
        CHECK_HR(dev->SetFVF(D3DFVF_XYZ | D3DFVF_DIFFUSE), D3D_OK); CHECK_HR(dev->SetStreamSource(0, vb, 0, sizeof(V)), D3D_OK);
        CHECK_HR(dev->CreateVertexBuffer(4 * 16, 0, D3DFVF_XYZRHW, D3DPOOL_SYSTEMMEM, &dst, nullptr), D3D_OK);
        CHECK_HR(dev->ProcessVertices(0, 0, 4, dst, nullptr, 0), D3D_OK);
        CHECK_HR(dst->Lock(0, 0, &p, 0), D3D_OK); const float* o = (const float*)p;
        for (int i = 0; i < 4; i++) {
            CHECK(near_ulp(o[4 * i + 0], quad[i].x * 320.0f + 320.0f, 1)); CHECK(near_ulp(o[4 * i + 1], -quad[i].y * 240.0f + 240.0f, 1)); CHECK(near_ulp(o[4 * i + 2], 0.0f, 1)); CHECK(near_ulp(o[4 * i + 3], 1.0f, 1));
        }
        CHECK_HR(dst->Unlock(), D3D_OK); dst->Release(); vb->Release(); dev->Release(); d3d->Release();
    }
}
