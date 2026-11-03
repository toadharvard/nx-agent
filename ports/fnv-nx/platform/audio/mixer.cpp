/* mixer.cpp - software mixer (see mixer.h). */
#include "mixer.h"
#include "../os/os.h"

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
};

static os_lock_t g_lock;
static mix_voice *g_voices;
static uint32_t g_rate = 48000, g_chunk = 256;
static float g_master = 1.0f;
static float *g_scratch;
static uint32_t g_scratch_frames;

void mixer_init(uint32_t out_rate, uint32_t chunk_frames)
{
    os_lock(&g_lock);
    g_rate = out_rate ? out_rate : 48000;
    g_chunk = chunk_frames ? chunk_frames : 256;
    g_master = 1.0f;
    os_unlock(&g_lock);
}

void mixer_shutdown(void)
{
    os_lock(&g_lock);
    for (mix_voice *v = g_voices; v; v = v->next)
        v->playing = 0;
    free(g_scratch);
    g_scratch = NULL;
    g_scratch_frames = 0;
    os_unlock(&g_lock);
}

uint32_t mixer_out_rate(void) { return g_rate; }

void mixer_set_master_gain(float g)
{
    os_lock(&g_lock);
    g_master = g;
    os_unlock(&g_lock);
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
    os_lock(&g_lock);
    v->next = g_voices;
    if (g_voices)
        g_voices->prev = v;
    g_voices = v;
    os_unlock(&g_lock);
    return v;
}

void mixer_voice_destroy(mix_voice *v)
{
    if (!v)
        return;
    os_lock(&g_lock);
    if (v->prev) v->prev->next = v->next; else g_voices = v->next;
    if (v->next) v->next->prev = v->prev;
    os_unlock(&g_lock);
    free(v->notify);
    free(v);
}

void mixer_voice_set_format(mix_voice *v, const mix_format *fmt)
{
    os_lock(&g_lock);
    v->fmt = *fmt;
    v->frames = fmt->block ? v->size / fmt->block : 0;
    v->freq = fmt->rate;
    v->pos = 0;
    os_unlock(&g_lock);
}

void mixer_voice_set_gain(mix_voice *v, float l, float r)
{
    os_lock(&g_lock);
    v->gl = l;
    v->gr = r;
    os_unlock(&g_lock);
}

void mixer_voice_set_freq(mix_voice *v, uint32_t f)
{
    os_lock(&g_lock);
    v->freq = f ? f : v->fmt.rate;
    os_unlock(&g_lock);
}

void mixer_voice_set_doppler(mix_voice *v, float ratio)
{
    os_lock(&g_lock);
    v->doppler = ratio > 0.0f ? ratio : 1.0f;
    os_unlock(&g_lock);
}

void mixer_voice_play(mix_voice *v, int looping)
{
    os_lock(&g_lock);
    v->looping = looping ? 1 : 0;
    v->playing = v->frames ? 1 : 0;
    os_unlock(&g_lock);
}

static void fire_stop(mix_voice *v)
{
    for (uint32_t i = 0; i < v->nnotify; i++)
        if (v->notify[i].offset == MIX_NOTIFY_STOP && v->notify_fn)
            v->notify_fn(v->notify_ctx, MIX_NOTIFY_STOP, v->notify[i].event);
}

void mixer_voice_stop(mix_voice *v)
{
    os_lock(&g_lock);
    if (v->playing) {
        v->playing = 0;
        fire_stop(v);
    }
    os_unlock(&g_lock);
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
    os_lock(&g_lock);
    uint32_t b = play_bytes(v);
    os_unlock(&g_lock);
    return b;
}

uint32_t mixer_voice_write_cursor(const mix_voice *v)
{
    os_lock(&g_lock);
    uint32_t b = play_bytes(v);
    if (v->playing && v->size) {
        /* one output period of lead, in source frames */
        uint64_t lead = ((uint64_t)g_chunk * v->freq + g_rate - 1) / g_rate;
        b = (uint32_t)((b + lead * v->fmt.block) % v->size);
    }
    os_unlock(&g_lock);
    return b;
}

void mixer_voice_set_cursor(mix_voice *v, uint32_t off)
{
    os_lock(&g_lock);
    uint32_t f = v->fmt.block ? off / v->fmt.block : 0;
    if (f >= v->frames)
        f = 0;
    v->pos = (uint64_t)f << 32;
    os_unlock(&g_lock);
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
    os_lock(&g_lock);
    mix_notify *old = v->notify;
    v->notify = copy;
    v->nnotify = count;
    v->notify_fn = fn;
    v->notify_ctx = ctx;
    os_unlock(&g_lock);
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

static void mix_voice_into(mix_voice *v, float *out, uint32_t frames)
{
    const uint64_t step = (uint64_t)((double)v->freq * v->doppler / g_rate * 4294967296.0);
    const uint64_t end = (uint64_t)v->frames << 32;
    const unsigned stereo = v->fmt.channels >= 2;
    const float gl = v->gl * g_master, gr = v->gr * g_master;
    uint32_t a = play_bytes(v), wraps = 0;
    for (uint32_t i = 0; i < frames; i++) {
        uint32_t f = (uint32_t)(v->pos >> 32);
        float t = (float)(uint32_t)v->pos * (1.0f / 4294967296.0f);
        uint32_t f1 = f + 1;
        int have1 = 1;
        if (f1 >= v->frames) {
            if (v->looping) f1 = 0;
            else have1 = 0;
        }
        float l0 = sample(v, f, 0), r0 = stereo ? sample(v, f, 1) : l0;
        float l1 = have1 ? sample(v, f1, 0) : 0.0f, r1 = have1 ? (stereo ? sample(v, f1, 1) : l1) : 0.0f;
        out[2 * i] += (l0 + (l1 - l0) * t) * gl;
        out[2 * i + 1] += (r0 + (r1 - r0) * t) * gr;
        v->pos += step;
        if (v->pos >= end) {
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
    fire_crossed(v, a, play_bytes(v), wraps);
}

void mixer_render_f32(float *out, uint32_t frames)
{
    memset(out, 0, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
    os_lock(&g_lock);
    for (mix_voice *v = g_voices; v; v = v->next)
        if (v->playing && v->data && v->frames && v->freq)
            mix_voice_into(v, out, frames);
    os_unlock(&g_lock);
}

void mixer_render_s16(int16_t *out, uint32_t frames)
{
    os_lock(&g_lock);
    if (g_scratch_frames < frames) {
        float *n = (float *)realloc(g_scratch, (size_t)frames * MIX_OUT_CHANNELS * sizeof(float));
        if (!n) {
            os_unlock(&g_lock);
            memset(out, 0, (size_t)frames * MIX_OUT_CHANNELS * 2);
            return;
        }
        g_scratch = n;
        g_scratch_frames = frames;
    }
    float *buf = g_scratch;
    os_unlock(&g_lock);
    /* the scratch buffer belongs to the (single) backend thread */
    mixer_render_f32(buf, frames);
    for (uint32_t i = 0; i < frames * MIX_OUT_CHANNELS; i++) {
        float s = buf[i] * 32767.0f;
        out[i] = (int16_t)(s > 32767.0f ? 32767 : s < -32768.0f ? -32768 : lrintf(s));
    }
}
