/* fnvnx_mmio.h - winmm's multimedia file I/O (mmio*), platform/win32/mmio.cpp. The structures have the Win32
 * layouts in the game's ILP32 data model (MMIOINFO 72 bytes, MMCKINFO 20): the game's own I/O procedure and the WAV
 * loader read and write their fields. */
#ifndef FNVNX_MMIO_H
#define FNVNX_MMIO_H

#include "fnvnx_win32_ext.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef DWORD FOURCC;
typedef void *HMMIO;
FNVNX_CALLBACK_T(LPMMIOPROC, LRESULT, (LPSTR lpmmioinfo, UINT uMsg, LPARAM lParam1, LPARAM lParam2));

typedef struct _MMIOINFO {
    DWORD dwFlags;
    FOURCC fccIOProc;
    LPMMIOPROC pIOProc;
    UINT wErrorRet;
    void *htask;
    LONG cchBuffer;
    char *pchBuffer;
    char *pchNext;
    char *pchEndRead;
    char *pchEndWrite;
    LONG lBufOffset;
    LONG lDiskOffset;
    DWORD adwInfo[3];
    DWORD dwReserved1;
    DWORD dwReserved2;
    HMMIO hmmio;
} MMIOINFO, *LPMMIOINFO;

typedef struct _MMCKINFO {
    FOURCC ckid;
    DWORD cksize;
    FOURCC fccType;
    DWORD dwDataOffset;
    DWORD dwFlags;
} MMCKINFO, *LPMMCKINFO;

#define FNVNX_FOURCC(a, b, c, d) ((FOURCC)(unsigned char)(a) | ((FOURCC)(unsigned char)(b) << 8) | \
                                  ((FOURCC)(unsigned char)(c) << 16) | ((FOURCC)(unsigned char)(d) << 24))
#define FOURCC_RIFF FNVNX_FOURCC('R', 'I', 'F', 'F')
#define FOURCC_LIST FNVNX_FOURCC('L', 'I', 'S', 'T')
#define FOURCC_DOS FNVNX_FOURCC('D', 'O', 'S', ' ')
#define FOURCC_MEM FNVNX_FOURCC('M', 'E', 'M', ' ')

#define MMIO_READ 0x00000000
#define MMIO_WRITE 0x00000001
#define MMIO_READWRITE 0x00000002
#define MMIO_RWMODE 0x00000003
#define MMIO_CREATE 0x00001000
#define MMIO_PARSE 0x00000100
#define MMIO_DELETE 0x00000200
#define MMIO_EXIST 0x00004000
#define MMIO_ALLOCBUF 0x00010000
#define MMIO_GETTEMP 0x00020000
#define MMIO_DIRTY 0x10000000
#define MMIO_FHOPEN 0x0010
#define MMIO_EMPTYBUF 0x0010
#define MMIO_FINDCHUNK 0x0010
#define MMIO_FINDRIFF 0x0020
#define MMIO_FINDLIST 0x0040
#define MMIO_CREATERIFF 0x0020
#define MMIO_CREATELIST 0x0040
#define MMIO_DEFAULTBUFFER 8192

#define MMIOM_READ MMIO_READ
#define MMIOM_WRITE MMIO_WRITE
#define MMIOM_SEEK 2
#define MMIOM_OPEN 3
#define MMIOM_CLOSE 4
#define MMIOM_WRITEFLUSH 5

#define MMSYSERR_NOERROR 0
#define MMSYSERR_INVALHANDLE 5
#define MMIOERR_BASE 256
#define MMIOERR_FILENOTFOUND (MMIOERR_BASE + 1)
#define MMIOERR_OUTOFMEMORY (MMIOERR_BASE + 2)
#define MMIOERR_CANNOTOPEN (MMIOERR_BASE + 3)
#define MMIOERR_CANNOTCLOSE (MMIOERR_BASE + 4)
#define MMIOERR_CANNOTREAD (MMIOERR_BASE + 5)
#define MMIOERR_CANNOTWRITE (MMIOERR_BASE + 6)
#define MMIOERR_CANNOTSEEK (MMIOERR_BASE + 7)
#define MMIOERR_CANNOTEXPAND (MMIOERR_BASE + 8)
#define MMIOERR_CHUNKNOTFOUND (MMIOERR_BASE + 9)
#define MMIOERR_UNBUFFERED (MMIOERR_BASE + 10)
#define MMIOERR_INVALIDFILE (MMIOERR_BASE + 16)

HMMIO mmioOpenA(LPSTR pszFileName, LPMMIOINFO pmmioinfo, DWORD fdwOpen);
UINT mmioClose(HMMIO hmmio, UINT fuClose);
LONG mmioRead(HMMIO hmmio, char *pch, LONG cch);
LONG mmioWrite(HMMIO hmmio, const char *pch, LONG cch);
LONG mmioSeek(HMMIO hmmio, LONG lOffset, int iOrigin);
UINT mmioGetInfo(HMMIO hmmio, LPMMIOINFO pmmioinfo, UINT fuInfo);
UINT mmioSetInfo(HMMIO hmmio, const MMIOINFO *pmmioinfo, UINT fuInfo);
UINT mmioAdvance(HMMIO hmmio, LPMMIOINFO pmmioinfo, UINT fuAdvance);
UINT mmioFlush(HMMIO hmmio, UINT fuFlush);
UINT mmioDescend(HMMIO hmmio, LPMMCKINFO pmmcki, const MMCKINFO *pmmckiParent, UINT fuDescend);
UINT mmioAscend(HMMIO hmmio, LPMMCKINFO pmmcki, UINT fuAscend);
UINT mmioCreateChunk(HMMIO hmmio, LPMMCKINFO pmmcki, UINT fuCreate);
LRESULT mmioSendMessage(HMMIO hmmio, UINT uMsg, LPARAM lParam1, LPARAM lParam2);
FOURCC mmioStringToFOURCCA(LPCSTR sz, UINT uFlags);

#ifdef __cplusplus
}
#endif

#endif
