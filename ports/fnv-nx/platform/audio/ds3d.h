/* ds3d.h - DirectSound 3D math (docs/AUDIO_INPUT.md "3D"): distance attenuation, cone, pan and doppler of one
 * source for one listener, in DirectSound's left-handed convention (x right, y up, z front). Pure functions. */
#ifndef FNVNX_AUDIO_DS3D_H
#define FNVNX_AUDIO_DS3D_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ds3d_vec { float x, y, z; } ds3d_vec;

typedef struct ds3d_listener {
    ds3d_vec pos, vel, front, top;
    float distance_factor;   /* meters per unit */
    float rolloff;           /* 0..10 */
    float doppler;           /* 0..10 */
} ds3d_listener;

#define DS3D_MODE_NORMAL 0
#define DS3D_MODE_HEADRELATIVE 1
#define DS3D_MODE_DISABLE 2

typedef struct ds3d_source {
    ds3d_vec pos, vel, cone_orient;
    uint32_t cone_inside, cone_outside;   /* degrees, full angles */
    int32_t cone_outside_volume;          /* hundredths of dB */
    float min_distance, max_distance;
    uint32_t mode;
} ds3d_source;

typedef struct ds3d_result {
    float gain_l, gain_r;    /* includes distance and cone attenuation */
    float doppler;           /* frequency ratio */
    float distance;
} ds3d_result;

#define DS3D_SPEED_OF_SOUND 343.3f   /* m/s */

/* Parameter groups: DirectSound applies DS3D_IMMEDIATE / DS3D_DEFERRED per parameter, never per object. */
#define DS3D_S_POS 1u
#define DS3D_S_VEL 2u
#define DS3D_S_CONE_ANGLES 4u
#define DS3D_S_CONE_ORIENT 8u
#define DS3D_S_CONE_VOLUME 16u
#define DS3D_S_MIN 32u
#define DS3D_S_MAX 64u
#define DS3D_S_MODE 128u
#define DS3D_S_ALL 255u
#define DS3D_L_POS 1u
#define DS3D_L_VEL 2u
#define DS3D_L_ORIENT 4u
#define DS3D_L_DISTANCE 8u
#define DS3D_L_ROLLOFF 16u
#define DS3D_L_DOPPLER 32u
#define DS3D_L_ALL 63u

/* copy the parameter groups in `mask` from src to dst */
void ds3d_source_merge(ds3d_source *dst, const ds3d_source *src, unsigned mask);
void ds3d_listener_merge(ds3d_listener *dst, const ds3d_listener *src, unsigned mask);
/* SDK ranges; 1 = every parameter valid: cone angles 0..360 with inside <= outside, cone outside volume
 * -10000..0, distances > 0, mode NORMAL/HEADRELATIVE/DISABLE; listener: distance factor FLT_MIN..FLT_MAX,
 * rolloff and doppler 0..10, front and top non-zero and not parallel. NaN is invalid everywhere. */
int ds3d_source_valid(const ds3d_source *s);
int ds3d_listener_valid(const ds3d_listener *l);

void ds3d_listener_default(ds3d_listener *l);
void ds3d_source_default(ds3d_source *s);
/* mute_at_max: DSBCAPS_MUTE3DATMAXDISTANCE */
void ds3d_compute(const ds3d_listener *l, const ds3d_source *s, int mute_at_max, ds3d_result *out);

#ifdef __cplusplus
}
#endif
#endif
