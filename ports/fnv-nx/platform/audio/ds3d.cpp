/* ds3d.cpp - DirectSound 3D math (see ds3d.h).
 *
 * Distance: DirectSound's inverse-distance law with rolloff, gain = min / (min + rolloff * (d - min)) for
 * min < d, clamped at max distance (beyond it the gain stays at its max-distance value, or is 0 with
 * MUTE3DATMAXDISTANCE). Rolloff 1 = -6 dB per doubling of distance.
 * Cone: full gain inside half the inside angle, cone_outside_volume outside half the outside angle, linear in
 * hundredths of dB between.
 * Pan: the x component of the unit direction in listener space, attenuate-only constant-power law
 * (centre 1/1, hard right 0/1).
 * Doppler: f' = f * (c - k*vl) / (c - k*vs), c = 343.3 m/s / distance factor, velocities projected onto the
 * source->listener direction, clamped to [0.5, 2].
 */
#include "ds3d.h"
#include "mixer.h"

#include <math.h>

static ds3d_vec v3(float x, float y, float z) { ds3d_vec v = {x, y, z}; return v; }
static ds3d_vec sub(ds3d_vec a, ds3d_vec b) { return v3(a.x - b.x, a.y - b.y, a.z - b.z); }
static ds3d_vec scale(ds3d_vec a, float s) { return v3(a.x * s, a.y * s, a.z * s); }
static float dot(ds3d_vec a, ds3d_vec b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static ds3d_vec cross(ds3d_vec a, ds3d_vec b)
{
    return v3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
static float len(ds3d_vec a) { return sqrtf(dot(a, a)); }
static ds3d_vec norm(ds3d_vec a, ds3d_vec dflt)
{
    float l = len(a);
    return l > 1e-12f ? scale(a, 1.0f / l) : dflt;
}

void ds3d_source_merge(ds3d_source *d, const ds3d_source *s, unsigned m)
{
    if (m & DS3D_S_POS) d->pos = s->pos;
    if (m & DS3D_S_VEL) d->vel = s->vel;
    if (m & DS3D_S_CONE_ANGLES) { d->cone_inside = s->cone_inside; d->cone_outside = s->cone_outside; }
    if (m & DS3D_S_CONE_ORIENT) d->cone_orient = s->cone_orient;
    if (m & DS3D_S_CONE_VOLUME) d->cone_outside_volume = s->cone_outside_volume;
    if (m & DS3D_S_MIN) d->min_distance = s->min_distance;
    if (m & DS3D_S_MAX) d->max_distance = s->max_distance;
    if (m & DS3D_S_MODE) d->mode = s->mode;
}

void ds3d_listener_merge(ds3d_listener *d, const ds3d_listener *s, unsigned m)
{
    if (m & DS3D_L_POS) d->pos = s->pos;
    if (m & DS3D_L_VEL) d->vel = s->vel;
    if (m & DS3D_L_ORIENT) { d->front = s->front; d->top = s->top; }
    if (m & DS3D_L_DISTANCE) d->distance_factor = s->distance_factor;
    if (m & DS3D_L_ROLLOFF) d->rolloff = s->rolloff;
    if (m & DS3D_L_DOPPLER) d->doppler = s->doppler;
}

static int in_range(float v, float lo, float hi) { return v >= lo && v <= hi; }   /* false for NaN */

int ds3d_source_valid(const ds3d_source *s)
{
    return s->cone_inside <= 360 && s->cone_outside <= 360 && s->cone_inside <= s->cone_outside &&
           s->cone_outside_volume >= -10000 && s->cone_outside_volume <= 0 && s->min_distance > 0.0f &&
           s->max_distance > 0.0f && s->mode <= DS3D_MODE_DISABLE;
}

int ds3d_listener_valid(const ds3d_listener *l)
{
    if (!in_range(l->distance_factor, 1.17549435e-38f, 3.402823466e+38f) || !in_range(l->rolloff, 0.0f, 10.0f) ||
        !in_range(l->doppler, 0.0f, 10.0f))
        return 0;
    ds3d_vec c = cross(l->front, l->top);
    return len(l->front) > 0.0f && len(l->top) > 0.0f && (c.x != 0.0f || c.y != 0.0f || c.z != 0.0f);
}

void ds3d_listener_default(ds3d_listener *l)
{
    l->pos = l->vel = v3(0, 0, 0);
    l->front = v3(0, 0, 1);
    l->top = v3(0, 1, 0);
    l->distance_factor = 1.0f;
    l->rolloff = 1.0f;
    l->doppler = 1.0f;
}

void ds3d_source_default(ds3d_source *s)
{
    s->pos = s->vel = v3(0, 0, 0);
    s->cone_orient = v3(0, 0, 1);
    s->cone_inside = s->cone_outside = 360;
    s->cone_outside_volume = 0;
    s->min_distance = 1.0f;
    s->max_distance = 1000000000.0f;
    s->mode = DS3D_MODE_NORMAL;
}

void ds3d_compute(const ds3d_listener *l, const ds3d_source *s, int mute_at_max, ds3d_result *out)
{
    out->gain_l = out->gain_r = 1.0f;
    out->doppler = 1.0f;
    out->distance = 0.0f;
    if (s->mode == DS3D_MODE_DISABLE)
        return;

    /* listener basis (orthonormalised; top is made perpendicular to front) */
    ds3d_vec f = norm(l->front, v3(0, 0, 1));
    ds3d_vec u = norm(sub(l->top, scale(f, dot(l->top, f))), v3(0, 1, 0));
    ds3d_vec r = cross(u, f);

    ds3d_vec rel_world, local, lvel;
    if (s->mode == DS3D_MODE_HEADRELATIVE) {
        local = s->pos;
        rel_world = s->pos;             /* everything in listener space */
        lvel = v3(0, 0, 0);
    } else {
        rel_world = sub(s->pos, l->pos);
        local = v3(dot(rel_world, r), dot(rel_world, u), dot(rel_world, f));
        lvel = l->vel;
    }
    float d = len(local);
    out->distance = d;

    /* distance */
    float mn = s->min_distance > 0.0f ? s->min_distance : 1e-6f;
    float mx = s->max_distance > mn ? s->max_distance : mn;
    float g = 1.0f;
    if (mute_at_max && d > mx)
        g = 0.0f;
    else if (d > mn) {
        float dd = d < mx ? d : mx;
        g = mn / (mn + l->rolloff * (dd - mn));
    }

    /* cone: angle between the cone orientation and the direction from the source to the listener */
    if ((s->cone_inside < 360 || s->cone_outside < 360) && d > 1e-6f && len(s->cone_orient) > 1e-12f) {
        ds3d_vec to_listener = scale(rel_world, -1.0f / len(rel_world));
        ds3d_vec co = norm(s->cone_orient, v3(0, 0, 1));
        float c = dot(co, to_listener);
        if (c > 1.0f) c = 1.0f;
        if (c < -1.0f) c = -1.0f;
        float ang = acosf(c) * (180.0f / 3.14159265358979f);
        float hin = (float)s->cone_inside * 0.5f, hout = (float)s->cone_outside * 0.5f;
        if (hout < hin) hout = hin;
        float mb;
        if (ang <= hin) mb = 0.0f;
        else if (ang >= hout) mb = (float)s->cone_outside_volume;
        else mb = (float)s->cone_outside_volume * (ang - hin) / (hout - hin);
        g *= mixer_mb_to_gain((int32_t)lrintf(mb));
    }

    /* pan */
    float px = d > 1e-6f ? local.x / d : 0.0f;
    float gl = sqrtf(1.0f - px), gr = sqrtf(1.0f + px);
    out->gain_l = g * (gl < 1.0f ? gl : 1.0f);
    out->gain_r = g * (gr < 1.0f ? gr : 1.0f);

    /* doppler */
    if (l->doppler > 0.0f && d > 1e-6f) {
        float df = l->distance_factor > 0.0f ? l->distance_factor : 1.0f;
        float c = DS3D_SPEED_OF_SOUND / df;
        ds3d_vec dir = scale(rel_world, -1.0f / len(rel_world));   /* source -> listener */
        float vs = dot(s->vel, dir) * l->doppler, vl = dot(lvel, dir) * l->doppler;
        if (vs > 0.9f * c) vs = 0.9f * c;
        if (vs < -0.9f * c) vs = -0.9f * c;
        if (vl > 0.9f * c) vl = 0.9f * c;
        if (vl < -0.9f * c) vl = -0.9f * c;
        float ratio = (c - vl) / (c - vs);
        out->doppler = ratio < 0.5f ? 0.5f : ratio > 2.0f ? 2.0f : ratio;
    }
}
