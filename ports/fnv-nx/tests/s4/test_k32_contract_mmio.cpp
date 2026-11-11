/* test_k32_contract_mmio.cpp - mmioOpenA's name operations and expandable memory files (task W20 group 11). */
#include "s4_test.h"
#include "fnvnx_mmio.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

TEST(k32c_mmioopen_name_operations)
{
    CreateDirectoryA("Data", NULL);
    s4_write_file("Data\\mm.wav", "RIFFxxxx");
    char name[260];
    /* MMIO_PARSE: the fully qualified name, written back; no file is needed */
    strcpy(name, "Data\\..\\Data\\nosuch.wav");
    MMIOINFO in = {};
    in.wErrorRet = 77;
    CHECK(mmioOpenA(name, &in, MMIO_PARSE) != NULL);
    CHECK_STR(name, "C:\\FalloutNV\\Data\\nosuch.wav");
    CHECK_EQ(in.wErrorRet, 0);
    strcpy(name, "other.dir\\x.wav");
    CHECK(mmioOpenA(name, NULL, MMIO_PARSE | MMIO_READ | MMIO_CREATE) != NULL);   /* all other flags are ignored */
    CHECK_STR(name, "C:\\FalloutNV\\other.dir\\x.wav");
    CHECK(mmioOpenA(NULL, &in, MMIO_PARSE) == NULL);
    CHECK(in.wErrorRet != 0);
    /* MMIO_EXIST: whether the file exists (a directory is no file) */
    strcpy(name, "data\\MM.wav");
    CHECK(mmioOpenA(name, NULL, MMIO_EXIST) != NULL);
    strcpy(name, "Data\\nosuch.wav");
    in.wErrorRet = 0;
    CHECK(mmioOpenA(name, &in, MMIO_EXIST) == NULL);
    CHECK_EQ(in.wErrorRet, MMIOERR_FILENOTFOUND);
    strcpy(name, "Data");
    CHECK(mmioOpenA(name, NULL, MMIO_EXIST) == NULL);
    /* MMIO_GETTEMP: "X:" and a prefix make a temporary file name on that drive */
    strcpy(name, "C:fnv");
    CHECK(mmioOpenA(name, &in, MMIO_GETTEMP) != NULL);
    CHECK(!strncmp(name, "C:\\", 3));
    CHECK(strstr(name, "fnv") != NULL || strstr(name, "FNV") != NULL);
    DeleteFileA(name);
    strcpy(name, "ab");
    CHECK(mmioOpenA(name, &in, MMIO_GETTEMP) != NULL);
    CHECK(strlen(name) > 3 && name[1] == ':');
    DeleteFileA(name);
    /* MMIO_DELETE */
    strcpy(name, "Data\\mm.wav");
    CHECK(mmioOpenA(name, NULL, MMIO_DELETE) != NULL);
    CHECK(GetFileAttributesA("Data\\mm.wav") == INVALID_FILE_ATTRIBUTES);
    CHECK(mmioOpenA(name, &in, MMIO_DELETE) == NULL);
    CHECK_EQ(in.wErrorRet, MMIOERR_FILENOTFOUND);
}

TEST(k32c_mmio_expandable_memory_file)
{
    /* a memory file made by mmioOpen (no buffer given, MMIO_ALLOCBUF): adwInfo[0] is the growth increment */
    MMIOINFO in = {};
    in.fccIOProc = FOURCC_MEM;
    in.cchBuffer = 8;
    in.adwInfo[0] = 16;
    HMMIO h = mmioOpenA(NULL, &in, MMIO_READWRITE | MMIO_CREATE | MMIO_ALLOCBUF);
    CHECK(h != NULL);
    if (!h)
        return;
    char data[100];
    for (int i = 0; i < 100; i++)
        data[i] = (char)('A' + i % 26);
    CHECK_EQ(mmioWrite(h, data, 5), 5);
    CHECK_EQ(mmioWrite(h, data + 5, 95), 95);             /* beyond the 8 bytes: grown in steps of 16 */
    MMIOINFO got = {};
    CHECK_EQ(mmioGetInfo(h, &got, 0), 0);
    CHECK(got.cchBuffer >= 100 && (got.cchBuffer - 8) % 16 == 0);
    CHECK_EQ(mmioSeek(h, 0, SEEK_SET), 0);
    char back[100];
    CHECK_EQ(mmioRead(h, back, 100), 100);
    CHECK(!memcmp(back, data, 100));
    CHECK_EQ(mmioClose(h, 0), 0);
    /* a memory file that is not expandable stops at its end */
    MMIOINFO fix = {};
    fix.fccIOProc = FOURCC_MEM;
    fix.cchBuffer = 8;
    HMMIO f = mmioOpenA(NULL, &fix, MMIO_READWRITE | MMIO_CREATE | MMIO_ALLOCBUF);
    CHECK(f != NULL);
    if (f) {
        CHECK_EQ(mmioWrite(f, data, 20), 8);
        CHECK_EQ(mmioClose(f, 0), 0);
    }
}
