/* reverb.h - the I3DL2 reverb DSP behind DirectSound's GUID_DSFX_STANDARD_I3DL2REVERB (platform/audio/dsound.cpp
 * SetFX / IDirectSoundFXI3DL2Reverb8), run by the mixer per voice (mixer_voice_set_reverb).
 *
 * Parameters as DSFXI3DL2Reverb (the I3DL2 level-2 set), levels in millibels:
 *   room / room_hf          attenuation of the whole room effect, and of it above hf_reference
 *   decay_time, decay_hf_ratio   late reverberation time (s) at low frequencies, and its ratio at hf_reference
 *   reflections / reflections_delay   early reflections level and delay (s) after the direct sound
 *   reverb / reverb_delay   late reverberation level and its delay (s) after the first reflection
 *   diffusion, density      echo density in time / modal density in frequency (percent)
 *   hf_reference            reference high frequency (Hz)
 * room_rolloff_factor needs a distance (DirectSound3D does that part) and is ignored, as in DirectSound's DMO.
 *
 * Structure: output = dry + room * (early + late). The wet input is the mono mix, room-filtered (high shelf at
 * hf_reference: room_hf). Early reflections: four taps spread from reflections_delay over 10 ms, alternating
 * between the channels. Late: the input delayed by reflections_delay + reverb_delay goes through two allpass
 * diffusers (coefficient from diffusion) into a 4-line feedback delay network (Householder matrix), line lengths
 * from density; each line's feedback gain gives a 60 dB decay in decay_time, and a one-pole low-pass in the loop
 * makes it decay_time * decay_hf_ratio at hf_reference. Not Microsoft's DMO sample for sample: the same parameters
 * with the same meaning. Plain C, no allocation after create. */
#ifndef FNVNX_AUDIO_REVERB_H
#define FNVNX_AUDIO_REVERB_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fnvnx_i3dl2 {
    int32_t room, room_hf;
    float room_rolloff_factor, decay_time, decay_hf_ratio;
    int32_t reflections;
    float reflections_delay;
    int32_t reverb;
    float reverb_delay, diffusion, density, hf_reference;
} fnvnx_i3dl2;

typedef struct fnvnx_reverb fnvnx_reverb;

fnvnx_reverb *fnvnx_reverb_create(uint32_t rate);
void fnvnx_reverb_destroy(fnvnx_reverb *r);
void fnvnx_reverb_set(fnvnx_reverb *r, const fnvnx_i3dl2 *p);   /* values are clamped to the I3DL2 ranges */
void fnvnx_reverb_reset(fnvnx_reverb *r);                         /* clears the delay lines (the tail) */
/* in place, interleaved stereo float */
void fnvnx_reverb_process(fnvnx_reverb *r, float *stereo, uint32_t frames);

#ifdef __cplusplus
}
#endif
#endif
