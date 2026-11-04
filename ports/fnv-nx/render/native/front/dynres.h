// fnv-nx native renderer: dynamic resolution controller (docs/RENDER_NATIVE.md §9). Header-only, freestanding.
// Fed the GPU time of each completed frame (deko3d timestamps around the scene passes, NxFrameStats.gpu_us); returns
// the scale of the scene target for the next frame. The UI is always drawn at native resolution after the upscale.
#ifndef FNVNX_NX_DYNRES_H
#define FNVNX_NX_DYNRES_H
#include <stdint.h>

namespace nx {

struct DynResConfig {
    float targetMs = 22.2f;        // GPU budget of a frame (45 fps; 16.7 for 60)
    float minScale = 0.6f, maxScale = 1.0f, step = 0.05f;   // per axis
    float downAt = 0.95f;          // smoothed time > downAt x target for downFrames frames: one step down
    float upAt = 0.80f;            // smoothed time < upAt x target for upFrames frames: one step up
    uint32_t downFrames = 3, upFrames = 30;
    float panicAt = 1.25f;         // one frame > panicAt x target: two steps down at once
    float smoothing = 0.25f;       // EMA weight of the newest frame
    uint32_t align = 8;            // scene target size multiple (tiles, BCn-friendly)
};

class DynResController {
public:
    explicit DynResController(const DynResConfig& c = DynResConfig()) : c_(c), scale_(c.maxScale) {}
    float scale() const { return scale_; }
    float smoothedMs() const { return ema_; }
    // One completed frame's GPU time (ms); returns the scale for the next frame.
    float Update(float gpuMs) {
        ema_ = ema_ < 0 ? gpuMs : ema_ + c_.smoothing * (gpuMs - ema_);
        if (gpuMs > c_.panicAt * c_.targetMs) { Set(scale_ - 2 * c_.step); return scale_; }
        if (ema_ > c_.downAt * c_.targetMs) { over_++; under_ = 0; } else if (ema_ < c_.upAt * c_.targetMs) { under_++; over_ = 0; } else { over_ = under_ = 0; }
        if (over_ >= c_.downFrames) Set(scale_ - c_.step);
        else if (under_ >= c_.upFrames) Set(scale_ + c_.step);
        return scale_;
    }
    // Scene target size for a native size at the current scale (rounded down to align, at least align).
    uint32_t Width(uint32_t native) const { return Snap(native); }
    uint32_t Height(uint32_t native) const { return Snap(native); }
private:
    void Set(float s) {
        s = s < c_.minScale ? c_.minScale : s > c_.maxScale ? c_.maxScale : s;
        // snap to the step grid from maxScale so repeated steps land on the same sizes
        float n = (c_.maxScale - s) / c_.step;
        int k = (int)(n + 0.5f);
        s = c_.maxScale - (float)k * c_.step;
        if (s < c_.minScale) s = c_.minScale;
        if (s != scale_) { scale_ = s; over_ = under_ = 0; ema_ = -1; }   // new resolution: measure afresh
    }
    uint32_t Snap(uint32_t native) const {
        uint32_t v = (uint32_t)((float)native * scale_ + 0.5f);
        v -= v % c_.align;
        return v < c_.align ? c_.align : v > native ? native : v;
    }
    DynResConfig c_;
    float scale_, ema_ = -1;
    uint32_t over_ = 0, under_ = 0;
};

} // namespace nx
#endif
