/* waveout.cpp - winmm waveOut (a DLL's audio path) on the DirectSound mixer.
 *
 * One device (id 0, also WAVE_MAPPER) = the mixer's sink (48 kHz stereo). Each open handle owns one mixer
 * pull-source voice in the handle's own format (8/16-bit, 1/2 ch, any rate), so format and rate conversion are the
 * mixer's. The mixer PULLS: while rendering it calls pull() for exactly the source frames its resampler needs
 * (mixer.h), pull() copies them from the queued WAVEHDR blocks in order (BEGINLOOP/ENDLOOP/dwLoops honoured;
 * paused = 0 frames), adds them to the exact consumed-frame position (waveOutGetPosition) and completes a block at
 * the very frame its last frame is pulled. Nothing depends on thread timing or on how the sink splits its
 * renders. Completed headers go to the delivery thread, which calls back with no lock held, in completion order.
 *
 * Locks: g_lock guards the handle list; each handle's wlock guards its queue/position and is taken by pull()
 * inside the mixer's render lock, so a holder of wlock never calls the mixer or takes g_lock (order: g_lock >
 * mixer lock > wlock > g_qlock). A block's length must be whole frames (else MMSYSERR_INVALPARAM). CALLBACK_THREAD
 * posts a thread message to the thread whose id the caller gave (dwCallback), as the documented contract. */
#include "../win32/w32.h"
#include <dsound.h>
#include "backend.h"
#include "mixer.h"
extern "C" int fnvnx_post_thread_message(DWORD tid, UINT m, WPARAM w, LPARAM l);

#include <stdlib.h>
#include <string.h>

namespace {
struct nx_wavehdr {
    char *lpData; uint32_t dwBufferLength, dwBytesRecorded; uintptr_t dwUser; uint32_t dwFlags, dwLoops;
    nx_wavehdr *lpNext; uintptr_t reserved;
};
#pragma pack(push, 1)
struct nx_mmtime { UINT wType; union { uint32_t v; uint8_t raw[8]; } u; };
struct nx_caps { uint16_t mid, pid; UINT ver; char name[32]; uint32_t formats; uint16_t ch, res; uint32_t support; };
#pragma pack(pop)

enum { OK = 0, BADDEV = 2, NOMEM = 7, INVALHANDLE = 5, BADERRNUM = 9, INVALFLAG = 10, INVALPARAM = 11,
       BADFORMAT = 32, STILLPLAYING = 33, UNPREPARED = 34 };
enum { H_DONE = 1, H_PREP = 2, H_BEGIN = 4, H_END = 8, H_QUEUE = 0x10 };
enum { CB_WINDOW = 1, CB_THREAD = 2, CB_FUNC = 3, CB_EVENT = 5 };
enum { WOM_OPEN = 0x3BB, WOM_CLOSE = 0x3BC, WOM_DONE = 0x3BD };

struct node {
    node *next; nx_wavehdr *h; uint32_t frames, off, flags, loops; bool fed;
};
FNVNX_CALLBACK_T(waveout_proc, void, (void *, UINT, uintptr_t, uintptr_t, uintptr_t));   /* waveOutProc */
struct cbinfo { unsigned kind; uintptr_t target, inst; };
struct wo {
    wo *next; uintptr_t id; mix_format fmt; uint32_t vol;
    mix_voice *voice; cbinfo cb;
    os_lock_t wlock;                        /* guards everything below */
    node *head, *tail, *cur, *loop_begin;
    uint64_t pulled, loop_fed; uint32_t loops_left;
    bool paused, loop_active;
    /* the stream's timeline in the clock's time (VC1): source frame k is consumed at anchor_t + (k - anchor_k) / rate; a pause
     * shifts the anchor by its length; a block completes (WOM_DONE) at the instant its last frame is consumed */
    uint64_t anchor_t, anchor_k, paused_at;
};
uint64_t comp_stamp(const wo *w) { return w->anchor_t + (w->pulled - w->anchor_k) * 1000000000ull / w->fmt.rate; }
struct msg { msg *next; cbinfo cb; uintptr_t hwo; UINT m; uintptr_t p1; uint64_t ck; };   /* ck: the clock's stamp of the event */
__thread int t_in_deliver;   /* the calling thread is the delivery thread */

os_lock_t g_lock, g_qlock;
os_cond_t g_qcond;
wo *g_list;
uintptr_t g_next_id = 0x100;
uint32_t g_devvol = 0xFFFFFFFFu;
msg *g_qhead, *g_qtail;
int g_inflight, g_threads;

void post(const cbinfo &cb, uintptr_t hwo, UINT m, uintptr_t p1, uint64_t stamp)   /* g_lock held or not */
{
    msg *q = (msg *)calloc(1, sizeof *q);
    if (!q) return;
    q->cb = cb; q->hwo = hwo; q->m = m; q->p1 = p1; q->ck = stamp;
    os_lock(&g_qlock);
    if (g_qtail) g_qtail->next = q; else g_qhead = q;
    g_qtail = q;
    os_cond_signal(&g_qcond);
    os_unlock(&g_qlock);
}

void deliver(const msg *q)
{
    switch (q->cb.kind) {
    case CB_FUNC: {
        /* CALLBACK_FUNCTION: dwCallback is a game address (an integer in the waveOutOpen call): converted here, where it is
         * interpreted as a function; g_qlock is not held (deliver_loop released it) */
        waveout_proc f((uintptr_t)q->cb.target);
        f((void *)q->hwo, q->m, q->cb.inst, q->p1, 0);
        break;
    }
    case CB_EVENT: SetEvent((HANDLE)q->cb.target); break;
    case CB_WINDOW: PostMessageA((HWND)q->cb.target, q->m, (WPARAM)q->hwo, (LPARAM)q->p1); break;
    case CB_THREAD: fnvnx_post_thread_message((DWORD)q->cb.target, q->m, (WPARAM)q->hwo, (LPARAM)q->p1); break;
    }
}

void deliver_loop(void *)
{
    t_in_deliver = 1;
    for (;;) {
        os_lock(&g_qlock);
        while (!g_qhead) clk_rt_cond_timedwait(&g_qcond, &g_qlock, 100000000ull);
        msg *q = g_qhead;
        g_qhead = q->next;
        if (!g_qhead) g_qtail = NULL;
        g_inflight++;
        os_unlock(&g_qlock);
        clk_thread_start(q->ck);   /* each callback is an activation at the event's instant (this thread serves every device) */
        deliver(q);
        free(q);
        __atomic_fetch_sub(&g_inflight, 1, __ATOMIC_SEQ_CST);
    }
}

wo *find(void *h)
{
    for (wo *w = g_list; w; w = w->next)
        if (w->id == (uintptr_t)h) return w;
    return NULL;
}

void apply_gain(wo *w)
{
    float l = (float)(w->vol & 0xFFFF) / 65535.0f * (float)(g_devvol & 0xFFFF) / 65535.0f;
    float r = (float)(w->vol >> 16) / 65535.0f * (float)(g_devvol >> 16) / 65535.0f;
    mixer_voice_set_gain(w->voice, l, r);
}

/* Hand out up to `frames` frames into dst (may be NULL with 0: drain), completing every block that is exhausted;
 * wlock held. Blocks complete in order; the blocks of a loop complete together when the loop ends. */
size_t advance(wo *w, uint8_t *dst, size_t frames)
{
    size_t got = 0;
    while (node *n = w->cur) {
        if (n->off == 0 && !w->loop_active && (n->flags & H_BEGIN)) {
            w->loop_active = true; w->loop_begin = n; w->loops_left = n->loops ? n->loops : 1; w->loop_fed = 0;
        }
        if (n->off < n->frames) {
            size_t k = n->frames - n->off;
            if (k > frames - got) k = frames - got;
            if (!k) break;
            memcpy(dst + got * w->fmt.block, (const uint8_t *)n->h->lpData + (size_t)n->off * w->fmt.block, k * w->fmt.block);
            n->off += (uint32_t)k; got += k; w->pulled += k; w->loop_fed += k;
            if (n->off < n->frames) break;
        }
        if (w->loop_active && (n->flags & H_END)) {
            if (w->loops_left > 1 && w->loop_fed > 0) {
                w->loops_left--; w->loop_fed = 0;
                for (node *m = w->loop_begin;; m = m->next) { m->off = 0; if (m == n) break; }
                w->cur = w->loop_begin;
                continue;
            }
            w->loop_active = false;
        }
        n->fed = true;
        w->cur = n->next;
        while (w->head && w->head->fed && !(w->loop_active && w->head == w->loop_begin)) {
            node *d = w->head;
            w->head = d->next;
            if (!w->head) w->tail = NULL;
            d->h->dwFlags = (d->h->dwFlags & ~(uint32_t)H_QUEUE) | H_DONE;
            post(w->cb, w->id, WOM_DONE, (uintptr_t)d->h, comp_stamp(w));
            free(d);
        }
    }
    return got;
}

/* the mixer's stream callback: runs under the mixer's render lock (must not call the mixer) */
size_t pull(void *user, void *dst, size_t frames)
{
    wo *w = (wo *)user;
    os_lock(&w->wlock);
    size_t got = w->paused ? 0 : advance(w, (uint8_t *)dst, frames);
    os_unlock(&w->wlock);
    return got;
}

void start_threads()   /* g_lock held */
{
    if (g_threads) return;
    /* the thread lives for the process; its os_thread record is never freed (the thread reads its own) */
    os_thread *t = os_thread_create(deliver_loop, NULL, 512 * 1024, 2, OS_THREAD_ROLE(OS_ROLE_AUDIO));
    if (t) os_thread_start(t);
    g_threads = 1;
}

/* WAVEFORMATEX / EXTENSIBLE PCM -> mixer format; false: WAVERR_BADFORMAT */
bool parse(const WAVEFORMATEX *p, mix_format *m)
{
    const uint8_t *b = (const uint8_t *)p;
    uint16_t tag = p->wFormatTag, ch = p->nChannels, bits = p->wBitsPerSample;
    if (tag == WAVE_FORMAT_EXTENSIBLE) {
        static const uint8_t pcm_guid[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
        const uint8_t *x = b + sizeof(WAVEFORMATEX);   /* wValidBitsPerSample, dwChannelMask, SubFormat */
        if (p->cbSize < 22 || memcmp(x + 6, pcm_guid, 16)) return false;
        uint16_t valid;
        memcpy(&valid, x, 2);
        if (valid > bits) return false;
    } else if (tag != WAVE_FORMAT_PCM) return false;
    if (ch < 1 || ch > 2 || (bits != 8 && bits != 16)) return false;
    if (p->nSamplesPerSec < 1 || p->nSamplesPerSec > 384000) return false;
    if (p->nBlockAlign != ch * bits / 8 || p->nAvgBytesPerSec != p->nSamplesPerSec * p->nBlockAlign) return false;
    memset(m, 0, sizeof *m);
    m->rate = p->nSamplesPerSec; m->channels = ch; m->bits = bits; m->block = p->nBlockAlign;
    return true;
}

bool is_device(uintptr_t v) { return v == 0 || v == 0xFFFFFFFFu; }
}   // namespace

extern "C" {

/* test hook and callers' barrier: wait until every queued callback has returned */
void fnvnx_waveout_service(void)
{
    if (t_in_deliver) return;   /* a callback waiting for the callbacks would wait for itself */
    for (int i = 0; i < 20000; i++) {
        os_lock(&g_qlock);
        bool idle = !g_qhead && !__atomic_load_n(&g_inflight, __ATOMIC_SEQ_CST);
        os_unlock(&g_qlock);
        if (idle) break;
        clk_rt_sleep_ns(100000);
    }
}

#include "waveout.env_const.gen.inc"   /* waveOutGetNumDevs: manifest/env_const.tsv */

UINT waveOutGetDevCapsA(uintptr_t id, void *out, UINT cb)
{
    os_lock(&g_lock);
    bool ok = is_device(id) || find((void *)id);
    os_unlock(&g_lock);
    if (!ok) return BADDEV;
    if (!out) return INVALPARAM;
    nx_caps c;
    memset(&c, 0, sizeof c);
    c.mid = 1; c.pid = 0x64; c.ver = 0x0600; c.ch = 2; c.formats = 0xFFFFF; c.support = 0x04 | 0x08 | 0x10;
    memcpy(c.name, "Speakers", 9);
    memcpy(out, &c, cb < sizeof c ? cb : sizeof c);
    return OK;
}

UINT waveOutGetErrorTextA(UINT e, char *text, UINT cch)
{
    static const char *const t[] = {
        "The specified command was carried out.", "Undefined external error.",
        "A device ID has been used that is out of range for your system.", "The driver failed to load or initialize.",
        "The specified device is already in use. Wait until it is free, and then try again.",
        "The specified device handle is invalid.", "There is no driver installed on your system !",
        "Not enough memory available for this task. Quit one or more applications to increase available memory, and then try again.",
        "This function is not supported. Use the Capabilities function to determine which functions and messages the driver supports.",
        "An error number was specified that is not defined in the system.",
        "An invalid flag was passed to a system function.", "An invalid parameter was passed to a system function.",
        "The handle is busy.", "Specified alias not found.", "Bad registry database.", "Registry key not found.",
        "Registry read error.", "Registry write error.", "Registry delete error.", "Registry value not found.",
        "Driver does not call DriverCallback."};
    static const char *const w[] = {
        "The specified format is not supported or cannot be translated. Use the Capabilities function to determine the supported formats.",
        "Cannot perform this operation while media data is still playing. Reset the device, or wait until the data is finished playing.",
        "The wave header was not prepared. Prepare the header, and then try again.",
        "Cannot open the device without using the WAVE_ALLOWSYNC flag. Use the flag, and then try again."};
    const char *s = e <= 20 ? t[e] : e >= 32 && e <= 35 ? w[e - 32] : NULL;
    if (!s) return BADERRNUM;
    if (!text) return INVALPARAM;
    if (cch) {
        size_t n = strlen(s);
        if (n > cch - 1) n = cch - 1;
        memcpy(text, s, n);
        text[n] = 0;
    }
    return OK;
}

UINT waveOutOpen(void **phwo, UINT dev, const void *pwfx, uintptr_t cb, uintptr_t inst, DWORD fl)
{
    if (fl & ~0x7000Fu) return INVALFLAG;
    const unsigned kind = (fl >> 16) & 7;
    if (kind != 0 && kind != CB_WINDOW && kind != CB_THREAD && kind != CB_FUNC && kind != CB_EVENT) return INVALFLAG;
    const bool query = fl & 1;
    if (!pwfx || (!query && !phwo)) return INVALPARAM;
    if (dev != 0 && dev != 0xFFFFFFFFu) return BADDEV;
    mix_format mf;
    if (!parse((const WAVEFORMATEX *)pwfx, &mf)) return BADFORMAT;
    if (query) return OK;
    if ((kind == CB_FUNC || kind == CB_THREAD) && !cb) return INVALPARAM;
    if (kind == CB_EVENT && !w32_obj_get((HANDLE)cb, W32_EVENT)) return INVALPARAM;
    if (kind == CB_WINDOW && !IsWindow((HWND)cb)) return INVALPARAM;
    fnvnx_audio_start();
    wo *w = (wo *)calloc(1, sizeof *w);
    if (!w) return NOMEM;
    w->fmt = mf;
    w->vol = 0xFFFFFFFFu;
    w->cb = {kind, cb, inst};
    w->voice = mixer_voice_create_stream(&mf, pull, w);
    if (!w->voice) { free(w); return NOMEM; }
    os_lock(&g_lock);
    do { g_next_id = (g_next_id + 1) & 0x0FFFFFFF; } while (g_next_id < 0x100 || find((void *)g_next_id));
    w->id = g_next_id;
    apply_gain(w);
    w->next = g_list; g_list = w;
    start_threads();
    *phwo = (void *)w->id;
    if (kind == CB_EVENT) clk_device_watch((const void *)cb, 1);
    post(w->cb, w->id, WOM_OPEN, 0, clk_stamp());
    mixer_voice_play(w->voice, 0);
    os_unlock(&g_lock);
    return OK;
}

UINT waveOutClose(void *h)
{
    os_lock(&g_lock);
    wo *w = find(h);
    if (!w) { os_unlock(&g_lock); return INVALHANDLE; }
    os_lock(&w->wlock);
    bool busy = w->head != NULL;
    os_unlock(&w->wlock);
    if (busy) { os_unlock(&g_lock); return STILLPLAYING; }
    for (wo **pp = &g_list; *pp; pp = &(*pp)->next)
        if (*pp == w) { *pp = w->next; break; }
    mixer_voice_destroy(w->voice);
    if (w->cb.kind == CB_EVENT) clk_device_watch((const void *)w->cb.target, 0);
    post(w->cb, w->id, WOM_CLOSE, 0, clk_stamp());
    os_unlock(&g_lock);
    free(w);
    return OK;
}

UINT waveOutPrepareHeader(void *h, void *hdr, UINT cb)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = !w ? INVALHANDLE : (!hdr || cb < sizeof(nx_wavehdr)) ? INVALPARAM : OK;
    nx_wavehdr *p = (nx_wavehdr *)hdr;
    if (rc == OK && !p->lpData && p->dwBufferLength) rc = INVALPARAM;
    if (rc == OK) p->dwFlags = (p->dwFlags & ~(uint32_t)H_DONE) | H_PREP;
    os_unlock(&g_lock);
    return rc;
}

UINT waveOutUnprepareHeader(void *h, void *hdr, UINT cb)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = !w ? INVALHANDLE : (!hdr || cb < sizeof(nx_wavehdr)) ? INVALPARAM : OK;
    nx_wavehdr *p = (nx_wavehdr *)hdr;
    if (rc == OK && (p->dwFlags & H_QUEUE)) rc = STILLPLAYING;
    if (rc == OK) p->dwFlags &= ~(uint32_t)H_PREP;
    os_unlock(&g_lock);
    return rc;
}

UINT waveOutWrite(void *h, void *hdr, UINT cb)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = !w ? INVALHANDLE : (!hdr || cb < sizeof(nx_wavehdr)) ? INVALPARAM : OK;
    nx_wavehdr *p = (nx_wavehdr *)hdr;
    if (rc == OK && !(p->dwFlags & H_PREP)) rc = UNPREPARED;
    if (rc == OK && (p->dwFlags & H_QUEUE)) rc = STILLPLAYING;
    if (rc == OK && p->dwBufferLength % w->fmt.block) rc = INVALPARAM;   /* must be block-aligned (MSDN) */
    node *n = rc == OK ? (node *)calloc(1, sizeof *n) : NULL;
    if (rc == OK && !n) rc = NOMEM;
    if (rc == OK) {
        n->h = p; n->frames = p->lpData ? p->dwBufferLength / w->fmt.block : 0;
        n->flags = p->dwFlags & (H_BEGIN | H_END); n->loops = p->dwLoops;
        p->dwFlags = (p->dwFlags | H_QUEUE) & ~(uint32_t)H_DONE;
        os_lock(&w->wlock);
        if (!w->cur) {   /* the stream was idle: its timeline starts with this block */
            w->anchor_t = clk_stamp(); w->anchor_k = w->pulled;
            if (w->paused) w->paused_at = w->anchor_t;
        }
        if (w->tail) w->tail->next = n; else w->head = n;
        w->tail = n;
        if (!w->cur) w->cur = n;
        advance(w, NULL, 0);   /* a zero-length block completes at once */
        os_unlock(&w->wlock);
    }
    os_unlock(&g_lock);
    return rc;
}

UINT waveOutPause(void *h)
{
    os_lock(&g_lock);
    wo *w = find(h);
    if (w) { os_lock(&w->wlock); if (!w->paused) w->paused_at = clk_stamp(); w->paused = true; os_unlock(&w->wlock); }
    os_unlock(&g_lock);
    return w ? OK : INVALHANDLE;
}

UINT waveOutRestart(void *h)
{
    os_lock(&g_lock);
    wo *w = find(h);
    if (w) {
        os_lock(&w->wlock);
        if (w->paused) { uint64_t t = clk_stamp(); if (t > w->paused_at) w->anchor_t += t - w->paused_at; }   /* a pause shifts the timeline */
        w->paused = false;
        os_unlock(&w->wlock);
    }
    os_unlock(&g_lock);
    return w ? OK : INVALHANDLE;
}

UINT waveOutReset(void *h)
{
    os_lock(&g_lock);
    wo *w = find(h);
    if (w) {
        os_lock(&w->wlock);
        while (w->head) {
            node *n = w->head;
            w->head = n->next;
            n->h->dwFlags = (n->h->dwFlags & ~(uint32_t)H_QUEUE) | H_DONE;
            post(w->cb, w->id, WOM_DONE, (uintptr_t)n->h, clk_stamp());   /* a reset returns every block now */
            free(n);
        }
        w->tail = w->cur = w->loop_begin = NULL;
        w->loop_active = w->paused = false;
        w->pulled = 0;
        os_unlock(&w->wlock);
        mixer_voice_set_cursor(w->voice, 0);   /* the resampler window restarts */
    }
    os_unlock(&g_lock);
    return w ? OK : INVALHANDLE;
}

UINT waveOutGetPosition(void *h, void *out, UINT cb)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = !w ? INVALHANDLE : (!out || cb < sizeof(nx_mmtime)) ? INVALPARAM : OK;
    if (rc == OK) {
        os_lock(&w->wlock);
        const uint64_t pos = w->pulled;
        os_unlock(&w->wlock);
        nx_mmtime *t = (nx_mmtime *)out;
        memset(&t->u, 0, sizeof t->u);
        if (t->wType == 1) t->u.v = (uint32_t)(pos * 1000 / w->fmt.rate);
        else if (t->wType == 2) t->u.v = (uint32_t)pos;
        else { t->wType = 4; t->u.v = (uint32_t)(pos * w->fmt.block); }
    }
    os_unlock(&g_lock);
    return rc;
}

UINT waveOutSetVolume(void *h, DWORD vol)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = OK;
    if (w) { w->vol = vol; apply_gain(w); }
    else if (is_device((uintptr_t)h)) {
        g_devvol = vol;
        for (wo *x = g_list; x; x = x->next) apply_gain(x);
    } else rc = (uintptr_t)h < 0x100 ? BADDEV : INVALHANDLE;
    os_unlock(&g_lock);
    return rc;
}

UINT waveOutGetVolume(void *h, DWORD *vol)
{
    os_lock(&g_lock);
    wo *w = find(h);
    UINT rc = OK;
    if (!w && !is_device((uintptr_t)h)) rc = (uintptr_t)h < 0x100 ? BADDEV : INVALHANDLE;
    else if (!vol) rc = INVALPARAM;
    else *vol = w ? w->vol : g_devvol;
    os_unlock(&g_lock);
    return rc;
}

}   // extern "C"
