/* nxd9play.c - S16-compare's reference renderer: an NXD9 capture (docs/RENDER_NATIVE.md 8.3) replayed through real
 * D3D9. Built as a 32-bit Windows program (build.sh, mingw-w64) and run under Wine, where D3D9 is WineD3D on Mesa
 * llvmpipe: the reference side of every frame comparison. Its outputs are named like nx_render's, so the same
 * comparison reads both:
 *
 *   nxd9play.exe <file.nxd9> -o <dir> [--per-draw] [--until-draw N] [--frame F] [--frames A-B]
 *
 *   --draw-stats <tsv> writes, for each draw of the frames --frame selects (all without it): frame, draw, render
 *   target 0's width, height and D3DFORMAT, and the number of its pixels the draw changed (-1 when the target cannot
 *   be read back): the reference's coverage, for the GPU model. <dir>/frames.tsv gives each frame's execution time
 *   in ms (QueryPerformanceCounter, Present to Present; a record of the reference only, never a metric; with
 *   --per-draw or --draw-stats it includes their read-backs).
 *   --frames A-B writes f<frame>.png only for frames A..B (A- to the end, A alone: that frame); every frame is still
 *   executed. --frame F only restricts --per-draw and --until-draw: it does not limit the PNGs (a long journal
 *   writes one PNG per frame without --frames).
 *
 *   <dir>/f<frame %05u>.png          the back buffer at each PRES
 *   <dir>/f<frame>_d<draw %05u>.png  --per-draw: render target 0 after each draw (every draw counts: nothing is skipped)
 *   <dir>/summary.txt                one counters line (also on stdout)
 *
 * Exit codes as nx_render: 0 ok, 1 runtime failure (malformed input, a D3D9 call failed), 2 usage or file I/O.
 *
 * Besides the records of 8.3 it executes S16's extension records (tools/compare/README.md, "NXD9 extensions"),
 * which nx_render skips as unknown tags: STRC (StretchRect), CFIL (ColorFill), TEXC (a cube face level), SFRQ
 * (SetStreamSourceFreq), and the extra SURF fields level and face. Streams, render targets and samplers beyond what
 * nx_render executes (stream 1+, RT 1-3, vertex samplers 257-260) are executed here as D3D9 does.
 */
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TAG(a, b, c, d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)

/* ---- PNG (stored deflate blocks: no compression library needed) -------------------------------------------- */
static uint32_t crc_tab[256];
static void crc_init(void) {
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_tab[n] = c;
    }
}
static uint32_t crc_upd(uint32_t c, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) c = crc_tab[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c;
}
static void be32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static void chunk(FILE *f, const char *type, const uint8_t *d, uint32_t n) {
    uint8_t h[8]; be32(h, n); memcpy(h + 4, type, 4);
    fwrite(h, 1, 8, f);
    if (n) fwrite(d, 1, n, f);
    uint32_t c = crc_upd(0xFFFFFFFFu, (const uint8_t *)type, 4);
    c = crc_upd(c, d, n) ^ 0xFFFFFFFFu;
    be32(h, c); fwrite(h, 1, 4, f);
}
/* rgba: w*h*4 bytes, row 0 at the top */
static int write_png(const char *path, const uint8_t *rgba, uint32_t w, uint32_t h) {
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    static const uint8_t sig[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    fwrite(sig, 1, 8, f);
    uint8_t ihdr[13]; be32(ihdr, w); be32(ihdr + 4, h);
    ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = ihdr[11] = ihdr[12] = 0;
    chunk(f, "IHDR", ihdr, 13);
    size_t raw = (size_t)h * (1 + 4 * (size_t)w), nblk = (raw + 65534) / 65535;
    size_t zn = 2 + raw + 5 * (nblk ? nblk : 1) + 4;
    uint8_t *z = malloc(zn), *q = z;
    if (!z) { fclose(f); return -1; }
    *q++ = 0x78; *q++ = 0x01;
    uint32_t a = 1, b = 0;
    size_t left = raw, row = 0, col = 0;   /* col: byte within the current filtered row */
    do {
        uint32_t n = left > 65535 ? 65535 : (uint32_t)left;
        *q++ = left <= 65535; *q++ = n & 0xFF; *q++ = n >> 8; *q++ = ~n & 0xFF; *q++ = (~n >> 8) & 0xFF;
        for (uint32_t i = 0; i < n; i++) {
            uint8_t v = col == 0 ? 0 : rgba[row * 4 * (size_t)w + col - 1];
            *q++ = v; a = (a + v) % 65521; b = (b + a) % 65521;
            if (++col == 1 + 4 * (size_t)w) { col = 0; row++; }
        }
        left -= n;
    } while (left);
    be32(q, b << 16 | a); q += 4;
    chunk(f, "IDAT", z, (uint32_t)(q - z));
    chunk(f, "IEND", NULL, 0);
    free(z);
    return fclose(f);
}

/* ---- objects by (kind, id) --------------------------------------------------------------------------------- */
enum { K_TEX = 1, K_SURF, K_VB, K_IB, K_DECL, K_VS, K_PS };
typedef struct { uint64_t key; IUnknown *p; uint32_t fmt, w, h, levels, flags; } Obj;
static Obj *objs; static size_t ocap, ocount;
static uint64_t okey(uint32_t kind, uint32_t id) { return (uint64_t)kind << 32 | id; }
static size_t oslot(uint64_t key) {
    size_t i = (size_t)((key * 0x9E3779B97F4A7C15ull) >> 40) & (ocap - 1);
    while (objs[i].key && objs[i].key != key) i = (i + 1) & (ocap - 1);
    return i;
}
static Obj *oget(uint32_t kind, uint32_t id) {
    if (!id || !ocap) return NULL;
    Obj *o = &objs[oslot(okey(kind, id))];
    return o->key ? o : NULL;
}
static Obj *oput(uint32_t kind, uint32_t id) {
    if (2 * (ocount + 1) > ocap) {
        size_t oc = ocap; Obj *old = objs;
        ocap = ocap ? 2 * ocap : 1024;
        objs = calloc(ocap, sizeof *objs);
        for (size_t i = 0; i < oc; i++) if (old[i].key) objs[oslot(old[i].key)] = old[i];
        free(old);
    }
    Obj *o = &objs[oslot(okey(kind, id))];
    if (o->key) { if (o->p) IUnknown_Release(o->p); }
    else ocount++;
    memset(o, 0, sizeof *o);
    o->key = okey(kind, id);
    return o;
}

/* ---- state ------------------------------------------------------------------------------------------------- */
static IDirect3D9 *d3d; static IDirect3DDevice9 *dev; static HWND hwnd;
static IDirect3DSurface9 *backbuf, *autods;
static uint32_t W, H, frame_no, draw_no, in_scene;
static const char *outdir; static int per_draw; static long until_draw = -1, only_frame = -1;
static long png_first = 0, png_last = -1;
static FILE *draw_stats;            /* --draw-stats <tsv>: pixels each draw changes on render target 0 (frames per --frame) */
static FILE *frame_times;           /* <dir>/frames.tsv: frame, ms (execution of the frame's records, PNG writes excluded) */
static LARGE_INTEGER qpf, t_frame0; static double png_ms;   /* --frames A-B: write f<frame>.png only for A..B (B -1: to the end) */
static unsigned long n_draws, n_failed_calls, n_records, n_unknown, n_ext;
static char first_err[256];

static void note(HRESULT hr, const char *what) {
    if (SUCCEEDED(hr)) return;
    n_failed_calls++;
    if (!first_err[0]) snprintf(first_err, sizeof first_err, "%s hr 0x%08lx", what, (unsigned long)hr);
}
static int fail(const char *m) { fprintf(stderr, "nxd9play: %s (frame %u)\n", m, frame_no); return 0; }

static int is_bc(uint32_t f) {
    return f == MAKEFOURCC('D', 'X', 'T', '1') || f == MAKEFOURCC('D', 'X', 'T', '2') || f == MAKEFOURCC('D', 'X', 'T', '3') ||
           f == MAKEFOURCC('D', 'X', 'T', '4') || f == MAKEFOURCC('D', 'X', 'T', '5') || f == MAKEFOURCC('A', 'T', 'I', '1') ||
           f == MAKEFOURCC('A', 'T', 'I', '2');
}
static int is_depth(uint32_t f) {
    return (f >= 70 && f <= 83 && f != 81) || f == MAKEFOURCC('I', 'N', 'T', 'Z') || f == MAKEFOURCC('D', 'F', '2', '4') ||
           f == MAKEFOURCC('D', 'F', '1', '6') || f == MAKEFOURCC('R', 'A', 'W', 'Z');
}

/* copy tightly packed rows into a locked level: rows = block rows for BCn; the row size follows from the bytes */
static void copy_rows(const D3DLOCKED_RECT *lr, uint32_t fmt, uint32_t lh, const uint8_t *src, uint32_t bytes) {
    uint32_t rows = is_bc(fmt) ? (lh + 3) / 4 : lh;
    if (!rows) rows = 1;
    uint32_t rb = bytes / rows, n = rb < (uint32_t)lr->Pitch ? rb : (uint32_t)lr->Pitch;
    for (uint32_t r = 0; r < rows; r++) memcpy((uint8_t *)lr->pBits + (size_t)r * lr->Pitch, src + (size_t)r * rb, n);
}
static void upload(Obj *t, uint32_t face, uint32_t level, const uint8_t *src, uint32_t bytes) {
    D3DLOCKED_RECT lr; HRESULT hr;
    uint32_t lh = t->h >> level; if (!lh) lh = 1;
    if (!bytes || level >= t->levels) return;
    if (t->flags & 2) {
        IDirect3DCubeTexture9 *c = (IDirect3DCubeTexture9 *)t->p;
        hr = IDirect3DCubeTexture9_LockRect(c, (D3DCUBEMAP_FACES)face, level, &lr, NULL, 0); note(hr, "cube LockRect");
        if (SUCCEEDED(hr)) { copy_rows(&lr, t->fmt, lh, src, bytes); IDirect3DCubeTexture9_UnlockRect(c, (D3DCUBEMAP_FACES)face, level); }
    } else {
        IDirect3DTexture9 *x = (IDirect3DTexture9 *)t->p;
        hr = IDirect3DTexture9_LockRect(x, level, &lr, NULL, 0); note(hr, "LockRect");
        if (SUCCEEDED(hr)) { copy_rows(&lr, t->fmt, lh, src, bytes); IDirect3DTexture9_UnlockRect(x, level); }
    }
}

static IDirect3DSurface9 *surf_of(uint32_t id) {   /* SRT/STRC/CFIL: 0 = back buffer */
    if (!id) return backbuf;
    Obj *o = oget(K_SURF, id);
    return o ? (IDirect3DSurface9 *)o->p : NULL;
}

static void scene_on(void) { if (!in_scene) { note(IDirect3DDevice9_BeginScene(dev), "BeginScene"); in_scene = 1; } }
static void scene_off(void) { if (in_scene) { note(IDirect3DDevice9_EndScene(dev), "EndScene"); in_scene = 0; } }

/* read a render-target surface back as RGBA8 and write it as PNG */
static float half_to_float(uint16_t h) {
    uint32_t sgn = (uint32_t)(h >> 15) << 31, e = h >> 10 & 31, m = h & 1023, u;
    if (e == 0) {
        if (m == 0) u = sgn;
        else { e = 113; while (!(m & 1024)) { m <<= 1; e--; } u = sgn | e << 23 | (m & 1023) << 13; }
    } else if (e == 31) u = sgn | 0x7f800000u | m << 13;
    else u = sgn | (e + 112) << 23 | m << 13;
    float f; memcpy(&f, &u, 4); return f;
}
static void dump_surface(IDirect3DSurface9 *s, const char *path) {
    D3DSURFACE_DESC d; IDirect3DSurface9 *sys = NULL; D3DLOCKED_RECT lr;
    if (!s || FAILED(IDirect3DSurface9_GetDesc(s, &d))) return;
    HRESULT hr = IDirect3DDevice9_CreateOffscreenPlainSurface(dev, d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL);
    note(hr, "CreateOffscreenPlainSurface");
    if (FAILED(hr)) return;
    hr = IDirect3DDevice9_GetRenderTargetData(dev, s, sys); note(hr, "GetRenderTargetData");
    if (SUCCEEDED(hr) && SUCCEEDED(IDirect3DSurface9_LockRect(sys, &lr, NULL, D3DLOCK_READONLY))) {
        uint8_t *rgba = malloc((size_t)d.Width * d.Height * 4);
        for (uint32_t y = 0; y < d.Height; y++) {
            const uint8_t *r = (const uint8_t *)lr.pBits + (size_t)y * lr.Pitch;
            uint8_t *o = rgba + (size_t)y * d.Width * 4;
            for (uint32_t x = 0; x < d.Width; x++, o += 4) {
                switch (d.Format) {
                case D3DFMT_A8R8G8B8: case D3DFMT_X8R8G8B8:
                    o[0] = r[4 * x + 2]; o[1] = r[4 * x + 1]; o[2] = r[4 * x]; o[3] = d.Format == D3DFMT_A8R8G8B8 ? r[4 * x + 3] : 255; break;
                case D3DFMT_A8B8G8R8: case D3DFMT_X8B8G8R8:
                    o[0] = r[4 * x]; o[1] = r[4 * x + 1]; o[2] = r[4 * x + 2]; o[3] = d.Format == D3DFMT_A8B8G8R8 ? r[4 * x + 3] : 255; break;
                case D3DFMT_R5G6B5: {
                    uint16_t v = ((const uint16_t *)r)[x];
                    o[0] = (v >> 11 & 31) * 255 / 31; o[1] = (v >> 5 & 63) * 255 / 63; o[2] = (v & 31) * 255 / 31; o[3] = 255; break;
                }
                case D3DFMT_A16B16G16R16F: case D3DFMT_A32B32G32R32F: case D3DFMT_R32F: case D3DFMT_R16F: default: {
                    /* float targets: clamped to [0,1] (nx_render's rule for RGBA32F); others: grey 0 */
                    float c[4] = {0, 0, 0, 1};
                    if (d.Format == D3DFMT_A32B32G32R32F) memcpy(c, r + 16 * x, 16);
                    else if (d.Format == D3DFMT_R32F) c[0] = c[1] = c[2] = ((const float *)r)[x];
                    else if (d.Format == D3DFMT_A16B16G16R16F) { for (int k = 0; k < 4; k++) c[k] = half_to_float(((const uint16_t *)r)[4 * x + k]); }
                    else if (d.Format == D3DFMT_R16F) c[0] = c[1] = c[2] = half_to_float(((const uint16_t *)r)[x]);
                    for (int k = 0; k < 4; k++) { float v = c[k] < 0 ? 0 : c[k] > 1 ? 1 : c[k]; o[k] = (uint8_t)(v * 255.0f + 0.5f); }
                }
                }
            }
        }
        IDirect3DSurface9_UnlockRect(sys);
        if (write_png(path, rgba, d.Width, d.Height)) fprintf(stderr, "nxd9play: cannot write %s\n", path);
        free(rgba);
    }
    IDirect3DSurface9_Release(sys);
}

static LRESULT CALLBACK wndproc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

static int create_device(uint32_t w, uint32_t h, uint32_t bfmt, uint32_t dfmt) {
    if (dev) { fprintf(stderr, "nxd9play: second DEV ignored (%ux%u)\n", w, h); return 1; }
    W = w; H = h;
    WNDCLASSA wc = {0}; wc.lpfnWndProc = wndproc; wc.hInstance = GetModuleHandleA(NULL); wc.lpszClassName = "nxd9play";
    RegisterClassA(&wc);
    RECT r = {0, 0, (LONG)w, (LONG)h};
    AdjustWindowRect(&r, WS_POPUP, FALSE);
    hwnd = CreateWindowExA(0, "nxd9play", "nxd9play", WS_POPUP, 0, 0, r.right - r.left, r.bottom - r.top, NULL, NULL, wc.hInstance, NULL);
    d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!hwnd || !d3d) return fail("no window or no Direct3D9");
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = w; pp.BackBufferHeight = h; pp.BackBufferFormat = bfmt ? (D3DFORMAT)bfmt : D3DFMT_A8R8G8B8;
    pp.BackBufferCount = 1; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = hwnd; pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = dfmt != 0; pp.AutoDepthStencilFormat = (D3DFORMAT)dfmt;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd,
                                         D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &dev);
    if (FAILED(hr)) { fprintf(stderr, "nxd9play: CreateDevice hr 0x%08lx\n", (unsigned long)hr); return 0; }
    IDirect3DDevice9_GetBackBuffer(dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &backbuf);
    if (dfmt) IDirect3DDevice9_GetDepthStencilSurface(dev, &autods);
    return 1;
}

static uint32_t U(const uint8_t *p, size_t i) { uint32_t v; memcpy(&v, p + 4 * i, 4); return v; }
static float F(const uint8_t *p, size_t i) { float v; memcpy(&v, p + 4 * i, 4); return v; }

static uint32_t fmt_bpp(uint32_t f) {
    switch (f) { case D3DFMT_A16B16G16R16F: case D3DFMT_A16B16G16R16: case D3DFMT_G32R32F: return 8;
                 case D3DFMT_A32B32G32R32F: return 16; case D3DFMT_R16F: case D3DFMT_R5G6B5: case D3DFMT_A1R5G5B5:
                 case D3DFMT_X1R5G5B5: case D3DFMT_A4R4G4B4: case D3DFMT_L16: case D3DFMT_A8L8: return 2;
                 case D3DFMT_L8: case D3DFMT_A8: return 1; default: return 4; }
}
/* the bytes of render target 0, rows packed; NULL when it cannot be read back */
static uint8_t *rt_bytes(uint32_t *w, uint32_t *h, uint32_t *fmt) {
    IDirect3DSurface9 *rt = NULL, *sys = NULL; D3DSURFACE_DESC d; D3DLOCKED_RECT lr; uint8_t *out = NULL;
    if (FAILED(IDirect3DDevice9_GetRenderTarget(dev, 0, &rt))) return NULL;
    if (SUCCEEDED(IDirect3DSurface9_GetDesc(rt, &d)) &&
        SUCCEEDED(IDirect3DDevice9_CreateOffscreenPlainSurface(dev, d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL))) {
        if (SUCCEEDED(IDirect3DDevice9_GetRenderTargetData(dev, rt, sys)) &&
            SUCCEEDED(IDirect3DSurface9_LockRect(sys, &lr, NULL, D3DLOCK_READONLY))) {
            uint32_t bpp = fmt_bpp(d.Format), row = d.Width * bpp;
            out = malloc((size_t)row * d.Height);
            if (out) for (uint32_t y = 0; y < d.Height; y++) memcpy(out + (size_t)y * row, (const uint8_t *)lr.pBits + (size_t)y * lr.Pitch, row);
            IDirect3DSurface9_UnlockRect(sys);
            *w = d.Width; *h = d.Height; *fmt = d.Format;
        }
        IDirect3DSurface9_Release(sys);
    }
    IDirect3DSurface9_Release(rt);
    return out;
}
static uint8_t *pre_rt; static uint32_t pre_w, pre_h, pre_fmt;
static int stats_frame(void);
static void before_draw(void) {
    free(pre_rt); pre_rt = NULL;
    if (draw_stats && stats_frame()) { scene_off(); pre_rt = rt_bytes(&pre_w, &pre_h, &pre_fmt); }
}
static void draw_stat(void) {
    if (!draw_stats || !stats_frame()) return;
    uint32_t w = 0, h = 0, f = 0; long changed = -1;
    scene_off();
    uint8_t *post = rt_bytes(&w, &h, &f);
    if (post && pre_rt && w == pre_w && h == pre_h && f == pre_fmt) {
        uint32_t bpp = fmt_bpp(f); changed = 0;
        for (size_t i = 0; i < (size_t)w * h; i++) changed += memcmp(post + i * bpp, pre_rt + i * bpp, bpp) != 0;
    }
    fprintf(draw_stats, "%u\t%u\t%u\t%u\t%u\t%ld\n", frame_no, draw_no, w, h, f, changed);
    free(post); free(pre_rt); pre_rt = NULL;
}
static int frame_selected(void) { return only_frame < 0 || (long)frame_no == only_frame; }

static int draw_allowed(void) {
    return !(until_draw >= 0 && frame_selected() && (long)draw_no > until_draw);
}
static int stats_frame(void) { return only_frame < 0 || (long)frame_no == only_frame; }
static void after_draw(void) {
    draw_stat();
    if (per_draw && frame_selected()) {
        IDirect3DSurface9 *rt = NULL; char p[1024];
        if (SUCCEEDED(IDirect3DDevice9_GetRenderTarget(dev, 0, &rt))) {
            snprintf(p, sizeof p, "%s/f%05u_d%05u.png", outdir, frame_no, draw_no);
            scene_off(); dump_surface(rt, p);
            IDirect3DSurface9_Release(rt);
        }
    }
    draw_no++; n_draws++;
}

static int record(uint32_t tag, const uint8_t *p, uint32_t n) {
    HRESULT hr;
    n_records++;
    if (!dev && tag != TAG('D', 'E', 'V', ' ')) {
        if (tag == TAG('T', 'E', 'X', ' ') || tag == TAG('D', 'R', 'A', 'W') || tag == TAG('D', 'R', 'W', 'I'))
            return fail("record before DEV");
        return 1;
    }
    switch (tag) {
    case TAG('D', 'E', 'V', ' '):
        if (n < 16) return fail("DEV");
        return create_device(U(p, 0), U(p, 1), U(p, 2), U(p, 3));
    case TAG('T', 'E', 'X', ' '): {
        if (n < 24) return fail("TEX");
        uint32_t id = U(p, 0), fmt = U(p, 1), w = U(p, 2), h = U(p, 3), lv = U(p, 4) ? U(p, 4) : 1, fl = U(p, 5);
        if (!w || !h || w > 16384 || h > 16384 || lv > 16) return fail("TEX size");
        Obj *o = oput(K_TEX, id);
        o->fmt = fmt; o->w = w; o->h = h; o->levels = lv; o->flags = fl;
        DWORD usage = (fl & 1) ? (is_depth(fmt) ? D3DUSAGE_DEPTHSTENCIL : D3DUSAGE_RENDERTARGET) : 0;
        D3DPOOL pool = (fl & 1) ? D3DPOOL_DEFAULT : D3DPOOL_MANAGED;
        if (fl & 2) hr = IDirect3DDevice9_CreateCubeTexture(dev, w, lv, usage, (D3DFORMAT)fmt, pool, (IDirect3DCubeTexture9 **)&o->p, NULL);
        else hr = IDirect3DDevice9_CreateTexture(dev, w, h, lv, usage, (D3DFORMAT)fmt, pool, (IDirect3DTexture9 **)&o->p, NULL);
        note(hr, "CreateTexture");
        if (FAILED(hr)) { o->p = NULL; return 1; }
        uint32_t q = 24;
        for (uint32_t l = 0; l < lv && q + 4 <= n; l++) {
            uint32_t b = U(p + q, 0);
            if (q + 4 + (uint64_t)b > n) return fail("TEX level bytes");
            if (b && !(fl & 1)) {
                if (fl & 2) { for (uint32_t f = 0; f < 6; f++) upload(o, f, l, p + q + 4 + (size_t)f * (b / 6), b / 6); }
                else upload(o, 0, l, p + q + 4, b);
            }
            q += 4 + ((b + 3) & ~3u);
        }
        return 1;
    }
    case TAG('T', 'E', 'X', 'U'): case TAG('T', 'E', 'X', 'C'): {
        int cube = tag == TAG('T', 'E', 'X', 'C'); uint32_t hdr = cube ? 16 : 12;
        if (n < hdr) return fail("TEXU");
        Obj *o = oget(K_TEX, U(p, 0));
        uint32_t b = U(p, cube ? 3 : 2);
        if (hdr + (uint64_t)b > n) return fail("TEXU bytes");
        if (o && o->p && !(o->flags & 1)) upload(o, cube ? U(p, 1) : 0, U(p, cube ? 2 : 1), p + hdr, b);
        if (cube) n_ext++;
        return 1;
    }
    case TAG('S', 'U', 'R', 'F'): {
        if (n < 24) return fail("SURF");
        uint32_t id = U(p, 0), kind = U(p, 1), fmt = U(p, 2), w = U(p, 3), h = U(p, 4), tid = U(p, 5);
        uint32_t level = n >= 28 ? U(p, 6) : 0, face = n >= 32 ? U(p, 7) : 0;
        Obj *t = oget(K_TEX, tid);
        IUnknown *s = NULL;
        if (t && t->p) {
            if (t->flags & 2) hr = IDirect3DCubeTexture9_GetCubeMapSurface((IDirect3DCubeTexture9 *)t->p, (D3DCUBEMAP_FACES)face, level, (IDirect3DSurface9 **)&s);
            else hr = IDirect3DTexture9_GetSurfaceLevel((IDirect3DTexture9 *)t->p, level, (IDirect3DSurface9 **)&s);
        } else if (kind == 2) {
            hr = IDirect3DDevice9_CreateDepthStencilSurface(dev, w, h, (D3DFORMAT)fmt, D3DMULTISAMPLE_NONE, 0, FALSE, (IDirect3DSurface9 **)&s, NULL);
        } else {
            hr = IDirect3DDevice9_CreateRenderTarget(dev, w, h, (D3DFORMAT)fmt, D3DMULTISAMPLE_NONE, 0, FALSE, (IDirect3DSurface9 **)&s, NULL);
        }
        note(hr, "SURF");
        Obj *o = oput(K_SURF, id);
        o->p = SUCCEEDED(hr) ? s : NULL; o->fmt = fmt; o->w = w; o->h = h;
        return 1;
    }
    case TAG('V', 'B', ' ', ' '): case TAG('I', 'B', ' ', ' '): {
        int isv = tag == TAG('V', 'B', ' ', ' ');
        if (n < (isv ? 8u : 12u)) return fail("VB/IB");
        uint32_t size = U(p, 1);
        if (!size || size > (256u << 20)) return fail("VB/IB size");
        Obj *o = oput(isv ? K_VB : K_IB, U(p, 0));
        o->w = size;
        if (isv) hr = IDirect3DDevice9_CreateVertexBuffer(dev, size, 0, 0, D3DPOOL_MANAGED, (IDirect3DVertexBuffer9 **)&o->p, NULL);
        else hr = IDirect3DDevice9_CreateIndexBuffer(dev, size, 0, (D3DFORMAT)U(p, 2), D3DPOOL_MANAGED, (IDirect3DIndexBuffer9 **)&o->p, NULL);
        note(hr, isv ? "CreateVertexBuffer" : "CreateIndexBuffer");
        if (FAILED(hr)) o->p = NULL;
        return 1;
    }
    case TAG('V', 'B', 'U', ' '): case TAG('I', 'B', 'U', ' '): {
        int isv = tag == TAG('V', 'B', 'U', ' ');
        if (n < 12) return fail("VBU/IBU");
        Obj *o = oget(isv ? K_VB : K_IB, U(p, 0));
        uint32_t off = U(p, 1), b = U(p, 2); void *dst;
        if (12 + (uint64_t)b > n) return fail("VBU/IBU bytes");
        if (!o || !o->p || !b || (uint64_t)off + b > o->w) return 1;
        hr = isv ? IDirect3DVertexBuffer9_Lock((IDirect3DVertexBuffer9 *)o->p, off, b, &dst, 0)
                 : IDirect3DIndexBuffer9_Lock((IDirect3DIndexBuffer9 *)o->p, off, b, &dst, 0);
        note(hr, "buffer Lock");
        if (SUCCEEDED(hr)) {
            memcpy(dst, p + 12, b);
            if (isv) IDirect3DVertexBuffer9_Unlock((IDirect3DVertexBuffer9 *)o->p);
            else IDirect3DIndexBuffer9_Unlock((IDirect3DIndexBuffer9 *)o->p);
        }
        return 1;
    }
    case TAG('D', 'E', 'C', 'L'): {
        if (n < 8) return fail("DECL");
        uint32_t c = U(p, 1);
        if (c > 64 || 8 + 8 * (uint64_t)c > n) return fail("DECL count");
        D3DVERTEXELEMENT9 e[65];
        memcpy(e, p + 8, 8 * c);
        D3DVERTEXELEMENT9 end = D3DDECL_END();
        e[c] = end;
        Obj *o = oput(K_DECL, U(p, 0));
        hr = IDirect3DDevice9_CreateVertexDeclaration(dev, e, (IDirect3DVertexDeclaration9 **)&o->p);
        note(hr, "CreateVertexDeclaration");
        if (FAILED(hr)) o->p = NULL;
        return 1;
    }
    case TAG('V', 'S', 'H', ' '): case TAG('P', 'S', 'H', ' '): {
        int vs = tag == TAG('V', 'S', 'H', ' ');
        if (n < 16) return fail("VSH/PSH");
        uint32_t bc = U(p, 3);
        if (16 + (uint64_t)bc > n || bc < 8) return fail("VSH/PSH bytecode");
        DWORD *code = malloc(bc);
        memcpy(code, p + 16, bc);
        Obj *o = oput(vs ? K_VS : K_PS, U(p, 0));
        hr = vs ? IDirect3DDevice9_CreateVertexShader(dev, code, (IDirect3DVertexShader9 **)&o->p)
                : IDirect3DDevice9_CreatePixelShader(dev, code, (IDirect3DPixelShader9 **)&o->p);
        note(hr, vs ? "CreateVertexShader" : "CreatePixelShader");
        if (FAILED(hr)) o->p = NULL;
        free(code);
        return 1;
    }
    case TAG('R', 'S', ' ', ' '): if (n >= 8) note(IDirect3DDevice9_SetRenderState(dev, U(p, 0), U(p, 1)), "SetRenderState"); return 1;
    case TAG('S', 'S', ' ', ' '): if (n >= 12) note(IDirect3DDevice9_SetSamplerState(dev, U(p, 0), U(p, 1), U(p, 2)), "SetSamplerState"); return 1;
    case TAG('T', 'S', 'S', ' '): if (n >= 12) IDirect3DDevice9_SetTextureStageState(dev, U(p, 0), U(p, 1), U(p, 2)); return 1;
    case TAG('S', 'T', 'E', 'X'): {
        if (n < 8) return 1;
        Obj *o = oget(K_TEX, U(p, 1));
        note(IDirect3DDevice9_SetTexture(dev, U(p, 0), o ? (IDirect3DBaseTexture9 *)o->p : NULL), "SetTexture");
        return 1;
    }
    case TAG('S', 'V', 'S', ' '): { Obj *o = n >= 4 ? oget(K_VS, U(p, 0)) : NULL; note(IDirect3DDevice9_SetVertexShader(dev, o ? (IDirect3DVertexShader9 *)o->p : NULL), "SetVertexShader"); return 1; }
    case TAG('S', 'P', 'S', ' '): { Obj *o = n >= 4 ? oget(K_PS, U(p, 0)) : NULL; note(IDirect3DDevice9_SetPixelShader(dev, o ? (IDirect3DPixelShader9 *)o->p : NULL), "SetPixelShader"); return 1; }
    case TAG('V', 'S', 'C', 'F'): case TAG('P', 'S', 'C', 'F'): case TAG('V', 'S', 'C', 'I'): case TAG('P', 'S', 'C', 'I'):
    case TAG('V', 'S', 'C', 'B'): case TAG('P', 'S', 'C', 'B'): {
        if (n < 8) return fail("constants");
        uint32_t s = U(p, 0), c = U(p, 1), t3 = tag >> 24, vs = (tag & 0xFF) == 'V';
        uint32_t each = t3 == 'B' ? 4 : 16;
        if (8 + (uint64_t)c * each > n) return fail("constants count");
        if (t3 == 'F') hr = vs ? IDirect3DDevice9_SetVertexShaderConstantF(dev, s, (const float *)(p + 8), c)
                               : IDirect3DDevice9_SetPixelShaderConstantF(dev, s, (const float *)(p + 8), c);
        else if (t3 == 'I') hr = vs ? IDirect3DDevice9_SetVertexShaderConstantI(dev, s, (const int *)(p + 8), c)
                                    : IDirect3DDevice9_SetPixelShaderConstantI(dev, s, (const int *)(p + 8), c);
        else hr = vs ? IDirect3DDevice9_SetVertexShaderConstantB(dev, s, (const BOOL *)(p + 8), c)
                     : IDirect3DDevice9_SetPixelShaderConstantB(dev, s, (const BOOL *)(p + 8), c);
        note(hr, "Set*ShaderConstant");
        return 1;
    }
    case TAG('S', 'D', 'C', 'L'): { Obj *o = n >= 4 ? oget(K_DECL, U(p, 0)) : NULL; if (o && o->p) note(IDirect3DDevice9_SetVertexDeclaration(dev, (IDirect3DVertexDeclaration9 *)o->p), "SetVertexDeclaration"); return 1; }
    case TAG('S', 'T', 'R', 'M'): {
        if (n < 16) return 1;
        Obj *o = oget(K_VB, U(p, 1));
        note(IDirect3DDevice9_SetStreamSource(dev, U(p, 0), o ? (IDirect3DVertexBuffer9 *)o->p : NULL, U(p, 2), U(p, 3)), "SetStreamSource");
        return 1;
    }
    case TAG('S', 'F', 'R', 'Q'): if (n >= 8) { note(IDirect3DDevice9_SetStreamSourceFreq(dev, U(p, 0), U(p, 1)), "SetStreamSourceFreq"); n_ext++; } return 1;
    case TAG('S', 'I', 'D', 'X'): { Obj *o = n >= 4 ? oget(K_IB, U(p, 0)) : NULL; note(IDirect3DDevice9_SetIndices(dev, o ? (IDirect3DIndexBuffer9 *)o->p : NULL), "SetIndices"); return 1; }
    case TAG('S', 'R', 'T', ' '): {
        if (n < 8) return 1;
        IDirect3DSurface9 *s = surf_of(U(p, 1));
        if (U(p, 0) == 0 && !s) return 1;   /* RT 0 cannot be NULL */
        note(IDirect3DDevice9_SetRenderTarget(dev, U(p, 0), U(p, 1) || U(p, 0) == 0 ? s : NULL), "SetRenderTarget");
        return 1;
    }
    case TAG('S', 'D', 'S', ' '): {
        if (n < 4) return 1;
        IDirect3DSurface9 *s = U(p, 0) == 0xFFFFFFFFu ? autods : U(p, 0) ? surf_of(U(p, 0)) : NULL;
        note(IDirect3DDevice9_SetDepthStencilSurface(dev, s), "SetDepthStencilSurface");
        return 1;
    }
    case TAG('V', 'I', 'E', 'W'): {
        if (n < 24) return 1;
        D3DVIEWPORT9 v = {U(p, 0), U(p, 1), U(p, 2), U(p, 3), F(p, 4), F(p, 5)};
        note(IDirect3DDevice9_SetViewport(dev, &v), "SetViewport");
        return 1;
    }
    case TAG('S', 'C', 'I', 'S'): { if (n >= 16) { RECT r; memcpy(&r, p, 16); note(IDirect3DDevice9_SetScissorRect(dev, &r), "SetScissorRect"); } return 1; }
    case TAG('C', 'L', 'R', ' '): {
        if (n < 20) return fail("CLR");
        uint32_t nr = U(p, 4);
        if (20 + 16 * (uint64_t)nr > n) return fail("CLR rects");
        scene_on();
        note(IDirect3DDevice9_Clear(dev, nr, nr ? (const D3DRECT *)(p + 20) : NULL, U(p, 0), U(p, 1), F(p, 2), U(p, 3)), "Clear");
        return 1;
    }
    case TAG('S', 'T', 'R', 'C'): {   /* src surf, flags (1 src rect, 2 dst rect), src rect, dst surf, dst rect, filter */
        if (n < 48) return fail("STRC");
        IDirect3DSurface9 *s = surf_of(U(p, 0)), *d = surf_of(U(p, 6));
        uint32_t fl = U(p, 1); RECT sr, dr; memcpy(&sr, p + 8, 16); memcpy(&dr, p + 28, 16);
        n_ext++;
        if (s && d) { scene_off(); note(IDirect3DDevice9_StretchRect(dev, s, fl & 1 ? &sr : NULL, d, fl & 2 ? &dr : NULL, (D3DTEXTUREFILTERTYPE)U(p, 11)), "StretchRect"); }
        return 1;
    }
    case TAG('C', 'F', 'I', 'L'): {   /* surf, flags (1 rect), rect, D3DCOLOR */
        if (n < 28) return fail("CFIL");
        IDirect3DSurface9 *s = surf_of(U(p, 0)); RECT r; memcpy(&r, p + 8, 16);
        n_ext++;
        if (s) { scene_off(); note(IDirect3DDevice9_ColorFill(dev, s, U(p, 1) & 1 ? &r : NULL, U(p, 6)), "ColorFill"); }
        return 1;
    }
    case TAG('D', 'R', 'A', 'W'):
        if (n < 12) return fail("DRAW");
        before_draw();
        if (draw_allowed()) { scene_on(); note(IDirect3DDevice9_DrawPrimitive(dev, U(p, 0), U(p, 1), U(p, 2)), "DrawPrimitive"); }
        after_draw();
        return 1;
    case TAG('D', 'R', 'W', 'I'):
        if (n < 24) return fail("DRWI");
        before_draw();
        if (draw_allowed()) { scene_on(); note(IDirect3DDevice9_DrawIndexedPrimitive(dev, U(p, 0), (INT)U(p, 1), U(p, 2), U(p, 3), U(p, 4), U(p, 5)), "DrawIndexedPrimitive"); }
        after_draw();
        return 1;
    case TAG('P', 'R', 'E', 'S'): {
        char path[1024];
        scene_off();
        { LARGE_INTEGER t; QueryPerformanceCounter(&t);
          if (frame_times) fprintf(frame_times, "%u\t%.3f\n", frame_no, (t.QuadPart - t_frame0.QuadPart) * 1000.0 / qpf.QuadPart - png_ms); }
        if ((long)frame_no >= png_first && (png_last < 0 || (long)frame_no <= png_last)) {
            snprintf(path, sizeof path, "%s/f%05u.png", outdir, frame_no);
            dump_surface(backbuf, path);
        }
        note(IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL), "Present");
        frame_no++; draw_no = 0; png_ms = 0; QueryPerformanceCounter(&t_frame0);
        return 1;
    }
    default:
        n_unknown++;
        return 1;
    }
}

int main(int argc, char **argv) {
    const char *in = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "--per-draw")) per_draw = 1;
        else if (!strcmp(argv[i], "--until-draw") && i + 1 < argc) until_draw = atol(argv[++i]);
        else if (!strcmp(argv[i], "--frame") && i + 1 < argc) only_frame = atol(argv[++i]);
        else if (!strcmp(argv[i], "--draw-stats") && i + 1 < argc) {
            draw_stats = fopen(argv[++i], "w");
            if (!draw_stats) { fprintf(stderr, "nxd9play: cannot write %s\n", argv[i]); return 2; }
        }
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) {
            char *e; png_first = strtol(argv[++i], &e, 10);
            png_last = *e == '-' && e[1] ? strtol(e + 1, NULL, 10) : (*e == '-' ? -1 : png_first);
        }
        else if (!in && argv[i][0] != '-') in = argv[i];
        else { in = NULL; break; }
    }
    if (!in || !outdir) { fprintf(stderr, "usage: nxd9play <file.nxd9> -o <dir> [--per-draw] [--until-draw N] [--frame F]\n"); return 2; }
    CreateDirectoryA(outdir, NULL);
    crc_init();
    QueryPerformanceFrequency(&qpf); QueryPerformanceCounter(&t_frame0);
    { char fp[1024]; snprintf(fp, sizeof fp, "%s/frames.tsv", outdir ? outdir : "."); frame_times = fopen(fp, "w"); }
    FILE *f = fopen(in, "rb");
    if (!f) { fprintf(stderr, "nxd9play: cannot open %s\n", in); return 2; }
    uint8_t hdr[8];
    if (fread(hdr, 1, 8, f) != 8 || memcmp(hdr, "NXD9\0\0\0\1", 8)) { fprintf(stderr, "nxd9play: not an NXD9 file\n"); fclose(f); return 1; }
    int ok = 1;
    uint8_t *buf = NULL; size_t cap = 0;
    for (;;) {
        uint32_t th[2];
        size_t got = fread(th, 1, 8, f);
        if (got == 0) break;
        if (got != 8) { ok = fail("truncated record header"); break; }
        size_t pad = ((size_t)th[1] + 3) & ~(size_t)3;
        if (th[1] > (512u << 20)) { ok = fail("record too large"); break; }
        if (pad > cap) { free(buf); cap = pad; buf = malloc(cap ? cap : 4); if (!buf) { ok = fail("out of memory"); break; } }
        if (pad && fread(buf, 1, pad, f) != pad) {
            /* the last record may lack its padding */
            if (feof(f) && th[1] <= pad) { /* accept only if the payload itself was complete */ }
            ok = fail("truncated record"); break;
        }
        if (!record(th[0], buf, th[1])) { ok = 0; break; }
    }
    fclose(f); free(buf);
    char line[512];
    snprintf(line, sizeof line, "nxd9play: frames %u draws %lu records %lu unknown %lu ext %lu failed_calls %lu%s%s",
             frame_no, n_draws, n_records, n_unknown, n_ext, n_failed_calls, first_err[0] ? " first_failure " : "", first_err);
    puts(line);
    if (frame_times) fclose(frame_times);
    if (draw_stats) fclose(draw_stats);
    char sp[1024]; snprintf(sp, sizeof sp, "%s/summary.txt", outdir);
    FILE *s = fopen(sp, "w");
    if (s) { fprintf(s, "%s\n", line); fclose(s); }
    if (dev) { IDirect3DDevice9_Release(dev); }
    if (d3d) IDirect3D9_Release(d3d);
    return ok ? 0 : 1;
}
