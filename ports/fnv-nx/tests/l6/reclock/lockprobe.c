/* lockprobe.c: plain D3D9 (no recorder knowledge). Walks mip chains the way FalloutNV.exe does (GetSurfaceLevel n and n+1, GetDesc x2, LockRect x2,
 * UnlockRect x2) and many lock variants; writes a deterministic pattern into every writable lock and reads it back through the next lock.
 * Prints one `LK` line per lock call and one `ST` line per step. Exit 0 iff every check passed (skips because the runtime refuses a format are
 * printed, not failures). Output must be identical with and without the recorder proxy. Build: i686-w64-mingw32-gcc -O1 lockprobe.c -ld3d9 */
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <string.h>
#define FCC(a, b, c, d) ((D3DFORMAT)((a) | ((b) << 8) | ((c) << 16) | ((DWORD)(d) << 24)))
#define DXT1 FCC('D', 'X', 'T', '1')
#define DXT3 FCC('D', 'X', 'T', '3')
#define DXT5 FCC('D', 'X', 'T', '5')
static IDirect3DDevice9 *dev; static int nfail, nlk, nst; static unsigned seedctr = 1;

static int isblock(D3DFORMAT f) { return f == DXT1 || f == DXT3 || f == DXT5; }
static int unitbytes(D3DFORMAT f) { switch ((int)f) { case DXT1: return 8; case DXT3: case DXT5: return 16; case D3DFMT_A8R8G8B8: return 4; case D3DFMT_L8: return 1; case D3DFMT_R5G6B5: return 2; case D3DFMT_A16B16G16R16F: return 8; } return 4; }
static unsigned rowsof(D3DFORMAT f, unsigned h) { return isblock(f) ? (h + 3) / 4 : h; }
static unsigned rowbytesof(D3DFORMAT f, unsigned w) { return (isblock(f) ? (w + 3) / 4 : w) * unitbytes(f); }
static unsigned lvl(unsigned d, unsigned l) { unsigned v = l >= 32 ? 0 : d >> l; return v ? v : 1; }
static unsigned char pat(unsigned seed, size_t i) { return (unsigned char)(seed * 31u + i * 7u + (i >> 8) * 13u + 1u); }

/* one texture under test */
typedef struct { int kind; /* 0 tex 1 cube 2 volume */ D3DFORMAT fmt; D3DPOOL pool; DWORD usage; unsigned W, H, D, levels; void *tex; int id; unsigned lastfull[6][16]; } T;
static const char *fn(D3DFORMAT f) { static char b[16]; if (f == DXT1) return "DXT1"; if (f == DXT3) return "DXT3"; if (f == DXT5) return "DXT5"; sprintf(b, "F%d", (int)f); return b; }

/* via: 0 texture/cube/volume method, 1 surface method */
typedef struct { void *p; int via; int box; } Lk;
static HRESULT dolock(T *t, int via, void *surf, unsigned face, unsigned l, const RECT *r, const D3DBOX *b, DWORD fl, void **bits, int *pitch, int *spitch) {
    D3DLOCKED_RECT lr; D3DLOCKED_BOX lb; HRESULT hr;
    if (t->kind == 2) { hr = IDirect3DVolumeTexture9_LockBox((IDirect3DVolumeTexture9 *)t->tex, l, &lb, b, fl); *bits = lb.pBits; *pitch = lb.RowPitch; *spitch = lb.SlicePitch; return hr; }
    if (via) hr = IDirect3DSurface9_LockRect((IDirect3DSurface9 *)surf, &lr, r, fl);
    else if (t->kind == 0) hr = IDirect3DTexture9_LockRect((IDirect3DTexture9 *)t->tex, l, &lr, r, fl);
    else hr = IDirect3DCubeTexture9_LockRect((IDirect3DCubeTexture9 *)t->tex, (D3DCUBEMAP_FACES)face, l, &lr, r, fl);
    *bits = lr.pBits; *pitch = lr.Pitch; *spitch = 0; return hr;
}
static HRESULT dounlock(T *t, int via, void *surf, unsigned face, unsigned l) {
    if (t->kind == 2) return IDirect3DVolumeTexture9_UnlockBox((IDirect3DVolumeTexture9 *)t->tex, l);
    if (via) return IDirect3DSurface9_UnlockRect((IDirect3DSurface9 *)surf);
    if (t->kind == 0) return IDirect3DTexture9_UnlockRect((IDirect3DTexture9 *)t->tex, l);
    return IDirect3DCubeTexture9_UnlockRect((IDirect3DCubeTexture9 *)t->tex, (D3DCUBEMAP_FACES)face, l);
}
static void *getsurf(T *t, unsigned face, unsigned l) {
    IDirect3DSurface9 *s = 0; D3DSURFACE_DESC d;
    if (t->kind == 2) return 0;
    if (t->kind == 0) IDirect3DTexture9_GetSurfaceLevel((IDirect3DTexture9 *)t->tex, l, &s); else IDirect3DCubeTexture9_GetCubeMapSurface((IDirect3DCubeTexture9 *)t->tex, (D3DCUBEMAP_FACES)face, l, &s);
    if (s) { IDirect3DSurface9_GetDesc(s, &d); if (d.Width != lvl(t->W, l) || d.Height != lvl(t->H, l) || d.Format != t->fmt) { printf("BAD desc\n"); nfail++; } }
    return s;
}
/* open lock state */
typedef struct { T *t; int via, ok; void *surf; unsigned face, l; void *bits; int pitch, spitch; unsigned w, h, d, rows, rb; int full; DWORD fl; unsigned rd; } O;
static void rd_extent(T *t, unsigned l, const RECT *r, const D3DBOX *b, O *o) {
    o->w = r ? r->right - r->left : (b ? b->Right - b->Left : lvl(t->W, l)); o->h = r ? r->bottom - r->top : (b ? b->Bottom - b->Top : lvl(t->H, l));
    o->d = t->kind == 2 ? (b ? b->Back - b->Front : lvl(t->D, l)) : 1; o->rows = rowsof(t->fmt, o->h); o->rb = rowbytesof(t->fmt, o->w); o->full = !r && !b;
}
static void lockop(T *t, O *o, int via, unsigned face, unsigned l, const RECT *r, const D3DBOX *b, DWORD fl, const char *tag) {
    memset(o, 0, sizeof *o); o->t = t; o->via = via; o->face = face; o->l = l; o->fl = fl; o->surf = via ? getsurf(t, face, l) : 0;
    HRESULT hr = dolock(t, via, o->surf, face, l, r, b, fl, &o->bits, &o->pitch, &o->spitch);
    o->ok = hr >= 0; rd_extent(t, l, r, b, o); nlk++;
    o->rd = 0xFFFFFFFF; if (o->ok && o->full && !(fl & D3DLOCK_DISCARD)) o->rd = t->lastfull[face][l];
    printf("LK %s %s tex=%d k=%d f=%s p=%d W=%u H=%u D=%u L=%u face=%u rect=%d,%d,%d,%d fl=%lx via=%d ok=%d rd=%d\n", tag, "lock", t->id, t->kind, fn(t->fmt), t->pool, t->W, t->H, t->D, l, face,
           r ? (int)r->left : b ? (int)b->Left : -1, r ? (int)r->top : b ? (int)b->Top : -1, r ? (int)r->right : b ? (int)b->Right : -1, r ? (int)r->bottom : b ? (int)b->Bottom : -1, fl, via, o->ok, (int)o->rd);
    if (b) printf("LKB %d %d\n", (int)b->Front, (int)b->Back);
}
static void check_read(O *o) {
    if (!o->ok || o->rd == 0xFFFFFFFF) return;
    size_t i = 0; int bad = 0;
    for (unsigned d = 0; d < o->d; d++) for (unsigned r = 0; r < o->rows; r++) for (unsigned k = 0; k < o->rb; k++, i++)
        if (((unsigned char *)o->bits)[d * o->spitch + r * o->pitch + k] != pat(o->rd, i)) bad++;
    if (bad) { printf("BAD read-back tex=%d L=%u: %d bytes differ\n", o->t->id, o->l, bad); nfail++; }
}
static void writepat(O *o, unsigned seed) {
    size_t i = 0;
    for (unsigned d = 0; d < o->d; d++) for (unsigned r = 0; r < o->rows; r++) for (unsigned k = 0; k < o->rb; k++, i++)
        ((unsigned char *)o->bits)[d * o->spitch + r * o->pitch + k] = pat(seed, i);
    printf("WR seed=%u bytes=%u\n", seed, (unsigned)i);
    if (o->full) o->t->lastfull[o->face][o->l] = seed; else o->t->lastfull[o->face][o->l] = 0xFFFFFFFF;
}
static void unlockop(O *o, int via) {
    if (!o->ok) return;
    if (via && !o->surf) o->surf = getsurf(o->t, o->face, o->l);
    HRESULT hr = dounlock(o->t, via, o->surf, o->face, o->l);
    if (hr < 0) { printf("BAD unlock hr=%08lx\n", (unsigned long)hr); nfail++; }
    if (o->surf) IDirect3DSurface9_Release((IDirect3DSurface9 *)o->surf);
}
/* the game's chain step: src level n locked (flags sf), dst level n+1 locked (flags 0), dst gets a pattern. mode bits: 1 = via surfaces (game), 2 = unlock through the other path */
static void chain(T *t, unsigned face, int via, int cross, DWORD sf, int use_rect, const char *tag) {
    nst++;
    if (t->levels == 1) {   /* single-level texture (non-power-of-two / non-multiple-of-4): write the whole level, read it back through a read-only lock */
        RECT r; const RECT *pr = 0; if (use_rect && t->kind != 2 && t->W > 1 && t->H > 1) { r = (RECT){ 0, 0, t->W - 1, t->H - 1 }; if (isblock(t->fmt)) r = (RECT){ 0, 0, 4, 4 }; if (isblock(t->fmt) && (t->W < 4 || t->H < 4)) pr = 0; else pr = &r; }
        O a; lockop(t, &a, via, face, 0, pr, 0, 0, tag); if (a.ok) writepat(&a, seedctr++); unlockop(&a, via ? 1 : 0);
        lockop(t, &a, via, face, 0, 0, 0, sf, tag); check_read(&a); unlockop(&a, cross ? !via : via); printf("ST %s done\n", tag); return;
    }
    for (unsigned n = 0; n + 1 < t->levels; n++) {
        void *s0 = via ? getsurf(t, face, n) : 0, *s1 = via ? getsurf(t, face, n + 1) : 0; D3DSURFACE_DESC d0, d1;
        if (via && t->kind != 2) { IDirect3DSurface9_GetDesc((IDirect3DSurface9 *)s0, &d0); IDirect3DSurface9_GetDesc((IDirect3DSurface9 *)s1, &d1); }
        RECT r0, r1; D3DBOX b0, b1; const RECT *pr0 = 0, *pr1 = 0; const D3DBOX *pb0 = 0, *pb1 = 0;
        if (use_rect && t->kind != 2) { unsigned w0 = lvl(t->W, n), h0 = lvl(t->H, n), w1 = lvl(t->W, n + 1), h1 = lvl(t->H, n + 1);
            if (isblock(t->fmt)) { if (w0 >= 8 && h0 >= 8) { r0 = (RECT){ 0, 0, (w0 / 2) & ~3, (h0 / 2) & ~3 }; pr0 = &r0; } if (w1 >= 8 && h1 >= 8) { r1 = (RECT){ 4, 4, 8, 8 }; pr1 = &r1; } }
            else { if (w0 > 1 && h0 > 1) { r0 = (RECT){ 1, 0, w0, h0 - 1 }; pr0 = &r0; } if (w1 > 1) { r1 = (RECT){ 0, 0, 1, h1 }; pr1 = &r1; } } }
        if (use_rect && t->kind == 2) { unsigned w1 = lvl(t->W, n + 1), d1_ = lvl(t->D, n + 1); if (w1 > 1 && d1_ > 1) { b1 = (D3DBOX){ 1, 0, w1, lvl(t->H, n + 1), 0, d1_ - 1 }; pb1 = &b1; } (void)b0; }
        O a, b; lockop(t, &a, via, face, n, pr0, pb0, sf, tag); lockop(t, &b, via, face, n + 1, pr1, pb1, 0, tag);
        check_read(&a); if (b.ok) { writepat(&b, seedctr++); }
        int cv = cross ? !via : via; if (a.ok) unlockop(&a, cv); if (b.ok) unlockop(&b, cv);
        if (s0) IDirect3DSurface9_Release((IDirect3DSurface9 *)s0);
        if (s1) IDirect3DSurface9_Release((IDirect3DSurface9 *)s1);
    }
    printf("ST %s done\n", tag);
}
static T *mk(int kind, D3DFORMAT f, D3DPOOL pool, DWORD usage, unsigned W, unsigned H, unsigned D, unsigned levels) {
    static T pool_[1024]; static int nt; T *t = &pool_[nt]; memset(t, 0xFF, sizeof *t);
    t->kind = kind; if (kind == 1) H = W; t->fmt = f; t->pool = pool; t->usage = usage; t->W = W; t->H = H; t->D = D; t->id = nt; HRESULT hr; t->tex = 0;
    if (kind == 0) hr = IDirect3DDevice9_CreateTexture(dev, W, H, levels, usage, f, pool, (IDirect3DTexture9 **)&t->tex, 0);
    else if (kind == 1) hr = IDirect3DDevice9_CreateCubeTexture(dev, W, levels, usage, f, pool, (IDirect3DCubeTexture9 **)&t->tex, 0);
    else hr = IDirect3DDevice9_CreateVolumeTexture(dev, W, H, D, levels, usage, f, pool, (IDirect3DVolumeTexture9 **)&t->tex, 0);
    nt++;
    if (hr < 0) { printf("SKIP create kind=%d f=%s p=%d %ux%ux%u L%u hr=%08lx\n", kind, fn(f), pool, W, H, D, levels, (unsigned long)hr); return 0; }
    t->levels = kind == 0 ? IDirect3DTexture9_GetLevelCount((IDirect3DTexture9 *)t->tex) : kind == 1 ? IDirect3DCubeTexture9_GetLevelCount((IDirect3DCubeTexture9 *)t->tex) : IDirect3DVolumeTexture9_GetLevelCount((IDirect3DVolumeTexture9 *)t->tex);
    printf("TEX %d kind=%d f=%s pool=%d %ux%ux%u levels=%u\n", t->id, kind, fn(f), pool, W, H, D, t->levels);
    memset(t->lastfull, 0xFF, sizeof t->lastfull); return t;
}
static void rel(T *t) { if (t) IUnknown_Release((IUnknown *)t->tex); }

int main(void) {
    IDirect3D9 *d = Direct3DCreate9(D3D_SDK_VERSION); if (!d) return 1;
    HWND w = CreateWindowA("STATIC", "lockprobe", WS_OVERLAPPEDWINDOW, 0, 0, 320, 240, NULL, NULL, NULL, NULL);
    D3DPRESENT_PARAMETERS pp = { 0 }; pp.BackBufferWidth = 320; pp.BackBufferHeight = 240; pp.BackBufferFormat = D3DFMT_UNKNOWN; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = w; pp.Windowed = TRUE;
    if (FAILED(IDirect3D9_CreateDevice(d, 0, D3DDEVTYPE_HAL, w, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev))) { printf("CreateDevice failed\n"); return 2; }
    T *t;
    /* 1. the game's texture and sequence, then the variants */
    const D3DFORMAT fm[] = { DXT1, DXT3, DXT5, D3DFMT_A8R8G8B8, D3DFMT_L8, D3DFMT_R5G6B5, D3DFMT_A16B16G16R16F };
    struct { unsigned W, H; } sz[] = { { 1024, 512 }, { 64, 64 }, { 37, 21 }, { 5, 3 }, { 1, 1 }, { 2, 2 }, { 4, 8 }, { 130, 70 }, { 20, 12 }, { 37, 21 }, { 5, 3 }, { 3, 7 } };
    static const struct { int via, cross, ro, nd, rect; const char *name; } var[] = {
        { 1, 0, 0, 0, 0, "surf" }, { 1, 0, 1, 0, 0, "surf-ro" }, { 1, 0, 0, 1, 0, "surf-nodirty" }, { 1, 0, 0, 0, 1, "surf-rect" },
        { 0, 0, 0, 0, 0, "tex" }, { 0, 0, 1, 0, 0, "tex-ro" }, { 0, 0, 0, 0, 1, "tex-rect" }, { 0, 1, 0, 0, 0, "tex-lock-surf-unlock" }, { 1, 1, 0, 0, 0, "surf-lock-tex-unlock" } };
    t = mk(0, DXT1, D3DPOOL_MANAGED, 0, 1024, 512, 1, 11);   /* exactly the game's texture, exactly its sequence */
    if (t) { chain(t, 0, 1, 0, 0, 0, "game"); rel(t); }
    for (int p = 0; p < 3; p++) for (unsigned f = 0; f < sizeof fm / sizeof *fm; f++) for (unsigned s = 0; s < sizeof sz / sizeof *sz; s++) {
        D3DPOOL pool = p == 0 ? D3DPOOL_MANAGED : p == 1 ? D3DPOOL_SYSTEMMEM : D3DPOOL_DEFAULT; DWORD us = p == 2 ? D3DUSAGE_DYNAMIC : 0;
        if (p && (s % 2 || s > 5) && s < 8) continue;   /* thin out the non-managed pools */
        t = mk(0, fm[f], pool, us, sz[s].W, sz[s].H, 1, s >= 8 ? 1 : 0); if (!t) continue;
        for (unsigned v = 0; v < sizeof var / sizeof *var; v++) { if (p && v >= 4 && v != 7) continue; if (sz[s].W > 200 && v) continue;
            DWORD sf = (var[v].ro ? D3DLOCK_READONLY : 0) | (var[v].nd ? D3DLOCK_NO_DIRTY_UPDATE : 0); chain(t, 0, var[v].via, var[v].cross, sf, var[v].rect, var[v].name); }
        rel(t);
    }
    /* 2. cube textures, per face, via surface and via the cube */
    for (unsigned f = 0; f < 4; f++) { t = mk(1, fm[f], D3DPOOL_MANAGED, 0, f == 0 ? 64 : 37, 1, 1, 0); if (!t) continue;
        for (unsigned face = 0; face < 6; face++) { chain(t, face, 1, 0, 0, 0, "cube-surf"); chain(t, face, 0, 0, D3DLOCK_READONLY, 0, "cube"); } rel(t); }
    /* 3. volume textures */
    const D3DFORMAT vf[] = { D3DFMT_A8R8G8B8, D3DFMT_L8, DXT1 };
    for (unsigned f = 0; f < 3; f++) { t = mk(2, vf[f], D3DPOOL_MANAGED, 0, 16, 8, 4 + 3 * f, 0); if (!t) continue; chain(t, 0, 0, 0, 0, 0, "vol"); chain(t, 0, 0, 0, D3DLOCK_READONLY, 1, "vol-box"); rel(t); }
    /* 4. the game's texture again, many times and two chains in flight (two textures alive at once) */
    T *a = mk(0, DXT1, D3DPOOL_MANAGED, 0, 1024, 512, 1, 11), *b = mk(0, DXT5, D3DPOOL_MANAGED, 0, 256, 256, 1, 0);
    if (a && b) { chain(a, 0, 1, 0, 0, 0, "game2"); chain(b, 0, 1, 0, 0, 0, "game3"); chain(a, 0, 1, 0, 0, 0, "game4"); }
    rel(a); rel(b);
    IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
    IDirect3DDevice9_Release(dev); IDirect3D9_Release(d);
    printf("SUMMARY locks=%d steps=%d fail=%d\n", nlk, nst, nfail);
    return nfail ? 1 : 0;
}
