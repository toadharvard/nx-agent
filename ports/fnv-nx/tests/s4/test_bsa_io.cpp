/* test_bsa_io.cpp - the game's archive I/O through the platform Win32 API: a synthetic BSA v104 (the public layout:
 * "BSA\0", version 0x68, folder records, file records, name block, raw data; no game data) is read back the way an
 * archive reader does, and a sparse 5 GiB file proves the 32-bit/64-bit offset contracts of SetFilePointer, GetFileSize
 * and ReadFile (incl. OVERLAPPED on a synchronous handle). The exe imports ReadFile, WriteFile, CreateFileA/W,
 * GetFileSize(Ex), SetFilePointer(Ex), FindFirst/NextFileA, GetFileAttributesA, SetEndOfFile, FlushFileBuffers - no
 * ReadFileEx/WriteFileEx/GetOverlappedResult/CreateIoCompletionPort and no file-mapping API. */
#include "s4_test.h"

#include <stdlib.h>
#include <stdint.h>
#include <vector>
#include <string>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "fnvnx_msvcrt.h"

#ifndef FILE_FLAG_SEQUENTIAL_SCAN
#define FILE_FLAG_SEQUENTIAL_SCAN 0x08000000
#endif
#ifndef FILE_FLAG_RANDOM_ACCESS
#define FILE_FLAG_RANDOM_ACCESS 0x10000000
#endif
#ifndef FILE_FLAG_NO_BUFFERING
#define FILE_FLAG_NO_BUFFERING 0x20000000
#endif

extern "C" void fnvnx_path_reset(const char *root);

namespace {

struct Entry { std::string folder, name; uint32_t size, off; };

std::vector<uint8_t> g_bsa;
std::vector<Entry> g_entries;
std::string g_root;

uint8_t pat(unsigned idx, uint32_t off) { return (uint8_t)(idx * 37u + off * 131u + (off >> 8) * 17u + 5u); }

void put32(std::vector<uint8_t> &v, uint32_t x) { for (int i = 0; i < 4; i++) v.push_back((uint8_t)(x >> (8 * i))); }
void put64(std::vector<uint8_t> &v, uint64_t x) { put32(v, (uint32_t)x); put32(v, (uint32_t)(x >> 32)); }

/* the reader under test never validates the hash field; a stable mix keeps the records distinct */
uint64_t mixhash(const std::string &s)
{
    uint64_t h = 1469598103934665603ull;
    for (char c : s) { h ^= (uint8_t)c; h *= 1099511628211ull; }
    return h;
}

void build_bsa()
{
    g_bsa.clear();
    g_entries.clear();
    static const struct { const char *folder; const char *name; uint32_t size; } spec[] = {
        { "meshes", "a.nif", 1 },       { "meshes", "b.nif", 511 },     { "meshes", "c.nif", 512 },
        { "meshes", "d.nif", 513 },     { "textures", "e.dds", 4095 },  { "textures", "f.dds", 4096 },
        { "textures", "g.dds", 70000 }, { "sound", "h.wav", 0 },        { "sound", "i.wav", 33333 },
    };
    const char *folders[] = { "meshes", "textures", "sound" };
    uint32_t nfolders = 3, nfiles = 9, fnames = 0, dnames = 0;
    for (auto &s : spec) fnames += (uint32_t)strlen(s.name) + 1;
    for (auto f : folders) dnames += (uint32_t)strlen(f) + 1;
    /* header 36 bytes, then 16-byte folder records, then per folder: bzstring name + 16-byte file records */
    uint32_t pos = 36 + 16 * nfolders;
    uint32_t folder_off[3];
    for (int fi = 0; fi < 3; fi++) {
        folder_off[fi] = pos + fnames;   /* v104: the offset counts the file-name block (as the format does) */
        uint32_t cnt = 0;
        for (auto &s : spec) if (!strcmp(s.folder, folders[fi])) cnt++;
        pos += 1 + (uint32_t)strlen(folders[fi]) + 1 + 16 * cnt;
    }
    uint32_t data_start = pos + fnames;
    std::vector<uint8_t> &v = g_bsa;
    v.push_back('B'); v.push_back('S'); v.push_back('A'); v.push_back(0);
    put32(v, 0x68);
    put32(v, 36);                 /* folder record offset */
    put32(v, 0x3);                /* archive flags: folder + file names */
    put32(v, nfolders);
    put32(v, nfiles);
    put32(v, dnames);             /* total folder name length */
    put32(v, fnames);             /* total file name length */
    put32(v, 0x1);                /* file flags (u16) + pad */
    for (int fi = 0; fi < 3; fi++) {
        uint32_t cnt = 0;
        for (auto &s : spec) if (!strcmp(s.folder, folders[fi])) cnt++;
        put64(v, mixhash(folders[fi]));
        put32(v, cnt);
        put32(v, folder_off[fi]);
    }
    uint32_t doff = data_start;
    for (int fi = 0; fi < 3; fi++) {
        v.push_back((uint8_t)(strlen(folders[fi]) + 1));
        for (const char *c = folders[fi]; *c; c++) v.push_back((uint8_t)*c);
        v.push_back(0);
        for (auto &s : spec) {
            if (strcmp(s.folder, folders[fi])) continue;
            put64(v, mixhash(s.name));
            put32(v, s.size);
            put32(v, doff);
            g_entries.push_back({ s.folder, s.name, s.size, doff });
            doff += s.size;
        }
    }
    /* the name block lists the files in the order of the records, which is the spec order (already grouped) */
    for (auto &e : g_entries) { for (char c : e.name) v.push_back((uint8_t)c); v.push_back(0); }
    for (unsigned i = 0; i < g_entries.size(); i++)
        for (uint32_t o = 0; o < g_entries[i].size; o++) v.push_back(pat(i, o));
}

void host_write(const std::string &rel, const void *data, size_t n)
{
    std::string p = g_root + "/" + rel;
    FILE *f = fopen(p.c_str(), "wb");
    CHECK(f != NULL);
    if (f) { CHECK(fwrite(data, 1, n, f) == n); fclose(f); }
}

void setup_root()
{
    g_root = std::string(s4_root()) + "/bsaio";
    std::string cmd = "rm -rf '" + g_root + "' && mkdir -p '" + g_root + "/Data'";
    CHECK(system(cmd.c_str()) == 0);
    fnvnx_path_reset(g_root.c_str());
}

void restore_root() { fnvnx_path_reset(s4_root()); }

bool read_exact(HANDLE h, void *buf, DWORD n)
{
    DWORD got = 0;
    return ReadFile(h, buf, n, &got, NULL) && got == n;
}

DWORD rd32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((DWORD)p[3] << 24); }

/* parse the whole archive the way a reader does and verify every file's bytes; chunk is the read size for file data */
bool read_archive(HANDLE h, DWORD chunk)
{
    uint8_t hdr[36];
    DWORD p0 = SetFilePointer(h, 0, NULL, FILE_BEGIN);
    if (p0 != 0 || !read_exact(h, hdr, 36)) return false;
    if (memcmp(hdr, "BSA\0", 4) || rd32(hdr + 4) != 0x68) return false;
    DWORD nfolders = rd32(hdr + 16), nfiles = rd32(hdr + 20), fnames = rd32(hdr + 28);
    if (nfolders != 3 || nfiles != 9) return false;
    std::vector<uint8_t> fr(16 * nfolders);
    if (!read_exact(h, fr.data(), (DWORD)fr.size())) return false;
    std::vector<Entry> got;
    for (DWORD fi = 0; fi < nfolders; fi++) {
        DWORD cnt = rd32(&fr[fi * 16 + 8]);
        uint8_t nl;
        if (!read_exact(h, &nl, 1)) return false;           /* mixed sizes: 1, nl, 16*cnt */
        std::string nm(nl, 0);
        if (!read_exact(h, &nm[0], nl)) return false;
        nm.resize(nl - 1);
        std::vector<uint8_t> recs(16 * cnt);
        if (!read_exact(h, recs.data(), (DWORD)recs.size())) return false;
        for (DWORD k = 0; k < cnt; k++) got.push_back({ nm, "", rd32(&recs[k * 16 + 8]), rd32(&recs[k * 16 + 12]) });
    }
    std::vector<char> names(fnames);
    if (!read_exact(h, names.data(), fnames)) return false;
    const char *np = names.data();
    for (auto &e : got) { e.name = np; np += e.name.size() + 1; }
    if (got.size() != g_entries.size()) return false;
    for (unsigned i = 0; i < got.size(); i++) {
        if (got[i].folder != g_entries[i].folder || got[i].name != g_entries[i].name || got[i].size != g_entries[i].size ||
            got[i].off != g_entries[i].off)
            return false;
        /* file data: seek by the record's offset, read in `chunk`-sized pieces across the record boundary */
        if (SetFilePointer(h, (LONG)got[i].off, NULL, FILE_BEGIN) != got[i].off) return false;
        std::vector<uint8_t> buf(got[i].size + 64);
        DWORD done = 0;
        while (done < got[i].size) {
            DWORD want = got[i].size - done < chunk ? got[i].size - done : chunk;
            if (!read_exact(h, &buf[done], want)) return false;
            done += want;
        }
        for (uint32_t o = 0; o < got[i].size; o++)
            if (buf[o] != pat(i, o)) return false;
    }
    return true;
}

DWORD reader_thread(LPVOID arg)
{
    DWORD flags = (DWORD)(uintptr_t)arg;
    DWORD ok = 1;
    for (int round = 0; round < 40 && ok; round++) {
        HANDLE h = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, flags, NULL);
        if (h == INVALID_HANDLE_VALUE) return 0;
        ok = read_archive(h, round % 2 ? 4096 : 777) ? 1 : 0;
        CloseHandle(h);
    }
    return ok;
}

} // namespace

TEST(bsa_synthetic_archive_read)
{
    setup_root();
    build_bsa();
    host_write("Data/Fallout - Test.bsa", g_bsa.data(), g_bsa.size());
    static const DWORD flagsets[] = { 0, FILE_FLAG_SEQUENTIAL_SCAN, FILE_FLAG_RANDOM_ACCESS,
                                      FILE_FLAG_SEQUENTIAL_SCAN | FILE_FLAG_RANDOM_ACCESS };
    for (DWORD fl : flagsets) {
        HANDLE h = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, fl, NULL);
        CHECK(h != INVALID_HANDLE_VALUE);
        if (h == INVALID_HANDLE_VALUE) continue;
        CHECK(read_archive(h, 1));
        CHECK(read_archive(h, 4096));
        CHECK(read_archive(h, 100000));
        CloseHandle(h);
    }
    restore_root();
}

TEST(bsa_size_pointer_eof)
{
    setup_root();
    build_bsa();
    host_write("Data/Fallout - Test.bsa", g_bsa.data(), g_bsa.size());
    HANDLE h = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD hi = 0xdead;
    CHECK_EQ(GetFileSize(h, &hi), g_bsa.size());
    CHECK_EQ(hi, 0);
    CHECK_EQ(GetFileSize(h, NULL), g_bsa.size());
    LARGE_INTEGER s;
    CHECK(GetFileSizeEx(h, &s));
    CHECK_EQ(s.QuadPart, (long long)g_bsa.size());
    /* SetFilePointer with a non-NULL high part */
    LONG high = 0;
    CHECK_EQ(SetFilePointer(h, 100, &high, FILE_BEGIN), 100);
    CHECK_EQ(high, 0);
    high = 0;
    CHECK_EQ(SetFilePointer(h, 0, &high, FILE_END), g_bsa.size());
    CHECK_EQ(high, 0);
    high = -1;                                   /* END, -(size) as 64-bit: position 0 */
    CHECK_EQ(SetFilePointer(h, -(LONG)g_bsa.size(), &high, FILE_END), 0);
    CHECK_EQ(high, 0);
    high = 0;
    CHECK_EQ(SetFilePointer(h, 10, &high, FILE_BEGIN), 10);
    high = 0;
    CHECK_EQ(SetFilePointer(h, 5, &high, FILE_CURRENT), 15);
    /* SetFilePointerEx */
    LARGE_INTEGER d, np;
    d.QuadPart = 36;
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK_EQ(np.QuadPart, 36);
    d.QuadPart = -6;
    CHECK(SetFilePointerEx(h, d, &np, FILE_END));
    CHECK_EQ(np.QuadPart, (long long)g_bsa.size() - 6);
    d.QuadPart = -1;
    CHECK(!SetFilePointerEx(h, d, &np, FILE_BEGIN));        /* negative position */
    CHECK_EQ(GetLastError(), 131);                          /* ERROR_NEGATIVE_SEEK */
    /* short read at EOF: success with fewer bytes, then success with 0 bytes */
    d.QuadPart = -10;
    CHECK(SetFilePointerEx(h, d, NULL, FILE_END));
    uint8_t buf[64];
    DWORD got = 99;
    CHECK(ReadFile(h, buf, 64, &got, NULL));
    CHECK_EQ(got, 10);
    CHECK(memcmp(buf, g_bsa.data() + g_bsa.size() - 10, 10) == 0);
    got = 99;
    CHECK(ReadFile(h, buf, 64, &got, NULL));
    CHECK_EQ(got, 0);
    got = 99;
    CHECK(ReadFile(h, buf, 0, &got, NULL));                 /* zero-length read */
    CHECK_EQ(got, 0);
    /* OVERLAPPED on a synchronous handle: read at Offset/OffsetHigh, the file pointer is updated */
    OVERLAPPED ov;
    memset(&ov, 0, sizeof ov);
    ov.Offset = 40;
    ov.OffsetHigh = 0;
    got = 0;
    CHECK(ReadFile(h, buf, 20, &got, &ov));
    CHECK_EQ(got, 20);
    CHECK(memcmp(buf, g_bsa.data() + 40, 20) == 0);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), 60);
    CHECK_EQ(ov.InternalHigh, 20);
    memset(&ov, 0, sizeof ov);
    ov.Offset = (DWORD)g_bsa.size() - 4;                    /* partial at EOF through OVERLAPPED */
    got = 0;
    CHECK(ReadFile(h, buf, 16, &got, &ov));
    CHECK_EQ(got, 4);
    CHECK_EQ(SetFilePointer(h, 0, NULL, FILE_CURRENT), g_bsa.size());
    memset(&ov, 0, sizeof ov);
    ov.Offset = (DWORD)g_bsa.size();                        /* exactly at EOF: ERROR_HANDLE_EOF */
    got = 99;
    CHECK(!ReadFile(h, buf, 16, &got, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CHECK_EQ(got, 0);
    CloseHandle(h);
    /* the ordinary pointer is independent per handle */
    HANDLE a = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    HANDLE b = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(a != INVALID_HANDLE_VALUE && b != INVALID_HANDLE_VALUE);
    SetFilePointer(a, 50, NULL, FILE_BEGIN);
    CHECK_EQ(SetFilePointer(b, 0, NULL, FILE_CURRENT), 0);
    CloseHandle(a);
    CloseHandle(b);
    restore_root();
}

TEST(bsa_no_buffering)
{
    setup_root();
    build_bsa();
    host_write("Data/Fallout - Test.bsa", g_bsa.data(), g_bsa.size());
    HANDLE h = CreateFileA("Data\\Fallout - Test.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    uint8_t *buf = (uint8_t *)aligned_alloc(4096, 8192);
    DWORD got = 0;
    CHECK(ReadFile(h, buf, 4096, &got, NULL));              /* sector-aligned buffer, length, offset */
    CHECK_EQ(got, 4096);
    CHECK(memcmp(buf, g_bsa.data(), 4096) == 0);
    CHECK(!ReadFile(h, buf + 1, 512, &got, NULL));          /* unaligned buffer: ERROR_INVALID_PARAMETER */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!ReadFile(h, buf, 100, &got, NULL));              /* length not a sector multiple */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* the final partial sector reads short, success */
    DWORD last = (DWORD)(g_bsa.size() / 512) * 512;
    LARGE_INTEGER d;
    d.QuadPart = last;
    CHECK(SetFilePointerEx(h, d, NULL, FILE_BEGIN));
    CHECK(ReadFile(h, buf, 4096, &got, NULL));
    CHECK_EQ(got, g_bsa.size() - last);
    free(buf);
    CloseHandle(h);
    restore_root();
}

TEST(bsa_two_threads_two_handles)
{
    setup_root();
    build_bsa();
    host_write("Data/Fallout - Test.bsa", g_bsa.data(), g_bsa.size());
    DWORD tid;
    HANDLE t1 = test_CreateThread(NULL, 0, reader_thread, (LPVOID)(uintptr_t)0, 0, &tid);
    HANDLE t2 = test_CreateThread(NULL, 0, reader_thread, (LPVOID)(uintptr_t)FILE_FLAG_RANDOM_ACCESS, 0, &tid);
    HANDLE t3 = test_CreateThread(NULL, 0, reader_thread, (LPVOID)(uintptr_t)FILE_FLAG_SEQUENTIAL_SCAN, 0, &tid);
    CHECK(t1 && t2 && t3);
    HANDLE all[3] = { t1, t2, t3 };
    CHECK_EQ(WaitForMultipleObjects(3, all, TRUE, 60000), WAIT_OBJECT_0);
    DWORD rc = 0;
    for (HANDLE t : all) { GetExitCodeThread(t, &rc); CHECK_EQ(rc, 1); CloseHandle(t); }
    restore_root();
}

TEST(bsa_find_attributes_spellings)
{
    setup_root();
    build_bsa();
    host_write("Data/Fallout - Test.bsa", g_bsa.data(), g_bsa.size());
    host_write("Data/FALLOUT - Other.BSA", "xyz", 3);
    host_write("Data/falloutnv.esm", "e", 1);
    WIN32_FIND_DATAA fd;
    int seen_test = 0, seen_other = 0, n = 0;
    HANDLE f = FindFirstFileA("Data\\*.bsa", &fd);
    CHECK(f != INVALID_HANDLE_VALUE);
    if (f != INVALID_HANDLE_VALUE) {
        do {
            n++;
            if (!strcmp(fd.cFileName, "Fallout - Test.bsa")) {
                seen_test++;
                CHECK_EQ(fd.nFileSizeHigh, 0);
                CHECK_EQ(fd.nFileSizeLow, g_bsa.size());
                CHECK(!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY));
            } else if (!strcmp(fd.cFileName, "FALLOUT - Other.BSA")) {
                seen_other++;
                CHECK_EQ(fd.nFileSizeLow, 3);
            }
        } while (FindNextFileA(f, &fd));
        CHECK_EQ(GetLastError(), ERROR_NO_MORE_FILES);
        CHECK(FindClose(f));
    }
    CHECK_EQ(n, 2);
    CHECK_EQ(seen_test, 1);
    CHECK_EQ(seen_other, 1);
    f = FindFirstFileA("data\\FALLOUT*.BSA", &fd);          /* wildcard and directory spelled in another case */
    CHECK(f != INVALID_HANDLE_VALUE);
    if (f != INVALID_HANDLE_VALUE) FindClose(f);
    CHECK(FindFirstFileA("Data\\*.zzz", &fd) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    /* attributes */
    DWORD a = GetFileAttributesA("Data\\Fallout - Test.bsa");
    CHECK(a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY));
    a = GetFileAttributesA("DATA");
    CHECK(a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY));
    CHECK_EQ(GetFileAttributesA("Data\\nope.bsa"), INVALID_FILE_ATTRIBUTES);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    /* spellings */
    static const char *spell[] = { "DATA\\fallout - test.BSA", "Data/Fallout - Test.bsa", "data/FALLOUT - TEST.bsa",
                                   ".\\Data\\Fallout - Test.bsa", "Data\\\\Fallout - Test.bsa",
                                   "C:\\FalloutNV\\Data\\Fallout - Test.bsa", "c:/falloutnv/data/fallout - test.bsa" };
    for (const char *sp : spell) {
        HANDLE h = CreateFileA(sp, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
        CHECK(h != INVALID_HANDLE_VALUE);
        if (h == INVALID_HANDLE_VALUE) { fprintf(stderr, "    spelling failed: %s\n", sp); continue; }
        uint8_t m[4];
        CHECK(read_exact(h, m, 4) && !memcmp(m, "BSA\0", 4));
        CloseHandle(h);
        CHECK(GetFileAttributesA(sp) != INVALID_FILE_ATTRIBUTES);
    }
    /* a missing archive: ERROR_FILE_NOT_FOUND; in a missing directory: ERROR_PATH_NOT_FOUND */
    CHECK(CreateFileA("Data\\Fallout - Nope.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(CreateFileA("Nodir\\x.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_PATH_NOT_FOUND);
    restore_root();
}

TEST(bsa_large_offsets_5gib)
{
    setup_root();
    const uint64_t SIZE = 5ull << 30;
    const uint64_t M1 = 0x7FFFFFF0ull, M2 = 0x80000010ull, M3 = 0x100000010ull;   /* below 2G, above 2G, above 4G */
    std::string p = g_root + "/Data/Fallout - Big.bsa";
    int fd = open(p.c_str(), O_CREAT | O_RDWR | O_TRUNC, 0644);
    bool ok = fd >= 0 && ftruncate(fd, (off_t)SIZE) == 0;
    if (ok) {
        struct stat st;
        ok = fstat(fd, &st) == 0 && (uint64_t)st.st_size == SIZE;
    }
    if (!ok) {
        fprintf(stderr, "  SKIP bsa_large_offsets_5gib: the host file system cannot make a 5 GiB sparse file\n");
        if (fd >= 0) close(fd);
        unlink(p.c_str());
        restore_root();
        return;
    }
    const char m1[8] = { 'L','O','W','2','G','b','1','!' }, m2[8] = { 'H','I','G','H','2','G','b','2' },
               m3[8] = { 'A','B','O','V','E','4','G','3' };
    CHECK(pwrite(fd, m1, 8, (off_t)M1) == 8);
    CHECK(pwrite(fd, m2, 8, (off_t)M2) == 8);
    CHECK(pwrite(fd, m3, 8, (off_t)M3) == 8);
    close(fd);

    HANDLE h = CreateFileA("DATA\\fallout - big.bsa", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_FLAG_RANDOM_ACCESS, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    if (h == INVALID_HANDLE_VALUE) { unlink(p.c_str()); restore_root(); return; }
    char b[8];
    /* sizes */
    DWORD shi = 0;
    CHECK_EQ((uint32_t)GetFileSize(h, &shi), (uint32_t)(SIZE & 0xFFFFFFFFu));
    CHECK_EQ(shi, 1);
    CHECK_EQ((uint32_t)GetFileSize(h, NULL), 0xFFFFFFFFu);      /* does not fit one DWORD */
    LARGE_INTEGER s;
    CHECK(GetFileSizeEx(h, &s));
    CHECK_EQ(s.QuadPart, (long long)SIZE);
    /* SetFilePointer FILE_BEGIN: high part given */
    LONG hi = 0;
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)M1, &hi, FILE_BEGIN), (uint32_t)M1);
    CHECK_EQ(hi, 0);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m1, 8));
    hi = 0;                                                  /* 0x80000010 as a LONG is negative; the high part (0) makes it 64-bit positive */
    SetLastError(0xBEEF);
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)(uint32_t)M2, &hi, FILE_BEGIN), (uint32_t)M2);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK_EQ(hi, 0);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m2, 8));
    hi = (LONG)(M3 >> 32);
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)(uint32_t)M3, &hi, FILE_BEGIN), (uint32_t)(M3 & 0xFFFFFFFFu));
    CHECK_EQ(hi, 1);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m3, 8));
    /* without a high part the low is signed: 0x80000010 is -2^31+16, a negative position, fails */
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)(uint32_t)M2, NULL, FILE_BEGIN), 0xFFFFFFFFu);
    CHECK_EQ(GetLastError(), 131);
    /* the INVALID_SET_FILE_POINTER ambiguity: a real position 0xFFFFFFFF succeeds with GetLastError()==ERROR_SUCCESS */
    hi = 0;
    SetLastError(0xBEEF);
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)0xFFFFFFFFu, &hi, FILE_BEGIN), 0xFFFFFFFFu);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK_EQ(hi, 0);
    /* a real failure: negative position */
    hi = -1;
    SetLastError(ERROR_SUCCESS);
    CHECK_EQ((uint32_t)SetFilePointer(h, -5, &hi, FILE_BEGIN), 0xFFFFFFFFu);
    CHECK(GetLastError() != ERROR_SUCCESS);
    /* FILE_CURRENT across 2 GiB and 4 GiB */
    hi = 0;
    SetFilePointer(h, (LONG)M1, &hi, FILE_BEGIN);
    hi = 0;                                                  /* +0x20 -> 0x80000010 */
    CHECK_EQ((uint32_t)SetFilePointer(h, 0x20, &hi, FILE_CURRENT), (uint32_t)M2);
    CHECK_EQ(hi, 0);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m2, 8));        /* pointer now M2+8 */
    hi = 0;                                                  /* +(M3 - M2 - 8) */
    CHECK_EQ((uint32_t)SetFilePointer(h, (LONG)(M3 - M2 - 8), &hi, FILE_CURRENT), (uint32_t)M3);
    CHECK_EQ(hi, 1);
    hi = -1;                                                 /* -0x10 as a 64-bit distance */
    CHECK_EQ((uint32_t)SetFilePointer(h, -0x10, &hi, FILE_CURRENT), (uint32_t)(M3 - 0x10));
    CHECK_EQ(hi, 1);
    /* FILE_END */
    hi = -1;
    CHECK_EQ((uint32_t)SetFilePointer(h, -16, &hi, FILE_END), (uint32_t)((SIZE - 16) & 0xFFFFFFFFu));
    CHECK_EQ(hi, 1);
    hi = 0;
    CHECK_EQ((uint32_t)SetFilePointer(h, 0, &hi, FILE_END), (uint32_t)(SIZE & 0xFFFFFFFFu));
    CHECK_EQ(hi, 1);
    {
        DWORD got = 77;
        CHECK(ReadFile(h, b, 8, &got, NULL));                /* at the end of a 5 GiB file: 0 bytes, success */
        CHECK_EQ(got, 0);
    }
    CHECK_EQ((uint32_t)SetFilePointer(h, 0, NULL, FILE_END), 0xFFFFFFFFu);   /* 5 GiB does not fit without a high part */
    /* SetFilePointerEx */
    LARGE_INTEGER d, np;
    d.QuadPart = (long long)M3;
    CHECK(SetFilePointerEx(h, d, &np, FILE_BEGIN));
    CHECK_EQ(np.QuadPart, (long long)M3);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m3, 8));
    d.QuadPart = -(long long)(SIZE - M2);
    CHECK(SetFilePointerEx(h, d, &np, FILE_END));
    CHECK_EQ(np.QuadPart, (long long)M2);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m2, 8));
    d.QuadPart = -(long long)(M2 + 8 - M1);
    CHECK(SetFilePointerEx(h, d, &np, FILE_CURRENT));
    CHECK_EQ(np.QuadPart, (long long)M1);
    CHECK(read_exact(h, b, 8) && !memcmp(b, m1, 8));
    /* a read that straddles the 2 GiB boundary: M1 marker, zeros, then the marker M2 */
    d.QuadPart = (long long)M1;
    SetFilePointerEx(h, d, NULL, FILE_BEGIN);
    std::vector<char> big((size_t)(M2 + 8 - M1));
    CHECK(read_exact(h, big.data(), (DWORD)big.size()));
    CHECK(!memcmp(big.data(), m1, 8) && !memcmp(&big[M2 - M1], m2, 8));
    /* OVERLAPPED reads on the synchronous handle with OffsetHigh */
    OVERLAPPED ov;
    DWORD got = 0;
    memset(&ov, 0, sizeof ov);
    ov.Offset = (DWORD)(M3 & 0xFFFFFFFFu);
    ov.OffsetHigh = (DWORD)(M3 >> 32);
    CHECK(ReadFile(h, b, 8, &got, &ov));
    CHECK_EQ(got, 8);
    CHECK(!memcmp(b, m3, 8));
    d.QuadPart = 0;
    SetFilePointerEx(h, d, &np, FILE_CURRENT);
    CHECK_EQ(np.QuadPart, (long long)M3 + 8);               /* the pointer moved to Offset + count */
    memset(&ov, 0, sizeof ov);
    ov.Offset = (uint32_t)(SIZE & 0xFFFFFFFFu) - 4;
    ov.OffsetHigh = (DWORD)(SIZE >> 32);
    got = 0;
    CHECK(ReadFile(h, b, 8, &got, &ov));                    /* partial: 4 bytes to the end */
    CHECK_EQ(got, 4);
    memset(&ov, 0, sizeof ov);
    ov.Offset = (uint32_t)(SIZE & 0xFFFFFFFFu);
    ov.OffsetHigh = (DWORD)(SIZE >> 32);
    CHECK(!ReadFile(h, b, 8, &got, &ov));
    CHECK_EQ(GetLastError(), ERROR_HANDLE_EOF);
    CloseHandle(h);
    /* FindFirstFile reports the 64-bit size */
    WIN32_FIND_DATAA fd2;
    HANDLE f = FindFirstFileA("Data\\*.bsa", &fd2);
    CHECK(f != INVALID_HANDLE_VALUE);
    if (f != INVALID_HANDLE_VALUE) {
        CHECK_STR(fd2.cFileName, "Fallout - Big.bsa");
        CHECK_EQ(fd2.nFileSizeHigh, 1);
        CHECK_EQ(fd2.nFileSizeLow, (uint32_t)(SIZE & 0xFFFFFFFFu));
        FindClose(f);
    }
    unlink(p.c_str());
    restore_root();
}
