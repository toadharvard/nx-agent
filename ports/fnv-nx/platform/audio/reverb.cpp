/* reverb.cpp - I3DL2 reverb DSP (see reverb.h; C with C linkage, a .cpp so the guest build picks it up). */
#include "reverb.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define LINES 4
#define MAX_PRE 0.4f       /* reflections_delay (0.3) + reverb_delay (0.1) */
#define TAPS 4

/* line lengths in ms at 100 % density, mutually prime-ish; density scales them down to 40 % */
static const float k_line_ms[LINES] = {29.7f, 37.1f, 41.1f, 43.7f};
static const float k_ap_ms[2] = {5.0f, 1.7f};
static const float k_tap_ms[TAPS] = {0.0f, 3.1f, 6.4f, 10.0f};
static const float k_tap_gain[TAPS] = {1.0f, 0.8f, 0.65f, 0.5f};

typedef struct { float *buf; uint32_t len, pos; } delay;

struct fnvnx_reverb {
    uint32_t rate;
    fnvnx_i3dl2 p;
    /* derived */
    float g_room, g_reflect, g_reverb, shelf_hf, lp_room_a;
    uint32_t pre_reflect, pre_late, tap[TAPS];
    uint32_t line_len[LINES];
    float fb[LINES], damp_a[LINES], ap_g;
    /* state */
    float lp_room;
    delay pre;                 /* the room-filtered input, long enough for both pre-delays and the taps */
    delay ap[2];
    delay line[LINES];
    float damp[LINES];
};

static float mb(int32_t v) { return v <= -10000 ? 0.0f : powf(10.0f, (float)v / 2000.0f); }
static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
static int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

static int delay_alloc(delay *d, uint32_t len)
{
    d->buf = (float *)calloc(len, sizeof(float));
    d->len = len;
    d->pos = 0;
    return d->buf != NULL;
}
/* sample written n samples ago (n < len) */
static inline float delay_tap(const delay *d, uint32_t n) { return d->buf[(d->pos + d->len - n) % d->len]; }
static inline void delay_push(delay *d, float v)
{
    d->pos = (d->pos + 1) % d->len;
    d->buf[d->pos] = v;
}

/* one-pole low-pass coefficient for a -3 dB point at f */
static float lp_coef(float f, uint32_t rate) { return expf(-2.0f * 3.14159265f * f / (float)rate); }

fnvnx_reverb *fnvnx_reverb_create(uint32_t rate)
{
    fnvnx_reverb *r = (fnvnx_reverb *)calloc(1, sizeof *r);
    if (!r)
        return NULL;
    r->rate = rate ? rate : 48000;
    int ok = delay_alloc(&r->pre, (uint32_t)(MAX_PRE * r->rate + k_tap_ms[TAPS - 1] * 1e-3f * r->rate) + 2);
    for (int i = 0; i < 2; i++)
        ok &= delay_alloc(&r->ap[i], (uint32_t)(k_ap_ms[i] * 1e-3f * r->rate) + 1);
    for (int i = 0; i < LINES; i++)
        ok &= delay_alloc(&r->line[i], (uint32_t)(k_line_ms[i] * 1e-3f * r->rate) + 2);
    if (!ok) {
        fnvnx_reverb_destroy(r);
        return NULL;
    }
    /* DirectSound's defaults (DSFX_I3DL2REVERB_*_DEFAULT) */
    fnvnx_i3dl2 d = {-1000, -100, 0.0f, 1.49f, 0.83f, -2602, 0.007f, 200, 0.011f, 100.0f, 100.0f, 5000.0f};
    fnvnx_reverb_set(r, &d);
    return r;
}

void fnvnx_reverb_destroy(fnvnx_reverb *r)
{
    if (!r)
        return;
    free(r->pre.buf);
    for (int i = 0; i < 2; i++)
        free(r->ap[i].buf);
    for (int i = 0; i < LINES; i++)
        free(r->line[i].buf);
    free(r);
}

void fnvnx_reverb_reset(fnvnx_reverb *r)
{
    memset(r->pre.buf, 0, r->pre.len * sizeof(float));
    for (int i = 0; i < 2; i++)
        memset(r->ap[i].buf, 0, r->ap[i].len * sizeof(float));
    for (int i = 0; i < LINES; i++) {
        memset(r->line[i].buf, 0, r->line[i].len * sizeof(float));
        r->damp[i] = 0.0f;
    }
    r->lp_room = 0.0f;
}

void fnvnx_reverb_set(fnvnx_reverb *r, const fnvnx_i3dl2 *in)
{
    fnvnx_i3dl2 p = *in;   /* the DSFXI3DL2Reverb ranges */
    p.room = clampi(p.room, -10000, 0);
    p.room_hf = clampi(p.room_hf, -10000, 0);
    p.decay_time = clampf(p.decay_time, 0.1f, 20.0f);
    p.decay_hf_ratio = clampf(p.decay_hf_ratio, 0.1f, 2.0f);
    p.reflections = clampi(p.reflections, -10000, 1000);
    p.reflections_delay = clampf(p.reflections_delay, 0.0f, 0.3f);
    p.reverb = clampi(p.reverb, -10000, 2000);
    p.reverb_delay = clampf(p.reverb_delay, 0.0f, 0.1f);
    p.diffusion = clampf(p.diffusion, 0.0f, 100.0f);
    p.density = clampf(p.density, 0.0f, 100.0f);
    p.hf_reference = clampf(p.hf_reference, 20.0f, 20000.0f);
    r->p = p;
    const float fs = (float)r->rate;
    r->g_room = mb(p.room);
    r->shelf_hf = mb(p.room_hf);
    r->g_reflect = mb(p.reflections);
    r->g_reverb = mb(p.reverb);
    float fc = p.hf_reference < fs * 0.45f ? p.hf_reference : fs * 0.45f;
    r->lp_room_a = lp_coef(fc, r->rate);
    r->pre_reflect = (uint32_t)(p.reflections_delay * fs);
    r->pre_late = (uint32_t)((p.reflections_delay + p.reverb_delay) * fs);
    for (int t = 0; t < TAPS; t++)
        r->tap[t] = r->pre_reflect + (uint32_t)(k_tap_ms[t] * 1e-3f * fs);
    r->ap_g = 0.7f * p.diffusion / 100.0f;
    const float scale = 0.4f + 0.6f * p.density / 100.0f;
    const float w = 2.0f * 3.14159265f * fc / fs;
    for (int i = 0; i < LINES; i++) {
        uint32_t n = (uint32_t)(k_line_ms[i] * 1e-3f * fs * scale);
        if (n < 2) n = 2;
        if (n > r->line[i].len - 1) n = r->line[i].len - 1;
        r->line_len[i] = n;
        /* 60 dB in decay_time: per pass of n samples */
        float g_lo = powf(10.0f, -3.0f * (float)n / (fs * p.decay_time));
        float g_hf = powf(10.0f, -3.0f * (float)n / (fs * p.decay_time * p.decay_hf_ratio));
        if (g_hf > g_lo) {   /* ratio > 1: HF decays slower; a one-pole low-pass cannot boost, keep it flat */
            r->fb[i] = g_lo;
            r->damp_a[i] = 0.0f;
            continue;
        }
        /* one-pole low-pass y = (1-a) x + a y' with DC gain 1 and gain g_hf/g_lo at w:
         * |H(w)|^2 = (1-a)^2 / (1 - 2a cos w + a^2) = k^2  ->  a from the quadratic */
        float k2 = (g_hf / g_lo) * (g_hf / g_lo), c = cosf(w);
        float A = k2 - 1.0f, B = 2.0f - 2.0f * k2 * c, C = k2 - 1.0f;   /* A a^2 + B a + C = 0 (a in [0,1)) */
        float a = 0.0f;
        if (fabsf(A) > 1e-9f) {
            float disc = B * B - 4.0f * A * C;
            if (disc < 0.0f) disc = 0.0f;
            float s1 = (-B + sqrtf(disc)) / (2.0f * A), s2 = (-B - sqrtf(disc)) / (2.0f * A);
            a = s1 >= 0.0f && s1 < 1.0f ? s1 : s2 >= 0.0f && s2 < 1.0f ? s2 : 0.0f;
        }
        r->fb[i] = g_lo;
        r->damp_a[i] = a;
    }
}

void fnvnx_reverb_process(fnvnx_reverb *r, float *io, uint32_t frames)
{
    if (r->g_room <= 0.0f)
        return;   /* room at -10000 mB: the direct sound only */
    const float a_room = r->lp_room_a, shelf = r->shelf_hf, ap_g = r->ap_g;
    for (uint32_t i = 0; i < frames; i++) {
        float l = io[2 * i], rr = io[2 * i + 1];
        float x = 0.5f * (l + rr);
        /* room filter: high shelf at hf_reference */
        r->lp_room = (1.0f - a_room) * x + a_room * r->lp_room;
        float in = r->lp_room + shelf * (x - r->lp_room);
        delay_push(&r->pre, in);
        /* early reflections */
        float el = 0.0f, er = 0.0f;
        for (int t = 0; t < TAPS; t++) {
            float s = delay_tap(&r->pre, r->tap[t]) * k_tap_gain[t];
            if (t & 1) er += s; else el += s;
        }
        el *= r->g_reflect;
        er *= r->g_reflect;
        /* late: diffusers, then the FDN */
        float d = delay_tap(&r->pre, r->pre_late);
        for (int k = 0; k < 2; k++) {   /* Schroeder allpass */
            float v = delay_tap(&r->ap[k], r->ap[k].len - 1);
            float y = -ap_g * d + v;
            delay_push(&r->ap[k], d + ap_g * y);
            d = y;
        }
        float o[LINES], sum = 0.0f;
        for (int k = 0; k < LINES; k++) {
            o[k] = delay_tap(&r->line[k], r->line_len[k] - 1);
            sum += o[k];
        }
        const float h = 2.0f / LINES;   /* Householder: o - (2/N) sum, lossless */
        for (int k = 0; k < LINES; k++) {
            float fbk = (o[k] - h * sum) * r->fb[k];
            r->damp[k] = (1.0f - r->damp_a[k]) * fbk + r->damp_a[k] * r->damp[k];
            delay_push(&r->line[k], r->damp[k] + d * 0.5f);
        }
        float ll = (o[0] + o[2]) * r->g_reverb * 0.5f, lr = (o[1] + o[3]) * r->g_reverb * 0.5f;
        io[2 * i] = l + r->g_room * (el + ll);
        io[2 * i + 1] = rr + r->g_room * (er + lr);
    }
}
