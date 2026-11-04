/* test_mmio.cpp - winmm mmio (platform/win32/mmio.cpp): RIFF chunk walking as the game's WAV loader does it, with
 * the DOS and MEM procedures and a custom I/O procedure that behaves like the game's (state in adwInfo[0], position
 * in lDiskOffset, MMIOM_SEEK returns 0). */
#include "s4_test.h"
#include "fnvnx_mmio.h"

#include <stdint.h>
#include <stdlib.h>
#include <vector>

namespace {
void put32(std::vector<char> &v, uint32_t x)
{
    for (int i = 0; i < 4; i++)
        v.push_back((char)(x >> (8 * i)));
}
void put4(std::vector<char> &v, const char *s) { v.insert(v.end(), s, s + 4); }

/* RIFF WAVE { fmt 16, LIST INFO { ISFT 5 + pad }, junk 300, data 37 + pad } */
std::vector<char> wave()
{
    std::vector<char> body;
    put4(body, "WAVE");
    put4(body, "fmt ");
    put32(body, 16);
    for (int i = 0; i < 16; i++)
        body.push_back((char)(0x10 + i));
    std::vector<char> list;
    put4(list, "INFO");
    put4(list, "ISFT");
    put32(list, 5);
    for (char c : {'a', 'b', 'c', 'd', '\0', '\0'})
        list.push_back(c);
    put4(body, "LIST");
    put32(body, (uint32_t)list.size());
    body.insert(body.end(), list.begin(), list.end());
    put4(body, "junk");
    put32(body, 300);
    body.insert(body.end(), 300, 'j');
    put4(body, "data");
    put32(body, 37);
    for (int i = 0; i < 37; i++)
        body.push_back((char)i);
    body.push_back(0);   /* pad */
    std::vector<char> f;
    put4(f, "RIFF");
    put32(f, (uint32_t)body.size());
    f.insert(f.end(), body.begin(), body.end());
    return f;
}

void write_bin(const char *path, const std::vector<char> &d)
{
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    DWORD put = 0;
    CHECK(WriteFile(h, d.data(), (DWORD)d.size(), &put, NULL));
    CloseHandle(h);
}

/* the walk the WAV loader does: RIFF/WAVE, fmt, (skip to) data, then the samples by mmioRead (direct 0) or by direct
 * buffer access (1; 2: a caller's buffer, without the guard byte) */
void walk(HMMIO h, int direct)
{
    MMCKINFO riff = {}, ck = {};
    CHECK_EQ(mmioDescend(h, &riff, NULL, 0), 0);
    CHECK(riff.ckid == FOURCC_RIFF && riff.fccType == FNVNX_FOURCC('W', 'A', 'V', 'E'));
    CHECK_EQ(riff.dwDataOffset, 8);
    ck.ckid = FNVNX_FOURCC('f', 'm', 't', ' ');
    CHECK_EQ(mmioDescend(h, &ck, &riff, MMIO_FINDCHUNK), 0);
    CHECK_EQ(ck.cksize, 16);
    CHECK_EQ(ck.dwDataOffset, 20);
    char fmt[16];
    CHECK_EQ(mmioRead(h, fmt, 16), 16);
    CHECK(fmt[0] == 0x10 && fmt[15] == 0x1f);
    CHECK_EQ(mmioAscend(h, &ck, 0), 0);
    /* a chunk that is not there: the end of the parent */
    MMCKINFO none = {};
    none.ckid = FNVNX_FOURCC('c', 'u', 'e', ' ');
    MMCKINFO mark = ck;
    CHECK_EQ(mmioDescend(h, &none, &riff, MMIO_FINDCHUNK), MMIOERR_CHUNKNOTFOUND);
    CHECK_EQ(mmioSeek(h, (LONG)(mark.dwDataOffset + 16), SEEK_SET), 36);   /* back after fmt */
    MMCKINFO info = {};
    info.fccType = FNVNX_FOURCC('I', 'N', 'F', 'O');
    CHECK_EQ(mmioDescend(h, &info, &riff, MMIO_FINDLIST), 0);
    CHECK(info.ckid == FOURCC_LIST && info.cksize == 18);
    MMCKINFO sft = {};
    CHECK_EQ(mmioDescend(h, &sft, &info, 0), 0);
    CHECK(sft.ckid == FNVNX_FOURCC('I', 'S', 'F', 'T') && sft.cksize == 5);
    CHECK_EQ(mmioAscend(h, &sft, 0), 0);   /* odd size: padded */
    CHECK_EQ(mmioSeek(h, 0, SEEK_CUR), (LONG)(sft.dwDataOffset + 6));
    CHECK_EQ(mmioDescend(h, &none, &info, MMIO_FINDCHUNK), MMIOERR_CHUNKNOTFOUND);
    CHECK_EQ(mmioAscend(h, &info, 0), 0);
    ck.ckid = FNVNX_FOURCC('d', 'a', 't', 'a');
    CHECK_EQ(mmioDescend(h, &ck, &riff, MMIO_FINDCHUNK), 0);   /* skips the 300-byte junk chunk */
    CHECK_EQ(ck.cksize, 37);
    char got[64];
    LONG n = 0;
    if (direct) {
        MMIOINFO io;
        CHECK_EQ(mmioGetInfo(h, &io, 0), 0);
        while (n < 37) {
            if (io.pchNext == io.pchEndRead) {
                CHECK_EQ(mmioAdvance(h, &io, MMIO_READ), 0);
                if (io.pchNext == io.pchEndRead)
                    break;
            }
            got[n++] = *io.pchNext++;
        }
        /* past the data: the pad byte, then EOF: 0 with nothing to read */
        for (int guard = 0; guard < 4 && io.pchNext != io.pchEndRead; guard++)
            io.pchNext++;
        CHECK_EQ(mmioAdvance(h, &io, MMIO_READ), 0);
        CHECK(io.pchNext == io.pchEndRead);
        CHECK_EQ(mmioAdvance(h, &io, MMIO_READ), 0);
        CHECK(io.pchNext == io.pchEndRead);
        if (direct == 1)   /* an allocated buffer (not a memory file's): the zeroed guard byte */
            CHECK_EQ(io.pchNext[0], 0);
    } else {
        n = mmioRead(h, got, 37);
        CHECK_EQ(mmioRead(h, got + 40, 10), 1);   /* the pad byte, then EOF */
        CHECK_EQ(mmioRead(h, got + 40, 10), 0);
    }
    CHECK_EQ(n, 37);
    bool same = true;
    for (int i = 0; i < 37; i++)
        same = same && got[i] == (char)i;
    CHECK(same);
    CHECK_EQ(mmioDescend(h, &none, NULL, MMIO_FINDCHUNK), MMIOERR_CHUNKNOTFOUND);   /* EOF */
}

/* an I/O procedure like the game's 0x00af0400: its file in adwInfo[0], the position in lDiskOffset, SEEK -> 0 */
struct memfile { const std::vector<char> *d; int opens, closes, reads, seeks; };
LRESULT game_like_proc(LPSTR p, UINT msg, LPARAM l1, LPARAM l2)
{
    MMIOINFO *i = (MMIOINFO *)p;
    memfile *f = (memfile *)(uintptr_t)i->adwInfo[0];
    switch (msg) {
    case MMIOM_OPEN:
        f->opens++;
        i->lDiskOffset = 0;
        return 0;
    case MMIOM_CLOSE:
        f->closes++;
        return 0;
    case MMIOM_READ: {
        f->reads++;
        LONG left = (LONG)f->d->size() - i->lDiskOffset, n = left < (LONG)l2 ? left : (LONG)l2;
        if (n < 0)
            n = 0;
        memcpy((void *)l1, f->d->data() + i->lDiskOffset, (size_t)n);
        i->lDiskOffset += n;
        return n;
    }
    case MMIOM_SEEK:
        f->seeks++;
        if (l2 == SEEK_SET)
            i->lDiskOffset = (LONG)l1;
        else if (l2 == SEEK_CUR)
            i->lDiskOffset += (LONG)l1;
        else
            i->lDiskOffset = (LONG)f->d->size() + (LONG)l1;
        return 0;
    }
    return -1;
}
} // namespace

TEST(mmio_dos_file_chunks)
{
    std::vector<char> w = wave();
    write_bin("C:\\FalloutNV\\test.wav", w);
    for (int direct = 0; direct < 2; direct++) {
        MMIOINFO in = {};
        in.cchBuffer = 16;   /* small: every walk crosses buffers */
        HMMIO h = mmioOpenA((LPSTR) "C:\\FalloutNV\\test.wav", &in, MMIO_READ | MMIO_ALLOCBUF);
        CHECK(h != NULL);
        if (!h)
            return;
        walk(h, direct);
        CHECK_EQ(mmioSeek(h, 0, SEEK_END), (LONG)w.size());
        CHECK_EQ(mmioSeek(h, -4, SEEK_CUR), (LONG)w.size() - 4);
        CHECK_EQ(mmioClose(h, 0), 0);
    }
    /* unbuffered (no MMIO_ALLOCBUF): the same walk through the procedure; Advance needs a buffer */
    HMMIO h = mmioOpenA((LPSTR) "C:\\FalloutNV\\test.wav", NULL, MMIO_READ);
    CHECK(h != NULL);
    walk(h, 0);
    MMIOINFO io;
    CHECK_EQ(mmioGetInfo(h, &io, 0), 0);
    CHECK_EQ(mmioAdvance(h, &io, MMIO_READ), MMIOERR_UNBUFFERED);
    CHECK_EQ(mmioClose(h, 0), 0);
    /* errors */
    MMIOINFO e = {};
    CHECK(mmioOpenA((LPSTR) "C:\\FalloutNV\\missing.wav", &e, MMIO_READ) == NULL);
    CHECK_EQ(e.wErrorRet, MMIOERR_FILENOTFOUND);
    CHECK(mmioOpenA((LPSTR) "C:\\FalloutNV\\test.wav", NULL, MMIO_EXIST) != NULL);
    CHECK_EQ(mmioClose(NULL, 0), MMSYSERR_INVALHANDLE);
    CHECK_EQ(mmioRead(NULL, NULL, 0), -1);
}

TEST(mmio_custom_procedure_like_the_game)
{
    std::vector<char> w = wave();
    for (LONG buf : {0L, 8L, 64L, 4096L}) {
        memfile f = {&w, 0, 0, 0, 0};
        MMIOINFO in = {};
        in.fccIOProc = FNVNX_FOURCC('W', 'A', 'V', ' ');
        in.pIOProc = game_like_proc;
        in.adwInfo[0] = (DWORD)(uintptr_t)&f;
        in.cchBuffer = buf;
        HMMIO h = mmioOpenA((LPSTR) "sound\\fx\\test.wav", &in, MMIO_READ | MMIO_ALLOCBUF);
        CHECK(h != NULL);
        if (!h)
            return;
        CHECK_EQ(f.opens, 1);
        MMIOINFO io;
        CHECK_EQ(mmioGetInfo(h, &io, 0), 0);
        CHECK(io.adwInfo[0] == (DWORD)(uintptr_t)&f && io.pIOProc == game_like_proc);
        CHECK_EQ(io.cchBuffer, buf ? buf : MMIO_DEFAULTBUFFER);
        walk(h, 1);
        CHECK(mmioSeek(h, 0, SEEK_SET) == 0);
        walk(h, 0);
        CHECK(f.seeks > 0);   /* positions came from lDiskOffset although SEEK returned 0 */
        CHECK_EQ(mmioClose(h, 0), 0);
        CHECK_EQ(f.closes, 1);
    }
    /* an unknown fccIOProc without a procedure */
    MMIOINFO in = {};
    in.fccIOProc = FNVNX_FOURCC('W', 'A', 'V', ' ');
    CHECK(mmioOpenA((LPSTR) "x.wav", &in, MMIO_READ) == NULL);
    CHECK_EQ(in.wErrorRet, MMIOERR_INVALIDFILE);
}

TEST(mmio_memory_file)
{
    std::vector<char> w = wave();
    MMIOINFO in = {};
    in.fccIOProc = FOURCC_MEM;
    in.pchBuffer = w.data();
    in.cchBuffer = (LONG)w.size();
    HMMIO h = mmioOpenA(NULL, &in, MMIO_READ);
    CHECK(h != NULL);
    if (!h)
        return;
    walk(h, 0);
    CHECK_EQ(mmioSeek(h, 0, SEEK_SET), 0);
    walk(h, 2);
    CHECK_EQ(mmioSeek(h, 1, SEEK_END), -1);
    CHECK_EQ(mmioSeek(h, -4, SEEK_END), (LONG)w.size() - 4);
    CHECK_EQ(mmioClose(h, 0), 0);
}

TEST(mmio_write_chunks)
{
    MMIOINFO in = {};
    HMMIO h = mmioOpenA((LPSTR) "C:\\FalloutNV\\out.wav", &in, MMIO_CREATE | MMIO_WRITE | MMIO_ALLOCBUF);
    CHECK(h != NULL);
    if (!h)
        return;
    MMCKINFO riff = {}, ck = {};
    riff.fccType = FNVNX_FOURCC('W', 'A', 'V', 'E');
    CHECK_EQ(mmioCreateChunk(h, &riff, MMIO_CREATERIFF), 0);
    ck.ckid = FNVNX_FOURCC('d', 'a', 't', 'a');
    CHECK_EQ(mmioCreateChunk(h, &ck, 0), 0);
    CHECK_EQ(mmioWrite(h, "abc", 3), 3);
    CHECK_EQ(mmioAscend(h, &ck, 0), 0);
    CHECK_EQ(ck.cksize, 3);
    CHECK_EQ(mmioAscend(h, &riff, 0), 0);
    CHECK_EQ(riff.cksize, 4 + 8 + 4);
    CHECK_EQ(mmioClose(h, 0), 0);
    HANDLE f = CreateFileA("C:\\FalloutNV\\out.wav", GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    char b[32] = {0};
    DWORD got = 0;
    CHECK(ReadFile(f, b, sizeof b, &got, NULL));
    CloseHandle(f);
    CHECK_EQ(got, 24);
    CHECK(!memcmp(b, "RIFF\x10\0\0\0WAVEdata\x03\0\0\0abc\0", 24));
    CHECK(mmioStringToFOURCCA("wave", 0x10) == FNVNX_FOURCC('W', 'A', 'V', 'E'));
    CHECK(fnvnx_win32_lookup_import("WINMM.dll", "mmioAdvance", FNVNX_IMPORT_BY_NAME) == (void *)&mmioAdvance);
}
