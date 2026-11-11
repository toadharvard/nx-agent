// part c: storage conversions of the gap formats both ways (upload through UpdateSurface, read-back through GetRenderTargetData). Exhaustive over the texel value space where it is <= 2^16, structured above.
// Expected read-back = the written bits for every format, also the most negative code of the biased formats (L6V5U5 / X8L8V8U8: stored as exact floats plus a side word, see front_int.h) and the x byte of X8L8V8U8.
#include "t_common.h"
#include <initializer_list>
typedef uint32_t (*Xf)(uint32_t);
static uint32_t id_(uint32_t v) { return v; }
static uint32_t a8l8(uint32_t v) { return v & 0xFFFF; }
static void sweep(TDev& d, uint32_t fmt, int bytes, bool rt, const uint32_t* texels, uint32_t n, uint32_t w, uint32_t h, Xf want, const char* name) {
    IDirect3DSurface9* src = t_surf(d, 2, w, h, fmt, D3DPOOL_SYSTEMMEM); IDirect3DSurface9* gpu = rt ? t_surf(d, 0, w, h, fmt, D3DPOOL_DEFAULT, 0, false) : t_surf(d, 2, w, h, fmt, D3DPOOL_DEFAULT);
    IDirect3DSurface9* back = t_surf(d, 2, w, h, fmt, D3DPOOL_SYSTEMMEM); if (!CHECK(src && gpu && back)) return;
    uint8_t* in = (uint8_t*)malloc((size_t)w * h * bytes); uint8_t* out = (uint8_t*)malloc((size_t)w * h * bytes); memset(in, 0, (size_t)w * h * bytes);
    for (uint32_t i = 0; i < n; i++) memcpy(in + (size_t)i * bytes, &texels[i], bytes);
    t_put(src, in, w * h * bytes); CHECK_HR(d.i9->UpdateSurface(src, nullptr, gpu, nullptr), D3D_OK); CHECK_HR(d.i9->GetRenderTargetData(gpu, back), D3D_OK); CHECK(t_get(back, out, w * h * bytes));
    bool ok = true; uint32_t bad = 0;
    for (uint32_t i = 0; i < n && bad < 3; i++) { uint32_t g = 0, e = want(texels[i]); memcpy(&g, out + (size_t)i * bytes, bytes); if (bytes < 4) e &= (1u << (8 * bytes)) - 1; if (g != e) { ok = false; bad++; printf("  %s texel %08x: read back %08x want %08x\n", name, texels[i], g, e); } }
    CHECK(ok); CHECK_EQ(n, (uint64_t)n); free(in); free(out); for (auto* s : {src, gpu, back}) s->Release();
}
static uint32_t* all(uint32_t n) { uint32_t* t = (uint32_t*)malloc(n * 4); for (uint32_t i = 0; i < n; i++) t[i] = i; return t; }
static uint32_t* structured(uint32_t* n, int bytes) {   // boundary values of every byte combined + pseudo random texels
    static const uint8_t B[] = {0, 1, 0x0F, 0x10, 0x7E, 0x7F, 0x80, 0x81, 0xFE, 0xFF}; uint32_t cap = 10000 + 4096, k = 0; uint32_t* t = (uint32_t*)malloc(cap * 4); uint32_t seed = 99;
    for (int a = 0; a < 10; a++) for (int b = 0; b < 10; b++) for (int c = 0; c < 10; c++) t[k++] = B[a] | B[b] << 8 | B[c] << 16 | (bytes > 3 ? (uint32_t)B[(a + b + c) % 10] << 24 : 0);
    for (int i = 0; i < 4096 + 100; i++) t[k++] = (t_lcg(&seed) << 8 ^ t_lcg(&seed)) & (bytes == 4 ? ~0u : 0xFFFFFFu); *n = 4096; return t;   // the first 4096 are checked (surface 64 x 64)
}
void t_c() {
    t_part('c'); TDev d = t_dev(false, 64, 64); uint32_t* t16 = all(65536), *t8 = all(256), n4; uint32_t* t4 = structured(&n4, 4);
    sweep(d, 60, 2, true, t16, 65536, 256, 256, id_, "V8U8");                 // RG8S: every one of the 2^16 texels
    sweep(d, 101, 2, true, t16, 65536, 256, 256, id_, "INDEX16");              // R16: bits are the texel
    sweep(d, 61, 2, false, t16, 65536, 256, 256, id_, "L6V5U5");            // biased integers: every one of the 2^16 texels
    sweep(d, 28, 1, true, t8, 256, 16, 16, id_, "A8"); sweep(d, 50, 1, true, t8, 256, 16, 16, id_, "L8");   // expanded RGBA8: alpha / red read back
    sweep(d, 51, 2, false, t16, 65536, 256, 256, a8l8, "A8L8");                // A8L8 (CONVERT, read through the unconvert)
    sweep(d, 20, 3, true, t4, 4096, 64, 64, [](uint32_t v) { return v & 0xFFFFFF; }, "R8G8B8");
    sweep(d, 62, 4, false, t4, 4096, 64, 64, id_, "X8L8V8U8");
    sweep(d, 63, 4, true, t4, 4096, 64, 64, id_, "Q8W8V8U8"); sweep(d, 64, 4, true, t4, 4096, 64, 64, id_, "V16U16"); sweep(d, 35, 4, true, t4, 4096, 64, 64, id_, "A2R10G10B10");
    sweep(d, 102, 4, true, t4, 4096, 64, 64, id_, "INDEX32");                  // R32F bits: includes NaN / denormal bit patterns (storage only)
    free(t16); free(t8); free(t4);
}
