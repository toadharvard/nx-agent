/* test_d3dx_save.cpp - D3DXSaveTextureToFileA / D3DXSaveSurfaceToFileA (render/nine/d3dx/d3dx_save.cpp), -m32 build:
 * a lockable A8R8G8B8 texture saved as PNG (the format the game's screenshot command asks for), BMP, TGA and DDS; the
 * files are read back and decoded here (PNG chunk CRCs, the zlib stream of stored blocks, Adler-32, every pixel). */
#include "../s4_test.h"

#include <d3dx9.h>
#include <stdint.h>
#include <vector>

namespace {
const unsigned W = 5, H = 3;   // odd width: BMP row padding

uint32_t pixel(unsigned x, unsigned y) { return 0x80000000u | (x * 40u) << 16 | (y * 70u) << 8 | (x + y * W); }

struct FakeSurface : IDirect3DSurface9 {
    uint32_t px[W * H];
    int locks = 0, refs = 1;
    FakeSurface() { for (unsigned y = 0; y < H; y++) for (unsigned x = 0; x < W; x++) px[y * W + x] = pixel(x, y); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { return --refs; }
    HRESULT STDMETHODCALLTYPE GetDevice(IDirect3DDevice9 **d) override { *d = nullptr; return D3DERR_INVALIDCALL; }
    HRESULT STDMETHODCALLTYPE SetPrivateData(REFGUID, const void *, DWORD, DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetPrivateData(REFGUID, void *, DWORD *) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE FreePrivateData(REFGUID) override { return E_NOTIMPL; }
    DWORD STDMETHODCALLTYPE SetPriority(DWORD) override { return 0; }
    DWORD STDMETHODCALLTYPE GetPriority() override { return 0; }
    void STDMETHODCALLTYPE PreLoad() override {}
    D3DRESOURCETYPE STDMETHODCALLTYPE GetType() override { return D3DRTYPE_SURFACE; }
    HRESULT STDMETHODCALLTYPE GetContainer(REFIID, void **) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDesc(D3DSURFACE_DESC *d) override
    {
        memset(d, 0, sizeof *d);
        d->Format = D3DFMT_A8R8G8B8;
        d->Type = D3DRTYPE_SURFACE;
        d->Pool = D3DPOOL_MANAGED;
        d->Width = W;
        d->Height = H;
        return D3D_OK;
    }
    HRESULT STDMETHODCALLTYPE LockRect(D3DLOCKED_RECT *lr, const RECT *, DWORD) override
    {
        locks++;
        lr->pBits = px;
        lr->Pitch = W * 4;
        return D3D_OK;
    }
    HRESULT STDMETHODCALLTYPE UnlockRect() override { locks--; return D3D_OK; }
    HRESULT STDMETHODCALLTYPE GetDC(HDC *) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ReleaseDC(HDC) override { return E_NOTIMPL; }
};

struct FakeTexture : IDirect3DTexture9 {
    FakeSurface s;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE GetDevice(IDirect3DDevice9 **d) override { *d = nullptr; return D3DERR_INVALIDCALL; }
    HRESULT STDMETHODCALLTYPE SetPrivateData(REFGUID, const void *, DWORD, DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetPrivateData(REFGUID, void *, DWORD *) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE FreePrivateData(REFGUID) override { return E_NOTIMPL; }
    DWORD STDMETHODCALLTYPE SetPriority(DWORD) override { return 0; }
    DWORD STDMETHODCALLTYPE GetPriority() override { return 0; }
    void STDMETHODCALLTYPE PreLoad() override {}
    D3DRESOURCETYPE STDMETHODCALLTYPE GetType() override { return D3DRTYPE_TEXTURE; }
    DWORD STDMETHODCALLTYPE SetLOD(DWORD) override { return 0; }
    DWORD STDMETHODCALLTYPE GetLOD() override { return 0; }
    DWORD STDMETHODCALLTYPE GetLevelCount() override { return 1; }
    HRESULT STDMETHODCALLTYPE SetAutoGenFilterType(D3DTEXTUREFILTERTYPE) override { return E_NOTIMPL; }
    D3DTEXTUREFILTERTYPE STDMETHODCALLTYPE GetAutoGenFilterType() override { return D3DTEXF_NONE; }
    void STDMETHODCALLTYPE GenerateMipSubLevels() override {}
    HRESULT STDMETHODCALLTYPE GetLevelDesc(UINT, D3DSURFACE_DESC *d) override { return s.GetDesc(d); }
    HRESULT STDMETHODCALLTYPE GetSurfaceLevel(UINT l, IDirect3DSurface9 **out) override
    {
        if (l) return D3DERR_INVALIDCALL;
        s.AddRef();
        *out = &s;
        return D3D_OK;
    }
    HRESULT STDMETHODCALLTYPE LockRect(UINT, D3DLOCKED_RECT *lr, const RECT *r, DWORD f) override { return s.LockRect(lr, r, f); }
    HRESULT STDMETHODCALLTYPE UnlockRect(UINT) override { return s.UnlockRect(); }
    HRESULT STDMETHODCALLTYPE AddDirtyRect(const RECT *) override { return D3D_OK; }
};

std::vector<uint8_t> read_all(const char *path)
{
    std::vector<uint8_t> v;
    HANDLE h = CreateFileA(path, GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return v;
    uint8_t b[4096];
    DWORD got;
    while (ReadFile(h, b, sizeof b, &got, NULL) && got) v.insert(v.end(), b, b + got);
    CloseHandle(h);
    return v;
}
uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
uint32_t crc(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
    }
    return ~c;
}
} // namespace

TEST(d3dx_save_texture_png)
{
    FakeTexture t;
    CHECK_EQ(D3DXSaveTextureToFileA("C:\\FalloutNV\\ScreenShot0.png", D3DXIFF_PNG, &t, NULL), D3D_OK);
    CHECK_EQ(t.s.locks, 0);
    CHECK_EQ(t.s.refs, 1);   // the level was released
    std::vector<uint8_t> f = read_all("C:\\FalloutNV\\ScreenShot0.png");
    CHECK(f.size() > 8 && !memcmp(f.data(), "\x89PNG\r\n\x1a\n", 8));
    std::vector<uint8_t> idat;
    size_t at = 8;
    int chunks = 0, crc_ok = 1;
    unsigned w = 0, h = 0;
    while (at + 12 <= f.size()) {
        uint32_t n = be32(&f[at]);
        if (at + 12 + n > f.size()) break;
        const uint8_t *type = &f[at + 4], *data = &f[at + 8];
        crc_ok &= crc(type, n + 4) == be32(&f[at + 8 + n]);
        if (!memcmp(type, "IHDR", 4)) {
            w = be32(data);
            h = be32(data + 4);
            CHECK(data[8] == 8 && data[9] == 2 && data[10] == 0 && data[11] == 0 && data[12] == 0);
        } else if (!memcmp(type, "IDAT", 4)) {
            idat.insert(idat.end(), data, data + n);
        }
        chunks++;
        at += 12 + n;
    }
    CHECK(crc_ok && chunks == 3 && at == f.size());
    CHECK(w == W && h == H);
    /* zlib: header, stored blocks, Adler-32 */
    CHECK(idat.size() > 6 && idat[0] == 0x78 && (idat[0] * 256 + idat[1]) % 31 == 0);
    std::vector<uint8_t> raw;
    size_t p = 2;
    for (;;) {
        uint8_t hdr = idat[p];
        unsigned len = idat[p + 1] | idat[p + 2] << 8, nlen = idat[p + 3] | idat[p + 4] << 8;
        CHECK((hdr & 6) == 0 && (len ^ nlen) == 0xffff);
        raw.insert(raw.end(), &idat[p + 5], &idat[p + 5] + len);
        p += 5 + len;
        if (hdr & 1) break;
    }
    uint32_t a = 1, b = 0;
    for (uint8_t c : raw) { a = (a + c) % 65521; b = (b + a) % 65521; }
    CHECK(p + 4 == idat.size() && be32(&idat[p]) == (b << 16 | a));
    CHECK_EQ(raw.size(), H * (1 + W * 3));
    int same = 1;
    for (unsigned y = 0; y < H; y++) {
        same &= raw[y * (1 + W * 3)] == 0;   // filter type 0
        for (unsigned x = 0; x < W; x++) {
            const uint8_t *q = &raw[y * (1 + W * 3) + 1 + x * 3];
            uint32_t c = pixel(x, y);
            same &= q[0] == (uint8_t)(c >> 16) && q[1] == (uint8_t)(c >> 8) && q[2] == (uint8_t)c;
        }
    }
    CHECK(same);
}

TEST(d3dx_save_bmp_tga_dds)
{
    FakeTexture t;
    CHECK_EQ(D3DXSaveTextureToFileA("C:\\FalloutNV\\s.bmp", D3DXIFF_BMP, &t, NULL), D3D_OK);
    std::vector<uint8_t> b = read_all("C:\\FalloutNV\\s.bmp");
    const unsigned row = (W * 3 + 3) & ~3u;
    CHECK(b.size() == 54 + row * H && b[0] == 'B' && b[1] == 'M' && le32(&b[2]) == b.size() && le32(&b[18]) == W &&
          le32(&b[22]) == H && b[28] == 24);
    int same = 1;
    for (unsigned y = 0; y < H; y++)
        for (unsigned x = 0; x < W; x++) {
            const uint8_t *q = &b[54 + (H - 1 - y) * row + x * 3];   // bottom-up rows, BGR
            uint32_t c = pixel(x, y);
            same &= q[0] == (uint8_t)c && q[1] == (uint8_t)(c >> 8) && q[2] == (uint8_t)(c >> 16);
        }
    CHECK(same);

    CHECK_EQ(D3DXSaveTextureToFileA("C:\\FalloutNV\\s.tga", D3DXIFF_TGA, &t, NULL), D3D_OK);
    std::vector<uint8_t> g = read_all("C:\\FalloutNV\\s.tga");
    CHECK(g.size() == 18 + W * H * 4 && g[2] == 2 && g[12] == W && g[14] == H && g[16] == 32 && g[17] == 0x28);
    CHECK(le32(&g[18]) == pixel(0, 0) && le32(&g[18 + 4 * (W * H - 1)]) == pixel(W - 1, H - 1));

    FakeSurface s;
    CHECK_EQ(D3DXSaveSurfaceToFileA("C:\\FalloutNV\\s.dds", D3DXIFF_DDS, &s, NULL, NULL), D3D_OK);
    std::vector<uint8_t> d = read_all("C:\\FalloutNV\\s.dds");
    CHECK(d.size() == 128 + W * H * 4 && le32(&d[0]) == 0x20534444 && le32(&d[4]) == 124 && le32(&d[12]) == H &&
          le32(&d[16]) == W && le32(&d[88]) == 32 && le32(&d[128]) == pixel(0, 0));

    /* not written: JPG, a source rectangle, no file name */
    CHECK_EQ(D3DXSaveTextureToFileA("C:\\FalloutNV\\s.jpg", D3DXIFF_JPG, &t, NULL), D3DERR_INVALIDCALL);
    RECT r = {0, 0, 1, 1};
    CHECK_EQ(D3DXSaveSurfaceToFileA("C:\\FalloutNV\\s.png", D3DXIFF_PNG, &s, NULL, &r), D3DERR_INVALIDCALL);
    CHECK_EQ(D3DXSaveTextureToFileA(NULL, D3DXIFF_PNG, &t, NULL), D3DERR_INVALIDCALL);
    /* through the import slot as the exe binds it */
    CHECK(fnvnx_win32_lookup_import("d3dx9_38.dll", "D3DXSaveTextureToFileA", FNVNX_IMPORT_BY_NAME) ==
          (void *)&D3DXSaveTextureToFileA);
    CHECK_EQ(s.locks, 0);
}
