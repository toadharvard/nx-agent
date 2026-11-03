/* mixer.h - the software mixer under the DirectSound layer (docs/AUDIO_INPUT.md "Mixer").
 *
 * Voices read PCM straight out of the memory the game locks and writes (DirectSound buffers are the voice's
 * storage), resample with linear interpolation to the output rate and mix into interleaved stereo. Cursors are
 * frames in 32.32 fixed point; byte cursors are derived from them. Position notifications are offsets crossed by
 * the play cursor during a render call, plus "stop" (DSBPN_OFFSETSTOP). Every function takes the mixer's one
 * (non-recursive) lock itself; notification callbacks run inside it and must not call back into the mixer.
 *
 * No DirectSound types here: plain fixed-width C so the mixer is testable on its own and the output backend
 * (audren on Horizon) can be LP64-clean.
 */
#ifndef FNVNX_AUDIO_MIXER_H
#define FNVNX_AUDIO_MIXER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MIX_OUT_CHANNELS 2
#define MIX_NOTIFY_STOP 0xFFFFFFFFu

typedef struct mix_format {
    uint32_t rate;        /* frames per second of the data */
    uint16_t channels;    /* 1..8; channels beyond 2 are ignored (front L/R) */
    uint16_t bits;        /* 8 (unsigned), 16 (signed), 32 (float when is_float, else signed) */
    uint16_t block;       /* bytes per frame */
    uint16_t is_float;
} mix_format;

typedef void (*mix_notify_fn)(void *ctx, uint32_t offset_or_stop, void *event);
typedef struct mix_notify { uint32_t offset; void *event; } mix_notify;

typedef struct mix_voice mix_voice;

/* mixer lifetime; out_rate is the device rate (48000 on Horizon), chunk is the backend's frames per period
 * (used for the write-cursor lead). */
void mixer_init(uint32_t out_rate, uint32_t chunk_frames);
void mixer_shutdown(void);
uint32_t mixer_out_rate(void);
void mixer_set_master_gain(float g);   /* primary buffer volume */

/* data may be NULL (primary buffer: no voice output). size is bytes, a multiple of fmt->block. */
mix_voice *mixer_voice_create(const mix_format *fmt, uint8_t *data, uint32_t size);
void mixer_voice_destroy(mix_voice *v);
void mixer_voice_set_format(mix_voice *v, const mix_format *fmt);    /* buffer must be stopped */

/* gains are linear amplitudes; freq_hz is the playback rate (before doppler) */
void mixer_voice_set_gain(mix_voice *v, float left, float right);
void mixer_voice_set_freq(mix_voice *v, uint32_t freq_hz);
void mixer_voice_set_doppler(mix_voice *v, float ratio);
void mixer_voice_play(mix_voice *v, int looping);
void mixer_voice_stop(mix_voice *v);   /* fires MIX_NOTIFY_STOP notifications if it was playing */
int mixer_voice_playing(const mix_voice *v);
int mixer_voice_looping(const mix_voice *v);
uint32_t mixer_voice_play_cursor(const mix_voice *v);    /* bytes */
uint32_t mixer_voice_write_cursor(const mix_voice *v);   /* bytes: play cursor + one period of lead, wrapped */
void mixer_voice_set_cursor(mix_voice *v, uint32_t byte_offset);   /* rounded down to a frame */
/* notifications: copied; offsets MIX_NOTIFY_STOP fire on stop/end of a one-shot. count 0 clears. */
void mixer_voice_set_notify(mix_voice *v, const mix_notify *n, uint32_t count, mix_notify_fn fn, void *ctx);

/* Render `frames` interleaved stereo frames. Called by the output backend thread (or a test). */
void mixer_render_f32(float *out, uint32_t frames);
void mixer_render_s16(int16_t *out, uint32_t frames);

/* DirectSound volume math (hundredths of a decibel) */
float mixer_mb_to_gain(int32_t millibels);                     /* 0 -> 1.0, -10000 -> 0 (silence) */
void mixer_pan_to_gains(int32_t pan, float *left, float *right);  /* DirectSound pan law: attenuate one side */

#ifdef __cplusplus
}
#endif
#endif
