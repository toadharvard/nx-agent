/* mixer.cpp - software mixer (see mixer.h). */
#include "mixer.h"
#include "reverb.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

struct mix_voice {
    mix_voice *prev, *next;
    mix_format fmt;
    uint8_t *data;
    uint32_t size;        /* bytes */
    uint32_t frames;      /* size / block */
    uint64_t pos;         /* 32.32 frames */
    uint32_t freq;
    float doppler;
    float gl, gr;
    int playing, looping;
    mix_notify *notify;
    uint32_t nnotify;
    mix_notify_fn notify_fn;
    void *notify_ctx;
    fnvnx_reverb *reverb;   /* SetFX I3DL2: the voice's own reverb, before its gains */
    /* pull-source voice (mixer_voice_create_stream): data points at sbuf, which holds the source frames
     * [sbase, sbase+scount) (indices relative to pos, rebased after every render); frames is 0xFFFFFFFF */
    mix_stream_fn stream;
    void *stream_user;
    uint8_t *sbuf;
    size_t scap;          /* bytes allocated */
    uint64_t sbase, scount;
};

static os_lock_t g_lock;
/* nothing under the mixer lock may call the game (a platform lock: os.h) */
static inline void mx_lock() { os_lock(&g_lock); }
static inline void mx_unlock() { os_unlock(&g_lock); }
static mix_voice *g_voices;
static uint32_t g_rate = 48000, g_chunk = 256;
static float g_master = 1.0f;
static float *g_scratch;
static uint32_t g_scratch_frames;
static float *g_fxbuf;   /* a voice with an effect renders here first (render runs under g_lock) */
static uint32_t g_fxbuf_frames;

void mixer_init(uint32_t out_rate, uint32_t chunk_frames)
{
    mx_lock();
    g_rate = out_rate ? out_rate : 48000;
    g_chunk = chunk_frames ? chunk_frames : 256;
    g_master = 1.0f;
    mx_unlock();
}

void mixer_shutdown(void)
{
    mx_lock();
    for (mix_voice *v = g_voices; v; v = v->next)
        v->playing = 0;
    free(g_scratch);
    g_scratch = NULL;
    g_scratch_frames = 0;
    free(g_fxbuf);
    g_fxbuf = NULL;
    g_fxbuf_frames = 0;
    mx_unlock();
}

uint32_t mixer_out_rate(void) { return g_rate; }

void mixer_set_master_gain(float g)
{
    mx_lock();
    g_master = g;
    mx_unlock();
}

float mixer_mb_to_gain(int32_t mb)
{
    if (mb <= -10000)
        return 0.0f;
    if (mb >= 0)
        return 1.0f;
    return powf(10.0f, (float)mb / 2000.0f);
}

void mixer_pan_to_gains(int32_t pan, float *l, float *r)
{
    if (pan < -10000) pan = -10000;
    if (pan > 10000) pan = 10000;
    *l = pan > 0 ? mixer_mb_to_gain(-pan) : 1.0f;
    *r = pan < 0 ? mixer_mb_to_gain(pan) : 1.0f;
}

mix_voice *mixer_voice_create(const mix_format *fmt, uint8_t *data, uint32_t size)
{
    mix_voice *v = (mix_voice *)calloc(1, sizeof *v);
    if (!v)
        return NULL;
    v->fmt = *fmt;
    v->data = data;
    v->size = size;
    v->frames = fmt->block ? size / fmt->block : 0;
    v->freq = fmt->rate;
    v->doppler = 1.0f;
    v->gl = v->gr = 1.0f;
    mx_lock();
    v->next = g_voices;
    if (g_voices)
        g_voices->prev = v;
    g_voices = v;
    mx_unlock();
    return v;
}

mix_voice *mixer_voice_create_stream(const mix_format *fmt, mix_stream_fn fn, void *user)
{
    if (!fn || !fmt->block)
        return NULL;
    mix_voice *v = mixer_voice_create(fmt, NULL, 0);
    if (!v)
        return NULL;
    mx_lock();
    v->stream = fn;
    v->stream_user = user;
    v->frames = 0xFFFFFFFFu;
    mx_unlock();
    return v;
}

void mixer_voice_destroy(mix_voice *v)
{
    if (!v)
        return;
    mx_lock();
    for (uint32_t i = 0; i < v->nnotify; i++) clk_device_watch(v->notify[i].event, 0);
    if (v->prev) v->prev->next = v->next; else g_voices = v->next;
    if (v->next) v->next->prev = v->prev;
    mx_unlock();
    fnvnx_reverb_destroy(v->reverb);
    free(v->notify);
    free(v->sbuf);
    free(v);
}

void mixer_voice_set_format(mix_voice *v, const mix_format *fmt)
{
    mx_lock();
    v->fmt = *fmt;
    v->frames = v->stream ? 0xFFFFFFFFu : fmt->block ? v->size / fmt->block : 0;
    v->freq = fmt->rate;
    v->pos = 0;
    v->sbase = v->scount = 0;
    mx_unlock();
}

void mixer_voice_set_gain(mix_voice *v, float l, float r)
{
    mx_lock();
    v->gl = l;
    v->gr = r;
    mx_unlock();
}

void mixer_voice_set_freq(mix_voice *v, uint32_t f)
{
    mx_lock();
    v->freq = f ? f : v->fmt.rate;
    mx_unlock();
}

void mixer_voice_set_doppler(mix_voice *v, float ratio)
{
    mx_lock();
    v->doppler = ratio > 0.0f ? ratio : 1.0f;
    mx_unlock();
}

void mixer_voice_play(mix_voice *v, int looping)
{
    mx_lock();
    v->looping = looping ? 1 : 0;
    v->playing = v->frames ? 1 : 0;
    mx_unlock();
}

static void fire_stop(mix_voice *v)
{
    for (uint32_t i = 0; i < v->nnotify; i++)
        if (v->notify[i].offset == MIX_NOTIFY_STOP && v->notify_fn)
            v->notify_fn(v->notify_ctx, MIX_NOTIFY_STOP, v->notify[i].event);
}

void mixer_voice_stop(mix_voice *v)
{
    mx_lock();
    if (v->playing) {
        v->playing = 0;
        fire_stop(v);
    }
    mx_unlock();
}

int mixer_voice_playing(const mix_voice *v) { return v->playing; }
int mixer_voice_looping(const mix_voice *v) { return v->playing && v->looping; }

static uint32_t play_bytes(const mix_voice *v)
{
    uint32_t f = (uint32_t)(v->pos >> 32);
    if (f >= v->frames)
        f = 0;
    return f * v->fmt.block;
}

uint32_t mixer_voice_play_cursor(const mix_voice *v)
{
    mx_lock();
    uint32_t b = play_bytes(v);
    mx_unlock();
    return b;
}

uint32_t mixer_voice_write_cursor(const mix_voice *v)
{
    mx_lock();
    uint32_t b = play_bytes(v);
    if (v->playing && v->size) {
        /* one output period of lead, in source frames */
        uint64_t lead = ((uint64_t)g_chunk * v->freq + g_rate - 1) / g_rate;
        b = (uint32_t)((b + lead * v->fmt.block) % v->size);
    }
    mx_unlock();
    return b;
}

void mixer_voice_set_cursor(mix_voice *v, uint32_t off)
{
    mx_lock();
    uint32_t f = v->fmt.block ? off / v->fmt.block : 0;
    if (f >= v->frames)
        f = 0;
    if (v->stream)
        f = 0, v->sbase = v->scount = 0;   /* a stream restarts: the window is dropped */
    v->pos = (uint64_t)f << 32;
    mx_unlock();
}

void mixer_voice_set_notify(mix_voice *v, const mix_notify *n, uint32_t count, mix_notify_fn fn, void *ctx)
{
    mix_notify *copy = NULL;
    if (count) {
        copy = (mix_notify *)malloc(count * sizeof *copy);
        if (!copy)
            return;
        memcpy(copy, n, count * sizeof *copy);
    }
    mx_lock();
    mix_notify *old = v->notify;
    for (uint32_t i = 0; i < v->nnotify; i++) clk_device_watch(old[i].event, 0);   /* a wait on these events runs the device (clock) */
    for (uint32_t i = 0; i < count; i++) clk_device_watch(copy[i].event, 1);
    v->notify = copy;
    v->nnotify = count;
    v->notify_fn = fn;
    v->notify_ctx = ctx;
    mx_unlock();
    free(old);
}

/* sample of frame f, channel c, as float -1..1 */
static inline float sample(const mix_voice *v, uint32_t f, unsigned c)
{
    const uint8_t *p = v->data + (size_t)f * v->fmt.block;
    switch (v->fmt.bits) {
    case 8: return ((float)p[c] - 128.0f) * (1.0f / 128.0f);
    case 16: { int16_t s; memcpy(&s, p + 2 * c, 2); return (float)s * (1.0f / 32768.0f); }
    case 24: {
        int32_t s = (int32_t)((uint32_t)p[3 * c] << 8 | (uint32_t)p[3 * c + 1] << 16 | (uint32_t)p[3 * c + 2] << 24);
        return (float)s * (1.0f / 2147483648.0f);
    }
    case 32:
        if (v->fmt.is_float) { float s; memcpy(&s, p + 4 * c, 4); return s; }
        else { int32_t s; memcpy(&s, p + 4 * c, 4); return (float)s * (1.0f / 2147483648.0f); }
    }
    return 0.0f;
}

/* fires notifications for byte offsets crossed going from byte a to byte b (wrapped `wraps` times) */
static void fire_crossed(mix_voice *v, uint32_t a, uint32_t b, uint32_t wraps)
{
    if (!v->notify_fn)
        return;
    for (uint32_t i = 0; i < v->nnotify; i++) {
        uint32_t o = v->notify[i].offset;
        if (o == MIX_NOTIFY_STOP)
            continue;
        int hit;
        if (wraps == 0)
            hit = o >= a && o < b;
        else if (wraps == 1 && b <= a)
            hit = o >= a || o < b;
        else
            hit = 1; /* a whole lap passed */
        if (hit)
            v->notify_fn(v->notify_ctx, o, v->notify[i].event);
    }
}

/* Pull exactly the source frames this render needs: output i reads frame floor(p_i) and, when p_i has a
 * fraction, floor(p_i)+1, with p_i = pos + i*step. So the window ends at (p_last>>32)+1+(frac(p_last)!=0), and
 * everything below floor(pos) is dropped (frames the resampler skips are still pulled). The window is a pure
 * function of pos/step/frames: the pulled total after K output frames from 0 is
 * (p>>32)+1+((uint32)p!=0) with p=(K-1)*step, independent of how the K frames are split into calls.
 * False when nothing can be rendered. */
static bool stream_fill(mix_voice *v, uint32_t frames, uint64_t step)
{
    const uint64_t p = v->pos + (uint64_t)(frames - 1) * step;
    const uint64_t need_end = (p >> 32) + 1 + ((uint32_t)p != 0), lo = v->pos >> 32, blk = v->fmt.block;
    uint64_t end = v->sbase + v->scount;
    if (lo > v->sbase) {   /* drop what is behind the cursor */
        if (lo < end)
            memmove(v->sbuf, v->sbuf + (lo - v->sbase) * blk, (size_t)((end - lo) * blk));
        v->scount = lo < end ? end - lo : 0;
        v->sbase = lo < end ? lo : end;
        end = v->sbase + v->scount;
    }
    if (need_end > end) {
        const uint64_t want = need_end - end;
        if ((v->scount + want) * blk > v->scap) {
            uint8_t *n = (uint8_t *)realloc(v->sbuf, (size_t)((v->scount + want) * blk));
            if (!n)
                return false;
            v->sbuf = n;
            v->scap = (size_t)((v->scount + want) * blk);
        }
        size_t got = v->stream(v->stream_user, v->sbuf + v->scount * blk, (size_t)want);
        v->scount += got > want ? want : got;
    }
    v->data = v->sbuf;
    return true;
}

static void mix_voice_into(mix_voice *v, float *out, uint32_t frames, float gl, float gr)
{
    const uint64_t step = (uint64_t)((double)v->freq * v->doppler / g_rate * 4294967296.0);
    const uint64_t end = (uint64_t)v->frames << 32;
    const unsigned stereo = v->fmt.channels >= 2;
    uint32_t a = play_bytes(v), wraps = 0;
    if (v->stream && !stream_fill(v, frames, step))
        return;
    for (uint32_t i = 0; i < frames; i++) {
        uint32_t f = (uint32_t)(v->pos >> 32);
        float t = (float)(uint32_t)v->pos * (1.0f / 4294967296.0f);
        uint32_t f1 = f + 1;
        int have1 = 1;
        if (v->stream) {
            /* the window holds [sbase, sbase+scount): f and, unless t == 0 (weight 0), f+1 must be in it;
             * otherwise the stream has run dry: silence from here, the cursor stays (the voice stays alive) */
            if (f < v->sbase || f - v->sbase >= v->scount || ((uint32_t)v->pos && f1 - v->sbase >= v->scount))
                break;
            f -= (uint32_t)v->sbase;
            f1 -= (uint32_t)v->sbase;
            have1 = (uint32_t)v->pos != 0;
            if (!have1)
                f1 = f;
        } else if (f1 >= v->frames) {
            if (v->looping) f1 = 0;
            else have1 = 0;
        }
        float l0 = sample(v, f, 0), r0 = stereo ? sample(v, f, 1) : l0;
        float l1 = have1 ? sample(v, f1, 0) : 0.0f, r1 = have1 ? (stereo ? sample(v, f1, 1) : l1) : 0.0f;
        out[2 * i] += (l0 + (l1 - l0) * t) * gl;
        out[2 * i + 1] += (r0 + (r1 - r0) * t) * gr;
        v->pos += step;
        if (!v->stream && v->pos >= end) {
            if (v->looping) {
                while (v->pos >= end)
                    v->pos -= end;
                wraps++;
            } else {
                /* one-shot reached its end: the cursor crossed every remaining offset, then stops at 0 */
                fire_crossed(v, a, v->size, 0);
                v->pos = 0;
                v->playing = 0;
                fire_stop(v);
                return;
            }
        }
    }
    if (v->stream) {
        v->pos -= v->sbase << 32;   /* rebase: pos and the window shift together */
        v->sbase = 0;
        return;
    }
    fire_crossed(v, a, play_bytes(v), wraps);
}

void mixer_render_f32(float *out, uint32_t frames)
{
    memset(out, 0, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
    mx_lock();
    for (mix_voice *v = g_voices; v; v = v->next) {
        if (!(v->playing && (v->data || v->stream) && v->frames && v->freq))
            continue;
        const float gl = v->gl * g_master, gr = v->gr * g_master;
        if (!v->reverb) {
            mix_voice_into(v, out, frames, gl, gr);
            continue;
        }
        if (g_fxbuf_frames < frames) {
            float *n = (float *)realloc(g_fxbuf, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
            if (!n) {
                mix_voice_into(v, out, frames, gl, gr);   /* no memory: dry */
                continue;
            }
            g_fxbuf = n;
            g_fxbuf_frames = frames;
        }
        /* the effect runs on the buffer's signal; its volume and pan come after (DirectSound's FX chain).
         * A voice that ends inside this call leaves zeros behind, which the effect's tail fills. */
        memset(g_fxbuf, 0, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
        mix_voice_into(v, g_fxbuf, frames, 1.0f, 1.0f);
        fnvnx_reverb_process(v->reverb, g_fxbuf, frames);
        for (uint32_t i = 0; i < frames; i++) {
            out[2 * i] += g_fxbuf[2 * i] * gl;
            out[2 * i + 1] += g_fxbuf[2 * i + 1] * gr;
        }
    }
    mx_unlock();
}

int mixer_voice_set_reverb(mix_voice *v, const fnvnx_i3dl2 *p)
{
    mx_lock();
    int rc = 0;
    if (!p) {
        fnvnx_reverb_destroy(v->reverb);
        v->reverb = NULL;
    } else {
        if (!v->reverb)
            v->reverb = fnvnx_reverb_create(g_rate);
        if (v->reverb)
            fnvnx_reverb_set(v->reverb, p);
        else
            rc = -1;
    }
    mx_unlock();
    return rc;
}

void mixer_render_s16(int16_t *out, uint32_t frames)
{
    mx_lock();
    if (g_scratch_frames < frames) {
        float *n = (float *)realloc(g_scratch, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
        if (!n) {
            mx_unlock();
            memset(out, 0, (size_t)frames * MIX_OUT_CHANNELS * 2);
            return;
        }
        g_scratch = n;
        g_scratch_frames = frames;
    }
    float *buf = g_scratch;
    mx_unlock();
    /* the scratch buffer belongs to the (single) backend thread */
    mixer_render_f32(buf, frames);
    for (uint32_t i = 0; i < frames * MIX_OUT_CHANNELS; i++) {
        float s = buf[i] * 32767.0f;
        out[i] = (int16_t)(s > 32767.0f ? 32767 : s < -32768.0f ? -32768 : lrintf(s));
    }
}
