/* backend.h - audio output backends: a thread that pulls periods from the mixer (mixer_render_s16) and hands
 * them to a device. Fixed-width C only (the Horizon backend talks to LP64 libnx).
 *
 *   manual   no thread; the caller (tests) calls mixer_render_* itself
 *   null     real-time paced thread that discards the output
 *   wav:PATH real-time paced thread that writes 16-bit stereo PCM to a .wav file (host debugging)
 *   audren   libnx audio renderer (Horizon; backend_audren.c, built only with __SWITCH__)
 *
 * Selection: fnvnx_audio_configure(spec), else $FNVNX_AUDIO, else "audren" on Horizon and "null" on hosts. */
#ifndef FNVNX_AUDIO_BACKEND_H
#define FNVNX_AUDIO_BACKEND_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_AUDIO_RATE 48000
#define FNVNX_AUDIO_PERIOD 512   /* frames per period (~10.7 ms) */

typedef struct fnvnx_audio_backend {
    const char *name;
    int (*start)(const char *arg, uint32_t rate, uint32_t period);  /* 0 ok */
    void (*stop)(void);
} fnvnx_audio_backend;

extern const fnvnx_audio_backend fnvnx_audio_backend_null;
extern const fnvnx_audio_backend fnvnx_audio_backend_wav;
#ifdef __SWITCH__
extern const fnvnx_audio_backend fnvnx_audio_backend_audren;
#endif

void fnvnx_audio_configure(const char *spec);   /* before the first DirectSoundCreate8 */
int fnvnx_audio_start(void);                     /* idempotent; inits the mixer; 0 ok */
void fnvnx_audio_stop(void);

#ifdef __cplusplus
}
#endif
#endif
