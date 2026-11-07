/* vorbisfile.cpp - the exe's vorbisfile.dll imports (docs/AUDIO_INPUT.md "Ogg Vorbis").
 *
 * FalloutNV.exe imports ov_open_callbacks (IAT 0x00fdf438, mis-decompiled as a noreturn thunk), ov_clear,
 * ov_pcm_total, ov_seekable, ov_read and ov_info from a dynamically linked vorbisfile.dll; libvorbis is not in
 * the exe. The engine embeds the OggVorbis_File in its own stream object: 0x2D0 bytes (the Win32 libvorbis
 * 1.x layout) followed by its own fields. To stay independent of the layout of whatever libvorbisfile we build,
 * the game's block holds only a magic and a pointer to a heap-allocated real OggVorbis_File ("boxing").
 *
 * Built against the real libvorbisfile/libvorbis/libogg (BSD-3-Clause) when FNVNX_HAVE_VORBIS is defined; those
 * are external code fetched and built by a later build step (pinned versions in docs/AUDIO_INPUT.md). Without
 * it every open fails with OV_ENOTVORBIS, which the engine handles as "no music file".
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* the platform lane (platform/win32/platstat.cpp; fnvnx_win32_ext.h is not included in C here) */
extern "C" volatile int fnvnx_plat_on;
extern "C" void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail);
#define PLAT_RA ((unsigned)(unsigned long)__builtin_return_address(0))

#ifdef FNVNX_HAVE_VORBIS
#include <vorbis/vorbisfile.h>
#endif

#define FNVNX_OV_GAME_SIZE 0x2D0      /* sizeof(OggVorbis_File) the engine reserves */
#define FNVNX_OV_MAGIC 0x564F4E46u     /* "FNOV" */
#define FNVNX_OV_ENOTVORBIS (-132)
#define FNVNX_OV_EINVAL (-131)
#define FNVNX_OV_EFAULT (-129)

/* the engine's view of the callbacks: four cdecl function pointers, passed by value */
typedef struct fnvnx_ov_callbacks {
    size_t (*read_func)(void *ptr, size_t size, size_t nmemb, void *datasource);
    int (*seek_func)(void *datasource, int64_t offset, int whence);
    int (*close_func)(void *datasource);
    long (*tell_func)(void *datasource);
} fnvnx_ov_callbacks;

struct box {
    uint32_t magic;
    void *real;   /* OggVorbis_File* */
};
static_assert(sizeof(struct box) <= FNVNX_OV_GAME_SIZE, "box fits the engine's OggVorbis_File");

#ifdef FNVNX_HAVE_VORBIS
static OggVorbis_File *unbox(void *vf)
{
    struct box *b = (struct box *)vf;
    return (b && b->magic == FNVNX_OV_MAGIC) ? (OggVorbis_File *)b->real : NULL;
}
#endif

extern "C" {

int fnvnx_ov_open_callbacks(void *datasource, void *vf, const char *initial, long ibytes, fnvnx_ov_callbacks cb)
{
    if (!vf)
        return FNVNX_OV_EINVAL;
    struct box *b = (struct box *)vf;
    b->magic = 0;
    b->real = NULL;
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = (OggVorbis_File *)calloc(1, sizeof *f);
    if (!f)
        return FNVNX_OV_EFAULT;
    ov_callbacks c;
    c.read_func = cb.read_func;
    c.seek_func = (int (*)(void *, ogg_int64_t, int))cb.seek_func;
    c.close_func = cb.close_func;
    c.tell_func = cb.tell_func;
    int r = ov_open_callbacks(datasource, f, initial, ibytes, c);
    if (r) {
        if (fnvnx_plat_on) {   /* the platform lane: 3 = decode (platstat.cpp) */
            char d[24];
            snprintf(d, sizeof d, "ov %d", r);
            fnvnx_plat_note(3, "ov_open_callbacks", PLAT_RA, d);
        }
        free(f);   /* on failure libvorbisfile has already cleared f; the caller must not ov_clear */
        return r;
    }
    b->magic = FNVNX_OV_MAGIC;
    b->real = f;
    return 0;
#else
    (void)datasource; (void)initial; (void)ibytes; (void)cb;
    if (fnvnx_plat_on)   /* a build without libvorbis: every music file fails here */
        fnvnx_plat_note(3, "ov_open_callbacks", PLAT_RA, "no libvorbis");
    return FNVNX_OV_ENOTVORBIS;
#endif
}

int fnvnx_ov_clear(void *vf)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    if (f) {
        ov_clear(f);
        free(f);
    }
#endif
    if (vf) {
        struct box *b = (struct box *)vf;
        b->magic = 0;
        b->real = NULL;
    }
    return 0;
}

void *fnvnx_ov_info(void *vf, int link)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? (void *)ov_info(f, link) : NULL;
#else
    (void)vf; (void)link;
    return NULL;
#endif
}

long fnvnx_ov_seekable(void *vf)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? ov_seekable(f) : 0;
#else
    (void)vf;
    return 0;
#endif
}

int64_t fnvnx_ov_pcm_total(void *vf, int i)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? (int64_t)ov_pcm_total(f, i) : FNVNX_OV_EINVAL;
#else
    (void)vf; (void)i;
    return FNVNX_OV_EINVAL;
#endif
}

double fnvnx_ov_time_total(void *vf, int i)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? ov_time_total(f, i) : FNVNX_OV_EINVAL;
#else
    (void)vf; (void)i;
    return FNVNX_OV_EINVAL;
#endif
}

long fnvnx_ov_read(void *vf, char *buffer, int length, int bigendianp, int word, int sgned, int *bitstream)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? ov_read(f, buffer, length, bigendianp, word, sgned, bitstream) : FNVNX_OV_EINVAL;
#else
    (void)vf; (void)buffer; (void)length; (void)bigendianp; (void)word; (void)sgned; (void)bitstream;
    return FNVNX_OV_EINVAL;
#endif
}

int fnvnx_ov_pcm_seek(void *vf, int64_t pos)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? ov_pcm_seek(f, pos) : FNVNX_OV_EINVAL;
#else
    (void)vf; (void)pos;
    return FNVNX_OV_EINVAL;
#endif
}

int64_t fnvnx_ov_pcm_tell(void *vf)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? (int64_t)ov_pcm_tell(f) : FNVNX_OV_EINVAL;
#else
    (void)vf;
    return FNVNX_OV_EINVAL;
#endif
}

int fnvnx_ov_time_seek(void *vf, double s)
{
#ifdef FNVNX_HAVE_VORBIS
    OggVorbis_File *f = unbox(vf);
    return f ? ov_time_seek(f, s) : FNVNX_OV_EINVAL;
#else
    (void)vf; (void)s;
    return FNVNX_OV_EINVAL;
#endif
}

} // extern "C"
