/* mmio.cpp - winmm's buffered multimedia file I/O (mmioOpenA ... mmioAdvance), for the game's WAV loader.
 *
 * The exe imports mmioOpenA, Descend, Read, Ascend, GetInfo, Advance and Close. Its WAV
 * loader opens with MMIO_ALLOCBUF and its own I/O procedure (pIOProc, fccIOProc 'WAV '), which reads through the
 * game's archive files: it keeps its file in adwInfo[0] and the position in lDiskOffset, and returns 0 from
 * MMIOM_SEEK. So every position here comes from lDiskOffset after a message, never from MMIOM_SEEK's return
 * (only -1 is an error), as on Windows. Then: mmioDescend (no flag and MMIO_FINDCHUNK, which must give
 * MMIOERR_CHUNKNOTFOUND 0x109 at the end of the parent), mmioRead, mmioAscend, and direct buffer access through
 * mmioGetInfo + pchNext/pchEndRead + mmioAdvance(MMIO_READ); at EOF mmioAdvance returns 0 with pchNext ==
 * pchEndRead. Buffers carry one zeroed guard byte past cchBuffer.
 *
 * Built-in procedures: DOS files (fccIOProc 0 or 'DOS ', through CreateFileA) and memory files ('MEM ': the buffer
 * is the file). The I/O procedure gets the handle's own MMIOINFO, which is what mmioGetInfo copies out. */
#include "w32.h"
#include "fnvnx_mmio.h"

#include <stdio.h>   /* SEEK_SET/CUR/END = FILE_BEGIN/CURRENT/END */
#include <stdlib.h>

#if defined(__ILP32__) || defined(__i386__)   /* the game's data model: the Win32 layouts */
static_assert(sizeof(MMIOINFO) == 72 && sizeof(MMCKINFO) == 20, "MMIOINFO/MMCKINFO layout");
static_assert(__builtin_offsetof(MMIOINFO, lDiskOffset) == 44 && __builtin_offsetof(MMIOINFO, adwInfo) == 48 &&
              __builtin_offsetof(MMIOINFO, hmmio) == 68, "MMIOINFO fields the game's I/O procedure uses");
#endif

namespace {
const DWORD MMIO_NAMEBUF = 128;   /* the least room the documentation promises szFilename for a name written back */
const uint32_t MMIO_MAGIC = 0x4F494D4Du;   /* "MMIO" */

struct mmio {
    MMIOINFO info;       /* first: the I/O procedure's LPSTR argument is &info */
    uint32_t magic;
    int mem;             /* memory file: the buffer is the whole file */
    int native_dos;      /* the built-in DOS procedure (a platform function, never a game address: not stored in pIOProc) */
    int own_buffer;
    char *wend;          /* buffered writes: end of the data written into the buffer */
    HANDLE dos;          /* the DOS procedure's file */
};

mmio *from(HMMIO h)
{
    mmio *m = (mmio *)h;
    return m && m->magic == MMIO_MAGIC ? m : NULL;
}

LRESULT dos_proc(LPSTR p, UINT msg, LPARAM l1, LPARAM l2)
{
    mmio *m = (mmio *)p;
    MMIOINFO *i = &m->info;
    switch (msg) {
    case MMIOM_OPEN: {
        DWORD mode = W32_DW(i->dwFlags) & MMIO_RWMODE;
        DWORD access = mode == MMIO_WRITE ? GENERIC_WRITE : mode == MMIO_READWRITE ? GENERIC_READ | GENERIC_WRITE : GENERIC_READ;
        DWORD disp = (W32_DW(i->dwFlags) & MMIO_CREATE) ? CREATE_ALWAYS : OPEN_EXISTING;
        m->dos = CreateFileA((LPCSTR)l1, access, FILE_SHARE_READ, NULL, disp, FILE_ATTRIBUTE_NORMAL, NULL);
        if (m->dos == INVALID_HANDLE_VALUE) {
            DWORD e = GetLastError();
            return e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND ? MMIOERR_FILENOTFOUND : MMIOERR_CANNOTOPEN;
        }
        i->lDiskOffset = 0;
        return 0;
    }
    case MMIOM_CLOSE:
        CloseHandle(m->dos);
        m->dos = INVALID_HANDLE_VALUE;
        return 0;
    case MMIOM_READ: {
        DWORD got = 0;
        if (!ReadFile(m->dos, (void *)l1, (DWORD)l2, &got, NULL))
            return -1;
        i->lDiskOffset += (LONG)W32_DW(got);
        return (LRESULT)W32_DW(got);
    }
    case MMIOM_WRITE:
    case MMIOM_WRITEFLUSH: {
        DWORD put = 0;
        if (!WriteFile(m->dos, (const void *)l1, (DWORD)l2, &put, NULL))
            return -1;
        i->lDiskOffset += (LONG)W32_DW(put);
        return (LRESULT)W32_DW(put);
    }
    case MMIOM_SEEK: {
        DWORD r = SetFilePointer(m->dos, (LONG)l1, NULL, (DWORD)l2);
        if (W32_DW(r) == 0xFFFFFFFFu)
            return -1;
        i->lDiskOffset = (LONG)W32_DW(r);
        return i->lDiskOffset;
    }
    }
    return 0;
}

LRESULT send(mmio *m, UINT msg, LPARAM l1, LPARAM l2)
{
    if (m->native_dos)
        return dos_proc((LPSTR)&m->info, msg, l1, l2);
    return m->info.pIOProc ? m->info.pIOProc((LPSTR)&m->info, msg, l1, l2) : (msg == MMIOM_SEEK ? -1 : 0);
}

bool buffered(const mmio *m) { return m->info.pchBuffer && m->info.cchBuffer > 0; }

/* logical position: in the buffer when buffered, the procedure's otherwise */
LONG position(const mmio *m)
{
    return buffered(m) ? m->info.lBufOffset + (LONG)(m->info.pchNext - m->info.pchBuffer) : m->info.lDiskOffset;
}

/* the procedure's position to `to`; false on a seek error */
bool place(mmio *m, LONG to)
{
    if (m->info.lDiskOffset == to)
        return true;
    if (send(m, MMIOM_SEEK, to, SEEK_SET) == -1)
        return false;
    return m->info.lDiskOffset == to;
}

/* writes out what was written into the buffer; the buffer then starts empty at the current position */
bool flush(mmio *m)
{
    if (!(W32_DW(m->info.dwFlags) & MMIO_DIRTY))
        return true;
    m->info.dwFlags &= ~(DWORD)MMIO_DIRTY;
    char *end = m->wend > m->info.pchNext ? m->wend : m->info.pchNext;
    LONG n = (LONG)(end - m->info.pchBuffer), at = position(m);
    m->wend = NULL;
    if (m->mem || n <= 0)
        return true;
    if (!place(m, m->info.lBufOffset) || send(m, MMIOM_WRITE, (LPARAM)m->info.pchBuffer, n) != n)
        return false;
    m->info.lBufOffset = at;
    m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
    return true;
}

/* refills the buffer from the current position; returns the bytes read (0 at EOF) or -1 */
LONG fill(mmio *m)
{
    if (m->mem)
        return 0;
    if (!flush(m))
        return -1;
    LONG at = position(m);
    if (!place(m, at))
        return -1;
    m->info.lBufOffset = m->info.lDiskOffset;
    m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
    LRESULT n = send(m, MMIOM_READ, (LPARAM)m->info.pchBuffer, m->info.cchBuffer);
    if (n < 0)
        return -1;
    if (n > m->info.cchBuffer)
        n = m->info.cchBuffer;
    m->info.pchEndRead = m->info.pchBuffer + n;
    if (m->own_buffer)
        m->info.pchBuffer[n] = 0;   /* a zero byte follows the data (the allocation has one past cchBuffer) */
    return (LONG)n;
}

bool read_exact(mmio *m, void *dst, LONG n) { return mmioRead((HMMIO)m, (char *)dst, n) == n; }
} // namespace

extern "C" {

HMMIO mmioOpenA(LPSTR name, LPMMIOINFO in, DWORD flags)
{
    flags = W32_DW(flags);
    /* the name-only operations. Their results are TRUE (a non-NULL value that is no handle) or NULL; a name they produce is
     * written back into szFilename, which the documentation requires to hold at least 128 bytes */
    if (flags & MMIO_PARSE) {                  /* the fully qualified file name; all other flags are ignored */
        char full[MAX_PATH];
        DWORD n = name ? GetFullPathNameA(name, sizeof full, full, NULL) : 0;
        if (!n || n >= MMIO_NAMEBUF) {
            if (in)
                in->wErrorRet = MMIOERR_CANNOTOPEN;
            return NULL;
        }
        memcpy(name, full, n + 1);
        if (in)
            in->wErrorRet = 0;
        return (HMMIO)1;
    }
    if (flags & MMIO_EXIST) {                  /* does the file exist (a directory is no file)? */
        /* MSDN also has the fully qualified name written back into szFilename; tests/s4/test_mmio.cpp passes a string
         * literal here, which no write can survive, so EXIST only answers the question (reported to the lead). */
        DWORD a = name ? GetFileAttributesA(name) : INVALID_FILE_ATTRIBUTES;
        if (a == INVALID_FILE_ATTRIBUTES || (a & FILE_ATTRIBUTE_DIRECTORY)) {
            if (in)
                in->wErrorRet = MMIOERR_FILENOTFOUND;
            return NULL;
        }
        if (in)
            in->wErrorRet = 0;
        return (HMMIO)1;
    }
    if (flags & MMIO_GETTEMP) {                /* "X:" and/or a prefix of up to three characters: a temporary file name */
        char dir[MAX_PATH], tmp[MAX_PATH];
        const char *prefix = name ? name : "";
        if (prefix[0] && prefix[1] == ':') {
            snprintf(dir, sizeof dir, "%c:\\", prefix[0]);
            prefix += 2;
        } else if (!GetTempPathA(sizeof dir, dir)) {
            dir[0] = 0;
        }
        if (!name || !dir[0] || !GetTempFileNameA(dir, prefix, 0, tmp) || strlen(tmp) >= MMIO_NAMEBUF) {
            if (in)
                in->wErrorRet = MMIOERR_FILENOTFOUND;
            return NULL;
        }
        memcpy(name, tmp, strlen(tmp) + 1);
        if (in)
            in->wErrorRet = 0;
        return (HMMIO)1;
    }
    if (flags & MMIO_DELETE) {
        BOOL ok = name && DeleteFileA(name);
        if (in)
            in->wErrorRet = ok ? 0 : MMIOERR_FILENOTFOUND;
        return ok ? (HMMIO)1 : NULL;
    }
    mmio *m = (mmio *)calloc(1, sizeof *m);
    if (!m) {
        if (in)
            in->wErrorRet = MMIOERR_OUTOFMEMORY;
        return NULL;
    }
    m->magic = MMIO_MAGIC;
    m->dos = INVALID_HANDLE_VALUE;
    if (in) {
        m->info.fccIOProc = in->fccIOProc;
        m->info.pIOProc = in->pIOProc;
        m->info.htask = in->htask;
        m->info.cchBuffer = in->cchBuffer;
        m->info.pchBuffer = in->pchBuffer;
        memcpy(m->info.adwInfo, in->adwInfo, sizeof m->info.adwInfo);
    }
    m->info.dwFlags = flags & ~(DWORD)MMIO_ALLOCBUF;
    m->info.hmmio = (HMMIO)m;
    UINT err = 0;
    if (!m->info.pIOProc) {
        FOURCC f = W32_DW(m->info.fccIOProc);
        if (f == FOURCC_MEM) {
            m->mem = 1;
        } else if (f == 0 || f == FOURCC_DOS) {
            m->info.fccIOProc = FOURCC_DOS;
            m->native_dos = 1;
        } else {
            err = MMIOERR_INVALIDFILE;   /* no installable I/O procedures (mmioInstallIOProc is not imported) */
        }
    }
    /* buffer: the caller's, or allocated (MMIO_ALLOCBUF, a memory file without one), one guard byte past it */
    if (!err && !m->info.pchBuffer && ((flags & MMIO_ALLOCBUF) || m->mem)) {
        if (m->info.cchBuffer <= 0)
            m->info.cchBuffer = m->mem ? 0 : MMIO_DEFAULTBUFFER;
        if (m->mem && m->info.cchBuffer <= 0)
            err = MMIOERR_OUTOFMEMORY;   /* a memory file needs a size (expanding ones are not supported) */
        else if (!(m->info.pchBuffer = (char *)calloc(1, (size_t)m->info.cchBuffer + 1)))
            err = MMIOERR_OUTOFMEMORY;
        else
            m->own_buffer = 1;
    }
    if (!m->info.pchBuffer)
        m->info.cchBuffer = 0;
    if (!err) {
        m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
        m->info.pchEndWrite = m->info.pchBuffer + m->info.cchBuffer;
        if (m->mem)
            m->info.pchEndRead = m->info.pchEndWrite;   /* the buffer is the file */
        else
            err = (UINT)send(m, MMIOM_OPEN, (LPARAM)name, 0);
    }
    if (err) {
        if (in)
            in->wErrorRet = err;
        if (m->own_buffer)
            free(m->info.pchBuffer);
        m->magic = 0;
        free(m);
        return NULL;
    }
    m->info.lBufOffset = m->info.lDiskOffset;
    if (in)
        in->wErrorRet = 0;
    return (HMMIO)m;
}

UINT mmioClose(HMMIO h, UINT flags)
{
    mmio *m = from(h);
    if (!m)
        return MMSYSERR_INVALHANDLE;
    UINT r = flush(m) ? 0 : MMIOERR_CANNOTWRITE;
    if (!m->mem && send(m, MMIOM_CLOSE, (LPARAM)flags, 0) != 0 && !r)
        r = MMIOERR_CANNOTCLOSE;
    if (m->own_buffer)
        free(m->info.pchBuffer);
    m->magic = 0;
    free(m);
    return r;
}

LONG mmioRead(HMMIO h, char *dst, LONG n)
{
    mmio *m = from(h);
    if (!m || n < 0 || (n && !dst))
        return -1;
    if (!buffered(m)) {
        LRESULT r = send(m, MMIOM_READ, (LPARAM)dst, n);
        return r < 0 ? -1 : (LONG)r;
    }
    LONG total = 0;
    while (n > 0) {
        LONG avail = (LONG)(m->info.pchEndRead - m->info.pchNext);
        if (avail <= 0) {
            LONG r = fill(m);
            if (r < 0)
                return total ? total : -1;
            if (r == 0)
                break;
            continue;
        }
        LONG k = avail < n ? avail : n;
        memcpy(dst + total, m->info.pchNext, (size_t)k);
        m->info.pchNext += k;
        total += k;
        n -= k;
    }
    return total;
}

LONG mmioWrite(HMMIO h, const char *src, LONG n)
{
    mmio *m = from(h);
    if (!m || n < 0 || (n && !src))
        return -1;
    if (!buffered(m)) {
        LRESULT r = send(m, MMIOM_WRITE, (LPARAM)src, n);
        return r < 0 ? -1 : (LONG)r;
    }
    LONG total = 0;
    while (n > 0) {
        LONG room = (LONG)(m->info.pchEndWrite - m->info.pchNext);
        if (room <= 0 && m->mem && m->own_buffer && (LONG)m->info.adwInfo[0] > 0) {
            /* an expandable memory file (adwInfo[0] = the growth increment) grows by whole increments to hold the write */
            LONG inc = (LONG)m->info.adwInfo[0], want = ((n + inc - 1) / inc) * inc;
            LONG at = (LONG)(m->info.pchNext - m->info.pchBuffer), end = (LONG)(m->info.pchEndRead - m->info.pchBuffer);
            char *nb = (char *)realloc(m->info.pchBuffer, (size_t)(m->info.cchBuffer + want) + 1);
            if (!nb)
                return total ? total : -1;
            memset(nb + m->info.cchBuffer, 0, (size_t)want + 1);
            m->info.cchBuffer += want;
            m->info.pchBuffer = nb;
            m->info.pchNext = nb + at;
            m->info.pchEndRead = nb + end;
            m->info.pchEndWrite = nb + m->info.cchBuffer;
            continue;
        }
        if (room <= 0) {
            if (m->mem || !flush(m))
                return total ? total : -1;
            m->info.lBufOffset = position(m);
            m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
            continue;
        }
        LONG k = room < n ? room : n;
        memcpy(m->info.pchNext, src + total, (size_t)k);
        m->info.pchNext += k;
        if (m->info.pchNext > m->wend)
            m->wend = m->info.pchNext;
        if (m->info.pchNext > m->info.pchEndRead)
            m->info.pchEndRead = m->info.pchNext;
        m->info.dwFlags |= MMIO_DIRTY;
        total += k;
        n -= k;
    }
    return total;
}

LONG mmioSeek(HMMIO h, LONG off, int origin)
{
    mmio *m = from(h);
    if (!m)
        return -1;
    LONG cur = position(m), to;
    if (m->mem) {
        to = origin == SEEK_SET ? off : origin == SEEK_CUR ? cur + off : origin == SEEK_END ? m->info.cchBuffer + off : -1;
        if (to < 0 || to > m->info.cchBuffer)
            return -1;
        m->info.pchNext = m->info.pchBuffer + to;
        return to;
    }
    if (origin != SEEK_SET && origin != SEEK_CUR && origin != SEEK_END)
        return -1;
    /* SEEK_CUR is relative to the logical position: sent as SEEK_SET (procedures need not know SEEK_CUR) */
    to = origin == SEEK_SET ? off : cur + off;
    if (origin != SEEK_END && to < 0)
        return -1;
    if (buffered(m) && origin != SEEK_END && !(W32_DW(m->info.dwFlags) & MMIO_DIRTY)) {
        LONG have = (LONG)(m->info.pchEndRead - m->info.pchBuffer);
        if (to >= m->info.lBufOffset && to <= m->info.lBufOffset + have) {   /* inside the buffer */
            m->info.pchNext = m->info.pchBuffer + (to - m->info.lBufOffset);
            return to;
        }
    }
    if (!flush(m))
        return -1;
    if (origin == SEEK_END ? send(m, MMIOM_SEEK, off, SEEK_END) == -1 : !place(m, to))
        return -1;
    if (buffered(m)) {
        m->info.lBufOffset = m->info.lDiskOffset;
        m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
    }
    return m->info.lDiskOffset;
}

UINT mmioGetInfo(HMMIO h, LPMMIOINFO out, UINT flags)
{
    mmio *m = from(h);
    if (!m || !out)
        return MMSYSERR_INVALHANDLE;
    *out = m->info;
    return 0;
}

UINT mmioSetInfo(HMMIO h, const MMIOINFO *in, UINT flags)
{
    mmio *m = from(h);
    if (!m || !in)
        return MMSYSERR_INVALHANDLE;
    if (buffered(m)) {
        char *lo = m->info.pchBuffer, *hi = m->info.pchBuffer + m->info.cchBuffer;
        if (in->pchNext < lo || in->pchNext > hi || in->pchEndRead < lo || in->pchEndRead > hi)
            return MMIOERR_CANNOTREAD;
        m->info.pchNext = in->pchNext;
        m->info.pchEndRead = in->pchEndRead;
        if (W32_DW(in->dwFlags) & MMIO_DIRTY) {
            m->info.dwFlags |= MMIO_DIRTY;
            if (m->info.pchNext > m->wend)
                m->wend = m->info.pchNext;
        }
    }
    memcpy(m->info.adwInfo, in->adwInfo, sizeof m->info.adwInfo);
    return 0;
}

UINT mmioAdvance(HMMIO h, LPMMIOINFO io, UINT flags)
{
    mmio *m = from(h);
    if (!m)
        return MMSYSERR_INVALHANDLE;
    if (!buffered(m))
        return MMIOERR_UNBUFFERED;
    if (io) {   /* the caller's direct-access pointers (and its writes) */
        UINT r = mmioSetInfo(h, io, 0);
        if (r)
            return r;
    }
    if (m->mem) {
        /* nothing to load: the buffer is the file; at its end pchNext == pchEndRead */
    } else if (flags == MMIO_WRITE) {
        if (!flush(m))
            return MMIOERR_CANNOTWRITE;
        m->info.lBufOffset = position(m);
        m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
    } else if (fill(m) < 0) {
        return MMIOERR_CANNOTREAD;
    }
    if (io)
        *io = m->info;
    return 0;
}

UINT mmioFlush(HMMIO h, UINT flags)
{
    mmio *m = from(h);
    if (!m)
        return MMSYSERR_INVALHANDLE;
    LONG at = position(m);
    if (!flush(m))
        return MMIOERR_CANNOTWRITE;
    if (buffered(m) && !m->mem && (flags & MMIO_EMPTYBUF)) {
        m->info.lBufOffset = at;
        m->info.pchNext = m->info.pchEndRead = m->info.pchBuffer;
    }
    return 0;
}

UINT mmioDescend(HMMIO h, LPMMCKINFO ck, const MMCKINFO *parent, UINT flags)
{
    mmio *m = from(h);
    if (!m || !ck)
        return MMSYSERR_INVALHANDLE;
    const FOURCC want_id = W32_DW(ck->ckid), want_type = W32_DW(ck->fccType);
    const bool find = flags & (MMIO_FINDCHUNK | MMIO_FINDRIFF | MMIO_FINDLIST);
    const LONG end = parent ? (LONG)(W32_DW(parent->dwDataOffset) + W32_DW(parent->cksize)) : 0x7FFFFFFF;
    for (;;) {
        LONG at = position(m);
        if (at < 0 || (parent && at + 8 > end))
            return MMIOERR_CHUNKNOTFOUND;
        uint32_t hdr[2];
        if (!read_exact(m, hdr, 8))
            return MMIOERR_CHUNKNOTFOUND;
        FOURCC id = hdr[0], type = 0;
        DWORD size = hdr[1];
        bool list = id == FOURCC_RIFF || id == FOURCC_LIST;
        if (list) {
            uint32_t t;
            if (!read_exact(m, &t, 4))
                return MMIOERR_CHUNKNOTFOUND;
            type = t;
        }
        bool hit = !find || ((flags & MMIO_FINDCHUNK) && id == want_id) ||
                   ((flags & MMIO_FINDRIFF) && id == FOURCC_RIFF && type == want_type) ||
                   ((flags & MMIO_FINDLIST) && id == FOURCC_LIST && type == want_type);
        if (hit) {
            ck->ckid = id;
            ck->cksize = size;
            if (list)
                ck->fccType = type;
            ck->dwDataOffset = (DWORD)(at + 8);
            ck->dwFlags = 0;
            return 0;
        }
        LONG next = at + 8 + (LONG)((size + 1) & ~(DWORD)1);
        if (next <= at || mmioSeek(h, next, SEEK_SET) != next)
            return MMIOERR_CHUNKNOTFOUND;
    }
}

UINT mmioAscend(HMMIO h, LPMMCKINFO ck, UINT flags)
{
    mmio *m = from(h);
    if (!m || !ck)
        return MMSYSERR_INVALHANDLE;
    LONG data = (LONG)W32_DW(ck->dwDataOffset);
    if (W32_DW(ck->dwFlags) & MMIO_DIRTY) {   /* a chunk mmioCreateChunk started: patch its size, pad to even */
        LONG at = position(m);
        if (at < data)
            return MMIOERR_CANNOTSEEK;
        DWORD size = (DWORD)(at - data);
        if (size & 1) {
            static const char pad = 0;
            if (mmioWrite(h, &pad, 1) != 1)
                return MMIOERR_CANNOTWRITE;
        }
        if (W32_DW(ck->cksize) != size) {
            uint32_t s32 = (uint32_t)size;
            if (mmioSeek(h, data - 4, SEEK_SET) != data - 4 || mmioWrite(h, (const char *)&s32, 4) != 4)
                return MMIOERR_CANNOTWRITE;
            ck->cksize = size;
        }
        ck->dwFlags &= ~(DWORD)MMIO_DIRTY;
    }
    LONG next = data + (LONG)((W32_DW(ck->cksize) + 1) & ~(DWORD)1);
    return mmioSeek(h, next, SEEK_SET) == next ? 0 : MMIOERR_CANNOTSEEK;
}

UINT mmioCreateChunk(HMMIO h, LPMMCKINFO ck, UINT flags)
{
    mmio *m = from(h);
    if (!m || !ck)
        return MMSYSERR_INVALHANDLE;
    LONG at = position(m);
    if (flags & MMIO_CREATERIFF)
        ck->ckid = FOURCC_RIFF;
    else if (flags & MMIO_CREATELIST)
        ck->ckid = FOURCC_LIST;
    uint32_t hdr[3] = {(uint32_t)W32_DW(ck->ckid), (uint32_t)W32_DW(ck->cksize), (uint32_t)W32_DW(ck->fccType)};
    LONG n = (flags & (MMIO_CREATERIFF | MMIO_CREATELIST)) ? 12 : 8;
    if (mmioWrite(h, (const char *)hdr, n) != n)
        return MMIOERR_CANNOTWRITE;
    ck->dwDataOffset = (DWORD)(at + 8);
    ck->dwFlags = MMIO_DIRTY;
    return 0;
}

LRESULT mmioSendMessage(HMMIO h, UINT msg, LPARAM l1, LPARAM l2)
{
    mmio *m = from(h);
    return m ? send(m, msg, l1, l2) : 0;
}

FOURCC mmioStringToFOURCCA(LPCSTR s, UINT flags)
{
    char c[4] = {' ', ' ', ' ', ' '};
    for (int i = 0; s && i < 4 && s[i]; i++)
        c[i] = (flags & 0x0010 /* MMIO_TOUPPER */) && s[i] >= 'a' && s[i] <= 'z' ? (char)(s[i] - 32) : s[i];
    return FNVNX_FOURCC(c[0], c[1], c[2], c[3]);
}

} // extern "C"
